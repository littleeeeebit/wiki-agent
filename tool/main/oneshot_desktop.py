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

import contextlib
import ctypes
import hashlib
import ipaddress
import json
import os
import re
import unicodedata
import urllib.parse
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
STEP_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")   # a check's step: it names the driver's operations and states
MAX_STEPS = 20
WINDOWS = ("main", "owned")   # where a step acts and its postcondition reads: the main window or the one it owns
ACT = ("action", "id", "value", "in")   # what a step does, apart from its name and postcondition
OBSERVES = ("report", "window", "composition")   # what an `owner` check observes (plan 9e5)
REPORTS_FROM = ("fixture", "screen")   # what the app captures for an `owner` check: the fixture's window, or a surface
MAX_SHOWS = 500   # characters in the sentence the critic judges a capture against
# A setting the look passes is one of two things, so a path to another place on this PC has no spelling left to
# take, however Windows, a C runtime, a web view or the app's own expansion would read it:
# - a whole remote URL with a host, an optional `name=` before it, in printable ASCII but `,` and `;`, so no list
#   hides a second field after it;
# - a value of letters, digits, spaces and `_-.,;=:+/` alone (no `%`, `\`, `$`, `~`, `@`, `#`, `?`, quote, brace or
#   control character: nothing expands, escapes, decodes, names a response file or ends a string), naming the
#   copy by a path relative to `run.cwd`, or `{data}`: no file URI, no drive, no path from a root, no `//`, no `..`
#   anywhere and no Windows device name.
# A URL holds no `,`, `;` or `\`: a browser reads `\` as `/`, so the host it sees may not be the one checked.
REMOTE = re.compile(r"(?i)(?:[a-z0-9_.-]+=)?((?:https?|wss?)://[!-+\--:<-\[\]-~]+)")
# The WHATWG URL standard's forbidden domain code points: a host holding one is no host a browser opens.
FORBIDDEN_HOST = re.compile(r"[\x00-\x20#%/:<>?@\[\\\]^|\x7f]")
PLAIN = set(" _-.,;=:+/")
# A `:` before anything but a space opens a namespace (`file:`, `shell:`, a drive), and a drive alone is one too.
OUTSIDE = re.compile(r"(?i):(?=\S)|(?<![a-z0-9])[a-z]:|(?:^|[\s;,=:])/|//|\.\.")
OWNED_ENV = {"APPDATA", "LOCALAPPDATA", "TEMP", "TMP"}   # the look sets these to its own folders, whatever run says
LOOK_ENV = {"ONESHOT_SCREEN", "ONESHOT_STATE"}   # the look names the screen and state to open, in any case


def _remote(setting: str) -> bool:
    """Whether a setting is a whole remote URL a browser's parser accepts: a host, and a port in range. A host
    whose last label is a number is an IP address to that parser, so it must be one (`999.999.999.999` is not),
    and a bracketed host must be an IPv6 address. A host holding `%` is refused: the parser decodes it before it
    classifies the host, and an IPv6 zone is no host it accepts."""

    if not (match := REMOTE.fullmatch(setting)):
        return False
    try:
        url = urllib.parse.urlsplit(match.group(1))
        host = url.hostname or ""
        if "%" in host or (url.port is not None and url.port == 0):
            return False
        bare = host[:-1] if host.endswith(".") else host   # the parser drops one empty last label
        if ":" in host:
            ipaddress.IPv6Address(host)
        elif FORBIDDEN_HOST.search(host) or not all(map(_punycode, bare.split("."))):   # no domain a parser takes
            return False
        elif re.fullmatch(r"(?i)\d+|0x[0-9a-f]*", bare.rsplit(".", 1)[-1]):
            ipaddress.IPv4Address(bare)   # dotted decimal only: the hex, octal and short forms are refused
        return bool(host)
    except ValueError:   # a port out of range, a bracket left open, a number that is no address
        return False


def _punycode(label: str) -> bool:
    """A label that is not `xn--`, or a valid IDNA A-label: its Punycode decodes, the decoded label passes
    IDNA 2008 (no leading combining mark, joiners only in context) and encodes back to it. `idna` comes with
    FastAPI (starlette, anyio). ponytail: IDNA 2008 is stricter than a browser's UTS 46 for symbols such as emoji,
    which this refuses."""

    import idna

    if not label.startswith("xn--"):
        return True
    try:
        return idna.encode(idna.decode(label)).decode("ascii") == label
    except (idna.IDNAError, UnicodeError):
        return False


