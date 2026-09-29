"""Reliability PR 4 (`docs/plans/reliability/4-jev-contract.md`): who owns each
decision, one retrieval decision where an admitted action already chose
retrieval, every call accounted with its cost known or said unknown, and the
graph's structural health read without building anything.

No credentials and no external calls: Jev and the rounds are the fakes of
`test_decision_flow`; the graph is `test_knowledge_graph`'s fixture world.
"""
# ruff: noqa: F811 — a borrowed fixture is named again by each test that takes it

import json
import sqlite3

import pytest

import decision
import search
from common.budget import Budget
from main import decisions, knowledge, tracing
from search import daemon as searchd
from search import evidence, knowledge_graph
from test_decision_flow import MODEL, POLICY, REPO, SOURCES, World, answering, chunk, english, found
from test_decision_flow import world as flow_world  # noqa: F401 — fixture
from test_knowledge_graph import bump, index_of
from test_knowledge_graph import world as graph_world  # noqa: F401 — fixture

KEY = "contract-secret-key-4711"
CAUSE = {"action_id": "p" * 32, "point": "loop.fix", "operation": "retrieve_evidence", "state_revision": "3.7"}


def flow(world, *, required=False, cause=None, cache=None, available=SOURCES):
    budget = Budget(seconds=30, calls=6, candidates=40)
    return knowledge.Flow("What did the team decide about the port?", "", 8, omitted=None, available=list(available),
                          repo_id=REPO, graph=True, model=MODEL, live=True, budget=budget, pol=POLICY,
                          evaluate=world.evaluate, normalize=english, first=world.first, mend=world.mend,
                          cache=cache, required=required, cause=cause).run()


def route_of(world) -> dict:
    return next(q for stage, _s, q in world.asked if stage == "route")


# -- one retrieval decision ------------------------------------------------------------

def test_a_direct_query_asks_whether_to_retrieve_once_and_accounts_each_call():
    world = World(answering(), [found([chunk("port")])])
    out = flow(world)
    assert out["status"] == "ready" and "retrieve" in route_of(world)
    assert [s for s, *_ in world.asked] == ["route", "judge"]
    assert [c["purpose"] for c in out["calls"]] == [["route", "source_select"], ["grade", "sufficiency"]]
    for c in out["calls"]:
        assert c["provider"] == "jev" and c["owner"] == "jev" and c["parent_call_id"] == out["trace_id"]
        assert c["cost_usd"] is None and c["cost_known"] is False, "Jev reports tokens, not money: unknown"
        assert c["token_usage"] == {"input": 10, "output": 1} and len(c["input_digest"]) == 16
    assert out["cause"] is None and out["versions"]["behavior"] == knowledge.behavior()["digest"]


def test_retrieval_an_admitted_action_chose_is_not_judged_again():
    # A route that would answer directly: with retrieval required it is never asked.
    world = World(answering(route=0.01, sources={"memory": 0.01}), [found([chunk("port")])])
    out = flow(world, required=True, cause=CAUSE)
    route = route_of(world)
    assert "retrieve" not in route, "a second retrieval-need decision"
    assert {"analysis", "source_hub", "source_documents", "source_memory"} <= set(route)
    to_retrieve = next(t for t in out["transitions"] if t["to"] == "retrieve")
    assert to_retrieve["reason"] == "retrieval_required_by_action" and to_retrieve["score"] is None
    assert (to_retrieve["action_id"], to_retrieve["point"]) == (CAUSE["action_id"], "loop.fix")
    assert to_retrieve["sources"] == ["hub", "documents"], "source selection stays Jev's"
    assert [s for s, *_ in world.asked] == ["route", "judge"], "support is still judged"
    assert out["cause"] == CAUSE and not out["direct"]
    assert {c["state_revision"] for c in out["calls"]} == {"3.7"}


def test_an_answers_return_to_retrieval_is_required_without_a_cause():
    world = World(answering(route=0.01), [found([chunk("port")])])
    out = flow(world, required=True)
    assert "retrieve" not in route_of(world)
    assert next(t for t in out["transitions"] if t["to"] == "retrieve")["reason"] == "explicit_requirement"


def test_a_failed_required_retrieval_says_so_and_never_claims_no_retrieval_was_needed():
    unsearched = flow(World(answering(route=0.01), []), required=True, cause=CAUSE)
    assert unsearched["status"] == "unavailable" and unsearched["reason"] == "retrieval_unavailable"
    down = flow(World(lambda *a: decision.JevError("timeout"), [found([chunk("port")])]), required=True, cause=CAUSE)
    assert down["status"] == "unavailable" and down["evidence"], "the baseline searched instead"
    assert not unsearched["direct"] and not down["direct"]


def test_a_cause_is_checked_before_anything_runs():
    with pytest.raises(ValueError, match="cause"):
        knowledge.prepare("q", None, cause={"action_id": "x"})
    with pytest.raises(ValueError, match="cause"):
        knowledge.prepare("q", None, cause={**CAUSE, "operation": "prepare_work_turn"})


