"""Controller policy checks; no credentials or external calls. The transport's
own checks are in `test_decision.py`."""

import functools
import threading
import time

import pytest

import decision
from common.budget import Budget
from common.language import language
from search import controller as jev
from search import evidence
from search.daemon import Embedder, Index


def hit(name, text="Useful evidence", completeness="whole"):
    """A hit shaped as the index returns one, EvidenceChunk fields and all."""

    repo = evidence.digest("repo", "/repo")
    source = evidence.source_id(repo, f"{name}.md")
    revision = evidence.digest("revision", text)
    return {"path": f"/repo/{name}.md", "line": 3, "end_line": 3 + text.count("\n"), "heading": name,
            "heading_path": [name], "text": text, "repo_id": repo, "source_id": source, "revision": revision,
            "chunk_id": evidence.chunk_id(source, revision, 3, 3 + text.count("\n")), "kind": "document",
            "visibility": "repository", "completeness": completeness, "language": language(text),
            "locator": {"path": f"{name}.md", "start_line": 3, "end_line": 3 + text.count("\n")}}


def keyless():
    """The real transport with the settings a test run reads: no key."""
    return functools.partial(decision.evaluate, decision.config())


def test_routes_grades_and_preserves_citations(monkeypatch):
    calls = []

    def evaluate(state, questions, trace, *_):
        assert all(q["instructions"].isascii() for q in questions.values())
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 0.01, "documents": 0.9, "memory": 0.1}
        if "sufficient" in questions:
            return {"sufficient": 0.95}
        return {"0": 0.1, "1": 0.5, "2": 0.99}

    def retrieve(query, root, sources, k, timeout):
        calls.append(sources)
        return [hit("irrelevant"), hit("partial"), hit("contradiction")]

    monkeypatch.setattr(jev, "retrieve", retrieve)
    out = jev.prepare("What was decided?", "/repo", evaluate=evaluate)
    assert calls == [["documents"]]
    assert out["status"] == "supported" and out["sources"] == ["documents"]
    assert [(h["path"], h["locator"]["start_line"]) for h in out["evidence"]] == [
        ("/repo/contradiction.md", 3), ("/repo/partial.md", 3)]


def test_direct_answer_does_not_retrieve(monkeypatch):
    monkeypatch.setattr(jev, "retrieve", lambda *a: pytest.fail("unexpected retrieval"))
    out = jev.prepare("Hello", None, evaluate=lambda *a: {"retrieve": 0.01, "hub": 0.01})
    assert out["status"] == "direct"
    assert out["budget"]["policy"] == "jev-budget-1" and out["budget"]["limits"]["calls"] == 6


def test_insufficient_evidence_widens_once_and_retains_bridge(monkeypatch):
    calls = []

    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 0.9, "hub": 0.01, "documents": 0.01, "memory": 0.9}
        return {q: 0.3 if q != "sufficient" else 0.1 for q in questions}

    def retrieve(query, root, sources, k, timeout):
        calls.append((sources, k))
        return [hit("bridge" if len(calls) == 1 else "detail")]

    monkeypatch.setattr(jev, "retrieve", retrieve)
    out = jev.prepare("Find the reason", "/repo", evaluate=evaluate)
    assert calls == [(["memory"], 10), (list(jev.SOURCES), 12)]
    assert out["status"] == "insufficient"
    assert {h["heading_path"][0] for h in out["evidence"]} == {"bridge", "detail"}


def test_missing_key_and_invalid_judgment_fall_back_to_all_sources(monkeypatch):
    calls = []
    monkeypatch.setattr(jev, "retrieve", lambda q, p, s, k, t: calls.append(s) or [hit("baseline")])
    out = jev.prepare("Find evidence", "/repo", evaluate=keyless())
    assert out["status"] == "fallback" and out["evidence"] == [jev.item(hit("baseline"))]
    assert calls == [list(jev.SOURCES)]
    assert out["trace"][-1] == {"fallback": "JevError", "reason": "missing_api_key"}
    out = jev.prepare("Find evidence", "/repo", evaluate=lambda *a: {})
    assert out["status"] == "fallback"
    assert out["trace"][-1] == {"fallback": "KeyError", "reason": "invalid_or_unavailable_decision"}


