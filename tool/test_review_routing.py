"""Synthetic selection checks; no network, reviewer, shell executor or repository writes."""

import copy
import json
from unittest.mock import patch

import decision
import pytest
from eval import review_routing as routing


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
