"""search — ask the search daemon. The wiki chat runs `python tool/search`;
the keep-alive hooks tell it what their cell is doing with `notify`.

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

__all__ = ("ask", "prepare", "local_index", "evidence_store", "resolve", "notify", "PING", "spawn", "PORT", "HUB",
           "cache_dir", "state_path", "version")

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
        wait: float = 0.0, start: bool = True, sources: list[str] | None = None) -> list[dict] | None:
    """The daemon's answer, or `None`.

    `None` for anything short of a proper answer. When no daemon is there it
    is started and this call goes without.

    `wait` asks the daemon to hold the answer until the repository's vectors
    are complete, up to that many seconds.
    """

    answer, _started = call("/search", {"query": query, "hub": str(HUB),
                                        "project": str(project) if project else None,
                                        "k": k, "wait": wait, "sources": sources}, timeout, wait, start)
    results = (answer or {}).get("results")
    return results if isinstance(results, list) else None


def prepare(query: str, project: str | Path | None, state: str = "", k: int = 8, *,
            evaluate, budget=None, normalize=None, omitted: dict | None = None) -> dict:
    """Jev's retrieval dossier; explicit callers opt into sending evidence to TypeSafe.

    `evaluate` is the decision transport (`decision.evaluate` bound to a
    configuration) and `normalize` English normalization (`translate.english`),
    handed in because this pipeline does not import another. `omitted`
    describes what a summarized `state` left out.
    """
    from .controller import prepare as run

    return run(query, project, state, k, evaluate=evaluate, budget=budget, normalize=normalize, omitted=omitted)


def evidence_store(project: str | Path | None, hub: Path | None = None):
    """The evidence store of `project`'s index beside `hub` (this one by
    default). It holds the deletion journal, read with `.journal("text")` and
    marked with `.cleared("text", ids)`; each chunk's source for checking a
    citation, `.source_of(chunk_id)`; and a private source's English,
    `.english(source_id, texts)` and `.keep_english(source_id, outcomes)`.
    The caller closes it."""

    from .daemon import Store, store_path

    return Store(store_path(Path(hub or HUB), Path(project) if project else None))


def resolve(chunk: dict, path: str | Path) -> str | None:
    """The original span `chunk` cites, read again from `path`, or `None`
    when that file is gone or no longer the revision it was cut from."""

    from .evidence import resolve as read

    return read(chunk, Path(path))


def local_index(project: str | Path | None, hub: Path | None = None, vectors: bool = False, wait: float = 600.0):
    """An index built in this process, not the daemon's.
    `.search(query, k, sources)` asks it; `.files` holds every file it read.

    Without `vectors`, BM25 only. With them, the daemon's own hybrid ranking:
    the same e5 model and vector cache under `cache_dir()`, waited on up to
    `wait` seconds until every chunk has been tried. `.complete()` says
    whether it got there — an incomplete index ranks with BM25 alone, which
    the caller must not report as hybrid.
    """

    from .daemon import Embedder, Index

    embedder = Embedder(cache_dir() if vectors else None)
    embedder.start()
    index = Index(Path(hub or HUB), Path(project) if project else None, embedder)
    index.refresh()
    end = time.monotonic() + wait
    while vectors and not index.complete() and embedder.state != "off" and time.monotonic() < end:
        time.sleep(0.2)
    return index


# The keep-alive ping the daemon types into an idle Claude cell. The hook
# (`keepalive.on_prompt`) takes a turn of exactly this text for the ping once
# the daemon confirms it sent one, and `workspace.INJECTED` carries its
# opening so no count takes it for a person.
PING = 'keep-alive — reply "ok" and nothing else.'
NOTIFY_TIMEOUT = 0.15
# How long a hook that had to start the daemon waits for it before dropping
# the notice.
SPAWN_WAIT = 3.0


def notify(path: str, body: dict, spawn_wait: float | None = None,
           retry: float = 0.0) -> dict | None:
    """Tell the daemon what a cell is doing. Its answer once it took the
    notice — `{"ping": ...}` — or `None`.

    Unlike a search, a notice left undelivered is not free: the timer it would
    have set or cleared stays as it was. Two ways to miss, two waits:

    - No daemon was there, so this started one. Nothing was armed in a daemon
      that was not running; wait up to `spawn_wait` for the new one and send
      again, or drop it — the daemon then errs towards pinging less.
    - A daemon was there and did not answer in time. It may hold a timer this
      notice was meant to clear, so try again for up to `retry` seconds. In
      the public copy's review a `/busy` lost this way let a ping into a turn.

    The whole notice takes at most the larger wait plus two calls, and a call
    is at most `NOTIFY_TIMEOUT` to connect and `NOTIFY_TIMEOUT` to answer —
    the socket timeout cuts a refused connect too, 155 ms measured on Windows.
    With 3 s and 2 s waits that is about 3.6 s, inside the hook's 10 seconds.
    """

    if os.environ.get("WIKI_SEARCH") == "off":
        return None
    answer, started = call(path, body, NOTIFY_TIMEOUT)
    if answer is not None:
        return answer
    wait = (SPAWN_WAIT if spawn_wait is None else spawn_wait) if started else retry
    until = time.monotonic() + wait
    while time.monotonic() < until:
        time.sleep(0.1)
        answer = call(path, body, NOTIFY_TIMEOUT, start=False)[0]
        if answer is not None:
            return answer
    return None


def call(path: str, body: dict, timeout: float, wait: float = 0.0,
         start: bool = True) -> tuple[dict | None, bool]:
    """`(the daemon's JSON answer or None, whether this started a daemon)`.

    No state file means no connection attempt at all: a refused connection to
    localhost takes two seconds on Windows. A stale state file costs one
    timed-out connect, once.

    `timeout + wait` bounds the whole call, not each read. A socket timeout
    restarts on every byte, so a peer trickling its answer held a 0.15 s ask
    for 12 s in the public copy's review. The exchange runs in a daemon thread
    and is abandoned at the deadline.
    """

    if os.environ.get("WIKI_SEARCH") == "off":
        return None, False
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
        port, token = int(state["port"]), str(state["token"])
    except Exception:  # noqa: BLE001
        if start:
            spawn()
        return None, start

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
        return None, start
    answer: list = []
    worker = threading.Thread(
        target=lambda: answer.append(exchange(conn, token, path, body, deadline, wait, start)),
        daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()) + wait)
    return answer[0] if answer else (None, False)


def exchange(conn: http.client.HTTPConnection, token: str, path: str, body: dict,
             deadline: float, wait: float, start: bool) -> tuple[dict | None, bool]:
    """One health check and one request on a connected socket. `call` bounds its time."""

    try:
        nonce = secrets.token_hex(8)
        conn.request("GET", "/health", headers={"X-Wiki-Nonce": nonce})
        health = json.loads(conn.getresponse().read())
        if health.get("proof") != proof(token, nonce):
            # Somebody else holds the port. Starting another daemon would not
            # get it back, and the question is not sent to a stranger.
            return None, False
        if health.get("version") != version():
            conn.request("POST", "/quit", body=b"{}", headers={"X-Wiki-Token": token})
            conn.getresponse().read()
            if start:
                spawn()
            return None, start
        conn.sock.settimeout(max(0.001, deadline - time.monotonic()) + wait)
        conn.request("POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                     headers={"X-Wiki-Token": token, "Content-Type": "application/json"})
        response = conn.getresponse()
        answer = json.loads(response.read())
        return (answer if response.status == 200 and isinstance(answer, dict) else None), False
    except Exception:  # noqa: BLE001
        return None, False
    finally:
        conn.close()
