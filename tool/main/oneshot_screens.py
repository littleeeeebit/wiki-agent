"""The oneshot frontend design: the feature list checked against the served API, the screens checked against
the owner's frequency map, the R2 sheet rendered from them, and what an edit reaches.

`docs/plans/oneshot/4-frontend.md` ("S5", "S6", "R2") is the design. Pure functions: no records, no sessions.
"""

from __future__ import annotations

import re

from .oneshot_sheet import translationese

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
MAX_FILES = 40
MOCK_PATH = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")
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


def views(profile: dict) -> list[str]:
    """The viewports each mock is shot at: an Android app's reference phone, or a web app's."""

    if "device" in profile:
        return ["phone"]
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


def board(found: list[dict], frequency, first_screen) -> tuple[dict, str]:
    """The owner's board: a class for every feature, and the first-screen line."""

    ids = [f["id"] for f in found]
    if not isinstance(frequency, dict) or set(frequency) != set(ids):
        raise ValueError("모든 기능을 한 칸에 하나씩 놓아야 한다")
    if bad := [k for k, v in frequency.items() if v not in CLASSES]:
        raise ValueError(f"없는 칸이다: {', '.join(bad)}")
    if not isinstance(first_screen, str) or not 1 <= len(first_screen.strip()) <= 200:
        raise ValueError("첫 화면이 무엇을 위한지 한 줄(200자 이내)로 적어라")
    return {i: frequency[i] for i in ids}, " ".join(first_screen.split())


# -- S6 ------------------------------------------------------------------------------------

def _screens(value, known: set[str], android: bool = False) -> list[dict]:
    """An Android screen names its `surface` where a web screen has its route."""

    if not isinstance(value, list) or not value:
        raise ValueError("`oneshot-screens` 는 비지 않은 목록이어야 한다")
    out, ids, routes = [], set(), set()
    place = "surface" if android else "route"
    for n, s in enumerate(value, 1):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not SCREEN_ID.fullmatch(s["id"]):
            raise ValueError(f"{n}번째 화면의 `id` 는 S1, S2 … 여야 한다")
        where = f"화면 {s['id']}"
        if s["id"] in ids or not android and s.get("route") in routes:
            raise ValueError(f"{where}: id 나 경로가 겹친다")
        if android and s.get("surface") not in SURFACES:
            raise ValueError(f"{where}: `surface` 는 {', '.join(SURFACES)} 중 하나다")
        if not android and (not isinstance(s.get("route"), str) or not ROUTE.fullmatch(s["route"])):
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
    """An Android app's `oneshot-mocks`: the mock sources as files, the file drawing each screen, the preview
    command and the folder it writes `<screen id>.png` to. `({screen: its source}, the preview as given)`;
    the command's own shape is the caller's check, as S4's are."""

    if not isinstance(value, dict) or not isinstance(files := value.get("files"), dict) or not files \
            or not isinstance(drawn := value.get("screens"), dict):
        raise ValueError("`oneshot-mocks` 는 files(경로에서 소스로), screens(화면 id 에서 경로로), command, shots 를 담은 객체다")
    paths = [*files, value.get("shots")]
    if bad := [p for p in paths if not isinstance(p, str) or not MOCK_PATH.fullmatch(p)
               or {".", "..", ".git"} & {part.lower() for part in p.split("/")}]:
        raise ValueError(f"`oneshot-mocks` 의 경로는 작업트리 안의 상대 경로(`/` 로 나눔)다 — {', '.join(map(str, bad[:5]))}")
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


def png_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's width and height from its header, or `None` for anything else."""

    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def sized(size: tuple[int, int] | None, device: dict) -> bool:
    return size is not None and all(abs(got - want) <= want * SIZE_SLACK
                                    for got, want in zip(size, (device["width"], device["height"])))


def design(found: dict, feats: list[dict], frequency: dict[str, str], android: bool = False) -> dict:
    """The four S6 blocks, shape-checked and checked against the owner's map."""

    if missing := [b for b in BLOCKS.split(",") if b not in found]:
        raise ValueError(f"블록이 빠졌다: {', '.join(missing)}")
    screens = _screens(found["oneshot-screens"], {f["id"] for f in feats}, android)
    # A screen opens at its own route on the app's one port, so the API must not answer the same path.
    api = api_paths([e for f in feats for e in f.get("endpoints") or []])
    if clash := [s["route"] for s in screens if not android and api.fullmatch(s["route"])]:
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
    if not android:
        return {**made, "mocks": _mocks(found["oneshot-mocks"], screens)}
    mocks, shown = preview(found["oneshot-mocks"], screens)
    return {**made, "mocks": mocks, "preview": shown}


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


