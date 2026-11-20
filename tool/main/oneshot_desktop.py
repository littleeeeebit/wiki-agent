"""oneshot S7's PC app build: the frontend build's `oneshot-desktop` block, the program its gate built, and that
program frozen into staging the run owns, with the identity every look launches.

`docs/plans/oneshot/9-kinds.md` ("The runnable program") is the design. `declared` reads the block's shape before
the gate; `built` checks the paths the gate left; `frozen` copies the build folder out of the worktree in one walk,
checks the copy (a Windows program, no packaged layout, no manifest asking for elevation), records each copied
file's digest and the launch settings, and names the build by one digest over both. Any change to a file, an
argument, the working directory or a data setting is a new identity, so a look's pass counts only for the build
it launched.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
from pathlib import Path
from xml.etree import ElementTree

from common import is_junction

if os.name == "nt":   # a PC app is built and checked on Windows; the server imports this module everywhere
    import msvcrt
    from ctypes import wintypes as w

from . import oneshot
from .oneshot_screens import DEVICE_NAMES, MOCK_PATH

BLOCK = "oneshot-desktop"
DATA = "{data}"   # where a look puts the app's data: a fresh folder it owns, per attempt
INSTALLERS = (".exe", ".msi", ".msix", ".msixbundle")
ELEVATED = ("requireAdministrator", "highestAvailable")   # a manifest's levels above the caller's own rights
MAX_FILES, MAX_BYTES = 5000, 1 << 30   # a folder of what the launch loads, never a whole build tree
MANIFEST_BYTES = 1 << 20
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# A setting the look passes names the copy by a path relative to `run.cwd`, `{data}`, or a remote URL, and never
# another place on this PC: a drive, a file URI, a path from a drive's root, a `..` step or a `%VAR%` the program
# could expand. A path into the live worktree then has no spelling left to take, however Windows or a web view would
# read it. A `//` or `\\` is a UNC or device path unless it is the `//` after a URL's scheme.
OUTSIDE = re.compile(r"(?i)file:|(?<![a-z0-9])[a-z]:|(?:^|[\s\"';,|=])[\\/]"
                     r"|(?:^|[\\/\s\"';,|=])\.\.(?:[\\/\s\"';,|]|$)|%[a-z_][a-z0-9_]*%")
DOUBLE = re.compile(r"[\\/]{2}")
SCHEME = re.compile(r"(?i)[a-z][a-z0-9+.-]+:$")


def _relative(value, where: str) -> str:
    """A path written with `/`, below its base and naming no Windows device or a component Windows trims."""

    if not isinstance(value, str) or not MOCK_PATH.fullmatch(value) \
            or {".", "..", ".git"} & {part.lower() for part in value.split("/")} \
            or any(part.endswith(".") or part.split(".")[0].upper() in DEVICE_NAMES for part in value.split("/")):
        raise ValueError(f"`{BLOCK}` 의 {where} 는 상대 경로(`/` 로 나눔, `.`·`..`·장치 이름·끝의 `.` 없음)로 적는다 "
                         f"— {value!r}")
    return value


def _reaches_out(setting: str) -> bool:
    """Whether a setting may name a place on this PC other than the copy and `{data}` (`OUTSIDE`)."""

    s = setting.replace(DATA, "data")
    return bool(OUTSIDE.search(s)) or any(m.group() != "//" or not SCHEME.search(s[:m.start()])
                                          for m in DOUBLE.finditer(s))


def declared(tree: Path, final: str) -> dict:
    """The build turn's `oneshot-desktop` block, checked for shape: `run`'s build folder, its program inside it,
    the arguments, the working directory inside that folder, the environment, with `{data}` named at least once
    and no setting that could name a place outside the copy (`OUTSIDE`); and the installer S8 attaches. Whether the files exist is `built`'s,
    after the gate."""

    value = oneshot.block(final, BLOCK)
    if not isinstance(value, dict) or not isinstance(value.get("run"), dict):
        raise ValueError(f"`{BLOCK}` 는 run{{folder, program, args, cwd, env}} 과 installer 를 담은 객체다")
    run = value["run"]
    out = {"folder": _relative(run.get("folder"), "run.folder"), "program": _relative(run.get("program"), "run.program"),
           "args": run.get("args", []), "cwd": run.get("cwd", "."), "env": run.get("env", {})}
    if out["cwd"] != ".":
        _relative(out["cwd"], "run.cwd")
    if Path(out["program"]).suffix.lower() != ".exe":
        raise ValueError(f"`{BLOCK}` 의 run.program 은 패키지하지 않은 디버그 빌드의 `.exe` 다 — MSIX 나 설치 파일은 "
                         "띄우지 않는다")
    if not isinstance(out["args"], list) or not all(isinstance(a, str) for a in out["args"]):
        raise ValueError(f"`{BLOCK}` 의 run.args 는 문자열 목록이다")
    if not isinstance(out["env"], dict) or not all(isinstance(k, str) and ENV_NAME.fullmatch(k) and isinstance(v, str)
                                                    for k, v in out["env"].items()):
        raise ValueError(f"`{BLOCK}` 의 run.env 는 환경 변수 이름과 문자열 값의 객체다")
    settings = [*out["args"], *out["env"].values()]
    if not any(DATA in s for s in settings):
        raise ValueError(f"`{BLOCK}` 의 run 은 앱의 데이터를 `{DATA}` 에 두는 인자나 환경 변수를 하나 이상 적는다")
    if outside := [s for s in settings if _reaches_out(s)]:
        raise ValueError(f"`{BLOCK}` 의 run 설정 `{outside[0]}` 은 사본 밖을 가리킬 수 있다 — 실행은 얼린 사본과 "
                         f"`{DATA}` 만 읽는다. 드라이브·UNC·파일 URI·루트부터의 경로·`..`·`%변수%` 없이, run.cwd 기준 "
                         f"상대 경로나 `{DATA}` 나 원격 URL 로 적는다")
    installer = _relative(value.get("installer"), "installer")
    if Path(installer).suffix.lower() not in INSTALLERS:
        raise ValueError(f"`{BLOCK}` 의 installer 는 S8 이 붙일 설치 파일(.exe, .msi, .msix)이다 — {installer}")
    if installer.casefold() == f"{out['folder']}/{out['program']}".casefold():   # one file to Windows
        raise ValueError(f"`{BLOCK}` 의 installer 와 run.program 이 같은 파일이다 — run 은 설치하지 않고 띄우는 빌드다")
    return {"run": out, "installer": installer}


def _inside(tree: Path, relative: str) -> Path | None:
    """`relative` under `tree` when no step of it is a link and it resolves inside `tree`, else `None`."""

    parts = Path(relative).parts
    steps = [tree.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
    if any(p.is_symlink() or is_junction(p) for p in steps) or not (tree / relative).resolve().is_relative_to(
            tree.resolve()):
        return None
    return tree / relative


def built(tree: Path, desktop: dict) -> str:
    """Why the paths `run` names are not what the gate left, or empty: its folder and program are plain files
    inside the worktree, reached through no link, and the installer, once built, is not the program. The copy
    `frozen` makes is checked again, and is what the look starts."""

    run = desktop["run"]
    folder = _inside(tree, run["folder"])
    if folder is None or not folder.is_dir():
        return f"게이트 뒤에 `{run['folder']}` 가 작업트리 안의 폴더로 없다 — `gate_cmd` 가 그 빌드를 만들어야 한다"
    program = _inside(folder, run["program"])
    if program is None or not program.is_file():
        return f"게이트 뒤에 `{run['folder']}/{run['program']}` 가 그 폴더 안의 파일로 없다"
    installer = tree / desktop["installer"]
    try:
        if installer.exists() and os.path.samefile(installer, program):
            return "installer 와 run.program 이 같은 파일이다 — run 은 설치하지 않고 띄우는 빌드다"
    except OSError:
        pass
    return ""


def _final(handle: int) -> str:
    """The path Windows resolved an open handle to, every link and junction on the way followed."""

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetFinalPathNameByHandleW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
    buffer = ctypes.create_unicode_buffer(32768)
    n = k.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not n or n >= len(buffer):
        raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW")
    path = buffer.value
    return "\\\\" + path[8:] if path.startswith("\\\\?\\UNC\\") else path.removeprefix("\\\\?\\")


def _folder(path: Path) -> str:
    """A folder's resolved path, read off a handle opened on the folder itself."""

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    # No access asked, every share allowed, an existing folder (FILE_FLAG_BACKUP_SEMANTICS opens one).
    handle = k.CreateFileW(str(path), 0, 0x7, None, 3, 0x02000000, None)
    if handle in (None, w.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "CreateFileW", str(path))
    try:
        return _final(handle)
    finally:
        k.CloseHandle(handle)


