"""Packaged boundaries: relocation, import recovery, local setup and owned children."""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from fastapi.testclient import TestClient
import pytest

import build_desktop
import desktop_migrate
from common import paths
from main import app, onboarding, query, runtime


@pytest.fixture
def setup(tmp_path, monkeypatch):
    onboarding.close()
    data = tmp_path / "사용자 데이터"
    workspace = tmp_path / "팀 프로젝트"
    data.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(onboarding, "DATA", data)
    monkeypatch.setattr(onboarding, "FILE", data / "onboarding.json")
    monkeypatch.setattr(onboarding, "PACKAGED", True)
    monkeypatch.setattr(onboarding, "release", lambda: {"version": "0.1.0"})
    monkeypatch.setattr(onboarding.channels, "WORKSPACE", workspace)
    monkeypatch.setattr(onboarding.channels, "LOCAL", {})
    monkeypatch.setattr(runtime, "stopping", threading.Event())
    monkeypatch.setattr(onboarding, "_halt", threading.Event())
    monkeypatch.setattr(onboarding, "_task", {"running": False, "action": "", "output": "", "error": "", "code": None})
    monkeypatch.setattr(onboarding, "refresh_path", lambda: None)
    monkeypatch.setattr(onboarding, "detect", lambda name: {"ready": True, "version": "fixture", "path": name})
    monkeypatch.setattr(onboarding.setup_chat, "logged_in", lambda agent: False)
    monkeypatch.setattr(onboarding, "authenticated", lambda provider: onboarding.setup_chat.logged_in(provider))
    monkeypatch.setattr(onboarding.channels, "codex_models", lambda: [{"id": "codex:fixture", "is_default": True}])
    monkeypatch.setattr(query, "_project", None)
    yield TestClient(app.app, base_url="http://127.0.0.1:8787"), data, workspace
    onboarding.close()


def wait():
    onboarding._thread.join(timeout=10)
    assert not onboarding._thread.is_alive()
    return dict(onboarding._task)


def source_at(path):
    (path / "tool/main").mkdir(parents=True)
    (path / "tool/main/__main__.py").write_text("", encoding="utf-8")
    (path / "raw/chat").mkdir(parents=True)
    (path / "raw/chat/wiki.jsonl").write_text('{"text":"기존 기록"}\n', encoding="utf-8")
    (path / ".chat-local.json").write_text('{"workspace":"../팀 프로젝트","model":"codex:fixture"}\n', encoding="utf-8")
    (path / ".env").write_text("GEMINI_API_KEY=fixture-only\n", encoding="utf-8")
    return path


def test_relocated_package_uses_user_data_without_checkout(tmp_path):
    root = tmp_path / "설치 폴더" / "payload/app"
    module = root / "tool/common/paths.py"
    module.parent.mkdir(parents=True)
    shutil.copyfile(paths.__file__, module)
    (root / "release.json").write_text('{"version":"0.1.0"}', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "WIKI_DATA_HOME"}
    env["LOCALAPPDATA"] = str(tmp_path / "별도 사용자")
    code = "import runpy,sys; p=runpy.run_path(sys.argv[1]); print(p['DATA']); print(p['RESOURCES']); print(p['PACKAGED'])"
    run = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code, str(module)], cwd=tmp_path, env=env,
                         capture_output=True, text=True, encoding="utf-8", check=True)
    expected = tmp_path / "별도 사용자/wiki-agent-data"
    assert run.stdout.splitlines() == [str(expected), str(root), "True"]
    assert not expected.is_relative_to(tmp_path / "별도 사용자/wiki-agent")


def test_import_backs_up_exact_bytes_before_converting_and_keeps_original(tmp_path):
    source = source_at(tmp_path / "기존 설치")
    data = tmp_path / "새 데이터"
    data.mkdir()
    original = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    backup = desktop_migrate.import_source(source, data, threading.Event(), lambda text: None)
    for name, content in original.items():
        assert (source / name).read_bytes() == content
        if str(name).startswith("tool"):
            continue
        assert (backup / name).read_bytes() == content
    saved = json.loads((data / ".chat-local.json").read_text(encoding="utf-8"))
    assert saved["workspace"] == str((source / "../팀 프로젝트").resolve())
    assert (data / "raw/chat/wiki.jsonl").read_bytes() == original[Path("raw/chat/wiki.jsonl")]
    assert not (data / "import-pending.json").exists()
    with pytest.raises(ValueError, match="이미"):
        desktop_migrate.import_source(source, data, threading.Event(), lambda text: None)


