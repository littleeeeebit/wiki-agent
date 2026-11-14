"""Local, cancellable setup. CLI providers retain ownership of their credentials."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Literal
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agent import cli_command
from common import errorlog, process, settings, shell
from common.paths import DATA, PACKAGED, RESOURCES, release
import desktop_migrate
import setup_chat

from . import channels, runtime

router = APIRouter(prefix="/api/onboarding")
FILE = DATA / "onboarding.json"
PACKAGES = {"git": "Git.Git", "pwsh": "Microsoft.PowerShell", "gh": "GitHub.cli",
            "claude": "Anthropic.ClaudeCode", "fd": "sharkdp.fd", "jq": "jqlang.jq"}
KEYS = {"GEMINI_API_KEY", "TYPESAFE_API_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"}
_lock = threading.RLock()
_thread: threading.Thread | None = None
_halt = threading.Event()
_task = {"running": False, "action": "", "output": "", "error": "", "code": None}


def local(request: Request) -> None:
    from .mobile import local as is_local
    if not is_local(request):
        raise HTTPException(403, "설정은 이 PC에서만 변경한다")


def saved() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def selected() -> list[str]:
    state = saved()
    return state.get("agents") or [state.get("agent", "codex")]


def authenticated(provider: str) -> bool:
    if provider != "gh":
        return setup_chat.logged_in(provider)
    result = subprocess.run([*cli_command("gh"), "auth", "status", "--hostname", "github.com"],
                            capture_output=True, timeout=15, **process.background_options())
    return result.returncode == 0


def refresh_path() -> None:
    """New user installs become visible without restarting an already-running app."""
    if os.name != "nt":
        return
    import winreg
    paths = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                paths += os.path.expandvars(winreg.QueryValueEx(handle, "Path")[0]).split(os.pathsep)
        except OSError:
            pass
    paths += [str(Path.home() / ".local/bin"), str(Path.home() / ".cargo/bin"),
              str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links")]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys([*paths, *os.environ.get("PATH", "").split(os.pathsep)]))


def output(text: str) -> None:
    with _lock:
        _task["output"] = errorlog.redact(text[-16000:])


def command(argv: list[str], timeout: float = 900) -> None:
    """One owned process tree; cancellation also reaps installers and login helpers."""
    with tempfile.TemporaryDirectory(prefix="wiki-setup-", ignore_cleanup_errors=True) as folder:
        file = Path(folder) / "output.log"
        with file.open("wb") as capture:
            try:
                _command(argv, timeout, capture, file)
            finally:
                capture.close()
                # Job exit can precede Windows releasing inherited file handles.
                for _ in range(40):
                    try:
                        file.unlink()
                        break
                    except PermissionError:
                        time.sleep(0.05)


def _command(argv, timeout, capture, file):
    child = subprocess.Popen(argv, cwd=DATA, stdin=subprocess.DEVNULL, stdout=capture, stderr=capture,
                             start_new_session=os.name != "nt",
                             creationflags=process.background_options().get("creationflags", 0) | process.SUSPENDED)
    job = process.contained(child)
    try:
        if os.name == "nt" and not job:
            child.kill()
            raise OSError("설치 프로세스의 종료 관리를 준비하지 못했다. 다시 시도하세요")
        process.resumed(child)
        deadline = time.monotonic() + timeout
        text = ""
        while child.poll() is None:
            if _halt.wait(0.2) or runtime.stopping.is_set():
                raise InterruptedError("취소했다. 다시 시도할 수 있다")
            if time.monotonic() > deadline:
                raise TimeoutError("시간 제한을 넘었다. 인터넷과 설치 창을 확인한 뒤 다시 시도하세요")
            with file.open("rb") as reader:
                reader.seek(max(0, file.stat().st_size - 16000))
                text = reader.read().decode("utf-8", errors="replace")
            if text:
                output(text)
        with file.open("rb") as reader:
            reader.seek(max(0, file.stat().st_size - 16000))
            text = reader.read().decode("utf-8", errors="replace")
        output(text or f"종료 코드: {child.returncode}")
        if child.returncode:
            raise ValueError(f"명령 실패 ({child.returncode}). 위 기록을 확인하고 다시 시도하세요")
    finally:
        process.terminated(job)
        if child.poll() is None:
            child.kill()
        child.wait()


def start(action: str, run) -> dict:
    global _thread
    with _lock:
        if _task["running"] or runtime.stopping.is_set():
            raise HTTPException(409, "진행 중인 준비를 끝내거나 취소하세요")
        _halt.clear()
        _task.update(running=True, action=action, output="준비 중…", error="", code=None)
        def work():
            try:
                run()
                with _lock:
                    _task["code"] = 0
            except Exception as exc:
                with _lock:
                    _task.update(error=errorlog.redact(str(exc)), code=130 if isinstance(exc, InterruptedError) else 1)
            finally:
                refresh_path()
                with _lock:
                    _task["running"] = False
        _thread = threading.Thread(target=work, name="desktop-onboarding", daemon=True)
        _thread.start()
        return dict(_task)


def close() -> None:
    _halt.set()
    if _thread:
        _thread.join()


def importing() -> bool:
    with _lock:
        return _task["running"] and _task["action"] == "import" or (DATA / "import-pending.json").exists()


def detect(name: str) -> dict:
    try:
        argv = cli_command(name)
        done = subprocess.run([*argv, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=15, **process.background_options())
        version = (done.stdout or done.stderr).strip()[:250]
        number = re.search(r"(\d+)\.(\d+)", version)
        minimum = {"git": (2, 31), "pwsh": (7, 0)}.get(name)
        ready = done.returncode == 0 and (not minimum or number is not None and tuple(map(int, number.groups())) >= minimum)
        if name == "codex" and ready:
            features = subprocess.run([*argv, "features", "list"], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=15, **process.background_options())
            hooks = re.search(r"^hooks\s+(.+?)\s+(?:true|false)\s*$", features.stdout, re.M)
            ready = features.returncode == 0 and hooks is not None and hooks[1].strip() not in {"removed", "deprecated"}
        return {"ready": ready, "path": argv[0], "version": version,
                **({"error": f"{name}: 지원 버전과 hooks 기능을 확인하고 설치/업데이트하세요"} if not ready else {})}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"ready": False, "error": str(exc)}


@router.get("")
def status(request: Request) -> dict:
    local(request)
    state = saved()
    with _lock:
        task = dict(_task)
    return {"packaged": PACKAGED, "complete": bool(state.get("complete")) if PACKAGED else True,
            "agents": selected(),
            "data": str(DATA), "version": release().get("version", "development"),
            "resources": str(RESOURCES), "python": sys.executable,
            "task": task, "restart_required": bool(state.get("restart_required") and state.get("import_process") == os.getpid())}


@router.get("/detect")
def detection(request: Request) -> dict:
    local(request)
    refresh_path()
    tools = {name: detect(name) for name in ("git", "pwsh", "gh", "fd", "jq", "codex", "claude")}
    accounts = {}
    for provider in ("codex", "claude", "gh"):
        account = {"authenticated": False, "error": ""}
        if tools[provider]["ready"]:
            try:
                account["authenticated"] = authenticated(provider)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                account["error"] = str(exc)
        accounts[provider] = account
    return {"tools": tools, "accounts": accounts, "winget": bool(shutil.which("winget"))}


class Configure(BaseModel):
    agents: list[Literal["claude", "codex"]] = Field(min_length=1, max_length=2)


@router.post("/configure")
def configure(body: Configure, request: Request) -> dict:
    local(request)
    with _lock:
        if _task["running"]:
            raise HTTPException(409, "진행 중인 준비를 끝내거나 취소하세요")
        settings.saved(FILE, {"agents": list(dict.fromkeys(body.agents))})
    return status(request)


class Install(BaseModel):
    tool: Literal["git", "pwsh", "gh", "fd", "jq", "claude", "codex"]


@router.post("/install")
def install(body: Install, request: Request) -> dict:
    local(request)
    if os.name != "nt":
        raise HTTPException(409, "Windows 설치에서만 지원한다")
    def run():
        refresh_path()
        if body.tool == "codex":
            script = DATA / "cache/codex-install.ps1"
            script.parent.mkdir(parents=True, exist_ok=True)
            with urlopen("https://chatgpt.com/codex/install.ps1", timeout=30) as response:
                content = response.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024:
                raise ValueError("공식 설치 스크립트가 예상 크기를 넘었다")
            script.write_bytes(content)
            command([*shell.terminal(), "-ExecutionPolicy", "Bypass", "-File", str(script)])
        else:
            winget = shutil.which("winget")
            if not winget:
                raise ValueError("winget이 없다. Microsoft Store에서 App Installer를 설치한 뒤 다시 시도하세요: https://apps.microsoft.com/detail/9nblggh4nns1")
            command([winget, "install", "--id", PACKAGES[body.tool], "--exact", "--source", "winget",
                     "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"])
        refresh_path()
        if not detect(body.tool)["ready"]:
            raise ValueError(f"설치 명령은 끝났지만 {body.tool}을 실행하지 못했다. 설치 위치와 위 기록을 확인하고 다시 시도하세요")
    return start(f"install:{body.tool}", run)


class Login(BaseModel):
    provider: Literal["claude", "codex", "gh"]


@router.post("/login")
def login(body: Login, request: Request) -> dict:
    local(request)
    provider = body.provider
    def run():
        if not authenticated(provider):
            args = ["auth", "login", "--hostname", "github.com", "--web", "--git-protocol", "https"] if provider == "gh" else setup_chat.AUTH[provider][1]
            command([*cli_command(provider), *args])
        if not authenticated(provider):
            raise ValueError("로그인을 확인하지 못했다. 브라우저에서 본인 계정을 승인한 뒤 다시 시도하세요")
        output(f"{provider}: 로그인 확인 완료")
    return start(f"login:{provider}", run)


@router.post("/cancel")
def cancel(request: Request) -> dict:
    local(request)
    _halt.set()
    return {"ok": True}


class Keys(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


@router.post("/keys")
def keys(body: Keys, request: Request) -> dict:
    local(request)
    if set(body.values) - KEYS or any(len(v) > 4096 or any(c.isspace() or c in "\0\"'" for c in v) for v in body.values.values()):
        raise HTTPException(400, "지원하는 서비스 키를 공백과 따옴표 없이 한 줄로 입력하세요")
    with _lock:
        if _task["running"]:
            raise HTTPException(409, "진행 중인 준비를 끝내거나 취소하세요")
        file = DATA / ".env"
        lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
        remaining = dict(body.values)
        for i, line in enumerate(lines):
            name = line.partition("=")[0].strip()
            if name in remaining:
                lines[i] = f"{name}={remaining.pop(name)}"
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join([*lines, *(f"{k}={v}" for k, v in remaining.items())]) + "\n")
        temporary.replace(file)
    return {"ok": True}


class Import(BaseModel):
    source: str


@router.post("/import")
def import_installation(body: Import, request: Request) -> dict:
    local(request)
    def run():
        backup = desktop_migrate.import_source(Path(body.source), DATA, _halt, output)
        settings.saved(FILE, {"restart_required": True, "import_process": os.getpid(), "backup": str(backup)})
        output(f"복구 사본: {backup}\n앱을 다시 시작하세요. 원본 설치는 그대로 보존했다")
    return start("import", run)


@router.post("/finish")
def finish(request: Request) -> dict:
    local(request)
    with _lock:
        if _task["running"]:
            raise HTTPException(409, "진행 중인 준비를 끝내거나 취소하세요")
        detected = detection(request)
        agents = selected()
        required = ["git", "pwsh", "gh", "fd", "jq", *agents]
        if not all(detected["tools"][name]["ready"] for name in required) or not all(detected["accounts"][name]["authenticated"] for name in agents):
            raise HTTPException(409, "필수 도구 실행과 공식 로그인을 먼저 확인하세요")
        agent = "claude" if "claude" in agents else "codex"
        model = ""
        if agent == "codex":
            models = channels.codex_models()
            model = next((m for m in models if m.get("is_default")), models[0])["id"]
        if not saved().get("complete"):
            settings.saved(DATA / ".chat-local.json", {"model": model})
            channels.LOCAL["model"] = model
        settings.saved(FILE, {"complete": True, "restart_required": False})
        output("도구 실행과 로그인을 확인했다. 프로젝트 경로는 앱의 프로젝트 연결에서 입력하세요")
    return status(request)