def _copied(tree: Path, label: str, app: Path) -> list[str]:
    """Every regular file under `tree/label` copied into `app` in one walk, the bounds counted as the bytes are
    copied. Each entry is checked before it is entered or read, and each file is read only after its open
    handle resolves inside that folder of the worktree: a folder renamed or turned into a junction meanwhile can
    change which names are walked, never where the bytes come from. A file that appears after its folder was
    walked is neither copied nor named. The copied files' paths."""

    source = tree / label
    # The folder's own path as Windows resolves its handle, the case of each name as stored: a file's resolved
    # path must sit under it exactly, so a case-sensitive folder's `DIST` is never taken for its `dist`.
    # The worktree's resolved path, then `run.folder` exactly as declared: a folder whose stored name differs even
    # in case is not the one declared, so no case-sensitive sibling can stand in for it.
    root = _folder(source)
    if root != os.path.join(os.path.realpath(tree), *label.split("/")):
        raise ValueError(f"`{label}` 가 작업트리 안의 그 이름 그대로의 폴더로 열리지 않는다 — `{root}` (run.folder 는 "
                         "디스크에 있는 대소문자 그대로 적는다)")
    inside = root.rstrip("\\") + "\\"
    names, size, folders = [], 0, [(source, app)]
    app.mkdir(parents=True)
    while folders:
        here, there = folders.pop()
        for path in sorted(here.iterdir()):
            name = path.relative_to(source).as_posix()
            if path.is_symlink() or is_junction(path):
                raise ValueError(f"`{label}` 에 링크가 있다 — `{name}`")
            if path.is_dir():
                (there / path.name).mkdir()
                folders.append((path, there / path.name))
                continue
            if not path.is_file():
                raise ValueError(f"`{label}` 에 보통 파일이 아닌 것이 있다 — `{name}`")
            names.append(name)
            if len(names) > MAX_FILES:
                raise ValueError(_too_big(label))
            with open(path, "rb") as read:
                if not _final(msvcrt.get_osfhandle(read.fileno())).startswith(inside):
                    raise ValueError(f"`{label}` 의 `{name}` 이 작업트리의 그 폴더 밖 파일로 열렸다")
                with open(there / path.name, "xb") as written:
                    while chunk := read.read(1 << 20):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError(_too_big(label))
                        written.write(chunk)
    return sorted(names)


