"""The `[리펙터링]` workflow (`docs/plans/refactor/3-cleanup.md`): a quick cleanup
run scans, freezes behaviour in a test PR, and stacks a step PR on it, sending
both to review because the request covers their tiers.

The host is a stand-in behind `refactor.ChatSession`, the runner behind
`refactor_profile.drive` (`test_refactor_profile.py` drives the real one), and
GitHub and the push behind `specs.sh`. Git itself runs.
"""
# ruff: noqa: F811 — a borrowed fixture is named again by each test that takes it

import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import refactor_profile
from agent.chat_session import Event
from main import loop, query, refactor, specs, work
from test_main import client, no_machine_settings  # noqa: F401 — the fixture is autouse
from test_specs import Remote, made, repo, spec_block  # noqa: F401 — `repo` is a fixture

BIG = "".join(f"value_{i} = {i}\n" for i in range(801))
TEST = "import big\nassert big.value_800 == 800\n"


class Host:
    """Writes the characterization test and names it."""

    made: list = []

    def __init__(self, path, **rest):
        self.path, self.id, self.parent_id, self.rest = Path(path), uuid.uuid4().hex, None, rest
        Host.made.append(self)

    def say(self, text, halt=None):
        if not self.rest.get("write"):   # the read-only audit: one L2 step on the module
            plan = '{"steps": [{"tier": "L2", "goal": "Split big.py by value range", "files": ["big.py"]}]}'
            yield Event("done", f"Plan.\n\n```refactor-plan\n{plan}\n```", {"tokens": {"in": 7, "out": 3}}, self.id)
            return
        (self.path / "test_big.py").write_text(TEST, encoding="utf-8")
        block = '{"tests": ["test_big.py"], "test_argv": ["%s", "-B", "test_big.py"]}' % sys.executable.replace("\\", "/")
        yield Event("done", f"Pinned.\n\n```refactor-tests\n{block}\n```", {"tokens": {"in": 10, "out": 5}}, self.id)

    def stop(self, halt):
        pass

    def close(self):
        pass


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


