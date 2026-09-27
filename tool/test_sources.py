"""Stage 3 of `docs/plans/jev/`: local catalogs, external source records,
the network boundary, adoption and promotion. No external calls: `fetch` and
`arxiv` are replaced where a test needs an answer, and the boundary is
exercised against a local server with the address check narrowed to it.

`WIKI_LIVE_ARXIV=1` adds the one live check, a real arXiv lookup."""

import http.server
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

import decision
import search
from main import knowledge
from search import controller, evidence, providers, sources
from search import daemon as searchd

FEED = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All
      You Need</title>
    <published>2017-06-12T17:57:34Z</published>
    <updated>2023-08-02T00:41:18Z</updated>
    <summary>  The dominant sequence transduction models are based on recurrent networks.
We propose the Transformer, based solely on attention mechanisms.
</summary>
    <link href="https://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf" title="pdf"/>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2005.11401v4</id>
    <title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
    <published>2020-05-22T00:00:00Z</published>
    <summary>We combine parametric memory with a dense retrieval index of Wikipedia.</summary>
    <author><name>Patrick Lewis</name></author>
  </entry>
</feed>"""


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A hub and a git repository whose `.wiki/` git ignores, as most do."""

    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "t")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t@example.com")
    hub, path = tmp_path / "hub", tmp_path / "demo"
    monkeypatch.setattr(search, "HUB", hub)
    monkeypatch.setattr(knowledge, "HUB", hub)
    (hub / "operator").mkdir(parents=True)
    (hub / "operator/rule.md").write_text("# Rule\n\nReview before merge.\n", encoding="utf-8")
    for folder in ("docs", ".wiki/decisions", ".wiki/modules", ".wiki/memory"):
        (path / folder).mkdir(parents=True)
    (path / ".gitignore").write_text(".wiki/\n", encoding="utf-8")
    (path / "docs/guide.md").write_text("# Guide\n\nThe guide explains ports.\n", encoding="utf-8")
    (path / ".wiki/decisions/001-ports.md").write_text("# Ports\n\nWe chose port 8791 for the daemon.\n",
                                                        encoding="utf-8")
    (path / ".wiki/modules/search.md").write_text("# search module\n\nThe search module ranks chunks.\n",
                                                   encoding="utf-8")
    (path / ".wiki/memory/login.md").write_text("# Login\n\nKeep password login.\n", encoding="utf-8")
    (path / ".wiki/memory/login.raw.md").write_text("# Raw\n\nThe whole transcript, password.\n", encoding="utf-8")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "start")
    return hub, path


def index_of(hub: Path, project: Path | None) -> searchd.Index:
    index = searchd.Index(hub, project, searchd.Embedder(None))
    index.refresh()
    return index


def fake_fetch(pages: dict):
    """`providers.fetch` answering from `pages`: url -> (content type, body) or a `FetchError`."""

    def fetch(url, limit=providers.MAX_BYTES, seconds=providers.SECONDS, types=providers.DOCUMENT_TYPES):
        got = pages[url]
        if isinstance(got, Exception):
            raise got
        kind, body = got
        return {"url": url, "content_type": kind, "charset": "utf-8", "body": body}

    return fetch


# ---- local sources --------------------------------------------------------------

def test_ignored_decisions_modules_and_memories_are_listed_but_not_transcripts(repo):
    hub, path = repo
    names = {p.relative_to(path).as_posix() for p in sources.listing(hub, path) if p.is_relative_to(path)}
    assert names == {"docs/guide.md", ".wiki/decisions/001-ports.md", ".wiki/modules/search.md",
                     ".wiki/memory/login.md"}
    index = index_of(hub, path)
    kinds = {c["locator"]["path"]: c["kind"] for c in index.chunks}
    assert kinds[".wiki/decisions/001-ports.md"] == "decision" and kinds[".wiki/memory/login.md"] == "memory"
    assert not index.search("transcript", 5)
    index.close()


def test_one_repositorys_memory_never_reaches_another(repo, tmp_path):
    hub, path = repo
    other = tmp_path / "other"
    (other / ".wiki/memory").mkdir(parents=True)
    (other / ".wiki/memory/secret.md").write_text("# Secret\n\nThe vault code is 4242.\n", encoding="utf-8")
    mine = index_of(hub, path)
    theirs = index_of(hub, other)
    assert not mine.search("vault 4242", 5, ["memory"])
    assert theirs.search("vault 4242", 5, ["memory"])[0]["kind"] == "memory"
    assert mine.store.db is not theirs.store.db and mine.records.folder != theirs.records.folder
    mine.close()
    theirs.close()


