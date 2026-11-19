"""The Windows installer kind of a release (`release_desktop`, plan step 6)."""

from __future__ import annotations

import ctypes
import json
import lzma
import os
import struct
import sys
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

import android_owned
from main import release, release_desktop as rd, release_rehearsal
from test_release import _rehearsable

DESKTOP = {"build": "b", "artifact": "nsis/r_{version}_x64-setup.exe", "exe": "r.exe", "product_name": "r",
           "publisher": "o"}
GOOD = {"version": "0.1.1", "file_version": "0.1.1", "product": "r", "exe_version": "0.1.1", "exe_product": "r",
        "publisher": "o", "script_product": True, "script_publisher": True}
APP = b"MZ app " + rd.MARKER + b"UNK tail"   # the app EXE as built; its packed copy says NSIS


def nsis(tmp_path: Path, script: bytes, *files: bytes) -> Path:
    """A synthetic installer laid out as NSIS does: its first header, then one solid LZMA stream holding the
    script's length and bytes, then each file's length and bytes."""

    body = struct.pack("<I", len(script)) + script + b"".join(struct.pack("<I", len(f)) + f for f in files)
    packed = lzma.compress(body, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}])
    props = bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 16)   # pb=2, lp=0, lc=3, as the filter's default
    head = struct.pack("<I", 0) + rd.MAGIC + struct.pack("<II", len(script), len(packed) + 5)
    (file := tmp_path / "setup.exe").write_bytes(b"MZ stub" + head + props + packed)
    return file


@pytest.fixture
def resources(monkeypatch):
    """Version resources read from a synthetic file's bytes: a file holding `MZ app` is the app EXE."""

    def version_info(file):
        app = Path(file).read_bytes().startswith(b"MZ app")
        return ({"version": "0.1.1", "ProductName": "r", "CompanyName": "o"} if app
                else {"version": "0.1.1", "ProductName": "r", "FileVersion": "0.1.1"})

    monkeypatch.setattr(rd, "version_info", version_info)


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


def test_versions_compare_as_four_integers_and_an_unreadable_previous_one_is_never_below():
    assert rd.newer("0.1.3", "0.1.2.7") and not rd.newer("0.1.1", "0.1.2.7") and not rd.newer("0.1.2", "0.1.2.0")
    assert not rd.newer("0.1.3", "unknown") and not rd.newer("0.1.3", "")
    assert any("0.1.2.7 보다 크지 않다" in why
               for why in rd.built_wrong(GOOD, "0.1.1", DESKTOP, {"version": "0.1.2.7", "product": "r", "publisher": "o"}))


def test_the_installed_app_is_read_from_inside_the_installer_and_must_be_the_one_built(tmp_path, resources):
    script = "r".encode("utf-16-le") + b"|" + "o".encode("utf-16-le")
    packed = APP.replace(b"UNK", rd.NSIS)
    other = b"MZ helper " + rd.MARKER + b"UNK"   # a binary Tauri packs beside the app, as the real one does
    installer = nsis(tmp_path, script, b"icon", other, packed)
    (exe := tmp_path / "r.exe").write_bytes(APP)
    seen = rd.facts(installer, exe)
    assert {k: seen[k] for k in GOOD} == GOOD and rd.built_wrong(seen, "0.1.1", DESKTOP) == []
    assert rd.facts(installer)["publisher"] == "o"   # a previous installer, without the build's EXE
    exe.write_bytes(APP + b" rebuilt")
    with pytest.raises(ValueError, match="빌드가 만든 앱 EXE 가 0 개"):
        rd.facts(installer, exe)


def test_an_installer_whose_script_names_neither_identity_is_refused(tmp_path, resources):
    seen = rd.facts(nsis(tmp_path, b"elsewhere", APP.replace(b"UNK", rd.NSIS)))
    why = rd.built_wrong(seen, "0.1.1", DESKTOP)
    assert len(why) == 2 and all("설치 스크립트가" in w for w in why)


def test_an_app_exe_or_another_packing_is_not_an_installer(tmp_path):
    (exe := tmp_path / "r.exe").write_bytes(APP)
    with pytest.raises(ValueError, match="NSIS 설치 파일이 아니다"):
        rd.payload(exe)
    (zlib := tmp_path / "z.exe").write_bytes(b"MZ" + struct.pack("<I", 0) + rd.MAGIC + struct.pack("<II", 4, 9)
                                             + b"\xff" * 40)
    with pytest.raises(ValueError, match="solid LZMA"):
        rd.payload(zlib)


@pytest.mark.parametrize("length, signature", [(4, rd.FIXED_SIGNATURE), (rd.FIXED_SIZE, 0)])
def test_a_fixed_version_block_that_is_short_or_unsigned_is_refused(monkeypatch, length, signature):
    backing = (wintypes.DWORD * 13)(signature, 0, 1, 2 << 16)

    def query(data, sub, pointer, size):
        if sub != "\\":
            return 0
        pointer._obj.value, size._obj.value = ctypes.addressof(backing), length
        return 1

    dll = SimpleNamespace(GetFileVersionInfoSizeW=lambda *_: 52, GetFileVersionInfoW=lambda *_: 1, VerQueryValueW=query)
    monkeypatch.setattr(rd, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(rd.ctypes, "WinDLL", lambda *a, **k: dll)
    with pytest.raises(ValueError, match="고정 버전이 없다"):
        rd.version_info(Path("synthetic.exe"))
    backing[0] = rd.FIXED_SIGNATURE
    if length == rd.FIXED_SIZE:
        assert rd.version_info(Path("synthetic.exe"))["version"] == "0.1.2"


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
