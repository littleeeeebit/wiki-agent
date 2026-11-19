"""The Windows installer kind of a release (`release_desktop`, plan step 6)."""

from __future__ import annotations

import ctypes
import hashlib
import json
import lzma
import os
import struct
import sys
import zlib
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
        "publisher": "o", "reg_product": "r", "reg_publisher": "o", "reg_version": "0.1.1", "install_key": True,
        "scope": "currentUser", "view": "256", "machine": rd.AMD64}
# The app EXE as built: Tauri's runtime names the NSIS value, then holds the placeholder its bundler sets.
def pe(machine=rd.AMD64) -> bytes:   # a PE header: `MZ`, `e_lfanew` at 0x3C, then `PE\0\0` and `Machine`
    return b"MZ app ".ljust(0x3C, b"\0") + struct.pack("<I", 0x40) + b"PE\0\0" + struct.pack("<H", machine)


APP = pe() + b" " + rd.MARKER + rd.NSIS + b" runtime " + rd.MARKER + b"UNK tail"
PACKED = pe() + b" " + rd.MARKER + rd.NSIS + b" runtime " + rd.MARKER + rd.NSIS + b" tail"
UNINSTALL = rd.UNINSTALL + "r"
TAURI = [(UNINSTALL, "DisplayName", "r"), (UNINSTALL, "Publisher", "o"), (UNINSTALL, "DisplayVersion", "0.1.1"),
         (UNINSTALL, "MainBinaryName", "r.exe"), ("Software\\o\\r", "", None)]


def nsis(tmp_path: Path, writes=TAURI, files=(("helper.exe", b"MZ helper " + rd.MARKER + rd.NSIS), ("r.exe", PACKED)),
         flags=0, dictionary=1 << 16, context=("0",), view=("256",),
         uninstaller=(b"icon", b"MZ uninstaller"), out=None, extra=()) -> Path:
    """A synthetic installer laid out as Unicode NSIS 3 lays one out: a stub, the first header on a 512-byte
    boundary, one solid LZMA stream holding the script (its blocks, its entries, its strings), each file and the
    uninstaller's two records, and the CRC32 of everything from byte 512. A value of None is a variable, as
    `$INSTDIR` is; an uninstaller record of None is one the stream never holds. `context` and `view` are what
    `SetShellVarContext` and `SetRegView` set; `out` is what `SetOutPath` sets before the files, `$INSTDIR` by
    default; `extra` holds more entries as (opcode, operands)."""

    table, at = bytearray(), {}

    def ptr(text):
        if text is None:
            return ptr(rd.INSTDIR)
        if text not in at:
            at[text] = len(table) // 2
            table.extend(text.encode("utf-16-le") + b"\0\0")
        return at[text]

    ptr("")
    data, entries = bytearray(), [(rd.EW_CREATEDIR, [ptr(out), 1, 0, 0, 0, 0])]
    for name, body in files:   # a body of None is an entry whose record the stream never holds
        entries.append((rd.EW_EXTRACTFILE, [0, ptr(name), len(data) + (body is None) * 64, 0, 0, 0]))
        data.extend(b"" if body is None else struct.pack("<I", len(body)) + body)
    icon, program = uninstaller
    entries.append((rd.EW_WRITEUNINSTALLER, [ptr(rd.INSTDIR + "\\uninstall.exe"), len(data), len(icon), 0, 0, 0]))
    data.extend(b"".join(struct.pack("<I", len(r)) + r for r in (icon, program) if r is not None))
    entries += [(rd.EW_SETFLAG, [flag, ptr(value), 0, 0, 0, 0])
                for flag, values in ((rd.FLAG_CONTEXT, context), (rd.FLAG_VIEW, view)) for value in values]
    entries += list(extra)
    entries += [(rd.EW_WRITEREG, [w[3] if len(w) > 3 else rd.SHCTX, ptr(w[0]), ptr(w[1]), ptr(w[2]), 1, 1])
                for w in writes]   # a fourth item is another root
    blocks = 4 + 8 * 8
    strings = blocks + 28 * len(entries)
    script = (struct.pack("<I", 0) + struct.pack("<16I", 0, 0, 0, 0, blocks, len(entries), strings, 0, *[0] * 8)
              + b"".join(struct.pack("<i6i", w, *a) for w, a in entries) + bytes(table))
    packed = lzma.compress(struct.pack("<I", len(script)) + script + data, format=lzma.FORMAT_RAW,
                           filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}])
    props = bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", dictionary)   # pb=2, lp=0, lc=3: the filter's default
    head = struct.pack("<I", flags) + rd.MAGIC + struct.pack("<II", len(script), 28 + 5 + len(packed) + 4)
    body = b"MZ stub".ljust(1024, b"\0") + head + props + packed
    (file := tmp_path / "setup.exe").write_bytes(body + struct.pack("<I", zlib.crc32(body[512:])))
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
    assert rd.built_wrong(GOOD, "0.1.1", DESKTOP, {**GOOD, "version": "0.1.0"}) == []
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


