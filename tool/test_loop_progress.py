"""Review progress, cancellation before dispatch and external ownership."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name

import json
import re
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import Event
from main import loop, specs, work
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