PARTS = re.compile(r"[/\s;,=:]+")
# `myapp::ipc=trace`: a module in Rust's log filter, read only from `RUST_LOG` and never as a path.
LOG_FILTER, RUST_PATH = "RUST_LOG", re.compile(r"(?<=\w)::(?=[^\W\d])")   # Rust identifiers may be Unicode


def _relative(value, where: str) -> str:
    """A path written with `/`, below its base and naming no Windows device or a component Windows trims."""

    if not isinstance(value, str) or not MOCK_PATH.fullmatch(value) \
            or {".", "..", ".git"} & {part.lower() for part in value.split("/")} \
            or any(part.endswith(".") or part.split(".")[0].upper() in DEVICE_NAMES for part in value.split("/")):
        raise ValueError(f"`{BLOCK}` 의 {where} 는 상대 경로(`/` 로 나눔, `.`·`..`·장치 이름·끝의 `.` 없음)로 적는다 "
                         f"— {value!r}")
    return value


def _reaches_out(setting: str, name: str = "") -> bool:
    """Whether a setting may name a place on this PC other than the copy and `{data}` (`OUTSIDE`). `name` is the
    variable a value is for, if any: `RUST_LOG`'s may name modules with `::`."""

    if _remote(setting):
        return False
    s = setting.replace(DATA, "data")
    if name.upper() == LOG_FILTER:   # a combining mark continues a Rust identifier and names no path
        s = RUST_PATH.sub("__", "".join(c for c in s if not unicodedata.category(c).startswith("M")))
    return not all(c.isalnum() or c in PLAIN for c in s) or bool(OUTSIDE.search(s)) \
        or any(part.split(".")[0].upper() in DEVICE_NAMES for part in PARTS.split(s))


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
    if len({k.upper() for k in out["env"]}) != len(out["env"]):   # one variable to Windows
        raise ValueError(f"`{BLOCK}` 의 run.env 에 대소문자만 다른 이름이 있다 — Windows 에는 한 변수다")
    settings = [*out["args"], *out["env"].values()]
    if named := sorted(k for k in out["env"] if k.upper() in LOOK_ENV):
        raise ValueError(f"`{BLOCK}` 의 run.env 는 `{named[0]}` 를 정하지 않는다 — 화면 검사가 "
                         f"{', '.join(sorted(LOOK_ENV))} 로 열 화면과 상태를 넘긴다")
    if owned := sorted(k for k in out["env"] if k.upper() in OWNED_ENV):
        raise ValueError(f"`{BLOCK}` 의 run.env 는 `{owned[0]}` 를 정하지 않는다 — look 이 {', '.join(sorted(OWNED_ENV))} 를 "
                         f"자기 폴더로 둔다. 앱 데이터는 `{DATA}` 를 받는 다른 인자나 변수로 넘긴다")
    if not any(DATA in s and not _remote(s) for s in settings):   # a local setting, never a URL's text
        raise ValueError(f"`{BLOCK}` 의 run 은 앱의 데이터를 `{DATA}` 에 두는 인자나 환경 변수를 하나 이상 적는다")
    named = [*(("", a) for a in out["args"]), *out["env"].items()]
    if outside := [s for name, s in named if _reaches_out(s, name)]:
        raise ValueError(f"`{BLOCK}` 의 run 설정 `{outside[0]}` 은 사본 밖을 가리킬 수 있다 — 실행은 얼린 사본과 "
                         f"`{DATA}` 만 읽는다. 드라이브·UNC·파일 URI·루트부터의 경로·`..`·`%변수%` 없이, run.cwd 기준 "
                         f"상대 경로나 `{DATA}` 나 원격 URL 로 적는다")
    installer = _relative(value.get("installer"), "installer")
    if Path(installer).suffix.lower() not in INSTALLERS:
        raise ValueError(f"`{BLOCK}` 의 installer 는 S8 이 붙일 설치 파일(.exe, .msi, .msix)이다 — {installer}")
    if installer.casefold() == f"{out['folder']}/{out['program']}".casefold():   # one file to Windows
        raise ValueError(f"`{BLOCK}` 의 installer 와 run.program 이 같은 파일이다 — run 은 설치하지 않고 띄우는 빌드다")
    return {"run": out, "installer": installer, "checks": _checks(value.get("checks", {}))}


