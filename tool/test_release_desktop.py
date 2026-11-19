"""The Windows installer kind of a release (`release_desktop`, plan step 6)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import android_owned
from main import release, release_desktop as rd, release_rehearsal
from test_release import _rehearsable

DESKTOP = {"build": "b", "artifact": "nsis/r_{version}_x64-setup.exe", "exe": "r.exe", "product_name": "r",
           "publisher": "o"}
GOOD = {"version": "0.1.1", "file_version": "0.1.1", "product": "r", "exe_version": "0.1.1", "exe_product": "r",
        "publisher": "o"}


@pytest.mark.skipif(os.name != "nt", reason="version resources are read through Windows")
def test_the_version_resource_is_read_from_a_real_windows_executable():
    shown = rd.version_info(Path(sys.executable))
    assert release.SEMVER.match(shown["version"]) and shown["ProductName"] and shown["FileVersion"]
    with pytest.raises(ValueError, match="버전 리소스"):
        rd.version_info(Path(__file__))   # a file Windows reads no version from


def test_a_good_installer_passes_and_a_four_part_version_reads_as_semver():
    assert rd.built_wrong(GOOD, "0.1.1", DESKTOP, {"version": "0.1.0", "product": "r", "publisher": "o"}) == []
    assert rd.built_wrong({**GOOD, "file_version": "0.1.1.0"}, "0.1.1", DESKTOP) == []
    assert rd.plain("0.1.1.0") == "0.1.1" and rd.plain("0.1.1.2") == "0.1.1.2"


@pytest.mark.parametrize("seen, previous, said", [
    ({"version": "0.1.0"}, None, "설치 파일 버전 '0.1.0'"),
    ({"exe_version": "0.1.0"}, None, "앱 EXE 버전 '0.1.0'"),
    ({}, {"version": "0.1.1", "product": "r"}, "이전 정식 설치 파일의 0.1.1 보다 크지 않다"),
    ({"product": "s"}, None, "설치 파일 제품 이름 's' 가 설정의 'r'"),
    ({}, {"version": "0.1.0", "product": "s"}, "이전 정식 설치 파일의 's'"),
    ({"publisher": "p"}, None, "게시자 'p' 가 설정의 'o'"),
    ({}, {"version": "0.1.0", "product": "r", "publisher": "p"}, "게시자 'o' 가 이전 정식 설치 파일의 'p'"),
])
def test_each_identity_or_version_mismatch_is_refused(seen, previous, said):
    assert any(said in why for why in rd.built_wrong({**GOOD, **seen}, "0.1.1", DESKTOP, previous))


def test_a_previous_installer_whose_publisher_was_never_recorded_is_held_to_the_adapter_only():
    assert rd.built_wrong(GOOD, "0.1.1", DESKTOP, {"version": "0.1.0", "product": "r", "publisher": None}) == []


def test_a_build_command_that_writes_no_app_exe_is_the_builds_failure(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()

    def gate(cmd, path, halt, env, timeout, idle):
        installer = release.output(path, DESKTOP, "0.1.0")
        installer.parent.mkdir(parents=True)
        installer.write_text("x")   # the installer, but no `r.exe` beside it
        return 0, "", ""

    monkeypatch.setattr(release.specs, "gate", gate)
    with pytest.raises(release.BuildFailed, match="r.exe"):
        release.build(src, DESKTOP, "0.1.0", {}, release.threading.Event(), read=lambda f: None)


def test_a_desktop_only_rehearsal_builds_two_installers_and_catches_a_renamed_product(tmp_path, monkeypatch):
    _rehearsable(tmp_path, monkeypatch)
    monkeypatch.setattr(release, "settings", lambda repo, sha: {"version_source": "v", "desktop": DESKTOP})
    monkeypatch.setattr(release, "test_key", lambda *a: pytest.fail("a desktop-only rehearsal needs no key"))
    monkeypatch.setattr(release, "checkout", lambda repo, sha, at: release.place(repo, at).mkdir(parents=True)
                        or release.place(repo, at))

    def build(src, kind, version, kit, halt, read=None):
        assert read is rd.readable
        installer = release.output(src, kind, version)
        installer.parent.mkdir(parents=True)
        installer.write_text(json.dumps({"version": version}))
        release.output(src, kind, version, "exe").write_text("{}")
        return installer

    def facts(installer, exe=None):
        version = json.loads(Path(installer).read_text())["version"]
        return {**GOOD, "version": version, "file_version": version, "exe_version": version,
                "sha256": android_owned.sha256(Path(installer))}

    monkeypatch.setattr(release, "build", build)
    monkeypatch.setattr(rd, "facts", facts)
    done = release_rehearsal.rehearse(tmp_path / "repo")
    assert done["ok"] and [r["version"] for r in done["rounds"]] == ["0.1.0", "0.1.1"] and done["cert"] is None
    assert sorted(p.name for p in Path(done["folder"]).iterdir()) == ["1-0.1.0-x64-setup.exe", "2-0.1.1-x64-setup.exe"]
    assert set(done["refusals"]) == {"product_name"}
