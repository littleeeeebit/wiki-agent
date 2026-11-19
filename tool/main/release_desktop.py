"""release_desktop — the Windows installer a release ships (`docs/plans/release/0-overview.md`, step 6).

The adapter's `[release.desktop]` command builds Tauri's NSIS installer with the version injected, as the Android
build is (`release.build`). The checks read what Windows and the installer itself will use. The installer's version
resource carries its version and its product name, the name Tauri's NSIS script keys an upgrade on: the uninstall
entry and the install folder are both named after it. Inside, under NSIS's CRC, one solid LZMA stream holds the
script and the files it installs (`payload`). The script's literal registry writes say what Windows will list:
the uninstall key named after the product, its display name, publisher, version and main binary, and the key
`Software\\<publisher>\\<product>` the next update finds the install folder by. The main binary is read from the
script's file entry of that name; its version resource carries the version, the product name and the publisher
(`CompanyName`). A build's installer must hold the very EXE the build made. Nothing is signed: code signing is
not planned yet, so Windows warns before running the installer.
"""

from __future__ import annotations

import ctypes
import hashlib
import lzma
import ntpath
import os
import struct
import tempfile
import zlib
from ctypes import wintypes
from pathlib import Path

ARCH = "x64"   # the one Windows target the adapter's build makes
SUFFIX = f"-{ARCH}-setup.exe"   # a release's installer asset: `<repo>-<version>-x64-setup.exe`
MAGIC = b"\xef\xbe\xad\xdeNullsoftInst"   # NSIS's first header: flags, this signature, the header's length
# Tauri sets the three bytes after it to `NSS` in the one app an NSIS installer installs; its other binaries,
# and the app as built, keep `UNK`.
MARKER, NSIS = b"__TAURI_BUNDLE_TYPE_VAR_", b"NSS"
FIXED_SIGNATURE, FIXED_SIZE = 0xFEEF04BD, 52   # VS_FIXEDFILEINFO
FH_SILENT = 2   # the one first-header flag allowed: an uninstaller, CRC off or forced are not Tauri's installer
EW_CREATEDIR, EW_SETFLAG, EW_EXTRACTFILE, EW_ASSIGNVAR, EW_PUSHPOP = 11, 13, 20, 25, 31   # NSIS 3's opcodes
EW_WRITEREG, EW_WRITEUNINSTALLER = 51, 62
OUTDIR = 22   # `$OUTDIR`'s variable index
# The flags whose value decides where `SHCTX` resolves: `SetShellVarContext` (0 current user, 1 all users) and
# `SetRegView` (256 the 64-bit view). Tauri's installMode sets the first once; "both" sets it at run time.
FLAG_CONTEXT, FLAG_VIEW = 1, 12
SCOPES = {"0": "currentUser", "1": "perMachine"}
SHCTX, REG_SZ = 0, (1, 1)   # a registry write's root and type as `WriteRegStr SHCTX` compiles them
INSTDIR = "\x03\u8095"   # `$INSTDIR`, variable 21, as Unicode NSIS 3 codes it: NS_VAR_CODE, CODE_SHORT(21)
UNINSTALL = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"   # + the product name, as Tauri's script names it
LIMITS = {"installer": 1 << 30, "dictionary": 64 << 20, "script": 16 << 20, "app": 1 << 30, "unpacked": 4 << 30}
CHUNK = 1 << 20   # compressed input fed, and decoded output taken, at a time


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


class _Stream:
    """The solid LZMA stream, decoded only as far as asked and never kept beyond what is asked for."""

    def __init__(self, packed: memoryview, dec: lzma.LZMADecompressor):
        self.packed, self.dec, self.fed, self.decoded = packed, dec, 0, 0

    def read(self, n: int, keep: bool = True) -> bytes:
        if self.decoded + n > LIMITS["unpacked"]:
            raise ValueError("압축을 푼 크기가 한도를 넘는다")
        parts, left = [], n
        while left:
            feed = b""
            if self.dec.needs_input or self.dec.eof:
                feed = self.packed[self.fed:self.fed + CHUNK]
                self.fed += len(feed)
                if not feed or self.dec.eof:
                    raise ValueError("압축 데이터가 기록이 끝나기 전에 끝난다")
            got = self.dec.decompress(feed, max_length=min(left, CHUNK))
            left -= len(got)
            if keep:
                parts.append(got)
        self.decoded += n
        return b"".join(parts)


