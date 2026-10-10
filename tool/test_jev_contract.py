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
import threading
import time

import pytest

import decision
import search
import translate
from common.budget import Budget
from main import decisions, knowledge, tracing
from search import daemon as searchd
from search import evidence, knowledge_graph, sources
from test_decision_flow import BOTH, MODEL, POLICY, REPO, SOURCES, World, answering, chunk, english, found
from test_decision_flow import run as run_flow
from test_decision_flow import world as flow_world  # noqa: F401 — fixture
from test_knowledge_graph import bump, index_of
from test_knowledge_graph import world as graph_world  # noqa: F401 — fixture

KEY = "contract-secret-key-4711"
CAUSE = {"action_id": "p" * 32, "point": "loop.fix", "operation": "retrieve_evidence", "state_revision": "3.7"}


def flow(world, *, required=False, cause=None, cache=None, available=SOURCES, external=False):
    budget = Budget(seconds=30, calls=6, candidates=40)
    return knowledge.Flow("What did the team decide about the port?", "", 8, omitted=None, available=list(available),
                          repo_id=REPO, graph=True, model=MODEL, live=True, budget=budget, pol=POLICY,
                          evaluate=world.evaluate, normalize=english, first=world.first, mend=world.mend,
                          cache=cache, required=required, cause=cause, external=external).run()


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


class Reply:
    """The translator's HTTP answer: one English sentence for each text of the batch."""

    def __init__(self, request):
        batch = json.loads(json.loads(request.data)["contents"][0]["parts"][0]["text"])
        text = json.dumps(["The team changes it." for _ in batch])
        self.body = json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}],
                                "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4}}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None

    def read(self):
        return self.body


