"""Refactoring conversations dispatch the settled request without ceilings."""
# ruff: noqa: F811 — imported pytest fixtures are reused by name

import json
import os
import threading
from unittest.mock import patch

import pytest

from agent.chat_session import Event
from fastapi import HTTPException
from main import planning, refactor, refactor_api, specs
from test_main import client, no_machine_settings  # noqa: F401
from test_refactor import Host, _git, selected  # noqa: F401
from test_specs import repo  # noqa: F401

if os.name == "nt":
    import _winapi


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


def test_the_scope_block_a_turn_is_asked_for_is_one_it_can_read():
    """ai-coding-wiki's audit, asked only for "a reason and choices", answered in YAML and stopped on
    "Expecting value: line 1 column 1"; the prompt now shows the JSON `turn` reads."""
    asked = refactor.purpose({"goal": "g", "done": [], "out": []})
    assert json.dumps(refactor.SCOPE) in asked
    specs._shape("choices", refactor.fenced("refactor-scope", f"```refactor-scope\n{json.dumps(refactor.SCOPE)}\n```"))


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


def test_a_package_directory_is_a_discovery_target(selected):
    # A legacy request naming the package directory, as a module described by
    # purpose usually is, was refused as "not a file in this repository".
    (selected / "pkg").mkdir()
    (selected / "pkg/orders.py").write_text("x = 1\n", encoding="utf-8")

    def checked(files):
        refactor_api.checked(selected, refactor_api.Start(request_id="legacy-full-0001", mode="cleanup", files=files))

    checked(["pkg", "big.py"])
    with pytest.raises(HTTPException, match="디렉터리가 아니다"):
        checked(["missing"])
    # A link named like a package but landing in Git metadata is refused where it lands.
    if os.name == "nt":
        _winapi.CreateJunction(str(selected / ".git"), str(selected / "meta"))
    else:
        (selected / "meta").symlink_to(selected / ".git", target_is_directory=True)
    with pytest.raises(HTTPException, match="디렉터리가 아니다"):
        checked(["meta"])
    paths = ["pkg/orders.py", "pkgx.py", "big.py"]
    assert [p for p in paths if refactor.targeted(p, ["pkg/"])] == ["pkg/orders.py"]
