"""Controller policy and transport checks; no credentials or external calls."""

import json
import math
import time

import pytest

from search import controller as jev
from search.daemon import Embedder, Index


def hit(name, text="Useful evidence"):
    return {"path": f"/repo/{name}.md", "line": 3, "heading": name, "text": text}


def test_routes_grades_and_preserves_citations(monkeypatch):
    calls = []

    def evaluate(state, questions, trace):
        assert all(q["instructions"].isascii() for q in questions.values())
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 0.01, "documents": 0.9, "memory": 0.1}
        if "sufficient" in questions:
            return {"sufficient": 0.95}
        return {"0": 0.1, "1": 0.5, "2": 0.99}

    def retrieve(query, root, sources, k, timeout):
        calls.append(sources)
        return [hit("irrelevant"), hit("partial"), hit("contradiction")]

    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "retrieve", retrieve)
    out = jev.prepare("What was decided?", "/repo")
    assert calls == [["documents"]]
    assert out["status"] == "supported" and out["sources"] == ["documents"]
    assert [(h["path"], h["line"]) for h in out["evidence"]] == [
        ("/repo/contradiction.md", 3), ("/repo/partial.md", 3)]


def test_direct_answer_does_not_retrieve(monkeypatch):
    monkeypatch.setattr(jev, "evaluate", lambda *a: {"retrieve": 0.01, "hub": 0.01})
    monkeypatch.setattr(jev, "retrieve", lambda *a: pytest.fail("unexpected retrieval"))
    assert jev.prepare("Hello", None)["status"] == "direct"


def test_insufficient_evidence_widens_once_and_retains_bridge(monkeypatch):
    calls = []

    def evaluate(state, questions, trace):
        if "retrieve" in questions:
            return {"retrieve": 0.9, "hub": 0.01, "documents": 0.01, "memory": 0.9}
        return {q: 0.3 if q != "sufficient" else 0.1 for q in questions}

    def retrieve(query, root, sources, k, timeout):
        calls.append((sources, k))
        return [hit("bridge" if len(calls) == 1 else "detail")]

    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "retrieve", retrieve)
    out = jev.prepare("Find the reason", "/repo")
    assert calls == [(["memory"], 10), (list(jev.SOURCES), 12)]
    assert out["status"] == "insufficient"
    assert {h["heading"] for h in out["evidence"]} == {"bridge", "detail"}


def test_missing_key_and_invalid_judgment_fall_back_to_all_sources(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(jev, "retrieve", lambda q, p, s, k, t: calls.append(s) or [hit("baseline")])
    out = jev.prepare("Find evidence", "/repo")
    assert out["status"] == "fallback" and out["evidence"] == [hit("baseline")]
    assert calls == [list(jev.SOURCES)]
    monkeypatch.setattr(jev, "evaluate", lambda *a: {})
    assert jev.prepare("Find evidence", "/repo")["status"] == "fallback"


def test_truncated_passage_cannot_be_dropped_or_prove_sufficiency(monkeypatch):
    monkeypatch.setattr(jev, "retrieve", lambda *a: [hit("long", "x" * (jev.MAX_PASSAGE + 1))])

    def evaluate(state, questions, trace):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 1 if q == "sufficient" else 0 for q in questions}

    monkeypatch.setattr(jev, "evaluate", evaluate)
    out = jev.prepare("Question", None)
    assert out["status"] == "insufficient" and len(out["evidence"]) == 1


def test_irrelevant_truncated_passage_does_not_block_whole_evidence(monkeypatch):
    monkeypatch.setattr(jev, "retrieve", lambda *a: [hit("long", "x" * (jev.MAX_PASSAGE + 1)), hit("short")])
    judged = []

    def evaluate(state, questions, trace):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        if "sufficient" in questions:
            judged.append([e["heading"] for e in state["evidence"]])
            return {"sufficient": 1}
        return {"0": 0, "1": 1}

    monkeypatch.setattr(jev, "evaluate", evaluate)
    out = jev.prepare("Question", None)
    assert out["status"] == "supported" and judged == [["short"]]
    assert {h["heading"] for h in out["evidence"]} == {"long", "short"}


def test_dossier_names_the_sources_actually_searched_after_widening(monkeypatch):
    def evaluate(state, questions, trace):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 0, "documents": 0, "memory": 1}
        return {q: 1 for q in questions}

    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "retrieve", lambda q, r, s, k, t: [] if s == ["memory"] else [hit("doc")])
    out = jev.prepare("Question", "/repo")
    assert out["status"] == "supported" and out["sources"] == list(jev.SOURCES)


def test_retrieval_failure_never_escapes_the_turn(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def broken(*a):
        raise RuntimeError("index")

    monkeypatch.setattr(jev, "retrieve", broken)
    out = jev.prepare("Question", "/repo")
    assert out["status"] == "fallback" and out["evidence"] == []
    assert out["trace"][-1] == {"fallback_retrieval": "RuntimeError"}


# At 0.6 the third call would end past the budget, so it never starts. At 0.8
# route, grade and assess fit, and the widened search is skipped because a call
# after it would not. Either way only the fallback searches again.
@pytest.mark.parametrize("budget, judged", [(0.6, 2), (0.8, 3)])
def test_one_budget_bounds_every_call_in_a_run(monkeypatch, budget, judged):
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused")
    calls = []

    def evaluate(state, questions, trace):
        calls.append(time.monotonic())
        time.sleep(0.2)
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 0.5 for q in questions}

    searched = []
    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "retrieve", lambda *a: searched.append(a) or [hit("a")])
    monkeypatch.setattr(jev, "TIMEOUT", 0.25)
    # `run`, not `prepare`: the steps' own budget, without the exit's bound.
    started = time.monotonic()
    out = jev.run("Question", None, ["hub"], "", 8, started + budget)
    assert time.monotonic() - started < budget
    assert len(calls) == judged and len(searched) == 2
    assert out["status"] == "fallback" and out["evidence"] == [hit("a")]
    assert out["trace"][-1] == {"fallback": "TimeoutError", "reason": "budget"}


