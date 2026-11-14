"""WebView protocol refuses stale builds, changed scope and forged native proof."""

import copy
import json
import subprocess
import sys
import ctypes
import os
import shutil
import time
import threading
import hashlib
from contextlib import nullcontext
from pathlib import Path

import pytest

import native_verification as native
import native_webview as webview
from main import verification, specs
from common import shell
from native_process import launched
from common import process


def test_owned_cleanup_regressions_use_real_node_processes():
    subprocess.run([shutil.which("node"), "--test", str(Path(__file__).with_name("test_native_cleanup.mjs"))], check=True)


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode().strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.org")
    (root / "scenario.mjs").write_text("export default async () => {};", encoding="utf-8")
    (root / "app.rs").write_text("fn main() {}", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fixture")
    binary = tmp_path / "application.exe"
    binary.write_bytes(b"approved fixture bytes, not executed")
    node = tmp_path / "node.exe"
    node.write_bytes(b"approved runtime bytes, not executed")
    def asset(file):
        return {"path": str(file), "sha256": native.digest(file)}
    target = {"driver": "webview", "platform": "windows", "application": "fixture", "scenario": "scenario.mjs",
              "build_inputs": ["*.mjs", "*.rs"], "actions": [{"assertion": "saved", "expected": "Saved"}]}
    settings = {"version": 2, "host": "webview", "node": asset(node), "applications": {
        "fixture": {"platform": "windows", "binary": asset(binary), "profile": "disposable"}}}
    return root, git("rev-parse", "HEAD"), target, settings


def test_webview_targets_require_registered_assertions_and_explicit_platform_approval(candidate):
    _, _, target, settings = candidate
    flow = {"id": "save", "title": "Save", "kind": "desktop", "command": "wiki-agent-native", "paths": ["*"],
            "assertions": [{"id": "saved", "expected": "Saved"}], "native": target}
    assert isinstance(verification.Flow.model_validate(flow).native, webview.Target)
    assert isinstance(webview.Settings.model_validate(settings), webview.Settings)
    for change in ({"assertions": [{"id": "other", "expected": "Saved"}]}, {"command": "node scenario.mjs"},
                   {"native": {**target, "cdp_url": "http://localhost:9222"}}):
        with pytest.raises(ValueError):
            verification.Flow.model_validate({**flow, **change})
    with pytest.raises(ValueError, match="version 2"):
        verification.Manifest.model_validate({"version": 1, "contracts": ["api.md"], "flows": [flow]})
    with pytest.raises(ValueError, match="package"):
        webview.Target.model_validate({**target, "platform": "android"})


def test_renderer_is_approved_per_android_app_with_unchanged_default(candidate):
    _, _, _, settings = candidate
    windows = settings["applications"]["fixture"]
    android = {**windows, "platform": "android", "avd": "fixture"}
    assert webview.Approval.model_validate(windows).renderer == "auto"
    assert webview.Approval.model_validate(android).renderer == "auto"
    for renderer in ("auto", "host", "software"):
        approved = webview.Settings.model_validate({**settings, "applications": {
            "fixture": windows, "android": {**android, "renderer": renderer}}})
        assert approved.applications["android"].renderer == renderer
        assert approved.applications["fixture"].renderer == "auto"
    for invalid in ({**android, "renderer": "host -anything"}, {**windows, "renderer": "host"}):
        with pytest.raises(ValueError):
            webview.Approval.model_validate(invalid)


def test_fixed_native_launch_bypasses_shell_and_cannot_misreport_its_command(monkeypatch, tmp_path):
    def refused(*_args, **_kwargs):
        raise AssertionError("The fixed native collector must not activate a shell")

    monkeypatch.setattr(shell, "launch", refused)
    argv = [sys.executable, "-c", "print('native collector')"]
    code, output, cut = specs.gate(shell.command_line(argv), tmp_path, threading.Event(), argv=argv)
    assert code == 0 and output.strip() == "native collector" and not cut
    with pytest.raises(ValueError, match="recorded command"):
        specs.judge(tmp_path, ["another command"], threading.Event(), argv=argv)


def test_owned_driver_uses_approved_environment_instead_of_launching_shell_setup(candidate, monkeypatch, tmp_path):
    root, head, target, settings = candidate
    (root / ".env").write_text("ANDROID_AVD_HOME='C:/approved/avd'\n", encoding="utf-8")
    monkeypatch.setenv("ANDROID_AVD_HOME", "C:/unrelated/avd")
    monkeypatch.setattr(native, "owned_desktop", lambda: nullcontext("fixture"))
    monkeypatch.setattr(webview, "preflight", lambda *_: {})

    def observed(argv, cwd, env, desktop, log):
        assert env["ANDROID_AVD_HOME"] == "C:/approved/avd"
        assert cwd == root and desktop == "fixture"
        assert argv[0] == settings["node"]["path"]
        raise RuntimeError("approved environment observed")

    monkeypatch.setattr(webview, "launched", observed)
    with pytest.raises(RuntimeError, match="approved environment observed"):
        webview.run({"root": str(root), "artifact_dir": str(tmp_path), "head": head,
                     "target": target, "settings": settings})


@pytest.mark.parametrize("source", ["inherited", "copied"])
@pytest.mark.parametrize("key,value", [
    ("NODE_OPTIONS", "--require=outside/startup.cjs"),
    ("node_options", "--import=outside/startup.mjs"),
    ("NODE_PATH", "outside/modules"),
    ("NODE_EXTRA_CA_CERTS", "outside/roots.pem"),
])
def test_owned_driver_excludes_unmeasured_node_environment(candidate, monkeypatch, tmp_path, source, key, value):
    root, head, target, settings = candidate
    (root / ".env").write_text("ANDROID_AVD_HOME=C:/approved/avd\n" +
                              (f"{key}={value}\n" if source == "copied" else ""), encoding="utf-8")
    if source == "inherited":
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(native, "owned_desktop", lambda: nullcontext("fixture"))
    monkeypatch.setattr(webview, "preflight", lambda *_: {})

    def observed(argv, cwd, env, desktop, log):
        assert not any(name.upper().startswith("NODE_") for name in env)
        assert env["ANDROID_AVD_HOME"] == "C:/approved/avd"
        raise RuntimeError("filtered environment observed")

    monkeypatch.setattr(webview, "launched", observed)
    with pytest.raises(RuntimeError, match="filtered environment observed"):
        webview.run({"root": str(root), "artifact_dir": str(tmp_path), "head": head,
                     "target": target, "settings": settings})


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native preflight")
def test_preflight_measures_reviewed_sources_and_approved_tools(candidate):
    root, head, target, settings = candidate
    assert set(webview.preflight(root, head, target, settings)) == {"app.rs", "scenario.mjs"}
    for mutate in (lambda value: value["applications"]["fixture"].update(platform="android"),
                   lambda value: value["node"].update(sha256="0" * 64)):
        changed = copy.deepcopy(settings)
        mutate(changed)
        with pytest.raises(ValueError):
            webview.preflight(root, head, target, changed)
    (root / "app.rs").write_text("fn changed() {}", encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed head"):
        webview.preflight(root, head, target, settings)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native receipt validation")
def test_native_receipt_requires_owned_process_cleanup_raw_observations_and_screenshots(candidate, tmp_path):
    root, head, target, settings = candidate
    artifact = tmp_path / "evidence"
    artifact.mkdir()
    session = {"pid": 123, "listener_pid": 124, "hwnd": 456, "geometry": [0, 0, 800, 600], "dpi": 96, "runtime": "actual runtime observation",
               "build_head": head, "binary_sha256": settings["applications"]["fixture"]["binary"]["sha256"]}
    raw = [{"assertion": "saved", "expected": "Saved", "actual": "Saved"}]
    revision = {"core": head, "screen": head, "profile": hashlib.sha256(str(artifact / "data").encode()).hexdigest()}
    for name, content in {"sessions.json": [session], "observations.json": raw, "build-observation.json": revision,
                          "cleanup.json": {"state": "cleaned", "desktop": "owned-fixture"}}.items():
        (artifact / name).write_text(json.dumps(content), encoding="utf-8")
    (artifact / "screen.png").write_bytes(b"fixture for hash validation, not screenshot acceptance")
    proof = {"application": "fixture", "host": "windows", "driver": "owned-webview-v1", "build_head": head,
             "build_inputs": webview.preflight(root, head, target, settings), "binary_sha256": session["binary_sha256"],
             "profile": "disposable", "revisions": {"windows": "fixture"}, "desktop": "owned-fixture",
             "sessions": [session], "actions": raw,
             "artifacts": [{"path": name, "sha256": native.digest(artifact / name)}
                           for name in ("sessions.json", "observations.json", "build-observation.json", "screen.png")]}
    receipt = {"head": head, "observations": [{"id": "saved", "expected": "Saved", "actual": "Saved", "pass": True}], "native": proof}
    def validate(data):
        return native.validate_receipt(data, root, artifact, target, settings, {"windows": "fixture"})
    assert validate(receipt) == proof
    for mutate in (lambda value: value["native"].update(host="browser"),
                   lambda value: value["native"].update(build_head="0" * 40),
                   lambda value: value["native"]["sessions"][0].update(pid=0),
                   lambda value: value["observations"][0].update(actual="Unobserved"),
                   lambda value: value["native"].update(artifacts=[])):
        changed = copy.deepcopy(receipt)
        mutate(changed)
        with pytest.raises(ValueError):
            validate(changed)
    (artifact / "build-observation.json").write_text(json.dumps({**revision, "profile": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="profile"):
        validate(receipt)
    (artifact / "build-observation.json").write_text(json.dumps(revision), encoding="utf-8")
    (artifact / "screen.png").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact"):
        validate(receipt)
    (artifact / "cleanup.json").write_text('{"state":"running"}', encoding="utf-8")
    with pytest.raises(ValueError, match="cleanup"):
        validate(receipt)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows owned process tree")
def test_owned_webview_driver_tree_is_reaped_on_cancellation_and_preserves_unrelated_process(tmp_path):
    from ctypes import wintypes as w

    node = shutil.which("node")
    assert node, "The web application toolchain includes Node"
    marker = tmp_path / "child.txt"
    script = tmp_path / "tree.mjs"
    script.write_text("import {spawn} from 'node:child_process';\n"
                      "import {writeFileSync} from 'node:fs';\n"
                      "const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio:'ignore'});\n"
                      "writeFileSync(process.argv[2], String(child.pid));\n"
                      "setInterval(() => {}, 1000);\n", encoding="utf-8")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    k.WaitForSingleObject.argtypes, k.CloseHandle.argtypes = (w.HANDLE, w.DWORD), (w.HANDLE,)
    handle = None
    unrelated = subprocess.Popen([node, "-e", "setInterval(() => {}, 1000)"], **process.background_options())
    try:
        with native.owned_desktop() as desktop, (tmp_path / "driver.log").open("wb") as log:
            with pytest.raises(RuntimeError, match="cancelled"):
                with launched([node, str(script), str(marker)], tmp_path, dict(os.environ), desktop, log):
                    deadline = time.monotonic() + 10
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    handle = k.OpenProcess(0x100000, False, int(marker.read_text()))
                    assert handle, "Observe the owned child before cancellation"
                    raise RuntimeError("cancelled")
            assert k.WaitForSingleObject(handle, 5000) == 0, "The owned grandchild must have exited"
            assert unrelated.poll() is None
    finally:
        if handle:
            k.CloseHandle(handle)
        unrelated.terminate()
        unrelated.wait(timeout=5)
