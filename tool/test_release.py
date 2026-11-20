"""Release steps 1 and 2: the `[release]` reader, the seed, the version selector, the APK checks and the release key
(`docs/plans/release/0-overview.md`). The real build and signing run in `release_rehearsal.rehearse`."""

import json
import subprocess
from pathlib import Path

import pytest

from main import release, release_rehearsal

ANDROID = {"build": "build", "artifact": "app.apk", "application_id": "ai.x.app", "abi": "arm64-v8a"}
TABLE = """agents = ["claude"]

[slots]
gate_cmd = "pytest"

[release]
version_source = "src-tauri/tauri.conf.json"

[release.android]
build = "build"
artifact = "app.apk"
application_id = "ai.x.app"
abi = "arm64-v8a"
"""


def committed(tmp_path: Path, files: dict) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo, release.git(repo, "rev-parse", "HEAD").strip()


def test_the_release_table_and_the_seed_are_read_from_the_pinned_commit_not_the_checkout(tmp_path):
    repo, sha = committed(tmp_path, {".wiki/adapter.toml": TABLE,
                                     "src-tauri/tauri.conf.json": json.dumps({"version": "0.1.0"})})
    (repo / ".wiki/adapter.toml").write_text("[slots]\n", encoding="utf-8")   # the checkout moved on

    cfg = release.settings(repo, sha)

    assert cfg == {"version_source": "src-tauri/tauri.conf.json", "android": ANDROID}
    assert release.seed(repo, sha, cfg["version_source"]) == "0.1.0"


@pytest.mark.parametrize("table, refusal", [
    ("[slots]\n", "`[release]` 표가 없다"),
    ("[release]\nversion_source = 'a.json'\n", "무엇을 빌드할지 정하지 않았다"),
    ("[release]\nversion_source = 'a.json'\ndesktop = 'x'\n", "desktop"),
    ("[release]\nversion_source = ''\n[release.android]\nbuild = 'b'\nartifact = 'a'\napplication_id = 1\nabi = 'x'\n",
     "version_source, android.application_id"),
])
def test_a_missing_or_mistyped_release_table_is_refused(tmp_path, table, refusal):
    repo, sha = committed(tmp_path, {".wiki/adapter.toml": table})

    with pytest.raises(ValueError, match=refusal.replace("[", r"\[").replace("]", r"\]")):
        release.settings(repo, sha)


def test_a_seed_that_is_not_major_minor_patch_is_refused(tmp_path):
    repo, sha = committed(tmp_path, {"Cargo.toml": "[package]\nversion = '0.1'\n"})

    with pytest.raises(ValueError, match="major.minor.patch"):
        release.seed(repo, sha, "Cargo.toml")


def test_a_seed_is_read_from_any_shape_its_kind_allows_and_another_is_refused_plainly(tmp_path):
    repo, sha = committed(tmp_path, {"a.toml": 'package = "demo"\nversion = "0.1.0"\n', "b.json": "[1]",
                                     "c.toml": '[package]\nname = "x"\n', "d.toml": '[package]\nversion = "0.2.0"\n',
                                     "e.toml": 'version = "0.3.0"\n[package]\nversion = ""\n'})
    assert release.seed(repo, sha, "a.toml") == "0.1.0" and release.seed(repo, sha, "d.toml") == "0.2.0"
    assert release.seed(repo, sha, "e.toml") == "0.3.0"   # an empty `[package] version` falls back to the root's
    for name in ("b.json", "c.toml"):
        with pytest.raises(ValueError, match="major.minor.patch"):
            release.seed(repo, sha, name)


def test_the_selector_bumps_the_last_eligible_production_release():
    releases = [{"tagName": "v0.1.0"}, {"tagName": "v0.1.3"}, {"tagName": "v0.9.0", "isPrerelease": True},
                {"tagName": "v1.0.0", "isDraft": True}, {"tagName": "android-test-1721b04"},
                {"tagName": "oneshot-apk-0123456789ab"}]

    assert [release.version(releases, [], bump, "0.1.0") for bump in release.BUMPS] == ["0.1.4", "0.2.0", "1.0.0"]


def test_the_first_release_is_the_seed_and_takes_no_bump():
    assert release.version([], [], None, "0.1.0") == "0.1.0"
    with pytest.raises(ValueError, match="seed"):
        release.version([], [], "patch", "0.1.0")
    with pytest.raises(ValueError, match="patch, minor, major"):
        release.version([{"tagName": "v0.1.0"}], [], None, "0.1.0")


def test_a_taken_version_moves_to_the_next_patch_after_a_bump_and_from_the_seed():
    """A failed first release left `v0.1.0` on the remote, and someone pushed `v0.2.0` by hand."""

    assert release.version([], ["v0.1.0", "v0.1.1"], None, "0.1.0") == "0.1.2"
    assert release.version([{"tagName": "v0.1.4"}], ["v0.2.0", "android-test-x"], "minor", "0.1.0") == "0.2.1"


def test_a_press_whose_version_changed_by_pin_time_is_refused():
    assert release.accepted("0.1.1", [{"tagName": "v0.1.0"}], [], "patch", "0.1.0") == "0.1.1"
    with pytest.raises(ValueError, match="0.1.1 이 지금은 0.1.2"):
        release.accepted("0.1.1", [{"tagName": "v0.1.0"}], ["v0.1.1"], "patch", "0.1.0")


def test_the_version_code_is_tauris_mapping_within_its_bounds():
    assert [release.code(v) for v in ("0.1.0", "0.1.1", "1.2.3", "2099.999.999")] == [1000, 1001, 1002003, 2099999999]
    for wrong in ("0.0.0", "0.1000.0", "0.1.1000", "2100.0.0"):
        with pytest.raises(ValueError, match="versionCode"):
            release.code(wrong)


