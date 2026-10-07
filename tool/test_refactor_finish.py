"""Finishing stages preserve test strength, resume receipts and ratchet limits."""
# ruff: noqa: F811 -- borrowed repository fixtures

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import debt
import improvement
from main import refactor, refactor_finish
from test_main import client, no_machine_settings  # noqa: F401
from test_refactor import Host, _git, finished, selected  # noqa: F401
from test_specs import Remote, repo  # noqa: F401


def worker(selected, kind="test_cleanup"):
    (selected / "test_fixture.json").write_text('{\n  "expected": [1, 2, 3]\n}\n', encoding="utf-8")
    (selected / "test_big.py").write_text("import big\nassert big.value_800 == 800\n", encoding="utf-8")
    (selected / "quality.py").write_text("# frozen quality control\n", encoding="utf-8")
    _git(selected, "add", ".")
    _git(selected, "commit", "-qm", "freeze tests")
    run = {"id": "finish01", "repo": selected.name, "scope": "project", "role": {"model": "", "effort": ""},
           "spent": {"seconds": 0, "calls": 0, "tokens": 0}, "phase": kind, "state": "running",
           "finish_version": 1, "tests": {"spec": "tests", "tests": ["test_big.py"],
                                        "test_argv": ["git", "--version"]},
           "steps": [{"n": 1, "kind": kind, "tier": "L1", "goal": "Verified finish", "state": "pending",
                      "spec": "finish", "base": "main", "files": ["test_big.py"]}]}
    improvement.atomic(refactor.file_of(selected.name, run["id"]), run)
    return refactor.Worker(selected, run), run


def compact(w, run, text, system, where):
    (where / "test_fixture.json").write_text('{"expected":[1,2,3]}\n', encoding="utf-8")
    return '```refactor-cleanup\n{"deferred": ["Snapshot serializer changes need mutation evidence"]}\n```'


def test_lossless_cleanup_records_evidence_and_never_repeats_a_verified_turn(selected):
    w, run = worker(selected)
    with patch.object(refactor, "turn", side_effect=compact) as turn:
        result = refactor_finish.candidate(w, run, run["steps"][0], selected)
        step = refactor.load(selected.name, run["id"])["steps"][0]
        assert not result["unchanged"] and step["after"]["bytes"] < step["before"]["bytes"]
        assert step["after"]["lines"] < step["before"]["lines"] and step["deferred"]
        assert refactor_finish.candidate(w, run, step, selected) == result
        assert turn.call_count == 1
        (selected / "test_big.py").write_text("assert True\n", encoding="utf-8")
        with pytest.raises(refactor.Stop, match="cleanup_changed"):
            refactor_finish.candidate(w, run, step, selected)
        _git(selected, "commit", "-qam", "weakened during review")
        with patch.object(refactor, "head_of", return_value=_git(selected, "rev-parse", "HEAD")), \
                patch.object(refactor, "live", return_value={}), pytest.raises(refactor.Stop, match="finish_review_changed"):
            refactor_finish.reviewed(w, "finish")


def test_document_only_review_revisions_do_not_replay_finishing(selected):
    w, run = worker(selected)
    with patch.object(refactor, "turn", side_effect=compact):
        refactor_finish.candidate(w, run, run["steps"][0], selected)
    (selected / "README.md").write_text("# Review notes\n", encoding="utf-8")
    _git(selected, "add", "README.md")
    _git(selected, "commit", "-qm", "document evidence")
    with patch.object(refactor, "head_of", return_value=_git(selected, "rev-parse", "HEAD")), \
            patch.object(refactor, "live", return_value={}):
        refactor_finish.reviewed(w, "finish")
    (selected / "test_expected.md").write_text("Approved output silently replaced\n", encoding="utf-8")
    _git(selected, "add", "test_expected.md")
    _git(selected, "commit", "-qm", "changed markdown fixture")
    with patch.object(refactor, "head_of", return_value=_git(selected, "rev-parse", "HEAD")), \
            patch.object(refactor, "live", return_value={}), pytest.raises(refactor.Stop, match="finish_review_changed"):
        refactor_finish.reviewed(w, "finish")


