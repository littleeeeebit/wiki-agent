"""Research before another nonconverging fix (`docs/plans/reliability/8-recovery.md`):
the triggers read off counted rounds and server finding ids, the barrier in
`loop.corrected` that sends no correction while research is owed, the bounded
research turn and its committed page, and the stops a person resumes.

The research host is `test_planning.Host` behind `recovery.ChatSession`; the
review loop runs as in `test_loop.py`, whose fixtures are borrowed.
"""
# ruff: noqa: F811 — a borrowed fixture is named again by each test that takes it

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from main import loop, recovery, specs
from test_loop import (CLEANED, GATE, Reviewer, allow, commit, denied, fixed, git, meta, pr_spec, template,  # noqa: F401
                       waited, world)
from test_main import client, no_machine_settings  # noqa: F401 — the fixture is autouse
from test_planning import SOURCE, Host
from test_specs import Worker, fresh_workers  # noqa: F401 — the fixture is autouse

FINDING = "[P1] a.txt:1 — wrong path"
OTHER = "[P1] b.txt:2 — leaks a handle"
LIMITS = {"seconds": 600, "calls": 3, "tokens": 100_000}
FOUND = {"problem": "The loader reads a relative path.", "failed_fixes": ["Round 1 joined the path twice."],
         "hypothesis": "The working directory differs under the gate.", "theory": "Paths resolve against the cwd.",
         "applicability": "a.txt is opened by name.", "counterevidence": [], "alternatives": ["Pin the cwd."],
         "experiment": "Run the gate from another folder.", "limits": "Only one platform checked.",
         "sources": [SOURCE], "claims": [{"id": "C1", "text": "cwd decides", "sources": ["S1"], "kind": "source"}]}


@pytest.fixture(autouse=True)
def fresh_hosts():
    Host.replies, Host.made = [], []


def researched(found=FOUND) -> str:
    return "Researched.\n\n```recovery-research\n" + json.dumps(found) + "\n```"


def item(fid, disposition=None, grade="P1", head=FINDING) -> dict:
    return {"grade": grade, "file": "a.txt", "line": 1, "head": head, "body": "", "id": fid,
            "disposition": disposition}


def rnd(n: int, *items, stale=False) -> dict:
    out = {"n": n, "head": f"{n:040x}", "base": "main", "verdict": "deny", "items": list(items),
           "findings": {g: sum(i["grade"] == g for i in items) for g in ("P0", "P1", "P2")}}
    return {**out, "stale": True} if stale else out


# -- the trigger -----------------------------------------------------------------------


F2 = item("F2", head=OTHER)


@pytest.mark.parametrize("rounds, state, expected", [
    # A-A: the fix said fixed, the next round raises it again.
    ([rnd(1, item("F1", "fixed")), rnd(2, item("F1"))], {}, [("F1", "repeat", [1, 2])]),
    # Disagreeing or not reproducing, with evidence, is not a fix.
    ([rnd(1, item("F1", "disagree")), rnd(2, item("F1"))], {}, []),
    ([rnd(1, item("F1", "not-reproduced")), rnd(2, item("F1"))], {}, []),
    # A-B-A: gone for a round that raised something else, then back.
    ([rnd(1, item("F1", "fixed")), rnd(2, {**F2, "disposition": "fixed"}), rnd(3, item("F1"))], {},
     [("F1", "oscillation", [1, 2, 3])]),
    # Distinct findings that keep improving never trigger.
    ([rnd(1, item("F1", "fixed")), rnd(2, {**F2, "disposition": "fixed"}), rnd(3, item("F3", head="[P1] c:1 — x"))],
     {}, []),
    # Two issues repeating at once: both are owed, in the round's order.
    ([rnd(1, item("F1", "fixed"), {**F2, "disposition": "fixed"}), rnd(2, F2, item("F1"))], {},
     [("F2", "repeat", [1, 2]), ("F1", "repeat", [1, 2])]),
    # P2 is never serious; a finding without an id never matches another.
    ([rnd(1, item("F1", "fixed", grade="P2")), rnd(2, item("F1", grade="P2"))], {}, []),
    ([rnd(1, item(None, "fixed")), rnd(2, item(None))], {}, []),
    # A ruled issue is the person's call now.
    ([rnd(1, item("F1", "fixed")), rnd(2, item("F1"))], {"F1": {"phase": "ruled"}}, []),
    # The round-7 backstop, unidentified findings sharing one issue.
    ([rnd(n, item(f"F{n}", "fixed")) for n in range(1, 7)] + [rnd(7, item(None))], {},
     [("unidentified", "backstop", [7])]),
    ([rnd(n, item(f"F{n}", "fixed")) for n in range(1, 7)] + [rnd(7, item(None))],
     {"unidentified": {"attempts": [{"status": "fixed"}]}}, []),
])
def test_the_triggers_read_counted_rounds_and_server_ids(rounds, state, expected):
    assert [(t["issue"], t["kind"], t["rounds"]) for t in recovery.trigger(rounds, state)] == expected


