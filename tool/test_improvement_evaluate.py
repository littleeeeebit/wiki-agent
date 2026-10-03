"""The supplied adapter grades frozen commands and reports actual usage."""

import json
import os
import subprocess
import sys

import pytest

from improvement_evaluate import evaluate
from improvement import Refused, execute


def request(tmp_path):
    return {"stage": "evaluate", "root": str(tmp_path), "split": "evolve", "ids": ["a", "b"],
            "model": "fixed-policy-v1", "trials": 2, "limits": {"seconds": 20, "calls": 20, "tokens": 1000}}


def test_offline_commands_have_explicit_zero_inference_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_IMPROVEMENT_CACHE", str(tmp_path / "cache"))
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {
        "a": {"argv": [sys.executable, "-c", "raise SystemExit(0)"], "inference": False, "seconds": 5},
        "b": {"argv": [sys.executable, "-c", "raise SystemExit(1)"], "inference": False, "seconds": 5}},
        "guards": {"evolve": {"integrity": [], "required_a": ["a"], "required_b": ["b"]}}}
    result = evaluate(request(tmp_path), manifest, tmp_path)
    assert result["usage"] == {"calls": 0, "tokens": 0}
    assert [r["reward"] for r in result["trials"]] == [1, 1, 0, 0]
    assert result["guards"] == {"integrity": True, "required_a": True, "required_b": False}


def test_inference_commands_report_cost_and_get_isolated_trial_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_IMPROVEMENT_CACHE", str(tmp_path / "cache"))
    # The task checks the pinned model and separate repetition cache before scoring.
    code = ("import json, os; assert os.environ['WIKI_IMPROVEMENT_MODEL'] == 'fixed-policy-v1'; "
            "print(json.dumps({'reward': 0.8, 'usage': {'calls': 2, 'tokens': 30}}))")
    task = {"argv": [sys.executable, "-c", code], "inference": True, "seconds": 5}
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {"a": task, "b": task},
                "guards": {"evolve": {"integrity": []}}}
    result = evaluate(request(tmp_path), manifest, tmp_path)
    assert result["usage"] == {"calls": 8, "tokens": 120}
    assert all(r["tokens"] == 30 and r["reward"] == .8 for r in result["trials"])
    assert len(list((tmp_path / "cache").glob("*/*"))) == 4
    assert json.dumps(result)


def test_inference_usage_is_not_assumed_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_IMPROVEMENT_CACHE", str(tmp_path / "cache"))
    task = {"argv": [sys.executable, "-c", "print('{}')"], "inference": True, "seconds": 5}
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {"a": task, "b": task},
                "guards": {"evolve": {"integrity": []}}}
    with pytest.raises(ValueError, match="usage is unavailable"):
        evaluate(request(tmp_path), manifest, tmp_path)


@pytest.mark.parametrize("split", ["evolve", "held_out"])
@pytest.mark.parametrize("ids", [[], ["unknown-task"]])
def test_invalid_guard_checks_are_refused_before_any_task(tmp_path, monkeypatch, split, ids):
    import improvement_evaluate

    def unexpected_execution(*args):
        pytest.fail("An invalid mandatory guard must be refused before spending any budget")

    monkeypatch.setattr(improvement_evaluate, "execute", unexpected_execution)
    data = {**request(tmp_path), "split": split}
    task = {"argv": [sys.executable, "-c", "raise SystemExit(0)"], "inference": False, "seconds": 5}
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {"a": task, "b": task},
                "guards": {split: {"integrity": [], "safety": ids}}}
    with pytest.raises(ValueError, match="require real task checks|belong to the evaluated split"):
        evaluate(data, manifest, tmp_path)


def test_command_timeout_and_output_limit_are_enforced(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        execute([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, None, .05, dict(os.environ))
    with pytest.raises(Refused, match="output exceeded"):
        execute([sys.executable, "-c", "import sys; sys.stdout.write('x' * 2000001)"],
                tmp_path, None, 5, dict(os.environ))