def test_the_selector_never_offers_a_version_android_cannot_carry_nor_reads_a_non_semver_tag():
    with pytest.raises(ValueError, match="versionCode"):
        release.accepted("0.0.0", [], [], None, "0.0.0")
    assert release.version([], [], None, "1.1000.0", {"desktop": {}}) == "1.1000.0"   # no versionCode to carry
    with pytest.raises(ValueError, match="65535"):
        release.version([], [], None, "1.65536.0", {"desktop": {}})
    with pytest.raises(ValueError, match="versionCode"):
        release.version([], [], None, "1.1000.0", {"android": {}, "desktop": {}})
    with pytest.raises(ValueError, match="versionCode"):   # the taken seed pushes the patch past 999
        release.version([], ["v0.1.999"], None, "0.1.999")
    assert release.parsed("v01.0.0") is None
    assert release.parsed("v9٠.0.0") is None   # a Unicode digit is not SemVer
    assert release.version([{"tagName": "v0.1.0"}, {"tagName": "v9٠.0.0"}], [], "patch", "0.1.0") == "0.1.1"
    assert release.version([{"tagName": "v0.1.0"}, {"tagName": "v09.0.0"}], [], "patch", "0.1.0") == "0.1.1"


BADGING = """package: name='ai.x.app' versionCode='1001' versionName='0.1.1' platformBuildVersionName='16'
sdkVersion:'26'
application-label:'App'
native-code: 'arm64-v8a'
"""
CERTS = """Signer #1 certificate DN: CN=wiki-agent release rehearsal
Signer #1 certificate SHA-256 digest: ABCDEF0123
Signer #1 certificate SHA-1 digest: 99
"""


def test_badging_and_certificates_are_read_from_the_tools_output():
    assert release.badging(BADGING) == {"package": "ai.x.app", "code": 1001, "name": "0.1.1", "abis": ["arm64-v8a"],
                                        "debuggable": False}
    assert release.badging(BADGING + "application-debuggable\n")["debuggable"]
    assert release.certificates(CERTS) == ["abcdef0123"]


def seen(**over) -> dict:
    return {"package": "ai.x.app", "code": 1001, "name": "0.1.1", "abis": ["arm64-v8a"], "debuggable": False,
            "certs": ["c1"], "sha256": "d", **over}


def test_an_apk_that_matches_the_version_identity_and_certificate_passes():
    assert release.refused(seen(), "0.1.1", ANDROID, "c1") == []
    assert release.refused(seen(), "0.1.1", ANDROID, "c1", seen(code=1000, name="0.1.0")) == []


@pytest.mark.parametrize("over, previous, refusal", [
    ({"name": "0.1.0"}, None, "versionName"),
    ({"code": 1}, None, "versionCode 1 가 버전 0.1.1 의 1001"),
    ({}, {"code": 1001}, "이전 정식 APK 의 1001 보다 크지 않다"),
    ({"package": "ai.x.other"}, None, "설정의 'ai.x.app'"),
    ({}, {"package": "ai.x.old"}, "이전 정식 APK의 'ai.x.old'"),
    ({"abis": ["arm64-v8a", "x86_64"]}, None, "ABI"),
    ({}, {"abis": ["x86_64"]}, "이전 정식 APK의 ['x86_64']"),
    ({"debuggable": True}, None, "디버그 빌드"),
    ({"certs": ["c2"]}, None, "기대한 인증서"),
    ({}, {"certs": ["c0"]}, "이전 정식 APK 의 인증서"),
])
def test_each_check_refuses_on_its_own(over, previous, refusal):
    why = release.refused(seen(**over), "0.1.1", ANDROID, "c1", previous and seen(**{"code": 1000, **previous}))

    assert len(why) == 1 and refusal in why[0]


def test_an_abi_that_moves_with_the_adapter_is_still_refused_against_the_previous_apk():
    why = release.refused(seen(abis=["x86_64"]), "0.1.1", {**ANDROID, "abi": "x86_64"}, "c1", seen(code=1000))

    assert len(why) == 1 and "이전 정식 APK의 ['arm64-v8a']" in why[0]


def test_an_app_with_no_native_code_declares_abi_none_and_needs_no_ndk(tmp_path, monkeypatch):
    none = {**ANDROID, "abi": "none"}
    assert release.refused(seen(abis=[]), "0.1.1", none, "c1") == []
    assert "ABI ['arm64-v8a']" in release.refused(seen(), "0.1.1", none, "c1")[0]
    tools = tmp_path / "build-tools" / release.oneshot_android.BUILD_TOOLS
    tools.mkdir(parents=True)
    for name in ("aapt2", "zipalign", "apksigner"):
        ext = release.oneshot_android.BAT if name == "apksigner" else release.oneshot_android.EXE
        (tools / f"{name}{ext}").write_text("")
    monkeypatch.setattr(release.oneshot_android, "roots", lambda: [tmp_path])
    monkeypatch.setattr(release.oneshot_android, "java", lambda home: True)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    monkeypatch.setenv("NDK_HOME", str(tmp_path / "an-old-ndk"))   # the server's own, never the kit's
    kit = release.tools({"android": none})
    assert kit["ndk"] is None and "NDK_HOME" not in release.environment(kit)
    with pytest.raises(ValueError, match="NDK"):
        release.tools({"android": ANDROID})