def test_normalization_is_one_call_per_translator_request_and_none_for_what_was_never_sent(monkeypatch, tmp_path):
    monkeypatch.setattr(translate, "CACHE", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(translate, "ENV", tmp_path / "absent.env")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.delenv("TRANSLATE_MONTHLY_USD", raising=False)
    sent = []
    monkeypatch.setattr(translate.urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Reply(req))

    def normalized(texts):
        run = knowledge.Flow("q", "", 8, omitted=None, available=["documents"], repo_id=REPO, graph=True, model=MODEL,
                             live=True, budget=Budget(seconds=30, calls=6, candidates=40), pol=POLICY,
                             evaluate=None, normalize=lambda t, s, o: knowledge.english(t, s, o), first=None,
                             mend=None)
        run.english(texts)
        return [(c["provider"], c["texts"], c["outcome"]) for c in run.dossier["calls"]]

    korean = ["팀이 포트를 바꾼다", "팀이 데몬을 바꾼다", "팀이 검색을 바꾼다", "팀이 색인을 바꾼다", "팀이 그래프를 바꾼다"]
    assert normalized(korean + ["An English line."]) == [("translator", 4, "ok"), ("translator", 1, "ok")]
    assert len(sent) == 2, "five texts go out in two batches"
    assert normalized(korean) == [("cache", 5, "ok")]
    assert normalized(["팀이 문서를 바꾼다", *korean]) == [("translator", 1, "ok"), ("cache", 5, "ok")]
    monkeypatch.delenv("GEMINI_API_KEY")
    assert normalized(["팀이 규칙을 바꾼다"]) == [], "no key: nothing was sent, so nothing was called"
    assert len(sent) == 3


def test_an_external_repair_keeps_each_grading_request_sent_even_when_the_fetch_is_abandoned(graph_world,
                                                                                            monkeypatch):
    _hub, repo = graph_world
    from search import providers

    paper = {"arxiv_id": "2401.00001", "version": "v1", "title": "Rotas", "authors": ["A"],
             "published": "2024-01-01", "summary": "On-call rotas.", "abs_url": "https://arxiv.org/abs/2401.00001v1",
             "pdf_url": "https://arxiv.org/pdf/2401.00001v1"}
    monkeypatch.setattr(providers, "arxiv", lambda query=None, ids=None, n=5, seconds=0: [paper])
    sent = []

    def send(key, body, seconds, cancel, dispatched):
        # Jev answers the grading with tokens spent and nothing usable: sent, billed, failed.
        sent.append(body)
        dispatched()
        return {"model": "jev-grader", "usage": {"input_tokens": 20, "output_tokens": 2}, "answers": {}}

    monkeypatch.setattr(decision, "send", send)
    real_policy = decision.policy

    def slow_policy(*a, **k):   # the papers are still being read when the repair's time runs out
        time.sleep(1.0)
        return real_policy(*a, **k)

    monkeypatch.setattr(decision, "policy", slow_policy)
    cfg = decision.Config("active", MODEL, "file", key=KEY)

    class Researching(World):
        def mend(self, req, result, need, ids):
            if need != "external":
                return super().mend(req, result, need, ids)
            out = knowledge.repair(req, result, need, repo, budget=Budget(seconds=0.4, calls=6, candidates=40),
                                   cfg=cfg, external=True)
            cancelled = Budget(seconds=5, calls=6, candidates=40)
            cancelled.cancel.set()
            # A grading stopped before its request went out: no call.
            knowledge.grade_papers("port", [paper], cfg, cancelled, out["note"]["graded"])
            return {"note": out["note"], "requests": [], "results": []}

    out = flow(Researching(answering(coverage=0.1), [found([chunk("port")])]), available=["documents"],
               external=True)
    research = [(c["purpose"], c["provider"], c["model"], c["token_usage"], c["outcome"]) for c in out["calls"]
                if c["purpose"] in (["research"], ["grade"])]
    assert research == [(["research"], "arxiv", None, None, "failed"),
                        (["grade"], "jev", "jev-grader", {"input": 20, "output": 2}, "failed")]
    assert len(sent) == 1


def test_an_external_repair_accounts_the_translation_of_its_query(graph_world, monkeypatch, tmp_path):
    _hub, repo = graph_world
    from search import providers

    monkeypatch.setattr(translate, "ENV", tmp_path / "absent.env")
    monkeypatch.setattr(translate, "CACHE", tmp_path / "cache.sqlite3")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.delenv("TRANSLATE_MONTHLY_USD", raising=False)
    monkeypatch.setattr(translate.urllib.request, "urlopen", lambda req, timeout: Reply(req))
    searched = []
    monkeypatch.setattr(providers, "arxiv", lambda query=None, ids=None, n=5, seconds=0: searched.append(query) or [])

    class Researching(World):
        def mend(self, req, result, need, ids):
            if need != "external":
                return super().mend(req, result, need, ids)
            # The question's own normalization failed: the repair sends its Korean to the translator again.
            out = knowledge.repair({**req, "query_en": None, "query_original": "팀이 포트를 바꾼다"}, result, need, repo,
                                   budget=Budget(seconds=5, calls=6, candidates=40),
                                   cfg=decision.Config("active", MODEL, "file", key=KEY), external=True)
            return {"note": out["note"], "requests": [], "results": []}

    out = flow(Researching(answering(coverage=0.1), [found([chunk("port")])]), available=["documents"],
               external=True)
    assert searched == ["The team changes it."]
    assert [(c["purpose"], c["provider"]) for c in out["calls"] if c["purpose"] in (["research"], ["normalize"])] \
        == [(["research"], "arxiv"), (["normalize"], "translator")]


def test_a_jev_request_is_sent_only_once_the_transport_wrote_it(monkeypatch):
    written = []

    class Connection:
        def request(self, *a, **kw):
            written.append(a)

        def getresponse(self):
            self.status = 200
            return self

        def read(self, limit):
            return b'{"answers": {}}'

        def close(self):
            pass

    monkeypatch.setattr(decision, "resolve", lambda end, cancel: "192.0.2.1")
    monkeypatch.setattr(decision, "connection", lambda found, timeout: Connection())
    cfg, questions = decision.Config("active", MODEL, "file", key=KEY), {"q": decision.noul("Is it?")}

    def asked(budget):
        trace = []
        with pytest.raises(decision.JevError):
            decision.evaluate(cfg, {"query": "q"}, questions, trace, budget, "papers")
        return [(e["error"], e.get("sent", False)) for e in trace]

    monkeypatch.setattr(decision, "IN_FLIGHT", threading.Semaphore(0))   # every slot taken
    assert asked(Budget(seconds=0.3, calls=2, candidates=0)) == [("busy", False)] and written == []
    monkeypatch.setattr(decision, "IN_FLIGHT", threading.Semaphore(1))
    assert asked(Budget(seconds=5, calls=2, candidates=0)) == [("invalid_response", True)] and len(written) == 1


def test_a_failed_jev_decision_is_a_call_with_its_tokens_only_when_it_went_out(monkeypatch):
    def send(key, body, seconds, cancel, dispatched):
        dispatched()
        return {"model": "jev-x", "usage": {"input_tokens": 20, "output_tokens": 2}, "answers": {}}

    monkeypatch.setattr(decision, "send", send)

    def route_calls(key):
        cfg = decision.Config("active", MODEL, "file", key=key)
        out = knowledge.Flow("What did the team decide about the port?", "", 8, omitted=None,
                             available=["documents"], repo_id=REPO, graph=True, model=MODEL, live=True,
                             budget=Budget(seconds=30, calls=6, candidates=40), pol=POLICY,
                             evaluate=lambda *a: decision.evaluate(cfg, *a), normalize=english,
                             first=World(answering(), [found([chunk("a")])]).first, mend=None).run()
        return [(c["provider"], c["token_usage"], c["outcome"]) for c in out["calls"] if "route" in c["purpose"]]

    assert route_calls(KEY) == [("jev", {"input": 20, "output": 2}, "invalid")]
    assert route_calls("") == [], "no key: nothing went out"


def test_malformed_usage_is_no_count_and_never_stops_retrieval(monkeypatch):
    def send(key, body, seconds, cancel, dispatched):
        dispatched()
        return {"model": "jev-x", "usage": "not-an-object", "answers": {}}

    monkeypatch.setattr(decision, "send", send)
    cfg = decision.Config("active", MODEL, "file", key=KEY)
    world = World(answering(), [found([chunk("a")])])
    out = knowledge.Flow("What did the team decide about the port?", "", 8, omitted=None, available=["documents"],
                         repo_id=REPO, graph=True, model=MODEL, live=True,
                         budget=Budget(seconds=30, calls=6, candidates=40), pol=POLICY,
                         evaluate=lambda *a: decision.evaluate(cfg, *a), normalize=english, first=world.first,
                         mend=None).run()
    assert not str(out["reason"]).startswith("error:"), out["reason"]
    assert world.firsts, "the failed route still searched"
    assert [(c["provider"], c["token_usage"]) for c in out["calls"] if "route" in c["purpose"]] == [("jev", None)]


def replayed(out, tape, query="What did the team decide about the port?"):
    recorded = {**json.loads(json.dumps(tape.data)), "limits": {"calls": 6, "candidates": 40, "tokens": None},
                "policy": POLICY.record(), "prompt_version": knowledge.PROMPT_VERSION,
                "inputs": {"query": query, "brief": "", "k": 8, "omitted": None, "available": ["documents"],
                           "repo_id": REPO, "graph": True, "model": MODEL, "live": True, "external": False},
                "transitions": knowledge.steps(out), "calls": tracing.totals(out["calls"])}
    return knowledge.replay(recorded)


def test_a_replay_accounts_cached_and_failed_decisions_as_the_run_did():
    cache = decision.Cache()
    run_flow(World(answering(), [found([chunk("a")])]), available=["documents"], cache=cache)
    tape = knowledge.Tape()
    out = run_flow(World(lambda *a: pytest.fail("a cached decision was sent"), [found([chunk("a")])]),
                   available=["documents"], cache=cache, tape=tape)
    assert tracing.totals(out["calls"])["cache_hits"] == 2
    again = replayed(out, tape)
    assert again["matches"] and again["calls_match"], tracing.totals(again["dossier"]["calls"])

    # A route that went out and came back unusable: its call and tokens replay too.
    def answer(stage, state, questions):
        return decision.JevError("invalid_response") if stage == "route" else answering()(stage, state, questions)

    tape = knowledge.Tape()
    out = run_flow(World(answer, [found([chunk("a")])]), available=["documents"], tape=tape)
    assert tracing.totals(out["calls"])["tokens"]["input"] > 0
    again = replayed(out, tape)
    assert again["matches"] and again["calls_match"], tracing.totals(again["dossier"]["calls"])


def test_a_draft_that_failed_or_was_stopped_is_still_a_call(tmp_path):
    run = knowledge.Run(tmp_path, "wiki", "q", decision.Config("active", MODEL, "file", key=KEY))

    class Failed(Exception):
        pass

    def failing(message):
        yield {"kind": "tool", "text": "python tool/jev_search.py port"}
        raise Failed("host exited")

    turn = knowledge.drafted(run, failing)("brief")
    next(turn)
    with pytest.raises(Failed):
        next(turn)

    def endless(message):
        while True:
            yield "text"

    turn = knowledge.drafted(run, endless)("brief")
    next(turn)
    turn.close()   # the reader stopped the run
    run.finish("abstained")
    assert [(c["purpose"], c["outcome"], c["host_searches"]) for c in run.calls] == \
        [(["draft"], "failed", 1), (["draft"], "cancelled", 0)]
    assert tracing.totals(run.calls)["cost_unknown"] == 2


def test_a_split_is_a_call_only_when_its_request_went_out(monkeypatch, tmp_path):
    monkeypatch.setattr(translate, "ENV", tmp_path / "absent.env")
    monkeypatch.setattr(translate, "CACHE", tmp_path / "cache.sqlite3")
    monkeypatch.delenv("TRANSLATE_MONTHLY_USD", raising=False)
    monkeypatch.setattr(translate.urllib.request, "urlopen", lambda req, timeout: Reply(req))

    def split_calls():
        out = run_flow(World(answering(), [found([chunk("a")])]), query=BOTH, available=["documents"],
                       divide=lambda question, seconds: translate.parts(question, time.monotonic() + seconds))
        return [(c["provider"], c["outcome"]) for c in out["calls"] if c["purpose"] == ["decompose"]]

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    assert split_calls() == [("translator", "ok")]
    monkeypatch.delenv("GEMINI_API_KEY")
    assert split_calls() == [], "no key: nothing was sent"


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
    assert set(before["hashes"]) == {"prompts", "ask", "analysis", "progress", "grounding", "normalization",
                                     "graph_extraction", "fallback"}
    monkeypatch.setattr(knowledge, "ASK", knowledge.ASK + " ")
    monkeypatch.setitem(knowledge.KIND_VERSIONS, "ask", "changed")
    assert knowledge.behavior()["digest"] != before["digest"]
    # Who settles what Jev leaves uncertain is behavior too (reliability PR 5, v2): runs with and without it differ.
    moved = knowledge.behavior()
    monkeypatch.setenv("WIKI_JEV_FALLBACK", "host")
    assert knowledge.behavior()["hashes"]["fallback"] != moved["hashes"]["fallback"]


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


def test_a_health_check_creates_no_database_and_takes_no_write_lock(graph_world):
    hub, repo = graph_world
    index_of(hub, repo).close()
    records = sources.records_folder(repo) / "sources.sqlite3"
    for path in records.parent.glob("sources.sqlite3*"):
        path.unlink()
    writer = sqlite3.connect(searchd.store_path(search.HUB, repo), timeout=0.1)
    writer.execute("BEGIN IMMEDIATE")   # another process mid-write: a writer here would wait, then fail
    try:
        assert health(repo)["status"] == "healthy"
    finally:
        writer.rollback()
        writer.close()
    assert not records.exists(), "a health check created the records database"


def test_a_file_added_during_the_check_reads_stale(graph_world, monkeypatch):
    hub, repo = graph_world
    index_of(hub, repo).close()
    real = knowledge_graph.verify

    def verify(graph, chunks):
        (repo / "docs/created-during-health.md").write_text("# Late\n\nWritten mid-check.\n", encoding="utf-8")
        return real(graph, chunks)

    monkeypatch.setattr(knowledge_graph, "verify", verify)
    out = health(repo)
    assert out["status"] == "stale" and out["counts"]["sources_added"] == 1, out


def test_a_sync_during_the_final_listing_reads_stale(graph_world, monkeypatch):
    hub, repo = graph_world
    index_of(hub, repo).close()
    real, scans = searchd.Index.scan, []

    def scan(self):
        scans.append(self)
        if len(scans) == 2:   # health's listing after its checks: another process syncs an edit now
            bump(repo / "docs/ports.md", "# Ports\n\nThe daemon listens on 9000.\n")
            index_of(hub, repo).close()
        return real(self)

    monkeypatch.setattr(searchd.Index, "scan", scan)
    out = health(repo)
    assert out["status"] == "stale" and out["reason"] == "store_changed", out


def test_a_source_record_changed_during_the_check_reads_stale(graph_world, monkeypatch):
    hub, repo = graph_world
    index_of(hub, repo).close()
    real = knowledge_graph.verify

    def verify(graph, chunks):
        # Another process registers a source: the records move, the evidence store and the listing do not.
        with sources.Records(sources.records_folder(repo)) as records, records.transaction() as db:
            sources.Records.bump(db)
        return real(graph, chunks)

    monkeypatch.setattr(knowledge_graph, "verify", verify)
    out = health(repo)
    assert out["status"] == "stale" and out["reason"] == "store_changed", out


def test_a_records_file_created_during_the_check_reads_stale(graph_world, monkeypatch):
    hub, repo = graph_world
    index_of(hub, repo).close()
    for path in sources.records_folder(repo).glob("sources.sqlite3*"):
        path.unlink()
    real = knowledge_graph.verify

    def verify(graph, chunks):
        with sources.Records(sources.records_folder(repo)) as records, records.transaction() as db:
            sources.Records.bump(db)
        return real(graph, chunks)

    monkeypatch.setattr(knowledge_graph, "verify", verify)
    out = health(repo)
    assert out["status"] == "stale" and out["reason"] == "store_changed", out


def test_zero_edges_is_empty_not_healthy(graph_world):
    hub, repo = graph_world
    index_of(hub, repo).close()
    db = sqlite3.connect(searchd.store_path(search.HUB, repo))
    with db:
        db.execute("DELETE FROM edges")
    db.close()
    assert health(repo)["status"] == "empty"
