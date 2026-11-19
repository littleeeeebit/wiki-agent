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
    monkeypatch.setattr(release, "pin", lambda repo, ref: head if ref == "origin/main" else pytest.fail(ref))
    monkeypatch.setattr(release, "settings", lambda repo, sha: {"version_source": "v.json", "android": {}})
    monkeypatch.setattr(release, "seed", lambda repo, sha, source: "0.1.0")
    monkeypatch.setattr(release_api, "_runs", {})
    monkeypatch.setattr(release_api, "_deadline", [])
    monkeypatch.setattr(release_api, "_revs", {})
    monkeypatch.setattr(release_api, "_boot", [1])
    monkeypatch.setattr(release_api, "BOOTS", tmp_path / "release-boot")
    monkeypatch.setattr(release_api.runtime, "stopping", threading.Event())
    app = FastAPI()
    app.include_router(release_api.router)
    return TestClient(app), fake, first


def test_before_a_first_release_the_card_shows_the_seed_and_offers_no_choice(project):
    client, fake, _ = project
    fake.add("android-test-1721b04", prerelease=True)   # a test prerelease never counts
    card = client.get("/api/release/app").json()

    assert card["last"] is None and card["versions"] == {"first": "0.1.0"}
    assert card["ahead"] == 3 and "잠겨 있다" in card["locked"] and card["rehearsal"]["state"] == "idle"


def test_the_card_shows_each_choice_from_the_last_production_release_and_the_commits_since(project):
    client, fake, first = project
    fake.add("v0.1.0", target=first)
    fake.add("v0.1.1")
    fake.refs["v0.1.1"] = first
    fake.refs["v0.1.2"] = first   # a taken version is skipped, as the attempt skips it
    card = client.get("/api/release/app").json()

    assert card["last"] == "v0.1.1" and card["ahead"] == 2
    assert card["versions"] == {"patch": "0.1.3", "minor": "0.2.0", "major": "1.0.0"} and card["refused"] == {}


def test_a_choice_android_cannot_carry_leaves_the_others_and_a_lost_tag_counts_nothing(project):
    client, fake, first = project
    fake.add("v1.999.0", target=first)
    del fake.refs["v1.999.0"]   # listed, but its tag is gone
    card = client.get("/api/release/app").json()

    assert card["versions"] == {"patch": "1.999.1", "major": "2.0.0"} and "versionCode" in card["refused"]["minor"]
    assert card["last"] == "v1.999.0" and card["ahead"] is None   # unknown, never the whole history


def test_a_repository_without_a_release_table_shows_why(project, monkeypatch):
    client, *_ = project

    def missing(repo, sha):
        raise ValueError("`.wiki/adapter.toml` 에 `[release]` 표가 없다")

    monkeypatch.setattr(release, "settings", missing)
    card = client.get("/api/release/app").json()
    assert "[release]" in card["error"] and "잠겨 있다" in card["locked"]
    assert client.get("/api/release/elsewhere").status_code == 404


def ended(client):
    for _ in range(200):
        run = client.get("/api/release/app/rehearsal").json()["rehearsal"]   # the state alone, no GitHub read
        if run["state"] != "running":
            return run
        threading.Event().wait(0.05)
    raise AssertionError("the test build never ended")


def stop(client):
    """A stop of the build the card shows, as the card sends it: by that build's ID."""

    run = client.get("/api/release/app/rehearsal").json()["rehearsal"]
    return client.post("/api/release/app/stop", params={"run_id": run.get("id", "")})


def test_the_button_runs_one_unpublished_rehearsal_of_the_cards_commit_that_a_stop_ends(project, monkeypatch):
    client, fake, _ = project
    entered, asked = threading.Event(), []

    def rehearse(repo, ref, halt, repair=None, expect=None):
        asked.append(ref)
        entered.set()
        halt.wait(10)
        return {"ok": False, "rounds": [{"version": "0.1.0", "failed": "cannot find symbol", "facts": {"x": 1}}],
                "refusals": {}, "repair": {"needed": "cannot find symbol", "error": "멈춤"}}

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", rehearse)
    assert client.post("/api/release/app/rehearse").json()["rehearsal"]["state"] == "running"
    assert entered.wait(5)
    assert client.post("/api/release/app/rehearse").status_code == 409   # one at a time
    assert stop(client).status_code == 200
    run = ended(client)

    assert run["state"] == "done" and run["stopped"] and "halt" not in run and "thread" not in run
    assert run["rounds"] == [{"version": "0.1.0", "failed": "cannot find symbol", "refused": []}]   # no facts
    assert asked == ["origin/main"]   # the commit the card pinned, never a stale local `main`
    assert not fake.rels and not [c for c in fake.calls if c[0] != "GET"]   # nothing written to GitHub
    assert stop(client).status_code == 409


