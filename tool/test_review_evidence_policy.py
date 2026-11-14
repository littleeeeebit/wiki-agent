"""Local evidence obligations reach independent review without inventing execution flows."""

# ruff: noqa: F811 — pytest injects shared fixtures by name.

import copy
import json
from pathlib import Path

import pytest

from main import loop, review_contract as contract, specs, verification, work
from test_loop import CLEANED, Reviewer, Worker, allow, commit, deny, git, looped, no_machine_settings, order, pr_spec, template, world  # noqa: F401
from test_local_verification import cloud_world, git_world  # noqa: F401
from test_review_contract import selected

REQUIRED = {"evidence": ["api", "browser", "desktop"]}


@pytest.mark.parametrize("state", ["고치는 중 R1", "멈춤"])
@pytest.mark.parametrize("shape", ["valid", "failed", "malformed", "absent"])
def test_repair_report_capture_cannot_authorize_workflow_transitions(world, monkeypatch, state, shape):
    spec = pr_spec(world, "report-authority", 7, "code.py", review=REQUIRED, state=state)
    path = Path(spec["worktree"])
    run = work.Run(Worker(path), person=True)
    monkeypatch.setattr(specs, "rounded", lambda *a, **k: pytest.fail("Report capture cannot execute a gate"))
    monkeypatch.setattr(specs, "pr_turn", lambda *a, **k: pytest.fail("Report capture cannot publish"))
    value = [{"item": spec["done"][0], "pass": shape != "failed", "evidence": "Observed runtime behavior"}]
    answer = "No completion report" if shape == "absent" else "```done-report\n" + ("{" if shape == "malformed" else json.dumps(value)) + "\n```"
    assert specs.check(path, run, answer) is None
    current = specs.load(spec["repo"], spec["id"])
    assert current["state"] == state and current["gate"] == spec["gate"] and not current["rounds"]
    if shape in {"valid", "failed"}:
        assert current["report"] == value and current["report_at"]["turn"] == run.turn
    else:
        assert current["report"] is None
    assert bool(current["fault"]) == (shape == "malformed")


@pytest.mark.parametrize("environment", [None, "external"])
@pytest.mark.parametrize("cleanup_followup", [False, True])
def test_repair_report_reaches_the_next_frozen_reviewer_handoff(world, environment, cleanup_followup):
    spec = pr_spec(world, "repair-report", 7, "code.py", review=REQUIRED, implementation_environment=environment)
    spec = specs.update(spec["repo"], spec["id"], report=[{"item": spec["done"][0], "pass": True,
                        "evidence": "Earlier observations"}], report_at={"head": spec["pr"]["head"], "rev": spec["rev"]})
    finding = "[P1] code.py:1 — Preserve runtime observations through repair completion"
    proof = {}
    def repair(path, halt):
        head = commit(path, "repair.py")
        proof.update(head=head, environment_id="isolated-desktop", installer_sha256="1" * 64,
                     requests=[{"method": "GET", "path": "/health", "status": 200}],
                     assertions=[{"expected": "Settings save survives reload", "actual": "Settings save survives reload"}])
        report = [{"item": spec["done"][0], "pass": True, "evidence": json.dumps(proof) + " token=fixture-value"}]
        return "```done-report\n" + json.dumps(report) + "\n```\n" + ("" if cleanup_followup else CLEANED) + disposition
    def inspect(first, text):
        frozen = order(world, 7, 2)
        assert "Current-head implementation report" in frozen
        assert all(value in frozen for value in (proof["head"], "isolated-desktop", "Settings save survives reload", "1" * 64))
        assert "Earlier observations" not in frozen and "fixture-value" not in frozen
        return allow(first, text)
    disposition = "```disposition\n" + json.dumps([{"finding": finding, "action": "fixed", "evidence": "Runtime observations collected"}]) + "\n```"
    if environment == "external":
        Reviewer.replies = [deny(finding)]
        waiting = looped(spec["id"])
        assert waiting["state"] == "멈춤" and not Worker.made
        path = Path(spec["worktree"])
        run = work.Run(Worker(path), person=True)
        assert specs.check(path, run, repair(path, run.halt)) is None
        if cleanup_followup:
            assert specs.check(path, run, CLEANED + disposition) is None
        git(path, "push", "-q", "origin", spec["id"])
        Reviewer.replies = [inspect]
    else:
        Reviewer.replies = [deny(finding), inspect]
        Worker.replies = [repair, CLEANED + disposition] if cleanup_followup else [repair]
    done = looped(spec["id"])
    assert done["state"] == "머지 가능"
    assert done["report_at"]["head"] == done["rounds"][-1]["head"] == proof["head"]
    assert done["report_at"]["rev"] == spec["rev"] and done["report_at"]["turn"]
    assert done["report"][0]["evidence"].startswith(json.dumps(proof))
    assert all(r.tools == loop.REVIEW_TOOLS and not r.verification for r in Reviewer.made)
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)


@pytest.mark.parametrize("credential", [
    "token=fixture-value", "password: fixture-value", "api_key='fixture-value'",
    "Authorization: Bearer fixture-value", "Bearer fixture-value",
])
def test_report_handoff_redacts_credentials_at_reviewer_boundary(world, credential):
    spec = pr_spec(world, "report-redaction", 1, "code.py", review=REQUIRED,
                   report=[{"item": "Runtime observations", "pass": True,
                            "evidence": f"GET /health returned 200; {credential}; artifacts/runtime.json"}])
    spec["report_at"] = {"head": spec["pr"]["head"], "rev": spec["rev"]}
    result = selected(world, spec)
    text = loop.instruction(spec, Path(spec["worktree"]), 1, result["head"], "main", True,
                            base_oid=result["base_oid"], contract=result)
    assert "fixture-value" not in text and "[redacted]" in text
    assert "GET /health returned 200" in text and "artifacts/runtime.json" in text
    assert '"pass": true' in text


