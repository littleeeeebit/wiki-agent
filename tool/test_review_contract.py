"""Production contract composition and dispatch/approval boundaries, with local Git."""

# ruff: noqa: F811 — pytest injects imported fixtures by name.

import copy
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import decision
import pytest
import refactor_profile
from main import loop, refactor, review_contract as contract, specs, verification
from test_loop import (  # noqa: F401 — shared temporary Git/GitHub fixtures
    Reviewer, Worker, client, commit, git, looped, no_machine_settings, order, pr_spec, template, world,
)
from test_local_verification import cloud_world, git_world  # noqa: F401 — local API fixtures


def selected(world, spec):
    path = Path(spec["worktree"])
    head = world.hub.head(spec["pr"]["number"])
    base = specs.current_merge_base(path, "main", head)
    paths = loop.changed(path, base, head)
    return contract.select(world.repo, path, spec, loop.effective(spec, paths), paths, head, base)


def test_closed_declarations_and_executable_markdown_keep_code_floor(world):
    for bad in ({"command": "run-anything"}, {"criteria": ["unknown"]}, {"evidence": "api"}, {"flows": [1]}):
        with pytest.raises(ValueError):
            contract.declared(bad)
    assert contract.declared({"criteria": ["data", "data"]})["criteria"] == ["data"]
    spec = {"review_profile": "plan", "artifact_root": "docs/plans/x"}
    assert loop.effective(spec, ["docs/plans/x/1.md"]) == "plan"
    assert loop.effective(spec, ["docs/plans/x/SKILL.md"]) == "mixed"
    assert loop.effective({**spec, "artifact_root": "prompts"}, ["prompts/p.md"]) == "mixed"
    assert loop.effective({**spec, "review": {"criteria": ["refactor"]}}, ["docs/plans/x/1.md"]) == "mixed"
    ordinary = pr_spec(world, "prose", 1, "docs/guide.md")
    assert selected(world, ordinary)["criteria"] == ["code", "documentation"]
    planned = {**ordinary, "review_profile": "plan", "artifact_root": "docs"}
    result = selected(world, planned)
    assert result["criteria"] == ["plan"] and result["evidence"] == ["offline"]


@pytest.mark.parametrize("kind", ["api", "browser", "desktop", "differential"])
def test_missing_runtime_or_preservation_proof_blocks_before_reviewer(world, kind):
    pr_spec(world, "missing", 1, review={"evidence": [kind]})
    spec = looped("missing")
    assert spec["state"] == "멈춤" and spec["stopped"]["reason"] == loop.Why.PREPARATION.value
    assert spec["review_contract"]["problems"] and not Reviewer.made and not Worker.made


def test_contract_is_in_instruction_and_changed_obligations_invalidate_approval(world):
    spec = pr_spec(world, "code", 1, "code.py", review={"criteria": ["security", "async"]})
    done = looped(spec["id"])
    assert done["state"] == "머지 가능"
    record = done["rounds"][0]["review_contract"]
    assert record["criteria"] == ["async", "code", "security"]
    assert record["shadow"]["status"] == "not_asked"
    assert "## Composed review contract" in order(world, 1, 1)
    assert "cancellation, duplicate delivery" in order(world, 1, 1)
    assert specs.view(world.repo, done)["unproven"] == ""
    changed = {**done, "review": {"criteria": ["security", "async", "data"]}}
    assert "다시" in specs.view(world.repo, changed)["unproven"]