@pytest.mark.parametrize("first", ["stop", "repair"])
def test_a_stop_and_the_start_of_a_repair_never_both_happen(project, monkeypatch, first):
    client, *_ = project
    building, go, starting, release_it, started = (threading.Event() for _ in range(5))

    def rehearse(repo, ref, halt, repair=None, expect=None):
        building.set()
        assert go.wait(5)
        try:   # past `release.handed`'s own check: the decision under the run's lock is what remains
            task = repair(repo, {"repair": {"needed": "boom"}})
        except RuntimeError as exc:
            return {"ok": False, "rounds": [], "refusals": {}, "repair": {"needed": "boom", "error": str(exc)}}
        return {"ok": False, "rounds": [], "refusals": {}, "repair": {"needed": "boom", "task": task}}

    def start(repo, record):
        starting.set()
        assert release_it.wait(5)
        return started.set() or "release-fix-0-1-0"

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", rehearse)
    monkeypatch.setattr(release_api.release_repair, "start", start)
    client.post("/api/release/app/rehearse")
    assert building.wait(5)
    if first == "stop":
        assert stop(client).status_code == 200
        go.set()
        run = ended(client)
        assert not started.is_set() and "멈춤" in run["repair"]["error"] and run["stopped"]
    else:
        go.set()
        assert starting.wait(5)
        assert client.get("/api/release/app/rehearsal").json()["rehearsal"]["repairing"]
        assert stop(client).status_code == 409   # too late to stop
        release_it.set()
        run = ended(client)
        assert run["repair"]["task"] == "release-fix-0-1-0" and not run["stopped"] and "repairing" not in run


def test_a_rehearsal_that_raises_is_shown_and_the_next_press_runs_again(project, monkeypatch):
    client, *_ = project

    def broken(repo, ref, halt, repair=None, expect=None):
        raise ValueError("`main` 는 origin 의 브랜치가 아니다")

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", broken)
    client.post("/api/release/app/rehearse")
    run = ended(client)
    assert run["state"] == "failed" and "origin" in run["error"]
    assert client.post("/api/release/app/rehearse").status_code == 200


def test_server_shutdown_stops_a_running_test_build_and_waits_for_its_cleanup(project, monkeypatch):
    client, *_ = project
    building, cleaned = threading.Event(), threading.Event()

    def rehearse(repo, ref, halt, repair=None, expect=None):
        building.set()
        halt.wait(10)
        threading.Event().wait(0.2)   # removing the checkout and the keys takes a moment
        cleaned.set()
        raise ValueError("빌드 실패 — 사람이 멈춤")

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", rehearse)
    client.post("/api/release/app/rehearse")
    assert building.wait(5)
    release_api.close_all()
    assert cleaned.is_set() and release_api.shown("app")["stopped"]


def test_once_shutdown_begins_no_test_build_starts(project, monkeypatch):
    client, *_ = project
    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", lambda *a, **k: pytest.fail("started"))
    release_api.runtime.stopping.set()   # `app.close_turns` sets it before `close_all` takes its snapshot
    release_api.close_all()
    assert client.post("/api/release/app/rehearse").status_code == 503 and release_api.shown("app")["state"] == "idle"


def test_a_thread_the_os_refuses_leaves_the_project_free_and_shutdown_whole(project, monkeypatch):
    def refused(self):
        raise RuntimeError("can't start new thread")

    with monkeypatch.context() as m:   # the route alone: a test client starts threads of its own
        m.setattr(threading.Thread, "start", refused)
        with pytest.raises(release_api.HTTPException) as answer:
            release_api.rehearse("app")
    assert answer.value.status_code == 503 and release_api.shown("app")["state"] == "idle"
    release_api.close_all()   # no unstarted thread to join


def test_shutdown_lets_a_started_repair_finish_and_records_a_build_it_could_not_wait_for(project, monkeypatch):
    client, *_ = project
    starting, release_it, recorded = threading.Event(), threading.Event(), []

    def rehearse(repo, ref, halt, repair=None, expect=None):
        task = repair(repo, {"repair": {"needed": "boom"}})
        return {"ok": False, "rounds": [], "refusals": {}, "repair": {"needed": "boom", "task": task}}

    def start(repo, record):
        starting.set()
        assert release_it.wait(5)
        return "release-fix-0-1-0"

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", rehearse)
    monkeypatch.setattr(release_api.release_repair, "start", start)
    monkeypatch.setattr(release_api, "SHUTDOWN_WAIT", 0.2)
    monkeypatch.setattr(release_api.errorlog, "record", lambda source, exc, **k: recorded.append(source))
    client.post("/api/release/app/rehearse")
    assert starting.wait(5)
    release_api.close_all()   # the repair is past the point of stopping: it is waited for, never stopped
    assert recorded == ["release-rehearse-shutdown"] and release_api.shown("app")["state"] == "running"
    release_it.set()
    run = ended(client)
    assert run["repair"]["task"] == "release-fix-0-1-0" and not run["stopped"]