def test_a_cached_decision_is_a_call_that_costs_nothing():
    cache = decision.Cache()
    flow(World(answering(), [found([chunk("a")])]), cache=cache, available=["documents"])
    again = flow(World(lambda *a: pytest.fail("a cached decision was sent"), [found([chunk("a")])]), cache=cache,
                 available=["documents"])
    assert [c["provider"] for c in again["calls"]] == ["cache", "cache"]
    totals = tracing.totals(again["calls"])
    assert totals["provider_calls"] == 0 and totals["cache_hits"] == 2
    assert totals["cost_usd_known"] == 0 and totals["cost_unknown"] == 0


def test_totals_keep_known_cost_apart_from_unknown_and_read_old_records_as_unknown():
    calls = [tracing.call("draft", "host", "host", cost_usd=0.25), tracing.call("explain", "host", "host"),
             tracing.call("normalize", "translator", "cache"), {"purpose": ["route"], "provider": "jev"}]
    totals = tracing.totals(calls)
    assert totals["cost_usd_known"] == 0.25 and totals["cost_unknown"] == 2 and totals["cache_hits"] == 1
    with pytest.raises(ValueError):
        tracing.call("routing", "jev", "jev")


def test_a_gathered_action_retrieves_once_under_its_cause_and_exports_no_secret(flow_world, monkeypatch, tmp_path):
    hub, repo = flow_world
    monkeypatch.setenv("JEV_ENV", str(tmp_path / "jev.env"))
    monkeypatch.setattr(decisions, "LOGS", tmp_path / "actions")
    monkeypatch.setattr(knowledge.DECISIONS, "_items", type(knowledge.DECISIONS._items)())  # no earlier verdicts
    cfg = decision.Config("active", MODEL, "file", key=KEY)
    fake = World(answering(route=0.01), [])
    monkeypatch.setattr(decision, "evaluate", lambda cfg_, *a: fake.evaluate(*a))
    monkeypatch.setattr(knowledge, "english", lambda texts, seconds, owners=None, project=None: english(texts, 0))
    record = {"id": "r1", "point": "loop.fix",
              "proposal": {"proposal_id": CAUSE["action_id"], "operation": "retrieve_evidence",
                           "spec_revision": "3.7"}}
    pick = decisions.Pick(decisions.fix_offer()[1], record, Budget(seconds=30, calls=6, candidates=40), cfg,
                          ("proj", "spec"))
    text, ids = decisions.gathered(pick, f"Who covers the ingest pipeline? {KEY}", repo)
    assert ids and "Evidence the server retrieved" in text
    assert "retrieve" not in route_of(fake) and [s for s, *_ in fake.asked] == ["route", "judge"]
    kept = decisions.file_of(("proj", "spec")).read_text(encoding="utf-8")
    outcome = json.loads(kept.splitlines()[-1])["outcome"]
    assert outcome["status"] == "executed" and [c["purpose"] for c in outcome["calls"]] == [
        ["route", "source_select"], ["grade", "sufficiency"]]
    assert KEY not in kept and "ingest pipeline" not in json.dumps(outcome["calls"])

    run = knowledge.Run(repo, "wiki", f"Who covers the ingest pipeline? {KEY}", cfg)
    knowledge.prepare(f"Who covers the ingest pipeline? {KEY}", repo, cfg=cfg, run=run, cache=None, cause=CAUSE)
    summary = run.finish("answered")
    assert summary["calls"]["calls"] == 2 and summary["calls"]["cost_unknown"] == 2
    exported = json.dumps(knowledge.export(run.id, repo))
    assert '"kind": "call"' in exported and KEY not in exported and "ingest pipeline" not in exported


# -- ownership and versions ------------------------------------------------------------

def test_coverage_names_every_owner_and_what_is_still_provisional():
    items = {i["point"]: i for i in decisions.coverage(decision.Config("active", MODEL, "file"))["items"]}
    for i in items.values():
        assert {"point", "owner", "allowed_operations", "authority", "policy_version", "provisional"} <= set(i)
    assert items["query.retrieval_required"]["owner"] == "code"
    assert items["host_tools"]["owner"] == "host" and items["host_tools"]["covered"] is False
    # ASK and ANALYSIS were never fitted: the committed artifact covers the routing kinds only.
    route = items["query.route"]["decisions"]
    assert route["route"] == "fitted" and route["ask"] == route["analysis"] == "provisional"
    assert items["query.route"]["provisional"] is True
    assert items["deterministic"]["owner"] == "code" and items["deterministic"]["policy_version"] is None