def test_the_installed_app_is_the_registered_main_binary_and_must_be_the_one_built(tmp_path, resources, monkeypatch):
    installer = nsis(tmp_path)
    (exe := tmp_path / "r.exe").write_bytes(APP)
    monkeypatch.setattr(android_owned, "sha256", lambda f: pytest.fail("the digest is of the one snapshot"))
    seen = rd.facts(installer, exe)
    assert {k: seen[k] for k in GOOD} == GOOD and rd.built_wrong(seen, "0.1.1", DESKTOP) == []
    assert seen["sha256"] == hashlib.sha256(installer.read_bytes()).hexdigest()
    assert rd.facts(installer)["publisher"] == "o"   # a previous installer, without the build's EXE
    exe.write_bytes(APP + b" rebuilt")
    with pytest.raises(ValueError, match="빌드가 만든 앱 EXE 와 다르다"):
        rd.facts(installer, exe)


@pytest.mark.parametrize("writes, said", [
    ([(UNINSTALL, "DisplayName", "r Tools"), *TAURI[1:]], "제품 이름 'r Tools'"),   # only a longer label
    ([*TAURI[:1], (UNINSTALL, "Publisher", "o Labs"), *TAURI[2:]], "게시자 'o Labs'"),
    ([*TAURI, (UNINSTALL, "Publisher", "p")], "게시자 None"),   # two writes that disagree
    ([*TAURI[:2], (UNINSTALL, "DisplayVersion", "0.1.0"), *TAURI[3:]], "버전 '0.1.0'"),
    (TAURI[:4], "설치 위치를"),
    ([*TAURI[:4], ("Software" + chr(92) + "o" + chr(92) + "r", "", "C:" + chr(92) + "elsewhere")], "설치 위치를"),
    ([*TAURI[:4], ("Software" + chr(92) + "o" + chr(92) + "r", "", None, -2147483647)], "설치 위치를"),   # HKCU, not SHCTX
    ([*TAURI, (UNINSTALL.upper(), "publisher", "p")], "게시자 None"),   # the same key and name to Windows
    ([*TAURI[:1], (UNINSTALL, "Publisher", "o", -2147483646), *TAURI[2:]], "게시자 None"),   # HKLM
])
def test_what_the_script_registers_must_be_the_identity(tmp_path, resources, writes, said):
    why = rd.built_wrong(rd.facts(nsis(tmp_path, writes)), "0.1.1", DESKTOP)
    assert any(said in w for w in why), why