def _too_big(label: str) -> str:
    return (f"`{label}` 가 실행이 읽는 것보다 크다 (파일 {MAX_FILES}개, {MAX_BYTES >> 20} MB 까지) — 실행에 필요한 "
            "파일만 모은 폴더를 `gate_cmd` 가 만든다")


def _manifests(program: Path) -> list[bytes]:
    """The application manifests embedded in `program` (`RT_MANIFEST`, ids 1 to 16), read as Windows maps the
    file for its resources. A file Windows cannot map so is not a Windows program."""

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.LoadLibraryExW.argtypes, k.LoadLibraryExW.restype = [w.LPCWSTR, w.HANDLE, w.DWORD], w.HMODULE
    k.FindResourceW.argtypes, k.FindResourceW.restype = [w.HMODULE, ctypes.c_void_p, ctypes.c_void_p], w.HANDLE
    k.SizeofResource.argtypes, k.SizeofResource.restype = [w.HMODULE, w.HANDLE], w.DWORD
    k.LoadResource.argtypes, k.LoadResource.restype = [w.HMODULE, w.HANDLE], w.HANDLE
    k.LockResource.argtypes, k.LockResource.restype = [w.HANDLE], ctypes.c_void_p
    k.FreeLibrary.argtypes = [w.HMODULE]
    module = k.LoadLibraryExW(str(program), None, 0x2 | 0x20)   # as a data file, as an image resource
    if not module:
        raise ValueError(f"`{program.name}` 는 Windows 프로그램이 아니다 — Windows error {ctypes.get_last_error()}")
    try:
        found = []
        for number in range(1, 17):
            if not (resource := k.FindResourceW(module, number, 24)):
                continue
            size = k.SizeofResource(module, resource)
            if size > MANIFEST_BYTES or not (data := k.LockResource(k.LoadResource(module, resource))):
                raise ValueError(f"`{program.name}` 의 매니페스트를 읽지 못했다")
            found.append(ctypes.string_at(data, size))
        return found
    finally:
        k.FreeLibrary(module)