def test_a_stale_round_is_not_a_round_between():
    spec = {"rounds": [rnd(1, item("F1", "fixed")), rnd(2, item("F1"), stale=True), rnd(2, item("F1"))]}
    assert recovery.trigger(loop.counted(spec), {})[0]["kind"] == "repeat"


def test_limits_are_positive_finite_and_never_assumed():
    assert recovery.limited({"seconds": 1, "calls": 2.0, "tokens": 3}) == {"seconds": 1.0, "calls": 2, "tokens": 3}
    for bad in (None, {}, {**LIMITS, "seconds": 0}, {**LIMITS, "calls": -1}, {**LIMITS, "tokens": float("inf")},
                {**LIMITS, "calls": True}, {**LIMITS, "seconds": "600"}, {**LIMITS, "calls": 0.5},
                {**LIMITS, "tokens": 10.5}):
        with pytest.raises(ValueError):
            recovery.limited(bad)


def test_the_research_block_is_checked(tmp_path):
    assert recovery.read(researched(), tmp_path)["claims"][0]["sources"] == ["S1"]
    for broken, why in (("no block", "블록"), (researched({**FOUND, "hypothesis": " "}), "hypothesis"),
                        (researched({**FOUND, "alternatives": "one"}), "alternatives"),
                        (researched({**FOUND, "claims": []}), "claims"),
                        (researched({**FOUND, "claims": [{**FOUND["claims"][0], "sources": ["S9"]}]}), "출처"),
                        (researched({**FOUND, "claims": [{**FOUND["claims"][0], "kind": "fact"}]}), "kind"),
                        (researched({**FOUND, "sources": [{**SOURCE, "url": "docs/missing.md"}]}), "URL")):
        with pytest.raises(ValueError, match=why):
            recovery.read(broken, tmp_path)


def test_the_page_goes_to_research_or_the_plan_folder():
    assert recovery.page_of({"id": "Fix-X"}, "F1", 2) == "docs/research/review-fix-x-f1-2.md"
    assert recovery.page_of({"id": "p", "planning": {"artifact_root": "docs/plans/p"}}, "unidentified", 1) \
        == "docs/plans/p/research-unidentified-1.md"


def test_the_next_counted_round_closes_a_researched_fix():
    state = {"F1": {**recovery.fresh("F1"), "attempts": [{"status": "fixed", "round": 2, "outcome": None}]}}
    assert recovery.observed({"recovery": state}, rnd(2, item("F1"))) is None, "the fixed round itself is not after it"
    assert recovery.observed({"recovery": state}, rnd(3, item("F1")))["F1"]["attempts"][0]["outcome"]["result"] \
        == "repeated"
    assert recovery.observed({"recovery": state}, rnd(3))["F1"]["attempts"][0]["outcome"]["result"] == "cleared"
    assert state["F1"]["attempts"][0]["outcome"] is None, "the loaded spec is not changed in place"


