"""Review progress, cancellation before dispatch and external ownership."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name

import re
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import Event
from main import loop, specs, work
from test_loop import (  # noqa: F401 -- shared fixtures and isolated Git/GitHub world
    Reviewer, Worker, allow, client, deny, git, no_machine_settings, pr_spec, template, waited, world,
)
from workspace import remove


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
