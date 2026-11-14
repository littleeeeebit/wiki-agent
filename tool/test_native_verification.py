"""Real Windows controls and receipts, separated from protocol-only checks."""

# ruff: noqa: F811 — pytest injects imported fixtures by name.

import copy
import json
import sys
from pathlib import Path

import pytest

import native_targets as targets
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


def webview_flow(platform: str, **changed) -> dict:
    target = {"driver": "webview", "platform": platform, "application": f"vto-{platform}",
              "scenario": "scripts/scenario.mjs", "build_inputs": ["scripts/scenario.mjs", "src/**/*.ts"],
              "build": "npm run tauri build", "output": "src-tauri/target/release/vto.exe",
              "origins": ["http://tauri.localhost"],
              "actions": [{"assertion": "saved", "expected": "Saved", "selector": "#status", "no_requests": True}],
              **({"package": "com.example.vto", "activity": ".MainActivity"} if platform == "android" else {}), **changed}
    return {"id": f"save-{platform}", "title": "Save", "kind": "desktop", "command": "wiki-agent-native",
            "paths": ["src"], "assertions": [{"id": "saved", "expected": "Saved"}], "native": target}


def test_webview_targets_validate_beside_unchanged_win32_targets():
    win32 = {"application": "fixture", "window_class": "Fixture", "build_inputs": ["app.py"],
             "actions": [{"assertion": "saved", "action": "click", "control_id": 1, "observe_id": 2, "expected": "Saved"}]}
    old = {**webview_flow("windows"), "id": "win32", "native": win32}
    parsed = verification.Manifest.model_validate({"version": 2, "contracts": ["api.md"],
                                                   "flows": [old, webview_flow("windows"), webview_flow("android")]})
    assert [type(f.native) for f in parsed.flows] == [targets.Win32Target, targets.WebViewTarget, targets.WebViewTarget]
    # A Win32 dump stays byte-identical, so its approved signature survives; naming the default is accepted.
    assert set(parsed.flows[0].native.model_dump()) == {*win32, "arguments"}
    assert verification.Flow.model_validate({**old, "native": {**win32, "driver": "win32"}})
    for wrong in (webview_flow("android", package=""), webview_flow("windows", package="com.example.vto"),
                  {**webview_flow("windows"), "native": {k: v for k, v in webview_flow("windows")["native"].items() if k != "build"}},
                  webview_flow("windows", scenario="scripts/other.mjs"), webview_flow("windows", driver="cdp"),
                  webview_flow("windows", window_class="Fixture"),
                  webview_flow("windows", actions=[{"assertion": "other", "expected": "Saved", "selector": "#status"}]),
                  webview_flow("windows", actions=[{"assertion": "saved", "expected": "Saved"}]),
                  webview_flow("windows", output="../prebuilt/vto.exe"), webview_flow("windows", output="C:/vto/vto.exe"),
                  webview_flow("windows", origins=["http://tauri.localhost/index.html"]), webview_flow("windows", origins=[]),
                  webview_flow("windows", calls=["http://ipc.localhost/invoke"]),
                  webview_flow("windows", origins=["http://user:secret@tauri.localhost"]),
                  webview_flow("windows", origins=["http://tauri.localhost:not-a-port"]),
                  webview_flow("windows", calls=["http://ipc.localhost:99999"]), webview_flow("windows", calls=["http://:80"]),
                  {**webview_flow("windows"), "native": {**webview_flow("windows")["native"], "executable": "C:/vto/vto.exe"}}):
        with pytest.raises(ValueError):
            verification.Flow.model_validate(wrong)
    assert verification.Flow.model_validate(webview_flow("windows", origins=["http://tauri.localhost:1420"]))
    with pytest.raises(ValueError, match="WebView"):
        targets.preflight(Path("."), "0" * 40, parsed.flows[1].native.model_dump(), {})


def test_driver_schemas_stay_out_of_the_hashed_collector_module():
    # A desktop flow's signature hashes native_verification.py (`identity["collector"]`), so a
    # schema-only change there would revoke every stored Win32 pass.
    collector = Path(native.__file__).read_text(encoding="utf-8")
    assert "native_targets" not in collector and "class WebViewTarget" not in collector
    assert not hasattr(native, "WebViewTarget") and not hasattr(native, "approval")


