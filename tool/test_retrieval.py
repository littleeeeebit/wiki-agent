"""Stage 5 of `docs/plans/jev/`: chunk-level retrieval with a bounded graph
lane. BM25 only (no model), no credentials, no external calls."""

import os
import threading
import time
from pathlib import Path

import pytest

import decision
import search
from main import knowledge
from search import evidence, retrieval
from search import daemon as searchd

ACTIVE = decision.Config("active", decision.MODEL, "file", key="k")
OFF = decision.Config("off", decision.MODEL, "default")
QUESTION = "Who is on call for the ingest pipeline?"
ANSWER = "Every Tuesday, Mira covers pages."


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A repository whose answer to `QUESTION` sits in a page the question
    shares no word with, one link away from a page it does match."""

    hub, repo = tmp_path / "hub", tmp_path / "repo"
    monkeypatch.setattr(search, "HUB", hub)
    monkeypatch.setattr(knowledge, "HUB", hub)
    for folder in (hub / "operator", hub / "craft", repo / "docs", repo / ".wiki/decisions", repo / ".wiki/memory"):
        folder.mkdir(parents=True)
    files = {
        hub / "operator/review.md": "# Review\n\nReview every pull request for the ingest pipeline before merge.\n",
        repo / "docs/owners.md": "# Owners\n\nThe ingest pipeline is owned by [the Atlas team](atlas.md).\n",
        repo / "docs/atlas.md": f"# Atlas\n\n## Rota\n\n{ANSWER}\n\n## History\n\nFormed from two groups.\n",
        repo / "docs/search.md": "# Search\n\n## Ranking\n\nThe ranker merges lexical and dense lists.\n\n"
                                 "## Cache\n\nThe ranker keeps dense lists per section.\n\n"
                                 "The search daemon is `search/daemon.py`.\n",
        repo / "docs/a.md": "# A\n\nCircular page alpha, see [b](b.md).\n",
        repo / "docs/b.md": "# B\n\nCircular page beta, see [a](a.md).\n",
        repo / ".wiki/decisions/2026-01-01-001-port.md": "# Port\n\nDecision. `search/daemon.py` listens on 8791.\n",
        repo / ".wiki/memory/2026-09-20-login.md": "# Login\n\nThe user decided the ingest pipeline logs in "
                                                   "with passkeys.\n",
    }
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    return hub, repo


def index_of(hub: Path, repo: Path) -> searchd.Index:
    index = searchd.Index(hub, repo, searchd.Embedder(None))
    index.refresh()
    return index


def ask(index, query: str = QUESTION, **fields) -> dict:
    req = retrieval.request(evidence.repo_id(index.project), query, **{
        "sources": ["hub", "documents", "memory"], **fields})
    return retrieval.run(index.snapshot(), req)


def texts(result: dict, lane: str | None = None) -> list[str]:
    return [c["text"] for c in result["chunks"] if lane is None or c["lane"] == lane]


def chunk_of(index, display: str, text: str) -> dict:
    return next(c for c in index.chunks if c["locator"].get("path") == display and text in c["text"])


# ---- the completion gate ------------------------------------------------------------

def test_a_passage_that_shares_no_word_with_the_question_is_found_through_a_link_and_not_without(world):
    hub, repo = world
    index = index_of(hub, repo)
    assert not set(searchd.terms(ANSWER)) & set(searchd.terms(QUESTION))
    off = ask(index, graph=None)
    on = ask(index)
    assert ANSWER not in "".join(texts(off))
    assert ANSWER in "".join(texts(on, "graph"))
    answer = next(c for c in on["chunks"] if ANSWER in c["text"])
    # Not a neighbour's title: the passage's own text, its own citation, and how it was reached.
    assert answer["locator"]["path"] == "docs/atlas.md" and answer["heading_path"] == ["Atlas", "Rota"]
    graph = on["scores"][answer["chunk_id"]]["graph"]
    (path,) = [on["paths"][p] for p in graph["paths"]]
    owners = chunk_of(index, "docs/owners.md", "owned by")
    assert path["seed"] == owners["chunk_id"] and path["status"] == "discovered" and path["hops"] == 1
    assert [s["kind"] for s in path["steps"][1:]] == ["links_to"]
    assert path["steps"][1]["through"] == owners["source_id"] or path["steps"][1]["through"] == answer["source_id"]
    # The discovery is recorded apart from the query scores, not as a probability.
    assert graph["hops"] == 1 and on["scores"][answer["chunk_id"]]["bm25"] is None
    # The seeds are the same with the graph on: nothing was pushed out to fill a quota.
    assert texts(on, "rrf") == texts(off)
    index.close()


def test_the_seeds_are_ranked_per_chunk_so_one_document_gives_two_sections(world):
    hub, repo = world
    index = index_of(hub, repo)
    result = ask(index, "ranker dense lists", graph=None)
    ranked = [c for c in result["chunks"] if c["locator"]["path"] == "docs/search.md"]
    assert {c["heading_path"][-1] for c in ranked} == {"Ranking", "Cache"}
    # Each lane is kept apart: rank and score per lane, RRF beside them.
    first = result["scores"][ranked[0]["chunk_id"]]
    assert first["bm25"]["rank"] == 1 and first["rrf"]["rank"] == 1 and first["cos"] is None
    index.close()


def test_every_allowed_source_gets_a_floor_before_the_cut_and_an_empty_one_gives_it_back(world):
    hub, repo = world
    index = index_of(hub, repo)
    # Five document sections match better than the memory; four slots still hold it.
    query = "ranker dense lists circular page pipeline"
    result = ask(index, query, graph=None, limit=4)
    assert result["coverage"]["memory"] == {"candidates": 1, "floor": 1, "selected": 1}
    assert len(result["chunks"]) == 4
    (memory,) = [c for c in result["chunks"] if c["kind"] == "memory"]
    assert result["scores"][memory["chunk_id"]]["rrf"]["rank"] > 4
    # A source that is not allowed is not ranked at all, however well it matches.
    narrow = ask(index, query, graph=None, limit=4, sources=["documents"])
    assert {c["kind"] for c in narrow["chunks"]} == {"document"}
    # A source with nothing gives its floor back and is named as missing.
    none = ask(index, "circular page", graph=None, limit=4)
    assert none["missing_sources"] == ["hub", "memory"] and len(none["chunks"]) == 2
    index.close()


def test_a_cycle_is_walked_once(world):
    hub, repo = world
    index = index_of(hub, repo)
    graph = {**retrieval.GRAPH, "hops": 3}
    result = ask(index, "circular alpha", graph=graph, limit=1)
    ids = [c["chunk_id"] for c in result["chunks"]]
    assert len(ids) == len(set(ids)) == 2
    for path in result["paths"]:
        nodes = [s["node"] for s in path["steps"]]
        assert len(nodes) == len(set(nodes))
    index.close()


def test_fan_out_and_the_candidate_allowance_bound_the_walk(world):
    hub, repo = world
    for n in range(12):
        (repo / f"docs/leaf{n}.md").write_text(f"# Leaf {n}\n\nNothing here {n}.\n", encoding="utf-8")
    (repo / "docs/index.md").write_text("# Index of leaves\n\n" + "\n".join(
        f"- [leaf {n}](leaf{n}.md)" for n in range(12)) + "\n", encoding="utf-8")
    index = index_of(hub, repo)
    one = {"seeds": 1, "hops": 1, "fanout": 5}
    wide = ask(index, "index of leaves", graph=one, limit=1)
    # Twelve links, five walked — and the result says the fan-out cut the rest.
    assert len(texts(wide, "graph")) == 5 and wide["truncated"] == ["fanout"]
    tight = ask(index, "index of leaves", graph=one, limit=1, max_candidates=3)
    assert len(tight["chunks"]) == 3 and tight["truncated"] == ["candidates"]
    # One refusal says the allowance ran out; the walk does not go on counting.
    assert [p["status"] for p in tight["paths"]] == ["discovered", "discovered", "budget"]
    index.close()


def test_a_node_gone_from_the_snapshot_is_a_recorded_failure_not_a_result(world):
    hub, repo = world
    index = index_of(hub, repo)
    snap = index.snapshot()
    # The graph still holds the atlas page; the loaded chunks no longer do.
    snap.chunks = [c for c in snap.chunks if c["locator"].get("path") != "docs/atlas.md"]
    req = retrieval.request(evidence.repo_id(repo), QUESTION, sources=["hub", "documents", "memory"],
                            graph_seeds=["f" * 64])
    result = retrieval.run(snap, req)
    assert ANSWER not in "".join(texts(result))
    statuses = {p["status"] for p in result["paths"]}
    assert "deleted" in statuses
    assert any(p["seed"] == "f" * 64 and p["status"] == "deleted" for p in result["paths"])
    index.close()


def test_an_oversized_block_reached_by_the_graph_comes_whole_and_says_so(world):
    hub, repo = world
    (repo / "docs/owners.md").write_text("# Owners\n\nThe ingest pipeline is described in [spec](spec.md).\n",
                                         encoding="utf-8")
    (repo / "docs/spec.md").write_text("# Spec\n\n```text\n" + "x = 1\n" * 400 + "```\n", encoding="utf-8")
    index = index_of(hub, repo)
    result = ask(index)
    (block,) = [c for c in result["chunks"] if c["locator"]["path"] == "docs/spec.md" and "x = 1" in c["text"]]
    assert block["lane"] == "graph" and block["completeness"] == "oversized" and block["text"].count("x = 1") == 400
    index.close()


def test_cancellation_and_the_deadline_stop_the_walk_and_say_so(world):
    hub, repo = world
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), QUESTION, sources=["documents"])
    cancel = threading.Event()
    cancel.set()
    cancelled = retrieval.run(index.snapshot(), req, cancel)
    assert cancelled["truncated"] == ["cancelled"] and texts(cancelled, "graph") == []
    late = retrieval.run(index.snapshot(), {**req, "deadline": time.time() - 1})
    assert late["truncated"] == ["deadline"] and texts(late, "graph") == []
    # The seeds are still there: stopping the walk is not an empty search.
    assert texts(late, "rrf")
    index.close()


def test_a_link_into_a_source_the_request_may_not_see_is_refused_on_the_way(world):
    hub, repo = world
    (repo / "docs/owners.md").write_text(
        "# Owners\n\nThe ingest pipeline: see [the login memory](../.wiki/memory/2026-09-20-login.md).\n",
        encoding="utf-8")
    index = index_of(hub, repo)
    result = ask(index, sources=["documents"])
    assert result["chunks"] and all(retrieval.family(c) == "documents" for c in result["chunks"])
    refused = [p for p in result["paths"] if p["status"] == "source_not_allowed"]
    assert refused and refused[0]["steps"][-1]["kind"] == "links_to"
    private = ask(index, sources=["documents", "memory"], filters={"visibility": ["repository"]})
    assert all(c["visibility"] == "repository" for c in private["chunks"])
    assert any(p["status"] == "filtered" for p in private["paths"])
    index.close()


def test_the_hub_is_another_repository_and_a_request_for_a_third_is_refused(world, tmp_path):
    hub, repo = world
    index = index_of(hub, repo)
    without = ask(index, "review pull request ingest", sources=["documents", "memory"])
    assert all(c["repo_id"] == evidence.repo_id(repo) for c in without["chunks"])
    with_hub = ask(index, "review pull request ingest")
    assert any(c["repo_id"] == evidence.repo_id(hub) for c in with_hub["chunks"])
    other = retrieval.request(evidence.repo_id(tmp_path / "other"), QUESTION)
    with pytest.raises(ValueError, match="repo_id"):
        retrieval.run(index.snapshot(), other)
    index.close()


def test_a_request_for_another_generation_is_stale_and_a_bad_one_is_refused(world):
    hub, repo = world
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), QUESTION, generation=index.generation() + 1)
    with pytest.raises(retrieval.Stale):
        retrieval.run(index.snapshot(), req)
    good = retrieval.request(evidence.repo_id(repo), QUESTION)
    assert retrieval.problems(good) == []
    for bad in ({"source_allowlist": ["web"]}, {"limit": 41}, {"limit": 0},
                {"graph_budget": {**retrieval.GRAPH, "hops": 9}}, {"seen_chunk_ids": ["x"]},
                {"filters": {"kinds": ["tweet"]}}, {"graph_budget": None, "graph_seeds": ["a" * 64]},
                {"round": 4}, {"deadline": float("nan")}, {"query_original": " "}, {"max_candidates": 0},
                {"graph_budget": {**retrieval.GRAPH, "candidates": 40}}):
        assert retrieval.problems({**good, **bad}), bad
    index.close()


def test_the_same_text_twice_is_one_candidate_that_keeps_both_ids(world):
    hub, repo = world
    (repo / "docs/copy.md").write_text("# Owners\n\nThe ingest pipeline is owned by [the Atlas team](atlas.md).\n",
                                       encoding="utf-8")
    index = index_of(hub, repo)
    result = ask(index, graph=None)
    owned = [c for c in result["chunks"] if "owned by" in c["text"]]
    assert len(owned) == 1 and len(owned[0]["duplicates"]) == 1
    assert owned[0]["duplicates"][0] in result["seen_chunk_ids"]
    index.close()


# ---- entities --------------------------------------------------------------------------

def proposer(passages, _model):
    out = {}
    for p in passages:
        found = "`search/daemon.py`" in p["text"]
        out[p["id"]] = {"entities": [{"name": "search/daemon.py", "type": "module", "quote": "`search/daemon.py`"}]
                        if found else [], "relations": []}
    return out, "fake-model"


def test_a_decision_is_reached_through_the_module_both_name(world, monkeypatch):
    hub, repo = world
    monkeypatch.setattr(decision, "evaluate", lambda cfg, state, questions, *a: {n: 0.5 for n in questions})
    knowledge.extract_graph(repo, cfg=ACTIVE, proposer=proposer)
    index = index_of(hub, repo)
    result = ask(index, "Which daemon serves the ranker?", limit=1)
    port = next(c for c in result["chunks"] if c["kind"] == "decision")
    (path,) = [result["paths"][p] for p in result["scores"][port["chunk_id"]]["graph"]["paths"]]
    assert [(s["kind"], s["node_kind"], s["reverse"]) for s in path["steps"][1:]] == [
        ("mentions", "entity", False), ("mentions", "chunk", True)]
    # The entity reached becomes a graph seed of its own in a bridge repair.
    entity = path["steps"][1]["node"]
    req = retrieval.request(evidence.repo_id(repo), "Which daemon serves the ranker?",
                            sources=["hub", "documents", "memory"], limit=1)
    (again,), note = retrieval.repair(req, result, "bridge")
    assert again["graph_seeds"] == [entity] and again["limit"] == 0 and note["entities"] == [entity]
    later = retrieval.run(index.snapshot(), again)
    # What round 1 returned is not returned again; the walk goes through it.
    assert not {c["chunk_id"] for c in later["chunks"]} & set(req["seen_chunk_ids"] + [port["chunk_id"]])
    index.close()


# ---- repair ----------------------------------------------------------------------------

def test_each_repair_asks_something_different_and_three_rounds_is_the_most(world):
    hub, repo = world
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), QUESTION, sources=["documents"], graph=None, limit=2)
    first = retrieval.run(index.snapshot(), req)
    (widened,), note = retrieval.repair(req, first, "sources", sources=["hub", "documents", "memory", "papers"])
    assert widened["source_allowlist"] == ["hub", "memory", "papers"] and widened["round"] == 2
    assert widened["seen_chunk_ids"] == first["seen_chunk_ids"] and widened["deadline"] == req["deadline"]
    assert widened["generation"] == first["generation"]
    assert retrieval.repair(req, first, "sources", sources=["documents"])[0] == []
    owners = chunk_of(index, "docs/owners.md", "owned by")["chunk_id"]
    (context,), _ = retrieval.repair(req, first, "context", chunk_ids=[owners, "e" * 64])
    assert context["context_of"] == [owners] and context["limit"] == 0
    subs, note = retrieval.repair(req, first, "subqueries", subqueries=["Who owns the ingest pipeline?",
                                                                         "Who is on call for the Atlas team?",
                                                                         QUESTION])
    assert [s["query_original"] for s in subs] == ["Who owns the ingest pipeline?",
                                                   "Who is on call for the Atlas team?"]
    assert all(s["limit"] == 1 for s in subs) and note["rejected"][0]["reason"] == "same_as_question"
    third = {**widened, "round": 3}
    with pytest.raises(retrieval.Exhausted):
        retrieval.repair(third, first, "sources", sources=["hub"])
    index.close()


def test_adjacent_sections_come_back_for_missing_context(world):
    hub, repo = world
    index = index_of(hub, repo)
    rota = chunk_of(index, "docs/atlas.md", "Mira")
    req = retrieval.request(evidence.repo_id(repo), "Mira", sources=["documents"], graph=None, limit=1)
    first = retrieval.run(index.snapshot(), req)
    assert [c["chunk_id"] for c in first["chunks"]] == [rota["chunk_id"]]
    (context,), _ = retrieval.repair(req, first, "context", chunk_ids=[rota["chunk_id"]])
    second = retrieval.run(index.snapshot(), context)
    # The section before it — the page's opening — and the one after.
    assert [c["heading_path"][-1] for c in second["chunks"]] == ["Atlas", "History"]
    assert {c["lane"] for c in second["chunks"]} == {"context"}
    index.close()


def test_subqueries_keep_the_questions_versions_and_exclusions():
    original = "Which port does the daemon use in v2, not `legacy/server.py`?"
    kept, rejected = retrieval.checked_subqueries(original, [
        "Which port does the daemon use in v2?",
        "Which port does the daemon use in v3?",
        "Which port does the daemon use?",
        "What does legacy/server.py do in v2?",
        "Who owns the daemon in v2, not `legacy/server.py`?",
        "", 7, "which port does  the daemon use in V2?", "Where does the daemon run in v2?",
        "When was the daemon written in v2?"])
    assert kept == ["Which port does the daemon use in v2?", "Who owns the daemon in v2, not `legacy/server.py`?",
                    "Where does the daemon run in v2?"]
    assert [r["reason"] for r in rejected] == ["version_added", "version_dropped", "exclusion_dropped", "empty",
                                               "empty", "duplicate", "over_limit"]


# ---- the main's composition ------------------------------------------------------------

def test_the_switch_turns_the_graph_lane_off_and_the_page_search_stays_as_it_was(world, tmp_path, monkeypatch):
    hub, repo = world
    env = tmp_path / "graph.env"
    env.write_text("WIKI_GRAPH_RETRIEVAL=off\n", encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(env))
    off = knowledge.retrieve(QUESTION, repo, cfg=OFF)
    assert off["request"]["graph_budget"] is None and texts(off["result"], "graph") == []
    assert off["request"]["query_en"] is None
    env.write_text("", encoding="utf-8")
    on = knowledge.retrieve(QUESTION, repo, cfg=OFF)
    assert on["request"]["graph_budget"] == retrieval.GRAPH and ANSWER in "".join(texts(on["result"], "graph"))
    # The old client's contract: pages with a path and a line, no lanes.
    index = index_of(hub, repo)
    pages = index.search(QUESTION, 8)
    assert pages and all({"path", "line", "heading", "text", "rrf"} <= set(p) and "lane" not in p for p in pages)
    index.close()


def test_a_subquery_repair_asks_the_model_within_the_budget_and_code_keeps_what_stands(world, monkeypatch):
    hub, repo = world
    from common.budget import Budget

    class Event:
        def __init__(self, kind, text):
            self.kind, self.text, self.meta = kind, text, {}

    asked = []

    def oneshot(prompt, payload, model):
        asked.append((prompt, payload))
        yield Event("done", '{"subqueries": ["Who owns the ingest pipeline?", "Who is on call for the Atlas team?", '
                            '"Who is on call for the ingest pipeline in 2025?"]}')

    monkeypatch.setattr(knowledge, "oneshot", oneshot)
    budget = Budget(seconds=10, calls=6, candidates=40)
    first = knowledge.retrieve(QUESTION, repo, cfg=OFF, graph=False, budget=budget, k=2)
    out = knowledge.repair(first["request"], first["result"], "subqueries", repo, budget=budget)
    assert asked == [("retrieval-subqueries.md", {"question": QUESTION})]
    assert out["note"]["subqueries"] == ["Who owns the ingest pipeline?", "Who is on call for the Atlas team?"]
    assert out["note"]["rejected"][0]["reason"] == "version_added"
    assert all(r is not None and r["round"] == 2 for r in out["results"])
    external = knowledge.repair(first["request"], first["result"], "external", repo, budget=budget)
    assert external["note"]["skipped"] == "external_not_allowed" and external["results"] == []


def test_the_daemon_walks_the_graph_outside_its_lock_and_answers_a_stale_request_with_nothing(world, monkeypatch):
    hub, repo = world
    daemon = searchd.Daemon("t", searchd.Embedder(None))
    held = []
    real = retrieval.run
    monkeypatch.setattr(retrieval, "run", lambda index, req, cancel=None: held.append(daemon.lock.locked())
                        or real(index, req, cancel))
    req = retrieval.request(evidence.repo_id(repo), QUESTION)
    result = daemon.retrieve(req, str(hub), str(repo), 0)
    assert held == [False] and result["schema_version"] == retrieval.RESULT
    assert ANSWER in "".join(texts(result, "graph"))
    with pytest.raises(retrieval.Stale):
        daemon.retrieve({**req, "generation": result["generation"] + 1}, str(hub), str(repo), 0)
    for index in daemon.indexes.values():
        index.close()


def test_the_bridge_manifest_gains_only_through_explicit_relationships_and_displaces_no_seed():
    from eval import retrieval as measure

    summary = measure.run(method="bm25")["summary"]
    assert summary["seeds_identical"] == summary["queries"] == 8
    assert summary["gained"] == ["bridge-01", "bridge-02", "bridge-03"]
    # A bridge only prose states is not found by structure, and the record says so.
    assert summary["missed_bridges"] == ["bridge-07"]
    assert summary["seed_recall_graph_on"] == summary["recall_graph_off"] < summary["recall_graph_on"]


def test_a_snapshot_walks_the_graph_its_chunks_were_loaded_with_after_a_refresh_rewrites_it(world):
    hub, repo = world
    index = index_of(hub, repo)
    snap = index.snapshot()
    # The same generation, rewritten: the link that makes the bridge is gone.
    (repo / "docs/owners.md").write_text("# Owners\n\nThe ingest pipeline is owned by the Atlas team.\n",
                                         encoding="utf-8")
    stamp = (repo / "docs/owners.md").stat().st_mtime_ns + 10**9
    os.utime(repo / "docs/owners.md", ns=(stamp, stamp))
    index.refresh()
    assert index.generation() == snap.generation()
    req = retrieval.request(evidence.repo_id(repo), QUESTION, sources=["documents"])
    assert ANSWER in "".join(texts(retrieval.run(snap, req), "graph"))
    assert ANSWER not in "".join(texts(retrieval.run(index.snapshot(), req)))
    # Read once per change of the graph, not per snapshot.
    assert index.snapshot().graph is index.snapshot().graph
    index.close()


# ---- round 1 review -------------------------------------------------------------------

def test_a_seed_the_caller_names_is_checked_before_the_walk_starts_from_it(world):
    hub, repo = world
    (repo / ".wiki/memory/2026-09-21-link.md").write_text(
        "# Link\n\nPrivate note, see [the rota](../../docs/atlas.md).\n", encoding="utf-8")
    index = index_of(hub, repo)
    memory = chunk_of(index, ".wiki/memory/2026-09-21-link.md", "Private note")
    req = retrieval.request(evidence.repo_id(repo), QUESTION, sources=["documents"], limit=0,
                            graph_seeds=[memory["chunk_id"]])
    result = retrieval.run(index.snapshot(), req)
    assert result["chunks"] == []
    assert [(p["seed"], p["status"]) for p in result["paths"]] == [(memory["chunk_id"], "source_not_allowed")]
    hidden = retrieval.run(index.snapshot(), {**req, "limit": 0, "graph_seeds": [], "context_of": [memory["chunk_id"]]})
    assert hidden["chunks"] == [] and hidden["paths"][0]["status"] == "source_not_allowed"
    index.close()


def test_adjacent_sections_spend_the_same_allowance(world):
    hub, repo = world
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "Mira", sources=["documents"], limit=1, max_candidates=2)
    first = retrieval.run(index.snapshot(), req)
    (context,), _ = retrieval.repair(req, first, "context", chunk_ids=[first["chunks"][0]["chunk_id"]])
    second = retrieval.run(index.snapshot(), context)
    assert len(first["chunks"]) + len(second["chunks"]) == 2 and second["truncated"] == ["candidates"]
    index.close()


def test_sibling_subqueries_split_what_is_left_of_the_allowance(world):
    hub, repo = world
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "circular alpha", sources=["documents"], limit=1,
                            max_candidates=3, graph=None)
    first = retrieval.run(index.snapshot(), req)
    subs, note = retrieval.repair(req, first, "subqueries", subqueries=["ranker", "ingest", "beta page"])
    assert len(subs) == 2 and note["rejected"] == [{"subquery": "beta page", "reason": "no_allowance"}]
    got = [c["chunk_id"] for s in subs for c in retrieval.run(index.snapshot(), s)["chunks"]]
    assert len(first["chunks"]) + len(set(got)) <= 3 and all(s["max_candidates"] == 2 for s in subs)
    index.close()


def test_a_subquery_repair_takes_a_request_from_the_budget_and_none_past_the_last_round(world, monkeypatch):
    hub, repo = world
    from common.budget import Budget, Exhausted

    asked = []
    monkeypatch.setattr(knowledge, "oneshot", lambda *a: asked.append(a) or iter(()))
    budget = Budget(seconds=10, calls=0, candidates=40)
    first = knowledge.retrieve(QUESTION, repo, cfg=OFF, graph=False, budget=budget, k=2)
    with pytest.raises(Exhausted):
        knowledge.repair(first["request"], first["result"], "subqueries", repo, budget=budget)
    last = {**first["request"], "round": retrieval.MAX_ROUNDS}
    with pytest.raises(retrieval.Exhausted):
        knowledge.repair(last, first["result"], "subqueries", repo, budget=Budget(seconds=10, calls=6, candidates=40))
    assert asked == []


def test_a_cold_round_is_one_at_a_time_and_ends_with_the_budget(world, monkeypatch):
    hub, repo = world
    from common.budget import Budget

    req = retrieval.request(evidence.repo_id(repo), QUESTION)
    with knowledge.COLD:
        assert knowledge.run_round(req, repo, Budget(seconds=5, calls=1, candidates=40)) is None
    release = threading.Event()
    monkeypatch.setattr(knowledge, "local_index", lambda project: release.wait(10))
    started = time.monotonic()
    assert knowledge.run_round(req, repo, Budget(seconds=0.5, calls=1, candidates=40)) is None
    assert time.monotonic() - started < 2
    release.set()


# ---- round 2 review -------------------------------------------------------------------

def test_a_snapshot_walks_no_graph_another_index_rebuilt_from_other_chunks(world):
    hub, repo = world
    owners = repo / "docs/owners.md"
    owners.write_text("# Owners\n\nThe ingest pipeline is owned by the Atlas team.\n", encoding="utf-8")
    mine, other = index_of(hub, repo), index_of(hub, repo)
    # Another index of the same store adds the link and rebuilds the graph of the same generation.
    owners.write_text("# Owners\n\nThe ingest pipeline is owned by [the Atlas team](atlas.md).\n", encoding="utf-8")
    stamp = owners.stat().st_mtime_ns + 10**9
    os.utime(owners, ns=(stamp, stamp))
    other.refresh()
    assert mine.generation() == other.generation()
    req = retrieval.request(evidence.repo_id(repo), QUESTION, sources=["documents"])
    # Its chunks hold no link, so no walk may follow one: no graph rather than a mixed one.
    stale = retrieval.run(mine.snapshot(), req)
    assert texts(stale, "graph") == [] and "graph_stale" in stale["truncated"]
    mine.refresh()
    fresh = retrieval.run(mine.snapshot(), req)
    assert ANSWER in "".join(texts(fresh, "graph")) and "graph_stale" not in fresh["truncated"]
    mine.close()
    other.close()


def test_the_allowance_counts_candidates_not_the_ids_of_their_identical_twins(world):
    hub, repo = world
    (repo / "docs/c.md").write_text((repo / "docs/a.md").read_text(encoding="utf-8"), encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "circular alpha", sources=["documents"], limit=1,
                            max_candidates=2, graph=None)
    first = retrieval.run(index.snapshot(), req)
    assert len(first["chunks"]) == 1 and first["chunks"][0]["duplicates"] and first["spent"] == 1
    subs, note = retrieval.repair(req, first, "subqueries", subqueries=["circular beta"])
    assert note["rejected"] == [] and [s["max_candidates"] - s["spent"] for s in subs] == [1]
    assert len(retrieval.run(index.snapshot(), subs[0])["chunks"]) == 1
    index.close()


def test_chunks_differing_in_case_or_indentation_are_not_twins(world):
    keys = [retrieval.text_key({"text": t}) for t in
            ("env: production\n", "env: PRODUCTION\n", "if a:\n    b\n", "if a:\nb\n", "env: production  \r\n")]
    assert keys[0] != keys[1] and keys[2] != keys[3] and keys[4] == keys[0]
    hub, repo = world
    for name, text in (("code", "    quarry = True\n"), ("tab", "\tquarry = True\n"), ("prose", "quarry = True\n")):
        (repo / f"docs/{name}.md").write_text(text, encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "quarry", sources=["documents"], limit=4, graph=None)
    got = retrieval.run(index.snapshot(), req)
    assert len(got["chunks"]) == 3 and not any(c["duplicates"] for c in got["chunks"]), got["chunks"]
    index.close()


def test_an_external_repair_writes_no_paper_once_the_budget_is_spent(world, monkeypatch):
    hub, repo = world
    from common.budget import Budget
    from search import providers

    entry = {"arxiv_id": "2401.00001", "version": "v1", "title": "Rotas", "authors": ["A"],
             "published": "2024-01-01", "summary": "On-call rotas.", "abs_url": "https://arxiv.org/abs/2401.00001v1",
             "pdf_url": "https://arxiv.org/pdf/2401.00001v1"}
    done = threading.Event()

    def arxiv(query=None, ids=None, n=5, seconds=0):
        time.sleep(0.6)
        return [entry]

    monkeypatch.setattr(providers, "arxiv", arxiv)
    graded = []
    monkeypatch.setattr(decision, "evaluate", lambda cfg, state, questions, trace, budget, name:
                        graded.append(budget) or {})
    real = knowledge.add_papers
    monkeypatch.setattr(knowledge, "add_papers", lambda *a, **k: [real(*a, **k), done.set()][0])
    budget = Budget(seconds=0.3, calls=6, candidates=40)
    req = retrieval.request(evidence.repo_id(repo), QUESTION, graph=None)
    out = knowledge.repair(req, {"seen_chunk_ids": [], "spent": 0, "generation": None, "chunks": [], "paths": []},
                           "external", repo, budget=budget, cfg=ACTIVE, external=True)
    assert out["note"]["fetched"] is None
    assert done.wait(120)
    # Grading spent from the run's allowance, not a fresh one of its own.
    assert graded == [budget]
    with knowledge.records(repo) as store:
        assert store.all() == []


# ---- round 3 review -------------------------------------------------------------------

def test_a_snapshot_walks_no_graph_a_forget_changed_after_it_was_built(world):
    hub, repo = world
    from search import knowledge_graph

    index = index_of(hub, repo)
    # A forget drops rows and moves the extraction serial, not the build key.
    with index.store.transaction() as db:
        knowledge_graph.forget(db, "0" * 64)
    req = retrieval.request(evidence.repo_id(repo), QUESTION, sources=["documents"])
    stale = retrieval.run(index.snapshot(), req)
    assert texts(stale, "graph") == [] and "graph_stale" in stale["truncated"]
    index.refresh()
    fresh = retrieval.run(index.snapshot(), req)
    assert ANSWER in "".join(texts(fresh, "graph")) and "graph_stale" not in fresh["truncated"]
    index.close()


def test_a_write_in_progress_when_the_caller_gives_up_ends_before_it_returns_and_none_follows():
    from common.budget import Budget

    gate, written = knowledge.Gate(), []

    def work():
        for n in range(3):
            with gate.passing() as open_:
                if not open_:
                    return "stopped"
                time.sleep(0.2)
                written.append(n)

    assert knowledge.bounded(work, Budget(seconds=0.1, calls=1, candidates=40), gate) is None
    at_return = list(written)
    time.sleep(0.8)
    assert written == at_return == [0]


def test_an_adjacent_section_with_the_text_of_another_costs_no_candidate(world):
    hub, repo = world
    (repo / "docs/twins.md").write_text("# Twins\n\n## Shared\n\nSame words here.\n\n## Anchor\n\n"
                                        "Mira anchors this page.\n\n## Shared\n\nSame words here.\n", encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "Mira anchors page", sources=["documents"], limit=1,
                            max_candidates=2, graph=None)
    first = retrieval.run(index.snapshot(), req)
    assert first["chunks"][0]["heading_path"][-1] == "Anchor"
    (context,), _ = retrieval.repair(req, first, "context", chunk_ids=[first["chunks"][0]["chunk_id"]])
    second = retrieval.run(index.snapshot(), context)
    (shared,) = second["chunks"]
    assert shared["heading_path"][-1] == "Shared" and len(shared["duplicates"]) == 1
    assert second["spent"] == 2 and second["truncated"] == []
    index.close()


# ---- round 4 review -------------------------------------------------------------------

@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_a_caller_that_stops_waiting_gets_nothing_though_the_write_it_waited_out_finished(stop):
    from common.budget import Budget

    gate = knowledge.Gate()

    def work():
        with gate.passing():
            time.sleep(0.2)
        return "written"

    budget = Budget(seconds=0.05 if stop == "deadline" else 5, calls=1, candidates=40)
    if stop == "cancel":
        threading.Timer(0.05, budget.cancel.set).start()
    assert knowledge.bounded(work, budget, gate) is None


def test_a_twin_another_lane_returns_carries_the_id_of_the_one_ranked_first(world):
    hub, repo = world
    (repo / "docs/one.md").write_text("# One\n\n## Shared\n\nSame words here.\n", encoding="utf-8")
    (repo / "docs/two.md").write_text("# Two\n\n## Anchor\n\nMira anchors this page.\n\n## Shared\n\n"
                                      "Same words here.\n", encoding="utf-8")
    index = index_of(hub, repo)
    shared = {c["chunk_id"] for c in index.chunks if c["heading_path"][-1] == "Shared"}
    req = retrieval.request(evidence.repo_id(repo), "Mira anchors page words", sources=["documents"], limit=1,
                            max_candidates=4, graph=None)
    first = retrieval.run(index.snapshot(), req)
    assert first["chunks"][0]["heading_path"][-1] == "Anchor"
    (context,), _ = retrieval.repair(req, first, "context", chunk_ids=[first["chunks"][0]["chunk_id"]])
    second = retrieval.run(index.snapshot(), context)
    assert [c["heading_path"][-1] for c in second["chunks"]].count("Shared") == 1
    # Both ids are seen: no later round returns the same text as new.
    assert len(shared) == 2 and shared <= set(second["seen_chunk_ids"])
    index.close()


# ---- round 5 review -------------------------------------------------------------------

def test_the_walk_starts_from_every_copy_of_a_seed_since_the_same_words_link_elsewhere(world):
    hub, repo = world
    for side, detail in (("a", "Nothing about rotas."), ("b", "Mira covers Tuesday.")):
        (repo / f"docs/{side}").mkdir()
        (repo / f"docs/{side}/intro.md").write_text("# Intro\n\nThe quarry conveyor points to [details](detail.md).\n",
                                                    encoding="utf-8")
        (repo / f"docs/{side}/detail.md").write_text(f"# Detail\n\n{detail}\n", encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "quarry conveyor", sources=["documents"], limit=1)
    result = retrieval.run(index.snapshot(), req)
    (seed,) = result["chunks"][:1]
    assert "quarry conveyor" in seed["text"] and len(seed["duplicates"]) == 1
    assert "Mira covers Tuesday." in "".join(texts(result, "graph"))
    index.close()


def test_every_seed_that_reaches_a_chunk_keeps_its_path_to_it(world):
    hub, repo = world
    for name in ("p", "q"):
        (repo / f"docs/{name}.md").write_text(f"# {name.upper()}\n\nThe quarry conveyor {name}, see [answer](answer.md).\n",
                                              encoding="utf-8")
    (repo / "docs/answer.md").write_text("# Answer\n\nMira covers Tuesday.\n", encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "quarry conveyor", sources=["documents"], limit=2)
    result = retrieval.run(index.snapshot(), req)
    seeds = {c["chunk_id"] for c in result["chunks"] if c["lane"] == "rrf"}
    answer = next(c for c in result["chunks"] if "Mira covers Tuesday." in c["text"])
    reached = [result["paths"][p] for p in result["scores"][answer["chunk_id"]]["graph"]["paths"]]
    assert len(seeds) == 2 and {p["seed"] for p in reached} == seeds
    assert sorted(p["status"] for p in reached) == ["corroborated", "discovered"]
    index.close()


# ---- round 6 review -------------------------------------------------------------------

def test_the_copies_of_a_seed_are_one_node_whose_links_are_pooled_and_whose_cut_is_said(world):
    hub, repo = world
    for n in range(7):
        (repo / f"docs/c{n}").mkdir()
        (repo / f"docs/c{n}/intro.md").write_text("# Intro\n\nThe quarry conveyor points to [details](detail.md).\n",
                                                  encoding="utf-8")
        detail = "Mira runs the conveyor on Tuesday." if n == 6 else f"Detail number {n}."
        (repo / f"docs/c{n}/detail.md").write_text(f"# Detail\n\n{detail}\n", encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "quarry conveyor", sources=["documents"], limit=1)
    result = retrieval.run(index.snapshot(), req)
    assert "Intro" in result["chunks"][0]["text"] and len(result["chunks"][0]["duplicates"]) == 6
    # The last copy's link competes with the others', by relevance, not by where the copy stands.
    assert "Mira runs the conveyor on Tuesday." in "".join(texts(result, "graph"))
    assert "fanout" in result["truncated"]
    # The path names the copy whose link it followed, not only the seed that stands for it.
    answer = next(c for c in result["chunks"] if "Mira" in c["text"])
    (path,) = [result["paths"][p] for p in result["scores"][answer["chunk_id"]]["graph"]["paths"]]
    last = chunk_of(index, "docs/c6/intro.md", "quarry")["chunk_id"]
    seed = result["chunks"][0]["chunk_id"]
    assert path["seed"] == seed and path["steps"][1].get("copy", seed) == last
    index.close()


def test_a_family_whose_passage_is_a_copy_of_another_familys_is_covered_not_missing(world):
    hub, repo = world
    for path in (hub / "operator/quarry.md", repo / "docs/quarry.md"):
        path.write_text("# Quarry\n\nThe quarry conveyor requires weekly review.\n", encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "quarry conveyor", sources=["hub", "documents"], limit=2,
                            graph=None)
    result = retrieval.run(index.snapshot(), req)
    (kept,) = [c for c in result["chunks"] if "weekly review" in c["text"]]
    assert len(kept["duplicates"]) == 1 and result["missing_sources"] == []
    assert all(result["coverage"][name]["candidates"] == result["coverage"][name]["selected"] == 1
               for name in ("hub", "documents"))
    index.close()


def test_a_step_names_as_its_copy_only_a_chunk_of_the_same_text(world):
    hub, repo = world
    (repo / "docs/start.md").write_text(
        "---\nreads: [docs/answer.md]\n---\n\n# Start\n\n## Shared\n\nThe quarry conveyor manual.\n\n"
        "## Other\n\nThe quarry conveyor other notes.\n\n## Shared\n\nThe quarry conveyor manual.\n", encoding="utf-8")
    (repo / "docs/answer.md").write_text("# Answer\n\nMira covers Tuesday.\n", encoding="utf-8")
    index = index_of(hub, repo)
    text = {c["chunk_id"]: retrieval.text_key(c) for c in index.chunks}
    req = retrieval.request(evidence.repo_id(repo), "quarry conveyor", sources=["documents"], limit=2)
    result = retrieval.run(index.snapshot(), req)
    assert "Mira covers Tuesday." in "".join(texts(result, "graph"))
    named = [(p["steps"][n - 1]["node"], s["copy"]) for p in result["paths"]
             for n, s in enumerate(p["steps"]) if "copy" in s]
    # A section of the same file is not a copy: a source's edge belongs to the node that asked for it.
    assert all(text[node] == text[copy] for node, copy in named)
    index.close()


def test_a_link_to_a_copy_of_a_seed_is_credited_to_that_seed(world):
    hub, repo = world
    for name in ("r", "s"):
        (repo / f"docs/{name}.md").write_text("# Shared\n\nThe quarry conveyor manual.\n", encoding="utf-8")
    for name in ("p", "q"):
        (repo / f"docs/{name}.md").write_text(f"# {name.upper()}\n\nThe quarry conveyor {name}, see [manual](s.md).\n",
                                              encoding="utf-8")
    index = index_of(hub, repo)
    s = chunk_of(index, "docs/s.md", "manual")["chunk_id"]
    req = retrieval.request(evidence.repo_id(repo), "quarry conveyor", sources=["documents"], limit=3)
    result = retrieval.run(index.snapshot(), req)
    rrf = [c for c in result["chunks"] if c["lane"] == "rrf"]
    held = next(c for c in rrf if "manual." in c["text"])
    others = {c["chunk_id"] for c in rrf} - {held["chunk_id"]}
    assert held["duplicates"] == [s] or held["chunk_id"] == s
    reached = [result["paths"][p] for p in (result["scores"][held["chunk_id"]]["graph"] or {}).get("paths", [])]
    assert {p["seed"] for p in reached} == others and {p["to"] for p in reached} <= {s, held["chunk_id"]}
    index.close()


def test_a_seed_another_seed_links_to_keeps_that_path(world):
    hub, repo = world
    (repo / "docs/sa.md").write_text("# SA\n\nThe quarry conveyor alpha, see [beta](sb.md).\n", encoding="utf-8")
    (repo / "docs/sb.md").write_text("# SB\n\nThe quarry conveyor beta.\n", encoding="utf-8")
    index = index_of(hub, repo)
    req = retrieval.request(evidence.repo_id(repo), "quarry conveyor", sources=["documents"], limit=2)
    result = retrieval.run(index.snapshot(), req)
    by_text = {c["text"]: c["chunk_id"] for c in result["chunks"] if c["lane"] == "rrf"}
    alpha = next(i for t, i in by_text.items() if "alpha" in t)
    beta = next(i for t, i in by_text.items() if "beta" in t)
    graph = result["scores"][beta]["graph"]
    assert graph is not None and graph["rank"] is None
    assert [(result["paths"][p]["seed"], result["paths"][p]["status"]) for p in graph["paths"]] == [
        (alpha, "corroborated")]
    index.close()


# ---- audience scope (reliability PR 3) ---------------------------------------------------

SCOPE = "wiki pipeline hooks jev install calibrate review"


@pytest.fixture
def hub_world(tmp_path, monkeypatch):
    """The hub as its own project — shared rules, hook setup, Jev maintenance,
    product architecture and an unclassified page — beside another repository
    holding a private memory."""

    hub, other = tmp_path / "hub", tmp_path / "other"
    monkeypatch.setattr(search, "HUB", hub)
    monkeypatch.setattr(knowledge, "HUB", hub)
    files = {
        hub / "operator/review.md": "# Review\n\nReview every wiki change before merge.\n",
        hub / "docs/hooks-setup.md": "# Hooks setup\n\nInstall the injection hooks with setup_agents.\n",
        hub / "docs/jev-maintenance.md": "# Jev maintenance\n\nCalibrate the jev thresholds; roll back with rollout.\n",
        hub / "docs/architecture.md": "# Architecture\n\nThe pipeline owns its state and its gate.\n",
        hub / "README.md": "# Readme\n\nThe wiki in one place, see [hook setup](docs/hooks-setup.md) and "
                           "[Jev upkeep](docs/jev-maintenance.md).\n",
        other / ".wiki/memory/2026-09-20-secret.md": "# Secret\n\nThe wiki pipeline hooks jev token is hunter2.\n",
        other / "docs/notes.md": "# Notes\n\nOur wiki pipeline notes.\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return hub, other


def scoped(index, audiences, query=SCOPE, **fields) -> dict:
    return ask(index, query, **{"graph": None, "filters": {"audiences": audiences} if audiences else {}, **fields})


def paths_of(result: dict) -> set[str]:
    return {c["locator"]["path"] for c in result["chunks"]}


def test_each_audience_gets_its_authority_and_the_shared_rules_and_the_unclassified_page_says_so(hub_world):
    hub, _other = hub_world
    index = index_of(hub, hub)
    everything = {"operator/review.md", "docs/hooks-setup.md", "docs/jev-maintenance.md", "docs/architecture.md",
                  "README.md"}
    # No scope, the rollback: every page as before, nothing counted.
    unfiltered = scoped(index, None)
    assert paths_of(unfiltered) == everything and unfiltered["audiences"] is None
    expected = {"hooks": {"docs/hooks-setup.md"},
                "jev": {"docs/jev-maintenance.md"},
                "product": {"docs/architecture.md", "docs/jev-maintenance.md"}}
    for audience, authority in expected.items():
        result = scoped(index, [audience])
        assert paths_of(result) == authority | {"operator/review.md", "README.md"}, audience
        assert result["audiences"] == {"requested": [audience], "unclassified": 1}
    # The classification rides on the chunk, as provenance; its id and text are what they were.
    jev = chunk_of(index, "docs/jev-maintenance.md", "Calibrate")
    assert jev["audiences"] == ["jev", "product"] and chunk_of(index, "README.md", "wiki")["audiences"] is None
    assert jev["chunk_id"] in {c["chunk_id"] for c in unfiltered["chunks"]}
    index.close()


def test_a_wrong_audience_page_does_not_come_in_through_a_link_or_as_a_named_seed(hub_world):
    hub, _other = hub_world
    index = index_of(hub, hub)
    # README is the only match; it links to both setup pages.
    result = scoped(index, ["hooks"], "one place", graph=retrieval.GRAPH)
    assert "docs/hooks-setup.md" in paths_of(result) and "docs/jev-maintenance.md" not in paths_of(result)
    refused = [p for p in result["paths"] if p["status"] == "audience"]
    assert refused and all(p["to"] != p["seed"] for p in refused)
    jev = chunk_of(index, "docs/jev-maintenance.md", "Calibrate")["chunk_id"]
    for named in ({"graph_seeds": [jev]}, {"context_of": [jev]}):
        req = retrieval.request(evidence.repo_id(hub), "one place", filters={"audiences": ["hooks"]}, limit=0,
                                **named)
        seeded = retrieval.run(index.snapshot(), req)
        assert seeded["chunks"] == [] and seeded["paths"][0]["status"] == "audience"
    index.close()


def test_a_wrong_audience_copy_reached_twice_corroborates_nothing(hub_world):
    # Review round 2 (P1): the second visit took the revisit branch before the refusal and
    # credited the excluded copy as corroborating the eligible chunk with its text.
    hub, _other = hub_world
    (hub / "docs/plans/jev").mkdir(parents=True)
    (hub / "docs/plans/jev/copy.md").write_text((hub / "docs/hooks-setup.md").read_text(encoding="utf-8"),
                                                encoding="utf-8")
    for name in ("README.md", "GUIDE.md"):
        (hub / name).write_text(f"# {name[:-3]}\n\nThe wiki in one place, see [hook setup](docs/hooks-setup.md) "
                                "and [a copy](docs/plans/jev/copy.md).\n", encoding="utf-8")
    index = index_of(hub, hub)
    copy = chunk_of(index, "docs/plans/jev/copy.md", "Install")["chunk_id"]
    result = scoped(index, ["hooks"], "one place", graph=retrieval.GRAPH)
    assert "docs/hooks-setup.md" in paths_of(result)
    reached = [p["status"] for p in result["paths"] if any(s["node"] == copy for s in p["steps"])]
    assert reached and set(reached) == {"audience"}, reached
    index.close()


def test_a_scope_never_widens_repository_isolation_and_hub_maintenance_pages_stay_in_the_hub(hub_world):
    hub, other = hub_world
    index = index_of(hub, hub)
    for audiences in (None, ["product"], ["hooks", "jev"]):
        assert not any("hunter2" in c["text"] for c in scoped(index, audiences)["chunks"])
    index.close()
    # From the other repository: its own documents are unclassified and stay,
    # the hub gives its shared rules only, never its maintenance pages.
    theirs = index_of(hub, other)
    result = scoped(theirs, ["jev"])
    assert "docs/notes.md" in paths_of(result) and "operator/review.md" in paths_of(result)
    assert not paths_of(result) & {"docs/jev-maintenance.md", "docs/hooks-setup.md", "docs/architecture.md"}
    theirs.close()


def test_an_audience_filter_is_checked_and_kept_by_every_repair():
    repo = evidence.repo_id(Path.cwd())
    for bad in ([], ["ops"], ["jev", "jev"], "jev"):
        assert "filters.audiences" in retrieval.problems(retrieval.request(repo, "q", filters={"audiences": bad}))
    req = retrieval.request(repo, "q", filters={"audiences": ["jev"]})
    result = {"seen_chunk_ids": [], "spent": 0, "generation": 1, "paths": [], "chunks": []}
    for need, extra in (("sources", {"sources": ["papers"]}), ("external", {}),
                        ("subqueries", {"subqueries": ["another question"]})):
        requests, _note = retrieval.repair(req, result, need, **extra)
        assert requests and all(r["filters"] == {"audiences": ["jev"]} for r in requests), need


def test_sibling_rounds_count_an_unclassified_chunk_they_both_returned_once():
    # Review round 1 (P1): the siblings' counts were added, though their chunks are deduplicated.
    before = {"spent": 0}
    chunk = {"chunk_id": "a" * 64, "audiences": None}
    sibling = {"chunks": [chunk], "paths": [], "truncated": [], "seen_chunk_ids": [chunk["chunk_id"]], "spent": 1,
               "audiences": {"requested": ["jev"], "unclassified": 1}}
    merged = knowledge.merged(before, [sibling, dict(sibling)])
    assert len(merged["chunks"]) == 1 and merged["audiences"] == {"requested": ["jev"], "unclassified": 1}


def test_prepare_carries_the_scope_into_its_rounds_and_refuses_an_unknown_one(world, monkeypatch):
    _hub, repo = world
    seen = []
    monkeypatch.setattr(knowledge, "run_round", lambda req, project, budget: seen.append(req) or None)
    dossier = knowledge.prepare(QUESTION, repo, cfg=OFF, cache=None, audiences=["hooks"])
    assert seen and all(r["filters"] == {"audiences": ["hooks"]} for r in seen)
    assert dossier["audiences"] == ["hooks"]
    with pytest.raises(ValueError, match="audiences"):
        knowledge.prepare(QUESTION, repo, cfg=OFF, cache=None, audiences=["ops"])