@pytest.mark.parametrize("environment", [None, "external"])
def test_unconfigured_local_and_external_tasks_reach_read_only_review_with_evidence(world, monkeypatch, environment):
    spec = pr_spec(world, "local-evidence", 1, "code.py", review=REQUIRED, implementation_environment=environment)
    path = Path(spec["worktree"])
    report = [{"item": "Run isolated behavior checks", "pass": True,
               "evidence": "Machine observations in artifacts/runtime.json; owner reports native login passed. token=fixture-secret"}]
    spec = specs.update(spec["repo"], spec["id"], report=report,
                        report_at={"head": spec["pr"]["head"], "rev": spec["rev"]}, state="멈춤",
                        stopped={"reason": loop.Why.PREPARATION.value, "detail": "등록된 검증 명세를 준비해야 한다"})
    monkeypatch.setattr(verification, "execute", lambda *a, **k: pytest.fail("No unregistered commands may execute"))
    result = selected(world, spec)
    assert not result["registered_runtime"] and not result["problems"] and not result["flows"]
    assert result["evidence"] == ["api", "browser", "desktop", "offline"]
    assert not result["unresolved"] and not contract.ready(world.repo, path, spec, result)
    assert not spec.get("rounds") and "local_verification" not in spec
    done = looped(spec["id"])
    assert done["state"] == "머지 가능" and Reviewer.made
    assert all(r.tools == loop.REVIEW_TOOLS and not r.verification for r in Reviewer.made)
    text = order(world, 1, 1)
    assert "Required evidence" in text and all(f"- {name}:" in text for name in REQUIRED["evidence"])
    assert "Supplied runtime evidence for independent review" in text
    assert "not certified runtime proof" in text and "must not receive an allow verdict" in text
    assert "artifacts/runtime.json" in text and "owner reports native login passed" in text
    assert "fixture-secret" not in text and "[redacted]" in text
    assert "local_verification" not in done and not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)


@pytest.mark.parametrize("environment,flows", [("claude-cloud", []), (None, ["health"]), ("external", ["health"])])
def test_cloud_and_explicit_flow_requests_still_require_registered_execution(world, environment, flows):
    spec = pr_spec(world, "registered-required", 1, "code.py", implementation_environment=environment,
                   review={**REQUIRED, "flows": flows})
    result = selected(world, spec)
    assert result["registered_runtime"] and result["problems"]
    assert "등록된 검증 명세를 준비해야 한다" in result["problems"]
    assert contract.ready(world.repo, Path(spec["worktree"]), spec, result)
    done = looped(spec["id"])
    assert done["state"] == "멈춤" and not Reviewer.made


@pytest.mark.parametrize("marker", [verification.LOCAL, ".wiki/verification.protection.json", verification.MANIFEST])
def test_broken_configured_registration_is_not_downgraded_to_report_review(world, marker):
    file = world.repo / marker
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("{}", encoding="utf-8")
    spec = pr_spec(world, "configured-evidence", 1, "code.py", review=REQUIRED)
    result = selected(world, spec)
    assert result["registered_runtime"] and result["problems"]
    assert contract.ready(world.repo, Path(spec["worktree"]), spec, result)


def test_existing_local_registered_flow_still_executes_and_requires_its_receipt(cloud_world):
    world = cloud_world
    spec = pr_spec(world, "registered-local", 1, "code.py", review={"evidence": ["api"]})
    assert selected(world, spec)["registered_runtime"]
    done = looped(spec["id"])
    assert done["state"] == "머지 가능"
    assert done["local_verification"]["flows"][0]["evidence"]["requests"][0]["status"] == 200
    assert "Registered runtime evidence" in order(world, 1, 1)


@pytest.mark.parametrize("changed", ["head", "rev"])
def test_stale_implementation_report_is_not_presented_as_current_evidence(world, changed):
    spec = pr_spec(world, "stale-report", 1, "code.py", review=REQUIRED,
                   report=[{"item": "Runtime", "pass": True, "evidence": "stale runtime observation"}])
    spec["report_at"] = {"head": spec["pr"]["head"], "rev": spec["rev"]}
    spec["report_at"][changed] = "f" * 40 if changed == "head" else spec["rev"] - 1
    text = "\n".join(contract.render(selected(world, spec), spec, Path(spec["worktree"])))
    assert "No current-head implementation report is available" in text
    assert "stale runtime observation" not in text


def test_report_changes_invalidate_review_binding_without_granting_approval(world):
    spec = pr_spec(world, "report-binding", 1, "code.py", review=REQUIRED)
    original = selected(world, spec)
    record = {"review_contract": original}
    changed = copy.deepcopy(spec)
    changed["report"] = [{"item": "Runtime", "pass": True, "evidence": "New owner claim"}]
    assert contract.signature(spec) != contract.signature(changed)
    assert not contract.matches(changed, record, selected(world, changed))
    loop.store(rounds=1)
    Reviewer.replies = [deny("[P1] code.py:1 — Required desktop behavior has no current runtime evidence.")]
    done = looped(spec["id"])
    assert done["state"] != "머지 가능" and done["rounds"][0]["verdict"] == "deny"
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in world.hub.calls)