def test_resume_takes_limits_accepts_unknown_spend_or_records_a_ruling():
    s = recovery.fresh("F1")
    with pytest.raises(ValueError, match="멈춘"):
        recovery.resumed({"recovery": {"F1": s}}, LIMITS, "")
    s["phase"] = "limits"
    with pytest.raises(ValueError):
        recovery.resumed({"recovery": {"F1": s}}, None, "")
    assert recovery.resumed({"recovery": {"F1": s}}, LIMITS, "")["recovery"]["F1"]["phase"] == "required"
    # One stop for two issues without limits: one `[계속]` gives each its own allowance.
    both = recovery.resumed({"recovery": {"F1": s, "F2": {**s, "issue_id": "F2"}}}, LIMITS, "")["recovery"]
    assert both["F1"]["limits"] == both["F2"]["limits"] == LIMITS
    spent = {**s, "phase": "budget", "limits": LIMITS, "spent": {"seconds": 10.0, "calls": 3, "tokens": 50},
             "active": {"cycle": 1, "status": "researching"}}
    with pytest.raises(ValueError, match="이미 쓴"):
        recovery.resumed({"recovery": {"F1": spent}}, LIMITS, "")
    assert recovery.resumed({"recovery": {"F1": spent}}, {**LIMITS, "calls": 5}, "")["recovery"]["F1"]["phase"] \
        == "researching"
    unknown = {**spent, "phase": "unknown", "spent": {**spent["spent"], "unknown": True},
               "active": {"cycle": 1, "status": "researched"}}
    back = recovery.resumed({"recovery": {"F1": unknown}}, None, "")["recovery"]["F1"]
    assert back["spent"]["accepted"] and back["limits"] == LIMITS and back["phase"] == "fixing"
    assert not unknown["spent"].get("accepted"), "the loaded spec is not changed in place"
    out = {**s, "phase": "exhausted"}
    with pytest.raises(ValueError, match="적어야"):
        recovery.resumed({"recovery": {"F1": out}}, None, "")
    fields = recovery.resumed({"recovery": {"F1": out}, "rulings": ["earlier"]}, None, "defer F1 to PR 9")
    assert fields["recovery"]["F1"]["phase"] == "ruled" and fields["rulings"] == ["earlier", "F1: defer F1 to PR 9"]


def test_a_restart_marks_an_inflight_turn_s_spend_unknown():
    s = {**recovery.fresh("F1"), "active": {"cycle": 1, "inflight": True}}
    assert recovery.interrupted({"recovery": {"F1": recovery.fresh("F1")}}) is None
    marked = recovery.interrupted({"recovery": {"F1": s}})["F1"]
    assert marked["spent"]["unknown"] and not marked["active"]["inflight"]


def test_the_stop_reason_is_the_loop_s():
    assert loop.Why.RECOVERY.value == recovery.WHY


# -- the barrier, end to end ------------------------------------------------------------


def test_a_repeated_finding_is_researched_and_committed_before_the_fix(world):
    spec = pr_spec(world, "recur", 7)
    sid, page = spec["id"], "docs/research/review-recur-f1-1.md"
    order = []
    Reviewer.replies = [denied([FINDING], meta(("loader", "reads its file"))),
                        denied([FINDING], meta(("loader", "reads its file", "F1"))), allow]

    def research(text, halt):
        order.append("research")
        assert "Round 2" in text and "Correction then: fixed" in text
        return researched()

    def recorded(path, halt):
        order.append("fix")
        assert git(path, "log", "-1", "--format=%s").startswith("docs: research F1"), "the page is committed first"
        body = (path / page).read_text(encoding="utf-8")
        assert recovery.PENDING in body and "## Hypothesis" in body
        (path / page).write_text(body.replace(recovery.PENDING, "Observed: the gate passes from another folder "
                                              "with test_fix.py at the fix commit."), encoding="utf-8")
        return fixed((FINDING, "fixed"), name="fix-cwd.txt")(path, halt)

    Worker.replies = [fixed((FINDING, "fixed")), recorded]
    Host.replies = [research]
    with patch.object(recovery, "ChatSession", Host):
        loop.kick("proj", sid)
        waited(lambda: not loop._loops)
        stopped = specs.load("proj", sid)
        assert stopped["state"] == "멈춤" and stopped["stopped"]["reason"] == recovery.WHY
        assert stopped["recovery"]["F1"]["phase"] == "limits"
        assert stopped["recovery"]["F1"]["trigger"]["kind"] == "repeat"
        assert sum(len(w.heard) for w in Worker.made) == 1, "no correction went out while research is owed"
        assert not Host.made

        web = client()
        assert web.post(f"/api/specs/{sid}/resume", json={"limits": {**LIMITS, "tokens": 0}}).status_code == 400
        web.post(f"/api/specs/{sid}/resume", json={"limits": LIMITS}).raise_for_status()
        waited(lambda: not loop._loops)
    done = specs.load("proj", sid)
    assert done["state"] == "머지 가능" and len(done["rounds"]) == 3, done["stopped"]
    assert order == ["research", "fix"]
    assert Host.made[0].tools == recovery.TOOLS
    assert "`F1` (repeat, rounds [1, 2]), cycle 1 of 2" in Worker.made[-1].heard[-1]
    assert page in git(world.origin, "diff", "--name-only", "main", sid).splitlines(), "the page is in the fix PR"
    s = done["recovery"]["F1"]
    assert s["phase"] == "fixed" and s["cycles_started"] == 1 and s["active"] is None
    assert s["attempts"][0]["outcome"]["result"] == "cleared" and s["attempts"][0]["page"] == page
    assert s["spent"]["calls"] == 1 and s["spent"]["tokens"] == 15 and not s["spent"]["unknown"]


