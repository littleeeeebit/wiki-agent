"""The update card: a moved `origin/main` is offered, pulled, then waits for a restart."""

import subprocess

import pytest
from fastapi import HTTPException

from main import update


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_offers_pulls_and_waits_for_restart(tmp_path, monkeypatch):
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
    monkeypatch.setattr(update, "STARTED", git(local, "rev-parse", "HEAD"))
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
