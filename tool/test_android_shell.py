"""Source-level WebView policy guard; this does not replace an Android device test."""

from pathlib import Path
import re


def test_new_window_links_use_existing_navigation_guard_and_require_user_gesture():
    source = (Path(__file__).resolve().parents[1] / "android/app/src/main/java/dev/wikiagent/mobile/MainActivity.java").read_text(encoding="utf-8")
    settings = dict(re.findall(r"settings\.(setSupportMultipleWindows|setJavaScriptCanOpenWindowsAutomatically)\((true|false)\)", source))
    # Android treats target=_blank as top-level navigation when multiple
    # windows are disabled, so the installed WebViewClient handles the URL.
    assert settings.get("setSupportMultipleWindows") == "false"
    assert settings.get("setJavaScriptCanOpenWindowsAutomatically") == "false"
    assert "shouldOverrideUrlLoading" in source and "Intent.ACTION_VIEW" in source