def test_a_saved_memory_is_searchable_at_once_and_a_deleted_one_is_gone(repo):
    hub, path = repo
    reader = index_of(hub, path)
    memory = path / ".wiki/memory/cache.md"
    memory.write_text("# Cache\n\nThe cache lives under the home folder.\n", encoding="utf-8")
    search.refresh(path)
    # Another reader — the daemon — sees the store change without cutting the file itself.
    loaded = reader.loaded
    reader.refresh()
    assert reader.loaded != loaded and reader.search("cache home folder", 1)[0]["locator"]["path"].endswith("cache.md")
    memory.unlink()
    search.refresh(path)
    reader.refresh()
    assert not [c for c in reader.chunks if c["locator"]["path"].endswith("cache.md")]
    reader.close()


# ---- the network boundary --------------------------------------------------------

@pytest.mark.parametrize("address, ok", [
    ("8.8.8.8", True), ("2606:4700:4700::1111", True), ("127.0.0.1", False), ("10.1.2.3", False),
    ("192.168.0.4", False), ("172.16.9.9", False), ("169.254.169.254", False), ("100.64.0.1", False),
    ("0.0.0.0", False), ("::1", False), ("fe80::1%3", False), ("fd00::1", False), ("::ffff:127.0.0.1", False),
    ("224.0.0.1", False),
])
def test_only_public_addresses_are_reached(address, ok):
    assert providers.public(address) is ok


@pytest.mark.parametrize("url, reason", [
    ("http://example.com/", "scheme"), ("file:///etc/passwd", "scheme"), ("ftp://example.com/", "scheme"),
    ("https://user:secret@example.com/", "credentials"), ("https://token@example.com/", "credentials"),
    ("https:///nohost", "address"), ("https://example.com:99999/", "address"),
])
def test_urls_that_are_refused_before_any_connection(url, reason, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("resolved a refused URL"))
    with pytest.raises(providers.FetchError) as caught:
        providers.fetch(url)
    assert caught.value.reason == reason


def test_a_host_with_any_private_address_is_refused(monkeypatch):
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
               (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers)
    with pytest.raises(providers.FetchError) as caught:
        providers.fetch("https://rebind.example/")
    assert caught.value.reason == "address"
    with pytest.raises(providers.FetchError) as caught:
        providers.fetch("https://localhost/")
    assert caught.value.reason == "address"