@pytest.fixture
def selected(repo, tmp_path, monkeypatch):
    (repo / "big.py").write_text(BIG, encoding="utf-8")
    _git(repo, "add", "big.py")
    _git(repo, "commit", "-qm", "big")
    monkeypatch.setattr(refactor, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(refactor_profile, "STORE", tmp_path / "frozen")
    monkeypatch.setattr(refactor, "ChatSession", Host)
    Host.made = []
    return repo


def until(client, rid: str, ready) -> dict:
    for _ in range(900):
        run = next(r for r in client.get("/api/refactors").json()["runs"] if r["id"] == rid)
        if ready(run):
            return run
        time.sleep(0.1)
    raise AssertionError(f"the run never got there: {run}")


def finished(client, rid: str) -> dict:
    return until(client, rid, lambda run: run["state"] != "running")


def test_cleanup_freezes_then_stacks_a_reviewed_step(selected):
    api = client()
    remote = Remote()
    kicked = []

    def review(name, sid):   # review allows each PR at once
        kicked.append(sid)
        specs.update(name, sid, state="머지 가능")

    def adopted(repo, scope, name, config):
        return {"state": "adopted", "commit": _git(repo, "rev-parse", "HEAD"), "branch": "none"}

    body = {"request_id": "refactor-req-0001", "mode": "cleanup", "top": 1,
            "role": {"model": "m", "effort": "high"}, "limits": {"seconds": 600, "calls": 5, "tokens": 100_000}}
    with patch.object(specs, "sh", side_effect=remote), patch.object(loop, "kick", side_effect=review), \
            patch.object(refactor_profile, "drive", side_effect=adopted):
        rid = api.post("/api/refactors", json=body).json()["id"]
        run = finished(api, rid)
        again = api.post("/api/refactors", json=body).json()

    assert run["state"] == "done", run.get("stopped")
    assert again["id"] == rid, "the same request is the same run"
    assert [h["path"] for h in run["hotspots"]] == ["big.py"]
    tests, step = run["tests"]["spec"], run["steps"][0]["spec"]
    assert kicked == [tests, step], "both tiers are covered by the request: review starts itself"
    creates = [c for c in remote.calls if c[:3] == ["gh", "pr", "create"]]
    assert [(c[c.index("--head") + 1], c[c.index("--base") + 1]) for c in creates] == [(tests, "main"), (step, tests)]
    assert _git(selected, "show", "--name-only", "--format=", tests) == "test_big.py", "the test PR holds tests only"
    frozen = (refactor_profile.STORE / "project" / step / "frozen.json").read_text(encoding="utf-8")
    assert '"tests": [\n    "test_big.py"' in frozen
    assert run["spent"]["calls"] == 1 and run["spent"]["tokens"] == 15


def test_a_restart_stops_a_running_run_and_a_code_edit_is_refused(selected):
    api = client()
    say = Host.say

    def wider(self, text, halt=None):
        (self.path / "big.py").write_text("x = 1\n", encoding="utf-8")
        yield from say(self, text, halt)

    with patch.object(specs, "sh", side_effect=Remote()), patch.object(Host, "say", wider):
        rid = api.post("/api/refactors", json={"request_id": "refactor-req-0002", "mode": "cleanup",
                                               "limits": {"seconds": 600, "calls": 5, "tokens": 9}}).json()["id"]
        run = finished(api, rid)
    assert run["stopped"]["reason"] == "tests_touched_code", run["stopped"]

    refactor.update("proj", rid, state="running")
    refactor.recover()
    assert refactor.load("proj", rid)["stopped"]["reason"] == "restart"


def _named(tests: list[str], argv: list[str], code: bool = False):
    def say(self, text, halt=None):
        (self.path / "test_big.py").write_text(TEST, encoding="utf-8")
        if code:
            (self.path / "big.py").write_text(BIG.replace("value_0 = 0", "value_0 = 1"), encoding="utf-8")
        block = json.dumps({"tests": tests, "test_argv": argv})
        yield Event("done", f"```refactor-tests\n{block}\n```", {"tokens": {"in": 1, "out": 1}}, self.id)
    return say


def test_the_checkout_test_names_one_request_and_the_test_command_are_all_bounded(selected):
    api = client()
    limits = {"seconds": 600, "calls": 5, "tokens": 100}

    def started(key, seconds=600):
        return api.post("/api/refactors", json={"request_id": key, "mode": "cleanup",
                                                "limits": {**limits, "seconds": seconds}}).json()["id"]

    py = sys.executable.replace("\\", "/")
    branch = _git(selected, "branch", "--show-current")
    relabelled = _named(["big.py", "test_big.py"], [py, "-B", "test_big.py"], code=True)
    with patch.object(specs, "sh", side_effect=Remote()), patch.object(Host, "say", relabelled):
        run = finished(api, started("refactor-req-0003"))
    assert run["stopped"]["reason"] == "tests_touched_code" and "big.py" in run["stopped"]["detail"], \
        "a production file named as a test is still production"
    _git(selected, "reset", "-q", "--hard")
    _git(selected, "clean", "-fdq")
    _git(selected, "switch", "-q", branch)

    release = query.hold(work._busy, query._lock, str(selected), "", kind="turn")
    try:
        with patch.object(specs, "sh", side_effect=Remote()):
            run = finished(api, started("refactor-req-0004"))
    finally:
        release()
    assert run["stopped"]["reason"] == "busy" and _git(selected, "branch", "--show-current") == branch, \
        "another turn's checkout is never switched under it"

    hangs = _named(["test_big.py"], [py, "-c", "import time; time.sleep(3600)"])
    with patch.object(specs, "sh", side_effect=Remote()), patch.object(Host, "say", hangs):
        began = time.monotonic()
        run = finished(api, started("refactor-req-0005", seconds=3))
    assert run["stopped"]["reason"] == "budget" and time.monotonic() - began < 20, run["stopped"]

    token, ids = refactor.secrets.token_hex, []

    def slow(n):   # widens the gap two equal requests would both walk through
        time.sleep(0.3)
        return token(n)

    with patch.object(refactor.secrets, "token_hex", slow), patch.object(refactor, "launch") as launch:
        threads = [threading.Thread(target=lambda: ids.append(started("refactor-req-0006"))) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert len(set(ids)) == 1 and launch.call_count == 1, "one request is one run"


def test_restructure_holds_the_repository_until_the_person_approves(selected):
    api = client()
    adopted = lambda repo, scope, name, config: {"state": "adopted", "branch": "none",  # noqa: E731
                                                  "commit": _git(repo, "rev-parse", "HEAD")}
    body = {"request_id": "refactor-req-0003", "mode": "restructure", "files": ["big.py"],
            "role": {"model": "m", "effort": "high"}, "limits": {"seconds": 600, "calls": 9, "tokens": 100_000}}
    other = specs.unique(selected, "other-task")
    specs.save({**specs.load("proj", made(selected, spec_block())[0]["id"]), "id": other, "state": "작업 중"})
    review = lambda name, sid: specs.update(name, sid, state="머지 가능")  # noqa: E731
    with patch.object(specs, "sh", side_effect=Remote()), patch.object(loop, "kick", side_effect=review), \
            patch.object(refactor_profile, "drive", side_effect=adopted):
        refused = api.post("/api/refactors", json=body)
        assert refused.status_code == 409 and other in refused.json()["detail"], "an open task refuses L2 work"
        specs.update("proj", other, state="머지됨")

        rid = api.post("/api/refactors", json=body).json()["id"]
        run = until(api, rid, lambda r: r["steps"] and r["steps"][0]["state"] == "published" or r["state"] != "running")
        step = run["steps"][0]
        assert (step["tier"], step["files"]) == ("L2", ["big.py"]), run.get("stopped")
        with pytest.raises(HTTPException, match=step["spec"]):
            specs.checkout_idle(selected)   # a new task is refused while the step runs
        specs.update("proj", step["spec"], state="머지 가능")   # the person ran the review; no kick for L2
        until(api, rid, lambda r: r["steps"][0]["state"] == "awaiting")
        audit = next(h for h in Host.made if not h.rest.get("write"))
        assert audit.rest["tools"] == "Read,Glob,Grep", "the audit gets no shell"

        branch = _git(selected, "branch", "--show-current")
        cleanup = api.post("/api/refactors", json={**body, "request_id": "refactor-req-0004", "mode": "cleanup",
                                                   "files": []}).json()["id"]
        assert finished(api, cleanup)["stopped"]["reason"] == "busy"
        assert _git(selected, "branch", "--show-current") == branch, "another run never forks a blocked checkout"

        refactor.recover()   # what a restart leaves: the hold, read off the spec
        with pytest.raises(HTTPException):
            specs.checkout_idle(selected)
        assert api.post(f"/api/refactors/{rid}/cancel").status_code == 200
        until(api, rid, lambda r: r["state"] == "stopped")
        specs.checkout_idle(selected)   # cancelling releases it

        api.post(f"/api/refactors/{rid}/resume")
        until(api, rid, lambda r: specs.load("proj", step["spec"])["refactor"].get("block"))
        with pytest.raises(HTTPException):
            specs.checkout_idle(selected)   # resuming takes it again
        specs.update("proj", step["spec"], state="작업 중")   # a revision sends the step back to work
        assert api.post(f"/api/refactors/{rid}/approve").status_code == 409, "approval needs the review as it stands"
        specs.update("proj", step["spec"], state="머지 가능")
        assert api.post(f"/api/refactors/{rid}/approve").status_code == 200
        run = finished(api, rid)
    assert run["state"] == "done", run.get("stopped")
    assert run["spent"]["tokens"] == 25, "the audit and the test turn are both charged"
    specs.checkout_idle(selected)

    spec = specs.load("proj", step["spec"])   # a hold a restart kept, with no worker left to release it
    specs.save({**spec, "refactor": {**spec["refactor"], "block": True}})
    with pytest.raises(HTTPException):
        specs.checkout_idle(selected)
    assert api.post(f"/api/refactors/{rid}/cancel").status_code == 200
    specs.checkout_idle(selected)   # cancel releases it without a worker


def test_a_read_only_audit_that_writes_stops_the_run(selected):
    api = client()
    say = Host.say

    def writes(self, text, halt=None):
        if not self.rest.get("write"):
            (self.path / "note.txt").write_text("x", encoding="utf-8")
        yield from say(self, text, halt)

    body = {"request_id": "refactor-req-0005", "mode": "restructure", "files": ["big.py"],
            "limits": {"seconds": 600, "calls": 9, "tokens": 100_000}}
    with patch.object(specs, "sh", side_effect=Remote()), patch.object(Host, "say", writes):
        run = finished(api, api.post("/api/refactors", json=body).json()["id"])
    assert run["stopped"]["reason"] == "read_only_wrote", run["stopped"]
