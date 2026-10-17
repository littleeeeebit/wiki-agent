"""The update card: a moved `origin/main` is offered, pulled, then waits for a restart."""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from main import app as main_app
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


def test_a_screen_older_than_its_sources_is_rebuilt_before_the_window_reads_it(tmp_path, monkeypatch):
    import os
    import threading

    root = tmp_path / "wiki"
    (root / "web" / "src").mkdir(parents=True)
    (root / "web" / "src-tauri").mkdir()
    for name in ("web/src/lib.ts", "web/src-tauri/main.rs"):
        (root / name).write_text("x", encoding="utf-8")
    git(tmp_path, "init", "-q", str(root))
    git(root, "add", ".")
    index = root / "web" / "dist" / "index.html"
    assert not update.screen_stale(root)   # no build at all is not a stale one
    index.parent.mkdir(parents=True)
    index.write_text("old", encoding="utf-8")
    os.utime(index, (1000, 1000))
    for name in ("web/src/lib.ts", "web/src-tauri/main.rs"):
        os.utime(root / name, (900, 900))
    assert not update.screen_stale(root)
    os.utime(root / "web/src-tauri/main.rs", (2000, 2000))   # the shell is not the screen
    assert not update.screen_stale(root)
    os.utime(root / "web/src/lib.ts", (2000, 2000))           # a checkout moved the screen's source
    assert update.screen_stale(root)

    release, ran, fail = threading.Event(), [], []
    (index.parent / "assets").mkdir()
    (index.parent / "assets" / "old.js").write_text("old", encoding="utf-8")

    def build(command, **kwargs):
        out = Path(command[command.index("--outDir") + 1])
        ran.append((command[1:3], kwargs["cwd"]))
        assert release.wait(10)
        (out / "assets").mkdir()
        (out / "assets" / "new.js").write_text("new", encoding="utf-8")
        if fail:   # broke half-way: something written, no index
            return subprocess.CompletedProcess(command, 1, "", "tsc failed")
        (out / "index.html").write_text("new", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(update.subprocess, "run", build)
    monkeypatch.setattr(update, "cli_command", lambda name: [name])
    monkeypatch.setattr(update.errorlog, "record", lambda *a, **k: None)
    monkeypatch.setattr(main_app, "DIST", index.parent)
    monkeypatch.setattr(update, "screen_stale", lambda root=None: True)
    screen = TestClient(main_app.app, base_url="http://127.0.0.1:8787")

    fail.append(1)
    update.rebuild_screen(root)
    ready = update.screen_ready
    monkeypatch.setattr(update, "screen_ready", lambda timeout=60: ready(0.2))   # `/` gives up waiting
    loading = screen.get("/")   # never the old screen while the new one builds
    assert loading.status_code == 503 and "다시 빌드" in loading.text and loading.headers["retry-after"]
    monkeypatch.setattr(update, "screen_ready", ready)
    release.set()
    assert update.screen_ready(10)
    # A failed build publishes nothing: the last good screen, still stale, for the next start.
    assert index.read_text(encoding="utf-8") == "old" and not (index.parent / "assets" / "new.js").exists()

    fail.clear()
    update.rebuild_screen(root)
    assert update.screen_ready(10) and screen.get("/").text == "new"
    assert (index.parent / "assets" / "old.js").exists()   # windows still open on the old build
    assert ran == [(["run", "build"], root / "web")] * 2


def test_a_broken_publish_or_staging_never_breaks_the_screen_or_leaves_it_loading(tmp_path, monkeypatch):
    staged, dist = tmp_path / "staged", tmp_path / "dist"
    for folder, text in ((staged, "new"), (dist, "old")):
        (folder / "assets").mkdir(parents=True)
        (folder / "index.html").write_text(text, encoding="utf-8")
    (staged / "assets" / "same-hash.js").write_text("whole", encoding="utf-8")
    (dist / "assets" / "same-hash.js").write_text("whole", encoding="utf-8")   # the old index uses it
    copy = update.shutil.copy2

    def torn(source, target, **kwargs):   # the disk fills half-way through a file
        Path(target).write_text("wh", encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(update.shutil, "copy2", torn)
    with pytest.raises(OSError):
        update.publish_screen(staged, dist)
    assert (dist / "assets" / "same-hash.js").read_text(encoding="utf-8") == "whole"
    assert (dist / "index.html").read_text(encoding="utf-8") == "old"
    monkeypatch.setattr(update.shutil, "copy2", copy)

    recorded = []

    def full(**kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(update.tempfile, "mkdtemp", full)
    monkeypatch.setattr(update.errorlog, "record", lambda where, exc: recorded.append(where))
    monkeypatch.setattr(update, "screen_stale", lambda root=None: True)
    update.rebuild_screen(tmp_path)
    assert update.screen_ready(10) and recorded == ["screen-build"]


def test_only_the_checkouts_owner_rebuilds_its_screen(monkeypatch):
    from main import runtime, specs

    built = []
    monkeypatch.setattr(update, "rebuild_screen", lambda root=None: built.append(1))
    monkeypatch.setattr(main_app.app.state, "rebuild_screen", True, raising=False)
    with runtime.server_owner(specs.SPECS.parent):   # the running server
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            with TestClient(main_app.app):
                pass
    assert built == []
    with TestClient(main_app.app):
        pass
    assert built == [1]


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
