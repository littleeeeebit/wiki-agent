"""Refactoring conversations dispatch the settled request without ceilings."""
# ruff: noqa: F811 — imported pytest fixtures are reused by name

import json
import threading
from unittest.mock import patch

import pytest

from agent.chat_session import Event
from fastapi import HTTPException
from main import channels, planning, query, refactor, specs
from test_main import client, no_machine_settings  # noqa: F401
from test_refactor import Host, _git, selected  # noqa: F401
from test_specs import repo, spec_block  # noqa: F401


def draft(repo, mode="cleanup", **extra):
    block = spec_block(slug="clean-orders", goal="Remove duplicate order handling", out=["Change public behavior"],
                       done=["Existing order behavior is preserved", "Duplicate handling is removed"],
                       refactor={"mode": mode, "files": ["big.py"]}, **extra)
    return specs.answered(repo, [{"name": "spec", "value": block}],
                          {"focus": "refactor", "turn": 1, "session": "discussion", "refactor_mode": mode,
                           "refactor_generation": query.config("refactor").get("refactor_generation", 0)})


def test_mode_changes_keep_the_conversation_and_block_stale_drafts(selected):
    api = client()
    assert [c.id for c in channels.CHANNELS][:2] == ["next", "refactor"]
    sid = draft(selected)[0]["id"]
    query.remember("refactor", "user", "Remove duplicate order handling", repo=selected)
    settings = {"repo": "proj", "model": "opus", "effort": "high", "refactor_mode": "restructure"}
    assert api.post("/api/config/refactor", json=settings).json()["kept"]
    assert query.recall("refactor")[0]["text"] == "Remove duplicate order handling"
    with patch.object(refactor, "launch") as launch:
        assert api.post(f"/api/specs/{sid}/start", json={}).status_code == 409
        assert not launch.called
    revised = draft(selected, "restructure")[0]["id"]
    assert revised == sid and specs.load("proj", sid)["rev"] == 2
    for mode in ("cleanup", "restructure"):
        api.post("/api/config/refactor", json={**settings, "refactor_mode": mode})
    with patch.object(refactor, "launch") as launch:
        assert api.post(f"/api/specs/{sid}/start", json={}).status_code == 409
        assert not launch.called
    draft(selected, "restructure")
    with patch.object(refactor, "launch") as launch:
        response = api.post(f"/api/specs/{sid}/start", json={"model": "opus", "effort": "high"})
        rid = response.json()["refactor"]
        run = refactor.load("proj", rid)
        assert run["mode"] == "restructure" and run["spec"] == sid
        assert run["goal"] == specs.load("proj", sid)["goal"]
        assert run["done"] == specs.load("proj", sid)["done"][1:]
        assert run["out"] == ["Change public behavior"]
        assert "limits" not in run and "top" not in run
        assert api.post(f"/api/specs/{sid}/start", json={}).json()["refactor"] == rid
        assert launch.call_count == 1
    assert api.get(f"/api/refactors/{rid}").json()["id"] == rid
    assert api.put(f"/api/specs/{sid}", json={"rev": 2, "goal": "Change behavior"}).status_code == 409
    assert api.post(f"/api/specs/{sid}/delete").status_code == 409
    assert api.post(f"/api/specs/{sid}/drop").status_code == 409