class Site(http.server.BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        Site.seen.append((self.path, self.headers.get("Host")))
        status, headers, body = Site.routes[self.path]
        if callable(body):
            return body(self)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site(monkeypatch):
    """A local plain-HTTP server under a made-up name that resolves to it.
    The address check is narrowed to exactly that address, not switched off."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    real = socket.getaddrinfo
    lookups = []

    def resolve(host, *args, **kwargs):
        lookups.append(host)
        if host == "site.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(providers, "SCHEMES", ("http",))
    monkeypatch.setattr(providers, "public", lambda address: address == "127.0.0.1")
    Site.routes, Site.seen = {}, []
    yield f"http://site.test:{port}", lookups
    server.shutdown()
    server.server_close()


def test_the_connection_goes_to_the_address_that_was_checked(site):
    base, lookups = site
    Site.routes["/doc"] = (200, {"Content-Type": "text/plain; charset=utf-8"}, "Hello 안녕".encode())
    got = providers.fetch(base + "/doc")
    assert got["body"].decode() == "Hello 안녕" and got["content_type"] == "text/plain"
    # The name is resolved once, and `site.test` exists nowhere else: the
    # socket went to the vetted address (whose literal is parsed, not looked up).
    assert [h for h in lookups if h != "127.0.0.1"] == ["site.test"]
    assert Site.seen == [("/doc", base.removeprefix("http://"))]


def test_every_redirect_is_checked_again(site):
    base, _lookups = site
    Site.routes["/ok"] = (302, {"Location": "/final"}, b"")
    Site.routes["/final"] = (200, {"Content-Type": "text/html"}, b"<p>fine</p>")
    Site.routes["/inward"] = (302, {"Location": "http://10.0.0.8/admin"}, b"")
    Site.routes["/secret"] = (301, {"Location": "http://user:pw@site.test/"}, b"")
    Site.routes["/loop"] = (302, {"Location": "/loop"}, b"")
    assert providers.fetch(base + "/ok")["url"] == base + "/final"
    for path, reason in (("/inward", "address"), ("/secret", "credentials"), ("/loop", "redirects")):
        with pytest.raises(providers.FetchError) as caught:
            providers.fetch(base + path)
        assert caught.value.reason == reason


def test_size_type_and_time_ceilings(site):
    base, _lookups = site

    def trickle(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        for _ in range(40):
            handler.wfile.write(b"x")
            handler.wfile.flush()
            time.sleep(0.05)

    def unsized(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        handler.wfile.write(b"y" * 5000)

    Site.routes["/big"] = (200, {"Content-Type": "text/plain", "Content-Length": "999999999"}, b"")
    Site.routes["/unsized"] = (200, {}, unsized)
    Site.routes["/image"] = (200, {"Content-Type": "image/png"}, b"\x89PNG")
    Site.routes["/slow"] = (200, {}, trickle)
    Site.routes["/gone"] = (404, {"Content-Type": "text/plain"}, b"no")
    for path, kwargs, reason in (("/big", {}, "too_large"), ("/unsized", {"limit": 1000}, "too_large"),
                                 ("/image", {}, "content_type"), ("/slow", {"seconds": 0.6}, "timeout"),
                                 ("/gone", {}, "http_404")):
        started = time.monotonic()
        with pytest.raises(providers.FetchError) as caught:
            providers.fetch(base + path, **kwargs)
        assert caught.value.reason == reason
        # A peer trickling bytes cannot hold the fetch past its deadline.
        assert time.monotonic() - started < 1.5


def test_html_becomes_text_with_its_headings():
    title, text = providers.html_text("<html><head><title> A  page </title><style>p{}</style></head><body>"
                                      "<h2>Setup</h2><p>Run <b>it</b>.</p><script>evil()</script><p>Done</p>")
    assert title == "A page" and text == "## Setup\n\nRun it.\n\nDone"


def test_the_arxiv_feed_and_its_error_entries():
    papers = providers.feed(FEED)
    assert [p["arxiv_id"] + p["version"] for p in papers] == ["1706.03762v7", "2005.11401v4"]
    assert papers[0]["title"] == "Attention Is All You Need" and papers[0]["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert papers[0]["abs_url"] == "https://arxiv.org/abs/1706.03762v7"
    assert papers[1]["pdf_url"] == "https://arxiv.org/pdf/2005.11401v4"
    error = (b"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/api/errors#bad</id>"
             b"<summary>incorrect id format for 1234</summary></entry></feed>")
    with pytest.raises(providers.FetchError) as caught:
        providers.feed(error)
    assert caught.value.reason == "arxiv_error"
    with pytest.raises(providers.FetchError):
        providers.feed(b"<not xml")


def test_arxiv_searches_all_fields_and_keeps_its_interval(monkeypatch):
    asked = []
    monkeypatch.setattr(providers, "fetch", lambda url, *a: asked.append((url, time.monotonic())) or
                        {"body": FEED, "url": url, "content_type": "application/atom+xml", "charset": None})
    monkeypatch.setattr(providers, "ARXIV_GAP", 0.3)
    providers.arxiv("graph retrieval", n=3)
    providers.arxiv(ids=["1706.03762"])
    assert "search_query=all%3Agraph+AND+all%3Aretrieval" in asked[0][0] and "max_results=3" in asked[0][0]
    assert "id_list=1706.03762" in asked[1][0]
    assert asked[1][1] - asked[0][1] >= 0.3


# ---- external records --------------------------------------------------------------

def test_a_url_is_indexed_cited_by_its_snapshot_and_one_record_per_document(repo, monkeypatch):
    hub, path = repo
    url = "https://docs.example.com/guide?x=1"
    body = b"<title>Guide</title><h2>Retries</h2><p>Retry three times with backoff.</p>"
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/html", body)}))
    record = knowledge.add_url(path, "HTTPS://Docs.Example.com:443/guide?x=1#part")
    assert sources.problems(record) == [] and record["status"] == "indexed" and record["coverage"] == "full_text"
    assert record["origin"] == url and record["title"] == "Guide"
    again = knowledge.add_url(path, url)
    assert again["source_id"] == record["source_id"] and again["editions"] == []
    index = index_of(hub, path)
    hit = index.search("retry backoff", 1, ["research"])[0]
    chunk = evidence.contract(hit)
    assert chunk["kind"] == "research" and chunk["locator"]["url"] == url
    assert search.resolve(chunk, hit["path"]) == chunk["original_text"] == "## Retries\n\nRetry three times with backoff."
    # Runtime data lives in the user's cache, never in the checkout.
    assert Path(hit["path"]).is_relative_to(search.cache_dir()) and git(path, "status", "--porcelain") == ""
    index.close()


def test_changed_content_is_a_new_edition_and_the_old_citation_still_resolves(repo, monkeypatch):
    hub, path = repo
    url = "https://example.com/notes.md"
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/markdown", b"# Notes\n\nUse port 80.\n")}))
    knowledge.add_url(path, url)
    index = index_of(hub, path)
    old = evidence.contract(index.search("port", 1, ["research"])[0])
    old_path = index.search("port", 1, ["research"])[0]["path"]
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/markdown", b"# Notes\n\nUse port 443.\n")}))
    record = knowledge.add_url(path, url)
    assert len(record["editions"]) == 1 and record["editions"][0]["content_hash"] == old["revision"]
    index.refresh()
    hits = index.search("port", 5, ["research"])
    assert len(hits) == 1 and "443" in hits[0]["text"]
    assert search.resolve(old, old_path) == "# Notes\n\nUse port 80."
    index.close()


def test_failed_fetches_are_visible_and_never_cost_what_was_read(repo, monkeypatch):
    hub, path = repo
    url = "https://example.com/a.txt"
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: providers.FetchError("timeout")}))
    record = knowledge.add_url(path, url)
    assert record["status"] == "unavailable" and record["error"]["reason"] == "timeout"
    assert record["content_hash"] is None
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/plain", b"Alpha beta gamma.")}))
    knowledge.add_url(path, url)
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: providers.FetchError("http_503")}))
    record = knowledge.add_url(path, url)
    assert record["status"] == "indexed" and record["error"]["reason"] == "http_503"
    index = index_of(hub, path)
    assert index.search("gamma", 1, ["research"])
    index.close()


def test_a_disabled_source_is_neither_searched_nor_fetched_and_removal_takes_its_snapshots(repo, monkeypatch):
    hub, path = repo
    url = "https://example.com/b.txt"
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/plain", b"Delta epsilon.")}))
    record = knowledge.add_url(path, url)
    index = index_of(hub, path)
    assert index.search("epsilon", 1)
    knowledge.switch(path, record["source_id"][:10], False)
    index.refresh()
    assert not index.search("epsilon", 5)
    assert controller.families(path) == []
    with pytest.raises(ValueError):
        knowledge.add_url(path, url)
    snapshot = search.records(path)
    held = snapshot.snapshot(record["content_hash"])
    snapshot.close()
    assert held.exists()
    assert knowledge.forget(path, record["source_id"]) and not held.exists()
    index.close()


@pytest.mark.parametrize("after", ["graph", "snapshot"])
def test_forgetting_a_source_takes_its_cached_extractions_too(repo, monkeypatch, after):
    hub, path = repo
    url = "https://example.com/c.txt"
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/plain", b"Zeta uses eta.")}))
    record = knowledge.add_url(path, url)
    index = index_of(hub, path)
    chunk = next(c for c in index.chunks if c.get("record"))
    key = (chunk["source_id"], search.knowledge_graph.digest(chunk["text"]))
    result = {"entities": [{"name": "Zeta", "type": "feature", "quote": "Zeta uses eta."}], "relations": []}
    # What an extraction run holds from its start: the records' chunks, asked again at each write.
    external = knowledge.still(path, {key})
    assert search.knowledge_graph.keep(index.store, [(*key, "v", result)], external) == 1
    search.knowledge_graph.activate(index.store, "v")
    index.close()
    index = index_of(hub, path)

    def quoted():
        with index.store.lock:
            return index.store.db.execute("SELECT quote FROM spans WHERE source_id = ? UNION ALL SELECT label"
                                          " FROM nodes WHERE source_id = ?", (key[0], key[0])).fetchall()

    assert ("Zeta uses eta.",) in quoted()
    # A local source's full id is never taken for a forgotten record: its graph stays.
    local = next(c["source_id"] for c in index.chunks if not c.get("record"))
    with index.store.lock:
        before = index.store.db.execute("SELECT COUNT(*) FROM nodes WHERE source_id = ?", (local,)).fetchone()
    with pytest.raises(KeyError):
        knowledge.forget(path, local)
    with index.store.lock:
        assert index.store.db.execute("SELECT COUNT(*) FROM nodes WHERE source_id = ?", (local,)).fetchone() == before
    assert before[0]
    # An evidence store that could not be opened forgets nothing, and says so.
    opened, delete = knowledge.evidence_store, sources.Records.delete
    monkeypatch.setattr(knowledge, "evidence_store", lambda _p: searchd.Store(None))
    with pytest.raises(OSError):
        knowledge.forget(path, record["source_id"])
    monkeypatch.setattr(knowledge, "evidence_store", opened)
    # A record deletion that fails before its commit changes nothing.
    monkeypatch.setattr(sources.Records, "delete", lambda *_a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        knowledge.forget(path, record["source_id"])
    monkeypatch.setattr(sources.Records, "delete", delete)
    assert ("Zeta uses eta.",) in quoted() and search.knowledge_graph.cached(index.store, "v")
    # Something failing after the record's commit: the graph step, or a snapshot left behind.
    graph_step = search.knowledge_graph.forget
    if after == "graph":
        monkeypatch.setattr(search.knowledge_graph, "forget", lambda *_a: (_ for _ in ()).throw(OSError("disk")))
    else:
        def committed_then_failed(store, source):
            delete(store, source)
            raise OSError("snapshot")
        monkeypatch.setattr(sources.Records, "delete", committed_then_failed)
    with pytest.raises(OSError) as failed:
        knowledge.forget(path, record["source_id"])
    monkeypatch.setattr(search.knowledge_graph, "forget", graph_step)
    monkeypatch.setattr(sources.Records, "delete", delete)
    with search.records(path) as held:
        assert held.get(record["source_id"]) is None
    if after == "graph":
        # The record is gone; its full id still finishes the graph step.
        assert record["source_id"] in "".join(failed.value.__notes__)
        assert ("Zeta uses eta.",) in quoted()
        assert knowledge.forget(path, record["source_id"])
    # Nothing that quotes it is left: no cached extraction, and no node or span in any generation.
    assert search.knowledge_graph.cached(index.store, "v") == {} and quoted() == []
    # A model answer arriving after the forget is not cached.
    assert search.knowledge_graph.keep(index.store, [(*key, "v", result)], external) == 0
    assert search.knowledge_graph.cached(index.store, "v") == {}
    index.close()


def papers_fixture(monkeypatch, entries=None):
    entries = entries or providers.feed(FEED)
    monkeypatch.setattr(providers, "arxiv", lambda query=None, ids=None, n=5, seconds=0: entries[:n])
    return entries


def test_an_arxiv_abstract_is_never_taken_for_the_full_text(repo, monkeypatch):
    hub, path = repo
    papers_fixture(monkeypatch)
    out = knowledge.add_papers(path, "attention transformer", n=2)
    assert [p["coverage"] for p in out["papers"]] == ["abstract_only", "abstract_only"]
    assert controller.families(path) == ["papers"]
    index = index_of(hub, path)
    hit = index.search("Transformer attention mechanisms", 1, ["papers"])[0]
    chunk = controller.item(hit, {"text": hit["text"], "status": "original_english", "language": "en"})
    assert chunk["kind"] == "paper" and chunk["coverage"] == "abstract_only"
    assert chunk["locator"]["url"] == "https://arxiv.org/abs/1706.03762v7"
    assert chunk["source_record"]["revision"] == "1706.03762v7"
    assert search.resolve(chunk, hit["path"]) == chunk["original_text"]
    # The dossier says so: a read of what the abstract stands for is still owed.
    assert controller.reads([chunk]) == [{"chunk_id": chunk["chunk_id"], "path": hit["path"],
                                          "locator": chunk["locator"], "reason": "abstract_only"}]
    index.close()


def test_a_full_text_that_fails_to_extract_leaves_the_abstract_and_says_why(repo, monkeypatch):
    hub, path = repo
    papers_fixture(monkeypatch)
    monkeypatch.setattr(providers, "fetch", fake_fetch({"https://arxiv.org/pdf/1706.03762v7":
                                                        ("application/pdf", b"%PDF-1.4 broken")}))
    out = knowledge.add_papers(path, ids=["1706.03762"], n=1, full=True)
    paper = out["papers"][0]
    assert paper["coverage"] == "abstract_only" and paper["status"] == "indexed"
    assert paper["error"]["reason"] in ("extraction_failed", "no_text", "no_pdf_extractor")


def test_without_an_extractor_a_pdf_is_a_visible_failure(repo, monkeypatch, tmp_path):
    _hub, path = repo
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name))
                        if name == "pypdf" else real(name, *a, **k))
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")
    record = knowledge.add_file(path, paper)
    assert record["status"] == "unavailable" and record["error"]["reason"] == "no_pdf_extractor"
    unsupported = tmp_path / "paper.docx"
    unsupported.write_bytes(b"PK")
    assert knowledge.add_file(path, unsupported)["error"]["reason"] == "unsupported_type"


def test_a_pdf_is_cited_by_page_and_block(repo, tmp_path):
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("pypdf")
    hub, path = repo
    document = fitz.open()
    for words in ("First page about sparse retrieval.", "Second page about graph expansion."):
        document.new_page().insert_text((72, 72), words)
    paper = tmp_path / "paper.pdf"
    document.save(paper)
    record = knowledge.add_file(path, paper)
    assert record["status"] == "indexed" and record["coverage"] == "full_text" and record["form"] == "pages"
    index = index_of(hub, path)
    hit = index.search("graph expansion", 1, ["papers"])[0]
    chunk = evidence.contract(hit)
    assert chunk["locator"] == {"document": paper.as_posix(), "page": 2, "block": 0}
    assert search.resolve(chunk, hit["path"]) == chunk["original_text"]
    assert "graph expansion" in chunk["original_text"]
    index.close()


def test_a_local_markdown_paper_keeps_its_lines_and_an_edit_is_a_new_edition(repo, tmp_path):
    hub, path = repo
    paper = tmp_path / "notes.md"
    paper.write_text("# Notes\n\nFirst claim.\n\n## Method\n\nBM25 then rerank.\n", encoding="utf-8")
    knowledge.add_file(path, paper)
    index = index_of(hub, path)
    chunk = evidence.contract(index.search("rerank", 1, ["papers"])[0])
    assert chunk["locator"] == {"path": paper.as_posix(), "start_line": 5, "end_line": 7}
    paper.write_text("# Notes\n\nFirst claim.\n\n## Method\n\nDense retrieval only.\n", encoding="utf-8")
    assert len(knowledge.add_file(path, paper)["editions"]) == 1
    index.close()


def test_jev_decides_what_is_read_first_and_its_failure_costs_no_paper(repo, monkeypatch):
    hub, path = repo
    papers_fixture(monkeypatch)
    monkeypatch.setattr(decision, "evaluate", lambda cfg, state, questions, trace, budget, stage:
                        {"0": 0.9, "1": 0.05})
    # Shadow records the grade and changes nothing: every paper is read.
    shadow = decision.Config("shadow", decision.MODEL, "file", key="k")
    out = knowledge.add_papers(path, "transformer attention", n=2, cfg=shadow)
    assert [(p["status"], p["relevance"]) for p in out["papers"]] == [("indexed", 0.9), ("indexed", 0.05)]
    for paper in out["papers"]:
        knowledge.forget(path, paper["source_id"])
    on = decision.Config("active", decision.MODEL, "file", key="k")
    out = knowledge.add_papers(path, "transformer attention", n=2, cfg=on)
    assert [(p["status"], p["relevance"]) for p in out["papers"]] == [("indexed", 0.9), ("discovered", 0.05)]
    index = index_of(hub, path)
    assert not index.search("dense retrieval Wikipedia parametric", 5, ["papers"])
    index.close()

    def broken(*_args):
        raise decision.JevError("quota", 429)

    monkeypatch.setattr(decision, "evaluate", broken)
    out = knowledge.add_papers(path, "transformer attention", n=2, cfg=on)
    assert [p["status"] for p in out["papers"]] == ["indexed", "indexed"]
    assert out["trace"][-1]["reason"] == "quota"


# ---- adoption --------------------------------------------------------------------

def test_decisions_need_read_content_and_reasons_and_a_rejection_is_found_again(repo, monkeypatch):
    hub, path = repo
    url = "https://example.com/c.txt"
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: providers.FetchError("timeout")}))
    record = knowledge.add_url(path, url)
    with pytest.raises(ValueError):
        knowledge.decide(path, record["source_id"], "rejected", rationale="unreachable")
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/plain", b"Use a global lock for every index.")}))
    knowledge.add_url(path, url)
    with pytest.raises(ValueError):
        knowledge.decide(path, record["source_id"], "adopted", rationale="fine")
    rejected = knowledge.decide(path, record["source_id"], "rejected", rationale="serializes unrelated repositories",
                                counterevidence=["per-index locks measured faster"])
    assert rejected["status"] == "rejected" and rejected["adoption"]["counterevidence"]
    index = index_of(hub, path)
    hit = index.search("serializes unrelated repositories", 1)[0]
    assert hit["record"]["decision"] == "rejected" and hit["record"]["rationale"] == "serializes unrelated repositories"
    # The rejection is part of what is searched, never of the cited original.
    assert "serializes" not in hit["text"]
    index.close()


def test_new_content_undoes_a_decision_made_on_the_old(repo, monkeypatch):
    _hub, path = repo
    url = "https://example.com/d.txt"
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/plain", b"Version one.")}))
    record = knowledge.add_url(path, url)
    knowledge.decide(path, record["source_id"], "adopted", rationale="matches", claims=["one"], scope="all")
    monkeypatch.setattr(providers, "fetch", fake_fetch({url: ("text/plain", b"Version two.")}))
    record = knowledge.add_url(path, url)
    assert record["status"] == "indexed" and record["adoption"] is None
    assert record["editions"][0]["adoption"]["decision"] == "adopted"


def test_promotion_is_a_worktree_commit_and_the_checkout_is_untouched(repo, monkeypatch):
    _hub, path = repo
    papers_fixture(monkeypatch)
    paper = knowledge.add_papers(path, ids=["1706.03762"], n=1)["papers"][0]
    with pytest.raises(ValueError):
        knowledge.promote(path, paper["source_id"])
    knowledge.decide(path, paper["source_id"], "adopted", rationale="The abstract states the design we use.",
                     claims=["Attention alone can replace recurrence for sequence transduction."],
                     scope="Reranking passages in tool/search.", counterevidence=["No retrieval benchmark."],
                     conditions=["Revisit when stage 10 measures reranking."])
    before = git(path, "status", "--porcelain"), git(path, "rev-parse", "HEAD")
    out = knowledge.promote(path, paper["source_id"])
    assert (git(path, "status", "--porcelain"), git(path, "rev-parse", "HEAD")) == before
    assert not (path / out["file"]).exists()
    tree = Path(out["worktree"])
    assert git(tree, "branch", "--show-current").strip() == out["branch"] == "research-attention-is-all-you-need"
    assert git(tree, "diff", "--name-only", "main", "HEAD").split() == [out["file"]]
    page = (tree / out["file"]).read_text(encoding="utf-8")
    assert "abstract only — the full text was not read" in page and "<https://arxiv.org/abs/1706.03762v7>" in page
    # A summary with its link, never the source's text.
    assert "We propose the Transformer" not in page


# ---- review round 1 ----------------------------------------------------------------

def test_a_credential_bearing_url_is_refused_before_it_is_canonicalized(repo, monkeypatch):
    _hub, path = repo
    monkeypatch.setattr(providers, "fetch", lambda *a, **k: pytest.fail("fetched a URL carrying credentials"))
    with pytest.raises(providers.FetchError) as caught:
        knowledge.add_url(path, "https://user:secret@example.com/doc")
    assert caught.value.reason == "credentials"
    store = search.records(path)
    # Nothing recorded: the secret is never written, and no stripped URL stands in for the request.
    assert store.all() == []
    store.close()


def test_an_ipv6_url_keeps_its_brackets():
    assert sources.canonical_url("https://[2606:4700:4700::1111]:443/doc#x") == "https://[2606:4700:4700::1111]/doc"
    assert sources.canonical_url("https://[2606:4700:4700::1111]:8443/doc") == "https://[2606:4700:4700::1111]:8443/doc"


def test_two_sources_with_the_same_bytes_are_two_pieces_of_evidence(repo, monkeypatch):
    hub, path = repo
    body = ("text/plain", b"Identical words in two places.")
    monkeypatch.setattr(providers, "fetch", fake_fetch({"https://a.example/doc": body, "https://b.example/doc": body}))
    a = knowledge.add_url(path, "https://a.example/doc")
    b = knowledge.add_url(path, "https://b.example/doc")
    index = index_of(hub, path)
    hits = index.search("identical words", 5, ["research"])
    assert {(h["source_id"], h["locator"]["url"]) for h in hits} == {
        (a["source_id"], "https://a.example/doc"), (b["source_id"], "https://b.example/doc")}
    index.close()


def test_a_new_arxiv_version_with_the_same_abstract_is_a_new_edition_and_undecided(repo, monkeypatch):
    _hub, path = repo
    v1 = providers.feed(FEED)[:1]
    papers_fixture(monkeypatch, v1)
    paper = knowledge.add_papers(path, ids=["1706.03762"], n=1)["papers"][0]
    knowledge.decide(path, paper["source_id"], "adopted", rationale="fits", claims=["c"], scope="s")
    v2 = [{**v1[0], "version": "v8", "abs_url": "https://arxiv.org/abs/1706.03762v8"}]
    papers_fixture(monkeypatch, v2)
    knowledge.add_papers(path, ids=["1706.03762"], n=1)
    store = search.records(path)
    record = store.get(paper["source_id"])
    store.close()
    assert record["revision"] == "1706.03762v8" and record["status"] == "indexed" and record["adoption"] is None
    assert [(e["revision"], e["adoption"]["decision"]) for e in record["editions"]] == [("1706.03762v7", "adopted")]


def test_the_deadline_bounds_name_resolution(site, monkeypatch):
    real = socket.getaddrinfo

    def slow(host, *args, **kwargs):
        time.sleep(0.5)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    started = time.monotonic()
    with pytest.raises(providers.FetchError) as caught:
        providers.fetch("http://example.com/", seconds=0.1)
    assert caught.value.reason == "timeout" and time.monotonic() - started < 0.4


def test_a_trickled_header_cannot_outlast_the_deadline(site):
    base, _lookups = site

    def headers(handler):
        handler.wfile.write(b"HTTP/1.1 200 OK\r\n")
        for _ in range(30):
            handler.wfile.write(b"X-Slow: y\r\n")
            handler.wfile.flush()
            time.sleep(0.05)

    Site.routes["/headers"] = (200, {}, headers)
    started = time.monotonic()
    with pytest.raises(providers.FetchError) as caught:
        providers.fetch(base + "/headers", seconds=0.5)
    assert caught.value.reason == "timeout" and time.monotonic() - started < 1.0


def test_a_stalled_tls_handshake_cannot_outlast_the_deadline(monkeypatch):
    """Round 2: the connection takes part of the budget, then the peer says nothing."""

    silent = socket.create_server(("127.0.0.1", 0))
    port = silent.getsockname()[1]
    held = []
    threading.Thread(target=lambda: held.append(silent.accept()), daemon=True).start()
    real = socket.create_connection

    def slow(*args, **kwargs):
        time.sleep(0.15)
        return real(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))])
    monkeypatch.setattr(socket, "create_connection", slow)
    monkeypatch.setattr(providers, "public", lambda address: address == "127.0.0.1")
    started = time.monotonic()
    with pytest.raises(providers.FetchError) as caught:
        providers.fetch(f"https://tls.test:{port}/", seconds=0.2)
    assert caught.value.reason == "timeout" and time.monotonic() - started < 0.3
    silent.close()


def test_an_unknown_charset_falls_back_to_utf8(repo, monkeypatch):
    _hub, path = repo
    url = "https://example.com/odd"

    def fetch(*_a, **_k):
        return {"url": url, "content_type": "text/plain", "charset": "x-no-such-charset", "body": b"Plain words."}

    monkeypatch.setattr(providers, "fetch", fetch)
    record = knowledge.add_url(path, url)
    assert record["status"] == "indexed"


def test_record_contract_refuses_what_it_cannot_stand_behind(repo):
    _hub, path = repo
    record = sources.new(path, "paper", "arxiv:1")
    assert sources.problems(record) == []
    assert sources.problems({**record, "status": "indexed"})
    assert sources.problems({**record, "status": "unavailable"})
    assert sources.problems({**record, "status": "adopted"})
    assert sources.problems({**record, "authority": "trusted"})


@pytest.mark.skipif(os.environ.get("WIKI_LIVE_ARXIV") != "1", reason="live arXiv lookup; WIKI_LIVE_ARXIV=1")
def test_live_arxiv_paper_becomes_evidence(repo, monkeypatch):
    hub, path = repo
    out = knowledge.add_papers(path, ids=["1706.03762"], n=1, cfg=decision.Config("off", decision.MODEL, "default"))
    assert out["papers"][0]["status"] == "indexed" and out["papers"][0]["coverage"] == "abstract_only"
    index = index_of(hub, path)
    hit = index.search("Transformer attention recurrence", 1, ["papers"])[0]
    chunk = evidence.contract(hit)
    assert search.resolve(chunk, hit["path"]) == chunk["original_text"]
    index.close()