@pytest.mark.parametrize("context, view, said", [
    (("0", "1"), ("256",), "설치 범위(사용자별 또는 컴퓨터 전체)를 하나로"),   # installMode "both"
    ((), ("256",), "설치 범위(사용자별 또는 컴퓨터 전체)를 하나로"),
    (("0",), ("256", "0"), "레지스트리 보기를 하나로"),
])
def test_an_install_scope_the_script_leaves_open_is_refused(tmp_path, resources, context, view, said):
    why = rd.built_wrong(rd.facts(nsis(tmp_path, context=context, view=view)), "0.1.1", DESKTOP)
    assert any(said in w for w in why), why


def test_a_scope_that_moved_since_the_previous_installer_is_refused(tmp_path, resources):
    previous = {**GOOD, "version": "0.1.0"}
    seen = rd.facts(nsis(tmp_path, context=("1",)))
    assert seen["scope"] == "perMachine" and rd.built_wrong(seen, "0.1.1", DESKTOP) == []
    assert any("'perMachine' 가 이전 정식 설치 파일의 'currentUser'" in w
               for w in rd.built_wrong(seen, "0.1.1", DESKTOP, previous))
    assert any("레지스트리 보기 '0'" in w for w in rd.built_wrong(rd.facts(nsis(tmp_path, view=())), "0.1.1", DESKTOP,
                                                                     previous))


def test_an_uninstaller_whose_second_record_is_missing_is_refused(tmp_path, resources):
    with pytest.raises(ValueError, match="기록이 끝나기 전에 끝난다"):
        rd.facts(nsis(tmp_path, uninstaller=(b"icon", None)))


def test_the_main_binary_is_found_by_where_it_lands(tmp_path, resources):
    beside = (("r.exe", PACKED), ("tools\\r.exe", b"MZ tool"), ("\x03\u809a\\r.exe", b"MZ plugin"))
    assert rd.facts(nsis(tmp_path, files=beside))["exe_version"] == "0.1.1"   # three files, three places
    with pytest.raises(ValueError, match="주 실행 파일 'r.exe' 를 하나로"):   # only under a resource folder
        rd.facts(nsis(tmp_path, files=(("tools\\r.exe", PACKED),)))
    with pytest.raises(ValueError, match="출력 위치를"):
        rd.facts(nsis(tmp_path, out="C:\\elsewhere"))
    distinct = (("r.exe", PACKED), ("Straße.exe", b"MZ one"), ("STRASSE.exe", b"MZ two"))   # apart to Windows
    assert rd.facts(nsis(tmp_path, files=distinct))["exe_version"] == "0.1.1"


@pytest.mark.skipif(os.name != "nt", reason="the uppercase table is Windows' own")
def test_names_compare_through_the_windows_uppercase_table(tmp_path, resources):
    assert rd.upper("Index.exe") != rd.upper("ındex.exe") and rd.upper("ſ") == "ſ" and rd.upper("é") == "É"
    distinct = (("r.exe", PACKED), ("Index.exe", b"MZ one"), ("ındex.exe", b"MZ two"))   # dotless i, apart to Windows
    assert rd.facts(nsis(tmp_path, files=distinct))["exe_version"] == "0.1.1"


def test_an_output_directory_moved_by_any_code_path_is_refused(tmp_path, resources):
    # `StrCpy $OUTDIR` and `Pop $OUTDIR`, wherever they sit: a call can run them before the main binary
    for entry in ((rd.EW_ASSIGNVAR, [rd.OUTDIR, 0, 0, 0, 0, 0]), (rd.EW_PUSHPOP, [rd.OUTDIR, 1, 0, 0, 0, 0])):
        with pytest.raises(ValueError, match="OUTDIR"):
            rd.facts(nsis(tmp_path, extra=(entry,)))
    assert rd.facts(nsis(tmp_path, extra=((rd.EW_PUSHPOP, [rd.OUTDIR, 0, 0, 0, 0, 0]),)))   # `Push $OUTDIR` reads it


