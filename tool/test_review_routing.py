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
    # A record frozen under an earlier prompt is asked again only with --rebuild, and the row names the
    # request actually sent, keeping the record's own identity apart.
    earlier = copy.deepcopy(case)
    frozen = earlier["record"]
    next(iter(frozen["request"]["questions"].values()))["question"]["instructions"] = "An earlier prompt."
    frozen["request"]["prompt_version"] = "review-contract-shadow-2:earlier"
    frozen["frozen_digest"] = verification.sha({"request": frozen["request"], "result": frozen["result"]})
    cfg = decision.Config("active", "test", "default", key="test")
    with patch.object(decision, "evaluate", side_effect=live):
        with pytest.raises(ValueError, match="differs"):
            production.sample(earlier, cfg)
        rebuilt = production.sample(earlier, cfg, rebuild=True)
    assert (rebuilt["source_prompt_version"], rebuilt["prompt_version"]) == ("review-contract-shadow-2:earlier", version)
    assert rebuilt["request"]["questions"] == qs
    assert rebuilt["context_digest"] == verification.sha(rebuilt["request"]["state_en"])
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


def test_a_rebuild_that_only_fits_the_state_records_the_state_it_sent():
    """Same questions, a shorter diff: the row names what was asked, not the frozen context."""
    from decision.contract import result

    smile = chr(0x1F600)
    state = {"baseline": {"criteria": ["code"], "evidence": ["offline"]}, "selected_flows": [],
             "registered_flows": [], "observations": {}, "goal": "Implement emoji support " + smile * 8500}
    state["grounds"] = [{"id": "s", "kind": "spec", "text": state["goal"]}, {"id": "d", "kind": "diff", "text": smile * 6000}]
    qs, version = contract.shadow_questions(state)
    req = decision.request("action", state, qs, allowed=["add", "skip", "s", "d"], model="test", prompt_version=version,
        policy_version=contract.POLICY.version, normalization_version="english",
        budget=contract.Budget(**contract.SHADOW_LIMITS))
    res = result(req, "unavailable", "state_too_large")
    record = {"request": req, "result": res, "input_identity": "fixture", "status": res["status"],
              "policy": contract.POLICY.record(), "context_digest": verification.sha(state),
              "frozen_digest": verification.sha({"request": req, "result": res})}
    sent = []

    def answers(cfg, state, questions, *rest):
        sent.append(copy.deepcopy(state))
        pick = lambda cid: "s" if cid.startswith("basis:") else "skip"  # noqa: E731
        return {cid: {"choice": pick(cid), "confidence": 0.9, "probabilities": {
            k: 0.9 if k == pick(cid) else 0.1 / (len(q["criteria"]) - 1) for k in q["criteria"]}}
            for cid, q in questions.items()}

    case = {"id": "fit", "family": "fit", "split": "heldout", "record": record}
    with patch.object(decision, "evaluate", side_effect=answers):
        row = production.sample(case, decision.Config("active", "test", "default", key="test"), rebuild=True)
    assert contract.shadow_questions(sent[0])[1] == version and sent[0] != state
    assert row["context_digest"] == verification.sha(sent[0]) != row["source_context_digest"]
    assert row["request"]["state_en"] == sent[0]


def test_one_cut_off_label_reply_keeps_the_other_paid_answers(tmp_path):
    import http.client

    from eval import review_shadow_labels as labeller

    class CutOff:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            raise http.client.IncompleteRead(b"{", 100)

    offered = {"criteria:async": ("a", "b")}
    with patch.object(labeller, "candidates", return_value=offered), patch.object(labeller, "prompt", return_value="x"), \
         patch.object(labeller.urllib.request, "urlopen", return_value=CutOff()), patch.object(labeller.time, "sleep"):
        assert labeller.answered("fixture-key", {})["error"].startswith("transport: IncompleteRead")

    def ask(key, request):
        if not request["ok"]:
            raise StopIteration   # a reply without the expected text block
        return {"labels": {"criteria:async": {"id": "criteria:async", "label": "required", "reason": "Fixture."}},
                "usage": {"input_tokens": 1, "output_tokens": 1}}

    state = {"baseline": {"criteria": ["code"], "evidence": ["offline"]}, "selected_flows": []}
    records = [("r", "ok", {"attempt_id": "a", "request": {"ok": True, "state_en": state}}),
               ("r", "bad", {"attempt_id": "b", "request": {"ok": False, "state_en": state}})]
    out = tmp_path / "labels.json"
    with patch.object(labeller.settings, "pick", return_value=("fixture-key", "fixture")), \
         patch.object(labeller, "records", return_value=records), patch.object(labeller, "candidates", return_value=offered), \
         patch.object(contract, "replay_shadow"), patch.object(labeller, "ask", side_effect=ask):
        assert labeller.main(["--out", str(out)]) == 1
    written = json.loads(out.read_text(encoding="utf-8"))
    assert [c["family"] for c in written["cases"]] == ["ok"]
    assert [f["spec"] for f in written["labels"]["failed"]] == ["bad"]
