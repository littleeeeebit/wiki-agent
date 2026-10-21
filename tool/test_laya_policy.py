"""Laya's per-kind rules (`python tool/eval/policy.py --laya`) from notebook result zips."""

import json
import zipfile

import pytest

from eval import policy


def result(tmp_path, kind="route", rows=None, checkpoint=True, predictions=True):
    rows = rows if rows is not None else (
        [{"state_hash": f"y{i}", "question": f"{kind}_q", "expected": True, "chance": 0.95} for i in range(6)]
        + [{"state_hash": f"n{i}", "question": f"{kind}_q", "expected": False, "chance": 0.9} for i in range(6)])
    run = {"kind": kind, "started": "20261021-000000", "chosen": "fine-tuned-e3-ls0.1",
           "checkpoint_zip": f"/content/drive/MyDrive/laya/{kind}-train-e3-ls0.1-20261021-000000-ab.zip"
           if checkpoint else None,
           "env": {"laya": "0.4.6", "packed": {"commit": "c0ffee", "splits": {kind: {"calibration": "cal-hash"}}}}}
    path = tmp_path / f"result-{kind}.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("results.json", json.dumps(run))
        z.writestr("e3-ls0.1-calibration.json", json.dumps({"temperature": [1.0, 1.0, 1.7]}))
        if predictions:
            z.writestr("calibration-predictions.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_a_laya_kind_is_fitted_on_its_own_calibration_and_bound_to_its_checkpoint(tmp_path):
    out = policy.laya_artifact(result(tmp_path))
    # `chance` is the probability of the expected answer, so a confident no is P(yes) 0.1.
    assert out["rule"] and out["errors"]["counts"] == {"positive_yes": 6, "negative_no": 6}
    assert out["model"] == "route-train-e3-ls0.1-20261021-000000-ab.zip" and out["commit"] == "c0ffee"
    assert out["dataset"] == {"split": "calibration", "sha256": "cal-hash", "questions": 12}
    assert out["temperature"] == {"temperature": [1.0, 1.0, 1.7]} and out["costs"] == policy.COSTS["route"]
    # Analysis is fitted with the costs Jev's arm D was compared on; a kind with no costs keeps no rule.
    assert policy.laya_artifact(result(tmp_path, "analysis"))["costs"] == policy.ANALYSIS_COSTS
    ask = policy.laya_artifact(result(tmp_path, "ask"))
    assert ask["rule"] is None and ask["errors"]["why"] == "no cost table for this kind"
    with pytest.raises(ValueError, match="not a Noul kind"):
        policy.laya_artifact(result(tmp_path, "support", rows=[{"state_hash": "s", "question": "q",
                                                                "expected": "all", "chance": 0.5}]))
    for missing in ({"checkpoint": False}, {"predictions": False}):
        with pytest.raises(ValueError, match="no saved checkpoint or calibration predictions"):
            policy.laya_artifact(result(tmp_path, **missing))

    # The command writes one artifact per kind.
    assert policy.main(["--laya", str(result(tmp_path)), str(result(tmp_path, "useful")),
                        "--out", str(tmp_path / "out")]) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["route.json", "useful.json"]
    assert json.loads((tmp_path / "out" / "useful.json").read_text(encoding="utf-8"))["schema"] == "laya-policy/1"
