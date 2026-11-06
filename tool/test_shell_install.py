"""Machine-wide install state, output, re-probing and server ownership."""

import os
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from common import shell
from main import connect, powershell, runtime


@pytest.fixture
def client(monkeypatch):
    for module in (shell, powershell):
        monkeypatch.setattr(module, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))
    monkeypatch.setattr(powershell, "_install", {"running": False, "output": "", "code": None})
    monkeypatch.setattr(powershell, "_install_thread", None)
    monkeypatch.setattr(runtime, "stopping", threading.Event())
    app = FastAPI()
    app.include_router(powershell.router)
    yield TestClient(app, base_url="http://127.0.0.1")
    runtime.stopping.set()
    powershell.close_install()


def test_install_runs_once_with_live_output_and_refreshes_the_machine(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, refreshes = [], []
    monkeypatch.setattr(powershell.shutil, "which", lambda name: "winget.exe" if name == "winget" else None)

    def gate(command, cwd, halt, env, timeout, owned_jobs, kill):
        calls.append(command)
        assert halt is runtime.stopping and timeout == 900
        runtime.gate_output.get()("설치 중 fixture".encode())
        entered.set()
        assert release.wait(5)
        return 0, "fixture installed", ""

    def detect(*, refresh=False):
        refreshes.append(refresh)
        shell._detected = (shell.PowerShell("pwsh", "7.6.1", 7),)
        return shell._detected

    monkeypatch.setattr(runtime, "gate", gate)
    assert client.post("/api/shell/install").json() == {"ok": True}
    assert entered.wait(5)
    state = powershell.status()
    assert state["major"] == 5 and state["install"]["running"]
    assert state["install"]["output"] == "설치 중 fixture"
    assert client.post("/api/shell/install").json() == {"ok": True}
    monkeypatch.setattr(shell, "detect", detect)
    release.set()
    powershell.close_install()
    assert refreshes.count(True) == 1
    state = powershell.status()
    assert state["major"] == 7 and not state["needs_install"]
    assert state["install"] == {"running": False, "output": "fixture installed", "code": 0}
    assert calls == ["winget install --id Microsoft.PowerShell --source winget"]


def test_missing_winget_has_a_reason_and_official_download_without_a_process(client, monkeypatch):
    monkeypatch.setattr(powershell.shutil, "which", lambda _: None)
    result = client.post("/api/shell/install").json()
    assert result["download"] == shell.DOWNLOAD
    assert "winget" in result["reason"]
    assert powershell._install_thread is None
    assert not powershell.status()["winget"]


def test_install_stops_with_the_server_and_retains_failure_output(client, monkeypatch):
    entered = threading.Event()
    monkeypatch.setattr(powershell.shutil, "which", lambda _: "fixture")
    monkeypatch.setattr(shell, "detect", lambda **_: shell._detected)

    def gate(command, cwd, halt, *args):
        entered.set()
        assert halt.wait(5)
        return None, "fixture interrupted", "사람이 멈춤"

    monkeypatch.setattr(runtime, "gate", gate)
    client.post("/api/shell/install")
    assert entered.wait(5)
    runtime.stopping.set()
    powershell.close_install()
    assert powershell._install == {"running": False, "code": None, "output": "사람이 멈춤"}


def test_a_phone_cannot_start_a_machine_install(client):
    response = client.post("/api/shell/install", headers={"x-forwarded-for": "192.0.2.1"})
    assert response.status_code == 403
    assert powershell._install_thread is None


def test_status_and_terminal_share_the_chosen_machine_version(client, tmp_path):
    state = connect.status(tmp_path, {})
    assert state["powershell"]["version"] == "5.1.19041.1"
    assert state["powershell"]["needs_install"]
    assert client.get("/api/shell/terminal").json() == {
        "argv": ["powershell", "-NoLogo", "-NoProfile"]}
