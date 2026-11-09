"""oneshot S5–R2: the feature split read from the served API, the owner's board, the frontend design
shot as screenshots, and the R2 sheet's decisions.

`docs/plans/oneshot/4-frontend.md` ("S5", "S6", "R2") is the design; `oneshot_screens` holds its checks.
The stages run on the spec's store, after the backend review approved:
`split → board → frontend → r2 → build-front`. `build-front` is where the frontend build begins.
Both model turns are read-only; the server writes and shoots the mock pages in the run's folder.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import is_junction

from . import oneshot, oneshot_front_build, oneshot_screens as screens, oneshot_spec, runtime, specs, work
from .oneshot_profile import python_cmd
from .query import ROOT, _lock as work_lock, hold

SPLIT_PROMPT = (ROOT / "tool/prompts/oneshot-split.md").read_text(encoding="utf-8")
FRONTEND_PROMPT = (ROOT / "tool/prompts/oneshot-frontend.md").read_text(encoding="utf-8")
SERVED_SECONDS = 120
SHOT_SECONDS = 15_000   # Playwright's milliseconds per page
SHOT_NAME = re.compile(r"S[1-9]\d*-(desktop|phone)\.png")
# Run by the project's own interpreter in the worktree: the app's schema exactly as FastAPI serves it.
SERVED = ("import json, sys\nsys.path.insert(0, '.')\nfrom app.main import app\n"
          "print('OPENAPI ' + json.dumps(app.openapi()))\n")

router = APIRouter()


# -- S5 the feature split ---------------------------------------------------------------------

def served(spec: dict) -> list[str]:
    """Every operation the built backend serves, read from its OpenAPI document, never written by a model."""

    store, tree = oneshot_spec.store(spec["repo"]), Path(spec["worktree"])
    store.folder.mkdir(parents=True, exist_ok=True)
    script = store.folder / "served.py"
    script.write_text(SERVED, encoding="utf-8", newline="\n")
    code, out, cut = specs.gate(f'{python_cmd(spec["oneshot"]["profile"])} "{script.as_posix()}"', tree,
                                runtime.stopping, timeout=SERVED_SECONDS)
    line = next((x for x in out.splitlines() if x.startswith("OPENAPI ")), "")
    if code != 0 or not line:
        raise RuntimeError(f"서버의 OpenAPI 문서를 읽지 못했다 ({cut or code}) — {out.strip()[-600:]}")
    return screens.operations(json.loads(line.removeprefix("OPENAPI ")))


def split(repo: str) -> None:
    """One read-only turn: each served operation named in the owner's words, grouped, and given a class.
    It holds the worktree from reading the app until the features are saved (`oneshot_review.holding`)."""

    review().holding(repo, "기능 나누기", lambda store, spec, tree: split_turn(repo, store, spec))


def split_turn(repo: str, store, spec: dict):
    o = spec["oneshot"]
    try:
        ops = served(spec)
    except (RuntimeError, ValueError, OSError) as exc:
        store.stop("block", str(exc))
        return None
    text = (f"The owner's sentence:\n\n{o['sentence']}\n\nThe confirmed restatement:\n\n"
            f"{json.dumps(o['intent'], ensure_ascii=False)}\n\nThe requirements:\n\n"
            f"{json.dumps(spec.get('requirements') or [], ensure_ascii=False)}\n\nThe glossary:\n\n"
            f"{json.dumps(o.get('glossary') or [], ensure_ascii=False)}\n\nEvery operation the built backend "
            f"serves, from its OpenAPI document:\n\n" + "\n".join(f"- `{op}`" for op in ops) + "\n")
    found = oneshot.turned(store, "split", SPLIT_PROMPT, text, "oneshot-features",
                           lambda value: screens.features(value, ops), Path(spec["worktree"]))
    if found is None:
        return None
    return lambda: oneshot_spec.saved(repo, oneshot={
        # a design and R2 decisions left from an earlier split were placed on features that are gone
        "stage": "board", "working": None, "inventory": ops, "features": found, "frequency": None,
        "front": None, "r2": None,
        "first_screen": o.get("first_screen") or o["intent"]["first_screen"]})


def review():
    from . import oneshot_review   # it imports this module

    return oneshot_review


# -- S6 the frontend design -------------------------------------------------------------------

def views(profile: dict) -> list[str]:
    return list(screens.VIEWPORTS) if profile.get("responsive") == "desktop-mobile" else ["desktop"]


def shoot(mocks: dict[str, str], folder: Path, viewports: list[str]) -> dict[str, list[str]]:
    """Each mock written into `folder` and shot at each viewport, offline: a request leaving the page is
    aborted and counted. `{screen: [console errors, page errors, outside requests]}`, empty when clean."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright 가 없어 목업을 찍지 못했다 — `pip install playwright` 와 "
                           "`playwright install chromium` 이 필요하다") from exc
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
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