def test_searches_spend_the_same_budget(monkeypatch):
    import search.daemon as daemon

    monkeypatch.setenv("TYPESAFE_API_KEY", "unused")
    asked = []

    def slow_daemon(query, project, timeout, k, sources):
        asked.append(timeout)
        time.sleep(min(timeout, 0.3))
        return [hit("daemon")]

    class Cold:
        def __init__(self, *a):
            pass

        def refresh(self):
            pass

        def search(self, query, k, sources):
            return [hit("cold")]

    def evaluate(state, questions, trace):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 0.5 for q in questions}

    monkeypatch.setattr(jev, "ask", slow_daemon)
    monkeypatch.setattr(daemon, "Index", Cold)
    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "TIMEOUT", 0.1)
    budget = 0.6
    started = time.monotonic()
    out = jev.run("Question", None, ["hub"], "", 8, started + budget)
    # Two daemon searches use the budget up; the fallback gets nothing left, so
    # it skips the daemon for the cold index instead of waiting three seconds more.
    assert time.monotonic() - started < budget + 0.15
    assert len(asked) == 2 and asked[0] <= budget and asked[1] < budget
    assert out["trace"][-1]["reason"] == "budget" and out["evidence"] == [hit("cold")]


def test_a_slow_cold_index_cannot_hold_the_caller_past_the_budget(monkeypatch):
    import search.daemon as daemon

    class Slow:
        def __init__(self, *a):
            pass

        def refresh(self):
            time.sleep(1.0)

        def search(self, query, k, sources):
            return [hit("late")]

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(jev, "ask", lambda *a, **kw: None)
    monkeypatch.setattr(daemon, "Index", Slow)
    monkeypatch.setattr(jev, "BUDGET", 0.2)
    started = time.monotonic()
    out = jev.prepare("Question", "/repo")
    assert time.monotonic() - started < 0.5
    assert out["status"] == "fallback" and out["evidence"] == []
    assert out["trace"] == [{"fallback": "TimeoutError", "reason": "budget"}]


def test_source_selection_precedes_top_k_and_never_reads_other_memory(tmp_path):
    hub, repo = tmp_path / "hub", tmp_path / "repo"
    for folder in (hub / "operator", repo / "docs", repo / ".wiki/memory", tmp_path / "other/.wiki/memory"):
        folder.mkdir(parents=True)
    (hub / "operator/rule.md").write_text("# Rule\n\nbanana banana", encoding="utf-8")
    (repo / "docs/doc.md").write_text("# Document\n\nbanana", encoding="utf-8")
    (repo / ".wiki/memory/saved.md").write_text("# Memory\n\nbanana", encoding="utf-8")
    (repo / ".wiki/memory/saved.raw.md").write_text("# Raw\n\nbanana", encoding="utf-8")
    (tmp_path / "other/.wiki/memory/secret.md").write_text("# Secret\n\nbanana", encoding="utf-8")
    index = Index(hub, repo, Embedder(None))
    index.refresh()
    assert index.search("banana", 1, ["documents"])[0]["heading"] == "Document"
    assert index.search("banana", 1, ["memory"])[0]["heading"] == "Memory"
    assert len(index.search("banana", 10)) == 3
    with pytest.raises(ValueError):
        index.search("banana", 1, ["papers"])


@pytest.mark.parametrize("value", [True, "0.9", -0.1, 1.1, math.nan, math.inf, None])
def test_transport_rejects_invalid_probabilities(monkeypatch, value):
    fake_transport(monkeypatch, value)
    with pytest.raises(ValueError):
        jev.evaluate({}, {"q": jev.question("Is it relevant?")}, [])


def fake_transport(monkeypatch, value=0.9, delay=0):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    sent = []

    class Connection:
        status = 200

        def __init__(self, host, timeout):
            assert host == "api.typesafe.ai"

        def request(self, method, path, body, headers):
            assert method == "POST" and path == "/v1/systemone"
            assert headers["Authorization"] == "Bearer test-key"
            sent.append(json.loads(body))

        def getresponse(self):
            time.sleep(delay)
            return self

        def read(self, limit):
            return json.dumps({"model": "test-model", "answers": {"q": {"type": "noul", "noul": value}},
                               "usage": {"input_tokens": 20}}).encode()

        def close(self):
            pass

    monkeypatch.setattr(jev.http.client, "HTTPSConnection", Connection)
    return sent


def test_transport_records_usage_without_credentials_and_has_deadline(monkeypatch):
    sent = fake_transport(monkeypatch)
    trace = []
    assert jev.evaluate({"query": "한글"}, {"q": jev.question("Relevant?")}, trace) == {"q": 0.9}
    assert sent[0]["state"]["query"] == "한글"
    assert trace[0]["usage"]["input_tokens"] == 20
    assert "test-key" not in json.dumps(trace)
    fake_transport(monkeypatch, delay=0.2)
    monkeypatch.setattr(jev, "TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        jev.evaluate({}, {"q": jev.question("Relevant?")}, [])
