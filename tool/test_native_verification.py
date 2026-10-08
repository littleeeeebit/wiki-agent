"""Real Windows controls and receipts, separated from protocol-only checks."""

# ruff: noqa: F811 — pytest injects imported fixtures by name.

import copy
import json
import sys
from pathlib import Path

import pytest

import native_verification as native
from main import loop, review_contract, verification
from test_local_verification import cloud_spec, cloud_world, git_world  # noqa: F401
from test_loop import Reviewer, Worker, client, commit, git, looped, pr_spec, template, no_machine_settings  # noqa: F401


def test_version_one_rejects_native_and_unknown_native_scope():
    target = {"application": "fixture", "window_class": "Fixture", "build_inputs": ["app.py"],
              "actions": [{"assertion": "saved", "action": "click", "control_id": 1,
                           "observe_id": 2, "expected": "Saved"}]}
    flow = {"id": "save", "title": "Save", "kind": "desktop", "command": "wiki-agent-native",
            "paths": ["app.py"], "assertions": [{"id": "saved", "expected": "Saved"}], "native": target}
    with pytest.raises(ValueError, match="version 2"):
        verification.Manifest.model_validate({"version": 1, "contracts": ["api.md"], "flows": [flow]})
    assert verification.Manifest.model_validate({"version": 2, "contracts": ["api.md"], "flows": [flow]})
    for changed in ({**target, "terminal": "discover"}, {**target, "actions": [{**target["actions"][0], "action": "keyboard"}]}):
        with pytest.raises(ValueError):
            native.NativeTarget.model_validate(changed)
    flow["kind"] = "browser"
    with pytest.raises(ValueError):
        verification.Flow.model_validate(flow)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows native acceptance requires Win32")
def test_actual_native_window_event_identity_and_read_only_handoff(cloud_world):
    world = cloud_world
    fixture = Path(__file__).with_name("fixtures") / "native_window.py"
    (world.repo / "native_app.py").write_bytes(fixture.read_bytes())
    target = {"application": "disposable-win32-fixture", "window_class": "WikiVerificationFixture",
              "arguments": ["native_app.py"], "build_inputs": ["native_app.py"],
              "actions": [{"assertion": "saved", "action": "click", "control_id": 1, "observe_id": 2,
                           "expected": "Saved", "event": "WM_COMMAND/save"}]}
    manifest = {"version": 2, "contracts": ["api-contract.md"], "flows": [
        {"id": "native-save", "title": "Native save event", "kind": "desktop", "command": "wiki-agent-native",
         "paths": ["native_app.py"], "native": target, "assertions": [{"id": "saved", "expected": "Saved"}]}]}
    (world.repo / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "native-contract.txt")
    git(world.repo, "push", "origin", "main")
    settings = {**world.settings, "version": 2, "manifest_digest": verification.manifest(world.repo)[1],
                "native": {"version": 1, "host": "windows-win32", "application": target["application"],
                           "executable": sys.executable, "executable_sha256": native.digest(Path(sys.executable)),
                           "ownership": "launch-disposable", "desktop": "attempt-owned", "profile": "isolated-test-profile"}}
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    spec = cloud_spec(world, "native-acceptance", 1, "change.py", review={"flows": ["native-save"]})
    done = looped(spec["id"])
    assert done["state"] == "머지 가능", done.get("stopped")
    assert Reviewer.made[0].tools == loop.CLOUD_TOOLS and not Worker.made
    record = done["local_verification"]
    row = record["flows"][0]
    evidence = row["evidence"]
    assert evidence["observations"][0]["pass"] and evidence["native"]["actions"][0]["event_observed"]["event"] == "WM_COMMAND/save"
    artifact, path = Path(row["artifact_dir"]), Path(spec["worktree"])
    assert json.loads((artifact / "ownership.json").read_text(encoding="utf-8"))["state"] == "cleaned"
    assert "WM_COMMAND/save" in (world.tmp / "review/proj/1/round-1.md").read_text(encoding="utf-8")
    snapshot = {"test_head": row["executed_head"], "manifest_digest": settings["manifest_digest"],
                "native_settings_digest": verification.sha(settings["native"]),
                "native_target_digest": verification.sha(target), "evidence": evidence,
                "accepted_before_negative_checks": not verification.proven(
                    world.repo, path, done, record["head"], record["base_oid"], flow_ids=["native-save"])}
    (artifact / "acceptance.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    for mutate in (lambda v: v["native"].update(build_head="0" * 40),
                   lambda v: v["native"].update(host="browser"),
                   lambda v: v["native"]["actions"][0].update(focused=False),
                   lambda v: v["native"]["actions"][0].update(event_observed=None)):
        changed = {"head": row["executed_head"], **copy.deepcopy(evidence)}
        mutate(changed)
        with pytest.raises(ValueError):
            native.validate_receipt(changed, path, artifact, verification.Flow.model_validate(manifest["flows"][0]).native.model_dump(),
                                    settings["native"], settings["revisions"])
    file = artifact / evidence["native"]["artifacts"][0]["path"]
    file.write_text("tampered", encoding="utf-8")
    assert verification.proven(world.repo, path, done, record["head"], record["base_oid"], flow_ids=["native-save"])
    assert review_contract.merge_problem(world.repo, path, done, record["head"], record["base_oid"])
    # A real missing native selector must stop before BM_CLICK, not fall back
    # to whatever window/control happens to have focus.
    manifest["flows"][0]["native"]["actions"][0]["control_id"] = 999
    (world.repo / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "native-negative.txt")
    git(world.repo, "push", "origin", "main")
    settings["manifest_digest"] = verification.manifest(world.repo)[1]
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    negative = cloud_spec(world, "native-wrong-selector", 2, "negative.py", review={"flows": ["native-save"]})
    refused = client().put(f"/api/verification/config?sid={negative['id']}", json={**settings, "version": 1, "native": None})
    assert refused.status_code == 400
    reviewers = len(Reviewer.made)
    stopped = looped(negative["id"])
    assert stopped["state"] == "멈춤" and len(Reviewer.made) == reviewers
    row = stopped["local_verification"]["flows"][0]
    assert row["blocked"] and "selector" in row["reason"]
    owned = Path(row["artifact_dir"])
    assert not (owned / "events.jsonl").exists()
    assert json.loads((owned / "ownership.json").read_text(encoding="utf-8"))["state"] == "cleaned"
    manifest["flows"][0]["native"]["actions"][0]["control_id"] = 1
    manifest["flows"][0]["native"]["arguments"].append("--wrong-focus")
    (world.repo / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "native-focus-negative.txt")
    git(world.repo, "push", "origin", "main")
    settings["manifest_digest"] = verification.manifest(world.repo)[1]
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    negative = cloud_spec(world, "native-wrong-focus", 3, "focus.py", review={"flows": ["native-save"]})
    stopped = looped(negative["id"])
    assert stopped["state"] == "멈춤" and len(Reviewer.made) == reviewers
    row = stopped["local_verification"]["flows"][0]
    assert row["blocked"] and '"focused": false' in row["reason"]
    owned = Path(row["artifact_dir"])
    assert not (owned / "events.jsonl").exists()
    assert json.loads((owned / "ownership.json").read_text(encoding="utf-8"))["state"] == "cleaned"