def test_an_attempt_checkout_is_a_fresh_clean_detached_copy_and_never_a_second_one(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "WORK", tmp_path / "work")
    repo, sha = committed(tmp_path, {"a.txt": "a"})

    src = release.checkout(repo, sha, "a1")

    assert release.git(src, "rev-parse", "HEAD").strip() == sha and (src / "a.txt").read_text() == "a"
    assert release.place(repo, "a1").as_posix() not in release.git(repo, "worktree", "list")   # not a hash's "a1"
    with pytest.raises(ValueError, match="이미 있다"):
        release.checkout(repo, sha, "a1")
    release.discard(repo, "a1")
    assert not release.place(repo, "a1").exists()


def test_removal_passes_the_hook_python_3_11_knows(tmp_path, monkeypatch):
    """`rmtree` took `onexc` only from 3.12; the repository still supports 3.11."""

    monkeypatch.setattr(release, "WORK", tmp_path / "work")
    monkeypatch.setattr(release.sys, "version_info", (3, 11, 9))
    called = {}
    monkeypatch.setattr(release.shutil, "rmtree", lambda p, **kw: called.update(kw))

    release.discard(Path("repo"), "a1")

    assert called == {"onerror": release._writable}


def test_the_build_gets_the_version_through_its_environment_and_an_overlay_beside_the_checkout(tmp_path, monkeypatch):
    src = tmp_path / "attempt" / "src"
    src.mkdir(parents=True)
    seen = {}

    def gate(cmd, path, halt, env, **_):
        seen.update(cmd=cmd, path=path, env=env, overlay=json.loads(Path(env["RELEASE_TAURI_CONFIG"]).read_text()))
        (path / "out").mkdir(exist_ok=True)
        (path / "out/app.apk").write_bytes(b"apk")   # the build makes it
        return 0, "", ""

    monkeypatch.setattr(release.specs, "gate", gate)
    monkeypatch.setattr(release, "run", lambda argv, env=None, timeout=300: BADGING)   # the APK reads back
    monkeypatch.setenv(release.SECRET, "inherited")
    monkeypatch.setenv("GH_TOKEN", "inherited-token")
    kit = {"java": tmp_path, "sdk": tmp_path, "ndk": tmp_path, "aapt2": "aapt2"}

    apk = release.build(src, {**ANDROID, "artifact": "out/app.apk"}, "0.1.1", kit, None)
    assert "GH_TOKEN" not in seen["env"]   # nor a GitHub token: what the build prints can reach the repair agent

    assert apk == (src / "out/app.apk").resolve() and seen["cmd"] == "build" and seen["path"] == src
    assert (seen["env"]["RELEASE_VERSION"], seen["env"]["RELEASE_VERSION_CODE"]) == ("0.1.1", "1001")
    assert seen["overlay"] == {"version": "0.1.1",
                               "bundle": {"android": {"versionCode": 1001, "autoIncrementVersionCode": False}}}
    assert Path(seen["env"]["RELEASE_TAURI_CONFIG"]).parent == src.parent   # never inside the checkout
    assert release.SECRET not in seen["env"]   # a key password the server inherited never reaches the build
    with pytest.raises(ValueError, match="빌드 전부터"):   # the commit's own file is not the build's output
        release.build(src, {**ANDROID, "artifact": "out/app.apk"}, "0.1.1", kit, None)
    with pytest.raises(ValueError, match="체크아웃 밖"):
        release.build(src, {**ANDROID, "artifact": "../x.apk"}, "0.1.1", kit, None)


