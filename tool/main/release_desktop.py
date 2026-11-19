"""release_desktop — the Windows installer a release ships (`docs/plans/release/0-overview.md`, step 6).

The adapter's `[release.desktop]` command builds Tauri's NSIS installer with the version injected, as the Android
build is (`release.build`). The checks read what Windows and the installer itself will use. The installer's version
resource carries its version and its product name, the name Tauri's NSIS script keys an upgrade on: the uninstall
entry and the install folder are both named after it. Inside, Tauri's solid LZMA data holds the script and the
files it installs (`payload`). The app EXE it installs is the one file whose Tauri bundle-type marker says NSIS; its
version resource carries the version, the product name and the publisher (`CompanyName`), and the script must
name the same product and publisher. A build's installer must hold the very EXE the build made. Nothing is signed:
code signing is not planned yet, so Windows warns before running the installer.
"""

from __future__ import annotations

import ctypes
import lzma
import os
import struct
import tempfile
from ctypes import wintypes
from pathlib import Path

import android_owned

ARCH = "x64"   # the one Windows target the adapter's build makes
SUFFIX = f"-{ARCH}-setup.exe"   # a release's installer asset: `<repo>-<version>-x64-setup.exe`
MAGIC = b"\xef\xbe\xad\xdeNullsoftInst"   # NSIS's first header: flags, this signature, the header's length
# Tauri sets the three bytes after it to `NSS` in the one app an NSIS installer installs; its other binaries,
# and the app as built, keep `UNK`.
MARKER, NSIS = b"__TAURI_BUNDLE_TYPE_VAR_", b"NSS"
FIXED_SIGNATURE, FIXED_SIZE = 0xFEEF04BD, 52   # VS_FIXEDFILEINFO
UNPACKED_LIMIT = 4 << 30   # what one installer may unpack to, at most


def version_info(file: Path) -> dict:
    """`file`'s version resource: its fixed version as SemVer (`0.1.1.0` is `0.1.1`) and its English strings."""

    if os.name != "nt":
        raise ValueError("Windows 버전 리소스는 Windows 에서만 읽는다")
    dll = ctypes.WinDLL("version", use_last_error=True)
    size = dll.GetFileVersionInfoSizeW(str(file), None)
    data = ctypes.create_string_buffer(size or 1)
    if not size or not dll.GetFileVersionInfoW(str(file), 0, size, data):
        raise ValueError(f"`{file.name}` 에 버전 리소스가 없다")
    ptr, n = ctypes.c_void_p(), wintypes.UINT()

    def query(sub: str):
        found = dll.VerQueryValueW(data, sub, ctypes.byref(ptr), ctypes.byref(n)) and n.value and ptr.value
        return (ptr.value, n.value) if found else None

    fixed = query("\\")
    words = fixed and fixed[1] >= FIXED_SIZE and ctypes.cast(fixed[0], ctypes.POINTER(wintypes.DWORD * 13)).contents
    if not words or words[0] != FIXED_SIGNATURE:
        raise ValueError(f"`{file.name}` 에 고정 버전이 없다")
    ms, ls = words[2], words[3]
    parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
    out = {"version": ".".join(map(str, parts[:3] if parts[3] == 0 else parts))}
    pairs = query("\\VarFileInfo\\Translation")
    langs = ctypes.cast(pairs[0], ctypes.POINTER(wintypes.WORD * (pairs[1] // 2))).contents if pairs else []
    for lang in [f"{langs[i]:04x}{langs[i + 1]:04x}" for i in range(0, len(langs) - 1, 2)] or ["040904b0"]:
        for key in ("ProductName", "CompanyName", "FileVersion"):
            got = query(f"\\StringFileInfo\\{lang}\\{key}")
            if got and key not in out:
                out[key] = ctypes.wstring_at(got[0], got[1]).rstrip("\0")
    return out


def payload(installer: Path) -> tuple[bytes, list[bytes]]:
    """The script and the files of an NSIS installer compressed as Tauri does by default, one solid LZMA stream.
    Any other shape is refused: what it would install cannot be read, so it cannot be checked."""

    data = installer.read_bytes()
    at = data.find(MAGIC) - 4
    if at < 0:
        raise ValueError(f"`{installer.name}` 는 NSIS 설치 파일이 아니다")
    header = struct.unpack_from("<I", data, at + 20)[0]
    props = data[at + 28:at + 33]
    try:
        if len(props) < 5 or props[0] >= 225:
            raise lzma.LZMAError("no LZMA properties")
        lc, lp, pb = props[0] % 9, props[0] // 9 % 5, props[0] // 45
        dec = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb,
                                                               "dict_size": struct.unpack("<I", props[1:])[0]}])
        out = dec.decompress(data[at + 33:], max_length=UNPACKED_LIMIT)
    except lzma.LZMAError as exc:
        raise ValueError(f"`{installer.name}` 가 Tauri 기본 압축(solid LZMA)이 아니다 — 담긴 앱을 읽을 수 없다") from exc
    if len(out) < 4 or struct.unpack_from("<I", out)[0] != header:
        raise ValueError(f"`{installer.name}` 의 스크립트 길이가 머리말과 다르다")
    files, pos = [], 4 + header
    while pos + 4 <= len(out):
        size = struct.unpack_from("<I", out, pos)[0] & 0x7FFFFFFF
        files.append(out[pos + 4:pos + 4 + size])
        pos += 4 + size
    return out[4:4 + header], files


