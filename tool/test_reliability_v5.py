"""Reliability PR 5's fifth set (`docs/plans/reliability/5-english-baseline.md`, v5): enough fresh held-out
cases for every rate gate to decide with one miss, and the per-class routing gates that replace the pooled
ones. No network."""

import json
from collections import Counter
from types import SimpleNamespace
from pathlib import Path

from eval import compare, dataset, report

JEV = Path(__file__).resolve().parents[1] / "eval" / "jev"
V5 = JEV / "reliability-v5"


def strata(data):
    """The v5 routing cohorts: analysis, fact, with a material segment, and request-only multipart."""

    held = [i for i in data["intents"] if i["split"] == "held_out" and i["route_expected"]]
    asks = {i["id"]: [s["ask"] for s in i["route_segments"]] for i in held}
    return {"analysis": [i for i in held if i["analysis"]], "fact": [i for i in held if not i["analysis"]],
            "material": [i for i in held if not all(asks[i["id"]])],
            "request": [i for i in held if all(asks[i["id"]]) and len(asks[i["id"]]) > 1]}


def test_the_fifth_set_holds_out_enough_fresh_cases_for_every_rate_gate():
    # The 2026-10-10 re-judgment left routing and the action gates inconclusive on too few cases.
    data = dataset.load(V5 / "intents.json")
    assert dataset.invalid(data) == [] and dataset.unresolved(data) == [] and dataset.frozen(data) == []
    earlier = [dataset.load(JEV / f"reliability{v}" / "intents.json") for v in ("", "-v2", "-v3", "-v4")]
    assert [i for i in data["intents"] if i["split"] == "calibration"] == \
        [i for i in earlier[0]["intents"] if i["split"] == "calibration"]
    held = [i for i in data["intents"] if i["split"] == "held_out"]
    assert all(i["review"] == "r5" for i in held)
    assert not {i["variants"]["en"] for i in held} & {i["variants"]["en"] for d in earlier for i in d["intents"]}
    assert not {i["id"] for i in held} & {i["id"] for d in earlier for i in d["intents"]}
    old_pages, new_pages = dataset.pages(earlier[-1]), dataset.pages(data)
    assert {n: new_pages.get(n) for n in old_pages} == old_pages
    # One miss each still clears a 0.90 Wilson lower bound at 53.
    assert all(len(c) >= 54 for c in strata(data).values())
    assert sorted(i["fault"]["kind"] for i in held if i.get("fault")) == \
        ["cancelled"] * 2 + ["exhausted"] * 2 + ["unavailable"] * 2
    fixtures = json.loads((V5 / "actions.json").read_text(encoding="utf-8"))
    old_ids = {f["id"] for d in ("reliability", "reliability-v2")
               for f in json.loads((JEV / d / "actions.json").read_text(encoding="utf-8"))["fixtures"]}
    assert not {f["id"] for f in fixtures["fixtures"]} & old_ids
    for point, held_out in (("work.start", 54), ("specs.check", 55), ("loop.fix", 54)):
        mine = [f for f in fixtures["fixtures"] if f["point"] == point]
        assert sorted(f["split"] for f in mine) == ["calibration"] * 12 + ["held_out"] * held_out
        labels = Counter(f["label"] for f in mine if f["split"] == "held_out")
        assert len(set(labels.values())) == 1, labels
    for f in fixtures["fixtures"]:
        assert f["label"] in [c["id"] for c in compare.offered_and_state(f)[0]], f["id"]
        assert fixtures["labels"]["reviews"][f["review"]]
    # v4's gates but routing, which four per-class gates replace at the same target.
    gates = json.loads((V5 / "gates.json").read_text(encoding="utf-8"))
    v4 = json.loads((JEV / "reliability-v4" / "gates.json").read_text(encoding="utf-8"))
    assert gates["copied_from"]["sha256"] == dataset.sha((JEV / "reliability-v4" / "gates.json").read_bytes())
    assert [g for g in gates["gates"] if g.get("cohort") != "routing"] == \
        [g for g in v4["gates"] if g.get("cohort") != "routing"]
    assert {g["id"]: g["target"] for g in gates["gates"] if g.get("cohort") == "routing"} == dict.fromkeys(
        ("analysis_recall", "fact_specificity", "material_classification", "request_classification"), 0.90)