def _checks(value) -> dict:
    """The block's `checks`: for each `look` owed check, by its id, the screen and state its scenario starts in
    and the driver's steps, each naming its control by `AutomationId` and ending with a postcondition; the last
    one reads the element's name, value or toggle state."""

    if not isinstance(value, dict):
        raise ValueError(f"`{BLOCK}` 의 checks 는 미룬 검사 id 마다 {{screen, state, steps}} 를 담은 객체다")
    out = {}
    for cid, c in value.items():
        where = f"`{BLOCK}` 의 checks.{cid}"
        if not isinstance(c, dict) or not isinstance(c.get("steps"), list) or not 1 <= len(c["steps"]) <= MAX_STEPS:
            raise ValueError(f"{where} 는 screen, state 와 1~{MAX_STEPS}개의 steps 를 담는다")
        steps, names = [], set()
        for n, s in enumerate(c["steps"], 1):
            at = f"{where}.steps[{n}]"
            if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not STEP_NAME.fullmatch(s["name"]) \
                    or s["name"] in names:
                raise ValueError(f"{at} 의 name 은 겹치지 않는 영문·숫자·`-_` 이름이다")
            names.add(s["name"])
            if why := action({k: s[k] for k in ACT if k in s}):
                raise ValueError(f"{at} 의 {why}")
            if why := postcondition(s.get("expect"), read=False):
                raise ValueError(f"{at} 의 {why}")
            steps.append({k: s[k] for k in ("name", "action", "id", "value", "in", "expect") if k in s})
        if why := postcondition(steps[-1]["expect"]):
            raise ValueError(f"{where} 의 마지막 step 은 {why}")
        if not all(isinstance(c.get(k), str) for k in ("screen", "state")):
            raise ValueError(f"{where} 의 screen 과 state 는 문자열이다")
        out[cid] = {"screen": c["screen"], "state": c["state"], "steps": steps}
    return out


def action(act) -> str:
    """Why `act` is not one driver step: an `action` on the control its `AutomationId` (`id`) names, in the main
    window or with `in: owned` the one window it owns, and a `value` for `set_value` only; empty when it is one."""

    import desktop_driver

    if not isinstance(act, dict) or set(act) - set(ACT) or act.get("action") not in desktop_driver.ACTIONS:
        return f"action 은 {', '.join(desktop_driver.ACTIONS)} 중 하나다"
    if not isinstance(act.get("id"), str) or not act["id"].strip():
        return "id 는 누를 컨트롤의 `AutomationId` 다"
    if ("value" in act) != (act["action"] == "set_value") or "value" in act and not (
            isinstance(act["value"], str) and desktop_driver.units(act["value"]) <= desktop_driver.MAX_VALUE):
        return f"value 는 set_value 에만, UTF-16 {desktop_driver.MAX_VALUE}자 이하 문자열로 적는다"
    if act.get("in", "main") not in WINDOWS:
        return "in 은 main(주 창) 이나 owned(주 창이 가진 창) 이다"
    return ""


def postcondition(expect, read: bool = True) -> str:
    """Why `expect` is not a postcondition the driver reads: an element by its `AutomationId` (`id`), in the main
    window or with `in: owned` the one window it owns, with its `name`, `value` or `toggle` state (with `read`,
    one at least); empty when it is one."""

    import desktop_driver

    plain = {k: v for k, v in expect.items() if k != "in"} if isinstance(expect, dict) else None
    if plain is None or set(plain) - set(desktop_driver.PROPERTIES) or not isinstance(plain.get("id"), str) \
            or not plain["id"].strip() or desktop_driver.wanted(plain) != plain or expect.get("in", "main") not in WINDOWS:
        return "expect 는 결과로 볼 요소의 id 에 name, value, toggle 을 더한 문자열 객체이고, in 은 main 이나 owned 다"
    if "toggle" in plain and plain["toggle"] not in desktop_driver.TOGGLES or \
            desktop_driver.units(plain.get("value", "")) > desktop_driver.MAX_VALUE:
        return (f"expect 의 toggle 은 {', '.join(desktop_driver.TOGGLES)} 중 하나, value 는 UTF-16 "
                f"{desktop_driver.MAX_VALUE}자 이하다")
    if read and not {"name", "value", "toggle"} & set(plain):
        return "expect 에 요소의 name, value, toggle 중 하나를 결과로 읽는다"
    return ""