def test_local_api_receipts_are_not_substituted_by_offline_gate_and_stale_receipts_fail(cloud_world):
    world = cloud_world
    spec = pr_spec(world, "local-api", 1, "change.py", review={"flows": ["health"]})
    result = selected(world, spec)
    path, head, base = Path(spec["worktree"]), result["head"], result["base_oid"]
    assert result["evidence"] == ["api", "offline"]
    assert contract.ready(world.repo, path, spec, result)
    # Exercise the already-approved executor explicitly, not reviewer permissions.
    spec = verification.execute(world.repo, spec, path, head, base, threading.Event())
    assert not contract.ready(world.repo, path, spec, result)
    assert spec["local_verification"]["flows"][0]["evidence"]["requests"][0]["status"] == 200
    done = looped(spec["id"])
    assert done["state"] == "머지 가능" and Reviewer.made[0].tools == loop.REVIEW_TOOLS
    instruction = order(world, 1, 1)
    assert "## Registered runtime evidence" in instruction and '"status": 200' in instruction
    assert "private-api-key" not in instruction
    assert specs.view(world.repo, done)["unproven"] == ""
    modified = copy.deepcopy(done)
    modified["local_verification"]["head"] = "stale"
    assert contract.ready(world.repo, path, modified, result)
    assert "실행 증거" in specs.view(world.repo, modified)["unproven"]
    modified = copy.deepcopy(done)
    modified["local_verification"]["flows"][0]["head"] = "stale"
    assert contract.ready(world.repo, path, modified, result)
    world.settings["revisions"]["api"] = "2"
    (world.repo / verification.LOCAL).write_text(json.dumps(world.settings), encoding="utf-8")
    assert contract.ready(world.repo, path, done, result)


def test_refactor_round_and_final_gate_rerun_frozen_checks_and_protect_tests(world, monkeypatch):
    commit(world.repo, "test_behavior.py", "print('preserved')\n")
    git(world.repo, "push", "origin", "main")
    origin = refactor.spec_for(SimpleNamespace(repo=world.repo, hub=False), {"id": "r1", "role": {}},
                              "refactor-origin", "Preserve behavior", "main", git(world.repo, "rev-parse", "HEAD"),
                              world.repo)
    spec = pr_spec(world, "refactor-step", 1, "a.py", **{k: origin[k] for k in ("start_head", "refactor")})
    assert "start" not in spec
    folder = world.tmp / "frozen/project/refactor-step"
    folder.mkdir(parents=True)
    file = folder / "frozen.json"
    file.write_text(json.dumps({"tests": ["test_behavior.py"], "test_argv": ["python", "test_behavior.py"]}), encoding="utf-8")
    monkeypatch.setattr(refactor_profile, "STORE", world.tmp / "frozen")
    monkeypatch.setattr(refactor, "load", lambda *_: {"scope": "project", "tests": {"spec": "characterization"},
                                                     "steps": [{"spec": spec["id"]}]})
    # A mapped offline check must not hide the preservation check.
    with patch.object(specs, "registered", return_value={"a": {"cmd": "python gate.py", "paths": ["a.py"]}}):
        done = looped(spec["id"])
    assert done["state"] == "머지 가능"
    assert done["rounds"][0]["review_contract"]["criteria"] == ["code", "refactor"]
    assert done["rounds"][0]["review_contract"]["preservation"]["baseline"] == origin["start_head"]
    assert "preserve" in done["gate"]["cmd"]
    assert "preserve" in done["validation"]["final"]["command"]
    for invalid in (None, "", "HEAD", "f" * 40):
        assert selected(world, {**done, "start_head": invalid})["problems"]
    assert selected(world, {k: v for k, v in done.items() if k != "start_head"})["problems"]
    path = Path(done["worktree"])
    old_command = contract.preservation_command(done)
    file.write_text(json.dumps({"tests": ["test_behavior.py"], "test_argv": ["python", "test_behavior.py"],
                                "revision": "new"}), encoding="utf-8")
    assert contract.preservation_command(done) != old_command
    assert "명령이나 환경이 바뀌었다" in specs.view(world.repo, done)["unproven"]
    stale = subprocess.run(old_command, cwd=path, shell=True, capture_output=True, text=True)
    assert stale.returncode == 2 and "frozen specification changed" in stale.stderr
    head = commit(path, "test_behavior.py", "print('weakened')\n")
    git(path, "push", "origin", spec["id"])
    changed = selected(world, {**done, "pr": {**done["pr"], "head": head}})
    assert "테스트가 바뀌었" in contract.ready(world.repo, path, done, changed)


