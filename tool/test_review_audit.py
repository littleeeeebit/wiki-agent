"""Pure shadow-audit contracts; Git, collectors and persistence have integration witnesses."""

import copy
import subprocess

import decision
import pytest
from main import review_contract as contract, verification
from test_review_contract import assert_grounded, audit_record


@pytest.fixture
def audit_case(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure audit composition must not start a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    spec = {"id": "audit", "repo": "fixture", "rev": 1, "goal": "Keep shadow advice non-authoritative.",
            "done": ["Preserve mandatory obligations."], "worktree": str(tmp_path), "pr": {"base": "main"}}
    # Synthetic identities are unit inputs, never proof of a reviewed/executed revision.
    baseline = contract.select(tmp_path, tmp_path, spec, "code", ["code.py"], "a" * 40, "b" * 40)
    return tmp_path, spec, baseline


@pytest.fixture
def offered_flow():
    return {"id": "health", "title": "Service health", "kind": "api",
            "paths": ["*.py", "api-contract.md", "verification.json"],
            "environments": ["api", "dataset", "settings"],
            "assertions": [{"id": "healthy", "expected": "API reports healthy"}]}


@pytest.mark.parametrize("added,criteria,evidence", [
    ("criteria:refactor", ["code", "refactor"], ["differential", "offline"]),
    ("evidence:differential", ["code", "refactor"], ["differential", "offline"]),
    ("evidence:browser", ["code"], ["api", "browser", "offline"]),
    ("evidence:desktop", ["code"], ["desktop", "offline"]),
    ("criteria:performance", ["code", "performance"], ["offline"]),
])
def test_shadow_closure_is_grounded_unresolved_and_never_enforced(audit_case, added, criteria, evidence):
    path, spec, baseline = audit_case
    result = contract.compose(baseline, audit_record(baseline, [added]))
    assert result["candidate"]["criteria"] == criteria and result["candidate"]["evidence"] == evidence
    assert result["enforced"] == baseline["enforced"] and result["digest"] == baseline["digest"]
    assert result["unresolved"] and all(r["scope"] == "candidate" for r in result["unresolved"])
    assert not contract.ready(path, path, spec, result)
    assert contract.matches(spec, {"review_contract": baseline}, result)
    assert_grounded(result)


@pytest.mark.parametrize("kind", ["api", "browser", "command", "desktop"])
def test_candidate_registered_flow_closure_and_assertion_origins(audit_case, offered_flow, kind):
    path, spec, baseline = audit_case
    flow = copy.deepcopy(offered_flow)
    flow["kind"] = kind
    observation = audit_record(baseline, ["flow:health"], flows=[flow])
    # Candidate manifest identity is an audit input even without mandatory runtime declarations.
    observation["request"]["state_en"]["manifest"]["digest"] = verification.sha({"flows": [flow]})
    observation["context_digest"] = verification.sha(observation["request"]["state_en"])
    observation["frozen_digest"] = verification.sha({"request": observation["request"], "result": observation["result"]})
    result = contract.compose(baseline, observation)
    expected = {"browser": ["api", "browser", "offline"], "api": ["api", "offline"],
                "desktop": ["desktop", "offline"], "command": ["offline"]}[kind]
    assert result["candidate"]["flows"] == ["health"] and result["candidate"]["evidence"] == expected
    assert result["flows"] == [] and result["enforced"]["flows"] == [] and not result["problems"]
    row = next(r for r in result["items"] if r["id"] == "flow:health")
    assert any(g.get("assertions") == ["healthy"] for g in row["grounds"])
    assert_grounded(result)


@pytest.mark.parametrize("mode", ["unsupported", "defer", "uncertain", "stale", "tampered", "unknown", "source"])
def test_candidate_rejections_preserve_mandatory_floor_and_reasons(audit_case, mode):
    path, spec, baseline = audit_case
    spec["review"] = {"criteria": ["security"]}
    baseline = contract.select(path, path, spec, "code", ["code.py"], baseline["head"], baseline["base_oid"])
    rec = audit_record(baseline, ["criteria:async"], choice=decision.DEFER if mode == "defer" else "add",
                       confidence=0.4 if mode == "uncertain" else 0.9,
                       unsupported=["criteria:async"] if mode == "unsupported" else [])
    if mode == "stale":
        rec["status"] = "stale"
    elif mode == "tampered":
        rec["request"]["state_en"]["grounds"][0]["id"] = "ground:invented"
    elif mode == "source":
        rec["request"]["state_en"]["grounds"][0]["id"] = "ground:other"
        rec["context_digest"] = verification.sha(rec["request"]["state_en"])
        rec["frozen_digest"] = verification.sha({"request": rec["request"], "result": rec["result"]})
    elif mode == "unknown":
        # A self-consistent frozen request is still not authority to register a candidate.
        rec["request"]["questions"]["criteria:invented"] = rec["request"]["questions"].pop("criteria:async")
        rec["request"]["questions"]["basis:criteria:invented"] = rec["request"]["questions"].pop("basis:criteria:async")
        for key in ("answers", "verdicts"):
            rec["result"][key]["criteria:invented"] = rec["result"][key].pop("criteria:async")
            rec["result"][key]["basis:criteria:invented"] = rec["result"][key].pop("basis:criteria:async")
        rec["frozen_digest"] = verification.sha({"request": rec["request"], "result": rec["result"]})
    result = contract.compose(baseline, rec)
    assert result["candidate"] == result["enforced"] == baseline["enforced"]
    assert result["digest"] == baseline["digest"] and result["dispositions"]
    assert any(r["reason"] for r in result["dispositions"])
    assert all(r["scope"] == "candidate" for r in result["unresolved"])


def test_candidate_probability_and_optional_defer_do_not_change_execution_identity(audit_case):
    path, spec, baseline = audit_case
    results = [contract.compose(baseline, audit_record(baseline, ["criteria:async"], **kw))
               for kw in ({}, {"confidence": 0.8}, {"choice": decision.DEFER}, {"choice": "skip"})]
    assert {r["digest"] for r in results} == {baseline["digest"]}
    assert len({r["candidate_digest"] for r in results}) == 4
    assert all(not contract.ready(path, path, spec, r) for r in results)


@pytest.mark.parametrize("malformed", ["command", "assertions", "kind"])
def test_malformed_offered_catalog_is_rejected_before_dependency_closure(audit_case, offered_flow, malformed):
    path, spec, baseline = audit_case
    flow = copy.deepcopy(offered_flow)
    observation = audit_record(baseline, ["flow:health"], flows=[flow])
    flow[malformed] = {"command": "unregistered execution", "assertions": [*flow["assertions"], *flow["assertions"]],
                       "kind": "invented"}[malformed]
    observation["context_digest"] = verification.sha(observation["request"]["state_en"])
    observation["frozen_digest"] = verification.sha({"request": observation["request"], "result": observation["result"]})
    result = contract.compose(baseline, observation)
    assert result["candidate"] == result["enforced"] == baseline["enforced"]
    assert result["digest"] == baseline["digest"] and result["dispositions"][-1]["disposition"] == "rejected"
    assert result["dispositions"][-1]["reason"] == "invalid offered flow catalog"