def test_import_cancellation_and_active_source_preserve_destination(tmp_path):
    source = source_at(tmp_path / "old")
    data = tmp_path / "new"
    data.mkdir()
    halt = threading.Event()
    def cancel(text):
        halt.set()
    with pytest.raises(InterruptedError):
        desktop_migrate.import_source(source, data, halt, cancel)
    assert not (data / "raw").exists()
    with runtime.server_owner(source / "raw"), pytest.raises(RuntimeError, match="이미 실행"):
        desktop_migrate.import_source(source, data, threading.Event(), lambda text: None)
    assert not (data / ".env").exists()


def test_import_recovers_after_interrupted_destination_write(tmp_path, monkeypatch):
    source = source_at(tmp_path / "old")
    data = tmp_path / "new"
    data.mkdir()
    replace = Path.replace
    def fail(path, target):
        if str(path).endswith(".env.importing"):
            raise OSError("fixture interruption")
        return replace(path, target)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="fixture"):
            desktop_migrate.import_source(source, data, threading.Event(), lambda text: None)
    assert (data / "import-pending.json").is_file()
    backup = desktop_migrate.resume_import(data)
    assert (data / ".env").read_bytes() == (backup / ".env").read_bytes()
    assert not (data / "import-pending.json").exists()


@pytest.mark.parametrize("phase", ["write", "replace"])
def test_import_marker_publication_is_atomic(tmp_path, monkeypatch, phase):
    source = source_at(tmp_path / "source")
    data = tmp_path / "data"
    data.mkdir()
    marker = data / "import-pending.json"
    write, replace = Path.write_text, Path.replace
    def interrupted_write(path, text, *args, **kwargs):
        if phase == "write" and path.parent == data and path.name.startswith("import-pending"):
            write(path, text[:4], *args, **kwargs)
            raise OSError("fixture interruption")
        return write(path, text, *args, **kwargs)
    def interrupted_replace(path, target):
        if phase == "replace" and Path(target) == marker:
            raise OSError("fixture interruption")
        return replace(path, target)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "write_text", interrupted_write)
        scoped.setattr(Path, "replace", interrupted_replace)
        with pytest.raises(OSError, match="fixture interruption"):
            desktop_migrate.import_source(source, data, threading.Event(), lambda text: None)
    assert not marker.exists()
    assert desktop_migrate.resume_import(data) is None
    backup = desktop_migrate.import_source(source, data, threading.Event(), lambda text: None)
    assert (data / ".env").read_bytes() == (source / ".env").read_bytes() == (backup / ".env").read_bytes()
    assert json.loads((data / ".chat-local.json").read_text(encoding="utf-8"))["model"] == "codex:fixture"


def test_machine_jev_settings_work_before_any_project_is_connected(setup, monkeypatch):
    client, data, workspace = setup
    monkeypatch.setattr(query, "PACKAGED", True)
    monkeypatch.setattr(query.channels, "repo_for", lambda name: None)
    monkeypatch.setenv("JEV_ENV", str(data / "absent.env"))
    response = client.get("/api/jev")
    assert response.status_code == 200
    assert response.json()["mode"] == "off"
    response = client.post("/api/jev/settings", json={"mode": "shadow", "limits": {"calls": 3}})
    assert response.status_code == 200
    assert response.json()["mode"] == "shadow" and response.json()["limits"]["calls"] == 3
    assert client.get("/api/jev").json()["limits"]["calls"] == 3
    assert query._project is None
    monkeypatch.setattr(query.channels, "repo_for", lambda name: workspace if name == "fixture" else None)
    response = client.post("/api/jev/settings", json={"mode": "active", "active_projects": [str(workspace)]})
    assert response.status_code == 200 and response.json()["canary"] is True
    assert client.get("/api/jev", headers={"X-Project": "fixture"}).json()["canary"] is False
    assert client.get("/api/jev", headers={"X-Project": "missing"}).status_code == 409


def test_package_staging_excludes_personal_files_and_tests(tmp_path, monkeypatch):
    root = tmp_path / "source"
    for name in ("tool/runtime.py", "tool/test_runtime.py", "operator/rule.md", ".env", "raw/chat/log.json", "web/dist/index.html"):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(build_desktop, "ROOT", root)
    monkeypatch.setattr(build_desktop.subprocess, "check_output", lambda *a, **k:
                        b"tool/runtime.py\0tool/test_runtime.py\0operator/rule.md\0.env\0raw/chat/log.json\0")
    payload = build_desktop.stage(tmp_path / "stage", "1.2.3", "a" * 40)
    assert (payload / "tool/runtime.py").is_file() and (payload / "operator/rule.md").is_file()
    assert not (payload / ".env").exists() and not (payload / "raw").exists()
    assert not (payload / "tool/test_runtime.py").exists()
    assert json.loads((payload / "release.json").read_text()) == {"version": "1.2.3", "revision": "a" * 40}