def observed(seen) -> str:
    """Why `seen` is not what an `owner` check observes: exactly one of `report`, the app's own report of what it
    captured (an element's value by its `id`, with `in: owned` in the window the main window owns, or a UTF-8
    `file` under `{data}`), `window`, the round's capture of the main window or with `in: owned` the one it owns,
    or `composition`, its capture of the fixture's region; each of the last two with the `shows` sentence the
    critic judges it against. Each names `from`, what the app itself captures however the round observes it: the
    fixture's window, or the screen the fixture covers. Empty when it is one."""

    if not isinstance(seen, dict) or len(seen) != 1 or next(iter(seen)) not in OBSERVES \
            or not isinstance(v := next(iter(seen.values())), dict):
        return "observe 는 report, window, composition 중 하나만 담은 객체다"
    what = next(iter(seen))
    if v.get("from") not in REPORTS_FROM:
        return f"observe.{what} 의 from 은 앱이 캡처하는 곳, fixture(고정 창) 나 screen(고정 창이 덮은 화면) 이다"
    v = {k: x for k, x in v.items() if k != "from"}
    if what == "report":
        if set(v) == {"file"}:
            try:
                _relative(v["file"], "observe.report.file")
            except ValueError:
                return f"observe.report.file 은 앱 데이터 폴더(`{DATA}`) 안의 상대 경로다"
            return ""
        if set(v) - {"id", "in"} or not isinstance(v.get("id"), str) or not v["id"].strip() \
                or v.get("in", "main") not in WINDOWS:
            return "observe.report 는 앱이 캡처한 것을 내놓는 요소의 id(in 은 main 이나 owned) 나 file 이다"
        return ""
    if set(v) - {"shows", "in"} or (what == "composition" and "in" in v) or v.get("in", "main") not in WINDOWS \
            or not isinstance(v.get("shows"), str) or not v["shows"].strip() or len(v["shows"]) > MAX_SHOWS:
        return (f"observe.{what} 는 캡처에 보여야 할 것 shows({MAX_SHOWS}자 이하)"
                + (" 와 창(in 은 main 이나 owned)" if what == "window" else "") + " 를 적는다")
    return ""


def same(a: dict, b: dict) -> bool:
    """Two steps' or postconditions' fields alike, `in: main` being the default."""

    def plain(d: dict) -> dict:
        return {k: v for k, v in d.items() if (k, v) != ("in", "main")}

    return plain(a) == plain(b)


def looked_for(o: dict) -> list[dict]:
    """The owed checks the look runs on the PC app: `look` ones whose review named the step (`act`) and the
    postcondition it brings about (`expect`)."""

    return [x for x in o.get("owed") or [] if x["kind"] == "desktop" and x.get("source") == "look"
            and isinstance(x.get("expect"), dict) and isinstance(x.get("act"), dict)]


def owner_owed(o: dict) -> list[dict]:
    """The PC app's owed checks the owner's desktop round runs: every one the look does not."""

    ran = {x["id"] for x in looked_for(o)}
    return [x for x in o.get("owed") or [] if x["kind"] == "desktop" and x["id"] not in ran]


def owner_scripted(o: dict) -> list[dict]:
    """The `owner` checks the round can run: those whose review named the step (`act`) and what it observes."""

    return [x for x in owner_owed(o) if x.get("source") == "owner" and not action(x.get("act"))
            and not observed(x.get("observe"))]


def scripted(checks: dict, spec: dict) -> str:
    """Why the block's `checks` do not script exactly the PC app's `look` owed checks and the `owner` ones the
    round can run, each from one of its screens in one of its states; empty when they do."""

    from .oneshot_device import STATES

    looks = {x["id"]: x for x in looked_for(spec["oneshot"])}
    owners = {x["id"]: x for x in owner_scripted(spec["oneshot"])}
    owed = [*looks, *owners]
    if missing := [i for i in owed if i not in checks]:
        return (f"`{BLOCK}` 의 checks 에 PC 앱에서 확인할 {', '.join(missing)} 의 시나리오가 없다 — 미룬 검사마다 "
                "screen, state, steps 를 적는다")
    if extra := sorted(set(checks) - set(owed)):
        return f"`{BLOCK}` 의 checks 의 {', '.join(extra)} 는 화면 검사가 맡은 미룬 검사가 아니다 — {', '.join(owed) or '없음'}"
    ids = {s["id"] for s in spec["oneshot"]["front"]["screens"] if s.get("kind", "desktop") == "desktop"}
    for cid, c in checks.items():
        if c["screen"] not in ids or c["state"] not in STATES:
            return f"`{BLOCK}` 의 checks.{cid} 는 PC 앱의 화면({', '.join(sorted(ids))})과 상태({', '.join(STATES)})에서 시작한다"
        last, x = c["steps"][-1], looks.get(cid) or owners[cid]   # the review's step and postcondition, never the builder's
        if not same({k: last[k] for k in ACT if k in last}, x["act"]) or cid in looks and not same(last["expect"], x["expect"]):
            got = {k: last[k] for k in (*ACT, "expect") if k in last}
            result = f" 과 결과 {json.dumps(x['expect'], ensure_ascii=False)}" if cid in looks else ""
            return (f"`{BLOCK}` 의 checks.{cid} 의 마지막 step 은 리뷰가 정한 동작 {json.dumps(x['act'], ensure_ascii=False)}"
                    f"{result} 그대로다 — 받은 값 {json.dumps(got, ensure_ascii=False)}")
    return ""


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
    return _plain(buffer.value)