def test_jev_is_shadow_even_when_global_mode_is_active_and_failure_is_non_authoritative(world):
    spec = pr_spec(world, "shadow", 1, "a.py")
    result = selected(world, spec)
    before = copy.deepcopy(result)
    seen = []

    def unavailable(cfg, state, questions, trace, budget, stage):
        seen.append((state, questions))
        raise decision.JevError("outage")

    with patch.object(decision, "config", return_value=decision.Config("active", "test", "default", key="test")), \
         patch.object(contract.decisions, "normalized", side_effect=lambda s, _: (s, "test")), \
         patch.object(decision, "evaluate", unavailable):
        observation = contract.shadow(spec, ["a.py"], result, threading.Event())
    assert seen and observation["mode"] == "shadow" and observation["status"] == "unavailable"
    assert result == before and not contract.ready(world.repo, Path(spec["worktree"]), spec, result)


def test_jev_additions_are_recorded_but_never_applied_and_cancel_sends_nothing(world):
    spec = pr_spec(world, "shadow-success", 1, "a.py")
    result = selected(world, spec)
    before = copy.deepcopy(result)

    def additions(cfg, state, questions, trace, budget, stage):
        return {name: {"choice": "add", "confidence": 0.9,
                       "probabilities": {k: 0.9 if k == "add" else 0.05 for k in q["criteria"]}}
                for name, q in questions.items()}

    with patch.object(decision, "config", return_value=decision.Config("active", "test", "default", key="test")), \
         patch.object(contract.decisions, "normalized", side_effect=lambda s, _: (s, "test")), \
         patch.object(decision, "evaluate", side_effect=additions) as sent:
        observation = contract.shadow(spec, ["a.py"], result, threading.Event())
        assert observation["status"] == "decided" and "evidence:api" in observation["additions"]
        assert observation["prompt_version"].startswith("review-contract-shadow-1:")
        assert result == before and result["evidence"] == ["offline"]
        halt = threading.Event()
        halt.set()
        assert contract.shadow(spec, ["a.py"], result, halt)["status"] == "not_asked"
        assert sent.call_count == 1


def test_spec_generation_and_revision_preserve_explicit_obligations(world):
    spec = pr_spec(world, "declared", 1, "a.py", review={"criteria": ["async"]})
    assert specs.profiled(world.repo, spec)["review"]["criteria"] == ["async"]
    assert '"review"' in specs.system(Path(spec["worktree"]))
    specs.revise(world.repo, spec, {"rev": 1, "reason": "Acceptance also requires authorization checks",
                                  "goal": spec["goal"], "done": spec["done"], "out": spec["out"],
                                  "review": {"criteria": ["async", "security"]}})
    assert spec["rev"] == 2 and spec["review"]["criteria"] == ["async", "security"]


def test_preservation_cli_rejects_bad_arguments_without_dispatch():
    with patch.object(sys, "argv", ["refactor_profile.py"]), patch.object(refactor_profile, "task") as task:
        assert refactor_profile.main() == 2
        task.assert_not_called()


def test_missing_jev_key_does_not_translate_or_send_task_data():
    with patch.object(decision, "config", return_value=decision.Config("active", "test", "default")), \
         patch.object(contract.decisions, "normalized") as normalize, patch.object(decision, "evaluate") as sent:
        assert contract.shadow({}, [], {}, threading.Event())["reason"] == "missing_key"
        normalize.assert_not_called()
        sent.assert_not_called()


def test_legacy_approval_reuse_is_preserved_only_without_new_obligations():
    current = {"profile": "code", "digest": "current"}
    assert contract.matches({}, {}, current)
    assert not contract.matches({"review": {"criteria": ["data"]}}, {}, current)
    assert not contract.matches({"refactor": {"run": "r1"}}, {}, current)
    assert not contract.matches({}, {}, {**current, "profile": "mixed"})
    assert contract.matches({}, {"review_contract": {"digest": "current"}}, current)
    assert not contract.matches({}, {"review_contract": {"digest": "old"}}, current)