def test_audit_can_discover_files_but_goal_expansion_stops_for_choices(selected):
    api = client()
    with patch.object(refactor, "launch"):
        rid = api.post("/api/refactors", json={"request_id": "purpose-refactor", "mode": "cleanup",
                       "files": ["big.py"], "goal": "Remove duplicate order handling", "done": ["No duplicates"]}).json()["id"]
    with patch.object(refactor, "hotspots", return_value=[]):
        run = refactor.scan(refactor.Worker(selected, refactor.load("proj", rid)), refactor.load("proj", rid))
    assert run["phase"] == "audit"
    (selected / "orders.py").write_text("ORDER = 1\n", encoding="utf-8")
    _git(selected, "add", "orders.py")
    _git(selected, "commit", "-qm", "related orders module")
    plan = {"steps": [{"tier": "L1", "goal": "Remove duplication", "files": ["big.py", "orders.py"]}]}
    with patch.object(refactor, "turn", return_value="```refactor-plan\n" + json.dumps(plan) + "\n```"):
        run = refactor.audited(refactor.Worker(selected, run), run)
    assert run["steps"][0]["files"] == ["big.py", "orders.py"]
    assert "No duplicates" in run["steps"][0]["goal"]
    say = Host.say

    def tests(self, text, halt=None):
        assert "- big.py" in text and "- orders.py" in text
        yield from say(self, text, halt)

    with patch.object(Host, "say", tests), patch.object(refactor, "published", return_value=7), refactor.owning(selected):
        refactor.characterized(refactor.Worker(selected, run), run, "discovered-tests")
    choices = {"question": "The public API must change. How should we proceed?", "options": [
        {"label": "Keep the API", "note": "Keep the agreed scope"}, {"label": "Reconsider the mode", "note": "Create a new specification"}]}

    def answer(self, text, halt=None):
        yield Event("done", "```refactor-scope\n" + json.dumps(choices) + "\n```", {}, self.id)

    worker = refactor.Worker(selected, run)
    with patch.object(Host, "say", answer), pytest.raises(refactor.Stop) as stopped:
        refactor.turn(worker, run, "Investigate", write=False)
    assert stopped.value.reason == "scope" and worker.spent()["unknown"]
    assert api.post(f"/api/refactors/{rid}/resume").status_code == 409


def test_only_refactoring_plans_can_omit_limits_and_unknown_usage_is_informational(selected):
    roles = planning.Roles(**{k: {"model": "opus", "effort": "high"} for k in ("planner", "reviser", "reviewer")})
    normal = planning.Plan(request_id="normal-plan-request", goal="Plan", roles=roles)
    with pytest.raises(HTTPException):
        planning.checked(normal)
    planning.checked(normal.model_copy(update={"refactor": True}))
    worker = planning.Worker("proj", "plan", {"input": {"refactor": True}, "limits": None,
        "spent": {"seconds": 100000, "calls": 1000, "tokens": 100000000, "unknown": True}})
    worker.unknown = True
    worker.budget.call()
    worker.halt.set()
    from common.budget import Cancelled
    with pytest.raises(Cancelled):
        worker.budget.call()


def test_cancel_stops_a_turn_even_before_another_event_arrives(selected):
    run = {"id": "cancel", "scope": "project", "spent": {"seconds": 0, "calls": 0, "tokens": 0}}
    worker = refactor.Worker(selected, run)
    halted = threading.Event()
    class Active:
        halt = halted
        class Chat:
            def stop(self, event):
                assert event.is_set()
        chat = Chat()
    worker.active = Active()
    worker.cancel()
    assert halted.is_set()


def test_resumed_runner_usage_is_recorded_once(selected, tmp_path):
    with patch.object(refactor, "launch"):
        rid = client().post("/api/refactors", json={"request_id": "runner-usage-resume", "mode": "cleanup",
                           "files": ["big.py"]}).json()["id"]
    run = refactor.load("proj", rid)
    run["tests"] = {"tests": ["tests.py"], "test_argv": ["python", "tests.py"]}
    step = {"goal": "Remove duplication", "tier": "L1", "files": ["big.py"]}
    with patch.object(refactor.refactor_profile, "prepare", return_value=tmp_path / "experiment.json"), \
         patch.object(refactor.refactor_profile, "drive", return_value={"state": "adopted"}), \
         patch.object(refactor.improvement.Experiment, "read") as read:
        for calls, tokens in [(4, 100), (4, 100), (7, 160)]:
            read.return_value = {"spent": {"calls": calls, "tokens": tokens, "unknown": True}}
            worker = refactor.Worker(selected, refactor.load("proj", rid))
            refactor.competed(worker, run, step, "usage", selected)
            saved = refactor.load("proj", rid)
            assert saved["spent"]["calls"] == calls and saved["spent"]["tokens"] == tokens
            assert saved["spent"]["unknown"]