def test_pinning_reads_its_own_fetch_not_a_tracking_ref_the_mapping_left_stale(tmp_path):
    origin, a = committed(tmp_path, {"a.txt": "a"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    subprocess.run(["git", "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other"],
                   cwd=clone, check=True)
    (origin / "a.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "b"], cwd=origin, check=True)
    b = release.git(origin, "rev-parse", "HEAD").strip()

    assert release.git(clone, "rev-parse", "origin/HEAD").strip() == a
    assert release.pin(clone, f"origin/{release.git(origin, 'branch', '--show-current').strip()}") == b
    assert "release-pin" not in release.git(clone, "for-each-ref")   # its own ref goes again


@pytest.mark.parametrize("linked", ["work", "raw"])
def test_a_linked_release_root_or_folder_above_it_is_never_written_or_removed(tmp_path, monkeypatch, linked):
    monkeypatch.setattr(release.paths, "DATA", tmp_path)
    monkeypatch.setattr(release, "WORK", tmp_path / "raw" / "work")
    monkeypatch.setattr(release, "is_junction", lambda p: Path(p).name == linked)
    removed = []
    monkeypatch.setattr(release.shutil, "rmtree", lambda p, **_: removed.append(p))

    with pytest.raises(ValueError, match="링크"):
        release.discard(Path("repo"), "a1")
    with pytest.raises(ValueError, match="링크"):
        release.checkout(Path("repo"), "0" * 40, "a1")
    with pytest.raises(ValueError, match="안이 아니다"):
        release.owned(release.WORK)
    assert removed == []


def _rehearsable(tmp_path, monkeypatch):
    """A rehearsal of one pinned commit with its tools, settings and seed faked."""

    monkeypatch.setattr(release, "WORK", tmp_path / "work")
    monkeypatch.setattr(release, "tools", lambda cfg=None: {})
    monkeypatch.setattr(release, "pin", lambda repo, ref: "0" * 40)
    monkeypatch.setattr(release, "settings", lambda repo, sha: {"version_source": "v", "android": ANDROID})
    monkeypatch.setattr(release, "seed", lambda repo, sha, source: "0.1.0")
    monkeypatch.setattr(release, "git", lambda *a, **k: "")


def test_every_test_keystore_goes_even_when_a_step_after_its_creation_fails(tmp_path, monkeypatch):
    _rehearsable(tmp_path, monkeypatch)

    def key(store, kit):
        store.write_bytes(b"key")   # written, then the tool reports failure
        return {"keystore": store, "alias": "a", "password": "p"}

    def broken(*_):
        raise ValueError("keytool failed")

    monkeypatch.setattr(release, "test_key", key)
    monkeypatch.setattr(release, "certificate", broken)

    with pytest.raises(ValueError, match="keytool failed"):
        release_rehearsal.rehearse(tmp_path / "repo")
    assert list((tmp_path / "work").rglob("*.jks")) == []

    tried, unlink = [], Path.unlink

    def locked(self, missing_ok=False):
        tried.append(self.name)
        if self.name == "rehearsal.jks":
            raise PermissionError("locked")
        unlink(self, missing_ok=missing_ok)

    def both(store, kit):   # both keys exist when cleanup runs
        (store.parent / "other.jks").write_bytes(b"key")
        return key(store, kit)

    monkeypatch.setattr(release, "test_key", both)
    monkeypatch.setattr(Path, "unlink", locked)
    monkeypatch.setattr(release_rehearsal.errorlog, "record", lambda *a, **k: None)
    with pytest.raises(ValueError, match="keytool failed") as failed:   # the error that ended it stays on top
        release_rehearsal.rehearse(tmp_path / "repo")
    assert tried == ["rehearsal.jks", "other.jks"]   # the locked first key never spares the second
    assert len(failed.value.left) == 1 and "rehearsal.jks: locked" in failed.value.left[0]   # and is reported

    monkeypatch.setattr(release, "certificate", lambda *a: "c" * 64)
    monkeypatch.setattr(release, "checkout", lambda *a: (_ for _ in ()).throw(release.Stopped("멈춤")))
    with pytest.raises(release.Stopped) as stopped:   # a stop, too, carries what it could not remove
        release_rehearsal.rehearse(tmp_path / "repo")
    assert len(stopped.value.left) == 1 and "rehearsal.jks" in stopped.value.left[0]

    # A source failure caught in its round keeps its outcome and its repair: the locked key is only reported.
    monkeypatch.setattr(release, "checkout", lambda *a: tmp_path / "src")
    monkeypatch.setattr(release, "build", lambda *a: (_ for _ in ()).throw(release.BuildFailed("error: boom")))
    asked = []
    monkeypatch.setattr(release_rehearsal.release, "ran", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    done = release_rehearsal.rehearse(tmp_path / "repo", repair=lambda repo, record: asked.append(record) or "fix-1")
    assert done["rounds"][0]["failed"] == "error: boom" and done["repair"]["task"] == "fix-1"
    assert asked[0]["repair"]["needed"] == "error: boom" and "rehearsal.jks: locked" in done["left"][0]


def test_a_stopped_rehearsal_build_leaves_no_checkout_and_starts_no_repair(tmp_path, monkeypatch):
    _rehearsable(tmp_path, monkeypatch)
    monkeypatch.setattr(release, "test_key", lambda store, kit: {"keystore": store, "alias": "a", "password": "p"})
    monkeypatch.setattr(release, "certificate", lambda *a: "c" * 64)
    made = []

    def checkout(repo, sha, at):
        (src := release.place(repo, at)).mkdir(parents=True)
        (src / "node_modules").mkdir()   # what a build had written by the time the person stopped it
        return made.append(src) or src

    def build(src, android, version, kit, halt):
        halt.set()
        raise ValueError("빌드 실패 — 사람이 멈춤")   # the machine's: not repairable

    monkeypatch.setattr(release, "checkout", checkout)
    monkeypatch.setattr(release, "build", build)
    with pytest.raises(ValueError, match="사람이 멈춤"):   # raised, so no repair is handed anything
        release_rehearsal.rehearse(tmp_path / "repo")
    assert made and not made[0].exists()


@pytest.mark.parametrize("failure", ["build", "debuggable", "second build"])
def test_the_rehearsal_hands_a_failed_build_to_repair_and_nothing_else(tmp_path, monkeypatch, failure):
    _rehearsable(tmp_path, monkeypatch)
    monkeypatch.setattr(release, "test_key", lambda store, kit: {"keystore": store, "alias": "a", "password": "p"})
    monkeypatch.setattr(release, "certificate", lambda *a: "c" * 64)
    monkeypatch.setattr(release, "checkout", lambda repo, sha, at: release.place(repo, at).mkdir(parents=True)
                        or release.place(repo, at))

    def build(src, android, version, kit, halt):
        if failure == "build" or (failure == "second build" and version == "0.1.1"):
            raise release.BuildFailed("빌드 실패 — 1 로 끝났다\nerror: boom")
        return src / "out.apk"

    def facts(apk, kit):   # the version is the signed APK's name: `<round>-<version>.apk`
        name = apk.stem.split("-", 1)[1]
        return seen(name=name, code=release.code(name), debuggable=failure == "debuggable", certs=["c" * 64],
                    package=ANDROID["application_id"])

    monkeypatch.setattr(release, "build", build)
    monkeypatch.setattr(release, "sign", lambda apk, out, key, kit: out)
    monkeypatch.setattr(release, "facts", facts)
    repo, _ = committed(tmp_path, {"a.txt": "a"})
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True)
    for args in (["branch", "-qM", "main"], ["remote", "add", "origin", str(tmp_path / "origin.git")],
                 ["push", "-q", "origin", "main"], ["tag", "v0.0.9"], ["branch", "experiment"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    asked = []

    def repair(repo, record):
        return asked.append(record) or "release-fix-0-1-0"

    # A repair's pull request needs a branch GitHub has: not a tag, a commit, or a branch only the clone has.
    for ref in ("v0.0.9", "0" * 40, "experiment"):
        with pytest.raises(ValueError, match="브랜치가 아니다"):
            release_rehearsal.rehearse(repo, ref, repair=repair)
    done = release_rehearsal.rehearse(repo, repair=repair)
    assert done["repair"]["task"] == "release-fix-0-1-0" and not done["ok"] and len(asked) == 1
    tag = "v0.1.1" if failure == "second build" else "v0.1.0"   # a later round's failure is the one handed on
    assert asked[0]["tag"] == tag and asked[0]["sha"] == "0" * 40 and asked[0]["base"] == "main"
    assert ("디버그" if failure == "debuggable" else "boom") in asked[0]["repair"]["needed"]
    assert [p for p in Path(done["folder"]).iterdir() if p.is_dir()] == []   # every round's checkout went


def test_a_round_that_fails_after_its_build_still_removes_its_checkout_and_keeps_its_own_error(tmp_path, monkeypatch):
    _rehearsable(tmp_path, monkeypatch)
    monkeypatch.setattr(release, "test_key", lambda store, kit: {"keystore": store, "alias": "a", "password": "p"})
    monkeypatch.setattr(release, "certificate", lambda *a: "c" * 64)
    made, logged = [], []

    def checkout(repo, sha, at):
        (src := release.place(repo, at)).mkdir(parents=True)
        return made.append(src) or src

    def broken(*_):
        raise RuntimeError("apksigner failed")

    monkeypatch.setattr(release, "checkout", checkout)
    monkeypatch.setattr(release, "build", lambda src, *a: src / "out.apk")
    monkeypatch.setattr(release, "sign", broken)
    with pytest.raises(RuntimeError, match="apksigner failed"):
        release_rehearsal.rehearse(tmp_path / "repo")
    assert made and not made[0].exists()

    def stuck(repo, at):
        raise OSError("in use")

    monkeypatch.setattr(release, "discard", stuck)
    monkeypatch.setattr(release_rehearsal.errorlog, "record", lambda *a, **k: logged.append(a[0]))
    with pytest.raises(RuntimeError, match="apksigner failed"):   # a failed cleanup never hides why the round ended
        release_rehearsal.rehearse(tmp_path / "repo")
    assert logged == ["release-rehearsal-cleanup"]


def test_a_stop_ends_a_running_command_at_once_and_without_it_nothing_changes(tmp_path):
    import sys
    import threading
    import time

    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    halt = threading.Event()
    threading.Timer(0.3, halt.set).start()
    began = time.monotonic()
    with release.stoppable(halt), pytest.raises(release.Stopped):   # never its own 60 seconds, nor `ran`'s 600
        release.ran(sleeper, 600)
    assert time.monotonic() - began < 10
    with release.stoppable(threading.Event()):
        utf8 = "import sys; sys.stdout.buffer.write('하나'.encode())"   # a piped child's own encoding is the locale's
        assert release.ran([sys.executable, "-c", utf8], 60).stdout.strip() == "하나"
    with pytest.raises(subprocess.TimeoutExpired), release.stoppable(threading.Event()):
        release.ran(sleeper, 0.5)
    two = "import sys; sys.stdout.buffer.write('둘'.encode())"
    assert release.run([sys.executable, "-c", two]).strip() == "둘"   # outside `stoppable`: as before


def test_a_stopped_scope_starts_nothing_and_a_tree_it_cannot_own_never_runs(tmp_path, monkeypatch):
    import sys
    import threading

    marker = tmp_path / "ran"
    writes = [sys.executable, "-c", f"open(r'{marker}', 'w').close()"]
    stopped = threading.Event()
    stopped.set()
    with release.stoppable(stopped), pytest.raises(release.Stopped, match="시작하지 않았다"):
        release.ran(writes, 60)
    assert not marker.exists()
    if release.os.name == "nt":   # Windows: no job, no run; the suspended child is ended and waited for
        made = []
        popen = release.subprocess.Popen
        monkeypatch.setattr(release.process, "contained", lambda proc: None)
        monkeypatch.setattr(release.subprocess, "Popen", lambda *a, **k: made.append(popen(*a, **k)) or made[-1])
        with release.stoppable(threading.Event()), pytest.raises(OSError, match="맡지 못했다"):
            release.ran(writes, 60)
        assert not marker.exists() and made[0].poll() is not None


def test_a_stopped_fetch_still_removes_its_own_ref(monkeypatch):
    import threading

    calls, halt = [], threading.Event()

    def ran(argv, timeout, cwd=None, env=None):
        calls.append((argv[1], release._stop.halt))
        if argv[1] == "fetch":
            halt.set()   # the person stopped it after it wrote the ref
            raise release.Stopped("멈춤")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(release, "ran", ran)
    with release.stoppable(halt), pytest.raises(release.Stopped):
        release.pin(Path("repo"), "origin/main")
    # Outside the stopped scope, yet still on the owned, bounded runner: a halt of its own that is never set.
    assert [c[0] for c in calls] == ["fetch", "update-ref"] and calls[1][1] is not halt and not calls[1][1].is_set()
    assert release._stop.halt is None   # and the scope is gone with the rehearsal


def test_a_command_is_torn_down_whatever_fails_on_the_way(monkeypatch):
    import sys
    import threading

    made, popen = [], release.subprocess.Popen
    monkeypatch.setattr(release.subprocess, "Popen", lambda *a, **k: made.append(popen(*a, **k)) or made[-1])
    monkeypatch.setattr(release.process, "contained", lambda proc: (_ for _ in ()).throw(RuntimeError("assign")))
    with release.stoppable(threading.Event()), pytest.raises(RuntimeError, match="assign"):
        release.ran([sys.executable, "-c", "import time; time.sleep(60)"], 60)
    assert made[0].poll() is not None   # the suspended child, ended and waited for

    closed, logged = [], []

    class Stuck:   # a child that will not die when asked
        def poll(self):
            return None

        def kill(self):
            raise PermissionError("denied")

    monkeypatch.setattr(release.subprocess, "Popen", lambda *a, **k: Stuck())
    monkeypatch.setattr(release.process, "contained", lambda proc: "job")
    monkeypatch.setattr(release.process, "resumed", lambda proc: (_ for _ in ()).throw(OSError("resume")))
    monkeypatch.setattr(release.process, "killed", lambda job: None)
    monkeypatch.setattr(release.process, "terminated", closed.append)
    monkeypatch.setattr(release.errorlog, "record", lambda source, exc, **k: logged.append(str(exc)))
    with release.stoppable(threading.Event()), pytest.raises(OSError, match="resume"):   # never "denied"
        release.ran(["anything"], 60)
    assert closed == ["job"] and logged == ["denied"]


def test_a_branch_that_moved_after_the_card_was_read_is_never_built(tmp_path, monkeypatch):
    _rehearsable(tmp_path, monkeypatch)
    monkeypatch.setattr(release, "settings", lambda *a: pytest.fail("built"))
    with pytest.raises(ValueError, match="움직였다"):
        release_rehearsal.rehearse(tmp_path / "repo", expect="1" * 40)


def test_a_fetch_that_times_out_after_writing_its_ref_leaves_no_ref(monkeypatch):
    refs = set()

    def git(repo, *args, timeout=120):
        if args[0] == "fetch":
            refs.add(args[-1].split(":")[1])
            raise subprocess.TimeoutExpired("git fetch", timeout)
        if args[:2] == ("update-ref", "-d"):
            refs.discard(args[2])
        return ""

    monkeypatch.setattr(release, "git", git)

    with pytest.raises(subprocess.TimeoutExpired):
        release.pin(Path("repo"))
    assert refs == set()


def test_a_pin_ref_git_cannot_delete_is_recorded_with_its_name(monkeypatch):
    refs, recorded = set(), []

    def git(repo, *args, timeout=120):
        if args[0] == "fetch":
            refs.add(args[-1].split(":")[1])
        if args[:2] == ("update-ref", "-d"):
            raise ValueError("ref is locked")
        return "a" * 40

    monkeypatch.setattr(release, "git", git)
    monkeypatch.setattr(release.errorlog, "record", lambda source, error, **context: recorded.append((source, context)))

    assert release.pin(Path("repo")) == "a" * 40
    (left,) = refs
    assert recorded == [("release-pin", {"repo": "repo", "ref": left})]


def test_only_the_key_tool_gets_the_password_when_signing(tmp_path, monkeypatch):
    monkeypatch.setenv(release.SECRET, "inherited")
    calls = []
    monkeypatch.setattr(release, "run", lambda argv, env=None, timeout=300: calls.append((argv, env)) or "")
    kit = {"java": tmp_path, "sdk": tmp_path, "ndk": tmp_path, "zipalign": "zipalign", "apksigner": "apksigner"}

    release.sign(tmp_path / "in.apk", tmp_path / "out.apk", {"keystore": "k", "alias": "a", "password": "p"}, kit)

    (align, align_env), (signer, signer_env) = calls
    assert align[0] == "zipalign" and align_env is not None and release.SECRET not in align_env
    assert signer[0] == "apksigner" and signer_env[release.SECRET] == "p"


# -- Step 2: the release key ----------------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://github.com/Owner/Repo.git", "https://github.com/owner/repo",
                                 "git@github.com:owner/repo.git", "ssh://git@github.com/owner/repo.git"])
def test_the_remote_that_names_a_key_is_canonical(monkeypatch, url):
    monkeypatch.setattr(release, "git", lambda *a, **k: url + "\n")

    assert release.remote(Path("x")) == "github.com/owner/repo"


@pytest.mark.parametrize("url", ["https://gitlab.com/owner/repo.git", "https://github.com/../repo",
                                 "https://github.com/owner/.."])
def test_a_remote_that_is_not_a_github_repository_names_no_key(monkeypatch, url):
    monkeypatch.setattr(release, "git", lambda *a, **k: url + "\n")

    with pytest.raises(ValueError, match="GitHub"):
        release.remote(Path("x"))


def test_keys_never_live_under_wiki_agents_own_checkout():
    assert not release.KEYS.resolve().is_relative_to(release.paths.RESOURCES.resolve())


@pytest.mark.skipif(release.os.name != "nt", reason="Credential Manager is Windows only")
def test_a_password_round_trips_through_credential_manager():
    name = f"github.com/test/{release.secrets.token_hex(6)}"
    try:
        assert release.secret_read(name) is None
        release.secret_write(name, "pässwörd-1")
        assert release.secret_read(name) == "pässwörd-1"
    finally:
        release.secret_delete(name)
    assert release.secret_read(name) is None


@pytest.fixture
def keys(tmp_path, monkeypatch):
    """`release_key` over a fake vault and a fake keytool. A keystore holds `<certificate>|<password>`, and only
    its password opens it."""

    vault = {}
    monkeypatch.setattr(release, "KEYS", tmp_path / "keys")
    monkeypatch.setattr(release, "remote", lambda repo: "github.com/o/r")
    monkeypatch.setattr(release, "secret_read", vault.get)
    monkeypatch.setattr(release, "secret_write", vault.__setitem__)

    def generated(store, alias, days, name, kit):
        assert (alias, days, name) == ("release", release.KEY_DAYS, "CN=github.com/o/r")
        password = release.secrets.token_hex(4)
        store.write_text(f"{release.secrets.token_hex(32)}|{password}", encoding="utf-8")
        return {"keystore": store, "alias": alias, "password": password}

    def certificate(store, alias, password, kit):
        cert, opener = store.read_text(encoding="utf-8").split("|")
        if password != opener:
            raise ValueError("keytool: wrong password")
        return cert

    monkeypatch.setattr(release, "generated", generated)
    monkeypatch.setattr(release, "certificate", certificate)
    return vault, tmp_path / "keys" / "github.com" / "o" / "r"


def test_the_release_key_is_made_once_and_then_the_same_key_is_returned(keys):
    vault, folder = keys

    with pytest.raises(ValueError, match="아직 없다"):
        release.release_key(Path("repo"), {})
    made = release.release_key(Path("repo"), {}, create=True)
    again = release.release_key(Path("repo"), {}, create=True)

    assert made["keystore"] == folder / "release.jks" and (folder / "release.jks").is_file()
    assert again["cert"] == made["cert"] == (folder / "certificate.sha256").read_text().strip()
    assert vault == {"github.com/o/r": made["password"]} and again["password"] == made["password"]
    assert not (folder / "release.jks.new").exists()


@pytest.mark.parametrize("loss, refusal", [("store", "키 파일"), ("password", "비밀번호"), ("changed", "인증서가")])
def test_an_established_key_that_lost_a_half_or_changed_is_refused_never_replaced(keys, loss, refusal):
    vault, folder = keys
    release.release_key(Path("repo"), {}, create=True)
    if loss == "store":
        (folder / "release.jks").unlink()
    elif loss == "password":
        vault.clear()
    else:
        (folder / "release.jks").write_text("another key", encoding="utf-8")

    with pytest.raises(ValueError, match=refusal):
        release.release_key(Path("repo"), {}, create=True)
    assert (folder / "certificate.sha256").exists() and not (folder / "release.jks.new").exists()


def test_a_creation_that_stopped_after_storing_its_password_is_finished_with_that_same_key(keys, monkeypatch):
    vault, folder = keys
    real, calls = release.certificate, []

    def broken(*args):
        calls.append(args)
        raise ValueError("keytool failed")

    monkeypatch.setattr(release, "certificate", broken)
    with pytest.raises(ValueError, match="keytool failed"):
        release.release_key(Path("repo"), {}, create=True)
    stored, staged = vault["github.com/o/r"], (folder / "release.jks.new").read_text()
    monkeypatch.setattr(release, "certificate", real)

    with pytest.raises(ValueError, match="만들다 멈췄다"):
        release.release_key(Path("repo"), {})
    made = release.release_key(Path("repo"), {}, create=True)

    assert made["password"] == stored == vault["github.com/o/r"]   # the stored password is never replaced
    assert (folder / "release.jks").read_text() == staged and not (folder / "release.jks.new").exists()


def test_a_keystore_whose_record_was_lost_gets_it_back_from_its_own_password_and_one_no_password_opens_stays(keys):
    vault, folder = keys
    made = release.release_key(Path("repo"), {}, create=True)
    (folder / "certificate.sha256").unlink()

    assert release.release_key(Path("repo"), {}, create=True)["cert"] == made["cert"]

    (folder / "certificate.sha256").unlink()
    vault.clear()
    with pytest.raises(ValueError, match="사람이 확인해야 한다"):
        release.release_key(Path("repo"), {}, create=True)
    assert (folder / "release.jks").read_text().startswith(made["cert"])   # never deleted or replaced
    assert vault == {}


def test_a_surviving_password_with_no_keystore_is_never_overwritten(keys):
    vault, folder = keys
    vault["github.com/o/r"] = "existing"

    with pytest.raises(ValueError, match="남아 있는데 키 파일이 없다"):
        release.release_key(Path("repo"), {}, create=True)
    assert vault == {"github.com/o/r": "existing"} and not (folder / "release.jks").exists()


def test_two_creations_at_once_make_one_key(keys, monkeypatch):
    vault, folder = keys
    monkeypatch.setattr(release, "LOCK_SECONDS", 0.3)
    original, nested = release.generated, []

    def racing(store, *args):
        if not nested:   # a second request arrives while the first is making its key
            with pytest.raises(ValueError, match="다른 요청이 쓰고 있다"):
                release.release_key(Path("repo"), {}, create=True)
            nested.append(True)
        return original(store, *args)

    monkeypatch.setattr(release, "generated", racing)
    made = release.release_key(Path("repo"), {}, create=True)

    assert nested and vault == {"github.com/o/r": made["password"]}
    assert release.release_key(Path("repo"), {})["cert"] == made["cert"]


def test_the_record_is_written_whole_or_not_at_all(keys, monkeypatch):
    vault, folder = keys
    replace = Path.replace

    def stopped(self, target):
        if Path(target).name == "certificate.sha256":
            raise OSError("stopped")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", stopped)
    with pytest.raises(OSError, match="stopped"):
        release.release_key(Path("repo"), {}, create=True)
    assert list(folder.glob("certificate.sha256.*.new")) == []   # the failed call leaves no staging file
    monkeypatch.setattr(Path, "replace", replace)

    assert not (folder / "certificate.sha256").exists()
    made = release.release_key(Path("repo"), {}, create=True)   # finished from the keystore and its password
    assert vault["github.com/o/r"] == made["password"]
    (folder / "certificate.sha256").write_text("trunc", encoding="utf-8")
    with pytest.raises(ValueError, match="깨졌다"):
        release.release_key(Path("repo"), {})


class _Flushless:
    """A staging handle whose `write` succeeds and whose close fails, as a full disk's final flush does."""

    def __init__(self, real):
        self.real = real

    def __enter__(self):
        return self

    def fileno(self):
        return self.real.fileno()

    def write(self, text):
        return self.real.write(text)

    def __exit__(self, *exc):
        self.real.close()
        raise OSError("flush failed")


def test_a_failed_flush_or_cleanup_keeps_the_original_error_and_never_removes_a_file_swapped_in(tmp_path, monkeypatch):
    target, opened = tmp_path / "ledger.json", open
    monkeypatch.setattr("builtins.open", lambda *a, **k: _Flushless(opened(*a, **k)) if a[1:2] == ("x",) else opened(*a, **k))
    with pytest.raises(OSError, match="flush failed"):   # the close is inside the cleanup's reach
        release.replaced(target, "{}")
    monkeypatch.setattr("builtins.open", opened)
    assert list(tmp_path.iterdir()) == []

    recorded, replace, unlink = [], Path.replace, Path.unlink
    monkeypatch.setattr(release.errorlog, "record", lambda kind, exc, **k: recorded.append((kind, str(exc))))
    monkeypatch.setattr(Path, "replace", lambda self, t: (_ for _ in ()).throw(OSError("stopped")))
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(PermissionError("staging locked")))
    with pytest.raises(OSError, match="stopped"):   # never "staging locked"
        release.replaced(target, "{}")
    assert recorded and recorded[0][0] == "release-staging" and "staging locked" in recorded[0][1]
    monkeypatch.setattr(Path, "unlink", unlink)

    def swapped(self, t):   # someone moves ours aside and puts another file at its name
        replace(self, self.with_name("moved"))
        self.write_text("not ours", encoding="utf-8")
        raise OSError("stopped")

    for stray in tmp_path.glob("*.new"):
        stray.unlink()
    monkeypatch.setattr(Path, "replace", swapped)
    with pytest.raises(OSError, match="stopped"):
        release.replaced(target, "{}")
    assert [p.read_text(encoding="utf-8") for p in tmp_path.glob("*.new")] == ["not ours"]


def test_keys_inside_the_checkout_or_through_a_link_are_refused(keys, tmp_path, monkeypatch):
    repo = tmp_path / "checkout"
    monkeypatch.setattr(release, "KEYS", repo / "appdata" / "wiki-agent-data" / "release-keys")
    with pytest.raises(ValueError, match="체크아웃 안"):
        release.release_key(repo, {}, create=True)

    monkeypatch.setattr(release, "KEYS", tmp_path / "keys")
    monkeypatch.setattr(release, "is_junction", lambda p: Path(p).name == "certificate.sha256")
    with pytest.raises(ValueError, match="링크를 거친다"):
        release.release_key(repo, {}, create=True)


def test_a_key_tools_diagnostic_and_a_refused_origin_never_carry_a_secret(monkeypatch):
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, "", "keystore password was 'hunter2-hunter2' and it failed"))
    with pytest.raises(ValueError) as caught:
        release.run(["keytool"], {release.SECRET: "hunter2-hunter2"})
    assert "hunter2" not in str(caught.value) and "***" in str(caught.value)

    for url in ("https://user:tok3n@gitlab.com/o/r.git", "https://github.com/o/r.git?access_token=tok3n"):
        monkeypatch.setattr(release, "git", lambda *a, url=url, **k: url + "\n")
        with pytest.raises(ValueError, match="GitHub") as caught:
            release.remote(Path("x"))
        assert "tok3n" not in str(caught.value)


