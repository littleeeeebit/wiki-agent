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
BUILD = release.build   # the real one, before `press` fakes it


@pytest.fixture
def press(tmp_path, monkeypatch):
    """A press against `github.com/o/r` with the build, the key and the APK tools faked. A fake APK is the JSON
    of what `aapt2` and `apksigner` would say about it."""

    fake = FakeGitHub("o/r").install(monkeypatch)
    keys = []
    monkeypatch.setattr(rp, "LEDGER", tmp_path / "ledger")
    monkeypatch.setattr(rp, "WAITS", (0, 0))   # transient tries without the waits
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
    real = release.built_wrong
    monkeypatch.setattr(release, "built_wrong", lambda *a: seen.append(a[3]) or real(*a))
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
    monkeypatch.setattr(release, "built_wrong", lambda *a: ["디버그 빌드다"])
    done = rp.publish(repo, "0.1.0", None)
    assert done["state"] == "failed" and "디버그" in done["reason"] and not fake.rels and not fake.refs
    assert done["repair"] == {"needed": "디버그 빌드다"}   # no server here to start the repair task


# -- Step 4: failures ---------------------------------------------------------------------------------

def _broken(monkeypatch, said="빌드 실패 — 1 로 끝났다\nerror: cannot find symbol"):
    def build(*args):
        raise release.BuildFailed(said)

    monkeypatch.setattr(release, "build", build)


def test_a_build_failure_ends_the_attempt_and_goes_to_a_repair_task(press, monkeypatch):
    fake, repo, _ = press
    _broken(monkeypatch)
    asked = []
    done = rp.publish(repo, "0.1.0", None, repair=lambda r, record: asked.append((r, record)) or "release-fix-0-1-0")

    assert done["state"] == "failed" and not fake.rels and not fake.refs   # its version is not taken
    assert done["repair"] == {"needed": done["reason"], "task": "release-fix-0-1-0"} and attempts()[0] == done
    assert asked[0][0] == repo and "cannot find symbol" in asked[0][1]["repair"]["needed"]
    assert not release.place(repo, done["id"]).exists() and rp.pending("github.com/o/r") is None


def test_a_wrong_certificate_never_goes_to_a_repair_task(press, monkeypatch):
    fake, repo, _ = press
    monkeypatch.setattr(release, "signed_wrong", lambda *a: ["서명 인증서가 다르다"])
    done = rp.publish(repo, "0.1.0", None, repair=lambda *a: pytest.fail("a key problem is no source change"))
    assert done["state"] == "failed" and "repair" not in done


def test_a_repair_that_cannot_start_is_recorded_and_the_attempt_still_ends(press, monkeypatch):
    from main import release_repair

    _, repo, _ = press
    _broken(monkeypatch)

    def refused(r, record):
        raise release_repair.Started("release-fix-0-1-0", RuntimeError("이 저장소에서 다른 작업이 돌고 있다"))

    done = rp.publish(repo, "0.1.0", None, repair=refused)
    assert done["state"] == "failed" and done["repair"]["task"] == "release-fix-0-1-0"
    assert "다른 작업" in done["repair"]["error"]


SOURCE_ERROR = "error TS2307: Cannot find module './theme.js' ghp_" + "x" * 36


@pytest.mark.parametrize("ending, repairable", [
    ("stopped", False), ("deadline", False), ("missing cmdlet", False), ("missing command", False),
    ("missing repository script", True), ("early optional probe", True), ("source", True)])
