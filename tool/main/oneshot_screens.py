"""The oneshot frontend design: the feature list checked against the served API, the screens checked against
the owner's frequency map, the R2 sheet rendered from them, and what an edit reaches.

`docs/plans/oneshot/4-frontend.md` ("S5", "S6", "R2") is the design. Pure functions: no records, no sessions.
"""

from __future__ import annotations

import re
import struct
import zlib

from .oneshot_profile import kinds_of
from .oneshot_sheet import KIND_NAMES, translationese

CLASSES = ("every", "sometimes", "rarely", "never")
CLASS_NAMES = {"every": "늘 쓴다", "sometimes": "가끔 쓴다", "rarely": "드물게 쓴다", "never": "화면에 없다"}
FEATURE_ID = re.compile(r"[a-z][a-z0-9-]{0,39}")
SCREEN_ID = re.compile(r"S[1-9]\d*")
PALETTE_ID = re.compile(r"C[1-9]")
ROUTE = re.compile(r"/[a-z0-9/-]*")
REACH = ("start", "one-click", "deeper")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
COLORS = ("primary", "surface", "text", "accent")
METHODS = ("get", "post", "put", "patch", "delete")
BLOCKS = "oneshot-screens,oneshot-states,oneshot-tokens,oneshot-mocks"
MAX_MOCK = 200_000
VIEWPORTS = {"desktop": (1280, 800), "phone": (390, 844)}
# Where an Android screen shows: the app's own, drawn over another app, or in the notification shade.
SURFACES = {"screen": "앱 화면", "overlay": "다른 앱 위", "notification": "알림"}
# Where a PC app's screen shows: a window of its own, a dialog over it, a window kept over another program's, or
# the notification area's menu.
DESKTOP_SURFACES = {"window": "창", "dialog": "대화 상자", "floating": "다른 프로그램 위", "tray": "알림 영역"}
NATIVE = {"android": SURFACES, "desktop": DESKTOP_SURFACES}   # the kinds whose screens a preview tool draws
MAX_FILES = 40
MOCK_PATH = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",   # Windows also reserves the superscript digits
                *(f"{d}{n}" for d in ("COM", "LPT") for n in [*range(1, 10), "¹", "²", "³"])}
SIZE_SLACK = 0.03   # a preview tool rounds dp to px: a picture within 3% of the device is its size
# A feature's key, then the words its refusals use: the served API on the web, the approved core on Android.
INVENTORY = {"endpoints": ("엔드포인트", "`METHOD /path`", "서버에 없는"),
             "parts": ("핵심 항목", "모듈(M)·권한 요구(R) id", "핵심 설계에 없는")}