def test_version_three_holds_one_approval_per_application(tmp_path):
    base = {"environment_id": "local", "test_scope": "fixture", "env_file": "C:/x/.env", "browser_tool": "chrome",
            "allowed_origins": ["http://127.0.0.1:1"], "revisions": {}, "manifest_digest": "0" * 64}
    win32 = {"version": 1, "host": "windows-win32", "application": "fixture", "executable": "C:/py/python.exe",
             "executable_sha256": "1" * 64, "ownership": "launch-disposable", "desktop": "attempt-owned", "profile": "p"}
    windows = {"version": 1, "host": "windows-webview2", "application": "vto-windows",
               "runner": "C:/node/node.exe", "runner_sha256": "3" * 64, "profile": "p"}
    android = {**windows, "host": "android-emulator", "application": "vto-android",
               "sdk": "C:/android/sdk", "adb": "C:/android/sdk/platform-tools/adb.exe", "adb_sha256": "4" * 64,
               "emulator": "C:/android/sdk/emulator/emulator.exe", "emulator_sha256": "5" * 64,
               "system_image": "C:/android/sdk/system-images/android-35", "system_image_sha256": "6" * 64}
    settings = verification.LocalSettings.model_validate({**base, "version": 3, "native": [win32, windows, android]}).model_dump()
    webviews = [verification.Flow.model_validate(webview_flow(p)).native for p in ("windows", "android")]
    assert [targets.approval(settings, t)["host"] for t in webviews] == ["windows-webview2", "android-emulator"]
    # Version 2 keeps its single Win32 approval; an approval for the wrong host does not count.
    v2 = verification.LocalSettings.model_validate({**base, "version": 2, "native": win32}).model_dump()
    assert targets.approval(v2, targets.Win32Target.model_validate(
        {"application": "fixture", "window_class": "F", "build_inputs": ["a.py"],
         "actions": [{"assertion": "a", "action": "click", "control_id": 1, "observe_id": 1, "expected": "x"}]})) == win32
    with pytest.raises(ValueError, match="vto-android"):
        targets.approval({"native": [{**windows, "application": "vto-android"}]}, webviews[1])
    for wrong in ({"version": 2, "native": [windows]}, {"version": 3, "native": win32},
                  {"version": 3, "native": [windows, windows]}, {"version": 3, "native": [{**windows, "sdk": "C:/sdk"}]},
                  {"version": 3, "native": [{**windows, "executable": "C:/vto/vto.exe", "executable_sha256": "2" * 64}]},
                  {"version": 3, "native": [{**android, "system_image": ""}]},
                  {"version": 3, "native": [{**android, "adb_sha256": ""}]},
                  {"version": 3, "native": [{**windows, "emulator": "C:/emulator.exe"}]}):
        with pytest.raises(ValueError):
            verification.LocalSettings.model_validate({**base, **wrong})


def test_a_webview_flow_fails_closed_before_any_setup_build_or_review(cloud_world):
    world = cloud_world
    marker = world.tmp / "ran"
    flow = {**webview_flow("windows"), "paths": ["*.py"]}
    flow["native"]["build"] = f"python -c \"open(r'{marker}-build', 'w')\""
    manifest = {"version": 2, "contracts": ["api-contract.md"], "flows": [flow]}
    (world.repo / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    commit(world.repo, "webview-contract.txt")
    git(world.repo, "push", "origin", "main")
    approval = {"version": 1, "host": "windows-webview2", "application": "vto-windows", "runner": sys.executable,
                "runner_sha256": native.digest(Path(sys.executable)), "profile": "isolated-test-profile"}
    settings = {**world.settings, "version": 3, "manifest_digest": verification.manifest(world.repo)[1],
                "setup": f"python -c \"open(r'{marker}-setup', 'w')\"", "native": [approval]}
    assert client().put("/api/verification/config", json=settings).status_code == 200
    spec = cloud_spec(world, "webview-closed", 1, "change.py", review={"flows": [flow["id"]]})
    stopped = looped(spec["id"])
    assert stopped["state"] == "멈춤" and not Reviewer.made and not Worker.made
    assert "WebView" in json.dumps(stopped, ensure_ascii=False)
    assert not any(world.tmp.glob("ran-*")), "nothing may run before the collector exists"


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
    # Version 3 approves per application; the negatives below then run through the saved list.
    other = {**settings, "version": 3, "native": [{**settings["native"], "application": "other"}]}
    assert client().put(f"/api/verification/config?sid={negative['id']}", json=other).status_code == 400
    listed = client().put(f"/api/verification/config?sid={negative['id']}", json={**settings, "version": 3, "native": [settings["native"]]})
    assert listed.status_code == 200 and isinstance(verification.local(world.repo)["native"], list)
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