def test_only_a_build_the_source_broke_needs_a_repair(monkeypatch, tmp_path, ending, repairable):
    halt = release.threading.Event()
    if ending == "stopped":
        halt.set()   # a person's stop, without a cut reason of its own
    said = {"missing cmdlet": "cargo : The term 'cargo' is not recognized as the name of a cmdlet",
            "missing command": "> tauri android build\nbash: rustup: command not found\nnpm ERR! code 127",
            "missing repository script": "The term './scripts/package-android.ps1' is not recognized as the "
                                         "name of a cmdlet",
            "early optional probe": "\n".join(["bash: rustup: command not found", *(["compiling"] * 15),
                                               SOURCE_ERROR]),
            "source": SOURCE_ERROR}.get(ending, "")
    monkeypatch.setattr(release, "environment", lambda kit: {"PATH": str(tmp_path / "bin")})   # holds no tools
    monkeypatch.setattr(release.specs, "gate", lambda *a, **k: (
        None if ending == "deadline" else 1, said, {"deadline": "시간 초과"}.get(ending, "")))
    (src := tmp_path / "src").mkdir()
    with pytest.raises(ValueError) as failed:
        BUILD(src, ANDROID, "0.1.0", {}, halt)
    assert isinstance(failed.value, release.BuildFailed) is repairable
    assert "ghp_" not in str(failed.value)   # a token a build prints is redacted before anything keeps it


def test_a_build_whose_apk_cannot_be_read_needs_a_repair(monkeypatch, tmp_path):
    def made(cmd, path, halt, env, **_):
        (path / "out.apk").write_bytes(b"not an apk")
        return 0, "", ""

    def badging(argv, env=None, timeout=300):
        raise ValueError("aapt2 실패 — ERROR: dump failed because no AndroidManifest.xml found")

    monkeypatch.setattr(release, "environment", lambda kit: {})
    monkeypatch.setattr(release.specs, "gate", made)
    monkeypatch.setattr(release, "run", badging)
    (src := tmp_path / "src").mkdir()
    with pytest.raises(release.BuildFailed, match="APK 로 읽지 못했다"):
        BUILD(src, ANDROID, "0.1.0", {"aapt2": "aapt2"}, release.threading.Event())


def test_a_build_that_makes_no_artifact_needs_a_repair(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "environment", lambda kit: {})
    monkeypatch.setattr(release.specs, "gate", lambda *a, **k: (0, "", ""))
    (src := tmp_path / "src").mkdir()
    with pytest.raises(release.BuildFailed, match="out.apk"):
        BUILD(src, ANDROID, "0.1.0", {}, release.threading.Event())


def test_a_transient_failure_before_the_manifest_ends_the_attempt_and_takes_no_version(press):
    fake, repo, _ = press
    rp.publish(repo, "0.1.0", None)
    fake.fail["GET git/ref/tags/v0.1.0"] = [502] * (len(rp.WAITS) + 1)   # the previous tag, read for the notes
    done = rp.publish(repo, "0.1.1", "patch")
    assert done["state"] == "failed" and "502" in done["reason"] and "repair" not in done
    assert "v0.1.1" not in fake.refs and rp.pending("github.com/o/r") is None
    assert rp.publish(repo, "0.1.1", "patch")["state"] == "published"   # the same version, pressed again


@pytest.mark.parametrize("where", ["GET releases", "GET git/matching-refs/tags", "POST releases", "POST git/refs", "previous"])
def test_a_transient_failure_is_tried_again_within_the_press(press, monkeypatch, where):
    fake, repo, _ = press
    if where == "previous":
        rp.publish(repo, "0.1.0", None)
        download, cut = gh.download, [503] * len(rp.WAITS)

        def flaky(*args):
            if cut:
                raise gh.Failed("download", cut.pop())
            return download(*args)

        monkeypatch.setattr(gh, "download", flaky)
        assert rp.publish(repo, "0.1.1", "patch")["state"] == "published" and not cut
        return
    fake.fail[where] = [502] * len(rp.WAITS)   # every try but the last
    assert rp.publish(repo, "0.1.0", None)["state"] == "published"