@pytest.mark.parametrize("category", ["auth_failed", "quota", "timeout", "unavailable"])
def test_each_provider_failure_keeps_its_own_reason(monkeypatch, category):
    def failing(*a):
        raise decision.JevError(category)

    monkeypatch.setattr(jev, "retrieve", lambda *a: [hit("baseline")])
    out = jev.prepare("Find evidence", "/repo", evaluate=failing)
    assert out["status"] == "fallback" and out["evidence"] == [jev.item(hit("baseline"))]
    assert out["trace"][-1] == {"fallback": "JevError", "reason": category}


def test_cancellation_ends_the_run_without_a_fallback_search(monkeypatch):
    budget = Budget(seconds=30, calls=6, candidates=40)
    searched = []

    def evaluate(*a):
        budget.cancel.set()
        time.sleep(0.3)
        return {"retrieve": 1, "hub": 1}

    monkeypatch.setattr(jev, "retrieve", lambda *a: searched.append(a) or [hit("a")])
    started = time.monotonic()
    out = jev.prepare("Question", None, evaluate=evaluate, budget=budget)
    assert time.monotonic() - started < 0.25, "the wait did not hear the cancel"
    assert out["status"] == "fallback" and out["trace"] == [{"fallback": "Cancelled", "reason": "cancelled"}]
    time.sleep(0.4)
    assert searched == [], "a cancelled run searched"


def test_candidates_come_out_of_one_allowance(monkeypatch):
    batches = []

    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        if "passages" in state:
            batches.append(len(state["passages"]))
        return {q: 0.5 for q in questions}

    monkeypatch.setattr(jev, "retrieve", lambda q, r, s, k, t: [hit(f"h{i}") for i in range(12)])
    out = jev.prepare("Question", None, evaluate=evaluate, budget=Budget(seconds=30, calls=6, candidates=15))
    assert batches == [12, 3]
    assert out["status"] == "insufficient" and out["budget"]["used"]["candidates"] == 15


def test_truncated_passage_cannot_be_dropped_or_prove_sufficiency(monkeypatch):
    monkeypatch.setattr(jev, "retrieve", lambda *a: [hit("long", "x" * (jev.MAX_PASSAGE + 1))])

    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 1 if q == "sufficient" else 0 for q in questions}

    out = jev.prepare("Question", None, evaluate=evaluate)
    assert out["status"] == "insufficient" and len(out["evidence"]) == 1


def test_irrelevant_truncated_passage_does_not_block_whole_evidence(monkeypatch):
    monkeypatch.setattr(jev, "retrieve", lambda *a: [hit("long", "x" * (jev.MAX_PASSAGE + 1)), hit("short")])
    judged = []

    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        if "sufficient" in questions:
            judged.append([e["heading"] for e in state["evidence"]])
            return {"sufficient": 1}
        return {"0": 0, "1": 1}

    out = jev.prepare("Question", None, evaluate=evaluate)
    assert out["status"] == "supported" and judged == [["short"]]
    assert {h["heading_path"][0] for h in out["evidence"]} == {"long", "short"}


def test_dossier_names_the_sources_actually_searched_after_widening(monkeypatch):
    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 0, "documents": 0, "memory": 1}
        return {q: 1 for q in questions}

    monkeypatch.setattr(jev, "retrieve", lambda q, r, s, k, t: [] if s == ["memory"] else [hit("doc")])
    out = jev.prepare("Question", "/repo", evaluate=evaluate)
    assert out["status"] == "supported" and out["sources"] == list(jev.SOURCES)


def test_retrieval_failure_never_escapes_the_turn(monkeypatch):
    def broken(*a):
        raise RuntimeError("index")

    monkeypatch.setattr(jev, "retrieve", broken)
    out = jev.prepare("Question", "/repo", evaluate=keyless())
    assert out["status"] == "fallback" and out["evidence"] == []
    assert out["trace"][-1] == {"fallback_retrieval": "RuntimeError"}


# At 0.6 the third call would end past the budget, so it never starts. At 0.8
# route, grade and assess fit, and the widened search is skipped because a call
# after it would not. Either way only the fallback searches again.
@pytest.mark.parametrize("budget, judged", [(0.6, 2), (0.8, 3)])
def test_one_budget_bounds_every_call_in_a_run(monkeypatch, budget, judged):
    calls = []

    def evaluate(state, questions, trace, *_):
        calls.append(time.monotonic())
        time.sleep(0.2)
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 0.5 for q in questions}

    searched = []
    monkeypatch.setattr(jev, "retrieve", lambda *a: searched.append(a) or [hit("a")])
    # `run`, not `prepare`: the steps' own budget, without the exit's bound.
    started = time.monotonic()
    out = jev.run("Question", None, ["hub"], "", 8, evaluate,
                  Budget(seconds=budget, calls=6, candidates=40, call_seconds=0.25))
    assert time.monotonic() - started < budget
    assert len(calls) == judged and len(searched) == 2
    assert out["status"] == "fallback" and out["evidence"] == [jev.item(hit("a"))]
    assert out["trace"][-1] == {"fallback": "Exhausted", "reason": "budget"}