def test_an_app_built_for_another_processor_is_not_shipped_as_x64(tmp_path, resources):
    arm = pe(0xAA64) + b" " + rd.MARKER + rd.NSIS
    why = rd.built_wrong(rd.facts(nsis(tmp_path, files=(("r.exe", arm),))), "0.1.1", DESKTOP)
    assert any("Machine 43620 가 x64(0x8664) 가 아니다" in w for w in why), why
    assert rd.machine(b"MZ") is None and rd.machine(b"MZ".ljust(0x40, b"\0")) is None


def test_a_main_binary_the_uninstaller_overwrites_is_refused(tmp_path, resources):
    writes = [*TAURI[:3], (UNINSTALL, "MainBinaryName", "Uninstall.exe"), TAURI[4]]
    with pytest.raises(ValueError, match="주 실행 파일 'Uninstall.exe' 를 하나로"):
        rd.facts(nsis(tmp_path, writes=writes, files=(("Uninstall.exe", PACKED),)))
    assert rd.destination(None, "r.exe") is None and rd.destination(None, "") is None


def test_a_main_binary_the_script_does_not_extract_once_is_refused(tmp_path, resources):
    with pytest.raises(ValueError, match="주 실행 파일 'r.exe' 를 하나로"):
        rd.facts(nsis(tmp_path, files=(("other.exe", PACKED),)))
    for second in ("r.exe", "R.EXE", "\x03\u8095\\r.exe", "r.exe. ", "tools\\..\\r.exe", "tools/../R.exe"):
        # the same file to Windows, so a later entry overwrites the main binary
        with pytest.raises(ValueError, match="주 실행 파일 'r.exe' 를 하나로"):
            rd.facts(nsis(tmp_path, files=(("r.exe", PACKED), (second, PACKED + b"2"))))


def test_a_file_entry_whose_record_is_not_in_the_stream_is_refused_after_the_main_binary_too(tmp_path, resources):
    files = (("r.exe", PACKED), ("later.dll", None))
    with pytest.raises(ValueError, match="기록이 끝나기 전에 끝난다"):
        rd.facts(nsis(tmp_path, files=files))


def test_a_string_is_read_only_terminated_inside_its_block_and_as_valid_utf16():
    table = "ab".encode("utf-16-le") + b"\0\0" + "cd".encode("utf-16-le")
    block = (0, len(table))
    assert rd._string(table, block, 0) == "ab" and rd._string(table, block, -1) is None
    assert rd._string(table, block, 3) is None   # "cd" runs to the end of the block with no NUL
    assert rd._string(table + b"\0\0", (0, len(table)), 3) is None   # its NUL lies past the block
    assert rd._string(b"\x00\xd8\0\0", (0, 4), 0) is None   # a lone surrogate