def unmarked(binary: bytes) -> bytes:
    """`binary` with Tauri's bundle-type value blanked: the bundler sets it in the copy it packs."""

    at = binary.find(MARKER) + len(MARKER)
    return binary[:at] + b"\0\0\0" + binary[at + 3:] if at >= len(MARKER) else binary


def readable(installer: Path) -> None:
    """Raises `ValueError` unless `installer` has a version resource and an NSIS payload this module can read."""

    version_info(installer)
    payload(installer)


def facts(installer: Path, exe: Path | None = None) -> dict:
    """What the checks compare. The installer's version and product name, and the app it installs: the one
    file carrying Tauri's marker, read from the installer itself. Given the build's `exe`, that app must be it."""

    shown = version_info(installer)
    script, files = payload(installer)
    apps = [f for f in files if f[:2] == b"MZ" and MARKER + NSIS in f]
    if exe is not None:
        built = unmarked(exe.read_bytes())
        apps = [f for f in apps if unmarked(f) == built]
    if len(apps) != 1:
        raise ValueError(f"`{installer.name}` 에 {'빌드가 만든 앱 EXE' if exe else '설치할 앱 EXE'} 가 "
                         f"{len(apps)} 개 들어 있다 — 하나여야 한다")
    with tempfile.TemporaryDirectory() as tmp:   # Windows reads a version resource from a file only
        (copy := Path(tmp) / "app.exe").write_bytes(apps[0])
        app = version_info(copy)

    def named(text: str | None) -> bool:   # the script's strings are UTF-16, as Tauri's NSIS is Unicode
        return bool(text) and text.encode("utf-16-le") in script

    return {"version": shown["version"], "file_version": shown.get("FileVersion"), "product": shown.get("ProductName"),
            "exe_version": app["version"], "exe_product": app.get("ProductName"), "publisher": app.get("CompanyName"),
            "script_product": named(shown.get("ProductName")), "script_publisher": named(app.get("CompanyName")),
            "sha256": android_owned.sha256(installer)}


def plain(text: str | None) -> str | None:
    """A version string as SemVer, `0.1.1.0` as `0.1.1`."""

    return text[:-2] if text and text.count(".") == 3 and text.endswith(".0") else text


def built_wrong(seen: dict, version: str, desktop: dict, previous: dict | None = None) -> list[str]:
    """Why this installer cannot be published, empty when it can: each is the build's, a source change fixes it.
    `previous` is the last production installer's facts, read from its own bytes."""

    out = []
    for name, got in (("설치 파일 버전", seen["version"]), ("설치 파일 FileVersion", plain(seen["file_version"])),
                      ("설치될 앱 EXE 버전", seen.get("exe_version"))):
        if got != version:
            out.append(f"{name} {got!r} 가 버전 {version!r} 와 다르다")
    if previous and not newer(version, previous["version"]):
        out.append(f"버전 {version} 이 이전 정식 설치 파일의 {previous['version']} 보다 크지 않다")
    for what, key, want in (("제품 이름", "script_product", seen["product"]),
                            ("게시자", "script_publisher", seen.get("publisher"))):
        if not seen.get(key):
            out.append(f"설치 스크립트가 {what} {want!r} 를 담지 않는다 — 설치 위치와 업데이트 대상이 확인되지 않는다")
    wants = [("설정", desktop["product_name"]), *((("이전 정식 설치 파일", previous["product"]),) if previous else ())]
    for name, want in wants:
        for what, got in (("설치 파일", seen["product"]), ("설치될 앱 EXE", seen.get("exe_product"))):
            if got != want:
                out.append(f"{what} 제품 이름 {got!r} 가 {name}의 {want!r} 와 다르다 — Windows 는 다른 앱으로 설치한다")
    wants = [("설정", desktop["publisher"]), *((("이전 정식 설치 파일", previous.get("publisher")),) if previous else ())]
    for name, want in wants:
        if seen.get("publisher") != want:
            out.append(f"게시자 {seen.get('publisher')!r} 가 {name}의 {want!r} 와 다르다 — 설치된 앱의 설정 위치가 바뀐다")
    return out


def newer(version: str, than: str) -> bool:
    """Whether `version` is above `than`, both read as four integers; an unreadable one is never above."""

    def parts(text: str) -> tuple[int, ...] | None:
        bits = text.split(".") if text else []
        if not 3 <= len(bits) <= 4 or not all(b.isascii() and b.isdigit() for b in bits):
            return None
        return tuple(map(int, bits)) + (0,) * (4 - len(bits))

    mine, theirs = parts(version), parts(than)
    return mine is not None and theirs is not None and mine > theirs