def test_updater_public_key_input_cannot_embed_private_key(tmp_path):
    file = tmp_path / "fixture.pub"
    value = base64.b64encode(b"untrusted comment: minisign public key: fixture\nfixture").decode()
    file.write_text(value, encoding="utf-8")
    assert build_desktop.public_key(file) == value
    file.write_text(base64.b64encode(b"untrusted comment: minisign encrypted secret key\nfixture").decode(), encoding="utf-8")
    with pytest.raises(ValueError, match="never a private key"):
        build_desktop.public_key(file)


@pytest.mark.parametrize("url", ["http://test.invalid/latest.json", "https://me:secret@test.invalid/latest.json",
                                 "https://test.invalid/latest.json?token=fixture", "https://test.invalid/latest.json#part",
                                 "https://test.invalid:0/latest.json", "https://test.invalid/a b.json"])
def test_test_feed_rejects_untrusted_url_forms(url):
    with pytest.raises(argparse.ArgumentTypeError):
        build_desktop.https_feed(url)


def test_test_feed_keeps_assets_isolated_and_production_default_unchanged():
    feed = "https://test.invalid:8443/updates/latest.json"
    assert build_desktop.https_feed(feed) == feed
    assert build_desktop.update_asset_url(feed, "0.1.4", "wiki-agent_0.1.4_x64-setup.exe") == \
        "https://test.invalid:8443/updates/wiki-agent_0.1.4_x64-setup.exe"
    assert build_desktop.update_asset_url(build_desktop.DEFAULT_UPDATE_FEED, "0.1.4", "setup.exe") == \
        "https://github.com/littleeeeebit/wiki-agent/releases/download/v0.1.4/setup.exe"


def test_readiness_checks_each_selected_agent_without_project_discovery(setup, monkeypatch):
    client, data, workspace = setup
    assert client.get("/api/onboarding").json()["complete"] is False
    assert client.post("/api/onboarding/finish", json={}).status_code == 409
    monkeypatch.setattr(onboarding.setup_chat, "logged_in", lambda agent: True)
    assert client.post("/api/onboarding/finish", json={}).json()["complete"] is True
    assert json.loads((data / ".chat-local.json").read_text())["model"] == "codex:fixture"
    assert onboarding.channels.LOCAL["model"] == "codex:fixture"
    assert query._project is None
    workspace.rename(workspace.with_name("moved-projects"))
    assert client.get("/api/onboarding").json()["complete"] is True
    assert client.post("/api/onboarding/configure", json={"agents": ["codex", "claude"]}).status_code == 200
    monkeypatch.setattr(onboarding.setup_chat, "logged_in", lambda agent: agent == "codex")
    assert client.post("/api/onboarding/finish", json={}).status_code == 409
    monkeypatch.setattr(onboarding.setup_chat, "logged_in", lambda agent: agent in {"claude", "codex"})
    assert client.post("/api/onboarding/finish", json={}).json()["complete"] is True
    assert onboarding.channels.LOCAL["model"] == "codex:fixture"


def test_local_setup_preserves_key_file_and_never_echoes_secrets(setup):
    client, data, _ = setup
    (data / ".env").write_text("# keep\nOTHER=old\nGEMINI_API_KEY=before\n", encoding="utf-8")
    response = client.post("/api/onboarding/keys", json={"values": {"GEMINI_API_KEY": "fixture-private-key"}})
    assert response.json() == {"ok": True}
    assert "fixture-private-key" not in client.get("/api/onboarding").text
    assert (data / ".env").read_text() == "# keep\nOTHER=old\nGEMINI_API_KEY=fixture-private-key\n"
    assert client.post("/api/onboarding/keys", json={"values": {"PATH": "bad"}}).status_code == 400
    assert client.post("/api/onboarding/configure", json={"agents": ["codex"]}, headers={"Origin": "https://other.invalid"}).status_code == 403
    assert client.post("/api/config/wiki", json={}).status_code == 400