def _plain(path: str) -> str:
    """`path` without Windows' extended-length prefix, `\\\\?\\UNC\\` read back as `\\\\`."""

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
    if root != _plain(os.path.join(os.path.realpath(tree), *label.split("/"))):
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


def altered(build: dict) -> str:
    """`held`'s reason, the files let go once it is read."""

    with held(build) as (why, _):
        return why


@contextlib.contextmanager
def held(build: dict):
    """`(why, moved)`: why the frozen copy is no longer the build its identity names, or empty, the manifest and
    every file kept open for reading only until the block ends, and `moved()`, whether any name, size or write
    under the copy changed since before it was first listed. Windows refuses anyone a write, a rename or a delete
    of a held file; a file added beside them is what `moved` reports, so a block that asks it last decides on what
    stays on disk. Changed: the manifest's files and the saved launch settings no longer digest to the run's saved
    identity, or a file the manifest records was changed or removed, or one was added. The program runs inside its
    copy, so each start is checked after it. A file that cannot be opened so (one another holds open for writing
    too), or a folder that cannot be listed or watched, raises OSError: it is not shown changed."""

    import msvcrt
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    k.FindFirstChangeNotificationW.argtypes = [w.LPCWSTR, w.BOOL, w.DWORD]
    k.FindFirstChangeNotificationW.restype = w.HANDLE
    k.FindCloseChangeNotification.argtypes = [w.HANDLE]
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.CloseHandle.argtypes = [w.HANDLE]
    app, invalid = Path(build["folder"]), w.HANDLE(-1).value

    def refused(path: Path) -> OSError:
        error = ctypes.WinError(ctypes.get_last_error())   # a sharing violation is a PermissionError
        return type(error)(error.errno, error.strerror, str(path), error.winerror)

    def unlisted(error: OSError):   # never `rglob`, which passes over a folder it cannot list
        raise error

    def listed() -> list[str]:
        return sorted((Path(folder) / name).relative_to(app).as_posix()
                      for folder, _, names in os.walk(app, onerror=unlisted) for name in names)

    with contextlib.ExitStack() as files_held:
        # Every name, folder name, size and last-write change in the whole tree, from here until the block ends.
        watch = k.FindFirstChangeNotificationW(str(app), True, 0x1 | 0x2 | 0x8 | 0x10)
        if watch in (None, invalid):
            raise refused(app)
        files_held.callback(k.FindCloseChangeNotification, watch)

        def moved() -> bool:
            return k.WaitForSingleObject(watch, 0) != 0x102   # anything but WAIT_TIMEOUT: changed, or unknown

        def read(path: Path) -> bytes:   # GENERIC_READ, FILE_SHARE_READ only, an existing file
            handle = k.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0, None)
            if handle in (None, invalid):
                raise refused(path)
            try:   # the handle is closed however its wrapping fails
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                k.CloseHandle(handle)
                raise
            try:
                f = os.fdopen(fd, "rb")
            except BaseException:
                os.close(fd)
                raise
            return files_held.enter_context(f).read()

        manifest = json.loads(read(app.parent / "manifest.json").decode("utf-8"))
        files = manifest.get("files") if isinstance(manifest, dict) else None   # `[]` is no manifest: damaged
        if not isinstance(files, dict) or hashlib.sha256(json.dumps(
                {"files": files, "launch": build.get("launch")}, sort_keys=True).encode()).hexdigest() != build.get("identity"):
            yield "얼린 PC 앱 빌드의 매니페스트가 그 빌드의 식별자와 맞지 않는다 — 다시 빌드한다", moved
            return
        there = listed()
        changed = sorted(set(there) ^ set(files)) or [
            n for n in there if hashlib.sha256(read(app / n)).hexdigest() != files[n]] or \
            (["(읽는 사이 바뀐 이름)"] if moved() else [])   # one added while the others were read
        yield (f"PC 앱이 얼린 사본의 `{changed[0]}` 를 바꿨다 — 실행은 사본을 읽기만 하고, 앱의 데이터는 `{DATA}` 에 "
               "쓴다" if changed else ""), moved
