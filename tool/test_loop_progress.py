"""Review progress, cancellation before dispatch and external ownership."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name

import json
import re
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import Event
from main import loop, specs, sweep, verification, work
from test_loop import (  # noqa: F401 -- shared fixtures and isolated Git/GitHub world
    Reviewer, Worker, allow, client, deny, git, no_machine_settings, pr_spec, template, waited, world,
    looped, mapped, allowed_spec, round_of, FINAL,
)
from workspace import remove


@pytest.mark.parametrize("fails", [1, 2])
def test_a_final_failure_fixed_outside_the_code_runs_again_without_a_commit(world, fails):
    """rfg_rag #73: the worktree had no `.venv`, so the final gate could not start. The
    work cell linked one and had nothing to commit; every continue read the `failed`
    receipt back. A repair with no commit reruns once; a continue after that, once more."""

    web = client()
    spec = allowed_spec(world, "fix-e", 7)
    specs.validate("proj", "fix-e", round=round_of(world, spec))
    real, calls, verdict, reasons = specs.judge, [], specs.verify.verdict, []

    def unstartable(path, cmds, halt, noted=lambda text: None, **kwargs):
        calls.append(list(cmds))
        if len(calls) <= fails:
            return {"ok": False, "code": 1, "reason": "게이트가 1 로 끝났다", "tail": "지정된 경로를 찾을 수 없습니다.",
                    "cmd": cmds[0], "head": spec["pr"]["head"]}
        return real(path, cmds, halt, noted, **kwargs)

    def judged(*args, **kwargs):
        reasons.append(kwargs["retry_reason"])
        return verdict(*args, **kwargs)

    # #185 round 1: a local-only retry reason would refuse a CI rerun; the continue rides `again` instead.
    with patch.object(specs, "judge", unstartable), patch.object(specs.verify, "verdict", judged):
        assert web.post("/api/specs/fix-e/merge", json={"head": spec["pr"]["head"]}).status_code == 409
        waited(lambda: ("proj", "fix-e") not in loop._loops)
        if fails == 2:   # the repair's own rerun failed too: a person's continue runs it once more
            stopped = specs.load("proj", "fix-e")
            assert stopped["stopped"]["reason"] == loop.Why.GATE.value and len(calls) == 2
            web.post("/api/specs/fix-e/resume", json={}).raise_for_status()
            waited(lambda: ("proj", "fix-e") not in loop._loops)
    spec = specs.load("proj", "fix-e")
    assert spec["state"] == "머지 가능" and spec["validation"]["final"]["ok"] and calls == [[FINAL]] * (fails + 1)
    assert len([w for w in Worker.made if w.heard]) >= 1, "the failure went to the work cell first"
    assert reasons == [""] * (fails + 1)


def test_a_recovery_never_runs_a_failed_receipt_its_spec_had_not_recorded(world):
    """#185 round 2: the receipt said `failed`, then the server died before the spec
    recorded it. A restart's recovery reads that failure back; only a continue runs it."""

    web = client()
    spec = allowed_spec(world, "fix-r", 7)
    specs.validate("proj", "fix-r", round=round_of(world, spec))
    calls = []

    def fails(path, cmds, halt, noted=lambda text: None, **kwargs):
        calls.append(list(cmds))
        return {"ok": False, "code": 1, "reason": "게이트가 1 로 끝났다", "tail": "E", "cmd": cmds[0], "head": spec["pr"]["head"]}

    with patch.object(specs, "judge", fails):
        assert web.post("/api/specs/fix-r/merge", json={"head": spec["pr"]["head"]}).status_code == 409
        waited(lambda: ("proj", "fix-r") not in loop._loops)
        assert calls == [[FINAL]] * 2, "its own run and the repair's rerun"
        final = specs.load("proj", "fix-r")["validation"]["final"]
        specs.validate("proj", "fix-r", phase="final_running", final={**final, "finished_at": None})   # the save a crash cut
        specs.update("proj", "fix-r", state="리뷰 R1", stopped=None)
        assert loop.recover() == [("proj", "fix-r")]
        loop.kick("proj", "fix-r", automatic=True)
        waited(lambda: ("proj", "fix-r") not in loop._loops)
    assert specs.load("proj", "fix-r")["state"] == "멈춤" and calls == [[FINAL]] * 2


