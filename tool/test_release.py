"""Release step 1: the `[release]` reader, the seed, the version selector and the APK checks
(`docs/plans/release/0-overview.md`). The real build and signing run in `release.rehearse`."""

import json
import subprocess
from pathlib import Path

import pytest

from main import release

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
    ("[release]\nversion_source = 'a.json'\n", "`[release.android]` 표가 없다"),
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


def test_an_attempt_checkout_is_a_fresh_clean_detached_copy_and_never_a_second_one(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "WORK", tmp_path / "work")
    repo, sha = committed(tmp_path, {"a.txt": "a"})

    src = release.checkout(repo, sha, "a1")

    assert release.git(src, "rev-parse", "HEAD").strip() == sha and (src / "a.txt").read_text() == "a"
    assert "a1" not in release.git(repo, "worktree", "list")
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
    monkeypatch.setenv(release.SECRET, "inherited")
    kit = {"java": tmp_path, "sdk": tmp_path, "ndk": tmp_path}

    apk = release.build(src, {**ANDROID, "artifact": "out/app.apk"}, "0.1.1", kit, None)

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


def test_every_test_keystore_goes_even_when_a_step_after_its_creation_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "WORK", tmp_path / "work")
    monkeypatch.setattr(release, "tools", lambda: {})
    monkeypatch.setattr(release, "pin", lambda repo, ref: "0" * 40)
    monkeypatch.setattr(release, "settings", lambda repo, sha: {"version_source": "v", "android": ANDROID})
    monkeypatch.setattr(release, "seed", lambda repo, sha, source: "0.1.0")
    monkeypatch.setattr(release, "git", lambda *a, **k: "")

    def key(store, kit):
        store.write_bytes(b"key")   # written, then the tool reports failure
        return {"keystore": store, "alias": "a", "password": "p"}

    def broken(*_):
        raise ValueError("keytool failed")

    monkeypatch.setattr(release, "test_key", key)
    monkeypatch.setattr(release, "certificate", broken)

    with pytest.raises(ValueError, match="keytool failed"):
        release.rehearse(tmp_path / "repo")
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
    with pytest.raises(OSError, match="시험 키를 지우지 못했다"):
        release.rehearse(tmp_path / "repo")
    assert tried == ["rehearsal.jks", "other.jks"]   # the locked first key never spares the second


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


def test_only_the_key_tool_gets_the_password_when_signing(tmp_path, monkeypatch):
    monkeypatch.setenv(release.SECRET, "inherited")
    calls = []
    monkeypatch.setattr(release, "run", lambda argv, env=None, timeout=300: calls.append((argv, env)) or "")
    kit = {"java": tmp_path, "sdk": tmp_path, "ndk": tmp_path, "zipalign": "zipalign", "apksigner": "apksigner"}

    release.sign(tmp_path / "in.apk", tmp_path / "out.apk", {"keystore": "k", "alias": "a", "password": "p"}, kit)

    (align, align_env), (signer, signer_env) = calls
    assert align[0] == "zipalign" and align_env is not None and release.SECRET not in align_env
    assert signer[0] == "apksigner" and signer_env[release.SECRET] == "p"
