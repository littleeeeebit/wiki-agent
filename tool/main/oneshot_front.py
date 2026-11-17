"""oneshot S5–R2: the feature split read from the approved API, the owner's board, the frontend design
shot as screenshots, and the R2 sheet's decisions.

`docs/plans/oneshot/4-frontend.md` ("S5", "S6", "R2") is the design; `oneshot_screens` holds its checks.
The stages run on the spec's store, after the backend review approved:
`split → board → frontend → r2 → build-front`. `build-front` is where the frontend build begins.
Both model turns are read-only; the server writes and shoots the mock pages in the run's folder.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import is_junction

from . import oneshot, oneshot_android, oneshot_brief as brief, oneshot_build, oneshot_front_build, \
    oneshot_screens as screens, oneshot_sheet as sheet, oneshot_spec, runtime, specs
from .oneshot_profile import DEVICES, kinds_of, shape, waits_at

SHOT_SECONDS = 15_000   # Playwright's milliseconds per page
PREVIEW_SECONDS = 1800   # an Android preview's first run downloads its Gradle dependencies
SHOT_NAME = re.compile(r"S[1-9]\d*-(desktop|phone)\.png")

router = APIRouter()


# -- S5 the feature split ---------------------------------------------------------------------

def served(spec: dict) -> list[str]:
    """Every operation of the API the owner approved at R1, which the contract tests exercise: the server's
    own copy of the design, never a file the build could have edited, and no framework of its own."""

    return screens.operations(json.loads(sheet.openapi(spec)))


def inventory(spec: dict) -> list[str]:
    """What the features are read from: the served API, or an approved core — every module in the server's own
    copy of the design, a web app's endpoints when several kinds share it, and every requirement with a
    platform capability."""

    if shape(spec["oneshot"]) == "webapp":
        return served(spec)
    design = spec["oneshot"]["design"]
    return [m["id"] for m in design["modules"]] + [p["id"] for p in design.get("api", [])] + \
        [r["id"] for r in spec.get("requirements") or [] if r.get("capability")]


def part_kinds(spec: dict) -> dict[str, list[str]]:
    """Each core part with the kinds that use it, in the order the owner picked them: a module or endpoint
    by `sheet.reach`, a capability by its requirement's kinds."""

    o, reqs = spec["oneshot"], spec.get("requirements") or []
    kinds = kinds_of(o)
    reached = {k: sheet.reach(o["design"], reqs, k) for k in kinds}
    out = {i: [k for k in kinds if i in reached[k]] for i in inventory(spec)}
    return out | {r["id"]: [k for k in kinds if k in (r.get("kinds") or kinds)] for r in reqs if r.get("capability")}


def with_kinds(found: list[dict], parts: dict[str, list[str]]) -> list[dict]:
    """Each feature's `kinds`: those every one of its parts applies to. A feature whose parts share none is
    refused, so the split turn groups again."""

    out = []
    for f in found:
        kinds = [k for k in parts[f["parts"][0]] if all(k in parts[p] for p in f["parts"])]
        if not kinds:
            raise ValueError(f"기능 `{f['id']}` 의 부분이 함께 걸린 종류가 없다 — " + ", ".join(
                f"{p}({'·'.join(sheet.KIND_NAMES[k] for k in parts[p]) or '없음'})" for p in f["parts"]))
        out.append({**f, "kinds": kinds})
    return out


def listed(spec: dict, ops: list[str]) -> str:
    if shape(spec["oneshot"]) == "webapp":
        return "Every operation of the approved API, which the built backend serves:\n\n" + \
            "\n".join(f"- `{op}`" for op in ops) + "\n"
    design = spec["oneshot"]["design"]
    said = {m["id"]: f"{m['name']} — {m['contract']}" for m in design["modules"]}
    said |= {p["id"]: f"`{p['method']} {p['path']}` — {p['does']}" for p in design.get("api", [])}
    said |= {r["id"]: f"`{r['capability']['permission']}` — {r['text']}" for r in spec.get("requirements") or []
             if r.get("capability")}
    used = part_kinds(spec) if shape(spec["oneshot"]) == "kinds" else {}
    return "Every part of the approved core, its modules and its platform capabilities:\n\n" + \
        "\n".join(f"- `{i}` {said[i]}" + (f" (used by: {', '.join(used[i]) or 'none'})" if used else "")
                  for i in ops) + "\n"


