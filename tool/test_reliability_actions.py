"""Focused checks split from tool/test_evaluation.py."""

import json
from unittest.mock import patch
import pytest
from eval import compare, report
from main import decisions
from test_evaluation import RELIABLE, action_result


def test_the_reliability_actions_read_their_own_fixtures(tmp_path, monkeypatch):
    env = tmp_path / "jev.env"
    env.write_text("TYPESAFE_API_KEY=test-key\nWIKI_JEV_MODE=active\n", encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(env))
    fixtures = json.loads((RELIABLE / "actions.json").read_text(encoding="utf-8"))
    labels = {f["id"]: f["label"] for f in fixtures["fixtures"]}

    def transport(cfg):
        def evaluate(state, questions, trace, budget, stage):
            budget.call()
            options = list(questions["action"]["criteria"])
            pick = options[0]
            return action_result(options, pick, trace, stage)
        return evaluate

    folder = tmp_path / "actions"
    with patch.object(decisions, "transport", transport):
        assert compare.main([str(folder), "--experiment", "actions", "--dataset", str(RELIABLE / "intents.json"),
                             "--actions", str(RELIABLE / "actions.json")]) == 0
    rows = compare.rows(folder, "actions")
    assert len(rows) == 18 and {r["key"] for r in rows} <= set(labels)
    got = report.build([folder])
    measured = {g["id"]: g for g in got["gates"]}
    assert all(measured[g]["detail"].get("n") == 6 for g in ("action_work_start", "action_specs_check",
                                                             "action_loop_fix"))
    assert measured["decision_quality"]["value"] == got["actions"]["selected_right_rate"]
    # Fixtures that moved since the run are not the ones measured.
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    run["options"]["fixtures"]["sha256"] = "0" * 64
    (folder / "run.json").write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(SystemExit, match="not the ones its manifest names"):
        report.build([folder])