@pytest.mark.parametrize("bad", ["assertion", "snapshot", "production", "commit", "growth", "delete"])
def test_cleanup_rejects_weaker_or_out_of_scope_edits_and_preserves_them_for_inspection(selected, bad):
    w, run = worker(selected)
    def change(*args, **kwargs):
        compact(*args, **kwargs)
        if bad == "assertion":
            (selected / "test_big.py").write_text("assert True\n", encoding="utf-8")
        elif bad == "snapshot":
            (selected / "test_fixture.json").write_text('{"expected":[]}\n', encoding="utf-8")
        elif bad == "production":
            (selected / "big.py").write_text("value_800 = 1\n", encoding="utf-8")
        elif bad == "commit":
            _git(selected, "commit", "-qam", "unapproved commit")
        elif bad == "growth":
            (selected / "test_big.py").write_text("# redundant\n" * 100 + "import big\nassert big.value_800 == 800\n", encoding="utf-8")
        else:
            (selected / "test_big.py").unlink()
        return '```refactor-cleanup\n{"deferred": []}\n```'
    with patch.object(refactor, "turn", side_effect=change), pytest.raises(refactor.Stop):
        refactor_finish.candidate(w, run, run["steps"][0], selected)
    assert not refactor.load(selected.name, run["id"])["steps"][0].get("verified_head")
    with patch.object(refactor, "turn") as turn, pytest.raises(refactor.Stop):
        step = refactor.load(selected.name, run["id"])["steps"][0]
        refactor_finish.candidate(w, run, step, selected)
    assert not turn.called, "resume never measures an unverified dirty patch as the new baseline"


@pytest.mark.parametrize("after", ["same", "survived", "missing", "skipped", "control", "incomplete"])
def test_structural_cleanup_requires_fixed_per_test_and_per_mutant_evidence(selected, after):
    w, run = worker(selected)
    settings = {"test_quality_cmd": "quality", "test_quality_files": '["quality.py"]'}
    baseline = {"complete": True, "tests": {"case": "passed"}, "mutants": {"off-by-one": "killed"}}
    result = json.loads(json.dumps(baseline))
    if after == "survived":
        result["mutants"]["off-by-one"] = "survived"
    elif after == "missing":
        result["tests"] = {}
    elif after == "skipped":
        result["tests"]["case"] = "skipped"
    elif after == "incomplete":
        result["complete"] = False
    receipts = iter([baseline, result])
    real = refactor_finish.refactor_profile.sh
    def command(argv, path, **kwargs):
        if argv == ["quality"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(next(receipts)), "")
        return real(argv, path, **kwargs)
    def factored(*args, **kwargs):
        answer = compact(*args, **kwargs)
        (selected / "test_big.py").write_text("import big\nx = big.value_800\nassert x == 800\n", encoding="utf-8")
        if after == "control":
            (selected / "quality.py").write_text("# forged receipt\n", encoding="utf-8")
        return answer
    with patch.object(refactor_finish, "slots_for", return_value=settings), \
            patch.object(refactor_finish.refactor_profile, "sh", side_effect=command), \
            patch.object(refactor, "turn", side_effect=factored):
        if after == "same":
            refactor_finish.candidate(w, run, run["steps"][0], selected)
            step = refactor.load(selected.name, run["id"])["steps"][0]
            assert step["quality_before"] == step["quality_after"] == {k: baseline[k] for k in ("tests", "mutants")}
        else:
            with pytest.raises(refactor.Stop):
                refactor_finish.candidate(w, run, run["steps"][0], selected)