def test_searches_spend_the_same_budget(monkeypatch):
    import search.daemon as daemon

    asked = []

    def slow_daemon(query, project, timeout, k, sources):
        asked.append(timeout)
        time.sleep(min(timeout, 0.3))
        return [hit("daemon")]

    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 0.5 for q in questions}

    monkeypatch.setattr(jev, "ask", slow_daemon)
    monkeypatch.setattr(daemon, "Index", lambda *a: pytest.fail("cold build with no time left"))
    budget = 0.6
    started = time.monotonic()
    out = jev.run("Question", None, ["hub"], "", 8, evaluate,
                  Budget(seconds=budget, calls=6, candidates=40, call_seconds=0.1))
    # Two daemon searches use the budget up. The fallback gets nothing left, so
    # it searches nothing and keeps what the daemon found.
    assert time.monotonic() - started < budget + 0.15
    assert len(asked) == 2 and asked[0] <= budget and asked[1] < budget
    assert out["trace"][-2:] == [{"fallback": "Exhausted", "reason": "budget"},
                                 {"fallback_retrieval": "unavailable"}]
    assert [h["heading_path"][0] for h in out["evidence"]] == ["daemon"]


def test_a_slow_cold_index_cannot_hold_the_caller_past_the_budget(monkeypatch):
    import search.daemon as daemon

    built = []

    class Slow:
        def __init__(self, *a):
            built.append(a)

        def refresh(self):
            time.sleep(0.6)

        def search(self, query, k, sources):
            return [hit("late")]

        def close(self):
            pass

    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 0.5 for q in questions}

    monkeypatch.setattr(jev, "ask", lambda *a, **kw: None)
    monkeypatch.setattr(daemon, "Index", Slow)
    started = time.monotonic()
    out = jev.prepare("Question", None, evaluate=evaluate,
                      budget=Budget(seconds=0.2, calls=6, candidates=40, call_seconds=0.05))
    assert time.monotonic() - started < 0.5
    assert out["status"] == "fallback" and out["evidence"] == []
    assert out["trace"] == [{"fallback": "Exhausted", "reason": "budget"}]
    # The abandoned run's build ends late (0.6 s); its fallback then has no time
    # left and must not start a second one. Wait by the clock: waiting on `COLD`
    # would hold it just when that fallback tries, and hide a second build.
    time.sleep(0.8)
    assert len(built) == 1
    assert jev.COLD.acquire(timeout=3)
    jev.COLD.release()


def test_one_cold_build_at_a_time_and_none_without_time(monkeypatch):
    import search.daemon as daemon

    built = []

    class Slow:
        def __init__(self, *a):
            built.append(a)

        def refresh(self):
            time.sleep(0.3)

        def search(self, query, k, sources):
            return [hit("cold")]

        def close(self):
            pass

    monkeypatch.setattr(jev, "ask", lambda *a, **kw: None)
    monkeypatch.setattr(daemon, "Index", Slow)
    assert jev.retrieve("Question", None, ["hub"], 8, 0) is None and built == []
    first = threading.Thread(target=jev.retrieve, args=("Question", None, ["hub"], 8, 1.0))
    first.start()
    time.sleep(0.05)
    assert jev.retrieve("Question", None, ["hub"], 8, 1.0) is None
    first.join()
    assert len(built) == 1
    assert jev.retrieve("Question", None, ["hub"], 8, 1.0) == [hit("cold")]


def test_a_busy_cold_build_is_unavailable_not_insufficient(monkeypatch):
    def evaluate(state, questions, trace, *_):
        if "retrieve" in questions:
            return {"retrieve": 1, "hub": 1}
        return {q: 1 for q in questions}

    monkeypatch.setattr(jev, "ask", lambda *a, **kw: None)
    assert jev.COLD.acquire(timeout=3)
    try:
        out = jev.prepare("Question", None, evaluate=evaluate)
    finally:
        jev.COLD.release()
    assert out["status"] == "fallback" and out["evidence"] == []
    assert out["trace"] == [{"fallback": "RuntimeError", "reason": "retrieval_unavailable"},
                            {"fallback_retrieval": "unavailable"}]


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