def runs_until_stopped(monkeypatch) -> threading.Event:
    """A test build that runs until it is stopped; the event is set once it runs."""

    entered = threading.Event()

    def rehearse(repo, ref, halt, repair=None, expect=None):
        entered.set()
        halt.wait(10)
        raise release.Stopped("멈춤")

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", rehearse)
    return entered


def test_every_change_of_a_run_carries_a_later_rev_so_an_older_answer_never_wins(project, monkeypatch):
    client, *_ = project
    entered = runs_until_stopped(monkeypatch)
    idle = client.get("/api/release/app/rehearsal").json()["rehearsal"]
    started = client.post("/api/release/app/rehearse").json()["rehearsal"]
    assert entered.wait(5)
    stopping = stop(client).json()["rehearsal"]
    done = ended(client)
    assert idle["state"] == "idle" and stopping["stopping"] and "stopping" not in done
    assert idle["rev"] < started["rev"] < stopping["rev"] < done["rev"]
    assert {idle["boot"], started["boot"], done["boot"]} == {1}   # this start's, never a clock's


def test_each_server_start_is_numbered_after_the_last_whatever_the_clock(project, monkeypatch, tmp_path):
    client, *_ = project
    before = client.get("/api/release/app/rehearsal").json()["rehearsal"]
    monkeypatch.setattr(release_api.release_publish, "recover", lambda: [])
    monkeypatch.setattr(release_api.time, "time_ns", lambda: 0)   # a clock set back changes nothing
    release_api.recover()   # the next start
    release_api.recover()   # and the one after it
    after = client.get("/api/release/app/rehearsal").json()["rehearsal"]
    assert (before["boot"], after["boot"]) == (1, 2)
    assert release_api.started() == 3
    (tmp_path / "release-boot").write_text("garbled")   # unreadable: restarts above any count, from the clock
    monkeypatch.setattr(release_api.time, "time_ns", lambda: 5_000_000_000)
    assert release_api.started() == 5_000_001 and release_api.started() == 5_000_002


def test_a_stop_meant_for_an_ended_build_never_stops_the_next(project, monkeypatch):
    client, *_ = project
    entered = runs_until_stopped(monkeypatch)
    first = client.post("/api/release/app/rehearse").json()["rehearsal"]
    assert entered.wait(5)
    client.post("/api/release/app/stop", params={"run_id": first["id"]})
    ended(client)
    entered.clear()
    second = client.post("/api/release/app/rehearse").json()["rehearsal"]
    assert entered.wait(5) and second["id"] != first["id"]
    late = client.post("/api/release/app/stop", params={"run_id": first["id"]})   # the first build's, delayed
    assert late.status_code == 409 and not release_api._runs["app"]["halt"].is_set()
    unnamed = client.post("/api/release/app/stop")   # an old card's, naming no build
    assert unnamed.status_code == 409 and not release_api._runs["app"]["halt"].is_set()
    client.post("/api/release/app/stop", params={"run_id": second["id"]})
    ended(client)


def test_the_card_says_when_its_reading_began_apart_from_the_run_it_carries(project, monkeypatch):
    client, *_ = project
    began = []

    def card(repo):   # a build ends while the card is still reading GitHub
        with release_api._lock:
            began.append(release_api._revs.get("app", 0))
            release_api._rev("app")
        return {"sha": "b" * 40, "locked": release_api.LOCKED}

    monkeypatch.setattr(release_api, "card", card)
    got = client.get("/api/release/app").json()
    assert got["read"] == began[0] < got["rehearsal"]["rev"]   # so the card knows to read itself again


def test_a_new_server_lifespan_gets_its_own_whole_shutdown_wait(project, monkeypatch):
    client, *_ = project
    monkeypatch.setattr(rp, "recover", lambda: [])
    release_api._deadline.append(0.0)   # an earlier lifespan in this process spent its wait long ago
    release_api.recover()   # the lifespan's start, once it owns the server
    building, cleaned = threading.Event(), threading.Event()

    def rehearse(repo, ref, halt, repair=None, expect=None):
        building.set()
        halt.wait(10)
        threading.Event().wait(0.2)
        cleaned.set()
        raise release.Stopped("멈춤")

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", rehearse)
    client.post("/api/release/app/rehearse")
    assert building.wait(5)
    release_api.close_all()
    assert cleaned.is_set()   # waited for, not cut off by the old deadline


def test_the_start_carries_the_cards_commit_and_a_failure_carries_what_it_left(project, monkeypatch):
    client, *_ = project
    got = []

    def rehearse(repo, ref, halt, repair=None, expect=None):
        got.append(expect)
        exc = ValueError("`origin/main` 가 카드를 읽은 뒤 움직였다")
        exc.left = ["C:/work/x/rehearsal.jks: locked"]
        raise exc

    monkeypatch.setattr(release_api.release_rehearsal, "rehearse", rehearse)
    client.post("/api/release/app/rehearse", params={"sha": "a" * 40})
    run = ended(client)
    assert got == ["a" * 40] and run["state"] == "failed" and run["left"] == ["C:/work/x/rehearsal.jks: locked"]


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