def test_ratchet_adopts_missing_baselines_and_refuses_to_adopt_growth(selected):
    w, run = worker(selected, "ratchet")
    result = refactor_finish.candidate(w, run, run["steps"][0], selected)
    step = refactor.load(selected.name, run["id"])["steps"][0]
    assert step["action"] == "adopted" and step["checked"] and not result["unchanged"]
    assert debt.check(selected)[0] == []
    assert _git(selected, "ls-files", debt.RATCHET) == debt.RATCHET
    (selected / "big.py").write_text((selected / "big.py").read_text(encoding="utf-8") + "more = 1\n", encoding="utf-8")
    _git(selected, "commit", "-qam", "growth")
    baseline = (selected / debt.RATCHET).read_bytes()
    step.pop("verified_head")
    step.pop("before_head")
    with pytest.raises(refactor.Stop, match="ratchet"):
        refactor_finish.candidate(w, run, step, selected)
    assert (selected / debt.RATCHET).read_bytes() == baseline


def test_the_real_workflow_finishes_in_order_and_legacy_runs_keep_their_contract(selected):
    api = client()
    def adopted(path, *args, **kwargs):
        return {"state": "adopted", "commit": _git(path, "rev-parse", "HEAD"), "branch": "none"}
    def reviewed(repo, sid, automatic=False):
        from main import specs
        specs.update(repo, sid, state="머지 가능")
    from main import loop
    with patch.object(refactor_finish.refactor_profile, "drive", side_effect=adopted), \
            patch.object(refactor_finish.specs, "sh", side_effect=Remote()), patch.object(loop, "kick", side_effect=reviewed):
        rid = api.post("/api/refactors", json={"request_id": "finish-in-order", "mode": "cleanup"}).json()["id"]
        run = finished(api, rid)
    assert run["state"] == "done", run.get("stopped")
    assert [s.get("kind") for s in run["steps"]] == [None, "test_cleanup", "ratchet"]
    assert run["steps"][-2]["unchanged"] and run["steps"][-2]["spec"] is None
    assert run["steps"][-1]["action"] == "adopted" and run["steps"][-1]["pr"]
    assert refactor_finish.schedule(run) == run["steps"], "scheduling is idempotent"
    assert refactor_finish.schedule({"steps": []}) == [], "old contracts are not retroactively replayed"


def test_quality_and_equivalence_fail_closed():
    assert refactor_finish.equivalent("tests/data.json", b'{"x": true}', b'{"x": 1}') is False
    assert refactor_finish.equivalent("tests/data.json", b'{"x": 1, "x": 2}', b'{"x": 2}') is False
    assert refactor_finish.equivalent("tests/data.json", b'{"x": 1.0000000000000000001}', b'{"x": 1.0}') is False
    assert refactor_finish.equivalent("tests/data.json", b'{"x": 1.0}', b'{"x": "1.0"}') is False
    assert refactor_finish.equivalent("tests/data.json", b'{"x": -0}', b'{"x": 0}') is False
    assert refactor_finish.equivalent("tests/data.json", b'{"x": 1}', b'{"x": ["number", "1"]}') is False
    assert refactor_finish.equivalent("tests/data.json", b'{"x": NaN}', b'{"x": null}') is False
    assert refactor_finish.equivalent("tests/data.json", b'{"x": 1, "y": [2]}', b'{"y": [2], "x": 1}') is True
    assert refactor_finish.equivalent("tests/ui.snap", b"expect text", b"expect") is False
    assert refactor_finish.equivalent("test_a.py", b"assert x == 1\n", b"assert True\n") is False
    assert refactor_finish.equivalent("test_a.py", b"assert x == 1\n", b"# kept\nassert (x == 1)\n") is True
    w = SimpleNamespace(halt=None)
    with patch.object(refactor_finish.refactor_profile, "sh", return_value=subprocess.CompletedProcess([], 0, "{}", "")), \
            pytest.raises(refactor.Stop, match="test_quality"):
        refactor_finish.quality(w, "runner", None)
