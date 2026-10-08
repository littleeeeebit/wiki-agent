"""Review children keep the server's Python when the terminal PATH differs."""

import json
import os
import subprocess
import sys
import threading
import venv

import pytest

from agent import ChatSession
from common import python_environment
from main import specs


def test_python_path_is_process_local_preserves_overrides_and_is_idempotent(tmp_path, monkeypatch):
    directory = tmp_path / "renamed environment"
    monkeypatch.setattr(sys, "executable", str(directory / "python"))
    monkeypatch.setenv("PATH", str(tmp_path / "other tools"))
    monkeypatch.setenv("PARENT_ONLY", "not inherited by an explicit environment")
    before = dict(os.environ)
    extra = {"PATH": os.pathsep.join([str(tmp_path / "override"), str(directory)]), "CHECK_MARKER": "kept"}
    env = python_environment(extra)
    assert env["PATH"].split(os.pathsep) == [str(directory), str(tmp_path / "override")]
    assert env["CHECK_MARKER"] == "kept"
    assert "PARENT_ONLY" not in env
    assert python_environment(env) == env
    assert dict(os.environ) == before and extra["PATH"].startswith(str(tmp_path / "override"))
    assert python_environment({"PATH": ""})["PATH"] == str(directory)
    if os.name == "nt":
        assert python_environment({"Path": "override"})["PATH"] == os.pathsep.join([str(directory), "override"])


@pytest.mark.parametrize("model", ["claude", "codex:fixture"])
def test_review_session_and_gate_use_the_running_environment_without_activation(tmp_path, monkeypatch, model):
    directory = tmp_path / "environment with spaces"
    venv.EnvBuilder(with_pip=False).create(directory)
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setenv("PATH", "")
    session = ChatSession(tmp_path, model=model, env={"CHECK_MARKER": "kept"})
    command = 'python -c "import json,os,sys; print(json.dumps([sys.executable,os.environ.get(\'CHECK_MARKER\')]))"'
    try:
        child = subprocess.run(command, shell=True, cwd=tmp_path, env=session._env,
                               capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout) == [str(python), "kept"]
        code, output, cut = specs.gate(command, tmp_path, threading.Event(), env={**os.environ, "CHECK_MARKER": "kept"})
        assert (code, cut) == (0, ""), output
        assert json.loads(output) == [str(python), "kept"]
        code, output, cut = specs.gate(command, tmp_path, threading.Event())
        assert (code, cut) == (0, ""), output
        assert json.loads(output) == [str(python), None]
        assert os.environ["PATH"] == ""
    finally:
        session.close()


def test_gate_receipt_changes_when_the_interpreter_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(specs, "adapter_path", lambda *_: None)
    before = specs.digest(tmp_path, tmp_path, "python -m pytest")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "other environment/python"))
    assert specs.digest(tmp_path, tmp_path, "python -m pytest") != before
