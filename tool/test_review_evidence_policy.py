"""Local evidence obligations reach independent review without inventing execution flows."""

# ruff: noqa: F811 — pytest injects shared fixtures by name.

import copy
from pathlib import Path

import pytest

from main import loop, review_contract as contract, specs, verification
from test_loop import Reviewer, Worker, deny, looped, no_machine_settings, order, pr_spec, template, world  # noqa: F401
from test_local_verification import cloud_world, git_world  # noqa: F401
from test_review_contract import selected

REQUIRED = {"evidence": ["api", "browser", "desktop"]}


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
