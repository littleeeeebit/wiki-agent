"""A startup recovery must never dismiss the app's error or an unrelated dialog."""

import pytest
import json
import subprocess
import sys
from pathlib import Path

from native_webview import system_ui_wait_button

FOCUS = ["  mCurrentFocus=Window{8d0c7e0 u0 Application Not Responding: com.android.systemui}\r",
         "  mFocusedApp=ActivityRecord{8a6f270 u0 dev.vto.overlay/.MainActivity t9}\r"]
WAIT = '<node resource-id="android:id/aerr_wait" package="android" enabled="true" clickable="true" bounds="[60,1090][860,1220]" />'


def test_system_ui_startup_wait_uses_observed_native_control_bounds():
    assert system_ui_wait_button(FOCUS, "<hierarchy>" + WAIT + "</hierarchy>") == {
        "control": "android:id/aerr_wait", "x": 460, "y": 1155}


@pytest.mark.parametrize("focus", [
    "Window{1 u0 Application Not Responding: dev.vto.overlay}",
    "Window{1 u0 Application Error: com.android.systemui}",
    "Window{1 u0 com.android.permissioncontroller/.PermissionActivity}",
    "Window{1 u0 dev.vto.overlay/.MainActivity}", "null",
])
def test_other_focus_never_authorizes_a_wait_tap(focus):
    assert system_ui_wait_button(["mCurrentFocus=" + focus], "<hierarchy>" + WAIT + "</hierarchy>") is None


@pytest.mark.parametrize("xml", ["<hierarchy />", "<hierarchy>" + WAIT * 2 + "</hierarchy>",
    WAIT.replace('enabled="true"', 'enabled="false"'), WAIT.replace('package="android"', 'package="dev.vto.overlay"'),
    WAIT.replace("aerr_wait", "aerr_close"), WAIT.replace("[60,1090][860,1220]", "[860,1090][60,1220]")])
def test_missing_ambiguous_or_wrong_native_control_blocks_recovery(xml):
    with pytest.raises(ValueError):
        system_ui_wait_button(FOCUS, xml)


@pytest.mark.parametrize("system_ui", [True, False])
def test_collector_cli_reads_observed_files_and_returns_only_eligible_native_controls(tmp_path, system_ui):
    focus, hierarchy = tmp_path / "focus.json", tmp_path / "hierarchy.xml"
    lines = FOCUS if system_ui else ["mCurrentFocus=Window{1 u0 Application Not Responding: dev.vto.overlay}"]
    focus.write_text(json.dumps({"lines": lines}), encoding="utf-8")
    hierarchy.write_text("<hierarchy>" + WAIT + "</hierarchy>", encoding="utf-8")
    output = subprocess.check_output([sys.executable, str(Path(__file__).with_name("native_webview.py")),
        "android-wait", str(focus), str(hierarchy)], text=True)
    assert json.loads(output) == (system_ui_wait_button(FOCUS, hierarchy.read_text()) if system_ui else None)