@pytest.mark.parametrize("after", ["resume", "recover"])
def test_continue_runs_a_final_gate_that_ran_out_of_time_once_more(world, after):
    """rfg_rag #72 r4: a final gate cut at its time limit stopped the loop, and
    every continue (`/resume`) read the cut-short receipt back instead of running it again.
    A restart's recovery goes on too; any other automatic start does not run it."""

    web = client()
    spec = allowed_spec(world, "fix-t", 7)
    specs.validate("proj", "fix-t", round=round_of(world, spec))
    real, calls = specs.judge, []

    def cut_once(path, cmds, halt, noted=lambda text: None, **kwargs):
        calls.append(list(cmds))
        if len(calls) == 1:
            return {"ok": False, "code": None, "reason": "검증 실행 시간 제한을 넘었다", "tail": "[  9%]", "cmd": cmds[0]}
        return real(path, cmds, halt, noted, **kwargs)

    with patch.object(specs, "judge", cut_once):
        assert web.post("/api/specs/fix-t/merge", json={"head": spec["pr"]["head"]}).status_code == 409
        waited(lambda: ("proj", "fix-t") not in loop._loops)
        stopped = specs.load("proj", "fix-t")
        assert stopped["state"] == "멈춤" and "시간 제한" in stopped["stopped"]["detail"] and calls == [[FINAL]]
        loop.kick("proj", "fix-t", automatic=True)   # no continue stamped: the receipt stands
        waited(lambda: ("proj", "fix-t") not in loop._loops)
        assert specs.load("proj", "fix-t")["state"] == "멈춤" and calls == [[FINAL]]
        if after == "resume":
            web.post("/api/specs/fix-t/resume", json={}).raise_for_status()
        else:
            specs.update("proj", "fix-t", state="리뷰 R1", stopped=None)   # the loop a restart cut
            assert loop.recover() == [("proj", "fix-t")]
            loop.kick("proj", "fix-t", automatic=True)
        waited(lambda: ("proj", "fix-t") not in loop._loops)
    spec = specs.load("proj", "fix-t")
    assert spec["state"] == "머지 가능" and spec["validation"]["final"]["ok"] and calls == [[FINAL], [FINAL]]
    receipt = json.loads(next((world.repo / ".git").glob("wiki-verification-*.json")).read_text(encoding="utf-8"))
    assert receipt["history"][0]["status"] == "incomplete" and receipt["status"] == "passed"


def test_a_pr_adopted_from_github_repairs_on_the_work_model_the_screen_chose(world):
    """rfg_rag #74-#76: a spec made from a GitHub PR named no work model, so its repair
    turns ran on Claude's default while the screen said `gpt-6.1-sol`. The loop button
    sends the choice; it fills a spec that names none and never replaces one that does."""

    git(world.repo, "push", "-q", "origin", "main:refs/heads/feat/model")
    world.hub.open(33, "feat/model")
    world.hub.push_elsewhere(33, "model.txt")
    Reviewer.replies = [deny("[P1] model.txt:1 — wrong")]
    chosen = {"model": "claude-chosen", "effort": "high", "fast": False}
    result = client().post("/api/loops", json={"prs": [33], "cell": chosen}).json()["results"][0]
    waited(lambda: not loop._loops)
    spec = specs.load("proj", result["id"])
    assert spec["cell"] == chosen and Worker.made and Worker.made[0].model == "claude-chosen"
    specs.update("proj", spec["id"], state="멈춤")
    again = client().post("/api/loops", json={"prs": [33], "cell": {"model": "claude-other", "effort": ""}})
    assert again.json()["results"][0].get("id") == spec["id"], again.text
    waited(lambda: not loop._loops)
    assert specs.load("proj", spec["id"])["cell"] == chosen
    # #191 round 1: a continue (`/resume`) on a spec that names none sends the choice too.
    blank = {"model": "", "effort": "", "fast": False}
    specs.update("proj", spec["id"], cell=blank, state="멈춤", stopped={"reason": loop.Why.PERSON.value, "detail": ""})
    with patch.object(loop, "proceed", lambda repo, spec, note, limits=None: spec):
        assert client().post(f"/api/specs/{spec['id']}/resume", json={"cell": chosen}).json()["cell"] == chosen


