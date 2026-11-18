"""Release step 3: one attempt from press to published Release, and every row of the restart table
(`docs/plans/release/0-overview.md`, "One attempt" and "Restart and collisions"), over a fake GitHub."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import android_owned
from main import github_release as gh
from main import release, release_publish as rp
from test_github_release import FakeGitHub

SHA, OTHER, CERT = "a" * 40, "b" * 40, "c" * 64
ANDROID = {"build": "build", "artifact": "out.apk", "application_id": "dev.o.r", "abi": "arm64-v8a"}


@pytest.fixture
def press(tmp_path, monkeypatch):
    """A press against `github.com/o/r` with the build, the key and the APK tools faked. A fake APK is the JSON
    of what `aapt2` and `apksigner` would say about it."""

    fake = FakeGitHub("o/r").install(monkeypatch)
    keys = []
    monkeypatch.setattr(rp, "LEDGER", tmp_path / "ledger")
    monkeypatch.setattr(release, "WORK", tmp_path / "work")
    monkeypatch.setattr(release, "tools", lambda: {})
    monkeypatch.setattr(release, "remote", lambda repo: "github.com/o/r")
    monkeypatch.setattr(release, "pin", lambda repo, ref="origin/main": SHA)
    monkeypatch.setattr(release, "settings", lambda repo, sha: {"version_source": "v.json", "android": ANDROID})
    monkeypatch.setattr(release, "seed", lambda repo, sha, source: "0.1.0")
    monkeypatch.setattr(release, "release_key", lambda repo, kit, create=False: keys.append(create) or {"cert": CERT})
    monkeypatch.setattr(release, "git", lambda repo, *args, **k: "feat: one (#1)\nfix: two (#2)\n")

    def checkout(repo, sha, attempt):
        into = release.place(repo, attempt)
        into.mkdir(parents=True)
        return into

    def build(src, android, version, kit, halt):
        apk = src / android["artifact"]
        apk.write_text(json.dumps({"package": android["application_id"], "code": release.code(version),
                                   "name": version, "abis": [android["abi"]], "debuggable": False}))
        return apk

    def sign(apk, out, key, kit):
        out.write_text(json.dumps({**json.loads(apk.read_text()), "certs": [key["cert"]]}))
        return out

    monkeypatch.setattr(release, "checkout", checkout)
    monkeypatch.setattr(release, "build", build)
    monkeypatch.setattr(release, "sign", sign)
    monkeypatch.setattr(release, "facts", lambda apk, kit: {**json.loads(Path(apk).read_text()),
                                                            "sha256": android_owned.sha256(Path(apk))})
    return fake, tmp_path / "r", keys


def attempts():
    return rp.ledger("github.com/o/r")["attempts"]


def test_the_first_press_publishes_the_seed_with_its_note_and_makes_the_key(press):
    fake, repo, keys = press
    done = rp.publish(repo, "0.1.0", None)

    rel = fake.release_named("v0.1.0")
    assert done["state"] == "published" and keys == [True] and fake.refs["v0.1.0"] == SHA
    assert not rel["draft"] and not rel["prerelease"] and rel["target_commitish"] == SHA
    assert release.FIRST_RELEASE_NOTE in rel["body"] and "- feat: one (#1)" in rel["body"]
    assert [a["name"] for a in rel["assets"]] == ["r-0.1.0-arm64-v8a.apk"]
    assert done["assets"] == [{"name": "r-0.1.0-arm64-v8a.apk", "sha256": done["manifest"][0]["sha256"],
                               "url": rel["assets"][0]["browser_download_url"]}]
    assert done["release_id"] == rel["id"] and not release.place(repo, done["id"]).exists()


def test_the_notes_tell_no_changes_apart_from_a_list_that_could_not_be_read(monkeypatch):
    monkeypatch.setattr(release, "git", lambda repo, *args, **k: "")
    assert rp.notes(Path("r"), SHA, OTHER, False) == "이전 릴리스 뒤로 병합된 변경이 없다."

    def broken(repo, *args, **k):
        raise ValueError("git log 실패")

    monkeypatch.setattr(release, "git", broken)
    assert rp.notes(Path("r"), SHA, OTHER, True).endswith("변경 목록을 만들지 못했다.")


def test_the_next_press_is_checked_against_the_previous_production_apk_on_github(press, monkeypatch):
    fake, repo, keys = press
    rp.publish(repo, "0.1.0", None)
    with pytest.raises(ValueError, match="patch, minor, major"):
        rp.publish(repo, "0.1.1", None)   # after a release there is something to bump: a choice is needed
    seen = []
    real = release.refused
    monkeypatch.setattr(release, "refused", lambda *a: seen.append(a[4]) or real(*a))
    done = rp.publish(repo, "0.1.1", "patch")

    assert done["state"] == "published" and keys == [True, False] and seen[0]["code"] == 1000
    assert "FIRST" not in fake.release_named("v0.1.1")["body"] and release.FIRST_RELEASE_NOTE not in done["notes"]


def test_a_press_whose_version_moved_or_whose_remote_is_busy_writes_nothing(press):
    fake, repo, _ = press
    fake.refs["v0.1.0"] = OTHER   # someone took the seed's tag since the card was drawn
    with pytest.raises(ValueError, match="0.1.1"):
        rp.publish(repo, "0.1.0", None)
    assert attempts() == [] and not fake.rels

    rp.reserve("github.com/o/r", {"id": "x", "tag": "v9.9.9", "state": "pending"})
    with pytest.raises(ValueError, match="진행 중"):
        rp.publish(repo, "0.1.1", None)
    with pytest.raises(ValueError, match="진행 중"):
        rp.reserve("github.com/o/r", {"id": "y", "tag": "v9.9.9", "state": "pending"})


def test_a_refused_build_ends_failed_with_nothing_on_github(press, monkeypatch):
    fake, repo, _ = press
    monkeypatch.setattr(release, "refused", lambda *a: ["디버그 빌드다"])
    done = rp.publish(repo, "0.1.0", None)
    assert done["state"] == "failed" and "디버그" in done["reason"] and not fake.rels and not fake.refs


def test_a_tag_at_another_commit_ends_the_attempt_and_is_left_as_it_is(press, monkeypatch):
    fake, repo, _ = press
    monkeypatch.setattr(gh, "tags", lambda repo: [])   # it appears after the press was accepted
    fake.refs["v0.1.0"] = OTHER
    done = rp.publish(repo, "0.1.0", None)
    assert done["state"] == "failed" and "가리킨다" in done["reason"] and fake.refs["v0.1.0"] == OTHER
    assert not fake.rels


def test_a_draft_whose_answer_was_lost_is_found_by_its_attempt_and_not_made_twice(press):
    fake, repo, _ = press
    fake.lose.add("POST releases")
    done = rp.publish(repo, "0.1.0", None)
    assert done["state"] == "published" and len(fake.rels) == 1 and f"{rp.MARK} {done['id']}" in fake.rels[0]["body"]


def test_a_cut_off_upload_is_deleted_and_sent_again(press):
    fake, repo, _ = press
    fake.cut.add("r-0.1.0-arm64-v8a.apk")
    done = rp.publish(repo, "0.1.0", None)
    assets = fake.release_named("v0.1.0")["assets"]
    assert done["state"] == "published" and [a["state"] for a in assets] == ["uploaded"]
    assert gh.digest(assets[0]) == done["manifest"][0]["sha256"]


def test_a_complete_asset_that_differs_ends_the_attempt_and_leaves_its_draft_and_tag_recorded(press, monkeypatch):
    fake, repo, _ = press
    real = gh.create

    def create(*args, **kwargs):   # someone else's file lands in the draft before ours
        held = real(*args, **kwargs)
        fake._store(fake.rels[-1], "r-0.1.0-arm64-v8a.apk", b"not ours", "uploaded")
        return held

    monkeypatch.setattr(gh, "create", create)
    done = rp.publish(repo, "0.1.0", None)
    rel = fake.release_named("v0.1.0")
    assert done["state"] == "failed" and "digest 가 다르다" in done["reason"]
    assert rel["draft"] and fake.blobs[rel["assets"][0]["id"]] == b"not ours"   # never replaced, never deleted
    assert done["release_id"] == rel["id"] and done["tagged"] and fake.refs["v0.1.0"] == SHA

    monkeypatch.setattr(gh, "create", real)
    again = rp.publish(repo, "0.1.1", None)   # the taken seed is skipped
    assert again["state"] == "published" and again["tag"] == "v0.1.1"


def test_a_publish_whose_answer_was_lost_is_read_back_and_recorded(press):
    fake, repo, _ = press
    fake.lose.add("PATCH releases/{id}")
    done = rp.publish(repo, "0.1.0", None)
    assert done["state"] == "published" and not fake.release_named("v0.1.0")["draft"]


def test_a_transient_failure_leaves_the_attempt_pending_and_recover_finishes_it(press):
    fake, repo, _ = press
    fake.fail["POST releases"] = [502]
    with pytest.raises(gh.Failed):
        rp.publish(repo, "0.1.0", None)
    assert rp.pending("github.com/o/r")["tagged"] and not fake.rels

    done = rp.recover()
    assert [d["state"] for d in done] == ["published"] and len(fake.rels) == 1
    assert attempts()[0]["state"] == "published" and rp.pending("github.com/o/r") is None


def test_a_release_published_before_its_ledger_write_is_only_read_and_recorded(press, monkeypatch):
    fake, repo, _ = press
    real = rp._ended

    def crashed(record, state, **fields):   # the server dies after the publish, before the ledger says so
        if state == "published":
            raise SystemExit("server stopped")
        return real(record, state, **fields)

    monkeypatch.setattr(rp, "_ended", crashed)
    with pytest.raises(SystemExit):
        rp.publish(repo, "0.1.0", None)
    monkeypatch.setattr(rp, "_ended", real)
    before = len(fake.calls)

    assert [d["state"] for d in rp.recover()] == ["published"] and len(fake.rels) == 1
    writes = [c for c in fake.calls[before:] if c[0] != "GET"]
    assert writes == []   # read-only from the moment it is public


def test_a_public_release_that_differs_from_the_attempt_is_left_and_the_attempt_fails(press):
    fake, repo, _ = press
    rp.publish(repo, "0.1.0", None)
    rel = fake.release_named("v0.1.0")
    record = {**attempts()[0], "id": "replay", "state": "pending"}
    record["manifest"] = [{**record["manifest"][0], "sha256": "0" * 64}]
    Path(record["manifest"][0]["path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["manifest"][0]["path"]).write_text("bytes", encoding="utf-8")
    record["manifest"][0]["sha256"] = android_owned.sha256(Path(record["manifest"][0]["path"]))
    rp.reserve("github.com/o/r", record)

    done = rp.finish("github.com/o/r", "replay")
    assert done["state"] == "failed" and "다르다" in done["reason"] and not rel["draft"] and len(rel["assets"]) == 1


def test_an_attempt_that_stopped_before_its_manifest_ends_failed_on_restart(press):
    _, repo, _ = press
    rp.reserve("github.com/o/r", {"id": "early", "remote": "github.com/o/r", "repo": str(repo), "tag": "v0.1.0",
                                  "sha": SHA, "state": "pending"})
    assert [d["state"] for d in rp.recover()] == ["failed"]
    assert "빌드가 끝나기 전에" in attempts()[0]["reason"]