def test_an_installer_or_app_over_its_limit_is_refused_before_it_is_read(tmp_path, resources, monkeypatch):
    installer = nsis(tmp_path)
    (exe := tmp_path / "r.exe").write_bytes(APP)
    whole = Path.read_bytes   # the fixture's version resources read the temporary copies

    def read_bytes(self):
        assert self not in (installer, exe), f"an unbounded read of {self.name}"
        return whole(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setitem(rd.LIMITS, "installer", installer.stat().st_size - 1)
    for call in (lambda: rd.facts(installer), lambda: rd.readable(installer)):
        with pytest.raises(ValueError, match="크기 한도"):
            call()
    monkeypatch.setitem(rd.LIMITS, "installer", 1 << 30)
    monkeypatch.setitem(rd.LIMITS, "app", len(APP) - 1)
    monkeypatch.setattr(rd, "_records", lambda stream, offsets, main: PACKED)   # only the loose EXE is over
    with pytest.raises(ValueError, match="`r.exe` 가 크기 한도"):
        rd.facts(installer, exe)


def test_a_damaged_or_foreign_installer_is_a_read_error(tmp_path, resources):
    (exe := tmp_path / "r.exe").write_bytes(APP)
    with pytest.raises(ValueError, match="NSIS 설치 파일이 아니다"):
        rd.facts(exe)
    good = nsis(tmp_path).read_bytes()
    for name, data, said in (
            ("crc", good[:1060] + bytes([good[1060] ^ 1]) + good[1061:], "CRC"),
            ("short", good[:1024 + 20], "NSIS 설치 파일이 아니다"),
            ("cut", good[:-40] + good[-4:], "머리말이 지원하지 않는"),
    ):
        (bad := tmp_path / f"{name}.exe").write_bytes(data)
        with pytest.raises(ValueError, match=said):
            rd.facts(bad)
    for kwargs, said in (({"flags": 4}, "flags 0x4"), ({"flags": 1}, "flags 0x1"),
                         ({"dictionary": 1 << 30}, "한도를 넘는다")):
        with pytest.raises(ValueError, match=said):
            rd.facts(nsis(tmp_path, **kwargs))


def test_a_record_that_runs_past_the_stream_or_the_limit_is_refused(tmp_path, resources, monkeypatch):
    installer = nsis(tmp_path)
    monkeypatch.setitem(rd.LIMITS, "app", len(PACKED) - 1)
    with pytest.raises(ValueError, match="한도를 넘는다"):
        rd.facts(installer)
    monkeypatch.setitem(rd.LIMITS, "app", 1 << 30)
    monkeypatch.setitem(rd.LIMITS, "unpacked", 200)
    with pytest.raises(ValueError, match="한도를 넘는다"):
        rd.facts(installer)
    monkeypatch.setitem(rd.LIMITS, "unpacked", 4 << 30)
    data = installer.read_bytes()
    body = data[:-4]
    at = body.index(rd.MAGIC) - 4
    short = body[:at + 33] + lzma.compress(lzma.decompress(body[at + 33:], format=lzma.FORMAT_RAW, filters=[
        {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}])[:-5], format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}])   # the main binary's record cut short
    short = short[:at + 24] + struct.pack("<I", len(short) + 4 - at) + short[at + 28:]
    (cut := tmp_path / "cut.exe").write_bytes(short + struct.pack("<I", zlib.crc32(short[512:])))
    with pytest.raises(ValueError, match="기록이 끝나기 전에 끝난다"):
        rd.facts(cut)


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


def test_a_desktop_build_gets_its_version_and_no_android_code(tmp_path, monkeypatch):
    (src := tmp_path / "src").mkdir()
    seen = {}

    def gate(cmd, path, halt, env, timeout, idle):
        seen.update(env)
        for key in ("artifact", "exe"):
            (made := release.output(path, DESKTOP, "1.1000.0", key)).parent.mkdir(parents=True, exist_ok=True)
            made.write_text("x")
        return 0, "", ""

    monkeypatch.setattr(release.specs, "gate", gate)
    release.build(src, DESKTOP, "1.1000.0", {}, release.threading.Event(), read=lambda f: None)
    assert seen["RELEASE_VERSION"] == "1.1000.0" and "RELEASE_VERSION_CODE" not in seen
    assert json.loads(Path(seen["RELEASE_TAURI_CONFIG"]).read_text()) == {"version": "1.1000.0", "bundle": {}}


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
        return {**GOOD, "version": version, "file_version": version, "exe_version": version, "reg_version": version,
                "sha256": android_owned.sha256(Path(installer))}

    monkeypatch.setattr(release, "build", build)
    monkeypatch.setattr(rd, "facts", facts)
    done = release_rehearsal.rehearse(tmp_path / "repo")
    assert done["ok"] and [r["version"] for r in done["rounds"]] == ["0.1.0", "0.1.1"] and done["cert"] is None
    assert sorted(p.name for p in Path(done["folder"]).iterdir()) == ["1-0.1.0-x64-setup.exe", "2-0.1.1-x64-setup.exe"]
    assert set(done["refusals"]) == {"product_name"}