def test_per_class_routing_gates_fail_what_a_pooled_rate_would_pass():
    data = dataset.load(V5 / "intents.json")
    gates = json.loads((V5 / "gates.json").read_text(encoding="utf-8"))
    held = [i for i in data["intents"] if i["split"] == "held_out" and i["route_expected"]]

    def verdicts(spoil=lambda i, r: r):
        rows = [spoil(i, {"key": f"{i['id']}:en:{arm}:0", "intent": i["id"], "arm": arm, "rep": 0,
                          "question_en": i["variants"]["en"], "analysis": i["analysis"],
                          "route_segments": i["route_segments"], "transitions": ["route", i["transitions"][0]]})
                for i in held for arm in "BD"]
        found = {"routing": report.routing_report(rows, data, gates["cohorts"]["routing"], gates)}
        return {g["id"]: g["verdict"] for g in report.judge(gates, found, True) if g["id"] in found["routing"]}

    assert set(verdicts().values()) == {"pass"}
    # A router that calls everything fact: fact specificity passes, analysis recall fails.
    fact = verdicts(lambda i, r: {**r, "analysis": False})
    assert fact["analysis_recall"] == "fail" and fact["fact_specificity"] == "pass"

    # Dropping the first request of every request-only multipart query leaves the other three passing.
    def dropped(i, r):
        if len(i["route_segments"]) > 1 and all(s["ask"] for s in i["route_segments"]):
            return {**r, "route_segments": [{**i["route_segments"][0], "ask": False}, *i["route_segments"][1:]]}
        return r

    lost = verdicts(dropped)
    assert lost["request_classification"] == "fail"
    assert {k: v for k, v in lost.items() if k != "request_classification"} == dict.fromkeys(
        ("analysis_recall", "fact_specificity", "material_classification"), "pass")


def test_the_grader_reads_what_the_answering_host_read_beside_the_dossier(monkeypatch):
    """Since #59 the answering host may read the repository with its own tools. A claim resting on a page it
    read, outside the retrieved dossier, is graded against that page, not as unsupported."""

    import agent
    from agent import ChatSession, Event

    claim = "The third quarter had four incidents; three were caused by configuration changes."
    page = "x" * 4100 + "\n" + claim   # past the 4000 characters a tool event shows
    # Each provider's own events, as the session reads them: the result is kept whole, a preview is not kept.
    events = {"": [{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r1",
                                                             "content": [{"type": "text", "text": page}]}]}},
                   {"type": "result", "result": claim}],
              "codex:test": [{"type": "item.started", "item": {"id": "r1", "type": "command_execution",
                                                                "command": "cat incidents-q3.md"}},
                             {"type": "item.completed", "item": {"id": "r1", "type": "command_execution",
                                                                 "aggregated_output": page, "exit_code": 0}},
                             {"type": "item.completed", "item": {"id": "r2", "type": "mcp_tool_call",
                                                                 "result": {"content": [{"type": "text",
                                                                                         "text": claim}]}}},
                             {"type": "item.completed", "item": {"type": "agent_message", "text": claim}},
                             {"type": "turn.completed", "usage": {}}]}
    for model, sent_events in events.items():
        read: list[str] = []
        chat = ChatSession(Path.cwd(), model=model or None, isolated=True, results=read)
        try:
            for ev in sent_events:
                chat._events.put(ev)
            assert compare.host_turn(SimpleNamespace(say=lambda _m: chat._drain()), "q")[0] == claim
        finally:
            chat.close()
        assert read == ([page] if not model else [page, claim]), model

    made = {}
    monkeypatch.setattr(agent, "ChatSession", lambda *a, **k: made.update(k) or SimpleNamespace(
        say=lambda _m: iter([Event("done", claim, {})]), close=lambda: None))
    assert compare.answered({"text": "q"}, {"evidence": []}, Path.cwd(), False, None, "")["read"] is made["results"]

    sent = {}

    def oneshot(prompt, payload, model):
        sent.update(payload)
        yield Event("done", '{"parts": {}, "claims": [], "abstained": false, "forbidden": []}', {})

    monkeypatch.setattr(agent, "oneshot", oneshot)
    unit = {"text": "q", "intent": {"parts": [], "forbidden": [], "abstain": False, "evidence": []}}
    compare.graded(unit, {"evidence": []}, {"text": claim, "read": [page]}, {}, "")
    assert sent["read_passages"] == [page] and sent["retrieved_passages"] == []