def test_a_stop_during_a_wait_ends_the_tries(press, monkeypatch):
    fake, repo, _ = press
    halt = release.threading.Event()
    halt.set()
    fake.fail["POST releases"] = [502]
    with pytest.raises(gh.Failed):
        rp.publish(repo, "0.1.0", None, halt)
    assert rp.pending("github.com/o/r") and fake.fail["POST releases"] == []


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
    fake.fail["POST releases"] = [502] * (len(rp.WAITS) + 1)   # every try of the press
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
    Path(rp.pending("github.com/o/r")["manifest"][0]["path"]).unlink()   # the local copy is no longer needed
    before = len(fake.calls)

    assert [d["state"] for d in rp.recover()] == ["published"] and len(fake.rels) == 1
    writes = [c for c in fake.calls[before:] if c[0] != "GET"]
    assert writes == []   # read-only from the moment it is public


def test_a_public_release_whose_tag_was_removed_is_never_retagged(press, monkeypatch):
    fake, repo, _ = press
    real = rp._ended
    monkeypatch.setattr(rp, "_ended", lambda record, state, **f: (_ for _ in ()).throw(SystemExit()) if state == "published"
                        else real(record, state, **f))
    with pytest.raises(SystemExit):
        rp.publish(repo, "0.1.0", None)
    monkeypatch.setattr(rp, "_ended", real)
    del fake.refs["v0.1.0"]
    before = len(fake.calls)

    assert [d["state"] for d in rp.recover()] == ["failed"] and "v0.1.0" not in fake.refs
    assert [c for c in fake.calls[before:] if c[0] != "GET"] == []


@pytest.mark.parametrize("change", ["retagged", "moved"])
def test_a_draft_retagged_or_a_tag_moved_during_the_upload_is_never_published(press, monkeypatch, change):
    fake, repo, _ = press
    upload = gh.upload

    def meddled(*args):
        done = upload(*args)
        if change == "retagged":
            fake.rels[-1]["tag_name"] = "v9.0.0"
        else:
            fake.refs["v0.1.0"] = OTHER
        return done

    monkeypatch.setattr(gh, "upload", meddled)
    done = rp.publish(repo, "0.1.0", None)
    refusal = "바뀌었다" if change == "retagged" else "공개하지 않는다"   # a retag is caught on the read after upload
    assert done["state"] == "failed" and refusal in done["reason"] and fake.rels[-1]["draft"]


def test_a_draft_someone_published_during_the_upload_gets_no_more_writes(press, monkeypatch):
    fake, repo, _ = press
    create = gh.create

    def published_at_once(*args, **kwargs):
        held = create(*args, **kwargs)
        fake.rels[-1]["draft"] = False
        return held

    monkeypatch.setattr(gh, "create", published_at_once)
    fake.cut.add("r-0.1.0-arm64-v8a.apk")
    done = rp.publish(repo, "0.1.0", None)
    assert done["state"] == "failed" and "그대로 둔다" in done["reason"]
    assert not [c for c in fake.calls if c[0] in ("DELETE", "PATCH") or c[1].endswith("/assets")]


def test_a_live_worker_keeps_its_attempt_from_recover(press, monkeypatch):
    fake, repo, _ = press
    build, swept = release.build, []

    def building(*args):
        swept.append(rp.recover())   # another process's sweep while this one builds
        return build(*args)

    monkeypatch.setattr(release, "build", building)
    done = rp.publish(repo, "0.1.0", None)
    assert [s["state"] for s in swept[0]] == ["pending"] and "busy" in swept[0][0]
    assert done["state"] == "published"


@pytest.mark.parametrize("lost", ["release_id", "published"])
def test_a_ledger_write_that_fails_after_a_remote_write_keeps_the_attempt_pending(press, monkeypatch, lost):
    fake, repo, _ = press
    real, failed = rp.update, []

    def unwritable(name, attempt, **fields):
        if (lost in fields or fields.get("state") == lost) and not failed:
            failed.append(lost)
            raise rp.Unrecorded("disk full")
        return real(name, attempt, **fields)

    monkeypatch.setattr(rp, "update", unwritable)
    with pytest.raises(rp.Unrecorded):
        rp.publish(repo, "0.1.0", None)
    still = rp.pending("github.com/o/r")
    assert still and Path(still["manifest"][0]["path"]).is_file()   # its files stay for the next finish

    assert [d["state"] for d in rp.recover()] == ["published"] and len(fake.rels) == 1


