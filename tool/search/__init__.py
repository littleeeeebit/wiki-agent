"""search — ask the search daemon. The wiki chat runs `python tool/search`.

One daemon per machine (`daemon.py`). This side stays light: nothing here
loads a model or starts a server in-process; `spawn` starts the daemon
detached and returns.

Everything that goes wrong is "no answer" — no state file, a refused or slow
connection, a server that cannot prove it holds the token, a different
version, broken JSON. The caller decides what no answer means; the command
line falls back to BM25 in its own process.

A pipeline like the others: it imports no other pipeline and no main, so the
hub's root is read here the way `wiki` reads it, not borrowed from `wiki`.

`__all__` is the contract, held by `lint.pipeline_surface`.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

__all__ = ("ask", "spawn", "PORT", "HUB", "cache_dir", "state_path", "version")

HERE = Path(__file__).resolve().parent
# The hub whose `operator/` and `craft/` every search covers. `WIKI_ROOT` as in `wiki`.
HUB = Path(os.environ.get("WIKI_ROOT") or HERE.parents[1])

# Next to the chat's 8787 and the mirror's 9090, and not the public copy's
# 8790: one machine runs both, and a shared port or state file would have each
# daemon retire the other as a stale version.
PORT = 8791


def cache_dir() -> Path:
    """Where the state file, the model and the vectors live — apart from the
    public copy's `~/.cache/ai-coding-agent-wiki/`, so two first starts never
    replace each other's half-downloaded model or vector cache.
    `WIKI_USER_HOME` stands in for the home in tests, as it does for `apply`."""

    return Path(os.environ.get("WIKI_USER_HOME") or Path.home()) / ".cache/wiki-agent"


def state_path() -> Path:
    return cache_dir() / "searchd.json"


def version() -> str:
    """The hash of every file in this package. After a `git pull` the caller's
    copy differs from what the running daemon reports, and it is replaced."""

    digest = hashlib.sha256()
    for path in sorted(HERE.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:12]


def proof(token: str, nonce: str) -> str:
    """What the daemon answers to a nonce. Only the process that wrote the
    state file knows the token, so a stranger holding the port cannot answer —
    and it is asked before the query, which carries the question, is sent."""

    return hashlib.sha256(f"{token}:{nonce}".encode()).hexdigest()[:16]


def spawn() -> None:
    """Start the daemon detached, and do not wait for it.

    Two callers starting it at once is fine: binding the port is the lock, and
    the second one fails to bind and exits quietly.
    """

    flags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, and out of the caller's
        # job so the host collecting the caller does not collect the daemon.
        flags = 0x00000008 | 0x00000200 | 0x01000000
    # The cache as its working folder, not the caller's: on Windows a process
    # holds its working folder, and a worktree it started from could not be
    # removed for three hours.
    folder = cache_dir()
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True, cwd=folder)
    command = [sys.executable, str(HERE / "daemon.py")]
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            try:
                subprocess.Popen(command, creationflags=flags, **options)
            except OSError:
                # A job that forbids breaking away. The daemon may then die
                # with the caller; the next call starts it again.
                subprocess.Popen(command, creationflags=flags & ~0x01000000, **options)
        else:
            subprocess.Popen(command, start_new_session=True, **options)
    except OSError as error:
        sys.stderr.write(f"searchd not started: {type(error).__name__}\n")


def ask(query: str, project: str | Path | None, timeout: float, k: int = 8,
        wait: float = 0.0, start: bool = True) -> list[dict] | None:
    """The daemon's answer, or `None`.

    `None` for anything short of a proper answer. When no daemon is there it
    is started and this call goes without. No state file means no connection
    attempt at all: a refused connection to localhost takes two seconds on
    Windows. A stale state file costs one timed-out connect, once.

    `wait` asks the daemon to hold the answer until the repository's vectors
    are complete, up to that many seconds.

    `timeout + wait` bounds the whole call, not each read. A socket timeout
    restarts on every byte, so a peer trickling its answer held a 0.15 s ask
    for 12 s in the public copy's review. The exchange runs in a daemon thread
    and is abandoned at the deadline.
    """

    if os.environ.get("WIKI_SEARCH") == "off":
        return None
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
        port, token = int(state["port"]), str(state["token"])
    except Exception:  # noqa: BLE001
        if start:
            spawn()
        return None

    deadline = time.monotonic() + timeout
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        # Here, not in the thread: connect is one bounded operation, and a
        # spawn left to a thread the join gave up on dies with the caller.
        conn.connect()
    except OSError:
        # Nothing listening: the daemon died and left its file behind.
        if start:
            spawn()
        return None
    answer: list = []
    worker = threading.Thread(
        target=lambda: answer.append(exchange(conn, token, query, project, deadline, k, wait, start)),
        daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()) + wait)
    return answer[0] if answer else None


def exchange(conn: http.client.HTTPConnection, token: str, query: str, project,
             deadline: float, k: int, wait: float, start: bool) -> list[dict] | None:
    """One health check and one search on a connected socket. `ask` bounds its time."""

    try:
        nonce = secrets.token_hex(8)
        conn.request("GET", "/health", headers={"X-Wiki-Nonce": nonce})
        health = json.loads(conn.getresponse().read())
        if health.get("proof") != proof(token, nonce):
            # Somebody else holds the port. Starting another daemon would not
            # get it back, and the question is not sent to a stranger.
            return None
        if health.get("version") != version():
            conn.request("POST", "/quit", body=b"{}", headers={"X-Wiki-Token": token})
            conn.getresponse().read()
            if start:
                spawn()
            return None
        body = json.dumps({"query": query, "hub": str(HUB), "project": str(project) if project else None,
                           "k": k, "wait": wait}, ensure_ascii=False)
        conn.sock.settimeout(max(0.001, deadline - time.monotonic()) + wait)
        conn.request("POST", "/search", body=body.encode("utf-8"),
                     headers={"X-Wiki-Token": token, "Content-Type": "application/json"})
        answer = json.loads(conn.getresponse().read())
        results = answer.get("results")
        return results if isinstance(results, list) else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()
