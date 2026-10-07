"""Stopped refactor runs follow ordinary tasks without replaying their old stages."""
# ruff: noqa: F811 — borrowed fixtures are named by the tests that use them

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import refactor, refactor_continuation, specs, work
from test_main import client, no_machine_settings  # noqa: F401
from test_refactor import Host, _git, finished, selected  # noqa: F401
from test_specs import Worker, repo  # noqa: F401 — selected uses the repository fixture


def stopped(selected):
    def timeout(self, *_):
        yield SimpleNamespace(kind="error", text="Host timed out", meta={})

    api = client()
    with patch.object(Host, "say", timeout):
        rid = api.post("/api/refactors", json={"request_id": "continuation-request", "mode": "cleanup"}).json()["id"]
        run = finished(api, rid)
    assert run["phase"] == "tests" and run["stopped"]["reason"] == "host"
    return api, run, specs.load(selected.name, run["tests"]["spec"])


def test_an_ordinary_agent_turn_transfers_ownership_and_cannot_replay_after_restart(selected):
    api, saved, spec = stopped(selected)
    Worker.replies, Worker.made = ["Continued in the task"], []
    with patch.object(work, "ChatSession", Worker):
        response = api.post("/api/work/say", json={"path": str(selected), "text": "Continue the cleanup"})
    assert response.status_code == 200
    assert refactor.load(selected.name, saved["id"])["continuation"] == {
        "spec": spec["id"], "birth": spec["history"][0]["ts"]}
    specs.update(selected.name, spec["id"], state="PR #26", pr={"number": 26, "url": "https://example.test/pr/26"})
    refactor.recover()
    status = api.get(f"/api/refactors/{saved['id']}").json()
    assert status["state"] == "continued" and status["continuation"]["pr"]["number"] == 26
    assert status["steps"] == saved["steps"], "manual work never fabricates candidate/review completion"
    assert api.get("/api/refactors").json()["runs"][0]["state"] == "continued"
    with patch.object(refactor, "turn") as turn:
        for action in ("resume", "approve", "cancel"):
            assert api.post(f"/api/refactors/{saved['id']}/{action}").status_code == 409
        with pytest.raises(HTTPException):
            refactor.launch(selected, saved)  # A stale caller cannot bypass the endpoint's guard.
        assert not turn.called
    specs.update(selected.name, spec["id"], state="머지됨")
    assert api.get(f"/api/refactors/{saved['id']}").json()["continuation"]["state"] == "머지됨"
    work.close_all()


def test_legacy_manual_work_is_reconciled_only_from_its_exact_task_record(selected):
    api, saved, spec = stopped(selected)
    file = work.LOGS / selected.name / f"{spec['id']}.jsonl"
    file.parent.mkdir(parents=True, exist_ok=True)
    later = saved["stopped"]["ts"] + 1
    wrong = file.with_name("old-task.jsonl")
    wrong.write_text(json.dumps({"role": "user", "ts": later, "text": "Other task"}) + "\n", encoding="utf-8")
    file.write_text(json.dumps({"role": "user", "ts": later - 2, "text": "Before failure"}) + "\n", encoding="utf-8")
    assert api.get(f"/api/refactors/{saved['id']}").json()["state"] == "stopped"
    file.write_text(json.dumps({"role": "user", "ts": later, "text": "Continue"}) + "\n", encoding="utf-8")
    assert api.get(f"/api/refactors/{saved['id']}").json()["state"] == "continued"
    assert api.post(f"/api/refactors/{saved['id']}/resume").status_code == 409
    refactor.recover()
    checkpoint = refactor.load(selected.name, saved["id"])["continuation"]
    assert checkpoint["birth"] == spec["history"][0]["ts"]
    file.unlink()  # Transcript retention cannot undo a saved ownership transfer.
    assert api.get(f"/api/refactors/{saved['id']}").json()["state"] == "continued"
    spec["history"][0]["ts"] += 1
    with patch.object(specs, "load", return_value=spec):
        assert refactor_continuation.view(refactor.load(selected.name, saved["id"]))["continuation"]["fault"]
        assert api.post(f"/api/refactors/{saved['id']}/resume").status_code == 409


def test_live_continuation_status_uses_the_current_branch_owner(selected):
    api, saved, spec = stopped(selected)
    refactor_continuation.take(selected)
    active = work.Run(SimpleNamespace(id="continuation-cell", model="m", path=selected))
    with patch.object(work, "_runs", {str(selected): active}):
        assert api.get(f"/api/refactors/{saved['id']}").json()["continuation"]["running"]
        _git(selected, "switch", "main")
        assert not api.get(f"/api/refactors/{saved['id']}").json()["continuation"]["running"]
        _git(selected, "switch", spec["id"])
        active.finish()
        assert not api.get(f"/api/refactors/{saved['id']}").json()["continuation"]["running"]


def test_legacy_hub_continuations_use_the_linked_tree_record_and_exact_path(selected):
    with patch.object(refactor, "scope_of", return_value="hub"):
        api, saved, spec = stopped(selected)
    assert "workspace_mode" not in spec
    path = Path(spec["worktree"])
    file = work.record(path)
    assert file.parent.name == f"{selected.name}-worktrees"
    later = saved["stopped"]["ts"] + 1
    wrong = {"role": "user", "ts": later, "text": "Another tree", "path": str(path.with_name("unrelated"))}
    file.write_text(json.dumps(wrong) + "\n", encoding="utf-8")
    assert api.get(f"/api/refactors/{saved['id']}").json()["state"] == "stopped"
    file.write_text(json.dumps({**wrong, "path": str(path), "text": "Continue the hub task"}) + "\n", encoding="utf-8")
    assert api.get(f"/api/refactors/{saved['id']}").json()["state"] == "continued"
    assert api.post(f"/api/refactors/{saved['id']}/resume").status_code == 409
    refactor.recover()
    assert refactor.load(selected.name, saved["id"])["continuation"]["spec"] == spec["id"]
    file.unlink()
    assert api.get(f"/api/refactors/{saved['id']}").json()["state"] == "continued"
