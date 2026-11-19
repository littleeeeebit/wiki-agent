"""release_desktop — the Windows installer a release ships (`docs/plans/release/0-overview.md`, step 6).

The adapter's `[release.desktop]` command builds Tauri's NSIS installer with the version injected, as the Android
build is (`release.build`). The checks read the version resources Windows itself reads. The installer carries its
version and its product name, the name Tauri's NSIS script keys an upgrade on: the uninstall entry and the install
folder are both named after it. The app EXE beside it carries the same version and the publisher, as
`CompanyName`; the installer does not, since NSIS compresses the script that holds it. Nothing is signed: code
signing is not planned yet, so Windows warns before running the installer.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

import android_owned

from . import release

ARCH = "x64"   # the one Windows target the adapter's build makes
SUFFIX = f"-{ARCH}-setup.exe"   # a release's installer asset: `<repo>-<version>-x64-setup.exe`


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
        found = dll.VerQueryValueW(data, sub, ctypes.byref(ptr), ctypes.byref(n)) and n.value
        return (ptr.value, n.value) if found else None

    fixed = query("\\")
    if not fixed:
        raise ValueError(f"`{file.name}` 에 고정 버전이 없다")
    ms, ls = ctypes.cast(fixed[0], ctypes.POINTER(wintypes.DWORD * 4)).contents[2:4]
    parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
    out = {"version": ".".join(map(str, parts[:3] if parts[3] == 0 else parts))}
    pairs = query("\\VarFileInfo\\Translation")
    words = ctypes.cast(pairs[0], ctypes.POINTER(wintypes.WORD * (pairs[1] // 2))).contents if pairs else []
    for lang in [f"{words[i]:04x}{words[i + 1]:04x}" for i in range(0, len(words) - 1, 2)] or ["040904b0"]:
        for key in ("ProductName", "CompanyName", "FileVersion"):
            got = query(f"\\StringFileInfo\\{lang}\\{key}")
            if got and key not in out:
                out[key] = ctypes.wstring_at(got[0], got[1]).rstrip("\0")
    return out


def readable(installer: Path) -> None:
    """Raises `ValueError` unless `installer` has a version resource: the build's product, readable as one."""

    version_info(installer)


def facts(installer: Path, exe: Path | None = None) -> dict:
    """What the checks compare: the installer's version and product name, and, from the app EXE when there is
    one, its version, product name and publisher. A previous release's installer comes without its EXE."""

    shown = version_info(installer)
    out = {"version": shown["version"], "file_version": shown.get("FileVersion"),
           "product": shown.get("ProductName"), "sha256": android_owned.sha256(installer)}
    if exe is not None:
        app = version_info(exe)
        out.update(exe_version=app["version"], exe_product=app.get("ProductName"), publisher=app.get("CompanyName"))
    return out


def plain(text: str | None) -> str | None:
    """A version string as SemVer, `0.1.1.0` as `0.1.1`."""

    return text[:-2] if text and text.count(".") == 3 and text.endswith(".0") else text


def built_wrong(seen: dict, version: str, desktop: dict, previous: dict | None = None) -> list[str]:
    """Why this installer cannot be published, empty when it can: each is the build's, a source change fixes it.
    `previous` is the last production installer's facts, its publisher only when this server recorded it."""

    out = []
    for name, got in (("설치 파일 버전", seen["version"]), ("설치 파일 FileVersion", plain(seen["file_version"])),
                      ("앱 EXE 버전", seen.get("exe_version"))):
        if got != version:
            out.append(f"{name} {got!r} 가 버전 {version!r} 와 다르다")
    if previous and not _newer(version, previous["version"]):
        out.append(f"버전 {version} 이 이전 정식 설치 파일의 {previous['version']} 보다 크지 않다")
    wants = [("설정", desktop["product_name"]), *((("이전 정식 설치 파일", previous["product"]),) if previous else ())]
    for name, want in wants:
        for what, got in (("설치 파일", seen["product"]), ("앱 EXE", seen.get("exe_product"))):
            if got != want:
                out.append(f"{what} 제품 이름 {got!r} 가 {name}의 {want!r} 와 다르다 — Windows 는 다른 앱으로 설치한다")
    wants = [("설정", desktop["publisher"]),
             *((("이전 정식 설치 파일", previous["publisher"]),) if previous and previous.get("publisher") else ())]
    for name, want in wants:
        if seen.get("publisher") != want:
            out.append(f"게시자 {seen.get('publisher')!r} 가 {name}의 {want!r} 와 다르다 — 설치된 앱의 설정 위치가 바뀐다")
    return out


def _newer(version: str, than: str) -> bool:
    mine, theirs = release.parsed(f"v{version}"), release.parsed(f"v{than}")
    return bool(mine) and (theirs is None or mine > theirs)