def _header(data: bytes) -> tuple[int, int, int]:
    """Where the NSIS first header is, the script's length and where the data it declares ends. NSIS looks for
    it on a 512-byte boundary and checks a CRC32 of every byte from 512 up to the CRC that ends that data."""

    at = data.find(MAGIC)
    while at >= 0 and (at - 4) % 512:
        at = data.find(MAGIC, at + 1)
    if at < 4 or len(data) < at + 24:
        raise ValueError("NSIS 설치 파일이 아니다")
    flags, script, total = struct.unpack_from("<I", data, at - 4)[0], *struct.unpack_from("<II", data, at + 16)
    at -= 4
    if flags & ~FH_SILENT or not 33 + 4 <= total <= len(data) - at:
        raise ValueError(f"NSIS 머리말이 지원하지 않는 형태다 (flags {flags:#x}) — 제거기이거나, CRC 검사가 꺼졌거나, 잘렸다")
    if zlib.crc32(memoryview(data)[512:at + total - 4]) != struct.unpack_from("<I", data, at + total - 4)[0]:
        raise ValueError("NSIS CRC 가 맞지 않는다 — NSIS 스스로 실행을 거부할 설치 파일이다")
    return at, script, at + total - 4


def _string(script: bytes, block: tuple[int, int], ptr: int) -> str | None:
    """The NSIS string at `ptr`, as Unicode NSIS 3 stores it: UTF-16 inside the string block, ending at a NUL,
    variables kept as their codes (`INSTDIR`). None for a language string or anything malformed."""

    start, end = block
    at = start + 2 * ptr
    if ptr < 0 or at >= end:
        return None
    stop = at
    while stop + 2 <= end and script[stop:stop + 2] != b"\0\0":
        stop += 2
    if stop + 2 > end:
        return None
    try:
        return script[at:stop].decode("utf-16-le")
    except UnicodeDecodeError:
        return None


