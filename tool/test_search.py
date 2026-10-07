"""The search daemon and its client, without a model.

The embedder is off throughout, or a fake stands in for it, so the daemon
ranks with BM25 alone — the same path it takes while the model downloads or
where `onnxruntime` is missing. Nothing here downloads anything or binds the
real port.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search  # noqa: E402
from search import daemon as searchd  # noqa: E402

RULE = """---
severity: contract
triggers: ["zzz"]
---

# {title}

Rule. {rule}

## Detail

{detail}
"""


def wiki(root: Path) -> Path:
    (root / "operator").mkdir(parents=True)
    (root / "craft").mkdir()
    (root / "operator" / "merge.md").write_text(RULE.format(
        title="Clean up after a merge", rule="Delete the merged branch.",
        detail="```\n## not a heading\n```\nPrune the remote too."), encoding="utf-8")
    (root / "craft" / "fonts.md").write_text(RULE.format(
        title="Type scale", rule="Keep the type scale small.", detail="화면 글꼴 크기를 줄인다."),
        encoding="utf-8")
    return root


@pytest.fixture
def hub(monkeypatch):
    root = wiki(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(search, "HUB", root)
    return root


def test_terms_are_english_words_and_hangul_bigrams():
    assert searchd.terms("Hook 훅이 느리다 v2_x") == ["hook", "훅이", "느리", "리다", "v2_x"]
    assert searchd.terms("훅") == ["훅"]


def test_chunks_cut_at_headings_and_know_their_lines():
    text = RULE.format(title="T", rule="R.", detail="```\n## fenced\n```\nbody")
    found = searchd.chunks(text, Path("x.md"))
    assert [c["line"] for c in found] == [6, 10], found
    assert found[1]["heading"] == "T > Detail"
    assert "## fenced" in found[1]["text"], "a heading inside a fence cut the chunk"
    assert text.splitlines()[found[1]["line"] - 1] == "## Detail"
    bare = searchd.chunks("# T\n\nintro\n\n## Steps\n\n### 1. First\n\ndo it\n", Path("x.md"))
    assert [c["heading"] for c in bare] == ["T", "T > Steps > 1. First"], bare


def test_a_fence_closes_only_on_its_own_marker():
    """A `~~~` inside a backtick fence toggled the state: the heading after it
    in the fence became a section, and the real one after the fence did not
    (review round 1)."""

    text = "# T\n\n## A\n\n```md\n~~~\n## fake\n```\n\n## Real\n\nbody\n"
    assert [c["heading"] for c in searchd.chunks(text, Path("x.md"))] == ["T", "T > A", "T > Real"]
    longer = "# T\n\n## A\n\n~~~~\n~~~\n## fake\n~~~~~\n\n## Real\n\nbody\n"
    assert [c["heading"] for c in searchd.chunks(longer, Path("x.md"))] == ["T", "T > A", "T > Real"]
    # Not a fence at all: a backtick in a backtick opener's info string (round 2).
    inline = "# T\n\n## A\n\n```js`x\n\n## Real\n\nbody\n"
    assert [c["heading"] for c in searchd.chunks(inline, Path("x.md"))] == ["T", "T > A", "T > Real"]
    tilde = "# T\n\n## A\n\n~~~ a`b\n## fake\n~~~\n\n## Real\n\nbody\n"
    assert [c["heading"] for c in searchd.chunks(tilde, Path("x.md"))] == ["T", "T > A", "T > Real"]


def test_headings_are_read_as_commonmark_reads_them():
    """Three shapes round 3 found: an indented heading, a heading ending in
    `#`, and a `#` title inside a code sample at the top of the page."""

    def headings(text):
        return [c["heading"] for c in searchd.chunks(text, Path("x.md"))]

    assert headings("# T\n\nintro\n\n   ## Deploy\n\nbody\n") == ["T", "T > Deploy"]
    assert headings("# T\n\n    ## code\n\nbody\n") == ["T"], "four spaces is a code block"
    assert headings("# T\n\n## C#\n\nbody\n\n## Done ##\n\nend\n") == ["T", "T > C#", "T > Done"]
    assert headings("```md\n# Fake\n```\n# Real\n\n## Deploy\n\nbody\n") == ["Real", "Real > Deploy"]
    assert headings("####### not\n\n#no-space\n\nbody\n") == ["x"], "neither is a heading"


def test_the_index_ranks_the_hub_and_the_repository_by_bm25(hub):
    project = Path(tempfile.mkdtemp())
    (project / "docs").mkdir()
    (project / "docs" / "deploy.md").write_text("# 배포\n\n## 순서\n\n태그를 먼저 민다.\n", encoding="utf-8")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "x.md").write_text("# 배포 태그\n", encoding="utf-8")
    index = searchd.Index(hub, project, searchd.Embedder(None))
    index.refresh()
    hits = index.search("merged branch 머지", 5)
    assert Path(hits[0]["path"]).stem == "merge", hits
    assert hits[0]["cos"] is None, "no vectors, so no cosine"
    assert Path(index.search("글꼴 크기", 5)[0]["path"]).stem == "fonts"
    found = index.search("배포 태그", 5)
    assert Path(found[0]["path"]).name == "deploy.md", found
    assert "node_modules" not in {Path(h["path"]).parent.name for h in found}


def test_an_edited_page_is_cut_again(hub):
    index = searchd.Index(search.HUB, None,searchd.Embedder(None))
    index.refresh()
    page = hub / "craft" / "fonts.md"
    page.write_text(page.read_text(encoding="utf-8").replace("small", "quokka"), encoding="utf-8")
    os.utime(page, ns=(1, 10**18))
    index.refresh()
    assert Path(index.search("quokka", 1)[0]["path"]).stem == "fonts"


def embedded(fails, db=None):
    """An index over the test hub, embedded by a fake model one chunk per
    batch. A passage `fails` picks raises; every other one points away from
    the query, so its cosine is negative."""

    import sqlite3

    numpy = pytest.importorskip("numpy")
    embedder = searchd.Embedder(Path(tempfile.mkdtemp()))
    embedder.np, embedder.state = numpy, "ready"

    def encode(texts, prefix):
        if prefix == "passage: " and any(fails(t) for t in texts):
            raise RuntimeError("no memory")
        side = 1.0 if prefix == "query: " else -1.0
        return numpy.array([[side, 0.0]] * len(texts), dtype=numpy.float32)

    embedder.encode = encode
    index = searchd.Index(search.HUB, None,embedder)
    index.refresh()
    if db is None:
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE v (k TEXT PRIMARY KEY, v BLOB)")
    while not embedder.jobs.empty():
        embedder.store([embedder.jobs.get()], db)
    return index, embedder, db


def test_a_chunk_that_fails_to_embed_does_not_stall_the_index(hub):
    """Left pending it was never queued again and the index never completed —
    every chat request waited out its minute."""

    index, embedder, db = embedded(lambda text: True)
    assert not embedder.pending
    assert index.complete()
    lexical = searchd.Index(search.HUB, None,searchd.Embedder(None))
    lexical.refresh()
    assert index.search("merged branch", 5) == lexical.search("merged branch", 5), (
        "an index where nothing embedded must rank with BM25 alone"
    )
    assert db.execute("SELECT COUNT(*) FROM v").fetchone()[0] == 0


def test_a_chunk_that_failed_is_not_in_the_vector_ranking(hub):
    """Given a zero vector, it outranked every negative cosine."""

    index, _embedder, _db = embedded(lambda text: text.startswith("Type scale"))
    hits = {Path(h["path"]).stem: h for h in index.search("nothing matches zzqq", 5)}
    assert "fonts" not in hits, "a failed chunk took a vector rank"
    assert hits["merge"]["cos"] < 0


def test_a_cache_that_cannot_be_written_keeps_the_worker_going(hub):
    """A write failure killed the worker and left every later chunk pending."""

    import sqlite3

    closed = sqlite3.connect(":memory:")
    closed.close()
    index, embedder, _db = embedded(lambda text: False, db=closed)
    assert not embedder.pending and index.complete()
    assert index.search("merged branch", 1)[0]["cos"] is not None


@pytest.fixture
def reachable(monkeypatch):
    """`ask` switched on, with `spawn` recorded instead of run."""

    monkeypatch.delenv("WIKI_SEARCH", raising=False)
    spawned = []
    monkeypatch.setattr(search, "spawn", lambda: spawned.append(1))
    search.state_path().parent.mkdir(parents=True, exist_ok=True)
    yield spawned
    search.state_path().unlink(missing_ok=True)


class Running:
    """A daemon on a free port with a state file in the test home."""

    def __init__(self, token="secret"):
        self.daemon = searchd.Daemon(token, searchd.Embedder(None))
        self.server = searchd.serve(0, self.daemon)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        search.state_path().write_text(json.dumps({"port": self.port, "token": "secret"}),
                                       encoding="utf-8")

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def running(hub, reachable):
    runs = []

    def start(**kw):
        runs.append(Running(**kw))
        return runs[-1]

    yield start
    for run in runs:
        run.close()


def test_ask_gets_an_answer_from_a_running_daemon(running, reachable):
    running()
    hits = search.ask("merged branch", None, timeout=5)
    assert hits and Path(hits[0]["path"]).stem == "merge", hits
    assert not reachable


def test_a_server_that_cannot_prove_the_token_is_not_sent_the_query(running, reachable):
    run = running(token="someone-else")
    asked = []
    real = run.daemon.search
    run.daemon.search = lambda *a: asked.append(a) or real(*a)
    assert search.ask("merged branch", None, timeout=5) is None
    assert not asked, "the question went to a server that did not hold the token"
    assert not reachable, "another daemon cannot take a port somebody holds"


def test_a_stale_version_is_told_to_quit_and_replaced(running, reachable, monkeypatch):
    run = running()
    monkeypatch.setattr(searchd, "RUNNING", "old")
    assert search.ask("merged branch", None, timeout=5) is None
    assert reachable == [1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", run.port), timeout=0.2).close()
        except OSError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the stale daemon was not told to quit")


def test_each_asker_searches_its_own_hub(running, tmp_path, monkeypatch):
    """Two checkouts at the same version share one daemon. Indexed from the
    daemon's own hub, the second would search the first one's rules — or a
    removed worktree's."""

    running()
    other = tmp_path / "other"
    (other / "operator").mkdir(parents=True)
    (other / "craft").mkdir()
    (other / "operator" / "quokka.md").write_text("# Quokka rule\n\nRule. Feed the quokka.\n",
                                                  encoding="utf-8")
    assert Path(search.ask("merged branch", None, timeout=5)[0]["path"]).stem == "merge"
    monkeypatch.setattr(search, "HUB", other)
    hits = search.ask("quokka merged branch", None, timeout=5)
    assert [Path(h["path"]).stem for h in hits] == ["quokka"], hits