def test_one_remote_that_fails_never_stops_the_sweep(press, monkeypatch):
    _, repo, _ = press
    for name in ("github.com/o/a", "github.com/o/b"):
        rp.reserve(name, {"id": name[-1], "remote": name, "repo": str(repo), "tag": "v1.0.0", "sha": SHA,
                          "state": "pending"})
    real = rp._finish
    monkeypatch.setattr(rp, "_finish", lambda name, attempt: (_ for _ in ()).throw(RuntimeError("boom"))
                        if name.endswith("/a") else real(name, attempt))
    done = {d["remote"]: d for d in rp.recover()}
    assert "boom" in done["github.com/o/a"]["last_error"] and done["github.com/o/b"]["state"] == "failed"


def _draft_left(fake, monkeypatch, repo, assets=()):
    """An attempt whose server stopped right after its draft was made, before the ledger had its ID."""

    create = gh.create

    def made_then_stopped(*args, **kwargs):
        create(*args, **kwargs)
        for name, data, state in assets:
            fake._store(fake.rels[-1], name, data, state)
        raise SystemExit("server stopped")

    monkeypatch.setattr(gh, "create", made_then_stopped)
    with pytest.raises(SystemExit):
        rp.publish(repo, "0.1.0", None)
    monkeypatch.setattr(gh, "create", create)
    return fake.rels[-1]


@pytest.mark.parametrize("state", ["public", "draft without its local copy"])
def test_a_draft_found_by_its_marker_has_its_id_recorded_first(press, monkeypatch, state):
    fake, repo, _ = press
    draft = _draft_left(fake, monkeypatch, repo)
    if state == "public":
        draft["draft"] = False
    else:
        Path(rp.pending("github.com/o/r")["manifest"][0]["path"]).unlink()
    done = rp.recover()[0]
    assert done["release_id"] == draft["id"] and done["state"] == "failed"


def test_a_draft_already_holding_its_files_publishes_without_the_local_copy(press, monkeypatch):
    fake, repo, _ = press
    signed, sign = [], release.sign   # the APK the press signed, which the draft already holds
    monkeypatch.setattr(release, "sign", lambda *a: signed.append(sign(*a)) or signed[-1])
    draft = _draft_left(fake, monkeypatch, repo)
    fake._store(draft, "r-0.1.0-arm64-v8a.apk", Path(signed[0]).read_bytes(), "uploaded")
    Path(signed[0]).unlink()

    assert [d["state"] for d in rp.recover()] == ["published"]
    assert not [c for c in fake.calls if c[1].endswith("/assets") and c[0] == "POST"]


def test_a_recorded_draft_retagged_before_recovery_is_never_written(press, monkeypatch):
    fake, repo, _ = press
    draft = _draft_left(fake, monkeypatch, repo, [("r-0.1.0-arm64-v8a.apk", b"half", "starter")])
    draft["tag_name"] = "v9.0.0"
    del fake.refs["v0.1.0"]   # so only the check at discovery stands between recovery and a tag push
    before = len(fake.calls)
    done = rp.recover()[0]
    assert done["state"] == "failed" and "바뀌었다" in done["reason"]
    assert [c for c in fake.calls[before:] if c[0] != "GET"] == [] and draft["assets"][0]["state"] == "starter"


def test_a_manifest_whose_bytes_are_gone_writes_nothing_to_github(press, monkeypatch):
    fake, repo, _ = press
    real = rp._finish
    monkeypatch.setattr(rp, "_finish", lambda name, attempt: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):   # stopped right after the manifest, before any remote write
        rp.publish(repo, "0.1.0", None)
    monkeypatch.setattr(rp, "_finish", real)
    Path(rp.pending("github.com/o/r")["manifest"][0]["path"]).unlink()

    done = rp.recover()[0]
    assert done["state"] == "failed" and "사본" in done["reason"]
    assert not fake.refs and not fake.rels   # the version is not consumed