def _text(item: dict, key: str, where: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: `{key}` 가 비었다")
    return " ".join(value.split())


def api_paths(ops: list[str]) -> re.Pattern:
    """A URL path any operation answers: `{id}` matches one segment."""

    templates = sorted({op.split(" ", 1)[1] for op in ops})
    parts = [re.sub(r"\\\{[^/]*?\\\}", "[^/]+", re.escape(t)) for t in templates]
    return re.compile("(?:" + "|".join(parts) + r")/?") if parts else re.compile(r"(?!)")


def operations(document) -> list[str]:
    """`METHOD /path` for every operation the approved OpenAPI document holds."""

    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise ValueError("OpenAPI 문서에 `paths` 가 없다")
    return sorted(f"{m.upper()} {path}" for path, ops in document["paths"].items() if isinstance(ops, dict)
                  for m in ops if m in METHODS)


def views(profile: dict, kind: str = "") -> list[str]:
    """The viewports each mock is shot at: an Android app's reference phone, a PC app's window, or a web app's.
    Several kinds' card holds every kind's field, so there a screen's `kind` picks."""

    if kind == "android" or not kind and "device" in profile:
        return ["phone"]
    if kind == "desktop" or not kind and "window" in profile:
        return ["window"]
    return list(VIEWPORTS) if profile.get("responsive") == "desktop-mobile" else ["desktop"]


def features(value, ops: list[str], key: str = "endpoints") -> list[dict]:
    """`oneshot-features`, checked: every served operation in exactly one feature, nothing invented. An
    Android app's features name their `parts` instead: the core's modules and its platform capabilities."""

    what, shape, absent = INVENTORY[key]
    if not isinstance(value, list) or not value:
        raise ValueError("`oneshot-features` 는 비지 않은 목록이어야 한다")
    out, seen, owner = [], set(), {}
    for n, item in enumerate(value, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not FEATURE_ID.fullmatch(item["id"]):
            raise ValueError(f"{n}번째 기능의 `id` 는 소문자, 숫자, `-` 로 쓴다")
        where = f"기능 `{item['id']}`"
        if item["id"] in seen:
            raise ValueError(f"{where}: id 가 겹친다")
        seen.add(item["id"])
        if item.get("class") not in CLASSES:
            raise ValueError(f"{where}: `class` 는 {', '.join(CLASSES)} 중 하나다")
        endpoints = item.get(key)
        if not isinstance(endpoints, list) or not endpoints or not all(isinstance(e, str) for e in endpoints):
            raise ValueError(f"{where}: `{key}` 는 비지 않은 {shape} 목록이다")
        if bad := [e for e in endpoints if e not in ops]:
            raise ValueError(f"{where}: {absent} {what}다 — {', '.join(bad)}")
        for e in endpoints:
            if e in owner:
                raise ValueError(f"`{e}` 가 `{owner[e]}` 와 `{item['id']}` 둘에 들어 있다")
            owner[e] = item["id"]
        out.append({"id": item["id"], "name": _text(item, "name", where), key: list(endpoints),
                    "class": item["class"], "why": _text(item, "why", where)})
    if missing := [e for e in ops if e not in owner]:
        raise ValueError(f"어느 기능에도 없는 {what}다: {', '.join(missing)}")
    return out


def board(found: list[dict], frequency, first_screen, kinds: list[str] = ()) -> tuple[dict, str | dict]:
    """The owner's board: a class for every feature, and the first-screen line; for several kinds (`kinds`)
    one line for each, keyed by kind."""

    ids = [f["id"] for f in found]
    if not isinstance(frequency, dict) or set(frequency) != set(ids):
        raise ValueError("모든 기능을 한 칸에 하나씩 놓아야 한다")
    if bad := [k for k, v in frequency.items() if v not in CLASSES]:
        raise ValueError(f"없는 칸이다: {', '.join(bad)}")
    line = lambda s: isinstance(s, str) and 1 <= len(s.strip()) <= 200   # noqa: E731
    if kinds:
        if not isinstance(first_screen, dict) or set(first_screen) != set(kinds) or not all(map(line, first_screen.values())):
            raise ValueError("종류마다 첫 화면이 무엇을 위한지 한 줄(200자 이내)씩 적어라")
        return {i: frequency[i] for i in ids}, {k: " ".join(first_screen[k].split()) for k in kinds}
    if not line(first_screen):
        raise ValueError("첫 화면이 무엇을 위한지 한 줄(200자 이내)로 적어라")
    return {i: frequency[i] for i in ids}, " ".join(first_screen.split())


# -- S6 ------------------------------------------------------------------------------------

def _screens(value, known: set[str], kind: str = "webapp") -> list[dict]:
    """An Android or a PC app's screen names its `surface` where a web screen has its route."""

    if not isinstance(value, list) or not value:
        raise ValueError("`oneshot-screens` 는 비지 않은 목록이어야 한다")
    out, ids, routes = [], set(), set()
    surfaces = NATIVE.get(kind)
    native = surfaces is not None
    place = "surface" if native else "route"
    for n, s in enumerate(value, 1):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not SCREEN_ID.fullmatch(s["id"]):
            raise ValueError(f"{n}번째 화면의 `id` 는 S1, S2 … 여야 한다")
        where = f"화면 {s['id']}"
        if s["id"] in ids or not native and s.get("route") in routes:
            raise ValueError(f"{where}: id 나 경로가 겹친다")
        if native and s.get("surface") not in surfaces:
            raise ValueError(f"{where}: `surface` 는 {', '.join(surfaces)} 중 하나다")
        if not native and (not isinstance(s.get("route"), str) or not ROUTE.fullmatch(s["route"])):
            raise ValueError(f"{where}: `route` 는 / 로 시작하고 소문자, 숫자, `-/` 만 쓴다")
        if not isinstance(s.get("primary"), bool) or s.get("reached") not in REACH:
            raise ValueError(f"{where}: `primary` 는 true 나 false, `reached` 는 {', '.join(REACH)} 중 하나다")
        feats = s.get("features")
        if not isinstance(feats, list) or not all(isinstance(f, str) for f in feats):
            raise ValueError(f"{where}: `features` 는 기능 id 목록이다")
        if bad := [f for f in feats if f not in known]:
            raise ValueError(f"{where}: 없는 기능이다 — {', '.join(bad)}")
        ids.add(s["id"])
        routes.add(s[place])
        out.append({"id": s["id"], place: s[place], "purpose": _text(s, "purpose", where),
                    "features": list(dict.fromkeys(feats)), "primary": s["primary"], "reached": s["reached"]})
    return out


def _states(value, screens: list[dict]) -> dict[str, dict]:
    if not isinstance(value, list):
        raise ValueError("`oneshot-states` 는 화면마다 하나인 목록이다")
    out = {}
    for s in value:
        if not isinstance(s, dict) or s.get("screen") not in {x["id"] for x in screens} or s["screen"] in out:
            raise ValueError("`oneshot-states` 의 `screen` 은 화면 id 이고 화면마다 하나다")
        out[s["screen"]] = {k: _text(s, k, f"{s['screen']} 상태") for k in ("empty", "loading", "error", "full")}
    if missing := [x["id"] for x in screens if x["id"] not in out]:
        raise ValueError(f"상태가 없는 화면이다: {', '.join(missing)}")
    return out


def _tokens(value) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("palettes"), list) or not 2 <= len(value["palettes"]) <= 3:
        raise ValueError("`oneshot-tokens` 의 `palettes` 는 두세 개다")
    palettes, ids = [], set()
    for p in value["palettes"]:
        if not isinstance(p, dict) or not PALETTE_ID.fullmatch(str(p.get("id"))) or p["id"] in ids:
            raise ValueError("팔레트마다 겹치지 않는 `id`(C1, C2, C3)가 있어야 한다")
        colors = p.get("colors")
        if not isinstance(colors, dict) or not all(COLOR.fullmatch(str(colors.get(k))) for k in COLORS):
            raise ValueError(f"팔레트 {p['id']}: `colors` 에 {', '.join(COLORS)} 가 #rrggbb 로 있어야 한다")
        ids.add(p["id"])
        palettes.append({"id": p["id"], "name": _text(p, "name", f"팔레트 {p['id']}"),
                         "colors": {k: colors[k].lower() for k in COLORS}})
    scale, spacing = value.get("type"), value.get("spacing")
    if not isinstance(scale, list) or not scale or not all(isinstance(t, str) and t.strip() for t in scale):
        raise ValueError("`type` 은 글자 크기 단계의 문자열 목록이다")
    if not isinstance(spacing, list) or not spacing or not all(
            isinstance(x, int) and not isinstance(x, bool) and 0 < x <= 128 for x in spacing):
        raise ValueError("`spacing` 은 1–128 사이 정수 목록이다")
    return {"palettes": palettes, "type": [t.strip() for t in scale], "spacing": spacing}


def _mocks(value, screens: list[dict]) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {s["id"] for s in screens}:
        raise ValueError("`oneshot-mocks` 는 화면 id 마다 HTML 하나다")
    for s in screens:
        html = value[s["id"]]
        if not isinstance(html, str) or not html.strip() or len(html) > MAX_MOCK:
            raise ValueError(f"{s['id']} 목업: HTML 이 비었거나 {MAX_MOCK:,}자를 넘는다")
        if missing := [f for f in s["features"] if f'data-feature="{f}"' not in html]:
            raise ValueError(f"{s['id']} 목업: `data-feature` 가 빠진 기능 — {', '.join(missing)}")
    return {s["id"]: value[s["id"]] for s in screens}


def preview(value, screens: list[dict]) -> tuple[dict[str, str], dict]:
    """A native app's `oneshot-mocks`: the mock sources as files, the file drawing each screen, the preview
    command and the folder it writes `<screen id>.png` to. `({screen: its source}, the preview as given)`;
    the command's own shape is the caller's check, as S4's are."""

    if not isinstance(value, dict) or not isinstance(files := value.get("files"), dict) or not files \
            or not isinstance(drawn := value.get("screens"), dict):
        raise ValueError("`oneshot-mocks` 는 files(경로에서 소스로), screens(화면 id 에서 경로로), command, shots 를 담은 객체다")
    paths = [*files, value.get("shots")]
    if bad := [p for p in paths if not isinstance(p, str) or not MOCK_PATH.fullmatch(p)
               or {".", "..", ".git"} & {part.lower() for part in p.split("/")}]:
        raise ValueError(f"`oneshot-mocks` 의 경로는 작업트리 안의 상대 경로(`/` 로 나눔)다 — {', '.join(map(str, bad[:5]))}")
    # Windows reads a path without case and drops a component's trailing dot, and a device name is no file: two
    # names it reads as one place, or a file named as another's folder, would write one source over another.
    if clash := sorted({p for p in files for q in files if p != q and (
            p.casefold() == q.casefold() or q.casefold().startswith(p.casefold() + "/"))}
            | {p for p in files if any(part.endswith(".") or part.split(".")[0].upper() in DEVICE_NAMES
                                       for part in p.split("/"))}):
        raise ValueError("`oneshot-mocks` 의 경로가 Windows 에서 다른 경로와 한 자리이거나 장치 이름이다 (대소문자만 다르거나, "
                         f"한 경로가 다른 경로의 폴더이거나, 끝이 `.` 이다) — {', '.join(clash[:5])}")
    if len(files) > MAX_FILES or not all(isinstance(t, str) and t.strip() for t in files.values()) \
            or sum(map(len, files.values())) > 2 * MAX_MOCK:
        raise ValueError(f"`oneshot-mocks` 의 files 는 {MAX_FILES}개, 합쳐서 {2 * MAX_MOCK:,}자 이하의 비지 않은 소스다")
    if set(drawn) != {s["id"] for s in screens} or any(drawn[s] not in files for s in drawn):
        raise ValueError("`oneshot-mocks` 의 screens 는 화면 id 마다 files 에 있는 경로 하나다")
    if not isinstance(value.get("command"), str) or not value["command"].strip():
        raise ValueError("`oneshot-mocks` 의 command 는 화면 사진을 찍는 명령이다")
    mocks = {s["id"]: files[drawn[s["id"]]] for s in screens}
    for s in screens:
        if missing := [f for f in s["features"] if f"feature:{f}" not in mocks[s["id"]]]:
            raise ValueError(f"{s['id']} 목업 `{drawn[s['id']]}`: `feature:<id>` 가 빠진 기능 — {', '.join(missing)}")
    return mocks, {"files": dict(files), "screens": {s["id"]: drawn[s["id"]] for s in screens},
                   "command": value["command"].strip(), "shots": value["shots"]}


CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}   # a PNG colour type's samples per pixel
# The bit depths each colour type allows. A palette image (type 3) is not taken: its indices could point past
# its palette, and preview tools write grey or true colour.
DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 4: (8, 16), 6: (8, 16)}
TRNS = {0: 2, 2: 6}   # a transparency chunk's length for grey and true colour; types with alpha carry none
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def scanlines(width: int, height: int, bits: int, interlace: int) -> list[int]:
    """Each scanline's length in bytes, its filter byte included, in the order the pixels hold them."""

    passes = ADAM7 if interlace else ((0, 0, 1, 1),)
    out = []
    for x, y, dx, dy in passes:
        w, h = (width - x + dx - 1) // dx, (height - y + dy - 1) // dy
        if w > 0:
            out += [1 + (w * bits + 7) // 8] * max(h, 0)
    return out


def ordered(names: list[bytes], colour: int) -> bool:
    """The chunks in the one order a decoder reads: `IHDR` first, at most one `PLTE` (never for grey), one
    unbroken run of `IDAT`, `IEND` last, and no other critical chunk. Ancillary chunks may sit elsewhere."""

    if not all(n.isalpha() for n in names):
        return False
    critical = [n for n in names if n[:1].isupper()]
    if critical[:1] != [b"IHDR"] or critical[-1:] != [b"IEND"]:
        return False
    middle = critical[1:-1]
    if middle[:1] == [b"PLTE"]:
        if colour in (0, 4):
            return False
        middle = middle[1:]
    runs = [i for i, n in enumerate(names) if n == b"IDAT"]
    return bool(middle) and set(middle) == {b"IDAT"} and runs == list(range(runs[0], runs[-1] + 1))


def picture(data: bytes, largest: tuple[int, int]) -> tuple[tuple[int, int], bytes] | None:
    """A complete PNG's width and height and the picture as kept, or `None`. Every chunk's checksum holds and
    the chunks come in order (`ordered`); it is no larger than `largest`, refused before anything is allocated
    for it; its colour type is grey or true colour with a bit depth that type allows; and its pixels inflate
    to exactly the scanlines its header promises, each opening with a defined filter (0–4). Every byte after
    that is a valid sample. The picture kept is rebuilt from `IHDR`, a `tRNS` (which marks pixels as
    transparent, so it is part of the picture: one of its exact length before the pixels, or the picture is
    refused), the `IDAT` run and `IEND`. Any other ancillary chunk a decoder might read and refuse (`sRGB`,
    `gAMA`, …) is dropped, so what is kept decodes and looks as the preview drew it."""

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    at, chunks = 8, []
    while at + 12 <= len(data) and (not chunks or chunks[-1][0] != b"IEND"):
        n, name = int.from_bytes(data[at:at + 4], "big"), data[at + 4:at + 8]
        body, crc = data[at + 8:at + 8 + n], data[at + 8 + n:at + 12 + n]
        if len(body) != n or len(crc) != 4 or zlib.crc32(name + body) != int.from_bytes(crc, "big"):
            return None
        chunks.append((name, body))
        at += 12 + n
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        return None
    width, height, depth, colour, method, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if depth not in DEPTHS.get(colour, ()) or not 0 < width <= largest[0] or not 0 < height <= largest[1] \
            or method or filtering or interlace not in (0, 1) or not ordered(names := [n for n, _ in chunks], colour):
        return None
    if b"tRNS" in names and (names.count(b"tRNS") > 1 or names.index(b"tRNS") > names.index(b"IDAT")
                             or len(chunks[names.index(b"tRNS")][1]) != TRNS.get(colour)):
        return None
    lines = scanlines(width, height, CHANNELS[colour] * depth, interlace)
    try:   # bounded: a picture that inflates past its own size is not one
        pixels = zlib.decompressobj().decompress(b"".join(b for name, b in chunks if name == b"IDAT"), sum(lines) + 1)
    except zlib.error:
        return None
    if len(pixels) != sum(lines):
        return None
    at = 0
    for n in lines:
        if pixels[at] > 4:
            return None
        at += n
    kept = b"".join(struct.pack(">I", len(body)) + name + body + struct.pack(">I", zlib.crc32(name + body))
                    for name, body in chunks if name in (b"IHDR", b"tRNS", b"IDAT", b"IEND"))
    return (width, height), data[:8] + kept


def largest(device: dict) -> tuple[int, int]:
    """The largest picture a preview of `device` may write: its size and the slack."""

    return int(device["width"] * (1 + SIZE_SLACK)), int(device["height"] * (1 + SIZE_SLACK))


def sized(size: tuple[int, int] | None, device: dict) -> bool:
    return size is not None and all(abs(got - want) <= want * SIZE_SLACK
                                    for got, want in zip(size, (device["width"], device["height"])))


def design(found: dict, feats: list[dict], frequency: dict[str, str], kind: str = "webapp") -> dict:
    """The four S6 blocks, shape-checked and checked against the owner's map. A native kind's mocks are its
    preview's sources, drawn by the stack's own preview tool."""

    if missing := [b for b in BLOCKS.split(",") if b not in found]:
        raise ValueError(f"블록이 빠졌다: {', '.join(missing)}")
    screens = _screens(found["oneshot-screens"], {f["id"] for f in feats}, kind)
    native = kind in NATIVE
    # A screen opens at its own route on the app's one port, so the API must not answer the same path.
    api = api_paths([e for f in feats for e in f.get("endpoints") or []])
    if clash := [s["route"] for s in screens if not native and api.fullmatch(s["route"])]:
        raise ValueError(f"화면 경로가 API 경로와 겹친다 — {', '.join(clash)}. 화면은 자기 경로에서 열린다")
    primary = [s for s in screens if s["primary"]]
    if len(primary) != 1 or primary[0]["reached"] != "start":
        raise ValueError("첫 화면(`primary`, `reached: start`)이 정확히 하나여야 한다")
    first = primary[0]
    by_class = {c: [f for f, k in frequency.items() if k == c] for c in CLASSES}
    if gone := [f for f in by_class["every"] if f not in first["features"]]:
        raise ValueError(f"늘 쓰는 기능은 첫 화면에 있어야 한다: {', '.join(gone)}")
    if wrong := [f for f in by_class["rarely"] + by_class["never"] if f in first["features"]]:
        raise ValueError(f"드물게 쓰거나 화면에 없는 기능이 첫 화면에 있다: {', '.join(wrong)}")
    near = {f for s in screens if s["reached"] in ("start", "one-click") for f in s["features"]}
    if far := [f for f in by_class["sometimes"] if f not in near]:
        raise ValueError(f"가끔 쓰는 기능은 첫 화면에서 한 번에 닿아야 한다: {', '.join(far)}")
    shown = {f for s in screens for f in s["features"]}
    if hidden := [f for f in frequency if frequency[f] != "never" and f not in shown]:
        raise ValueError(f"어느 화면에도 없는 기능이다: {', '.join(hidden)}")
    if leaked := [f for f in by_class["never"] if f in shown]:
        raise ValueError(f"화면에 없기로 한 기능이 화면에 있다: {', '.join(leaked)}")
    made = {"screens": screens, "states": _states(found["oneshot-states"], screens), "tokens": _tokens(found["oneshot-tokens"])}
    if not native:
        return {**made, "mocks": _mocks(found["oneshot-mocks"], screens)}
    mocks, shown = preview(found["oneshot-mocks"], screens)
    return {**made, "mocks": mocks, "preview": shown}


def design_kinds(found: dict, feats: list[dict], frequency: dict[str, str], kinds: list[str],
                 api: list[str] = ()) -> dict:
    """Several kinds' four S6 blocks: each screen names its `kind`, the tokens are shared, and `oneshot-mocks`
    holds each kind's mocks under its name. Each kind is checked on its own by `design`, with only the
    features that apply to it (`api`: the server's `METHOD /path`, which a web screen's route may not be)."""

    if missing := [b for b in BLOCKS.split(",") if b not in found]:
        raise ValueError(f"블록이 빠졌다: {', '.join(missing)}")
    shown, mocks = found["oneshot-screens"], found["oneshot-mocks"]
    if not isinstance(shown, list) or not shown or not all(isinstance(s, dict) for s in shown):
        raise ValueError("`oneshot-screens` 는 비지 않은 목록이어야 한다")
    if bad := sorted({str(s.get("kind")) for s in shown} - set(kinds)):
        raise ValueError(f"화면마다 `kind` 가 고른 종류({', '.join(kinds)}) 중 하나여야 한다 — {', '.join(bad)}")
    if len(ids := [s.get("id") for s in shown]) != len(set(map(str, ids))):
        raise ValueError("화면 id 는 모든 종류를 통틀어 겹치지 않는다")
    if not isinstance(mocks, dict) or set(mocks) != set(kinds):
        raise ValueError(f"`oneshot-mocks` 는 종류마다({', '.join(kinds)}) 그 종류의 목업을 담은 객체다")
    states = found["oneshot-states"] if isinstance(found["oneshot-states"], list) else []
    if stray := [s.get("screen") for s in states if not isinstance(s, dict) or s.get("screen") not in ids]:
        raise ValueError(f"`oneshot-states` 의 `screen` 은 화면 id 다 — {', '.join(map(str, stray))}")
    out = {"screens": [], "states": {}, "mocks": {}}
    for k in kinds:
        mine = [{x: v for x, v in s.items() if x != "kind"} for s in shown if s["kind"] == k]
        if not mine:
            raise ValueError(f"{k} 의 화면이 없다")
        own = {s["id"] for s in mine}
        kept = [f for f in feats if k in f["kinds"]]
        # The API is the server's whole, whichever features are the web app's: none left must not drop the check.
        if k == "webapp" and (clash := [s["route"] for s in mine if isinstance(s.get("route"), str)
                                        and api_paths(list(api)).fullmatch(s["route"])]):
            raise ValueError(f"webapp: 화면 경로가 API 경로와 겹친다 — {', '.join(clash)}. 화면은 자기 경로에서 열린다")
        try:
            made = design({"oneshot-screens": mine, "oneshot-states": [s for s in states if s["screen"] in own],
                           "oneshot-tokens": found["oneshot-tokens"], "oneshot-mocks": mocks[k]},
                          kept, {f["id"]: frequency[f["id"]] for f in kept}, k)
        except ValueError as exc:
            raise ValueError(f"{k}: {exc}") from exc
        out["screens"] += [{**s, "kind": k} for s in made["screens"]]
        out["states"] |= made["states"]
        out["mocks"] |= made["mocks"]
        out["tokens"] = made["tokens"]
        if "preview" in made:   # each native kind's own, run in a folder of its own
            out.setdefault("previews", {})[k] = made["preview"]
    order = {sid: n for n, sid in enumerate(ids)}   # the screens in the order the turn gave them
    return {**out, "screens": sorted(out["screens"], key=lambda s: order[s["id"]])}


def soft(front: dict) -> list[str]:
    """Translationese in what the owner reads: sent back once, never a stop."""

    texts = [(s["id"], s["purpose"]) for s in front["screens"]]
    texts += [(f"{sid} 상태", t) for sid, st in front["states"].items() for t in st.values()]
    texts += [(p["id"], p["name"]) for p in front["tokens"]["palettes"]]
    return [f"{where} 번역체: " + "; ".join(f"{n} `{s}`" for n, s in hits)
            for where, text in texts if (hits := translationese(text))]


def decidable(front: dict) -> list[str]:
    """Every R2 item: each screen and each screen's states. The palette is a choice beside them."""

    return [x for s in front["screens"] for x in (s["id"], f"{s['id']}.states")]


def previews(front: dict, kind: str) -> dict[str, dict]:
    """Each native kind's preview, by kind. Several kinds keep them as `previews`; one kind's design holds its
    own as `preview`, and so does a run of several kinds designed before a PC app could be one of them, whose
    one preview was the Android app's."""

    if "previews" in front:
        return front["previews"]
    return {kind if kind in NATIVE else "android": front["preview"]} if "preview" in front else {}


def every(front: dict) -> dict[str, object]:
    """Each R2 item's content, to tell what an edit changed: a screen with its mock, a screen's states. A
    native screen's mock includes the file it is drawn from, since the preview reads that path."""

    drawn = {sid: path for p in previews(front, "").values() for sid, path in p["screens"].items()}
    return {**{s["id"]: [s, front["mocks"][s["id"]], drawn.get(s["id"])] for s in front["screens"]},
            **{f"{sid}.states": st for sid, st in front["states"].items()}}


def dependents(start: set[str]) -> set[str]:
    """A screen's states, mock and shots depend on the screen."""

    return start | {f"{i}.states" for i in start if SCREEN_ID.fullmatch(i)}


# -- The R2 sheet --------------------------------------------------------------------------

def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def place(s: dict) -> str:
    """Where a screen opens, as the sheet names it: a web route, or a native surface."""

    return f"`{s['route']}`" if "route" in s else {**SURFACES, **DESKTOP_SURFACES}[s["surface"]]


def placed(group: list[dict], feats: list[dict], frequency: dict, shown: list[str], first_line: str, mark, shot,
           h: str = "") -> list[str]:
    """One kind's part of the R2 sheet (or the whole run's for one kind): its first screen, where each feature
    sits, its other screens. `h` deepens the headings under a kind's own."""

    first = next(s for s in group if s["primary"])
    lines = [f"> 첫 화면은 {first_line}", "",
             f"{h}## 첫 화면 {first['id']}{mark(first['id'])} {place(first)}", "", first["purpose"], "",
             *[f"![{first['id']} {v}]({shot(first['id'], v)})" for v in shown], "",
             f"{h}## 기능을 둔 자리", "", "| 기능 | 주인이 고른 칸 | 놓인 화면 |", "| --- | --- | --- |"]
    where = {f["id"]: [s["id"] for s in group if f["id"] in s["features"]] for f in feats}
    lines += [f"| {_cell(f['name'])} (`{f['id']}`) | {CLASS_NAMES[frequency[f['id']]]} | "
              f"{', '.join(where[f['id']]) or '없음'} |" for f in feats]
    lines += ["", f"{h}## 다른 화면", ""]
    for s in group:
        if s is not first:
            lines += [f"{h}### {s['id']}{mark(s['id'])} {place(s)} — {s['purpose']}", "",
                      *[f"![{s['id']} {v}]({shot(s['id'], v)})" for v in shown], ""]
    return lines


def sheet(spec: dict, shot) -> str:
    """The R2 sheet in Markdown; `shot(screen_id, viewport)` gives a screenshot's URL. Several kinds' screens
    are grouped by kind."""

    o = spec["oneshot"]
    front, feats, frequency = o["front"], o["features"], o["frequency"]
    marks = (o.get("r2") or {}).get("marks") or {}
    tag = {"edited": " (고친 항목)", "consequence": " (따라 바뀜)", "again": " (다시 승인)"}
    mark = lambda i: tag.get(marks.get(i), "")   # noqa: E731
    lines = [f"# 화면 설계 — {spec['repo']}", ""]
    kinds = [k for k in kinds_of(o) if any(s.get("kind") == k for s in front["screens"])]
    for k in kinds or [""]:   # several kinds: one section each in the owner's order, the same shape one level down
        group = [s for s in front["screens"] if s.get("kind", "") == k]
        own = [f for f in feats if not k or k in f["kinds"]]
        h = "#" if k else ""
        if k:
            lines += [f"## {KIND_NAMES[k]}", ""]
        lines += placed(group, own, frequency, views(o["profile"], k), o["first_screen"][k] if k else o["first_screen"],
                        mark, shot, h)
    lines += ["## 색 배합 (하나를 고른다)", "", "| id | 이름 | " + " | ".join(COLORS) + " |",
              "| --- | --- | " + " | ".join("---" for _ in COLORS) + " |"]
    chosen = (o.get("r2") or {}).get("palette")
    lines += [f"| {p['id']}{' (고름)' if p['id'] == chosen else ''} | {_cell(p['name'])} | "
              + " | ".join(f"`{p['colors'][k]}`" for k in COLORS) + " |" for p in front["tokens"]["palettes"]]
    reviewed = "핵심 리뷰" if {"device", "window"} & set(o["profile"]) else "백엔드 리뷰"
    lines += ["", f"<details><summary>화면마다의 상태, {reviewed}</summary>", ""]
    for s in front["screens"]:
        st = front["states"][s["id"]]
        lines.append(f"- **{s['id']}.states**{mark(s['id'] + '.states')} 비었을 때: {st['empty']} / 불러오는 중: "
                     f"{st['loading']} / 오류: {st['error']} / 찼을 때: {st['full']}")
    review = (o.get("reviews") or [{}])[-1]
    lines += ["", f"- {reviewed}: {review.get('verdict', '없음')} (`{str(review.get('head', ''))[:12]}`), "
                  f"발견 {len(review.get('findings') or [])}개", "", "</details>", ""]
    return "\n".join(lines)