def front_turn(final: str, attempt: int, spec: dict, reach: set[str] | None = None) -> tuple[dict, list[str]]:
    """The four S6 blocks checked against the owner's map, outside an edit's reach unchanged, then shot."""

    o = spec["oneshot"]
    front = screens.design(oneshot_spec.blocks(final), o["features"], o["frequency"])
    if reach is not None:   # an approval outside the reach survives, so nothing outside it may move
        old, new = screens.every(o["front"]), screens.every(front)
        if moved := sorted(i for i in old if i not in reach and old[i] != new.get(i)):
            raise ValueError(f"고치라고 한 범위 밖 항목을 바꾸거나 지웠다: {', '.join(moved)} — 그대로 두어라")
        if front["tokens"] != o["front"]["tokens"]:   # no item holds them: the owner's palette stands as chosen
            raise ValueError("화면을 고치는 턴이 색 배합·글자 크기·간격(`tokens`)을 바꿨다 — 그대로 두어라")
    warnings = screens.soft(front)
    if warnings and attempt == 0:
        raise ValueError("다음을 고쳐 다시 내라 — " + " / ".join(warnings[:12]))
    n = int(o.get("shots") or 0) + 1
    if broken := shoot(front["mocks"], shots(spec["repo"], n), views(o["profile"])):
        raise ValueError("목업이 깨끗하게 열리지 않는다 (외부 자원 없이 한 파일로 써라) — " +
                         " / ".join(f"{sid}: {'; '.join(found[:3])}" for sid, found in broken.items()))
    return {**front, "shots": n}, warnings


def shots(repo: str, n: int) -> Path:
    return oneshot_spec.store(repo).folder / "front" / f"r{n}"


def front_text(spec: dict) -> str:
    o = spec["oneshot"]
    return (oneshot_spec.design_text(spec) + f"\nThe backend design:\n\n{json.dumps(o['design'], ensure_ascii=False)}"
            f"\n\nThe features, each with its served endpoints:\n\n{json.dumps(o['features'], ensure_ascii=False)}"
            f"\n\nThe owner's frequency map:\n\n{json.dumps(o['frequency'], ensure_ascii=False)}"
            f"\n\nThe first screen is for: {o['first_screen']}\n\nViewports shot: {', '.join(views(o['profile']))}\n")


def frontend(repo: str) -> None:
    """One S6 turn in the worktree, read-only; the server checks the blocks, shoots the mocks and shows R2."""

    def body(store, spec, tree):
        found = oneshot.turned(store, "frontend", FRONTEND_PROMPT, front_text(spec), screens.BLOCKS,
                               lambda final, attempt: front_turn(final, attempt, spec), tree)
        if found is None:
            return None
        front, warnings = found
        return lambda: oneshot_spec.saved(repo, oneshot={
            "front": front, "shots": front["shots"], "stage": "r2", "working": None,
            "r2": {"round": 1, "decisions": {}, "marks": {}, "warnings": warnings, "palette": ""}})

    review().holding(repo, "화면 설계", body)