def payload(data: bytes) -> tuple[list[tuple], dict[str, set[int]], dict[int, bool], dict[int, set], _Stream]:
    """An NSIS installer packed as Tauri's default is: Unicode NSIS 3, one solid LZMA stream, its CRC on. Gives
    the script's registry writes as (root, key, name, value, type); each extracted file's data offsets by its
    destination as Windows compares it (`destination`); every offset a file or uninstaller
    entry reads, true where the record after it is read too; the values each flag is set to; and the stream
    positioned at the first file. Any other shape is refused: what it would install could not be read, so it
    could not be checked."""

    at, length, end = _header(data)
    props = data[at + 28:at + 33]
    dictionary = struct.unpack("<I", props[1:])[0]
    if props[0] >= 225 or dictionary > LIMITS["dictionary"] or length > LIMITS["script"]:
        raise ValueError("Tauri 기본 압축(solid LZMA)이 아니거나 한도를 넘는다 — 담긴 앱을 읽을 수 없다")
    lc, lp, pb = props[0] % 9, props[0] // 9 % 5, props[0] // 45
    try:
        stream = _Stream(memoryview(data)[at + 33:end], lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[
            {"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dictionary}]))
        if struct.unpack("<I", stream.read(4))[0] != length:
            raise ValueError("스크립트 길이가 머리말과 다르다")
        script = stream.read(length)
    except lzma.LZMAError as exc:
        raise ValueError("Tauri 기본 압축(solid LZMA)이 아니다 — 담긴 앱을 읽을 수 없다") from exc
    if length < 68:
        raise ValueError("NSIS 스크립트가 너무 짧다")
    entries, count = struct.unpack_from("<II", script, 4 + 8 * 2)
    strings, languages = struct.unpack_from("<I", script, 4 + 8 * 3)[0], struct.unpack_from("<I", script, 4 + 8 * 4)[0]
    block = (strings, languages if strings < languages <= length else length)
    if entries + 28 * count > length or strings >= length:
        raise ValueError("NSIS 명령 표나 문자열 표가 스크립트 밖으로 나간다")
    writes, files, offsets, flags, out = [], {}, {}, {}, None
    for k in range(count):   # in the order the script lays them out. Calls and jumps run code in another
        # order, so the output directory is held to one value: every `SetOutPath`, and every write of
        # `$OUTDIR`, must be `$INSTDIR`, as in Tauri's script, and a relative name before the first is refused
        which, *arg = struct.unpack_from("<i6i", script, entries + 28 * k)
        if which == EW_WRITEREG:
            writes.append((arg[0], *(_string(script, block, a) for a in arg[1:4]), (arg[4], arg[5])))
        elif which == EW_CREATEDIR and arg[1]:   # `SetOutPath`: the directory, made and made current
            if (out := _string(script, block, arg[0])) != INSTDIR:
                raise ValueError("설치 스크립트가 출력 위치를 $INSTDIR 밖으로 옮긴다 — 주 실행 파일이 놓일 곳을 확인할 수 없다")
        elif which in (EW_ASSIGNVAR, EW_PUSHPOP) and arg[0] == OUTDIR and (which == EW_ASSIGNVAR or arg[1]):
            raise ValueError("설치 스크립트가 $OUTDIR 을 바꾼다 — 주 실행 파일이 놓일 곳을 확인할 수 없다")
        elif which == EW_EXTRACTFILE:
            offsets[arg[2]] = offsets.get(arg[2], False)
            if (where := destination(out, _string(script, block, arg[1]))) is None:
                raise ValueError("설치 파일이 풀어 놓는 파일의 위치를 정할 수 없다")
            files.setdefault(where, set()).add(arg[2])
        elif which == EW_WRITEUNINSTALLER:   # its icon record, then the record after it: the uninstaller,
            offsets[arg[1]] = True            # written where its name lands, over any file already there
            files.setdefault(destination(out, _string(script, block, arg[0])), set()).add(None)
        elif which == EW_SETFLAG:   # a third operand restores a saved value, which no literal names
            flags.setdefault(arg[0], set()).add(None if arg[2] else _string(script, block, arg[1]))
    return writes, files, offsets, flags, stream


def upper(text: str) -> str:
    """`text` as Windows compares file names and registry keys: each UTF-16 unit through the system's own
    uppercase table (`RtlUpcaseUnicodeChar`, what `CompareStringOrdinal` ignores case with), so `ß`, `ı` and `ſ`
    stay as they are. Off Windows, Python's one-to-one uppercase stands in."""

    if os.name != "nt":
        return "".join(c.upper() if len(c.upper()) == 1 else c for c in text)
    if not _UPCASE:
        call = ctypes.WinDLL("ntdll").RtlUpcaseUnicodeChar
        call.argtypes, call.restype = [ctypes.c_ushort], ctypes.c_ushort
        _UPCASE.append(call)
    units = struct.iter_unpack("<H", text.encode("utf-16-le", "surrogatepass"))
    return b"".join(struct.pack("<H", _UPCASE[0](u)) for (u,) in units).decode("utf-16-le", "surrogatepass")


_UPCASE: list = []   # `RtlUpcaseUnicodeChar`, loaded on first use


def destination(out: str | None, name: str | None) -> str | None:
    """Where an extracted file lands, as Windows compares paths: a relative name under the current output
    directory, separators and `..` resolved, trailing dots and spaces dropped, each letter upper-cased one to one
    as Windows compares file names. A variable keeps its
    code, so `$INSTDIR\\r.exe` stays apart from `$PLUGINSDIR\\r.exe`. None when it cannot be told."""

    absolute = bool(name) and (name[0] < " " or name.startswith("\\") or name[1:2] == ":")   # a variable, a root
    if not name or (not absolute and out is None):
        return None
    full = name if absolute else f"{out}\\{name}"
    return upper("\\".join(part.rstrip(". ") for part in ntpath.normpath(full).split("\\")))


def _registered(writes: list, product: str | None) -> dict:
    """What the script registers under the uninstall key named after `product`. Windows reads registry keys and
    value names without case, so neither does this. A value counts only when every write of that name agrees
    and is a string in `SHCTX`, where Tauri's next update reads it."""

    seen = {}
    for root, key, name, value, kind in writes:
        if key is not None and name is not None:
            seen.setdefault((upper(key), upper(name)), set()).add((root, kind, value))

    def value(key: str, name: str) -> str | None:
        found = seen.get((upper(key), upper(name)), set())
        return next(v for r, k, v in found) if len(found) == 1 and next(iter(found))[:2] == (SHCTX, REG_SZ) else None

    uninstall = UNINSTALL + (product or "")
    reg = {name: value(uninstall, name) for name in ("DisplayName", "Publisher", "DisplayVersion", "MainBinaryName")}
    reg["install_key"] = value(f"Software\\{reg['Publisher']}\\{product}", "") == INSTDIR
    return reg


def _records(stream: _Stream, offsets: dict[int, bool], main: int) -> bytes:
    """Every record the script reads, each checked to lie whole in the stream, the uninstaller's two included;
    only the main binary's kept. A record that starts inside another, or is not solid, is refused."""

    if not offsets or min(offsets) < 0:
        raise ValueError("파일 기록 위치가 잘못됐다")
    base, kept = stream.decoded, b""   # offsets count from the first record, after the script

    def record(keep: bool) -> bytes:
        size = struct.unpack("<I", stream.read(4))[0]
        if size & 0x80000000 or size > LIMITS["app"]:
            raise ValueError("파일 기록이 지원하지 않는 형태이거나 한도를 넘는다")
        return stream.read(size, keep=keep)

    for offset in sorted(offsets):
        if offset < stream.decoded - base:
            raise ValueError("파일 기록이 다른 기록 안에서 시작한다")
        stream.read(offset - (stream.decoded - base), keep=False)
        body = record(offset == main)
        kept = body if offset == main else kept
        if offsets[offset]:
            record(False)
    return kept


def _bounded(file: Path, limit: int) -> bytes:
    """`file`'s bytes, refused before they are read when there are more than `limit` of them."""

    with file.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > limit or len(data := handle.read(size)) != size or handle.read(1):
            raise ValueError(f"`{file.name}` 가 크기 한도({limit >> 20} MiB)를 넘는다")
    return data


def readable(installer: Path) -> None:
    """Raises `ValueError` unless `installer` is an NSIS installer this module can read."""

    payload(_bounded(installer, LIMITS["installer"]))


def facts(installer: Path, exe: Path | None = None) -> dict:
    """What the checks compare, all from one read of `installer`: its version resource, what its script
    registers, and the main binary that registration names, read from inside it. Given the build's `exe`, that
    binary must be it, as Tauri packs it: the first bundle-type placeholder set to NSIS."""

    data = _bounded(installer, LIMITS["installer"])   # one snapshot: the checks, the digest and the manifest
    writes, files, offsets, flags, stream = payload(data)
    with tempfile.TemporaryDirectory() as tmp:   # Windows reads a version resource from a file only
        (copy := Path(tmp) / "setup.exe").write_bytes(data)
        shown = version_info(copy)
        reg = _registered(writes, shown.get("ProductName"))
        name = reg["MainBinaryName"]
        main = files.get(destination(INSTDIR, name), set()) if name and not any(c in name for c in "\\/:") else set()
        if len(main) != 1 or None in main:   # one record lands at `$INSTDIR\\<MainBinaryName>`, and nothing
            # else, the uninstaller included, writes there after it
            raise ValueError(f"`{installer.name}` 의 스크립트가 등록하는 주 실행 파일 {reg['MainBinaryName']!r} 를 "
                             "하나로 찾을 수 없다")
        try:
            app = _records(stream, offsets, next(iter(main)))
        except lzma.LZMAError as exc:
            raise ValueError(f"`{installer.name}` 의 압축 데이터가 깨졌다") from exc
        if exe is not None and app != _bounded(exe, LIMITS["app"]).replace(MARKER + b"UNK", MARKER + NSIS, 1):
            raise ValueError(f"`{installer.name}` 에 든 {reg['MainBinaryName']!r} 가 빌드가 만든 앱 EXE 와 다르다")
        (copy := Path(tmp) / "app.exe").write_bytes(app)
        shown_app = version_info(copy)
    return {"version": shown["version"], "file_version": shown.get("FileVersion"), "product": shown.get("ProductName"),
            "exe_version": shown_app["version"], "exe_product": shown_app.get("ProductName"),
            "publisher": shown_app.get("CompanyName"), "reg_product": reg["DisplayName"],
            "reg_publisher": reg["Publisher"], "reg_version": reg["DisplayVersion"],
            "install_key": reg["install_key"], "scope": _one(flags.get(FLAG_CONTEXT), SCOPES),
            "view": _one(flags.get(FLAG_VIEW, {"0"}), None), "sha256": hashlib.sha256(data).hexdigest()}


def _one(values: set | None, names: dict | None) -> str | None:
    """The value a flag is always set to, by its name when `names` has one; None when the script leaves it
    open: never set, set to more than one value, or to a value no literal names."""

    if not values or len(values) != 1 or (value := next(iter(values))) is None:
        return None
    return value if names is None else names.get(value)


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
    for what, key, want in (("제품 이름", "reg_product", seen["product"]),
                            ("게시자", "reg_publisher", seen.get("publisher")), ("버전", "reg_version", version)):
        if seen.get(key) != want:
            out.append(f"설치 스크립트가 등록하는 {what} {seen.get(key)!r} 가 {want!r} 와 다르다 — Windows 의 설치 목록이 어긋난다")
    for what, key in (("설치 범위(사용자별 또는 컴퓨터 전체)", "scope"), ("레지스트리 보기", "view")):
        if seen.get(key) is None:
            out.append(f"설치 스크립트의 {what}를 하나로 정할 수 없다 — 업데이트가 이전 설치를 찾을 곳이 정해지지 않는다")
        elif previous and seen[key] != previous.get(key):
            out.append(f"{what} {seen[key]!r} 가 이전 정식 설치 파일의 {previous.get(key)!r} 와 다르다 — "
                       "업데이트가 이전 설치를 찾지 못하고 따로 설치된다")
    if not seen.get("install_key"):
        out.append("설치 스크립트가 설치 위치를 Software\\<게시자>\\<제품 이름> 에 기록하지 않는다 — 다음 업데이트가 설치 위치를 "
                   "찾지 못한다")
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


def carries(release: str) -> None:
    """Refuses a version Windows' version resources cannot hold: each of their parts is 16 bits."""

    if max(map(int, release.split("."))) > 0xFFFF:
        raise ValueError(f"버전 {release} 는 Windows 버전 리소스로 옮길 수 없다 — 각 자리는 65535 까지다")


def newer(version: str, than: str) -> bool:
    """Whether `version` is above `than`, both read as four integers; an unreadable one is never above."""

    def parts(text: str) -> tuple[int, ...] | None:
        bits = text.split(".") if text else []
        if not 3 <= len(bits) <= 4 or not all(b.isascii() and b.isdigit() for b in bits):
            return None
        return tuple(map(int, bits)) + (0,) * (4 - len(bits))

    mine, theirs = parts(version), parts(than)
    return mine is not None and theirs is not None and mine > theirs
