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
    code = ("import json, os, sys; r = json.load(sys.stdin); "
            "assert r['stage'] == 'task' and r['id'] in ('a', 'b') and r['trial'] in (0, 1); "
            "assert r['split'] == 'evolve' and r['model'] == 'fixed-policy-v1'; "
            "assert r['limits']['calls'] == 2 and r['limits']['tokens'] == 30; "
            "assert 0 < r['limits']['seconds'] <= 5; "
            "assert os.environ['WIKI_IMPROVEMENT_MODEL'] == 'fixed-policy-v1'; "
            "print(json.dumps({'reward': 0.8, 'usage': {'calls': 2, 'tokens': 30}}))")
    task = {"argv": [sys.executable, "-c", code], "inference": True, "seconds": 5,
            "max_usage": {"calls": 2, "tokens": 30}}
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {"a": task, "b": task},
                "guards": {"evolve": {"integrity": []}}}
    result = evaluate(request(tmp_path), manifest, tmp_path)
    assert result["usage"] == {"calls": 8, "tokens": 120}
    assert all(r["tokens"] == 30 and r["reward"] == .8 for r in result["trials"])
    assert len(list((tmp_path / "cache").glob("*/*"))) == 4
    assert json.dumps(result)


def test_inference_usage_is_not_assumed_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_IMPROVEMENT_CACHE", str(tmp_path / "cache"))
    task = {"argv": [sys.executable, "-c", "print('{}')"], "inference": True, "seconds": 5,
            "max_usage": {"calls": 1, "tokens": 1}}
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


@pytest.mark.parametrize("cap", [None, {}, {"calls": True, "tokens": 1}, {"calls": 0, "tokens": 1}])
def test_inference_requires_enforced_caps_before_launch(tmp_path, monkeypatch, cap):
    import improvement_evaluate

    monkeypatch.setattr(improvement_evaluate, "execute", lambda *args: pytest.fail("Must not launch"))
    task = {"argv": [sys.executable, "-c", "print('{}')"], "inference": True, "seconds": 5, "max_usage": cap}
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {"a": task},
                "guards": {"evolve": {"integrity": []}}}
    with pytest.raises(ValueError, match="usage ceiling"):
        evaluate({**request(tmp_path), "ids": ["a"]}, manifest, tmp_path)


@pytest.mark.parametrize("key", ["calls", "tokens"])
def test_trial_cannot_start_without_its_whole_usage_allowance(tmp_path, monkeypatch, key):
    import improvement_evaluate

    monkeypatch.setattr(improvement_evaluate, "execute", lambda *args: pytest.fail("Must not launch"))
    task = {"argv": [sys.executable, "-c", "print('{}')"], "inference": True, "seconds": 5,
            "max_usage": {"calls": 2, "tokens": 2}}
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {"a": task},
                "guards": {"evolve": {"integrity": []}}}
    data = request(tmp_path)
    data["limits"][key] = 1
    result = evaluate({**data, "ids": ["a"]}, manifest, tmp_path)
    assert "cannot cover" in result["error"]
    assert result["usage"] == {"calls": 0, "tokens": 0}
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("actual", [1, 2])
def test_exact_budget_or_violation_never_starts_another_trial(tmp_path, monkeypatch, actual):
    import improvement_evaluate

    monkeypatch.setenv("WIKI_IMPROVEMENT_CACHE", str(tmp_path / "cache"))
    launches = []

    def counted(*args):
        launches.append(json.loads(args[2]))
        return 0, json.dumps({"reward": 1, "usage": {"calls": actual, "tokens": actual}}).encode("utf-8"), ""

    monkeypatch.setattr(improvement_evaluate, "execute", counted)
    task = {"argv": [sys.executable, "-c", "print('{}')"], "inference": True, "seconds": 5,
            "max_usage": {"calls": 1, "tokens": 1}}
    manifest = {"schema": "wiki-improvement-tasks/1", "tasks": {"a": task},
                "guards": {"evolve": {"integrity": []}}}
    data = {**request(tmp_path), "ids": ["a"], "limits": {"seconds": 20, "calls": 1, "tokens": 1}}
    result = evaluate(data, manifest, tmp_path)
    assert len(launches) == 1
    assert result["usage"] == {"calls": actual, "tokens": actual}
    assert ("cannot cover" if actual == 1 else "violated") in result["error"]
    assert len(list((tmp_path / "cache").glob("*/*"))) == 1