def refront(repo: str, comments: dict[str, str], reach: list[str]) -> None:
    """The owner's edits and what depends on them, regenerated in one turn and checked whole."""

    review().holding(repo, "화면 수정", lambda store, spec, tree: refront_turn(repo, store, spec, tree, comments, reach))


def refront_turn(repo: str, store, spec: dict, tree: Path, comments: dict[str, str], reach: list[str]):
    o = spec["oneshot"]
    text = (front_text(spec) + f"\nThe current frontend design:\n\n"
            f"{json.dumps({k: v for k, v in o['front'].items() if k != 'shots'}, ensure_ascii=False)}"
            f"\n\nThe owner edited these items, with these comments:\n\n{json.dumps(comments, ensure_ascii=False)}"
            f"\n\nReplace every item in this set and keep the rest as it is: {', '.join(reach)}. "
            "Return all four blocks in full.\n")
    found = oneshot.turned(store, "frontend", FRONTEND_PROMPT, text, screens.BLOCKS,
                           lambda final, attempt: front_turn(final, attempt, spec, set(reach)), tree)
    if found is None:
        return None
    front, warnings = found
    r2 = o["r2"]
    marks, decisions = oneshot_spec.marked(screens.decidable(front), comments, reach, screens.every(o["front"]),
                                           screens.every(front), r2["decisions"])
    palette = r2["palette"] if r2["palette"] in {p["id"] for p in front["tokens"]["palettes"]} else ""
    return lambda: oneshot_spec.saved(repo, oneshot={
        "front": front, "shots": front["shots"], "working": None,
        "r2": {"round": r2["round"] + 1, "decisions": decisions, "marks": marks, "warnings": warnings,
               "palette": palette}})


# -- R2 approved ------------------------------------------------------------------------------

def approved(repo: str) -> None:
    """Every item approved and a palette chosen: the frontend design, its mocks and shots committed.
    The commit writes the worktree, so it holds it as a turn does, on the HEAD the build passed: a tree
    another turn holds refuses the approval (409, the decisions kept), and a moved one goes back to the
    build's checks before anything is written."""

    spec, store = oneshot_spec.spec_of(repo), oneshot_spec.store(repo)
    tree = oneshot_spec.own_tree(spec)
    if not tree:
        store.stop("folder", f"작업트리가 `{oneshot.BRANCH}` 브랜치의 그 작업트리가 아니다: {spec['worktree']}")
        return
    release = hold(work._busy, work_lock, str(tree), "작업트리를 다른 턴이 쓰고 있다 — 끝난 뒤 다시 승인하라",
                   kind="turn")
    passed_on = False
    try:
        if why := review().moved(spec, tree):
            review().rebuild(repo, store, f"R2 승인 전에 작업트리가 바뀌었다 — {why}")
        else:
            passed_on = record(repo, store, spec, tree)
    finally:
        release()
        if not passed_on:   # the build turn that follows sends the queued instruction when it ends
            work.dispatch(tree, SimpleNamespace(halt=runtime.stopping, turn=""))


