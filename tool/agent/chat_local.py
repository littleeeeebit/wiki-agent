"""Find this checkout's settings and the current user's executables, nothing else.

Account quota reads the existing CLI credential file without changing it.
"""

import json
import hashlib
from datetime import datetime
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from common.process import background_options

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".chat-local.json"


def settings():
    if not SETTINGS.exists():
        return {}
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) - {"workspace", "model"}
            or any(not isinstance(v, str) for v in data.values())):
        raise ValueError(".chat-local.json의 값은 문자열이어야 합니다. 설치 명령을 다시 실행하세요.")
    return data


def cli_command(name):
    """Use the CLI on PATH as it is. A Windows npm shim does not go through cmd."""
    binary = shutil.which(name)
    if not binary:
        raise FileNotFoundError(f"{name}을 PATH에서 찾지 못했습니다. docs/chat-setup.md의 설치 절차를 확인하세요.")
    path = Path(binary)
    if path.suffix.lower() not in (".cmd", ".bat", ".ps1"):
        return [binary]
    # Read the real executable the shim points at, following the package
    # layout whether that ends at a js file or a native binary. When there are
    # several paths, as in `npm.cmd`, the last one is the entry point that
    # actually runs.
    found = re.findall(r"""node_modules[\\/][^"'\s%]+\.(?:js|cjs|mjs|exe)""",
                       path.read_text(encoding="utf-8", errors="replace"))
    target = path.parent / found[-1].replace("\\", "/") if found else None
    if target and target.is_file():
        if target.suffix.lower() == ".exe":
            return [str(target)]
        node = path.parent / "node.exe"
        node_binary = str(node) if node.is_file() else shutil.which("node")
        if node_binary:
            return [node_binary, str(target)]
    raise ValueError(f"{name} 실행 파일을 확인할 수 없습니다. 공식 CLI 설치 후 다시 시도하세요: {binary}")


class CodexServer:
    """One `codex app-server`, spoken to a request at a time.

    Closing its stdin ends it before it answers, so the requests go down an
    open pipe and each reply is waited for by id. Used by the chat for the
    model list and by `setup_agents` for hook trust.
    """

    def __init__(self, env=None, cwd=None, timeout=25):
        self.proc = subprocess.Popen(
            [*cli_command("codex"), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, **(env or {})}, cwd=cwd, **background_options(),
        )
        self.replies = queue.Queue()
        self.deadline = time.monotonic() + timeout
        self.ids = 0

        def read():
            for line in self.proc.stdout:
                try:
                    self.replies.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.replies.put(None)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def __enter__(self):
        try:
            self.request("initialize", {"clientInfo": {"name": "wiki", "version": "0.1.0"}})
            self.proc.stdin.write('{"method":"initialized"}\n')
            self.proc.stdin.flush()
        except BaseException:
            self.__exit__()
            raise
        return self

    def request(self, method, params):
        self.ids += 1
        self.proc.stdin.write(json.dumps({"id": self.ids, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            try:
                message = self.replies.get(timeout=max(0, self.deadline - time.monotonic()))
            except queue.Empty as exc:
                raise RuntimeError(f"Codex 응답 시간 초과: {method}") from exc
            if message is None:
                raise RuntimeError(f"Codex 연결 종료: {method}")
            if message.get("id") == self.ids:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def __exit__(self, *_exc):
        try:
            self.proc.stdin.close()
        except OSError:
            pass  # A failed startup may already have closed the peer's pipe.
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.reader.join(timeout=1)
        self.proc.stdout.close()


def codex_usage(env=None, cwd=None) -> list[dict]:
    """Public account quota fields only, using the CLI's own login."""
    with CodexServer(env=env, cwd=cwd, timeout=12) as server:
        data = server.request("account/rateLimits/read", {})
    return quota_windows(data)


def quota_windows(data: dict) -> list[dict]:
    buckets = data.get("rateLimitsByLimitId") or {"codex": data.get("rateLimits") or {}}
    return [{"name": f"{name} · {key}", "used_percent": value.get("usedPercent"),
             "window_minutes": value.get("windowDurationMins"), "resets_at": value.get("resetsAt")}
            for name, bucket in buckets.items() for key in ("primary", "secondary")
            if isinstance(value := bucket.get(key), dict)]


_claude_usage_cache: dict = {}
_claude_usage_lock = threading.Lock()


def claude_usage(env=None) -> list[dict]:
    """Read quota with the selected CLI login; never refresh or rewrite credentials.

    Responses close in this call. Cache only public quota fields, keyed by a
    token digest, and cache failures too to avoid hammering a throttled endpoint.
    """
    env = {**os.environ, **(env or {})}
    token = env.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if not token:
        config = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        try:
            saved = json.loads((config / ".credentials.json").read_text(encoding="utf-8"))
            token = (saved.get("claudeAiOauth") or {}).get("accessToken", "")
        except (OSError, ValueError, AttributeError):
            pass
    if not token:
        raise RuntimeError("Claude 계정 로그인 사용량을 확인할 수 없다. Claude CLI 로그인을 확인하세요")
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _claude_usage_lock:
        cached = _claude_usage_cache.get(key)
        if cached and time.monotonic() - cached["at"] < 300:
            if cached.get("error"):
                raise RuntimeError(cached["error"])
            return cached["quota"]
        try:
            request = Request("https://api.anthropic.com/api/oauth/usage", headers={
                "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                "Accept": "application/json", "User-Agent": "wiki-agent"})
            with urlopen(request, timeout=8) as response:
                quota = claude_quota(json.loads(response.read(100_000)))
            if not quota:
                raise ValueError("No quota windows")
        except (HTTPError, URLError, OSError, ValueError) as exc:
            status = exc.code if isinstance(exc, HTTPError) else None
            if isinstance(exc, HTTPError):
                exc.close()
            message = f"Claude 계정 사용량을 받지 못했다{f' (HTTP {status})' if status else ''}. 5분 후 다시 확인한다"
            _claude_usage_cache[key] = {"at": time.monotonic(), "error": message}
            raise RuntimeError(message) from None
        _claude_usage_cache[key] = {"at": time.monotonic(), "quota": quota}
        return quota


def claude_quota(data: dict) -> list[dict]:
    """OAuth utilization is already a percentage; SDK event utilization is a fraction."""
    rows = []
    for name in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "seven_day_oauth_apps"):
        value = data.get(name)
        if not isinstance(value, dict):
            continue
        used, reset = value.get("utilization"), value.get("resets_at")
        if not isinstance(used, (int, float)) or isinstance(used, bool) or not 0 <= used <= 100:
            continue
        if isinstance(reset, str):
            reset = datetime.fromisoformat(reset.replace("Z", "+00:00")).timestamp()
        rows.append({"name": name, "used_percent": used, "resets_at": reset})
    return rows