def test_login_installer_failure_and_retry_remain_unready(setup, monkeypatch):
    client, _, _ = setup
    calls = []
    monkeypatch.setattr(onboarding, "cli_command", lambda name: [name])
    monkeypatch.setattr(onboarding, "command", lambda argv: calls.append(argv))
    assert client.post("/api/onboarding/login", json={"provider": "codex"}).status_code == 200
    assert wait()["code"] == 1
    assert calls == [["codex", "login"]]
    monkeypatch.setattr(onboarding.setup_chat, "logged_in", lambda name: True)
    client.post("/api/onboarding/login", json={"provider": "codex"})
    assert wait()["code"] == 0
    assert client.get("/api/onboarding/detect").json()["accounts"]["codex"]["authenticated"] is True


def test_legacy_agent_selection_and_github_login_keep_credentials_cli_owned(setup, monkeypatch):
    client, data, _ = setup
    onboarding.FILE.write_text('{"agent":"claude","complete":true}', encoding="utf-8")
    assert client.get("/api/onboarding").json()["agents"] == ["claude"]
    assert client.post("/api/onboarding/configure", json={"agents": []}).status_code == 422
    assert client.post("/api/onboarding/login", json={"provider": "other"}).status_code == 422
    authenticated = set()
    calls = []
    monkeypatch.setattr(onboarding, "authenticated", lambda provider: provider in authenticated)
    monkeypatch.setattr(onboarding, "cli_command", lambda provider: [provider])
    def command(argv):
        calls.append(argv)
        authenticated.add("gh")
    monkeypatch.setattr(onboarding, "command", command)
    client.post("/api/onboarding/login", json={"provider": "gh"})
    assert wait()["code"] == 0
    assert calls == [["gh", "auth", "login", "--hostname", "github.com", "--web", "--git-protocol", "https"]]
    assert client.get("/api/onboarding/detect").json()["accounts"]["gh"]["authenticated"]
    assert not (data / ".env").exists()


def test_packaged_empty_project_list_reaches_connection_instead_of_failing(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(query, "PACKAGED", True)
    monkeypatch.setattr(query, "project", lambda: "")
    assert client.get("/api/channels").json() == []
    assert client.post("/api/folders", json={"path": ""}).status_code == 400
    assert client.post("/api/config/wiki", json={"model": "haiku"}).status_code == 400


def test_release_history_is_versioned_and_refuses_missing_or_duplicate_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(build_desktop, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    file = tmp_path / "docs/windows-releases.json"
    rows = [{"version": version, "notes": f"Changes {version}"} for version in ("0.1.9", "0.1.10", "0.1.8")]
    file.write_text(json.dumps(rows), encoding="utf-8")
    assert [row["version"] for row in build_desktop.release_history("0.1.9")] == ["0.1.9", "0.1.8"]
    with pytest.raises(ValueError, match="Add release notes"):
        build_desktop.release_history("0.1.11")
    file.write_text(json.dumps(rows + [rows[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        build_desktop.release_history("0.1.9")


def test_cancellable_process_streams_progress_and_reaps_child(setup):
    _, data, _ = setup
    pid = data / "child.pid"
    code = f"import os,time,pathlib; pathlib.Path({str(pid)!r}).write_text(str(os.getpid())); print('fixture progress',flush=True); time.sleep(30)"
    onboarding.start("fixture", lambda: onboarding.command([sys.executable, "-u", "-c", code]))
    deadline = time.monotonic() + 10
    while "fixture progress" not in onboarding._task["output"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "fixture progress" in onboarding._task["output"]
    onboarding._halt.set()
    result = wait()
    assert result["code"] == 130, result
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid.read_text()))
        if handle:
            status = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(status))
            ctypes.windll.kernel32.CloseHandle(handle)
            assert status.value != 259


@pytest.mark.parametrize("name, version, features, ready", [
    ("git", "git version 2.20.0", "", False),
    ("git", "git version 2.51.0", "", True),
    ("pwsh", "PowerShell 5.1", "", False),
    ("pwsh", "PowerShell 7.6.0", "", True),
    ("codex", "codex-cli fixture", "hooks deprecated true\n", False),
    ("codex", "codex-cli fixture", "hooks    removed true\n", False),
    ("codex", "codex-cli fixture", "hooks experimental false\n", True),
])
def test_detection_reuses_compatible_executables_only(monkeypatch, name, version, features, ready):
    monkeypatch.setattr(onboarding, "cli_command", lambda name: [name])
    monkeypatch.setattr(onboarding.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, features if "features" in argv else version, ""))
    assert onboarding.detect(name)["ready"] is ready