def record(repo: str, store, spec: dict, tree: Path) -> bool:
    o = spec["oneshot"]
    front, source = o["front"], shots(repo, o["front"]["shots"])
    copies = {"docs/design/frontend.md": None,
              **{f"docs/design/mock/{s['id']}.html": source / f"{s['id']}.html" for s in front["screens"]},
              **{f"docs/design/shots/{s['id']}-{v}.png": source / f"{s['id']}-{v}.png"
                 for s in front["screens"] for v in views(o["profile"])}}
    root = tree.resolve()
    for name in copies:   # a link or a junction on the way, or a place that resolves outside the tree
        parts = Path(name).parts
        steps = [tree.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
        if any(p.is_symlink() or is_junction(p) for p in steps) or not (tree / name).resolve().is_relative_to(root):
            store.stop("folder", f"설계 문서 자리가 작업트리 밖을 가리킨다: {name}")
            return False
    for name, src in copies.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        if src is None:
            (tree / name).write_text(screens.sheet(spec, lambda sid, v: f"shots/{sid}-{v}.png"),
                                     encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(src, tree / name)
    head = oneshot_spec.commit(repo, tree, list(copies), f"Record the approved frontend design (R2 round {o['r2']['round']})")
    if not head:
        return False
    # `working` names the build before its turn is sent: a server that dies in between leaves it for `recover`.
    oneshot_spec.saved(repo, oneshot={"stage": "build-front", "front_head": head, "working": "build-front"})
    return True


oneshot_spec.WORKERS.update(split=split, frontend=frontend, refront=refront)
oneshot_spec.RUNNING.update(split="split", frontend="frontend", r2="refront")
oneshot_spec.SHEETS.add("r2")


# -- HTTP ---------------------------------------------------------------------------------------

class Board(BaseModel):
    frequency: dict[str, str]
    first_screen: str


class R2(BaseModel):
    decisions: dict[str, dict] = {}
    palette: str = ""
    reopen: bool = False


def extra(spec: dict) -> dict:
    o, out = spec["oneshot"], {}
    if o.get("features"):
        out["board"] = {"features": o["features"], "first_screen": o.get("first_screen"),
                        "frequency": o.get("frequency") or {f["id"]: f["class"] for f in o["features"]}}
    if o.get("front"):
        n = o["front"]["shots"]
        out["front_sheet"] = screens.sheet(spec, lambda sid, v: f"/api/oneshot/spec/{spec['repo']}/shot/{n}/{sid}-{v}.png")
        out["front_items"] = screens.decidable(o["front"])
    return out


oneshot_spec.VIEWS.append(extra)


@router.post("/api/oneshot/spec/{repo}/board")
def submit_board(repo: str, body: Board) -> dict:
    """The owner's classes and first-screen line; the frontend design starts from them, from scratch."""

    with oneshot._lock:
        o = oneshot_spec.spec_of(repo)["oneshot"]
        oneshot_spec.idle(o, "board")
        try:
            frequency, first = screens.board(o["features"], body.frequency, body.first_screen)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
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
        if body.reopen:   # the placement was wrong, not the screen: every S6 result and R2 decision goes
            return oneshot_spec.view(oneshot_spec.saved(repo, oneshot={"stage": "board", "front": None, "r2": None}))
        decisions, comments = oneshot_spec.decided(screens.decidable(o["front"]), o["r2"]["decisions"], body.decisions)
        palette = body.palette or o["r2"]["palette"]
        if palette and palette not in {p["id"] for p in o["front"]["tokens"]["palettes"]}:
            raise HTTPException(400, f"없는 색 배합이다: {palette}")
        r2 = {**o["r2"], "decisions": decisions, "palette": palette}
        if comments:
            reach = sorted(screens.dependents(set(comments)))
            oneshot_spec.start(repo, "refront", comments, reach, r2=r2)
            return oneshot_spec.view(oneshot_spec.spec_of(repo))
        if not palette:
            raise HTTPException(400, "색 배합을 하나 골라라")
        oneshot_spec.saved(repo, oneshot={"r2": r2})
        approved(repo)
    if oneshot_spec.spec_of(repo)["oneshot"]["stage"] == "build-front":   # outside the lock: the turn takes the worktree's hold
        oneshot_front_build.begin(repo)
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.get("/api/oneshot/spec/{repo}/shot/{n}/{name}")
def shot(repo: str, n: int, name: str) -> FileResponse:
    oneshot_spec.spec_of(repo)   # a repository with a oneshot spec, never a path
    path = shots(repo, n) / name
    if not SHOT_NAME.fullmatch(name) or not path.is_file():
        raise HTTPException(404, "그런 화면 사진이 없다")
    return FileResponse(path, media_type="image/png")
