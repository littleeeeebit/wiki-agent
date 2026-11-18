"""The release card and its button (`main.release_api`): what a press would publish, and the locked publish."""

from __future__ import annotations

import subprocess
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main import release, release_api, release_publish as rp
from test_github_release import FakeGitHub


def git(repo, *args) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr + done.stdout
    return done.stdout.strip()


def commit(repo, n):
    for _ in range(n):
        git(repo, "commit", "-q", "--allow-empty", "-m", "change")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def project(monkeypatch, tmp_path):
    """A connected project whose `main` has three commits, a fake GitHub and the release reads around them."""

    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    first = commit(repo, 1)
    head = commit(repo, 2)
    fake = FakeGitHub("o/r", main=head).install(monkeypatch)
    monkeypatch.setattr(rp, "LEDGER", tmp_path / "ledger")
    monkeypatch.setattr(release_api.channels, "repo_for", lambda name: repo if name == "app" else None)
    monkeypatch.setattr(release, "remote", lambda repo: "github.com/o/r")
    monkeypatch.setattr(release, "pin", lambda repo, ref="origin/main": head)
    monkeypatch.setattr(release, "settings", lambda repo, sha: {"version_source": "v.json", "android": {}})
    monkeypatch.setattr(release, "seed", lambda repo, sha, source: "0.1.0")
    monkeypatch.setattr(release_api, "_runs", {})
    app = FastAPI()
    app.include_router(release_api.router)
    return TestClient(app), fake, first


def test_before_a_first_release_the_card_shows_the_seed_and_offers_no_choice(project):
    client, fake, _ = project
    fake.add("android-test-1721b04", prerelease=True)   # a test prerelease never counts
    card = client.get("/api/release/app").json()

    assert card["last"] is None and card["versions"] == {"first": "0.1.0"}
    assert card["ahead"] == 3 and "잠겨 있다" in card["locked"] and card["rehearsal"] is None


def test_the_card_shows_each_choice_from_the_last_production_release_and_the_commits_since(project):
    client, fake, first = project
    fake.add("v0.1.0", target=first)
    fake.add("v0.1.1")
    fake.refs["v0.1.1"] = first
    fake.refs["v0.1.2"] = first   # a taken version is skipped, as the attempt skips it
    card = client.get("/api/release/app").json()

    assert card["last"] == "v0.1.1" and card["ahead"] == 2
    assert card["versions"] == {"patch": "0.1.3", "minor": "0.2.0", "major": "1.0.0"}


def test_a_repository_without_a_release_table_shows_why(project, monkeypatch):
    client, *_ = project

    def missing(repo, sha):
        raise ValueError("`.wiki/adapter.toml` 에 `[release]` 표가 없다")

    monkeypatch.setattr(release, "settings", missing)
    card = client.get("/api/release/app").json()
    assert "[release]" in card["error"] and "잠겨 있다" in card["locked"]
    assert client.get("/api/release/elsewhere").status_code == 404


def test_the_button_runs_one_unpublished_rehearsal_that_a_stop_ends(project, monkeypatch):
    client, fake, _ = project
    entered, asked = threading.Event(), []

    def rehearse(repo, ref, halt, repair=None):
        asked.append((ref, repair))
        entered.set()
        halt.wait(10)
        return {"ok": False, "rounds": [{"version": "0.1.0", "failed": "cannot find symbol", "facts": {"x": 1}}],
                "refusals": {}, "repair": {"needed": "cannot find symbol", "error": "멈춤"}}

    monkeypatch.setattr(release, "rehearse", rehearse)
    assert client.post("/api/release/app/rehearse").json()["rehearsal"]["state"] == "running"
    assert entered.wait(5)
    assert client.post("/api/release/app/rehearse").status_code == 409   # one at a time
    assert client.post("/api/release/app/stop").status_code == 200
    for _ in range(100):
        run = client.get("/api/release/app").json()["rehearsal"]
        if run["state"] != "running":
            break
        threading.Event().wait(0.05)

    assert run["state"] == "done" and run["stopped"] and "halt" not in run
    assert run["rounds"] == [{"version": "0.1.0", "failed": "cannot find symbol", "refused": []}]   # no facts
    assert asked == [("main", release_api.release_repair.start)]   # a source failure goes to a repair task
    assert not fake.rels and not [c for c in fake.calls if c[0] != "GET"]   # nothing written to GitHub
    assert client.post("/api/release/app/stop").status_code == 409


def test_a_rehearsal_that_raises_is_shown_and_the_next_press_runs_again(project, monkeypatch):
    client, *_ = project

    def broken(repo, ref, halt, repair=None):
        raise ValueError("`main` 는 origin 의 브랜치가 아니다")

    monkeypatch.setattr(release, "rehearse", broken)
    client.post("/api/release/app/rehearse")
    for _ in range(100):
        run = client.get("/api/release/app").json()["rehearsal"]
        if run["state"] != "running":
            break
        threading.Event().wait(0.05)
    assert run["state"] == "failed" and "origin" in run["error"]
    assert client.post("/api/release/app/rehearse").status_code == 200


def test_server_start_carries_pending_attempts_on_without_waiting_for_them(monkeypatch):
    swept, recorded = threading.Event(), []

    def recover():
        swept.set()
        return [{"remote": "github.com/o/r", "state": "pending", "last_error": "Failed: HTTP 502"}]

    monkeypatch.setattr(rp, "recover", recover)
    monkeypatch.setattr(release_api.errorlog, "record", lambda source, exc, **k: recorded.append((source, str(exc), k)))
    release_api.recover()
    assert swept.wait(5)
    for _ in range(100):
        if recorded:
            break
        threading.Event().wait(0.02)
    assert recorded == [("release-recover", "Failed: HTTP 502", {"remote": "github.com/o/r"})]