def test_a_rule_for_ask_or_analysis_is_fitted_only_for_the_text_it_was_fitted_on(tmp_path):
    artifact = tmp_path / "policy.json"
    rules = {"ask": {"no": 0.1, "yes": 0.7}, "route": {"no": 0.1, "yes": 0.7}}
    artifact.write_text(json.dumps({"model": MODEL, "prompt_version": knowledge.PROMPT_VERSION, "rules": rules}),
                        encoding="utf-8")
    old = decision.policy(MODEL, artifact, knowledge.PROMPT_VERSION, knowledge.KIND_VERSIONS)
    assert old.fitted == ("route",), "an ask rule of unknown text is not relabelled as fitted"
    artifact.write_text(json.dumps({"model": MODEL, "prompt_version": knowledge.PROMPT_VERSION, "rules": rules,
                                    "kind_versions": knowledge.KIND_VERSIONS}), encoding="utf-8")
    assert set(decision.policy(MODEL, artifact, knowledge.PROMPT_VERSION, knowledge.KIND_VERSIONS).fitted) == {
        "ask", "route"}


def test_the_behavior_digest_moves_with_any_instruction(monkeypatch):
    before = knowledge.behavior()
    assert set(before["hashes"]) == {"prompts", "ask", "analysis", "grounding", "normalization", "graph_extraction"}
    monkeypatch.setattr(knowledge, "ASK", knowledge.ASK + " ")
    monkeypatch.setitem(knowledge.KIND_VERSIONS, "ask", "changed")
    assert knowledge.behavior()["digest"] != before["digest"]


# -- graph health ----------------------------------------------------------------------

def health(repo):
    return knowledge.graph_health(repo)


def poison(repo, *rows) -> None:
    """Rows written straight into the published generation's graph, as a bug would leave them."""

    store = searchd.Store(searchd.store_path(search.HUB, repo))
    try:
        with store.transaction() as db:
            db.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [(store.reading(), *row) for row in rows])
    finally:
        store.close()


def nodes_of(repo) -> list[str]:
    store = searchd.Store(searchd.store_path(search.HUB, repo))
    try:
        return [n for (n,) in store.db.execute("SELECT node_id FROM nodes WHERE gen = ? AND repo_id = ?",
                                               (store.reading(), evidence.repo_id(repo)))]
    finally:
        store.close()


def test_an_absent_index_is_not_indexed_and_nothing_is_built(graph_world):
    _hub, repo = graph_world
    out = health(repo)
    assert out["status"] == "not_indexed" and out["reason"] == "no_store" and out["counts"] is None
    assert not searchd.store_path(search.HUB, repo).exists(), "a health check built an index"


def test_a_valid_snapshot_is_healthy_with_its_counts_and_versions(graph_world):
    hub, repo = graph_world
    index_of(hub, repo).close()
    out = health(repo)
    assert out["status"] == "healthy" and out["reason"] is None, out
    assert out["counts"]["edges"] > 0 and out["counts"]["violations"] == dict.fromkeys(knowledge.FAILURES, 0)
    assert out["counts"]["by_origin"]["deterministic"]["adopted"] > 0
    assert out["versions"]["structure"] == knowledge_graph.STRUCTURE and out["versions"]["active_extraction"] is None
    assert out["generation"] is not None and out["semantic_evaluation"] is None


def test_dangling_invalid_and_cross_scope_edges_fail_by_edge_id(graph_world):
    hub, repo = graph_world
    index_of(hub, repo).close()
    a, b = nodes_of(repo)[:2]
    repo_id, other, nowhere = evidence.repo_id(repo), evidence.digest("repo", "other"), evidence.digest("nowhere")
    dangling, crossed, bogus = (evidence.digest("edge", n) for n in ("dangling", "crossed", "bogus"))
    poison(repo, (dangling, repo_id, a, nowhere, "links_to", 1, "deterministic", 1.0, "structure/1", "adopted"),
           (crossed, other, a, b, "links_to", 1, "deterministic", 1.0, "structure/1", "adopted"),
           (bogus, repo_id, a, b, "invented", 1, "deterministic", 1.0, "structure/1", "adopted"))
    out = health(repo)
    assert out["status"] == "failing"
    assert dangling in out["violations"]["dangling"] and crossed in out["violations"]["out_of_scope"]
    assert bogus in {v["edge_id"] for v in out["violations"]["invalid"]}
    assert out["counts"]["violations"]["dangling"] == 1


def test_a_source_changed_since_the_snapshot_is_stale_and_left_as_it_was(graph_world):
    hub, repo = graph_world
    index_of(hub, repo).close()
    bump(repo / "docs/ports.md", "# Ports\n\nThe daemon listens on 9000.\n")
    (repo / "docs/new.md").write_text("# New\n\nA page not indexed yet.\n", encoding="utf-8")
    out = health(repo)
    assert out["status"] == "stale" and out["reason"] == "sources_changed"
    assert out["counts"]["sources_changed"] == 1 and out["counts"]["sources_added"] == 1
    assert health(repo)["status"] == "stale", "the check re-indexed what it found changed"


def test_zero_edges_is_empty_not_healthy(graph_world):
    hub, repo = graph_world
    index_of(hub, repo).close()
    db = sqlite3.connect(searchd.store_path(search.HUB, repo))
    with db:
        db.execute("DELETE FROM edges")
    db.close()
    assert health(repo)["status"] == "empty"
