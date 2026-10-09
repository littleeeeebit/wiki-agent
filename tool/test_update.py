"""The update card: a moved `origin/main` is offered, pulled, then waits for a restart."""

import subprocess
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from main import update


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkouts(tmp_path, monkeypatch):
    """This server's checkout, started on `first`, and another that pushes to their origin."""

    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "t")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t@example.com")
    origin, local, other = tmp_path / "origin.git", tmp_path / "local", tmp_path / "other"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(other))
    git(other, "commit", "-q", "--allow-empty", "-m", "first")
    git(other, "push", "-q", "origin", "HEAD:main")
    git(tmp_path, "clone", "-q", str(origin), str(local))
    monkeypatch.setattr(update, "WIKI", local)
    monkeypatch.setattr(update, "LATER", tmp_path / "later")
    monkeypatch.setattr(update, "STARTED", git(local, "rev-parse", "HEAD"))
    monkeypatch.setattr(update, "_prepared", "")
    monkeypatch.setattr(update, "_failed", "")
    return local, other


def test_offers_pulls_and_waits_for_restart(checkouts):
    local, other = checkouts
    assert update.status(force=True)["state"] == "current"

    git(other, "commit", "-q", "--allow-empty", "-m", "second")
    git(other, "push", "-q", "origin", "HEAD:main")
    found = update.status(force=True)
    assert found["state"] == "available" and found["behind"] == 1 and not found["blocked"]
    assert [c["subject"] for c in found["commits"]] == ["second"]

    (local / "f.txt").write_text("x")
    git(local, "add", "f.txt")
    assert update.status(force=True)["blocked"]      # a dirty checkout is never pulled over
    with pytest.raises(HTTPException):
        update.apply()
    git(local, "reset", "-q", "--hard")

    assert update.apply()["state"] == "ready"        # pulled; the running code is still the old one
    assert git(local, "log", "-1", "--format=%s") == "second"
    assert update.steps(["web/src/App.tsx"])[0][0] == "npm run build"
    assert update.steps(["tool/main/app.py"]) == []
    assert update.steps(["requirements-hooks.txt"])[0][0] == "pip install"   # included by requirements-chat.txt


def test_a_failed_preparation_keeps_the_button_and_is_retried(checkouts, tmp_path, monkeypatch):
    local, other = checkouts
    offline = tmp_path / "offline"
    offline.touch()
    ran = []

    def steps(paths):
        ran.append(paths)
        return [("pip install", local, [sys.executable, "-c", f"import os, sys; sys.exit(os.path.exists({str(offline)!r}))"])]
    monkeypatch.setattr(update, "steps", steps)
    (other / "requirements-chat.txt").write_text("x\n")
    git(other, "add", "requirements-chat.txt")
    git(other, "commit", "-q", "-m", "needs a package")
    git(other, "push", "-q", "origin", "HEAD:main")

    failed = update.apply()
    assert failed["state"] == "available" and failed["error"].startswith("pip install 실패")
    assert update.status()["error"] == failed["error"], "the cached state keeps the failure"
    assert git(local, "log", "-1", "--format=%s") == "needs a package", "the pull itself went through"
    offline.unlink()
    assert update.apply()["state"] == "ready", "pressing it again retries the preparation"
    assert "requirements-chat.txt" in ran[-1] and "error" not in update.status()


def test_a_missing_npm_is_a_failed_step_not_a_lost_card(checkouts, monkeypatch):
    local, other = checkouts

    def missing(name):
        raise FileNotFoundError(f"{name} not found")
    monkeypatch.setattr(update, "cli_command", missing)
    (other / "web").mkdir()
    (other / "web" / "App.tsx").write_text("x\n")
    git(other, "add", "web/App.tsx")
    git(other, "commit", "-q", "-m", "screen")
    git(other, "push", "-q", "origin", "HEAD:main")

    failed = update.apply()
    assert failed["state"] == "available" and failed["error"].startswith("npm run build 실패")
    assert update.status(force=True)["state"] == "available", "the check after the pull never resolves npm"
    monkeypatch.setattr(update, "cli_command", lambda name: [sys.executable, "-c", "pass"])
    assert update.apply()["state"] == "ready"


def test_later_is_kept_by_the_server_across_windows(checkouts):
    local, other = checkouts
    git(other, "commit", "-q", "--allow-empty", "-m", "second")
    git(other, "push", "-q", "origin", "HEAD:main")
    remote = git(other, "rev-parse", "--short=7", "HEAD")
    server = FastAPI()
    server.include_router(update.router)
    client = TestClient(server)
    assert client.get("/api/update").json()["later"] == ""
    assert client.post("/api/update/later", json={"remote": remote}).json()["later"] == remote
    assert update.status()["later"] == remote, "a window on a new port reads the same answer"
    assert client.post("/api/update/later", json={"remote": "../x"}).status_code == 422