def test_the_packaged_apps_own_data_folder_holds_its_keys(keys, tmp_path, monkeypatch):
    vault, _ = keys
    monkeypatch.setattr(release.paths, "DATA", tmp_path / "wiki-agent-data")
    monkeypatch.setattr(release.paths, "RESOURCES", tmp_path / "Program Files" / "wiki-agent")
    monkeypatch.setattr(release, "KEYS", release.paths.DATA / "release-keys")

    assert release.release_key(tmp_path / "checkout", {}, create=True)["password"] == vault["github.com/o/r"]

    monkeypatch.setattr(release, "KEYS", release.paths.RESOURCES / "release-keys")
    with pytest.raises(ValueError, match="체크아웃 안"):
        release.release_key(tmp_path / "checkout", {}, create=True)


def test_a_staged_keystore_with_no_password_is_left_untouched(keys):
    vault, folder = keys
    folder.mkdir(parents=True)
    staged = release.generated(folder / "release.jks.new", "release", release.KEY_DAYS, "CN=github.com/o/r", {})
    before = staged["keystore"].read_bytes()

    with pytest.raises(ValueError, match="비밀번호가 자격 증명 관리자에 없다"):
        release.release_key(Path("repo"), {}, create=True)
    assert staged["keystore"].read_bytes() == before and vault == {} and not (folder / "release.jks").exists()


def test_writing_the_record_never_writes_through_a_name_that_already_exists(keys, monkeypatch):
    vault, folder = keys
    release.release_key(Path("repo"), {}, create=True)
    store = folder / "release.jks"
    before = store.read_bytes()
    (folder / "certificate.sha256").unlink()
    monkeypatch.setattr(release.secrets, "token_hex", lambda n: "aaaa")   # the staging name is known in advance
    release.os.link(store, folder / "certificate.sha256.aaaa.new")   # and already aliases the keystore

    with pytest.raises(FileExistsError):
        release.release_key(Path("repo"), {}, create=True)
    assert store.read_bytes() == before and not (folder / "certificate.sha256").exists()


def test_the_first_release_note_tells_debug_installs_to_reinstall():
    assert "디버그" in release.FIRST_RELEASE_NOTE and "지우고" in release.FIRST_RELEASE_NOTE
