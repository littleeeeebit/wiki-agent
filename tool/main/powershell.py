"""Machine-wide PowerShell installation, owned and stopped by the server."""

import os
import shutil
import threading

from fastapi import APIRouter, HTTPException, Request

from common import shell
from . import runtime, specs, work
from .query import ROOT

router = APIRouter()
_install_lock = threading.RLock()
_install_thread: threading.Thread | None = None
_install = {"running": False, "output": "", "code": None}


def status() -> dict:
    with _install_lock:
        return {**shell.status(), "winget": bool(shutil.which("winget")),
                "download": shell.DOWNLOAD, "install": dict(_install)}


def close_install() -> None:
    if _install_thread:
        _install_thread.join()


@router.get("/api/shell/terminal")
def terminal_shell() -> dict:
    try:
        return {"argv": shell.terminal()}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/shell/install")
def install_powershell(request: Request) -> dict:
    global _install_thread
    from . import mobile

    if not mobile.local(request):
        raise HTTPException(403, "PowerShell 설치는 PC 화면에서만 시작한다")
    if runtime.stopping.is_set():
        raise HTTPException(409, "서버가 종료 중이다")
    if os.name != "nt":
        raise HTTPException(409, "Windows에서만 PowerShell을 설치한다")
    if not shutil.which("winget"):
        return {"download": shell.DOWNLOAD, "reason": "winget이 없어 공식 다운로드 페이지를 연다"}
    with _install_lock:
        if _install["running"]:
            return {"ok": True}
        _install.update(running=True, output="PowerShell 7 설치를 시작한다…", code=None)

        def output(data: bytes) -> None:
            with _install_lock:
                _install["output"] = data.decode("utf-8", errors="replace")
            work.feed.put({"kind": "connect", "repo": ""})

        def run() -> None:
            token = runtime.gate_output.set(output)
            try:
                command = shell.resolve("install-powershell")
                code, text, cut = runtime.gate(command, ROOT, runtime.stopping, None, 15 * 60, None, specs.kill)
                with _install_lock:
                    _install.update(code=code, output=cut or text[-16000:] or f"설치 명령 종료 코드: {code}")
                shell.detect(refresh=True)
                if code == 0 and shell.status()["needs_install"]:
                    with _install_lock:
                        _install["output"] += "\nPowerShell 7을 찾지 못했다. 설치 결과를 확인하고 앱을 다시 시작하세요."
            except Exception as exc:  # noqa: BLE001 -- the screen must retain an install failure
                with _install_lock:
                    _install.update(code=None, output=str(exc))
            finally:
                runtime.gate_output.reset(token)
                with _install_lock:
                    _install["running"] = False
                work.feed.put({"kind": "connect", "repo": ""})

        _install_thread = threading.Thread(target=run, name="install-powershell", daemon=True)
        _install_thread.start()
    return {"ok": True}
