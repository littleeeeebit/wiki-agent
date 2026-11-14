"""A startup recovery must never dismiss the app's error or an unrelated dialog."""

import pytest
import json
import subprocess
import sys
import os
import shutil
from pathlib import Path

import native_verification
import native_webview
from native_webview import system_ui_wait_button

FOCUS = ["  mCurrentFocus=Window{8d0c7e0 u0 Application Not Responding: com.android.systemui}\r",
         "  mFocusedApp=ActivityRecord{8a6f270 u0 dev.example.app/.MainActivity t9}\r"]
WAIT = '<node resource-id="android:id/aerr_wait" package="android" enabled="true" clickable="true" bounds="[60,1090][860,1220]" />'


@pytest.mark.parametrize("activity", [".MainActivity", "dev.example.app.MainActivity"])
@pytest.mark.parametrize("focus,accepted", [
    pytest.param("mCurrentFocus=Window{1 u0 dev.example.app/.MainActivity}", True, id="abbreviated"),
    pytest.param("  mCurrentFocus=Window{1 u0 dev.example.app/dev.example.app.MainActivity}\r", True, id="qualified"),
    pytest.param("mCurrentFocus=Window{1 u0 dev.example.app/.OtherActivity}", False, id="other-activity"),
    pytest.param("mCurrentFocus=Window{1 u0 outside.dev.example.app/.MainActivity}", False, id="package-prefix"),
    pytest.param("mCurrentFocus=Window{1 u0 dev.example.app.extra/.MainActivity}", False, id="package-suffix"),
    pytest.param("mCurrentFocus=Window{1 u0 dev.example.app/dev.example.app.MainActivityExtra}", False, id="activity-suffix"),
    pytest.param("mFocusedApp=ActivityRecord{1 u0 dev.example.app/.MainActivity t9}", False, id="background-only"),
    pytest.param("mCurrentFocus=null", False, id="no-window"),
    pytest.param("mCurrentFocus=Window{1 u0 Application Not Responding: dev.example.app}", False, id="app-dialog"),
    pytest.param("mCurrentFocus=Window{1 u0 outside.app/.MainActivity}\n"
                 "mFocusedApp=ActivityRecord{1 u0 dev.example.app/.MainActivity t9}", False, id="background-with-other-focus"),
])
def test_collector_and_receipt_require_the_same_exact_android_activity(tmp_path, monkeypatch, activity, focus, accepted):
    fixture = Path(__file__).with_name("fixtures") / "test_android_driver.mjs"
    driver = Path(__file__).with_name("native_webview.mjs")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NODE_")}

    def collect(root, line):
        result = subprocess.run([shutil.which("node"), "--experimental-vm-modules", str(fixture), str(driver),
                                 sys.executable, str(root), "ready", "auto", activity, line], env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        observed = json.loads((root / "evidence/result.json").read_text(encoding="utf-8"))
        return result, observed

    result, receipt = collect(tmp_path, focus)
    assert (result.returncode == 0) is accepted, result.stderr
    assert ("blocked" not in receipt) is accepted
    root = tmp_path
    if not accepted:
        root = tmp_path / "baseline"
        baseline, receipt = collect(root, "mCurrentFocus=Window{1 u0 dev.example.app/.MainActivity}")
        assert baseline.returncode == 0, baseline.stderr
        artifact = root / "evidence/android-focus.json"
        artifact.write_text(json.dumps({"lines": focus.splitlines()}), encoding="utf-8")
        next(row for row in receipt["native"]["artifacts"] if row["path"] == artifact.name)["sha256"] = native_verification.digest(artifact)
    artifact = root / "evidence"
    # The wrapper records cleanup after Node exits; asset/build preflight uses simulated adapters.
    (artifact / "cleanup.json").write_text(json.dumps({"state": "cleaned", "desktop": "fixture"}), encoding="utf-8")
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(native_webview, "preflight", lambda *_: {})
    if accepted:
        assert native_verification.validate_receipt(receipt, root, artifact, request["target"], request["settings"], {}) == receipt["native"]
    else:
        with pytest.raises(ValueError, match="Native Android activity focus is missing"):
            native_verification.validate_receipt(receipt, root, artifact, request["target"], request["settings"], {})


@pytest.mark.parametrize("focus", [
    "Window{1 u0 Application Not Responding: dev.example.app}",
    "Window{1 u0 Application Error: com.android.systemui}",
    "Window{1 u0 com.android.permissioncontroller/.PermissionActivity}",
    "Window{1 u0 dev.example.app/.MainActivity}", "null",
])
def test_other_focus_never_authorizes_a_wait_tap(focus):
    assert system_ui_wait_button(["mCurrentFocus=" + focus], "<hierarchy>" + WAIT + "</hierarchy>") is None


@pytest.mark.parametrize("xml", ["<hierarchy />", "<hierarchy>" + WAIT * 2 + "</hierarchy>",
    WAIT.replace('enabled="true"', 'enabled="false"'), WAIT.replace('package="android"', 'package="dev.example.app"'),
    WAIT.replace("aerr_wait", "aerr_close"), WAIT.replace("[60,1090][860,1220]", "[860,1090][60,1220]")])
def test_missing_ambiguous_or_wrong_native_control_blocks_recovery(xml):
    with pytest.raises(ValueError):
        system_ui_wait_button(FOCUS, xml)


@pytest.mark.parametrize("mode,passed,taps", [
    ("system_ui", True, 1), ("ready", True, 0),
    ("starting", True, 0), ("recurrent", False, 1),
    ("app_error", False, 0), ("late_system_ui", False, 0),
])
def test_driver_recovers_only_initial_system_ui_before_focus_pid_and_debugger(tmp_path, mode, passed, taps):
    fixture = Path(__file__).with_name("fixtures") / "test_android_driver.mjs"
    driver = Path(__file__).with_name("native_webview.mjs")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NODE_")}
    result = subprocess.run([shutil.which("node"), "--experimental-vm-modules", str(fixture), str(driver),
                             sys.executable, str(tmp_path), mode], env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=20)
    receipt = tmp_path / "evidence/result.json"
    assert receipt.exists(), result.stderr
    observed = json.loads(receipt.read_text(encoding="utf-8"))
    trace = json.loads((tmp_path / "trace.json").read_text(encoding="utf-8"))
    assert (result.returncode == 0) is passed, result.stderr
    assert ("blocked" not in observed) is passed
    assert sum(row[0] == "tap" for row in trace) == taps
    if taps and passed:
        actions = [row[0] for row in trace]
        assert actions.index("tap") < actions.index("pid") < actions.index("debugger")
        assert {row["path"] for row in observed["native"]["artifacts"]} >= {
            "startup-system-ui.json", "startup-system-ui.xml", "startup-system-ui.png", "startup-recovery.json"}
    elif taps:
        assert not any(row[0] == "debugger" or row == ["pid", "ready"] for row in trace)


@pytest.mark.parametrize("renderer", ["auto", "host", "software"])
def test_driver_passes_only_the_approved_android_renderer(tmp_path, renderer):
    fixture = Path(__file__).with_name("fixtures") / "test_android_driver.mjs"
    driver = Path(__file__).with_name("native_webview.mjs")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NODE_")}
    result = subprocess.run([shutil.which("node"), "--experimental-vm-modules", str(fixture), str(driver),
                            sys.executable, str(tmp_path), "ready", renderer], env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    trace = json.loads((tmp_path / "trace.json").read_text(encoding="utf-8"))
    arguments = next(row[2] for row in trace if row[:2] == ["spawn", "fixture-emulator"])
    if renderer == "auto":
        assert "-gpu" not in arguments
    else:
        assert arguments[arguments.index("-gpu") + 1] == renderer