def every(front: dict) -> dict[str, object]:
    """Each R2 item's content, to tell what an edit changed: a screen with its mock, a screen's states."""

    return {**{s["id"]: [s, front["mocks"][s["id"]]] for s in front["screens"]},
            **{f"{sid}.states": st for sid, st in front["states"].items()}}


def dependents(start: set[str]) -> set[str]:
    """A screen's states, mock and shots depend on the screen."""

    return start | {f"{i}.states" for i in start if SCREEN_ID.fullmatch(i)}


# -- The R2 sheet --------------------------------------------------------------------------

def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def place(s: dict) -> str:
    """Where a screen opens, as the sheet names it: a web route, or an Android surface."""

    return f"`{s['route']}`" if "route" in s else SURFACES[s["surface"]]


def sheet(spec: dict, shot) -> str:
    """The R2 sheet in Markdown; `shot(screen_id, viewport)` gives a screenshot's URL."""

    o = spec["oneshot"]
    front, feats, frequency = o["front"], o["features"], o["frequency"]
    marks = (o.get("r2") or {}).get("marks") or {}
    tag = {"edited": " (고친 항목)", "consequence": " (따라 바뀜)", "again": " (다시 승인)"}
    mark = lambda i: tag.get(marks.get(i), "")   # noqa: E731
    shown = views(o["profile"])
    first = next(s for s in front["screens"] if s["primary"])
    lines = [f"# 화면 설계 — {spec['repo']}", "", f"> 첫 화면은 {o['first_screen']}", "",
             f"## 첫 화면 {first['id']}{mark(first['id'])} {place(first)}", "", first["purpose"], "",
             *[f"![{first['id']} {v}]({shot(first['id'], v)})" for v in shown], "",
             "## 기능을 둔 자리", "", "| 기능 | 주인이 고른 칸 | 놓인 화면 |", "| --- | --- | --- |"]
    where = {f["id"]: [s["id"] for s in front["screens"] if f["id"] in s["features"]] for f in feats}
    lines += [f"| {_cell(f['name'])} (`{f['id']}`) | {CLASS_NAMES[frequency[f['id']]]} | "
              f"{', '.join(where[f['id']]) or '없음'} |" for f in feats]
    lines += ["", "## 다른 화면", ""]
    for s in front["screens"]:
        if s is not first:
            lines += [f"### {s['id']}{mark(s['id'])} {place(s)} — {s['purpose']}", "",
                      *[f"![{s['id']} {v}]({shot(s['id'], v)})" for v in shown], ""]
    lines += ["## 색 배합 (하나를 고른다)", "", "| id | 이름 | " + " | ".join(COLORS) + " |",
              "| --- | --- | " + " | ".join("---" for _ in COLORS) + " |"]
    chosen = (o.get("r2") or {}).get("palette")
    lines += [f"| {p['id']}{' (고름)' if p['id'] == chosen else ''} | {_cell(p['name'])} | "
              + " | ".join(f"`{p['colors'][k]}`" for k in COLORS) + " |" for p in front["tokens"]["palettes"]]
    reviewed = "핵심 리뷰" if "device" in o["profile"] else "백엔드 리뷰"
    lines += ["", f"<details><summary>화면마다의 상태, {reviewed}</summary>", ""]
    for s in front["screens"]:
        st = front["states"][s["id"]]
        lines.append(f"- **{s['id']}.states**{mark(s['id'] + '.states')} 비었을 때: {st['empty']} / 불러오는 중: "
                     f"{st['loading']} / 오류: {st['error']} / 찼을 때: {st['full']}")
    review = (o.get("reviews") or [{}])[-1]
    lines += ["", f"- {reviewed}: {review.get('verdict', '없음')} (`{str(review.get('head', ''))[:12]}`), "
                  f"발견 {len(review.get('findings') or [])}개", "", "</details>", ""]
    return "\n".join(lines)