def test_the_version_covers_every_file_in_the_package(monkeypatch, tmp_path):
    """Hashing `daemon.py` alone would leave a daemon running an old
    `__init__.py` it imported — `proof` and `HUB` live there."""

    for name in ("__init__.py", "daemon.py"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(search, "HERE", tmp_path)
    before = search.version()
    (tmp_path / "__init__.py").write_text("y", encoding="utf-8")
    assert search.version() != before


def test_no_state_file_starts_the_daemon_without_connecting(reachable):
    search.state_path().unlink(missing_ok=True)
    assert search.ask("q", None, timeout=0.15) is None
    assert reachable == [1]


def test_switched_off_it_neither_connects_nor_starts(reachable, monkeypatch):
    monkeypatch.setenv("WIKI_SEARCH", "off")
    search.state_path().unlink(missing_ok=True)
    assert search.ask("q", None, timeout=0.15) is None
    assert not reachable


def test_a_dead_daemon_left_behind_is_replaced(reachable):
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    search.state_path().write_text(json.dumps({"port": port, "token": "t"}), encoding="utf-8")
    assert search.ask("q", None, timeout=0.15) is None
    assert reachable == [1]


def test_a_peer_trickling_its_answer_does_not_hold_the_caller(reachable):
    """A socket timeout restarts on every byte. Held per read, a 0.15 s ask
    took 12 s against this peer."""

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def trickle():
        conn, _ = listener.accept()
        conn.recv(4096)
        for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 100:
            try:
                conn.send(bytes([byte]))
            except OSError:
                return
            time.sleep(0.05)

    threading.Thread(target=trickle, daemon=True).start()
    search.state_path().write_text(json.dumps({"port": listener.getsockname()[1], "token": "t"}),
                                   encoding="utf-8")
    try:
        started = time.perf_counter()
        assert search.ask("q", None, timeout=0.15) is None
        assert time.perf_counter() - started < 0.5
    finally:
        listener.close()


def test_owned_retrieval_cancels_and_collects_its_socket_worker(reachable):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    accepted = threading.Event()
    finished = threading.Event()
    cancel = threading.Event()

    def peer():
        conn, _ = listener.accept()
        try:
            conn.recv(4096)
            accepted.set()
            while conn.recv(4096):
                pass
        finally:
            conn.close()
            finished.set()

    server = threading.Thread(target=peer)
    server.start()
    search.state_path().write_text(json.dumps({"port": listener.getsockname()[1], "token": "t"}), encoding="utf-8")
    timer = threading.Timer(0.15, cancel.set)
    before = set(threading.enumerate())
    timer.start()
    try:
        started = time.perf_counter()
        assert search.retrieve({}, None, 2, start=False, cancel=cancel) is None
        assert accepted.is_set() and time.perf_counter() - started < 1
        assert finished.wait(1) and not reachable
        assert not {t for t in threading.enumerate() if t not in before and t is not timer}
    finally:
        cancel.set()
        listener.close()
        timer.join(2)
        server.join(2)


def test_the_port_is_the_lock():
    daemon = searchd.Daemon("t", searchd.Embedder(None))
    first = searchd.serve(0, daemon)
    try:
        assert searchd.serve(first.server_address[1], daemon) is None
    finally:
        first.server_close()


def test_quit_needs_the_token(running):
    import http.client

    run = running()
    conn = http.client.HTTPConnection("127.0.0.1", run.port, timeout=5)
    conn.request("POST", "/quit", body=b"{}", headers={"X-Wiki-Token": "wrong"})
    assert conn.getresponse().status == 403
    conn.close()
    assert search.ask("merged branch", None, timeout=5), "a bad token stopped it"


def test_the_command_line_answers_with_path_and_line_when_switched_off(tmp_path):
    root = wiki(tmp_path / "hub")
    project = tmp_path / "repo"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "deploy.md").write_text("# 배포\n\n## 순서\n\n태그를 먼저 민다.\n", encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(HERE / "search"), "배포 순서 merged branch", "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env=dict(os.environ, WIKI_ROOT=str(root), WIKI_SEARCH="off"))
    assert out.returncode == 0, out.stderr
    assert "## docs/deploy.md:3 — 배포 > 순서" in out.stdout, out.stdout
    assert f"## {(root / 'operator' / 'merge.md').as_posix()}:" in out.stdout, out.stdout


def test_each_hit_carries_its_neighbours_once(tmp_path):
    """One hop through the hub's `graph.json` and the repository's
    `.wiki/graph.json`, with the gist from the node or `corpus.json`. A page
    already shown, or listed under an earlier hit, is not listed again."""

    root = wiki(tmp_path / "hub")
    (root / "graph.json").write_text(json.dumps({
        "nodes": [{"id": "craft/fonts", "headline": "Type scale", "rule": "Keep it small."},
                  {"id": "craft/colour", "headline": "Colour", "rule": "Two accents."}],
        "links": [{"a": "operator/merge", "b": "craft/colour"},
                  {"a": "operator/merge", "b": "craft/fonts"},
                  {"a": "craft/fonts", "b": "craft/colour"}]}), encoding="utf-8")
    project = tmp_path / "repo"
    (project / ".wiki").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / "docs" / "deploy.md").write_text("# 배포\n\n## 순서\n\n태그를 먼저 민다.\n", encoding="utf-8")
    (project / ".wiki" / "graph.json").write_text(json.dumps(
        {"edges": [{"a": "docs/deploy.md", "b": "docs/release.md"}]}), encoding="utf-8")
    (project / ".wiki" / "corpus.json").write_text(json.dumps(
        {"docs": [{"path": "docs/release.md", "title": "릴리스", "lead": "태그가 먼저다."}]}), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(HERE / "search"), "배포 순서 merged branch", "--project", str(project), "--k", "2"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env=dict(os.environ, WIKI_ROOT=str(root), WIKI_SEARCH="off"))
    assert out.returncode == 0, out.stderr
    assert "- `docs/release.md` — 릴리스 — 태그가 먼저다." in out.stdout, out.stdout
    assert "- `craft/colour` — Colour — Two accents." in out.stdout, out.stdout
    assert "- `craft/fonts` — Type scale — Keep it small." in out.stdout, out.stdout
    assert out.stdout.count("`craft/colour`") == 1, out.stdout