def test_a_copied_marker_never_takes_the_attempt_from_its_own_draft(press, monkeypatch):
    fake, repo, _ = press
    draft = _draft_left(fake, monkeypatch, repo)
    fake.add("v0.0.9", draft=True, body=draft["body"])   # someone copied the notes into another draft
    fake.rels.reverse()   # and GitHub lists the copy first
    assert [d["state"] for d in rp.recover()] == ["published"] and not draft["draft"]

    for _ in range(2):   # the same marker on two drafts of the attempt's own tag
        fake.add("v0.2.0", draft=True, body=f"<!-- {rp.MARK} twin -->")
    record = {**rp.ledger("github.com/o/r")["attempts"][0], "id": "twin", "state": "pending", "tag": "v0.2.0"}
    record.pop("release_id")
    rp.reserve("github.com/o/r", record)
    done = rp.finish("github.com/o/r", "twin")
    assert done["state"] == "failed" and "정할 수 없다" in done["reason"] and "release_id" not in done


def test_an_asset_in_a_state_it_does_not_know_is_left_alone(press, monkeypatch):
    fake, repo, _ = press
    draft = _draft_left(fake, monkeypatch, repo, [("r-0.1.0-arm64-v8a.apk", b"?", "quarantined")])
    done = rp.recover()[0]
    assert done["state"] == "failed" and "알 수 없는 상태" in done["reason"] and len(draft["assets"]) == 1


def test_uploads_that_all_fail_transiently_leave_the_attempt_pending(press):
    fake, repo, _ = press
    fake.fail["POST releases/{id}/assets"] = [502] * rp.UPLOAD_TRIES * (len(rp.WAITS) + 1)
    with pytest.raises(gh.Failed):
        rp.publish(repo, "0.1.0", None)
    still = rp.pending("github.com/o/r")
    assert still and Path(still["manifest"][0]["path"]).is_file()
    assert [d["state"] for d in rp.recover()] == ["published"]


def test_a_ledger_that_cannot_be_written_on_disk_raises_unrecorded(press, monkeypatch):
    fake, repo, _ = press
    replaced, failed = release.replaced, []

    def full(target, text):
        if '"release_id"' in text and not failed:
            failed.append(True)
            raise OSError("No space left on device")
        return replaced(target, text)

    monkeypatch.setattr(release, "replaced", full)
    with pytest.raises(rp.Unrecorded, match="No space"):
        rp.publish(repo, "0.1.0", None)
    assert rp.pending("github.com/o/r") and [d["state"] for d in rp.recover()] == ["published"]


def test_the_last_permitted_upload_that_lands_counts(press):
    fake, repo, _ = press
    fake.fail["POST releases/{id}/assets"] = [502] * (rp.UPLOAD_TRIES - 1)
    assert rp.publish(repo, "0.1.0", None)["state"] == "published"


def test_a_press_overtaken_by_another_while_choosing_its_version_is_refused(press, monkeypatch):
    fake, repo, _ = press
    rp.publish(repo, "0.1.0", None)
    accepted, inner = release.accepted, []

    def overtaken(shown, *args):
        if not inner:   # a minor press starts and finishes in the gap
            inner.append(None)
            inner[0] = rp.publish(repo, "0.2.0", "minor")
        return accepted(shown, *args)

    monkeypatch.setattr(release, "accepted", overtaken)
    with pytest.raises(ValueError, match="다른 배포가 있었다"):
        rp.publish(repo, "0.1.1", "patch")
    assert inner[0]["state"] == "published" and [r["tag_name"] for r in fake.rels] == ["v0.1.0", "v0.2.0"]


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