# -- the stops, against a spec the loop left mid-correction ------------------------------


def correcting(w, name: str, recovery_state: dict, rounds=None) -> tuple[SimpleNamespace, dict]:
    spec = pr_spec(w, name, 7)
    spec.update(state="고치는 중 R2", recovery=recovery_state,
                rounds=rounds or [rnd(1, item("F1", "fixed")), rnd(2, item("F1"))])
    specs.save(spec)
    return SimpleNamespace(repo="proj", sid=name, halt=threading.Event(), run=None), spec


def prepared(lp, spec) -> tuple[dict | None, dict]:
    with patch.object(recovery, "ChatSession", Host):
        got = recovery.prepared(lp, spec, Path(spec["worktree"]))
    return got, specs.load("proj", lp.sid)


def test_two_cycles_then_the_person_decides(world):
    lp, spec = correcting(world, "spent", {"F1": {**recovery.fresh("F1"), "cycles_started": 2, "limits": LIMITS}})
    got, after = prepared(lp, spec)
    assert got is None and after["stopped"]["reason"] == recovery.WHY and "2번" in after["stopped"]["detail"]
    assert after["recovery"]["F1"]["phase"] == "exhausted" and after["recovery"]["F1"]["cycles_started"] == 2
    assert not Host.made, "no third cycle"


def test_a_cut_turn_s_spend_is_unknown_until_the_person_accepts_it(world):
    active = {"cycle": 1, "round": 2, "head": rnd(2)["head"], "status": "researching", "inflight": True}
    lp, spec = correcting(world, "cut", {"F1": {**recovery.fresh("F1"), "cycles_started": 1, "limits": LIMITS,
                                                "active": active, "phase": "researching"}})
    got, after = prepared(lp, spec)
    assert got is None and after["recovery"]["F1"]["phase"] == "unknown"
    assert after["recovery"]["F1"]["spent"]["unknown"] and after["recovery"]["F1"]["cycles_started"] == 1
    assert not Host.made, "an unknown spend is never counted as zero"


def cut_turn(text, halt):
    halt.set()   # `/api/work/stop` on the research turn, after its answer was complete
    return researched()


@pytest.mark.parametrize("reply, phase", [((researched(), False, {"in": 1, "out": 1}), "web_unavailable"),
                                          (("no block", True, {"in": 1, "out": 1}), "budget"),
                                          (cut_turn, "host"),
                                          ((researched(), True, {"in": 1, "out": 1}), "page")])
def test_failed_research_never_falls_through_to_a_fix(world, reply, phase):
    limits = {**LIMITS, "calls": 1}
    lp, spec = correcting(world, f"fail-{phase.replace('_', '-')}", {"F1": {**recovery.fresh("F1"), "limits": limits}})
    Host.replies = [reply]
    if phase == "page":
        with patch.object(recovery.planning, "committed", side_effect=RuntimeError("commit 실패")):
            got, after = prepared(lp, spec)
    else:
        got, after = prepared(lp, spec)
    s = after["recovery"]["F1"]
    assert got is None and s["phase"] == phase and after["stopped"]["reason"] == recovery.WHY
    assert s["cycles_started"] == 1 and s["spent"]["calls"] == 1
    assert git(Path(spec["worktree"]), "rev-parse", "HEAD") == spec["pr"]["head"], "nothing was committed"
    assert not Worker.made


