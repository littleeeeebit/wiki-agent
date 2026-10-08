"""Stage 2 of `docs/plans/jev/`: evidence chunks, their original provenance,
English normalization and the store. No credentials and no external calls:
the translator's request (`translate._ask`) is replaced where a test needs an
answer from it."""

import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import decision
import search
import translate
from main import knowledge
from search import cache_dir, evidence
from search import daemon as searchd
from test_decision_flow import answering, found

ACTIVE = decision.Config("active", "jev-1.13.0", "file", key="k")


def jev(monkeypatch, hits: list[dict], seen: list[dict], answer=None) -> None:
    """Every round finds `hits`; Jev is a fake that keeps each state it is sent."""

    monkeypatch.setattr(knowledge, "run_round", lambda req, project, budget: found(hits))

    def evaluate(cfg, state, questions, trace, budget, stage):
        budget.call()
        seen.append(state)
        trace.append({"stage": stage, "model": cfg.model, "usage": {"input_tokens": 1, "output_tokens": 1}})
        return (answer or answering())(stage, state, questions)

    monkeypatch.setattr(decision, "evaluate", evaluate)


def index_of(hub: Path, repo: Path | None) -> searchd.Index:
    index = searchd.Index(hub, repo, searchd.Embedder(None))
    index.refresh()
    return index


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A hub and a repository. The hub stands in for this one, as
    `main.knowledge` finds the store beside it."""

    hub, repo = tmp_path / "hub", tmp_path / "repo"
    monkeypatch.setattr(search, "HUB", hub)
    for folder in (hub / "operator", repo / "docs", repo / ".wiki/memory"):
        folder.mkdir(parents=True)
    (hub / "operator/rule.md").write_text("# Rule\n\nReview before merge.\n", encoding="utf-8")
    (repo / "docs/ports.md").write_text("# Ports\n\n## Search\n\nThe daemon listens on 8791.\n", encoding="utf-8")
    (repo / "docs/limit.md").write_text("# 한도\n\n번역 요청이 한도를 넘으면 멈춘다.\n", encoding="utf-8")
    (repo / ".wiki/memory/login.md").write_text("# Login\n\nKeep password login.\n", encoding="utf-8")
    return hub, repo


def by_path(index: searchd.Index) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for chunk in index.chunks:
        found.setdefault(chunk["locator"]["path"], []).append(chunk)
    return found


def bump(path: Path, text: str) -> None:
    """Write and move the modification time on, as a later edit would."""

    path.write_text(text, encoding="utf-8")
    stamp = path.stat().st_mtime_ns + 10**9
    os.utime(path, ns=(stamp, stamp))


# ---- contract -----------------------------------------------------------------

def test_every_hit_is_a_valid_evidence_chunk_that_resolves_to_its_original(corpus):
    hub, repo = corpus
    index = index_of(hub, repo)
    hits = index.search("daemon 8791 한도 password review", 10)
    assert len(hits) == 4
    for hit in hits:
        chunk = evidence.contract(hit)
        assert chunk["translation"] == {"status": "pending", "version": None} and chunk["text_en"] is None
        # The citation is exactly the original's lines, read again from the file.
        assert evidence.resolve(chunk, Path(index.store.source_of(chunk["chunk_id"]))) == chunk["original_text"]
    kinds = {h["locator"]["path"]: (h["kind"], h["visibility"]) for h in hits}
    assert kinds == {"operator/rule.md": ("rule", "shared"), "docs/ports.md": ("document", "repository"),
                     "docs/limit.md": ("document", "repository"), ".wiki/memory/login.md": ("memory", "private")}
    index.close()


@pytest.mark.parametrize("locator, ok", [
    ({"path": "a.md", "start_line": 1, "end_line": 2}, True),
    ({"path": "a.md", "start_line": 0, "end_line": 2}, False),
    ({"path": "a.md", "start_line": 3, "end_line": 2}, False),
    ({"path": "a.md", "start_line": None, "end_line": None}, False),   # unknown is not line 1
    ({"path": "a.md"}, False),
    ({"url": "https://arxiv.org/abs/1", "snapshot": "a" * 64, "start": 0, "end": 10}, True),
    ({"url": "https://arxiv.org/abs/1", "snapshot": "latest", "start": 0, "end": 10}, False),
    ({"url": "file:///etc/passwd", "snapshot": "a" * 64, "start": 0, "end": 10}, False),
    ({"document": "paper.pdf", "page": 3, "block": 0}, True),
    ({"document": "paper.pdf", "page": 0, "block": 0}, False),
])
def test_locators_have_three_shapes_and_no_invented_position(locator, ok):
    assert (evidence.locator_problem(locator) is None) is ok


def test_english_that_did_not_come_about_cannot_look_as_if_it_had(corpus):
    hub, repo = corpus
    hit = index_of(hub, repo).search("한도", 1)[0]
    failed = {"text": None, "status": "unavailable", "language": "ko", "reason": "no_key"}
    chunk = evidence.contract(hit, {**failed, "text": hit["text"]})
    assert chunk["translation"] == {"status": "unavailable", "version": None, "reason": "no_key"}
    assert chunk["text_en"] is None, "the original was passed off as English"
    assert evidence.problems({**chunk, "text_en": hit["text"]}) == ["unavailable with English text"]
    with pytest.raises(ValueError):
        evidence.contract(hit, {"text": None, "status": "translated", "language": "ko"})


def test_the_same_sentence_in_two_sources_is_two_pieces_of_evidence(tmp_path):
    for name in ("one", "two"):
        (tmp_path / name / "docs").mkdir(parents=True)
        (tmp_path / name / "docs/x.md").write_text("# X\n\nSame sentence.\n", encoding="utf-8")
    (tmp_path / "hub/operator").mkdir(parents=True)
    one, two = (index_of(tmp_path / "hub", tmp_path / n).search("same sentence", 1)[0] for n in ("one", "two"))
    assert one["text"] == two["text"]
    assert one["repo_id"] != two["repo_id"] and one["chunk_id"] != two["chunk_id"]
    assert searchd.store_path(tmp_path / "hub", tmp_path / "one") != searchd.store_path(tmp_path / "hub", tmp_path / "two")


# ---- chunking -----------------------------------------------------------------

def test_a_long_section_is_cut_at_its_blocks_and_labels_what_it_had_to_cut(tmp_path):
    paragraph = " ".join(["word"] * 120)            # about 600 characters
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| row {i} | {'v' * 40} |" for i in range(60))
    fence = "```\n" + "\n".join("code line " * 20 for _ in range(12)) + "\n\nstill code\n```"
    items = "- one\n\n  continued\n- two"
    text = f"# T\n\n## Long\n\n{paragraph}\n\n{paragraph}\n\n{paragraph}\n\n{items}\n\n{table}\n\n{fence}\n\nend\n"
    path = tmp_path / "long.md"
    path.write_text(text, encoding="utf-8")
    found = searchd.chunks(text, path)
    lines = text.splitlines()
    assert found[0]["text"] == "# T" and found[0]["heading_path"] == ["T"]
    long = found[1:]
    for chunk in long:
        # Exactly the original's lines; the heading path is context, not quoted text.
        assert chunk["text"] == "\n".join(lines[chunk["line"] - 1:chunk["end_line"]])
        assert chunk["heading_path"] == ["T", "Long"]
        if chunk["completeness"] != "oversized":
            assert searchd.size(chunk["text"]) <= searchd.MAX_CHUNK
    shapes = [c["completeness"] for c in long]
    assert shapes.count("whole") >= 2 and "partial" in shapes and shapes.count("oversized") == 1
    oversized = next(c for c in long if c["completeness"] == "oversized")
    assert oversized["text"].startswith("```") and oversized["text"].endswith("```"), "a fence was cut"
    assert "- one\n\n  continued\n- two" in next(c["text"] for c in long if "- one" in c["text"]), "a loose list was cut"
    assert long[0]["text"].startswith("## Long") and not long[1]["text"].startswith("## Long")


def test_hangul_counts_double_toward_the_ceiling(tmp_path):
    korean = "\n\n".join("가" * 400 for _ in range(3))    # 1200 characters, 2400 by weight
    found = searchd.chunks(f"# T\n\n{korean}\n", tmp_path / "k.md")
    assert len(found) == 3 and all(c["completeness"] == "whole" for c in found)


# ---- updates ------------------------------------------------------------------

def test_edits_renames_and_deletions_never_return_obsolete_chunks(corpus):
    hub, repo = corpus
    index = index_of(hub, repo)
    before = {c["chunk_id"] for c in index.chunks}
    ports = repo / "docs/ports.md"
    old = by_path(index)["docs/ports.md"][-1]
    bump(ports, "# Ports\n\n## Search\n\nThe daemon listens on 9999.\n")
    index.refresh()
    new = by_path(index)["docs/ports.md"][-1]
    assert "8791" in old["text"] and "9999" in new["text"]
    assert new["chunk_id"] != old["chunk_id"] and old["chunk_id"] not in {c["chunk_id"] for c in index.chunks}
    # The old citation no longer resolves: the file is not the revision it was cut from.
    assert evidence.resolve(evidence.contract(old), ports) is None
    assert index.search("8791", 5) == [] and index.search("9999", 1)[0]["chunk_id"] == new["chunk_id"]

    ports.rename(repo / "docs/network.md")
    index.refresh()
    assert "docs/ports.md" not in by_path(index) and "docs/network.md" in by_path(index)
    (repo / "docs/network.md").unlink()
    index.refresh()
    assert "docs/network.md" not in by_path(index) and index.search("9999", 5) == []
    assert before - {c["chunk_id"] for c in index.chunks} >= {old["chunk_id"]}

    # A second index on the same store, as the daemon and a command line share
    # it, sees the change another made.
    other = index_of(hub, repo)
    bump(repo / "docs/limit.md", "# 한도\n\n한도를 넘어도 캐시는 답한다.\n")
    other.refresh()
    index.refresh()
    assert "캐시는" in by_path(index)["docs/limit.md"][0]["text"]
    index.close()
    other.close()


def test_a_stale_read_cannot_bring_back_a_memory_another_process_deleted(corpus, monkeypatch):
    """Review round 1: one store read the edited memory and paused while
    another deleted the file and its row; the first then wrote it back."""

    import threading

    hub, repo = corpus
    memory = repo / ".wiki/memory/login.md"
    first, second = searchd.Store(searchd.store_path(hub, repo)), searchd.Store(searchd.store_path(hub, repo))

    def listed():
        return [(memory, repo, memory.resolve(), False)] if memory.exists() else []

    first.sync(listed())
    bump(memory, "# Login\n\nKeep password login, edited.\n")
    real, reading, paused = searchd.chunks, threading.Event(), threading.Event()

    def slow(*args):
        reading.set()
        paused.wait(5)
        return real(*args)

    monkeypatch.setattr(searchd, "chunks", slow)
    worker = threading.Thread(target=first.sync, args=(listed(),))
    worker.start()
    assert reading.wait(5)
    memory.unlink()
    monkeypatch.setattr(searchd, "chunks", real)
    second.sync([])
    paused.set()
    worker.join()
    assert second.db.execute("SELECT count(*) FROM sources").fetchone() == (0,)
    assert second.db.execute("SELECT count(*) FROM chunks WHERE text LIKE '%password%'").fetchone() == (0,)
    first.close()
    second.close()


def test_a_failed_update_keeps_what_was_there(corpus, monkeypatch):
    hub, repo = corpus
    index = index_of(hub, repo)
    before = [c["chunk_id"] for c in index.chunks]
    bump(repo / "docs/ports.md", "# Ports\n\nChanged.\n")

    def full_disk(*_a, **_k):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(searchd.Store, "remove", full_disk)
    index.refresh()   # fails inside the transaction
    assert [c["chunk_id"] for c in index.chunks] == before
    fresh = index_of(hub, repo)
    assert [c["chunk_id"] for c in fresh.chunks] == before, "a half-written update reached the store"
    fresh.close()
    monkeypatch.undo()
    index.refresh()
    assert "Changed." in by_path(index)["docs/ports.md"][0]["text"]
    index.close()


def journalled(hub: Path, repo: Path) -> int:
    with sqlite3.connect(searchd.store_path(hub, repo)) as db:
        return db.execute("SELECT count(*) FROM journal").fetchone()[0]


def test_a_deleted_memory_leaves_no_text_or_vector_behind(corpus):
    hub, repo = corpus
    index = index_of(hub, repo)
    memory = by_path(index)[".wiki/memory/login.md"][0]
    # Its vector on disk, as code before this one wrote a private memory's.
    vectors = cache_dir() / "vectors.sqlite3"
    db = sqlite3.connect(vectors)
    db.execute("CREATE TABLE IF NOT EXISTS v (k TEXT PRIMARY KEY, v BLOB)")
    db.execute("INSERT OR REPLACE INTO v VALUES (?, ?)", (memory["key"], b"\0" * 4))
    db.commit()
    db.close()

    (repo / ".wiki/memory/login.md").unlink()
    index.refresh()
    assert ".wiki/memory/login.md" not in by_path(index) and index.search("password", 5) == []
    with sqlite3.connect(vectors) as db:
        assert db.execute("SELECT count(*) FROM v WHERE k = ?", (memory["key"],)).fetchone() == (0,)
    with sqlite3.connect(searchd.store_path(hub, repo)) as db:
        assert db.execute("SELECT count(*) FROM chunks WHERE text LIKE '%password%'").fetchone() == (0,)
    assert journalled(hub, repo) == 0
    index.close()


def test_a_private_title_edit_drops_the_old_vector_and_keeps_the_unchanged_one(corpus):
    hub, repo = corpus
    memory = repo / ".wiki/memory/titled.md"
    memory.write_text("# OldPrivate\n\nIntro.\n\n## Stable\n\nSame words.\n", encoding="utf-8")
    index = index_of(hub, repo)
    old = {c["heading"]: c["key"] for c in by_path(index)[".wiki/memory/titled.md"]}
    other = by_path(index)["docs/ports.md"][0]["key"]
    for key in [*old.values(), other]:
        index.embedder.vectors[key] = b"vector"
    # The same text under a new title is a new vector key (review round 3).
    bump(memory, "# NewPrivate\n\nIntro.\n\n## Stable\n\nSame words.\n")
    index.refresh()
    assert not set(old.values()) & set(index.embedder.vectors), "a vector of the old title was left"
    assert other in index.embedder.vectors
    # An edit that keeps a chunk's title and text keeps its vector.
    kept = {c["heading"]: c["key"] for c in by_path(index)[".wiki/memory/titled.md"]}
    for key in kept.values():
        index.embedder.vectors[key] = b"vector"
    bump(memory, "# NewPrivate\n\nIntro, edited.\n\n## Stable\n\nSame words.\n")
    index.refresh()
    assert kept["NewPrivate > Stable"] in index.embedder.vectors
    assert kept["NewPrivate"] not in index.embedder.vectors
    index.close()


def test_a_vector_cache_that_cannot_be_written_leaves_the_journal_to_finish_it(corpus, monkeypatch):
    hub, repo = corpus
    index = index_of(hub, repo)
    (repo / ".wiki/memory/login.md").unlink()
    monkeypatch.setattr(searchd, "drop_vectors", lambda keys: False)
    index.refresh()
    assert journalled(hub, repo) >= 1
    monkeypatch.undo()
    index.refresh()
    assert journalled(hub, repo) == 0
    index.close()


def test_a_private_vector_is_never_written_and_one_dropped_while_queued_is_not_kept(tmp_path):
    np = pytest.importorskip("numpy")
    embedder = searchd.Embedder(tmp_path)
    embedder.state = "ready"
    embedder.encode = lambda texts, prefix: np.ones((len(texts), 4), dtype=np.float32)
    db = sqlite3.connect(tmp_path / "vectors.sqlite3")
    db.execute("CREATE TABLE v (k TEXT PRIMARY KEY, v BLOB)")
    embedder.want([("shared", "a rule"), ("memory", "a memory"), ("gone", "a deleted memory")], {"memory", "gone"})
    embedder.drop(["gone"])   # its memory was deleted while it waited in the queue
    embedder.store([embedder.jobs.get() for _ in range(3)], db)
    assert sorted(k for (k,) in db.execute("SELECT k FROM v")) == ["shared"]
    assert set(embedder.vectors) == {"shared", "memory"} and not embedder.pending
    embedder.drop(["memory"])
    assert set(embedder.vectors) == {"shared"}


@pytest.fixture
def private_korean(corpus, translator):
    """A Korean memory, and a translator that knows its English."""

    hub, repo = corpus
    (repo / ".wiki/memory/push.md").write_text("# 푸시\n\n묻지 않고 푸시한다.\n", encoding="utf-8")
    translator["# 푸시\n\n묻지 않고 푸시한다."] = "# Push\n\nPush without asking."
    translator["푸시"] = "Push"
    return hub, repo


def shots(text: str) -> int:
    key = translate._key(translate.KO_EN, translate.glossary()[2], text)
    with sqlite3.connect(translate.CACHE) as db:
        return db.execute("SELECT count(*) FROM shots WHERE k = ?", (key,)).fetchone()[0]


def test_a_private_memorys_english_is_kept_beside_it_and_goes_with_it(private_korean, monkeypatch):
    hub, repo = private_korean
    index = index_of(hub, repo)
    memory = by_path(index)[".wiki/memory/push.md"][0]
    seen = []
    jev(monkeypatch, [index.search("푸시", 1)[0]], seen)
    knowledge.prepare("Push?", repo, cfg=ACTIVE, cache=None)
    assert next(s["passages"] for s in seen if "passages" in s)[0]["text"] == "# Push\n\nPush without asking."
    assert shots(memory["text"]) == 0 and shots("푸시") == 0, "a private memory's English reached the shared cache"
    store = search.evidence_store(repo)
    assert store.english(memory["source_id"], [memory["text"]])[memory["text"]]["status"] == "translated"
    store.close()

    # Edited, a plain refresh drops the old English, and a translation of the
    # old text that lands afterwards is not kept.
    bump(repo / ".wiki/memory/push.md", "# 푸시\n\n묻고 나서 푸시한다.\n")
    index.refresh()
    store = search.evidence_store(repo)
    assert store.english(memory["source_id"], [memory["text"]]) == {}
    assert store.keep_english(memory["source_id"], [(memory["text"], {"status": "translated"})]) == 0
    store.close()

    # Deleted, a plain refresh takes its English with it: no Jev turn, nothing else to run.
    (repo / ".wiki/memory/push.md").unlink()
    index.refresh()
    with sqlite3.connect(searchd.store_path(hub, repo)) as db:
        assert db.execute("SELECT count(*) FROM english").fetchone() == (0,)
    index.close()


def test_kept_private_english_of_another_version_or_retired_is_not_used(private_korean):
    hub, repo = private_korean
    index = index_of(hub, repo)
    memory = by_path(index)[".wiki/memory/push.md"][0]
    text, source = memory["text"], memory["source_id"]
    index.close()
    fresh = knowledge.english([text], 5, [(source,)], repo)[0]
    assert (fresh["text"], fresh["cached"]) == ("# Push\n\nPush without asking.", False)

    store = search.evidence_store(repo)
    store.keep_english(source, [(text, {**fresh, "text": "Push only after asking.",
                                        "version": "an older model/p0/g0/e0"})])
    store.close()
    again = knowledge.english([text], 5, [(source,)], repo)[0]
    assert again["text"] == "# Push\n\nPush without asking.", "English of another version was used"
    assert knowledge.english([text], 5, [(source,)], repo)[0]["cached"]

    assert translate.retire([text])
    retired = knowledge.english([text], 5, [(source,)], repo)[0]
    assert (retired["status"], retired["text"]) == ("retired", None)


def test_ingest_keeps_a_private_memorys_english_out_of_the_shared_cache(private_korean, translator, monkeypatch):
    hub, repo = private_korean
    translator["한도"] = "Limit"
    first = knowledge.ingest(repo)
    assert shots("# 푸시\n\n묻지 않고 푸시한다.") == 0 and shots("푸시") == 0
    assert shots("한도") == 1   # a shared document's is cached as before
    asked = []
    ask = translate._ask
    monkeypatch.setattr(translate, "_ask", lambda system, batch, seconds: asked.extend(batch) or ask(system, batch,
                                                                                                    seconds))
    again = knowledge.ingest(repo)
    assert again["statuses"] == first["statuses"]
    assert not {"푸시", "# 푸시\n\n묻지 않고 푸시한다."} & set(asked), "the kept English was asked for again"


def test_ingest_caches_a_text_a_shared_document_has_even_if_a_memory_has_it_too(corpus, translator):
    hub, repo = corpus
    (repo / "docs/common.md").write_text("공통 문장\n", encoding="utf-8")
    (repo / ".wiki/memory/common.md").write_text("공통 문장\n", encoding="utf-8")
    translator["공통 문장"] = "A common sentence."
    knowledge.ingest(repo)
    assert shots("공통 문장") == 1, "the shared document's English was kept only beside the memory"


def test_another_chunker_builds_its_own_generation_and_going_back_selects_the_old(corpus, monkeypatch):
    hub, repo = corpus
    first = index_of(hub, repo)
    gen, ids = first.store.gen, {c["chunk_id"] for c in first.chunks}
    first.close()
    monkeypatch.setattr(evidence, "CHUNKER", "chunks/next")
    second = index_of(hub, repo)
    assert second.store.gen == gen + 1 and ids.isdisjoint(c["chunk_id"] for c in second.chunks)
    second.close()
    monkeypatch.undo()
    back = index_of(hub, repo)
    assert back.store.gen == gen, "rollback rebuilt instead of selecting the kept generation"
    assert {c["chunk_id"] for c in back.chunks} == ids
    with sqlite3.connect(searchd.store_path(hub, repo)) as db:
        assert db.execute("SELECT v FROM meta WHERE k = 'current'").fetchone() == (str(gen),)
        assert sorted(g for (g,) in db.execute("SELECT gen FROM generations")) == [gen, gen + 1]
    back.close()


def test_a_memory_deleted_before_a_new_generation_indexed_it_leaves_no_generation_holding_it(corpus, monkeypatch):
    hub, repo = corpus
    first = index_of(hub, repo)
    memory = by_path(first)[".wiki/memory/login.md"][0]
    first.close()
    monkeypatch.setattr(evidence, "CHUNKER", "chunks/next")
    (repo / ".wiki/memory/login.md").unlink()
    second = index_of(hub, repo)
    with sqlite3.connect(searchd.store_path(hub, repo)) as db:
        assert db.execute("SELECT count(*) FROM chunks WHERE text LIKE '%password%'").fetchone() == (0,)
    assert second.store.keep_english(memory["source_id"], [(memory["text"], {"status": "translated"})]) == 0
    second.close()


def test_a_file_edited_while_a_new_generation_reads_it_is_read_again_before_publishing(corpus, monkeypatch):
    hub, repo = corpus
    index_of(hub, repo).close()
    monkeypatch.setattr(evidence, "CHUNKER", "chunks/next")
    ports = repo / "docs/ports.md"
    real, edits = searchd.chunks, []

    def edited(text, path):
        if path == ports and len(edits) < 1:
            edits.append(path)
            bump(ports, "# Ports\n\n## Search\n\nThe daemon listens on 8792.\n")
        return real(text, path)

    monkeypatch.setattr(searchd, "chunks", edited)
    second = index_of(hub, repo)
    assert edits and any("8792" in c["text"] for c in by_path(second)["docs/ports.md"])
    with sqlite3.connect(searchd.store_path(hub, repo)) as db:
        assert db.execute("SELECT v FROM meta WHERE k = 'current'").fetchone() == (str(second.store.gen),)
    second.close()


def unfinished(repo: Path, monkeypatch, chunker: str = "chunks/next"):
    """A new chunker whose build never finishes: `ports.md` changes on every
    read. Returns the real chunker, to finish it with."""

    monkeypatch.setattr(evidence, "CHUNKER", chunker)
    ports, real = repo / "docs/ports.md", searchd.chunks

    def always_edited(text, path):
        if path == ports:
            bump(ports, text + "x")
        return real(text, path)

    monkeypatch.setattr(searchd, "chunks", always_edited)
    return real


def test_a_new_generation_missing_a_file_is_not_published(corpus, monkeypatch):
    hub, repo = corpus
    first = index_of(hub, repo)
    gen = first.store.gen
    first.close()
    unfinished(repo, monkeypatch)
    second = index_of(hub, repo)
    with sqlite3.connect(searchd.store_path(hub, repo)) as db:
        assert db.execute("SELECT v FROM meta WHERE k = 'current'").fetchone() == (str(gen),)
    # Until its own is whole, it reads the published one (review round 5).
    assert second.store.reading() == gen and second.search("8791", 3)
    second.close()




def test_an_unfinished_build_serves_neither_a_deleted_document_nor_loses_a_memory(corpus, monkeypatch):
    hub, repo = corpus
    first = index_of(hub, repo)
    gen = first.store.gen
    memory = by_path(first)[".wiki/memory/login.md"][0]
    first.store.keep_english(memory["source_id"], [(memory["text"], {"status": "translated", "text": "Kept."})])
    first.close()
    (repo / "docs/limit.md").unlink()
    unfinished(repo, monkeypatch)
    second = searchd.Index(hub, repo, searchd.Embedder(None))
    second.embedder.vectors[memory["key"]] = b"vector"
    second.refresh()
    assert second.store.reading() == gen
    # The published generation it reads loses the deleted document (review round 6)...
    assert "docs/limit.md" not in by_path(second)
    # ...and keeps the memory the new generation indexed unchanged, with its
    # vector and its English: the same content, still read there.
    assert second.search("password login", 3, sources=["memory"])
    assert memory["key"] in second.embedder.vectors
    assert second.store.english(memory["source_id"], [memory["text"]])
    second.close()


def test_an_unfinished_build_prunes_nothing_and_publishing_keeps_the_rollback(corpus, monkeypatch):
    hub, repo = corpus
    index_of(hub, repo).close()
    monkeypatch.setattr(evidence, "CHUNKER", "chunks/2")
    index_of(hub, repo).close()

    def gens() -> list[int]:
        with sqlite3.connect(searchd.store_path(hub, repo)) as db:
            return sorted(g for (g,) in db.execute("SELECT gen FROM generations"))

    assert gens() == [1, 2]
    # A third build that never finishes.
    real = unfinished(repo, monkeypatch, "chunks/3")
    (repo / "docs/new.md").write_text("# New\n\nA change in the same pass.\n", encoding="utf-8")
    index_of(hub, repo).close()
    assert gens() == [1, 2, 3], "an unfinished build pruned the rollback generation"
    # Finished, it publishes and keeps the one it replaced.
    monkeypatch.setattr(searchd, "chunks", real)
    index_of(hub, repo).close()
    assert gens() == [2, 3]


def test_a_link_out_of_the_repository_is_not_its_evidence(tmp_path):
    (tmp_path / "hub/operator").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside/secret.md").write_text("# Secret\n\nbanana\n", encoding="utf-8")
    (tmp_path / "repo/docs").mkdir(parents=True)
    try:
        (tmp_path / "repo/docs/link.md").symlink_to(tmp_path / "outside/secret.md")
    except OSError:
        pytest.skip("symbolic links need privileges here")
    assert index_of(tmp_path / "hub", tmp_path / "repo").search("banana", 5) == []


# ---- normalization ------------------------------------------------------------

@pytest.fixture
def translator(monkeypatch, tmp_path):
    """A translator that answers from `answers` (masked input -> reply), with
    a cache of its own so one test's replies never answer another's."""

    answers: dict[str, str] = {}
    monkeypatch.setattr(translate, "CACHE", tmp_path / "translate.sqlite3")
    monkeypatch.setattr(translate, "api_key", lambda: "test-key")
    monkeypatch.setattr(translate, "_ask", lambda system, batch, seconds: [answers.get(b, b) for b in batch])
    # Schema initialization is fixture setup, not the fake model's request budget.
    cache = translate._store()
    assert cache is not None
    cache.close()
    return answers


def test_translator_initializes_cache_before_the_request_budget(monkeypatch, request):
    now = 0.0
    open_store = translate._store

    def cold_store(path=None):
        nonlocal now
        cold = not (path or translate.CACHE).exists()
        db = open_store(path)
        if cold:
            now += 6   # Schema setup exceeds the request's five-second budget.
        return db

    monkeypatch.setattr(translate, "time", SimpleNamespace(
        monotonic=lambda: now, strftime=time.strftime, gmtime=time.gmtime))
    monkeypatch.setattr(translate, "_store", cold_store)
    answers = request.getfixturevalue("translator")
    answers["원문이 이긴다."] = "The original wins."

    out = translate.english(["원문이 이긴다."], now + 5)[0]
    assert (out["status"], out["text"]) == ("translated", "The original wins.")


def test_english_outcomes_say_what_happened(translator):
    translator["한도를 넘으면 멈춘다."] = "It stops when over the limit."
    later = time.monotonic() + 5
    en, ko, other = translate.english(["Plain English.", "한도를 넘으면 멈춘다.", "日本語の文"], later)
    assert (en["status"], en["text"]) == ("original_english", "Plain English.")
    assert (ko["status"], ko["text"], ko["spans"], ko["cached"]) == (
        "translated", "It stops when over the limit.", "intact", False)
    assert ko["model"] == translate.MODEL and ko["version"].startswith(translate.MODEL)
    assert (other["status"], other["text"], other["reason"]) == ("uncertain", None, "unsupported_language")
    again = translate.english(["한도를 넘으면 멈춘다."], later)[0]
    assert again["status"] == "translated" and again["cached"]


@pytest.mark.parametrize("text, expected", [
    ("Accept with [받아들임] or send [다시 PR] after the review.", "en"),   # Korean names of Korean things
    ("번역 요청이 한도를 넘으면 멈춘다.", "ko"),
    ("WIKI_JEV_MODE 가 off 이면 Jev 요청을 보내지 않는다.", "ko"),
    ("`search.daemon.chunks()` 의 처리를 재사용한다.", "ko"),              # code is neither
    # A Korean clause among much English is still Korean (review round 1).
    ("For context, this document describes the approval workflow in detail. 단, 사용자가 거절하면 "
     "실행하지 않는다.", "ko"),
    ("日本語の文", "und"),
    ("x = 1", "en"),
    # A fenced sketch keeps its Korean, as the translator keeps it (live ingest, round 2).
    ("## Screen\n\n```\n│ 프로젝트 │ 위키 질의 │\n```\n\nThe screen has two columns.", "en"),
    ("## Screen\n\n```\n│ 프로젝트 │\n```\n\n화면은 두 칸이다.", "ko"),
])
def test_language_weighs_prose_words(text, expected):
    from common.language import language

    assert language(text) == expected


@pytest.mark.parametrize("source, reply, reason", [
    ("포트 8791 을 쓴다.", "It uses port 8790.", "protected_changed"),        # a number substituted
    ("포트 8791 을 쓴다.", "It uses a port.", "protected_changed"),           # a number lost
    ("search.daemon 을 고친다.", "Fix the daemon.", "protected_changed"),     # an identifier lost
    ("`tool/x.py` 를 고친다.", "Fix it.", "spans_broken"),                    # a placeholder dropped
    # A `~~~` fence is code as a backtick fence is (review round 2).
    ("~~~\n실행하지 않는다\n~~~\n설명한다.", "~~~\nExecute\n~~~\nExplain.", "spans_broken"),
])
def test_a_protected_span_that_changed_is_uncertain_not_translated(translator, source, reply, reason):
    masked, _spans = translate.protect(source, translate.glossary()[0])
    translator[masked] = reply
    out = translate.english([source], time.monotonic() + 5)[0]
    assert (out["status"], out["text"], out["reason"]) == ("uncertain", None, reason)


def test_numbers_and_identifiers_that_came_through_are_fine(translator):
    source = "포트 8791 과 search.daemon 을 v2 에서 3번 확인한다."
    translator[source] = "Check port 8791 and search.daemon three times in v2."
    # `3번` became `three`: a lost number is uncertain, not quietly accepted.
    assert translate.english([source], time.monotonic() + 5)[0]["status"] == "uncertain"
    # Nor cached: a corrected translator is asked again (review round 1).
    translator[source] = "Check port 8791 and search.daemon 3 times in v2."
    assert translate.english([source], time.monotonic() + 5)[0]["status"] == "translated"


def test_failures_are_unavailable_with_their_reason(monkeypatch, translator):
    assert translate.english(["한도"], time.monotonic() - 1)[0]["reason"] == "deadline"
    monkeypatch.setattr(translate, "api_key", lambda: "")
    out = translate.english(["다른 문장"], time.monotonic() + 5)[0]
    assert (out["status"], out["text"], out["reason"]) == ("unavailable", None, "no_key")
    monkeypatch.setattr(translate, "api_key", lambda: "test-key")
    monkeypatch.setattr(translate, "_ask", lambda *a: None)
    assert translate.english(["또 다른 문장"], time.monotonic() + 5)[0]["reason"] == "request_failed"
    # And `translate` itself still hands back the original, as its callers expect.
    assert translate.translate(["또 다른 문장"], translate.KO_EN, time.monotonic() + 5) == ["또 다른 문장"]


def test_a_retired_translation_is_not_used_or_made_again(translator):
    translator["원문이 이긴다."] = "The translation wins."
    assert translate.english(["원문이 이긴다."], time.monotonic() + 5)[0]["status"] == "translated"
    assert translate.retire(["원문이 이긴다."])
    out = translate.english(["원문이 이긴다."], time.monotonic() + 5)[0]
    assert (out["status"], out["text"]) == ("retired", None)


# ---- the decision workflow reads English ----------------------------------------

def test_jev_reads_english_and_an_untranslated_passage_proves_nothing(corpus, translator, monkeypatch):
    hub, repo = corpus
    index = index_of(hub, repo)
    korean, english_hit = index.search("한도", 1)[0], index.search("daemon 8791", 1)[0]
    seen = []
    jev(monkeypatch, [korean, english_hit], seen)

    # Nothing translates the Korean passage: it stays, ungraded, and cannot show coverage.
    out = knowledge.prepare("Which port?", repo, cfg=ACTIVE, cache=None)
    judged = next(s for s in seen if "passages" in s)
    assert [p["text"] for p in judged["passages"]] == [english_hit["text"]]
    assert judged["complete_passages"] == ["p0"]
    assert {"not_normalized": [korean["chunk_id"]]} in out["limits"]
    untranslated = next(e for e in out["evidence"] if e["chunk_id"] == korean["chunk_id"])
    assert untranslated["relevance"] is None and untranslated["translation"]["status"] == "uncertain"
    assert {"chunk_id": korean["chunk_id"], "path": korean["path"], "locator": korean["locator"],
            "reason": "not_normalized"} in out["reads"]

    # Translated, its English is what Jev reads; the citation stays the original.
    seen.clear()
    translator[translate.protect(korean["text"], translate.glossary()[0])[0]] = (
        "# Limit\n\nIt stops when a request is over the limit.")
    translator["한도"] = "Limit"
    out = knowledge.prepare("Which port?", repo, cfg=ACTIVE, cache=None)
    passages = next(s["passages"] for s in seen if "passages" in s)
    assert "It stops when a request is over the limit." in passages[0]["text"] + passages[1]["text"]
    chunk = next(e for e in out["evidence"] if e["chunk_id"] == korean["chunk_id"])
    assert chunk["original_text"] == korean["text"] and chunk["translation"]["status"] == "translated"
    assert out["status"] == "ready" and out["reads"] == []
    index.close()


def test_a_question_without_english_means_no_jev_but_retrieval_goes_on(monkeypatch):
    from test_decision_flow import english

    seen = []
    jev(monkeypatch, [], seen, answer=lambda *a: pytest.fail("Jev was asked"))
    monkeypatch.setattr(knowledge, "english", lambda texts, seconds, owners=None, project=None: english(texts, 0))
    out = knowledge.prepare("포트는?", None, cfg=ACTIVE, cache=None)
    assert out["normalization"] == {"query": "unavailable", "state": None}
    assert out["status"] == "unavailable" and out["reason"] == "normalization_failed" and seen == []


def test_the_meaning_labels_accept_their_reference_and_catch_a_flipped_meaning():
    from eval import meaning

    cases = json.loads(meaning.MANIFEST.read_text(encoding="utf-8"))["cases"]
    assert {c["kind"] for c in cases} >= {"negation", "partial-negation", "condition", "version", "quantity"}
    for case in cases:
        assert meaning.check(case, {"status": "translated", "text": case["reference"]}) == [], case["id"]
    negation = next(c for c in cases if c["id"] == "negation-01")
    assert meaning.check(negation, {"status": "translated", "text": "A hook grants tool permissions."})
    partial = next(c for c in cases if c["id"] == "negation-02")
    assert meaning.check(partial, {"status": "translated", "text": "No test reads the real key."})
    assert meaning.check(negation, {"status": "uncertain", "reason": "protected_changed", "text": None})


def test_ingest_counts_before_it_sends_and_every_citation_resolves(corpus, translator):
    hub, repo = corpus
    translator["번역 요청이 한도를 넘으면 멈춘다."] = "A translation request stops over the limit."
    translator["한도"] = "Limit"
    estimate = knowledge.ingest(repo, estimate=True)
    assert estimate["korean"] == 2 and estimate["requests_at_most"] == 1 and estimate["unresolved_citations"] == []
    assert "statuses" not in estimate
    done = knowledge.ingest(repo)
    assert done["statuses"] == {"uncertain": 1, "translated": 1}   # the chunk also carries its `# 한도` line


def test_mode_off_sends_nothing_to_the_translator(monkeypatch):
    monkeypatch.setattr(translate, "english", lambda *a: pytest.fail("the translator was asked"))
    monkeypatch.setattr(knowledge, "run_round", lambda req, project, budget: found([]))
    out = knowledge.prepare("포트는?", None)   # no key in a test run: mode off
    assert out["jev"]["mode"] == "off" and out["normalization"] is None and out["reason"] == "disabled"


def test_a_long_state_is_summarized_not_dropped(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "docs/plans").mkdir(parents=True)
    (repo / "docs/plans/1-x.md").write_text(
        "# X\n\n## Steps\n\n| # | Step | Status |\n| --- | --- | --- |\n| 1 | Build | Not started |\n",
        encoding="utf-8")
    state = "user: " + "earlier turn. " * 600 + "user: the latest ask"
    brief, omitted = knowledge.summarized(state, repo)
    summary = json.loads(brief)
    assert len(brief) <= knowledge.STATE_CHARS
    assert summary["recent_state"].endswith("the latest ask")
    assert summary["unresolved_requirements"][0].startswith("docs/plans/1-x.md: ")
    assert omitted["characters"] == len(state) - len(summary["recent_state"])
    assert knowledge.summarized("short", repo) == ("short", None)

    # The summary is what Jev judges, told what it left out.
    from test_decision_flow import english

    seen = []
    jev(monkeypatch, [], seen)
    monkeypatch.setattr(knowledge, "english", lambda texts, seconds, owners=None, project=None: english(texts, 0))
    out = knowledge.prepare("Question", None, state, cfg=ACTIVE, cache=None)
    brief, omitted = knowledge.summarized(state, None)
    assert out["state"]["summarized"] and out["status"] != "unavailable"
    assert seen[0]["current_state"] == brief and seen[0]["omitted_context"] == omitted
