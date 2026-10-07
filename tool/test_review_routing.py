"""Synthetic selection checks; no network, reviewer, shell executor or repository writes."""

import copy
import json
import subprocess
import sys
from unittest.mock import patch

import decision
import pytest
from eval import review_routing as routing
from eval import review_shadow as production
from main import review_contract as contract, verification


def cases():
    return {c["id"]: c for c in routing.load(routing.DATASET)["cases"]}


def test_dataset_is_grouped_and_expected_labels_never_reach_jev():
    data = routing.load(routing.DATASET)
    assert len(data["cases"]) == 48
    assert sum(c["split"] == "heldout" for c in data["cases"]) == 32
    assert data["labels"]["reviewed_by"] is None
    fresh = routing.load(routing.HUB / "eval/jev/review-routing-validation.json")
    assert len(fresh["cases"]) == 32 and all(c["split"] == "heldout" for c in fresh["cases"])
    assert not {c["group"] for c in data["cases"]} & {c["group"] for c in fresh["cases"]}
    case = cases()["refactor-api-incomplete"]
    sent = []

    def unavailable(cfg, state, questions, trace, budget, stage):
        sent.append(state)
        raise decision.JevError("unavailable")

    with patch.object(decision, "evaluate", unavailable):
        row = routing.sample(case, decision.Config("active", "test", "default", key="test"), "normal")
    assert sent == [case["state"]]
    assert not {"expected", "rationale", "id", "split", "group"} & sent[0].keys()
    assert row["decision"]["status"] == "unavailable"
    assert row["guarded"]["next"] == "wait_evidence"
    assert row["guarded"]["evidence"] == ["api", "differential", "offline"]


def test_synthetic_guard_preserves_obligations_and_measures_identity():
    for cid in ("refactor-api-incomplete", "api-persistence-incomplete", "native-window-incomplete",
                "cloud-source-not-evidence-incomplete", "environment-missing-complete",
                "ambiguous-scope-complete", "recurring-invariant-complete"):
        case = cases()[cid]
        unsafe = {"criteria": [], "evidence": [], "next": "review"}
        guarded = routing.compose(case["state"], unsafe, [])
        assert guarded["next"] == case["expected"]["next"], cid
        assert "offline" in guarded["evidence"]
    state = cases()["refactor-local-complete"]["state"]
    for field in ("head", "base", "spec_revision", "environment", "observed", "passed"):
        changed = copy.deepcopy(state)
        changed["receipts"][-1][field] = False if field in ("observed", "passed") else "stale"
        assert routing.next_step(changed, ["offline", "differential"]) == "wait_evidence"
    inferred = routing.compose(cases()["hidden-refactor-intent-incomplete"]["state"],
                               {"criteria": ["refactor"], "evidence": [], "next": "review"}, [])
    assert inferred["next"] == "wait_evidence" and inferred["criteria"] == ["code", "refactor"]
    assert routing.compose(state, {"criteria": [], "evidence": [], "next": "review"}, ["next"])["next"] == "clarify"


def test_option_renaming_and_order_preserve_meaning_and_labels_resolve_offline(tmp_path):
    normal, mapping = routing.questions("normal")
    for variant in routing.VARIANTS:
        questions, changed = routing.questions(variant)
        assert questions.keys() == normal.keys()
        for name, q in questions.items():
            assert sorted(q["question"]["criteria"].values()) == sorted(normal[name]["question"]["criteria"].values())
            assert sorted(changed[name].values()) == sorted(mapping[name].values())
    case = cases()["plan-only-complete"]
    assert routing.current(case["state"]) == ["plan"]
    assert routing.current(cases()["mixed-plan-code-complete"]["state"]) == ["code", "plan"]
    out = tmp_path / "result.json"
    with patch.object(decision, "evaluate", side_effect=AssertionError("offline must not send")):
        assert routing.main(["--out", str(out)]) == 0
    assert not out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(out.read_text(encoding="utf-8"))["usage"]["requests"] == 0
    broken = routing.load(routing.DATASET)
    broken["cases"][1]["split"] = "heldout"
    path = tmp_path / "leaked.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="leaks"):
        routing.load(path)


def test_production_prompt_replay_isolated_labels_frozen_families_and_live_request(tmp_path):
    state = {"goal": "Preserve a local API caller's public behavior", "done": ["Keep frozen caller outputs"],
        "out": [], "paths": ["src/client.py"], "baseline": {"profile": "code", "criteria": ["code"],
        "evidence": ["offline"], "problems": []}, "registered_flows": [], "selected_flows": [],
        "grounds": [{"id": "ground:spec", "kind": "spec", "text": "Behavior preservation is required."}]}
    qs, version = contract.shadow_questions(state)
    budget = contract.Budget(**contract.SHADOW_LIMITS)
    req = decision.request("action", state, qs, allowed=["add", "skip", "ground:spec"], model="test",
        prompt_version=version, policy_version=contract.POLICY.version, normalization_version="english", budget=budget)

    def answers(state, questions, trace, budget, stage):
        out = {}
        for name, q in questions.items():
            pick = "ground:spec" if name.startswith("basis:") else (
                "add" if name in ("criteria:refactor", "evidence:differential") else "skip")
            out[name] = {"choice": pick, "confidence": 0.9,
                        "probabilities": {k: 0.9 if k == pick else 0.1 / (len(q["criteria"]) - 1)
                                          for k in q["criteria"]}}
        return out

    res = decision.decide(req, answers, budget, [], contract.POLICY)
    record = {"request": req, "result": res, "status": res["status"], "input_identity": "fixture",
        "context_digest": verification.sha(state), "frozen_digest": verification.sha({"request": req, "result": res}),
        "policy": contract.POLICY.record()}
    case = {"id": "preserve", "family": "local-preservation", "split": "calibration", "record": record,
        "rationale": "LABEL MUST NOT REACH THE PROVIDER", "expected": {"criteria": ["code", "refactor"],
        "evidence": ["offline", "differential"], "flows": [], "unresolved": [], "prohibited": ["evidence:desktop"],
        "acceptable_extras": []}}
    with patch.object(decision, "evaluate", side_effect=AssertionError("offline cannot send")):
        row = production.sample(case)
    assert row["score"]["exact_sets"] and not row["score"]["prohibited"]
    sent = []

    def live(cfg, state, questions, *rest):
        sent.append((state, questions))
        return answers(state, questions, *rest)

    with patch.object(decision, "evaluate", side_effect=live):
        live_row = production.sample(case, decision.Config("active", "test", "default", key="test"))
    assert live_row["predicted"] == row["predicted"] and sent == [(state, {k: v["question"] for k, v in qs.items()})]
    path = tmp_path / "evaluation.json"
    data = {"schema": "review-shadow-evaluation/1", "labels": {"reviewed_by": None}, "cases": [case]}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert production.load(path)["cases"] == [case]
    out = tmp_path / "replayed.json"
    with patch.object(decision, "evaluate", side_effect=AssertionError("offline cannot send")):
        assert production.main(["--dataset", str(path), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["usage"]["requests"] == 0
    assert not out.read_bytes().startswith(b"\xef\xbb\xbf")
    cli = subprocess.run([sys.executable, production.__file__, "--dataset", str(path), "--out", str(out)],
                         capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert cli.returncode == 0, cli.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["usage"]["requests"] == 0
    frozen = path.read_bytes()
    with pytest.raises(SystemExit):
        production.main(["--dataset", str(path), "--out", str(path)])
    assert path.read_bytes() == frozen
    data["cases"].append({**case, "id": "leaked", "split": "heldout"})
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="leaks"):
        production.load(path)