def split(repo: str) -> None:
    """One read-only turn: each served operation named in the owner's words, grouped, and given a class.
    It holds the worktree from reading the app until the features are saved (`oneshot_review.holding`)."""

    review().holding(repo, "기능 나누기", lambda store, spec, tree: split_turn(repo, store, spec))


def split_turn(repo: str, store, spec: dict):
    o = spec["oneshot"]
    try:
        ops = inventory(spec)
    except (RuntimeError, ValueError, OSError) as exc:
        store.stop("block", str(exc))
        return None
    kind = shape(o)
    text = (f"The owner's sentence:\n\n{o['sentence']}\n\nThe confirmed restatement:\n\n"
            f"{json.dumps(o['intent'], ensure_ascii=False)}\n\nThe requirements:\n\n"
            f"{json.dumps(spec.get('requirements') or [], ensure_ascii=False)}\n\nThe glossary:\n\n"
            f"{json.dumps(o.get('glossary') or [], ensure_ascii=False)}\n\n" + listed(spec, ops))
    key = "endpoints" if kind == "webapp" else "parts"
    parts = part_kinds(spec) if kind == "kinds" else None
    found = oneshot.turned(store, "split", oneshot.prompt("split", kind), text, "oneshot-features",
                           lambda value: (lambda f: with_kinds(f, parts) if parts else f)(screens.features(value, ops, key)),
                           Path(spec["worktree"]))
    if found is None:
        return None
    first = o.get("first_screen") or o["intent"]["first_screen"]
    if kind == "kinds" and isinstance(first, str):   # one line for each kind, each the restatement's to start
        first = {k: first for k in kinds_of(o)}
    return lambda: oneshot_spec.saved(repo, oneshot={
        # a design and R2 decisions left from an earlier split were placed on features that are gone
        "stage": "board", "working": None, "inventory": ops, "features": found, "frequency": None,
        "front": None, "r2": None, "first_screen": first})


def review():
    from . import oneshot_review   # it imports this module

    return oneshot_review


# -- S6 the frontend design -------------------------------------------------------------------

views = screens.views


def shoot(mocks: dict[str, str], folder: Path, viewports: list[str], clear: bool = True) -> dict[str, list[str]]:
    """Each mock written into `folder` and shot at each viewport, offline: a request leaving the page is
    aborted and counted. `{screen: [console errors, page errors, outside requests]}`, empty when clean.
    `clear` empties the folder first; several kinds keep the Android pictures already in it."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright 가 없어 목업을 찍지 못했다 — `pip install playwright` 와 "
                           "`playwright install chromium` 이 필요하다") from exc
    if clear:
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    errors: dict[str, list[str]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for sid, html in mocks.items():
                page_file = folder / f"{sid}.html"
                page_file.write_text(html, encoding="utf-8", newline="\n")
                for view in viewports:
                    found = errors.setdefault(sid, [])
                    shot_one(browser, page_file, view, found)
        finally:
            browser.close()
    return {sid: found for sid, found in errors.items() if found}


def shot_one(browser, page_file: Path, view: str, found: list[str]) -> None:
    width, height = screens.VIEWPORTS[view]
    # The routes sit on the context, so a popup's first request is filtered too; a WebSocket is never opened.
    context = browser.new_context(viewport={"width": width, "height": height})
    own = unquote(page_file.as_uri())

    def routed(route) -> None:   # the mock itself and inline data; any other file is as outside as the network
        url = route.request.url
        if url.startswith("data:") or unquote(url) == own:
            route.continue_()
        else:
            found.append(f"{'로컬 파일' if url.startswith('file:') else '외부'} 요청 {url[:120]}")
            route.abort()

    def socket(ws) -> None:   # a routed socket never reaches its server; closing it here blocks the shot
        found.append(f"WebSocket 요청 {ws.url[:120]}")

    try:
        context.route("**/*", routed)
        context.route_web_socket(re.compile(".*"), socket)
        page = context.new_page()
        context.on("page", lambda popup: found.append(f"새 창 {popup.url[:120]}"))
        page.on("console", lambda m: m.type == "error" and found.append(f"콘솔 오류 {m.text[:200]}"))
        page.on("pageerror", lambda e: found.append(f"페이지 오류 {str(e)[:200]}"))
        page.goto(page_file.as_uri(), wait_until="load", timeout=SHOT_SECONDS)
        page.screenshot(path=str(page_file.with_name(f"{page_file.stem}-{view}.png")), full_page=True)
    finally:
        context.close()


def unpacked(tree: Path, into: Path) -> None:
    """The worktree's HEAD as plain files in `into`: no `.git`, and no link leading out of it."""

    done = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=tree, capture_output=True, timeout=300)
    if done.returncode:
        raise ValueError(f"git archive 실패 — {done.stderr.decode(errors='replace').strip()[-300:]}")
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        tar.extractall(into, filter="data")