def test_a_review_still_reuses_the_checkout_that_holds_its_branch(world):
    """#188 round 2: the selected checkout on the PR branch is reused as before, not
    refused by the hold the review itself took on it."""

    git(world.repo, "push", "-q", "origin", "main:refs/heads/feat/here")
    world.hub.open(32, "feat/here")
    head = world.hub.push_elsewhere(32, "here.txt")
    git(world.repo, "fetch", "-q", "origin", "feat/here:feat/here")
    git(world.repo, "checkout", "-q", "feat/here")
    result = client().post("/api/loops", json={"prs": [32]}).json()["results"][0]
    waited(lambda: not loop._loops)
    assert specs.load("proj", result["id"])["worktree"] == str(world.repo) and git(world.repo, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("how", ["clean", "draft", "hidden draft", "turn", "spec"])
def test_a_review_takes_a_branch_another_worktree_holds_when_it_can_let_go(world, monkeypatch, how):
    """rfg_rag #75: an Orca workspace that published the pull request still had its branch
    checked out, and `git worktree add` refused it. Clean at the head, it lets go on that
    commit. With work of its own — a draft `status` settings hide too, a running turn, a
    spec that owns it (#188 round 1) — it keeps the branch and the reason says where."""

    git(world.repo, "push", "-q", "origin", "main:refs/heads/feat/usage")
    world.hub.open(31, "feat/usage")
    head = world.hub.push_elsewhere(31, "usage.txt")
    git(world.repo, "fetch", "-q", "origin", "feat/usage:feat/usage")
    elsewhere = world.tmp / "orca-barracuda"
    git(world.repo, "worktree", "add", "-q", str(elsewhere), "feat/usage")
    if "draft" in how:
        (elsewhere / "draft.txt").write_text("unsaved", encoding="utf-8")
    if how == "hidden draft":
        git(elsewhere, "config", "status.showUntrackedFiles", "no")
    if how == "turn":
        work._busy[str(elsewhere.resolve())] = object()
    if how == "spec":
        listing = specs.listing
        monkeypatch.setattr(specs, "listing", lambda name: [*listing(name), {"id": "other", "worktree": str(elsewhere),
                                                                         "pr": {"number": 9, "branch": "other"}}])
    try:
        result = client().post("/api/loops", json={"prs": [31]}).json()["results"][0]
    finally:
        work._busy.pop(str(elsewhere.resolve()), None)
    if how != "clean":
        assert str(elsewhere.resolve()) in result["error"]
        assert git(elsewhere, "branch", "--show-current") == "feat/usage"
        return
    waited(lambda: not loop._loops)
    spec = specs.load("proj", result["id"])
    assert git(Path(spec["worktree"]), "branch", "--show-current") == "feat/usage"
    assert git(Path(spec["worktree"]), "rev-parse", "HEAD") == head == git(elsewhere, "rev-parse", "HEAD")
    assert not git(elsewhere, "branch", "--show-current") and (elsewhere / "usage.txt").is_file()


@pytest.mark.parametrize("error", [specs.verify.Pending, RuntimeError])
def test_final_preparation_trouble_stops_without_another_test_run_or_worker(world, monkeypatch, error):
    mapped(world, "*")
    pr_spec(world, "final-preparation", 7, gate=None)
    real = specs.judge
    calls = []

    def judge(path, commands, halt, *args, **kwargs):
        calls.append(commands)
        assert commands == ["git --version"], "Preparation trouble must not start the whole gate"
        return real(path, commands, halt, *args, **kwargs)

    def unavailable(*_args, **_kwargs):
        raise error("CI connection unavailable; existing owner retained")

    monkeypatch.setattr(specs, "judge", judge)
    monkeypatch.setattr(specs.verify, "final", unavailable)
    result = looped("final-preparation")
    assert result["state"] == "멈춤" and result["stopped"]["reason"] == loop.Why.PREPARATION.value
    assert result["validation"]["final"]["pending"] and not result["validation"]["final"]["ok"]
    assert calls == [["git --version"]] and not Worker.made


class Hangs:
    """A work cell whose turn runs until it is stopped."""

    id, parent_id, is_codex = "h", None, False

    def __init__(self):
        self.go = threading.Event()
        self.sent = []   # what reached the CLI: said with no halt set

    def say(self, text, halt=None):
        # As `ChatSession._say`: a halt set before the send stops the turn unsent.
        if halt is not None and halt.is_set():
            yield Event("error", "멈췄다.", {}, self.id)
            return
        self.sent.append(text)
        yield Event("tool", "x", {}, self.id)
        self.go.wait(10)
        yield Event("error", "프로세스가 닫혔다.", {}, self.id)

    def stop(self, halt):
        self.go.set()


@pytest.mark.parametrize("where", ["session", "begin"])
def test_a_stopped_loop_sends_no_turn(tmp_path, where):
    """The loop holds the worktree when the stop lands — before its run
    exists, or once the run is there and its thread not started. Either way
    nothing reaches the CLI."""

    spot, cell = loop.Loop("proj", "t1"), Hangs()
    real = work.begin

    def session(*args):
        if where == "session":
            spot.stop()
        return cell

    def begin(*args, **kwargs):
        if where == "begin":
            spot.stop()
        return real(*args, **kwargs)

    path = tmp_path / "proj-worktrees" / "t1"
    with patch.object(work, "session", session), patch.object(work, "begin", begin):
        assert loop.told(spot, {}, path, "x") is None
    assert cell.sent == [] and str(path) not in work._busy


def test_review_progress_has_its_own_reconnectable_stream_and_record(world):
    original = pr_spec(world, "visible-review", 1)
    started, finish = threading.Event(), threading.Event()

    def say(chat, text, halt=None):
        named = re.search(r"`([^`]+round-\d+\.md)`", text)
        first = Path(named[1]).read_text(encoding="utf-8").splitlines()[0]
        yield Event("delta", "Reading the diff.")
        yield Event("progress", "Reading the diff.\nChecking the behavior.")
        yield Event("tool", "git diff", {"tool": "commandExecution"})
        started.set()
        assert finish.wait(120)
        yield Event("done", allow(first), {"session_id": "independent-review", "model": "codex:test"})

    with patch.object(Reviewer, "say", say):
        loop.kick("proj", original["id"])
        try:
            assert started.wait(120)
            found = client().get("/api/specs/visible-review/review/log").json()
            assert found["running"] and not found["rows"]
            assert specs.load("proj", original["id"])["state"] == "리뷰 R1"
            assert work.recall(Path(original["worktree"])) == []
            assert client().post("/api/work/say", json={"path": original["worktree"], "text": "change it"}).status_code == 409
        finally:
            finish.set()
        waited(lambda: ("proj", original["id"]) not in loop._loops)
    response = client().get("/api/specs/visible-review/review/events", params={"turn": found["running"]["turn"]})
    assert response.status_code == 200 and '"kind": "progress"' in response.text
    assert 'Checking the behavior.' in response.text
    record = client().get("/api/specs/visible-review/review/log").json()
    assert record["running"] is None and record["rows"][0]["steps"][0]["kind"] == "progress"
    assert record["rows"][0]["steps"][1]["command"] is True
    assert client().get("/api/specs/visible-review/review/log", headers={"X-Project": "other"}).status_code == 409


def test_external_implementer_keeps_its_branch_and_can_add_remote_review_rounds(world):
    original = pr_spec(world, "remote-review", 1)
    remove(world.repo, Path(original["worktree"]))
    specs.file_of("proj", original["id"]).unlink()
    git(world.repo, "checkout", "remote-review")
    local_head = git(world.repo, "rev-parse", "HEAD")
    remote_head = world.hub.push_elsewhere(1)
    Reviewer.replies = [deny("[P1] remote-review.txt:1 — Missing behavior")]
    response = client().post("/api/loops", json={"prs": [1], "implementation_environment": "external"})
    assert response.status_code == 200 and "error" not in response.json()["results"][0]
    waited(lambda: ("proj", original["id"]) not in loop._loops)
    first = specs.load("proj", original["id"])
    assert first["stopped"]["reason"] == "외부 수정 대기" and not Worker.made
    assert git(Path(first["worktree"]), "rev-parse", "HEAD") == remote_head
    assert not git(Path(first["worktree"]), "branch", "--show-current")
    assert git(world.repo, "rev-parse", "HEAD") == local_head
    corrected = world.hub.push_elsewhere(1)
    client().post(f"/api/specs/{original['id']}/resume", json={}).raise_for_status()
    waited(lambda: ("proj", original["id"]) not in loop._loops)
    second = specs.load("proj", original["id"])
    assert second["state"] == "머지 가능" and second["rounds"][-1]["head"] == corrected
    assert not Worker.made and git(world.repo, "rev-parse", "HEAD") == local_head
    assert loop.refusal({}, second) == ""
    client().post(f"/api/specs/{original['id']}/review").raise_for_status()
    waited(lambda: ("proj", original["id"]) not in loop._loops)
    third = specs.load("proj", original["id"])
    assert len(third["rounds"]) == 3 and third["rounds"][-1]["head"] == corrected
    assert third["state"] == "머지 가능" and not Worker.made


def test_an_answered_escalation_ends_the_step_unless_the_turn_was_stopped(tmp_path):
    """`handoff.applied` leaves the author's answer owed; the turn that escalated ends the step, and a new one
    sends it (`handed`). A work pane's Stop on that turn stops the loop with the answer still owed."""
    store = {"rev": 2, "handoff_owed": None}
    halted, reporting = [], []

    def begin(path, chat, text, release, run):
        store["handoff_owed"] = {"kind": "spec-update", "rev": 2}   # this turn's check applied the author's revision
        if reporting:   # this turn saves a done-report of its own
            store["report_at"] = {"rev": 2, "turn": run.turn, "head": "h"}
        run.put({"kind": "done", "text": "escalated", "meta": {}, "session_id": "h", "parent_id": None})
        if halted:
            run.halt.set()
        release()
        run.finish()

    where = tmp_path / "proj-worktrees" / "t1"
    with patch.object(work, "session", lambda *a: Hangs()), patch.object(work, "begin", begin), \
            patch.object(specs, "load", lambda *a: dict(store)), patch.object(loop, "stop", return_value=False) as stop:
        with pytest.raises(loop.Revised):
            loop.turn(loop.Loop("proj", "t1"), {}, where, "fix R1")
        # An answer owed from before is not this turn's: a cleanup turn sent ahead of it finishes. A done-report
        # it saved never carried the answer, so it becomes the baseline the delivery's report is measured from.
        # The owner's report from their own turn, saved while the loop was stopped, is not moved: it still counts.
        store["report_at"] = {"rev": 2, "turn": "owner", "head": "h"}
        with patch.object(loop, "own", side_effect=lambda lp, **f: store.update(f) or dict(store)):
            assert loop.turn(loop.Loop("proj", "t1"), {}, where, "cleanup") == "escalated" and not stop.called
            assert store["handoff_owed"] == {"kind": "spec-update", "rev": 2}
            reporting.append(True)
            assert loop.turn(loop.Loop("proj", "t1"), {}, where, "cleanup") == "escalated" and not stop.called
        assert store["handoff_owed"] == {"kind": "spec-update", "rev": 2, "report_at": store["report_at"]}
        assert store["report_at"]["turn"] not in (None, "owner")
        reporting.clear()
        store.update(handoff_owed={"kind": "spec-update", "rev": 2}, report_at=None)
        # A newer answer than the one being delivered: its text is stale before it is sent.
        sending = loop.Loop("proj", "t1")
        sending.handing = {"rev": 2, "owed": {"kind": "guidance", "rev": 2, "text": "older"}, "turns": []}
        with pytest.raises(loop.Revised):
            loop.turn(sending, {}, where, "the older answer")
        halted.append(True)
        store["handoff_owed"] = None
        # Any turn the work pane stopped stops the loop: no answer of it is judged or retried. It is raised, so
        # a repair that committed code keeps its cleanup baseline (`sweep.held`).
        for _ in range(2):
            with pytest.raises(loop.Halted):
                loop.turn(loop.Loop("proj", "t1"), {}, where, "fix R1")
        assert not stop.called and store["handoff_owed"]
        with patch.object(loop, "stepped", side_effect=loop.Halted):
            assert loop.step(loop.Loop("proj", "t1")) is False
    assert stop.call_args[0][2:4] == ("t1", loop.Why.PERSON)
    with patch.object(loop, "stepped", side_effect=loop.Revised):
        assert loop.step(loop.Loop("proj", "t1")) is True


def test_the_authors_answer_stays_owed_until_its_turn_passes_this_revision(tmp_path):
    owed = {"kind": "spec-update", "rev": 2, "report_at": {"rev": 1, "turn": "old"}}
    store = {"rev": 2, "done": ["x"], "report": [{"item": "x", "pass": True}], "report_at": owed["report_at"],
             "handoff_owed": owed}
    sent = []

    def told(spot, spec, path, text):
        sent.append(text)
        spot.handing["turns"].append(f"t{len(sent)}")
        # The first turn's report fails its item; the second passes it. A report of another turn never counts.
        store["report"] = [{"item": "x", "pass": len(sent) == 2}]
        store["report_at"] = {"rev": 2, "turn": f"t{len(sent)}", "head": "h2"}
        return "answer"

    spot, at, held = loop.Loop("proj", "t1"), subprocess.CompletedProcess([], 0, "h2\n", ""), []
    with patch.object(loop, "told", told), patch.object(specs, "load", lambda *a: dict(store)), \
            patch("main.handoff.turn_for", return_value="Revision 2 replaces the spec."), \
            patch.object(specs, "sh", return_value=at), patch.object(loop, "change", return_value={}) as change, \
            patch.object(loop, "wait_hold", side_effect=lambda *a: held.append(1) or (lambda: None)):
        assert loop.handed(spot, dict(store), tmp_path, owed) is True
        assert sent[0].startswith("Revision 2 replaces the spec.") and "revision 2" in sent[0]
        assert sent[1] == specs.REPORT_AGAIN.format(rev=2) and change.call_args.kwargs == {"handoff_owed": None}
        # The person finished the revision in their own turn while the loop stood: nothing is sent again.
        mine = {**store, "report_at": {"rev": 2, "turn": "person", "head": "h2"}}
        assert loop.handed(spot, mine, tmp_path, owed) is True and len(sent) == 2
        # Not a report the checkout has moved past, nor one where two copies of an item stand for another.
        with patch.object(loop, "told", return_value=None) as again:
            assert loop.handed(spot, {**mine, "report_at": {**mine["report_at"], "head": "h1"}}, tmp_path, owed) is False
            twice = {**mine, "done": ["x", "y"], "report": [{"item": "x", "pass": True}] * 2}
            assert loop.handed(spot, twice, tmp_path, owed) is False and again.call_count == 2
        # A newer answer saved meanwhile is the next step's to send, never cleared unsent.
        store.update(report=mine["report"], report_at=mine["report_at"], handoff_owed={**owed, "rev": 3})
        with pytest.raises(loop.Revised):
            loop.handed(spot, dict(store), tmp_path, owed)

        class Revising:   # a revision lands between the report's check and the lock the clear takes
            def __enter__(self):
                store.update(rev=3, report=None)

            def __exit__(self, *exc):
                return False

        store.update(handoff_owed=owed)
        with patch.object(specs, "_files", Revising()), pytest.raises(loop.Revised):
            loop.handed(spot, dict(store), tmp_path, owed)
    # Every clear is judged holding the worktree, so no other turn commits between its check and the clear.
    assert change.call_count == 2 and len(held) == 4 and spot.handing is None


def test_an_external_gate_cut_between_its_receipt_and_gate_is_rebuilt_from_the_receipt(tmp_path):
    """A receipt without its `gate` was reused on resume while `gate` stayed empty, so every step ran again;
    the receipt goes first, so a lone `gate` never passes for a base or environment it never ran on."""
    lp = loop.Loop("proj", "t1")
    with patch.object(loop, "wait_hold", return_value=lambda: None), patch.object(loop, "sync_review"), \
            patch.object(specs, "for_round", return_value={}), patch.object(loop, "reusable", return_value=True), \
            patch.object(specs, "rounded", return_value=({"ok": True, "head": "h"}, {"ok": True})) as rounded, \
            patch.object(specs, "validate", return_value={}) as receipt, \
            patch.object(loop, "change", side_effect=lambda *a, **k: {} if receipt.called else None) as change:
        assert loop.external_shipped(lp, {"gate": {"head": "h", "ok": True}}, tmp_path, tmp_path, "h", "main")
        assert not rounded.called   # receipt and `gate` both stand: reused
        # An older failed `gate` at this head, cut before the passing receipt's `gate` was saved: rebuilt.
        assert loop.external_shipped(lp, {"gate": {"head": "h", "ok": False}}, tmp_path, tmp_path, "h", "main")
        assert rounded.call_count == 1 and change.call_args.kwargs == {"gate": {"ok": True, "head": "h"}}
        assert loop.external_shipped(lp, {}, tmp_path, tmp_path, "h", "main")   # `gate` only after the receipt
        assert rounded.call_count == 2 and change.call_args.kwargs == {"gate": {"ok": True, "head": "h"}}
        receipt.return_value = None   # made again under the ID: no `gate` for it
        change.reset_mock()
        assert loop.external_shipped(lp, {}, tmp_path, tmp_path, "h", "main") is False and not change.called


def test_a_review_loop_stop_keeps_a_committed_repairs_cleanup_baseline(tmp_path):
    def stopped(spot, spec, path, text):
        spot.halt.set()   # the Review Loop Stop lands after the repair committed code
        return None

    task = {"cleanup_owed": "base", "history": [{"ts": 1.0}], "worktree": "w"}
    with patch.object(loop, "turn", stopped), patch.object(sweep, "owed", return_value=True), \
            patch.object(specs, "load", side_effect=lambda *a: dict(task)), patch.object(specs, "update") as update:
        assert loop.told(loop.Loop("proj", "t1"), dict(task), tmp_path, "fix R1") is None
        update.assert_called_once_with("proj", "t1", cleanup_owed="base")   # on the stopped spec, its state untouched
        chosen = dict(task)

        def replaced(spot, spec, path, text):   # deleted and adopted again under the same ID meanwhile
            task["history"] = [{"ts": 2.0}]
            return stopped(spot, spec, path, text)

        with patch.object(loop, "turn", replaced):
            assert loop.told(loop.Loop("proj", "t1"), chosen, tmp_path, "fix R1") is None
        # Replaced before the repair even looked: the snapshot that chose it still names the old task.
        assert loop.told(loop.Loop("proj", "t1"), chosen, tmp_path, "fix R1") is None
        # Handed the replacement itself (a lookup by ID after the swap): a loop that ran the old task sends nothing.
        old = loop.Loop("proj", "t1")
        old.born = 1.0
        with patch.object(loop, "turn") as sent:
            assert loop.told(old, dict(task), tmp_path, "fix R1") is None and not sent.called
        task["state"] = "고치는 중 R1"
        assert loop.stepped(old) is False   # nor does its next step run the replacement
        # Nor does it write the replacement's gate or its round receipt (a stale pass would skip its gate).
        with patch.object(specs, "save") as save:
            assert loop.change(old, gate={"ok": True}) is None
            assert specs.validate("proj", "t1", old.born, round={"ok": True}) is None and not save.called
            assert loop.own(old, repaired="h") is None and not save.called
            assert loop.stop(old, "proj", "t1", loop.Why.GATE) is False and not save.called
            # Nor its local-verification record: an old cloud failure (`return_to_cloud`) goes through `keep`.
            with pytest.raises(verification.Replaced):
                verification.keep({"repo": "proj", "id": "t1", "history": [{"ts": 1.0}]}, failure_attempts=["old"])
            assert not save.called
            verification.keep({"repo": "proj", "id": "t1", "history": [{"ts": 2.0}]}, state="failed")
            assert save.call_args[0][0]["local_verification"]["state"] == "failed"   # its own task is written
            with patch.object(loop, "stepped", side_effect=verification.Replaced("gone")):
                assert loop.step(old) is False   # the old loop leaves; the replacement's own loop runs it
    assert update.call_count == 1   # the replacement never takes the old checkout's baseline