def _levels(manifest: bytes, name: str) -> list[str]:
    """Every `requestedExecutionLevel` a manifest declares, read as XML in whatever encoding it declares."""

    if b"<!DOCTYPE" in manifest.upper() or "<!DOCTYPE".encode("utf-16-le") in manifest:
        raise ValueError(f"`{name}` 의 매니페스트에 DOCTYPE 이 있다")
    try:
        root = ElementTree.fromstring(manifest)
    except (ElementTree.ParseError, LookupError, UnicodeError) as exc:   # malformed, or an encoding unknown here
        raise ValueError(f"`{name}` 의 매니페스트를 XML 로 읽지 못했다 — {exc}") from exc
    return [e.get("level", "") for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "requestedExecutionLevel"]


def checked(app: Path, run: dict, label: str) -> str:
    """Why the copy the look would start is not an unpackaged, non-elevated Windows program, or empty."""

    program = app / run["program"]
    if not program.is_file():
        return f"`{label}/{run['program']}` 가 그 폴더 안의 파일로 없다"
    if any((app / folder / "AppxManifest.xml").exists() for folder in program.relative_to(app).parents):
        return f"`{label}` 는 MSIX 로 패키지한 배치다 — run 은 패키지하지 않은 디버그 빌드다"
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetBinaryTypeW.argtypes, kind = [w.LPCWSTR, ctypes.POINTER(w.DWORD)], w.DWORD()
    if not k.GetBinaryTypeW(str(program), ctypes.byref(kind)) or kind.value not in (0, 6):   # 32- or 64-bit
        return f"`{run['program']}` 는 Windows 가 프로세스로 띄우는 32·64비트 프로그램이 아니다"
    if run["cwd"] != "." and not (app / run["cwd"]).is_dir():
        return f"run.cwd `{run['cwd']}` 가 `{label}` 안의 폴더로 없다"
    sidecar = program.with_name(program.name + ".manifest")
    manifests = _manifests(program)
    if sidecar.is_file():
        if sidecar.stat().st_size > MANIFEST_BYTES:
            return f"`{sidecar.name}` 가 매니페스트로 읽기엔 크다"
        manifests.append(sidecar.read_bytes())
    if any(level in ELEVATED for m in manifests for level in _levels(m, program.name)):
        return f"`{run['program']}` 의 매니페스트가 관리자 권한을 요구한다 — run 은 권한 상승 없이 뜨는 빌드다"
    return ""


def frozen(tree: Path, desktop: dict, into: Path) -> dict:
    """`run`'s build folder copied into `into`, a folder the run owns, with `manifest.json` beside the copy: each
    copied file's digest and the launch settings, the program and working directory as paths inside the copy.
    The build's identity is one digest over both. A folder holding a link, or more than a launch loads, is
    refused, and so is a copy that is not a program the look may start (`checked`)."""

    run = desktop["run"]
    app = into / "app"
    names = _copied(tree, run["folder"], app)
    if sorted(p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file()) != names:
        raise ValueError(f"`{run['folder']}` 의 사본이 복사한 파일과 다르다")
    if why := checked(app, run, run["folder"]):
        raise ValueError(why)
    files = {name: hashlib.sha256((app / name).read_bytes()).hexdigest() for name in names}
    launch = {"program": run["program"], "args": run["args"], "cwd": run["cwd"], "env": run["env"]}
    identity = hashlib.sha256(json.dumps({"files": files, "launch": launch}, sort_keys=True).encode()).hexdigest()
    (into / "manifest.json").write_text(json.dumps({"identity": identity, "files": files, "launch": launch},
                                                   indent=1, sort_keys=True), encoding="utf-8")
    return {"identity": identity, "folder": str(app), "files": len(files), "launch": launch,
            "installer": desktop["installer"]}
