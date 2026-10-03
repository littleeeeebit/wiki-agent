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
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

__all__ = ("ask", "local_index", "evidence_store", "resolve", "spawn", "PORT", "HUB",
           "cache_dir", "state_path", "version", "records", "refresh", "sources", "providers",
           "knowledge_graph", "projection", "retrieval", "retrieve", "published")

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


def retrieve(request: dict, project: str | Path | None, timeout: float, wait: float = 0.0,
             start: bool = True) -> dict | None:
    """The daemon's RetrievalResult for a RetrievalRequest (`retrieval`), or
    `None` — no answer, as for `ask`, and a request naming a generation the
    daemon no longer reads is no answer too.

    Its own endpoint beside `/search`: a client of `ask` keeps getting pages
    with a path and a line, and a chunk-level result is never mistaken for
    them — it carries `retrieval.RESULT` as its schema.
    """

    answer, _started = call("/retrieve", {"request": request, "hub": str(HUB),
                                          "project": str(project) if project else None, "wait": wait},
                            timeout, wait, start)
    return answer if (answer or {}).get("schema_version") == retrieval.RESULT else None


def evidence_store(project: str | Path | None, hub: Path | None = None):
    """The evidence store of `project`'s index beside `hub` (this one by
    default). It holds each chunk's source for checking a citation,
    `.source_of(chunk_id)`, and a private source's English,
    `.english(source_id, texts)` and `.keep_english(source_id, outcomes)`.
    The caller closes it."""

    from .daemon import Store, store_path

    return Store(store_path(Path(hub or HUB), Path(project) if project else None))


def resolve(chunk: dict, path: str | Path) -> str | None:
    """The original span `chunk` cites, read again from `path` — a file, or
    an external source's snapshot — or `None` when that is gone or no longer
    the revision it was cut from."""

    if "path" in chunk["locator"]:
        from .evidence import resolve as read
    else:
        from .sources import resolve as read

    return read(chunk, Path(path))


def records(project: str | Path | None):
    """The external source records of `project` (the hub's without one),
    `sources.Records`. The caller closes it, or uses it in a `with` block."""

    from .sources import Records, records_folder

    return Records(records_folder(Path(project).resolve() if project else HUB))


def refresh(project: str | Path | None) -> None:
    """Bring `project`'s evidence store up to date now — after a memory is
    saved or deleted — rather than at the next question. A running daemon
    sees the store change and reloads."""

    local_index(project).close()


def projection(project: str | Path) -> list[dict]:
    """The map's document-level view of `project`'s knowledge graph:
    `{a, b, kind}` between two of its files.
    Read only: nothing is built, and before the index is there it is `[]`."""

    from .daemon import store_path
    from .evidence import repo_id

    root = Path(project).resolve()
    return knowledge_graph.projection(store_path(HUB, root), repo_id(root))


def published(project: str | Path | None) -> tuple[int | None, str | None]:
    """`(generation, why not)` of `project`'s evidence store, read-only:
    the published generation this code reads, or `None` and why there is
    none this code can read — `no_store`, `not_published`, `other_chunker`
    (its generation, then) or `unreadable`. Nothing is created."""

    from .daemon import store_path
    from .evidence import CHUNKER

    path = store_path(HUB, Path(project).resolve() if project else None)
    if not path.exists():
        return None, "no_store"
    try:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
        try:
            current = knowledge_graph.meta(db, "current")
            row = db.execute("SELECT chunker FROM generations WHERE gen = ?", (int(current),)).fetchone() \
                if current is not None else None
        finally:
            db.close()
    except (sqlite3.Error, ValueError):
        return None, "unreadable"
    if current is None:
        return None, "not_published"
    return int(current), None if row and row[0] == CHUNKER else "other_chunker"


def local_index(project: str | Path | None, hub: Path | None = None, vectors: bool = False, wait: float = 600.0,
                existing: bool = False):
    """An index built in this process, not the daemon's.
    `.search(query, k, sources)` asks it; `.files` holds every file it read;
    `.graph` is its knowledge graph (`knowledge_graph.Graph`).

    Without `vectors`, BM25 only. With them, the daemon's own hybrid ranking:
    the same e5 model and vector cache under `cache_dir()`, waited on up to
    `wait` seconds until every chunk has been tried. `.complete()` says
    whether it got there — an incomplete index ranks with BM25 alone, which
    the caller must not report as hybrid.

    `existing` opens the store as it stands, read-only (`Index(readonly=True)`,
    `refresh(sync=False)`): nothing is created, cut, embedded or rebuilt.
    The caller checks first that the store exists (`published`).
    """

    from .daemon import Embedder, Index

    embedder = Embedder(cache_dir() if vectors else None)
    embedder.start()
    index = Index(Path(hub or HUB), Path(project) if project else None, embedder, readonly=existing)
    index.refresh(sync=not existing)
    end = time.monotonic() + wait
    while vectors and not index.complete() and embedder.state != "off" and time.monotonic() < end:
        time.sleep(0.2)
    return index


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


# The stage 3 to 5 modules, whole, for the main to compose. Last: they import from here.
from . import knowledge_graph, providers, retrieval, sources  # noqa: E402