def shared(preview: dict) -> dict:
    """What no R2 item holds: the preview's files other than the screens' own, its command and its folder."""

    own = set(preview["screens"].values())
    return {"files": {p: t for p, t in preview["files"].items() if p not in own},
            "command": preview["command"], "shots": preview["shots"]}


def photographed(spec: dict, preview: dict, folder: Path) -> None:
    """An Android app's mocks drawn by the stack's own preview tool, with no emulator: the reviewed HEAD unpacked
    into the run's folder, the mock files added and the preview command run on the card's toolchain. Only the
    pictures stay, `<folder>/<screen>-phone.png`, each at the reference phone's size.

    The command is model-written code run with the server's rights, as S4's contract tests and gate are; the
    scratch folder is not a sandbox (`docs/plans/oneshot/7-android.md`, "How 7c builds it")."""

    if not oneshot_build.chained(preview["command"]):
        raise ValueError(f"`oneshot-mocks` 의 command 는 {oneshot_build.CHAIN}")
    device = DEVICES[spec["oneshot"]["profile"]["device"]]
    try:   # a missing toolchain refuses before anything is unpacked
        env = oneshot_android.build_env()
    except OSError as exc:
        raise ValueError(f"Android 도구를 읽지 못했다 — {exc}") from exc
    shutil.rmtree(folder, ignore_errors=True)
    project = folder / "project"
    project.mkdir(parents=True)
    try:
        unpacked(Path(spec["worktree"]), project)
        root = project.resolve()
        for name, text in preview["files"].items():
            target = project / name
            if not target.resolve().is_relative_to(root):
                raise ValueError(f"목업 파일 자리가 프로젝트 밖을 가리킨다: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        # A first run can download quietly for long: only the deadline cuts it, never a silent spell.
        code, out, cut = specs.gate(preview["command"], project, runtime.stopping, env=env, timeout=PREVIEW_SECONDS,
                                    idle=PREVIEW_SECONDS)
        if cut or code:
            raise ValueError(f"화면 사진 명령이 {f'끊겼다 ({cut})' if cut else f'{code} 로 끝났다'} — {out.strip()[-800:]}")
        problems = []
        for sid in preview["screens"]:
            picture = project / preview["shots"] / f"{sid}.png"
            data = picture.read_bytes() if picture.is_file() else b""
            found = screens.picture(data, screens.largest(device))
            if not found or not screens.sized(found[0], device):
                problems.append(f"{sid}: `{preview['shots']}/{sid}.png` 가 " + (
                    f"{found[0][0]}×{found[0][1]}px 다" if found else "기준 폰보다 크지 않은 온전한 PNG 로 없다"))
            else:   # the picture as checked: no chunk the checks did not read
                (folder / f"{sid}-phone.png").write_bytes(found[1])
        if problems:
            raise ValueError(f"화면 사진이 기준 폰 {device['width']}×{device['height']}px 로 찍히지 않았다 — " +
                             " / ".join(problems) + f" — 명령 출력: {out.strip()[-400:]}")
    finally:
        shutil.rmtree(project, ignore_errors=True)


def front_turn(final: str, attempt: int, spec: dict, reach: set[str] | None = None) -> tuple[dict, list[str]]:
    """The four S6 blocks checked against the owner's map, outside an edit's reach unchanged, then shot."""

    o = spec["oneshot"]
    kinds = kinds_of(o) if shape(o) == "kinds" else []
    if kinds:
        front = screens.design_kinds(oneshot_spec.blocks(final), o["features"], o["frequency"], kinds, [
            f"{p['method'].upper()} {p['path']}" for p in o["design"].get("api", [])])
    else:
        front = screens.design(oneshot_spec.blocks(final), o["features"], o["frequency"], shape(o) == "android")
    android = "preview" in front
    if reach is not None:   # an approval outside the reach survives, so nothing outside it may move
        old, new = screens.every(o["front"]), screens.every(front)
        if moved := sorted(i for i in old if i not in reach and old[i] != new.get(i)):
            raise ValueError(f"고치라고 한 범위 밖 항목을 바꾸거나 지웠다: {', '.join(moved)} — 그대로 두어라")
        if front["tokens"] != o["front"]["tokens"]:   # no item holds them: the owner's palette stands as chosen
            raise ValueError("화면을 고치는 턴이 색 배합·글자 크기·간격(`tokens`)을 바꿨다 — 그대로 두어라")
        if android and shared(front["preview"]) != shared(o["front"]["preview"]):
            raise ValueError("화면을 고치는 턴이 화면 밖의 미리보기 파일·명령·사진 폴더를 바꿨다 — 그대로 두어라")
    warnings = screens.soft(front)
    if warnings and attempt == 0:
        raise ValueError("다음을 고쳐 다시 내라 — " + " / ".join(warnings[:12]))
    n = int(o.get("shots") or 0) + 1
    if android:   # first: it empties the folder the web mocks are then shot into
        photographed(spec, front["preview"], shots(spec["repo"], n))
    web = {s["id"]: front["mocks"][s["id"]] for s in front["screens"] if s.get("kind", "webapp") == "webapp"} \
        if not android or kinds else {}
    if web and (broken := shoot(web, shots(spec["repo"], n), views(o["profile"], "webapp" if kinds else ""),
                                clear=not android)):
        raise ValueError("목업이 깨끗하게 열리지 않는다 (외부 자원 없이 한 파일로 써라) — " +
                         " / ".join(f"{sid}: {'; '.join(found[:3])}" for sid, found in broken.items()))
    return {**front, "shots": n}, warnings


def shots(repo: str, n: int) -> Path:
    return oneshot_spec.store(repo).folder / "front" / f"r{n}"


def front_text(spec: dict) -> str:
    o = spec["oneshot"]
    if shape(o) in ("android", "kinds"):
        device = DEVICES[o["profile"]["device"]]
        designed, parts = "core", "core parts" + (" and the kinds it applies to" if shape(o) == "kinds" else "")
        shot = (f"The reference phone each screen is shot at: {device['label']}, {device['width']}×{device['height']} px "
                f"at {device['dpi']} dpi.\n\nThe toolchain the preview command runs on:\n\n"
                f"{oneshot_build.toolchain_text(spec)}\n")
        if shape(o) == "kinds":
            shot = f"The web viewports shot: {', '.join(views(o['profile'], 'webapp'))}\n\n" + shot
    else:
        designed, parts, shot = "backend", "served endpoints", f"Viewports shot: {', '.join(views(o['profile']))}\n"
    return (oneshot_spec.design_text(spec) + f"\nThe {designed} design:\n\n{json.dumps(o['design'], ensure_ascii=False)}"
            f"\n\nThe features, each with its {parts}:\n\n{json.dumps(o['features'], ensure_ascii=False)}"
            f"\n\nThe owner's frequency map:\n\n{json.dumps(o['frequency'], ensure_ascii=False)}"
            f"\n\nThe first screen is for: " + (json.dumps(o["first_screen"], ensure_ascii=False)   # one line per kind
                                                if isinstance(o["first_screen"], dict) else o["first_screen"]) + "\n\n" + shot)


def frontend(repo: str) -> None:
    """One S6 turn in the worktree, read-only; the server checks the blocks, shoots the mocks and shows R2."""

    def body(store, spec, tree):
        found = oneshot.turned(store, "frontend", oneshot.prompt("frontend", shape(spec["oneshot"])), front_text(spec),
                               screens.BLOCKS, lambda final, attempt: front_turn(final, attempt, spec), tree)
        if found is None:
            return None
        front, warnings = found
        return lambda: oneshot_spec.briefing(repo, "r2", {
            "front": front, "shots": front["shots"], "stage": "r2",
            "r2": {"round": 1, "decisions": {}, "marks": {}, "warnings": warnings, "palette": ""}})

    review().holding(repo, "화면 설계", body)


def refront(repo: str, comments: dict[str, str], reach: list[str]) -> None:
    """The owner's edits and what depends on them, regenerated in one turn and checked whole."""

    review().holding(repo, "화면 수정", lambda store, spec, tree: refront_turn(repo, store, spec, tree, comments, reach))


def refront_turn(repo: str, store, spec: dict, tree: Path, comments: dict[str, str], reach: list[str]):
    o = spec["oneshot"]
    # An Android design's per-screen sources are its preview's files, so they are sent once; several kinds keep
    # the web screens' pages, which nothing else holds.
    current = {k: v for k, v in o["front"].items() if k != "shots"}
    if "preview" in current:
        web = {s["id"]: current["mocks"][s["id"]] for s in current["screens"] if s.get("kind") == "webapp"}
        current = {**current, "mocks": {"webapp": web}} if web else {k: v for k, v in current.items() if k != "mocks"}
    text = (front_text(spec) + f"\nThe current frontend design:\n\n"
            f"{json.dumps(current, ensure_ascii=False)}"
            f"\n\nThe owner edited these items, with these comments:\n\n{json.dumps(comments, ensure_ascii=False)}"
            f"\n\nReplace every item in this set and keep the rest as it is: {', '.join(reach)}. "
            "Return all four blocks in full.\n")
    found = oneshot.turned(store, "frontend", oneshot.prompt("frontend", shape(o)), text, screens.BLOCKS,
                           lambda final, attempt: front_turn(final, attempt, spec, set(reach)), tree)
    if found is None:
        return None
    front, warnings = found
    r2 = o["r2"]
    marks, decisions = oneshot_spec.marked(screens.decidable(front), comments, reach, screens.every(o["front"]),
                                           screens.every(front), r2["decisions"])
    palette = r2["palette"] if r2["palette"] in {p["id"] for p in front["tokens"]["palettes"]} else ""
    return lambda: oneshot_spec.briefing(repo, "r2", {
        "front": front, "shots": front["shots"],
        "r2": {"round": r2["round"] + 1, "decisions": decisions, "marks": marks, "warnings": warnings,
               "palette": palette}})


# -- R2 approved ------------------------------------------------------------------------------

def approved(repo: str) -> None:
    """Every item approved and a palette chosen: the frontend design, its mocks and shots committed.
    The commit writes the worktree, so it holds it as a turn does, on the HEAD the build passed: a tree
    another turn holds refuses the approval (409, the decisions kept), and a moved one goes back to the
    build's checks before anything is written. The build turn that follows sends the queued instruction
    when it ends."""

    review().approval(repo, "R2 승인", lambda store, spec, tree: record(repo, store, spec, tree))


def record(repo: str, store, spec: dict, tree: Path) -> bool:
    o = spec["oneshot"]
    front, source = o["front"], shots(repo, o["front"]["shots"])
    # An Android app's mocks are its preview's source files, written as they were shot; a web screen's is its page.
    # Several kinds keep each kind's under its own folder, so a preview file never lands on a web page's place.
    several = any("kind" in s for s in front["screens"])
    android, web = ("android/", "webapp/") if several else ("", "")
    mocks = {f"docs/design/mock/{android}{p}": text for p, text in front["preview"]["files"].items()} \
        if "preview" in front else {}
    mocks |= {f"docs/design/mock/{web}{s['id']}.html": source / f"{s['id']}.html" for s in front["screens"]
              if s.get("kind", "webapp") == "webapp" and "route" in s}
    copies = {f"docs/design/{'screens' if 'preview' in front else 'frontend'}.md": None, **mocks,
              **{f"docs/design/shots/{s['id']}-{v}.png": source / f"{s['id']}-{v}.png"
                 for s in front["screens"] for v in views(o["profile"], s.get("kind", ""))}}
    root = tree.resolve()
    for name in copies:   # a link or a junction on the way, or a place that resolves outside the tree
        parts = Path(name).parts
        steps = [tree.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
        if any(p.is_symlink() or is_junction(p) for p in steps) or not (tree / name).resolve().is_relative_to(root):
            store.stop("folder", f"설계 문서 자리가 작업트리 밖을 가리킨다: {name}")
            return False
    for name, src in copies.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        if src is None or isinstance(src, str):
            text = screens.sheet(spec, lambda sid, v: f"shots/{sid}-{v}.png") if src is None else src
            (tree / name).write_text(text, encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(src, tree / name)
    head = oneshot_spec.commit(repo, tree, list(copies), f"Record the approved frontend design (R2 round {o['r2']['round']})")
    if not head:
        return False
    # `working` names the build before its turn is sent: a server that dies in between leaves it for `recover`,
    # and the screens are already the spec's `done` that the resumed turn reports against.
    oneshot_spec.saved(repo, done=[f"{s['id']} {screens.place(s)}: {s['purpose']}" for s in front["screens"]],
                       oneshot={"stage": "build-front", "front_head": head, "working": "build-front", "attempts": 0})
    return True


oneshot_spec.WORKERS.update(split=split, frontend=frontend, refront=refront)
oneshot_spec.RUNNING.update(split="split", frontend="frontend", r2="refront")
oneshot_spec.SHEETS.add("r2")


# -- HTTP ---------------------------------------------------------------------------------------

class Board(BaseModel):
    frequency: dict[str, str]
    first_screen: str | dict[str, str]   # one line for each kind when several share the run


class R2(BaseModel):
    decisions: dict[str, dict] = {}
    palette: str = ""
    reopen: bool = False
    reading: dict = {}
    source: str = ""   # as on R1 (`oneshot_spec.Decisions`)
    lines: dict[str, dict] = {}
    rest: bool = False


def extra(spec: dict) -> dict:
    o, out = spec["oneshot"], {}
    if o.get("features"):
        out["board"] = {"features": o["features"], "first_screen": o.get("first_screen"),
                        "frequency": o.get("frequency") or {f["id"]: f["class"] for f in o["features"]},
                        **({"kinds": [{"id": k, "name": sheet.KIND_NAMES[k]} for k in kinds_of(o)]}
                           if shape(o) == "kinds" else {})}
    if o.get("front"):
        n = o["front"]["shots"]
        shot = lambda sid, v: f"/api/oneshot/spec/{spec['repo']}/shot/{n}/{sid}-{v}.png"   # noqa: E731
        out["front_sheet"] = screens.sheet(spec, shot)
        out["front_items"] = screens.decidable(o["front"])
        # The brief's pictures sit with each screen's confirmation line.
        out["front_brief"] = {**brief.shown(spec, "r2"),
                              "pictures": {s["id"]: [shot(s["id"], v) for v in views(o["profile"], s.get("kind", ""))]
                                           for s in o["front"]["screens"]}}
    return out


oneshot_spec.VIEWS.append(extra)


@router.post("/api/oneshot/spec/{repo}/board")
def submit_board(repo: str, body: Board) -> dict:
    """The owner's classes and first-screen line; the frontend design starts from them, from scratch."""

    with oneshot._lock:
        o = oneshot_spec.spec_of(repo)["oneshot"]
        oneshot_spec.idle(o, "board")
        try:
            frequency, first = screens.board(o["features"], body.frequency, body.first_screen,
                                             kinds_of(o) if shape(o) == "kinds" else ())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if waits_at(o) == "frontend":   # a kind S6 cannot design yet (`desktop`, plan 9e)
            oneshot_spec.saved(repo, oneshot={"stage": "frontend", "frequency": frequency, "first_screen": first,
                                              "front": None, "r2": None, "working": None, "stopped": {
                                                  "reason": "kind", "state": "frontend",
                                                  "detail": "여러 종류의 화면 설계는 다음 버전에서 이어진다 — 기능 보드를 받았다"}})
        else:
            oneshot_spec.start(repo, "frontend", stage="frontend", frequency=frequency, first_screen=first,
                               front=None, r2=None)
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/r2")
def decide(repo: str, body: R2) -> dict:
    """Every item decided at once and a palette picked; or the board reopened, which discards the design."""

    with oneshot._lock:
        spec = oneshot_spec.spec_of(repo)
        o = spec["oneshot"]
        oneshot_spec.idle(o, "r2")
        items = screens.decidable(o["front"])
        given, briefed = oneshot_spec.on_brief(spec, "r2", items, body)   # a stale tab reopens nothing either
        if body.reopen:   # the placement was wrong, not the screen: every S6 result and R2 decision goes
            return oneshot_spec.view(oneshot_spec.saved(repo, oneshot={"stage": "board", "front": None, "r2": None}))
        decisions, comments = oneshot_spec.decided(items, o["r2"]["decisions"], given)
        palette = body.palette or o["r2"]["palette"]
        if palette and palette not in {p["id"] for p in o["front"]["tokens"]["palettes"]}:
            raise HTTPException(400, f"없는 색 배합이다: {palette}")
        r2 = {**o["r2"], "decisions": decisions, "palette": palette}
        reading = oneshot_spec.read(o, "r2", body.reading, comments, briefed)
        if comments:
            reach = sorted(screens.dependents(set(comments)))
            oneshot_spec.start(repo, "refront", comments, reach, r2=r2, reading=reading)
            return oneshot_spec.view(oneshot_spec.spec_of(repo))
        if not palette:
            raise HTTPException(400, "색 배합을 하나 골라라")
        oneshot_spec.saved(repo, oneshot={"r2": r2, "reading": reading})
        approved(repo)
    after = oneshot_spec.spec_of(repo)["oneshot"]
    if after["stage"] == "build-front" and not after.get("stopped"):   # outside the lock: the turn takes the worktree's hold
        oneshot_front_build.begin(repo)
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.get("/api/oneshot/spec/{repo}/shot/{n}/{name}")
def shot(repo: str, n: int, name: str) -> FileResponse:
    oneshot_spec.spec_of(repo)   # a repository with a oneshot spec, never a path
    path = SHOT_NAME.fullmatch(name) and oneshot_spec.within(oneshot_spec.store(repo).folder, "front", f"r{n}", name)
    if not path or not path.is_file():   # a link or a junction on the way never serves a file outside the run
        raise HTTPException(404, "그런 화면 사진이 없다")
    return FileResponse(path, media_type="image/png")
