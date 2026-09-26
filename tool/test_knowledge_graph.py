"""Stage 4 of `docs/plans/jev/`: the knowledge graph. No credentials and no
external calls: the proposing model is a function handed in, and Jev's
request (`decision.evaluate`) is replaced where a test needs a verdict."""

import json
import os
import sqlite3
from pathlib import Path

import pytest

import decision
import repo_graph
import search
from main import knowledge
from search import evidence, knowledge_graph
from search import daemon as searchd

ACTIVE = decision.Config("active", decision.MODEL, "file", key="k")
OFF = decision.Config("off", decision.MODEL, "default")
REAL_WRITE = knowledge_graph.write


def bump(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = path.stat().st_mtime_ns + 10**9
    os.utime(path, ns=(stamp, stamp))


def index_of(hub: Path, repo: Path | None) -> searchd.Index:
    index = searchd.Index(hub, repo, searchd.Embedder(None))
    index.refresh()
    return index


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A hub with two linked rules, and a repository whose document, module
    page and decisions name the same module."""

    hub, repo = tmp_path / "hub", tmp_path / "repo"
    monkeypatch.setattr(search, "HUB", hub)
    for folder in (hub / "operator", hub / "craft", repo / "docs", repo / ".wiki/decisions", repo / ".wiki/modules",
                   repo / ".wiki/memory"):
        folder.mkdir(parents=True)
    (hub / "operator/review.md").write_text(
        "---\nseverity: contract\nlinks: [merge]\n---\n\n# Review\n\nReview before merge, see [[craft/merge]].\n",
        encoding="utf-8")
    (hub / "craft/merge.md").write_text("# Merge\n\nSquash on merge.\n", encoding="utf-8")
    (repo / "docs/search.md").write_text(
        "# Search\n\n## Ranking\n\nThe ranker `search/daemon.py` merges BM25 and vectors.\n\n"
        "## Ports\n\nSee [the ports](ports.md) and `docs/missing.md`.\n", encoding="utf-8")
    (repo / "docs/ports.md").write_text("# Ports\n\nThe daemon listens on 8791.\n", encoding="utf-8")
    (repo / ".wiki/modules/search.md").write_text(
        "---\nreads: [docs/search.md, docs/nowhere.md]\n---\n\n# search module\n\n"
        "`search/daemon.py` imports `search/evidence.py` to cut chunks.\n", encoding="utf-8")
    (repo / ".wiki/decisions/2026-01-01-001-ports.md").write_text(
        "---\ntitle: Ports\n---\n\n# Use port 8790\n\nDecision. `search/daemon.py` listens on port 8790.\n",
        encoding="utf-8")
    (repo / ".wiki/decisions/2026-02-01-002-ports.md").write_text(
        "---\ntitle: Ports again\nsupersedes: [2026-01-01-001-ports]\n---\n\n# Use port 8791\n\n"
        "Decision. `search/daemon.py` listens on port 8791, not 8790.\n", encoding="utf-8")
    (repo / ".wiki/decisions/2026-03-01-003-later.md").write_text(
        "# Later, unrelated\n\nDecision. Keep the map read-only.\n", encoding="utf-8")
    return hub, repo


def node_of(index, display: str) -> str:
    return next(c["source_id"] for c in index.chunks if c["locator"].get("path") == display)


def chunks_of(index, display: str) -> list[dict]:
    return sorted((c for c in index.chunks if c["locator"].get("path") == display), key=lambda c: c["line"])


# Every entity a passage names, as a model that reads it well would propose it.
NAMES = {"search/daemon.py": "module", "search/evidence.py": "module"}


def proposer(calls: list):
    def propose(passages, _model):
        calls.append(len(passages))
        out = {}
        for p in passages:
            entities = [{"name": n, "type": t, "quote": f"`{n}`"} for n, t in NAMES.items() if f"`{n}`" in p["text"]]
            relations = []
            if "imports `search/evidence.py`" in p["text"]:
                relations.append({"kind": "depends_on", "from": "search/daemon.py", "to": "search/evidence.py",
                                  "quote": "`search/daemon.py` imports `search/evidence.py`"})
            out[p["id"]] = {"entities": entities, "relations": relations}
        return out, "fake-model"
    return propose


@pytest.fixture
def jev(monkeypatch):
    """Jev answering from `verdicts` by the words of a question; 0.95 otherwise."""

    verdicts: dict[str, float] = {}
    asked: list[dict] = []

    def evaluate(cfg, state, questions, trace, budget, stage):
        asked.append({"stage": stage, "questions": questions, "state": state})
        return {name: next((v for word, v in verdicts.items() if word in q["instructions"]), 0.95)
                for name, q in questions.items()}

    monkeypatch.setattr(decision, "evaluate", evaluate)
    return verdicts, asked


# ---- structure --------------------------------------------------------------------

def test_structure_has_exactly_the_expected_endpoints_and_every_span_resolves(world):
    hub, repo = world
    index = index_of(hub, repo)
    graph = index.graph
    edges = graph.edges()
    got = {(e["kind"], e["from_id"], e["to_id"]) for e in edges if e["kind"] in ("links_to", "reads", "supersedes")}
    search_doc, ports = node_of(index, "docs/search.md"), node_of(index, "docs/ports.md")
    link_chunk = chunks_of(index, "docs/search.md")[-1]["chunk_id"]
    repo_id = evidence.repo_id(repo)
    old = knowledge_graph.decision_id(repo_id, ".wiki/decisions/2026-01-01-001-ports.md")
    new = knowledge_graph.decision_id(repo_id, ".wiki/decisions/2026-02-01-002-ports.md")
    review, merge = node_of(index, "operator/review.md"), node_of(index, "craft/merge.md")
    review_chunk = chunks_of(index, "operator/review.md")[0]["chunk_id"]
    assert got == {
        # A Markdown link from the chunk it is in; the missing target is no edge.
        ("links_to", link_chunk, ports),
        # Front matter `reads`, from the page; `docs/nowhere.md` resolves to nothing.
        ("reads", node_of(index, ".wiki/modules/search.md"), search_doc),
        # An explicit supersession — the later, unrelated decision supersedes nothing.
        ("supersedes", new, old),
        # A hub rule's front-matter `links` and its wiki link.
        ("links_to", review, merge), ("links_to", review_chunk, merge),
    }
    for doc in ("docs/search.md", ".wiki/modules/search.md"):
        mine = chunks_of(index, doc)
        contained = {e["to_id"] for e in graph.edges([node_of(index, doc)], "out", ["contains"])}
        assert contained == {c["chunk_id"] for c in mine}
    _title, ranking, ports_section = chunks_of(index, "docs/search.md")
    assert [e["to_id"] for e in graph.edges([ranking["chunk_id"]], "out", ["next_chunk"])] == [ports_section["chunk_id"]]
    report = knowledge_graph.verify(graph, index.chunks)
    assert report["invalid"] == report["dangling"] == report["out_of_scope"] == report["unresolved_spans"] == []
    # The span of a link is its line; of front matter, the key's lines.
    link = next(e for e in edges if e["from_id"] == link_chunk)
    assert link["source_spans"][0]["locator"] == {"path": "docs/search.md", "start_line": 9, "end_line": 9}
    reads = next(e for e in edges if e["kind"] == "reads")
    assert reads["source_spans"][0]["locator"] == {"path": ".wiki/modules/search.md", "start_line": 2, "end_line": 2}
    index.close()


def test_a_rebuild_from_nothing_keeps_every_id_and_edge(world, tmp_path, monkeypatch):
    hub, repo = world
    index = index_of(hub, repo)
    first = {(e["edge_id"], e["kind"], e["from_id"], e["to_id"]) for e in index.graph.edges()}
    nodes = set(index.graph.nodes())
    index.close()
    monkeypatch.setenv("WIKI_USER_HOME", str(tmp_path / "elsewhere"))
    again = index_of(hub, repo)
    assert {(e["edge_id"], e["kind"], e["from_id"], e["to_id"]) for e in again.graph.edges()} == first
    assert set(again.graph.nodes()) == nodes
    again.close()


def test_the_graph_is_built_once_per_change(world, monkeypatch):
    hub, repo = world
    index = index_of(hub, repo)
    writes = []
    real = knowledge_graph.write
    monkeypatch.setattr(knowledge_graph, "write", lambda *a: writes.append(1) or real(*a))
    index.refresh()
    assert writes == []
    bump(repo / "docs/ports.md", "# Ports\n\nThe daemon listens on 8792.\n")
    index.refresh()
    assert writes == [1]
    index.close()


# ---- semantics ----------------------------------------------------------------------

def test_a_document_reaches_a_decision_through_the_module_both_name(world, jev):
    hub, repo = world
    calls = []
    out = knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    assert out["graph"]["dangling"] == out["graph"]["unresolved_spans"] == out["graph"]["invalid"] == []
    index = index_of(hub, repo)
    graph = index.graph
    doc_chunk = chunks_of(index, "docs/search.md")[1]
    module = knowledge_graph.entity_id(evidence.repo_id(repo), "module", "search/daemon.py")
    assert [e["to_id"] for e in graph.edges([doc_chunk["chunk_id"]], "out", ["mentions"])] == [module]
    # Walked backwards, from the entity to whatever mentions it.
    back = graph.edges([module], "in", ["mentions"])
    assert all(e["reverse"] and e["to_id"] == module for e in back)
    mentioned_by = {graph.nodes([e["from_id"]])[e["from_id"]]["source_id"] for e in back}
    decision_source = node_of(index, ".wiki/decisions/2026-01-01-001-ports.md")
    assert {node_of(index, "docs/search.md"), decision_source} <= mentioned_by
    # The mention's span is the quote's line, and it holds the quote.
    span = graph.edges([doc_chunk["chunk_id"]], "out", ["mentions"])[0]["source_spans"][0]
    assert span["quote"] == "`search/daemon.py`" and span["locator"]["start_line"] == span["locator"]["end_line"] == 5
    index.close()


def test_a_dependency_keeps_its_direction(world, jev):
    hub, repo = world
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    index = index_of(hub, repo)
    rid = evidence.repo_id(repo)
    daemon_, evidence_ = (knowledge_graph.entity_id(rid, "module", n) for n in NAMES)
    out = index.graph.edges([daemon_], "out", ["depends_on"])
    assert [(e["from_id"], e["to_id"], e["status"], e["reverse"]) for e in out] == [(daemon_, evidence_, "adopted", False)]
    assert index.graph.edges([evidence_], "out", ["depends_on"]) == []
    (back,) = index.graph.edges([evidence_], "in", ["depends_on"])
    assert back["reverse"] and back["from_id"] == daemon_
    index.close()


def test_the_same_module_name_in_two_repositories_is_two_entities(world, tmp_path, monkeypatch, jev):
    hub, repo = world
    other = tmp_path / "other"
    (other / "docs").mkdir(parents=True)
    (other / "docs/a.md").write_text("# A\n\nOur `search/daemon.py` is a shell script.\n", encoding="utf-8")
    for project in (repo, other):
        knowledge.extract_graph(project, cfg=ACTIVE, proposer=proposer([]))
    ids = {knowledge_graph.entity_id(evidence.repo_id(p), "module", "search/daemon.py") for p in (repo, other)}
    assert len(ids) == 2
    index = index_of(hub, other)
    mine = {n for n, node in index.graph.nodes().items() if node["kind"] == "entity"}
    assert len(mine & ids) == 1
    assert knowledge_graph.verify(index.graph, index.chunks)["out_of_scope"] == []
    index.close()


def test_what_the_model_made_up_never_becomes_an_edge(world, jev):
    hub, repo = world
    verdicts, _asked = jev
    verdicts["depends on"] = 0.1

    def inventive(passages, _model):
        return {p["id"]: {"entities": [
            {"name": "search/daemon.py", "type": "module", "quote": "the ranker is fast"},     # quote not there
            {"name": "BM25 engine", "type": "module", "quote": "merges BM25 and vectors"},    # name not in quote
            {"name": "vectors", "type": "gadget", "quote": "vectors"},                       # no such type
            {"name": "BM25", "type": "feature", "quote": "merges BM25 and vectors"},
            {"name": "vectors", "type": "feature", "quote": "merges BM25 and vectors"},
        ], "relations": [
            {"kind": "depends_on", "from": "BM25", "to": "vectors", "quote": "merges BM25 and vectors"},
            {"kind": "depends_on", "from": "BM25", "to": "Elasticsearch", "quote": "merges BM25"},
            {"kind": "causes", "from": "BM25", "to": "vectors", "quote": "merges BM25 and vectors"},
        ]} for p in passages if "BM25" in p["text"]}, "fake-model"

    out = knowledge.extract_graph(repo, cfg=ACTIVE, proposer=inventive)
    assert out["rejected"] == {"quote not in passage": 1, "name not in quote": 1, "type": 1,
                               "endpoint is not an entity of this passage": 1, "kind": 1}
    index = index_of(hub, repo)
    # The one dependency that passed the code checks, Jev did not support: dropped.
    assert index.graph.edges(kinds=["depends_on"], statuses=knowledge_graph.STATUSES) == []
    names = {n["label"] for n in index.graph.nodes().values() if n["kind"] == "entity"}
    assert names == {"BM25", "vectors"}
    index.close()


def test_an_uncertain_or_unjudged_dependency_is_a_candidate_not_a_default_edge(world, jev):
    hub, repo = world
    verdicts, _asked = jev
    verdicts["depends on"] = 0.5
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    index = index_of(hub, repo)
    assert index.graph.edges(kinds=["depends_on"]) == []
    (candidate,) = index.graph.edges(kinds=["depends_on"], statuses=("candidate",))
    assert candidate["confidence"] == 0.5
    index.close()


def test_jev_off_or_failing_leaves_a_dependency_unjudged_and_a_later_run_judges_it(world, jev, monkeypatch):
    hub, repo = world
    calls = []
    knowledge.extract_graph(repo, cfg=OFF, proposer=proposer(calls))
    index = index_of(hub, repo)
    (unjudged,) = index.graph.edges(kinds=["depends_on"], statuses=("candidate",))
    assert unjudged["confidence"] is None
    index.close()
    # Jev on: the cached proposal is judged, and nothing is proposed again.
    before = len(calls)
    out = knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    assert len(calls) == before and out["proposed"]["judged"] == 1
    index = index_of(hub, repo)
    assert [e["status"] for e in index.graph.edges(kinds=["depends_on"])] == ["adopted"]
    index.close()

    def broken(*_a, **_k):
        raise decision.JevError("unavailable")

    monkeypatch.setattr(decision, "evaluate", broken)
    before = len(calls)
    bump(repo / ".wiki/modules/search.md", (repo / ".wiki/modules/search.md").read_text(encoding="utf-8") + "\nMore.\n")
    out = knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    assert len(calls) == before + 1 and out["trace"][0]["reason"] == "unavailable"
    index = index_of(hub, repo)
    # The failure is no verdict: a candidate, never dropped as unsupported.
    assert index.graph.edges(kinds=["depends_on"]) == []
    assert len(index.graph.edges(kinds=["depends_on"], statuses=("candidate",))) == 1
    index.close()


def test_contradicting_decisions_are_both_kept_and_linked_with_both_spans(world, jev):
    hub, repo = world
    verdicts, asked = jev
    out = knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    assert out["contradictions"]["judged"] >= 1
    # Bounded: only passages that name the same entity, each pair once.
    pairs = [p for a in asked if a["stage"] == "graph_contradiction" for p in a["state"]["pairs"]]
    assert all(p["subject"] in NAMES for p in pairs)
    index = index_of(hub, repo)
    old, new = (chunks_of(index, f".wiki/decisions/{n}")[0]["chunk_id"]
                for n in ("2026-01-01-001-ports.md", "2026-02-01-002-ports.md"))
    found = index.graph.edges([old], kinds=["contradicts"])
    between = [e for e in found if {e["from_id"], e["to_id"]} == {old, new}]
    assert len(between) == 1 and not between[0]["directed"] and not between[0]["reverse"]
    assert {s["chunk_id"] for s in between[0]["source_spans"]} == {old, new}
    # Nothing is merged: both decisions are still evidence, and still what supersession says.
    assert {old, new} <= set(index.graph.nodes())
    assert len(index.graph.edges(kinds=["supersedes"])) == 1
    # A second run compares nothing again.
    again = knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    assert again["contradictions"] == {"pairs": 0, "judged": 0}
    index.close()


def test_a_newer_date_alone_supersedes_nothing(world):
    hub, repo = world
    index = index_of(hub, repo)
    later = knowledge_graph.decision_id(evidence.repo_id(repo), ".wiki/decisions/2026-03-01-003-later.md")
    assert index.graph.edges([later], kinds=["supersedes"]) == []
    index.close()


# ---- revisions ------------------------------------------------------------------------

def test_an_edit_invalidates_that_passages_edges_until_it_is_extracted_again(world, jev):
    hub, repo = world
    calls = []
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    rid = evidence.repo_id(repo)
    daemon_ = knowledge_graph.entity_id(rid, "module", "search/daemon.py")
    doc = repo / "docs/search.md"
    bump(doc, doc.read_text(encoding="utf-8").replace("merges BM25", "fuses BM25"))
    index = index_of(hub, repo)
    edited = chunks_of(index, "docs/search.md")[1]["chunk_id"]
    assert index.graph.edges([edited], "out", ["mentions"]) == []
    # Other passages keep theirs.
    assert index.graph.edges([daemon_], "in", ["mentions"])
    index.close()
    before = len(calls)
    out = knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    # Only the edited passage went to the model.
    assert calls[before:] == [1] and out["to_extract"] == 1
    index = index_of(hub, repo)
    assert [e["to_id"] for e in index.graph.edges([edited], "out", ["mentions"])] == [daemon_]
    index.close()


def test_deleting_a_source_removes_its_edges_and_keeps_another_sources_support(world, jev):
    hub, repo = world
    # A second page supports the same dependency.
    (repo / "docs/also.md").write_text("# Also\n\n`search/daemon.py` imports `search/evidence.py` too.\n",
                                       encoding="utf-8")
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    index = index_of(hub, repo)
    (dependency,) = index.graph.edges(kinds=["depends_on"])
    assert len(dependency["source_spans"]) == 2
    ports = node_of(index, "docs/ports.md")
    assert index.graph.edges([ports], "in", ["links_to"])
    index.close()
    (repo / ".wiki/modules/search.md").unlink()
    (repo / "docs/ports.md").unlink()
    index = index_of(hub, repo)
    (dependency,) = index.graph.edges(kinds=["depends_on"])
    also = node_of(index, "docs/also.md")
    assert [s["source_id"] for s in dependency["source_spans"]] == [also]
    assert index.graph.edges([ports], "in", ["links_to"]) == []
    report = knowledge_graph.verify(index.graph, index.chunks)
    assert report["dangling"] == report["unresolved_spans"] == []
    index.close()


def test_a_deleted_memory_takes_its_extractions_and_entities_in_the_same_transaction(world, jev):
    hub, repo = world
    memory = repo / ".wiki/memory/secret.md"
    memory.write_text("# Secret\n\nWe chose `search/secret.py` for tokens.\n", encoding="utf-8")
    NAMES["search/secret.py"] = "module"
    try:
        knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    finally:
        del NAMES["search/secret.py"]
    index = index_of(hub, repo)
    source = node_of(index, ".wiki/memory/secret.md")
    assert any(n["label"] == "search/secret.py" for n in index.graph.nodes().values())
    index.close()
    memory.unlink()
    store = searchd.Store(searchd.store_path(hub, repo))
    listed = []
    for path in searchd.listing(hub, repo):
        shared = path.parent.parent == hub
        listed.append((path, hub if shared else repo, path.resolve(), shared))
    store.sync(listed)
    # No graph rebuild ran: the sync itself took all of it.
    db = store.db
    assert db.execute("SELECT COUNT(*) FROM extractions WHERE source_id = ?", (source,)).fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM nodes WHERE label = 'search/secret.py'").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM spans WHERE source_id = ?", (source,)).fetchone() == (0,)
    store.close()


def test_a_failed_graph_update_keeps_the_graph_it_had(world, jev, monkeypatch):
    hub, repo = world
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    index = index_of(hub, repo)
    before = {e["edge_id"] for e in index.graph.edges()}
    index.close()

    def crash(*_a):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(knowledge_graph, "write", crash)
    knowledge.retire_graph(repo)
    index = index_of(hub, repo)
    # The rebuild failed as a whole: the graph is the one it had, and search goes on.
    assert {e["edge_id"] for e in index.graph.edges()} == before
    assert index.search("ranker", 1)
    monkeypatch.setattr(knowledge_graph, "write", REAL_WRITE)
    index.refresh()
    assert not any(e["origin"] == "extracted" for e in index.graph.edges())
    assert knowledge_graph.verify(index.graph, index.chunks)["dangling"] == []
    index.close()


def test_a_source_edited_while_the_graph_cannot_be_rebuilt_leaves_nothing_dangling(world, monkeypatch):
    hub, repo = world
    index = index_of(hub, repo)
    before = {e["edge_id"] for e in index.graph.edges()}
    monkeypatch.setattr(knowledge_graph, "write", lambda *_a: (_ for _ in ()).throw(sqlite3.OperationalError("io")))
    bump(repo / "docs/ports.md", "# Ports\n\nThe daemon listens on 8792.\n")
    index.refresh()
    # The edit's own transaction took the old revision's place in the graph; nothing new came in.
    after = {e["edge_id"] for e in index.graph.edges()}
    assert after < before
    assert knowledge_graph.verify(index.graph, index.chunks)["dangling"] == []
    monkeypatch.setattr(knowledge_graph, "write", REAL_WRITE)
    index.refresh()
    assert knowledge_graph.verify(index.graph, index.chunks)["unresolved_spans"] == []
    assert index.graph.edges([node_of(index, "docs/ports.md")], "in", ["links_to"])
    index.close()


def test_another_chunker_builds_its_own_graph_and_going_back_reads_the_old(world, monkeypatch):
    hub, repo = world
    index = index_of(hub, repo)
    old = {e["edge_id"] for e in index.graph.edges()}
    index.close()
    monkeypatch.setattr(evidence, "CHUNKER", "chunks/test")
    new_index = index_of(hub, repo)
    new = {e["edge_id"] for e in new_index.graph.edges()}
    # Chunk ids hold the chunker, so its graph is its own; source-level edges are shared ids.
    assert new != old and new & old
    new_index.close()
    monkeypatch.undo()
    back = index_of(hub, repo)
    assert {e["edge_id"] for e in back.graph.edges()} == old
    back.close()


def test_retiring_semantics_keeps_structure_and_running_again_reuses_the_cache(world, jev):
    hub, repo = world
    calls = []
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    index = index_of(hub, repo)
    structural = {e["edge_id"] for e in index.graph.edges() if e["origin"] == "deterministic"}
    semantic = {e["edge_id"] for e in index.graph.edges() if e["origin"] == "extracted"}
    assert semantic
    index.close()
    report = knowledge.retire_graph(repo)
    assert "mentions" not in report["by_kind"] and report["dangling"] == []
    index = index_of(hub, repo)
    assert {e["edge_id"] for e in index.graph.edges()} == structural
    assert index.search("ranker", 1)
    index.close()
    before = len(calls)
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    assert len(calls) == before
    index = index_of(hub, repo)
    assert {e["edge_id"] for e in index.graph.edges() if e["origin"] == "extracted"} == semantic
    index.close()


def test_another_prompt_or_model_reuses_nothing(world, jev, monkeypatch):
    hub, repo = world
    calls = []
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer(calls))
    first = len(calls)
    knowledge.extract_graph(repo, cfg=ACTIVE, model="another-model", proposer=proposer(calls))
    assert len(calls) == 2 * first
    other_jev = decision.Config("active", "jev-9", "file", key="k")
    knowledge.extract_graph(repo, cfg=other_jev, proposer=proposer(calls))
    assert len(calls) == 3 * first


def test_estimate_sends_nothing(world, jev):
    hub, repo = world
    _verdicts, asked = jev
    calls = []
    out = knowledge.extract_graph(repo, estimate=True, cfg=ACTIVE, proposer=proposer(calls))
    assert calls == [] and asked == [] and out["to_extract"] == out["passages"] > 0


def test_an_observed_co_injection_is_a_hint_not_an_edge_to_follow(world):
    hub, repo = world
    (hub / "graph.json").write_text(json.dumps({"links": [
        {"a": "craft/merge", "b": "operator/review", "kind": "co", "by": {"all": 3}},
        {"a": "operator/review", "b": "craft/none", "kind": "co", "by": {"all": 3}}]}), encoding="utf-8")
    index = index_of(hub, repo)
    assert index.graph.edges(kinds=["co_injected"]) == []
    (hint,) = index.graph.edges(kinds=["co_injected"], statuses=("candidate",))
    assert hint["origin"] == "observed" and hint["source_spans"] == [] and hint["confidence"] is None
    assert knowledge_graph.problems(hint) == []
    index.close()


# ---- contract and projection ------------------------------------------------------------

def test_the_contract_refuses_what_it_cannot_stand_behind(world):
    hub, repo = world
    index = index_of(hub, repo)
    edge = index.graph.edges(kinds=["links_to"])[0]
    edge = {k: v for k, v in edge.items() if k not in ("via", "reverse")}
    assert knowledge_graph.problems(edge) == []
    assert "an adopted edge without source spans" in knowledge_graph.problems(
        {**edge, "source_spans": [], "source_revisions": []})
    assert "directed does not match the kind" in knowledge_graph.problems({**edge, "directed": False})
    bad_span = {**edge["source_spans"][0], "locator": {"path": "a.md", "start_line": 1}}
    assert knowledge_graph.problems({**edge, "source_spans": [bad_span]}) != []
    index.close()


def test_the_map_reads_the_projection_and_never_builds_it(world, jev):
    hub, repo = world
    path = searchd.store_path(hub, repo)
    # No index yet: the map is what it was, and nothing was made.
    before = repo_graph.picture(repo)
    assert not path.exists()
    assert not any(e.get("kind") == "reads" for e in before["edges"])
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    picture = repo_graph.picture(repo)
    kinds = {(e["a"], e["b"], e["kind"]) for e in picture["edges"]}
    # What the map drew before stays as it was; the graph adds what it did not have.
    assert (".wiki/modules/search.md", "docs/search.md", "link") in kinds
    assert (".wiki/decisions/2026-02-01-002-ports.md", ".wiki/decisions/2026-01-01-001-ports.md",
            "supersedes") in kinds
    # Document level: no chunk or entity ids, and no edge within one file.
    ids = {n["id"] for n in picture["nodes"]}
    assert all(e["a"] in ids and e["b"] in ids and e["a"] != e["b"] for e in picture["edges"])


def chunk_(text: str, start: int, source: str = "a" * 64) -> dict:
    return {"source_id": source, "repo_id": "r" * 64, "revision": "f" * 64, "chunk_id": evidence.digest(text, str(start)),
            "kind": "document", "heading": "H", "heading_path": ["H"], "text": text, "path": "x.md", "record": None,
            "locator": {"path": "x.md", "start_line": start, "end_line": start + text.count("\n")}}


def test_a_dependency_is_cited_only_by_a_quote_that_names_both_ends():
    text = "`Alpha` depends on `Beta`.\nUnrelated sentence."
    proposal = {"entities": [{"name": "Alpha", "type": "module", "quote": "`Alpha`"},
                             {"name": "Beta", "type": "module", "quote": "`Beta`"}],
                "relations": [{"kind": "depends_on", "from": "Alpha", "to": "Beta", "quote": "Unrelated sentence."}]}
    _entities, relations, rejected = knowledge_graph.validate(text, proposal)
    assert relations == [] and rejected == [{"relation": "Alpha depends_on Beta", "reason": "quote does not name both ends"}]


def test_jev_judges_the_cited_words_in_english_even_for_a_translated_passage(monkeypatch):
    korean = "`Alpha` 는 `Beta` 에 의존한다.\n`Alpha` 와 `Beta` 는 테스트된다."
    english = {korean: "`Alpha` depends on `Beta`.\n`Alpha` and `Beta` are tested.",
               "`Alpha` 는 `Beta` 에 의존한다.": "`Alpha` depends on `Beta`.",
               "`Alpha` 와 `Beta` 는 테스트된다.": "`Alpha` and `Beta` are tested."}
    monkeypatch.setattr(knowledge, "english", lambda texts, *_a: [
        {"text": english[t], "status": "translated", "version": "t1"} if t in english else
        {"text": None, "status": "unavailable", "version": None} for t in texts])
    asked = []

    def evaluate(cfg, state, questions, trace, budget, stage):
        claims = {c["id"]: c["cited_words"] for c in state["claims"]}
        asked.append(claims)
        # A judge that reads only what it is told to: the cited words.
        return {name: 0.95 if "depends on" in claims[name] else 0.05 for name in questions}

    monkeypatch.setattr(decision, "evaluate", evaluate)
    chunk = chunk_(korean, 1)
    entities = [{"name": n, "type": "module", "quote": f"`{n}`"} for n in ("Alpha", "Beta")]
    wrong = {"entities": entities, "relations": [{"kind": "depends_on", "from": "Alpha", "to": "Beta", "quote":
                                                   "`Alpha` 와 `Beta` 는 테스트된다.", "support": None}]}
    right = {"entities": entities, "relations": [{"kind": "depends_on", "from": "Alpha", "to": "Beta", "quote":
                                                   "`Alpha` 는 `Beta` 에 의존한다.", "support": None}]}
    budget = knowledge.Budget(seconds=60, calls=4, candidates=0)
    assert knowledge.supported([(chunk, wrong), (chunk, right)], ACTIVE, budget, [], None) == 2
    assert asked == [{"r0": "`Alpha` and `Beta` are tested.", "r1": "`Alpha` depends on `Beta`."}]
    assert wrong["relations"][0]["support"] == 0.05 and right["relations"][0]["support"] == 0.95
    # A quote with no English is not judged at all.
    untranslatable = {"entities": entities, "relations": [{"kind": "depends_on", "from": "Alpha", "to": "Beta",
                                                           "quote": "`Alpha` 는 `Beta`", "support": None}]}
    assert knowledge.supported([(chunk, untranslatable)], ACTIVE, budget, [], None) == 0
    assert untranslatable["relations"][0]["support"] is None


@pytest.mark.parametrize("now", ["failing", "unreadable"])
def test_a_verdict_on_english_that_changed_since_is_withdrawn_even_when_it_cannot_be_judged_again(
        world, jev, monkeypatch, now):
    hub, repo = world
    # A second page supports the same dependency, so the verdict past the first `limit` counts too.
    (repo / "docs/also.md").write_text("# Also\n\n`search/daemon.py` imports `search/evidence.py` too.\n",
                                       encoding="utf-8")
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer([]))
    index = index_of(hub, repo)
    assert [e["status"] for e in index.graph.edges(kinds=["depends_on"])] == ["adopted"]
    index.close()
    # The passage and its quote now normalize to other English, and Jev cannot say again.
    new = {"text": "`search/daemon.py` imports `search/evidence.py`", "status": "translated", "version": "t2"}
    if now == "unreadable":
        new = {"text": None, "status": "unavailable", "version": None}
    monkeypatch.setattr(knowledge, "english", lambda texts, *_a: [dict(new) for _ in texts])

    def broken(*_a, **_k):
        raise decision.JevError("unavailable")

    monkeypatch.setattr(decision, "evaluate", broken)
    knowledge.extract_graph(repo, limit=1, cfg=ACTIVE, proposer=proposer([]))
    index = index_of(hub, repo)
    assert index.graph.edges(kinds=["depends_on"]) == []
    (candidate,) = index.graph.edges(kinds=["depends_on"], statuses=("candidate",))
    assert candidate["confidence"] is None
    index.close()


def test_two_sections_of_one_source_with_the_same_text_both_get_their_edges():
    a, b = chunk_("Uses `Alpha`.", 1), chunk_("Uses `Alpha`.", 5)
    result = {"entities": [{"name": "Alpha", "type": "module", "quote": "`Alpha`"}], "relations": [], "versions": "v"}
    _nodes, edges, _spans = knowledge_graph.derive([a, b], [(a["source_id"], knowledge_graph.digest(a["text"]), result)])
    mentioned = {e[1] for e in edges.values() if e[3] == "mentions"}
    assert mentioned == {a["chunk_id"], b["chunk_id"]}


def test_an_extraction_kept_while_a_rebuild_was_deriving_is_not_hidden_by_it(world, jev, monkeypatch):
    hub, repo = world
    index = index_of(hub, repo)
    knowledge_graph.activate(index.store, "v")
    chunk = next(c for c in index.chunks if "`search/daemon.py`" in c["text"] and c["repo_id"] == evidence.repo_id(repo))
    row = (chunk["source_id"], knowledge_graph.digest(chunk["text"]), "v",
           {"entities": [{"name": "search/daemon.py", "type": "module", "quote": "`search/daemon.py`"}],
            "relations": []})
    real = knowledge_graph.derive

    def slow(*args):
        out = real(*args)
        knowledge_graph.keep(index.store, [row])     # lands while this rebuild derives
        return out

    monkeypatch.setattr(knowledge_graph, "derive", slow)
    stamp = index.loaded.split("/")[0]
    # Derived from the extraction set before the keep: not published.
    assert knowledge_graph.update(index.store, index.chunks, stamp, stamp="x") is False
    monkeypatch.setattr(knowledge_graph, "derive", real)
    assert knowledge_graph.update(index.store, index.chunks, stamp, stamp="x") is True
    assert index.graph.edges([chunk["chunk_id"]], "out", ["mentions"])
    index.close()


def test_the_map_shows_another_kind_on_a_pair_it_already_links(world, tmp_path):
    hub, repo = world
    new, old = ".wiki/decisions/2026-02-01-002-ports.md", ".wiki/decisions/2026-01-01-001-ports.md"
    page = repo / new
    page.write_text(page.read_text(encoding="utf-8") + f"\nSee [the old one]({Path(old).name}).\n", encoding="utf-8")
    index_of(hub, repo).close()
    kinds = {(e["a"], e["b"], e["kind"]) for e in repo_graph.picture(repo)["edges"]}
    assert {(new, old, "link"), (new, old, "supersedes")} <= kinds


def test_the_labeled_subset_scores_a_perfect_extraction_perfectly_and_catches_an_adopted_negative(jev):
    from eval import graph as scorer

    data = json.loads(scorer.MANIFEST.read_text(encoding="utf-8"))

    def labelled(passages, _model, negatives=False):
        out = {}
        for p, case in zip(passages, data["passages"]):
            wanted = case["relations"] + (case["not_relations"] if negatives else [])
            out[p["id"]] = {"entities": [{**e, "quote": e["name"]} for e in case["entities"]],
                            "relations": [{"kind": "depends_on", **r, "quote": p["text"]} for r in wanted]}
        return out, "labels"

    # English passages need no translator; the Korean one stays unjudged without one.
    perfect = scorer.run(scorer.MANIFEST, ACTIVE, proposer=labelled)["summary"]
    assert perfect["entities"]["precision"] == perfect["entities"]["recall"] == 1.0
    assert perfect["typed_entities"]["precision"] == 1.0
    assert perfect["proposed"]["precision"] == perfect["proposed"]["recall"] == 1.0
    assert perfect["adopted"]["precision"] == 1.0 and perfect["negatives_adopted"] == []
    careless = scorer.run(scorer.MANIFEST, ACTIVE, proposer=lambda p, m: labelled(p, m, True))["summary"]
    assert careless["negatives_adopted"] and careless["proposed"]["precision"] < 1.0