@pytest.mark.parametrize("usage, phase", [(None, "unknown"), ({"in": 900, "out": 200}, "budget")])
def test_a_good_answer_past_its_spend_keeps_its_page_and_stops_before_the_fix(world, usage, phase):
    lp, spec = correcting(world, f"over-{phase}", {"F1": {**recovery.fresh("F1"), "limits": {**LIMITS, "tokens": 1000}}})
    Host.replies = [(researched(), True, usage)]
    got, after = prepared(lp, spec)
    s = after["recovery"]["F1"]
    assert got is None and s["phase"] == phase and s["active"]["status"] == "researched"
    assert git(Path(spec["worktree"]), "log", "-1", "--format=%s").startswith("docs: research F1"), "the page stays"
    fields = recovery.resumed(after, {**LIMITS, "tokens": 2000} if phase == "budget" else None, "")
    specs.update("proj", lp.sid, **fields, state="고치는 중 R2")
    assert fields["recovery"]["F1"]["phase"] == "fixing"
    got, _ = prepared(lp, specs.load("proj", lp.sid))
    assert [s["issue_id"] for s in got] == ["F1"] and len(Host.made) == 1, "the page is not researched again"


def test_every_owed_issue_is_researched_before_the_one_fix(world):
    rounds = [rnd(1, item("F1", "fixed"), {**F2, "disposition": "fixed"}), rnd(2, item("F1"), F2)]
    lp, spec = correcting(world, "two", {}, rounds=rounds)
    got, after = prepared(lp, spec)
    assert got is None and {k: s["phase"] for k, s in after["recovery"].items()} == {"F1": "limits", "F2": "limits"}
    assert after["stopped"]["detail"].startswith("F1, F2 · "), "one stop names both"
    specs.update("proj", "two", **recovery.resumed(after, LIMITS, ""), state="고치는 중 R2")
    Host.replies = [researched(), researched()]
    got, after = prepared(lp, specs.load("proj", "two"))
    assert [s["issue_id"] for s in got] == ["F1", "F2"] and len(Host.made) == 2
    path = Path(spec["worktree"])
    assert git(path, "log", "-2", "--format=%s").splitlines() == [
        "docs: research F2 before fixing it again (cycle 1)", "docs: research F1 before fixing it again (cycle 1)"]
    text = recovery.briefed(after, got)
    assert "`F1` (repeat" in text and "`F2` (repeat" in text
    state = recovery.finished(after, got, path)
    assert all(state[k]["active"] is None and state[k]["attempts"][-1]["status"] == "fixed" for k in ("F1", "F2"))


def test_a_plan_spec_s_research_lands_only_in_its_artifact_root(world):
    lp, spec = correcting(world, "plan-x", {"F1": {**recovery.fresh("F1"), "limits": LIMITS}})
    spec = specs.update("proj", "plan-x", planning={"artifact_root": "docs/plans/plan-x"})
    Host.replies = [researched()]
    got, after = prepared(lp, spec)
    path, page = Path(spec["worktree"]), got[0]["active"]["page"]
    assert page == "docs/plans/plan-x/research-f1-1.md" and got[0]["phase"] == "fixing"
    assert git(path, "show", "--name-only", "--format=", "HEAD").splitlines() == [page]
    assert "plan-file" in recovery.briefed(after, got), "the reviser returns the page; nothing else writes it"
    # The reviser's write boundary takes the page, and nothing beside or above it.
    assert recovery.planning.target(path, "docs/plans/plan-x", page) == (path / page).resolve()
    for outside in ("docs/plans/plan-x/notes.md", "docs/research/research-f1-1.md"):
        with pytest.raises(ValueError):
            recovery.planning.target(path, "docs/plans/plan-x", outside)


def test_the_fix_must_record_the_observed_result(world):
    lp, spec = correcting(world, "noted", {})
    path = Path(spec["worktree"])
    s = [{"active": {"page": "docs/research/x.md"}}]
    assert "없다" in recovery.unrecorded(path, s)
    (path / "docs/research").mkdir(parents=True)
    commit(path, "docs/research/x.md", f"# x\n\n## Result\n\n{recovery.PENDING}\n\n## Unresolved limits\n\nnone\n")
    assert "Result" in recovery.unrecorded(path, s)
    commit(path, "docs/research/x.md", "# x\n\n## Result\n\nObserved: passes.\n\n## Unresolved limits\n\nnone\n")
    assert recovery.unrecorded(path, s) == ""
