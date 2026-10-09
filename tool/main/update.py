"""update — say when GitHub's `origin/main` moved past the build the person opened.

Orca's way: check quietly in the background, never interrupt, let the person
pick the moment. A failed check stays silent; an update is a card, not a
dialog; "later" hides that one version only; nothing restarts on its own.

`STARTED` is the commit this server loaded. A pull while it runs changes the
checkout, not the running code, so the card then asks for a restart instead.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from agent import cli_command

from . import mobile
from .channels import WIKI

router = APIRouter()

CHECK_EVERY = 3600.0    # seconds between fetches; a person keeps the window open for days
SHOWN = 20              # commit subjects on the card


def git(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=WIKI, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def ancestor(older: str, newer: str) -> bool:
    return git("merge-base", "--is-ancestor", older, newer).returncode == 0


STARTED = git("rev-parse", "HEAD").stdout.strip()
_lock = threading.Lock()
_applying = threading.Lock()
_cached: tuple[float, dict] | None = None


def check() -> dict:
    """The update state, from a fresh fetch of `origin/main`."""

    try:
        if git("fetch", "--quiet", "origin", "main", timeout=60).returncode:
            return {"state": "unknown"}
        remote = git("rev-parse", "origin/main").stdout.strip()
        head = git("rev-parse", "HEAD").stdout.strip()
        if not remote or not STARTED or ancestor(remote, STARTED):
            return {"state": "current", "running": STARTED[:7]}
        if ancestor(remote, head):
            return {"state": "ready", "running": STARTED[:7], "remote": remote[:7]}
        rows = git("log", f"--max-count={SHOWN}", "--format=%h%x1f%s%x1f%cI", f"HEAD..{remote}").stdout.splitlines()
        behind = int(git("rev-list", "--count", f"HEAD..{remote}").stdout.strip() or 0)
        branch = git("branch", "--show-current").stdout.strip()
        blocked = ("main 브랜치에서만 업데이트할 수 있습니다" if branch != "main"
                   else "커밋하지 않은 변경이 있습니다" if git("status", "--porcelain", "--untracked-files=no").stdout.strip()
                   else "로컬 main 이 origin/main 과 갈라졌습니다" if not ancestor(head, remote)
                   else "")
        return {"state": "available", "running": STARTED[:7], "remote": remote[:7], "behind": behind,
                "commits": [dict(zip(("sha", "subject", "at"), r.split("\x1f"))) for r in rows],
                "blocked": blocked}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"state": "unknown"}


def status(force: bool = False) -> dict:
    global _cached
    with _lock:
        if force or _cached is None or time.monotonic() - _cached[0] > CHECK_EVERY:
            _cached = (time.monotonic(), check())
        return _cached[1]


def changed(before: str, after: str) -> list[str]:
    return git("diff", "--name-only", before, after).stdout.splitlines()


def apply() -> dict:
    """Fast-forward `main`, then bring the screen and its packages up to it.

    The steps are `setup_chat.install`'s, run only for what the pull changed.
    The running server keeps its code; the card then asks for a restart.
    """

    if not _applying.acquire(blocking=False):
        raise HTTPException(409, "업데이트를 이미 받는 중입니다")
    try:
        found = status(force=True)
        if found["state"] != "available":
            return found
        if found["blocked"]:
            raise HTTPException(409, found["blocked"])
        before = git("rev-parse", "HEAD").stdout.strip()
        pulled = git("pull", "--ff-only", "--quiet", "origin", "main", timeout=120)
        if pulled.returncode:
            raise HTTPException(409, f"git pull 실패 — {(pulled.stderr or pulled.stdout).strip()[-500:]}")
        for name, cwd, command in steps(changed(before, git("rev-parse", "HEAD").stdout.strip())):
            done = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=900)
            if done.returncode:
                return {**status(force=True), "error": f"{name} 실패 — {(done.stderr or done.stdout).strip()[-500:]}"}
        return status(force=True)
    finally:
        _applying.release()


def steps(paths: list[str]) -> list[tuple[str, object, list[str]]]:
    """What a pull that changed `paths` still needs before a restart can use it."""

    found = []
    if "requirements-chat.txt" in paths:
        found.append(("pip install", WIKI, [sys.executable, "-m", "pip", "install", "-q", "-r", "requirements-chat.txt"]))
    if "web/package-lock.json" in paths:
        found.append(("npm ci", WIKI / "web", [*cli_command("npm"), "ci"]))
    if any(p.startswith("web/") and not p.startswith("web/src-tauri/") for p in paths):
        # The old build's hashed chunks stay; this open window still imports them.
        found.append(("npm run build", WIKI / "web", [*cli_command("npm"), "run", "build"]))
    return found


@router.get("/api/update")
def update_state(force: bool = False) -> dict:
    return status(force)


@router.post("/api/update")
def update_apply(request: Request) -> dict:
    # A paired phone may read the card; only the desktop changes its checkout.
    if not mobile.local(request):
        raise HTTPException(403, "업데이트는 PC 에서만 받을 수 있습니다")
    return apply()
