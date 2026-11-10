"""oneshot S7's critique and R3: a read-only critic that sees the screenshots, the stop rule, and the final sheet.

`docs/plans/oneshot/4-frontend.md` ("Critique", "Fix and repeat", "R3") is the design. The critic is a
separate read-only Claude turn whose screenshots travel in the turn itself (`ChatSession.say(images=...)`);
it never sees the builder's reasoning. A round is clean when neither the harness nor the critic found
anything high. Two clean rounds in a row open R3; six rounds with high findings left stop with `critique`.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import oneshot, oneshot_build, oneshot_front_build as front_build, oneshot_pr, oneshot_review as review, \
    oneshot_screens as screens, oneshot_spec
from .query import ROOT

CRITIC_PROMPT = (ROOT / "tool/prompts/oneshot-critic.md").read_text(encoding="utf-8")
SEVERITIES = ("high", "medium", "low")
STATES = ("full", "empty", "error", "loading")   # the order the critic sees them in
MAX_ROUNDS = 6
MAX_IMAGES = 24
SHOT = re.compile(r"S[1-9]\d*-(desktop|phone)-(empty|full|loading|error)\.png")

router = APIRouter()


def critic() -> dict:
    """The critic's model: Claude, the one provider whose turns carry images."""

    return {"model": "opus", "effort": "high"}


oneshot.ROLES["critic"] = critic


def begin(repo: str) -> None:
    """After a build passed the harness: one critique worker; a start a shutdown refused stays resumable."""

    try:
        oneshot_spec.start(repo, "critique", stage="critique")
    except HTTPException:
        oneshot_spec.store(repo).stop("restart", "서버가 꺼지는 중이라 화면 비평을 시작하지 못했다")


def folder(repo: str, n: int) -> Path:
    return oneshot_spec.store(repo).folder / "build" / f"r{n}"


def pictures(spec: dict, n: int) -> tuple[list[Path], list[str]]:
    """The shots the critic sees, every screen's `full` first, at most `MAX_IMAGES`, and the names of those
    missing or reached through a link: a round the critic cannot see whole is never judged."""

    o, base = spec["oneshot"], oneshot_spec.store(spec["repo"]).folder
    names = [f"{s['id']}-{v}-{state}.png" for state in STATES for s in o["front"]["screens"]
             for v in front_build.views(o["profile"])][:MAX_IMAGES]
    found = {name: oneshot_spec.within(base, "build", f"r{n}", name) for name in names}
    return [p for p in found.values() if p and p.is_file()], [k for k, p in found.items() if not (p and p.is_file())]


def critique_of(value, spec: dict) -> list[dict]:
    """An `oneshot-critique` block, checked: each finding names a screen route, a viewport and a state."""

    o = spec["oneshot"]
    routes, views = {s["route"] for s in o["front"]["screens"]}, front_build.views(o["profile"])
    if not isinstance(value, list):
        raise ValueError("`oneshot-critique` 는 발견의 목록이다 (없으면 빈 목록)")
    out = []
    for n, f in enumerate(value, 1):
        if not isinstance(f, dict) or f.get("severity") not in SEVERITIES:
            raise ValueError(f"{n}번째 발견의 `severity` 는 high, medium, low 중 하나다")
        if f.get("route") not in routes or f.get("viewport") not in views or f.get("state") not in STATES:
            raise ValueError(f"{n}번째 발견의 `route`·`viewport`·`state` 가 찍은 화면에 없다")
        if not all(isinstance(f.get(k), str) and f[k].strip() for k in ("what", "fix")):
            raise ValueError(f"{n}번째 발견에 `what` 과 `fix` 가 있어야 한다")
        out.append({k: f[k].strip() if isinstance(f[k], str) else f[k]
                    for k in ("severity", "route", "viewport", "state", "what", "fix")})
    return out


def critique_text(spec: dict, look: dict, shown: list[Path]) -> str:
    o = spec["oneshot"]
    return ("Judge the built frontend from its screenshots, attached in this order:\n\n"
            + "\n".join(f"{i}. `{p.name}`" for i, p in enumerate(shown, 1))
            + f"\n\nThe approved R2 sheet:\n\n{screens.sheet(spec, lambda sid, v: f'{sid}-{v}-full.png')}\n\n"
            f"The owner's frequency map: {json.dumps(o['frequency'], ensure_ascii=False)}\n\nThe chosen palette: "
            f"{o['r2']['palette']}\n\nThe harness's mechanical findings on these shots:\n\n"
            f"{json.dumps(look['findings'], ensure_ascii=False)}\n")


def critique(repo: str) -> None:
    """One critique round, holding the worktree on the HEAD the build and its shots passed, as the backend
    review does: a tree that moved goes back to the build's checks, and so does a round with shots missing."""

    if oneshot.codex_role("critic"):
        oneshot_spec.store(repo).stop("critique", "비평가는 그림을 받는 Claude 세션이어야 한다")
        return

    def body(store, spec, tree):
        look = spec["oneshot"]["looks"][-1]
        shown, missing = pictures(spec, look["round"])
        if missing:
            review.rebuild(repo, store, f"비평할 화면 사진이 없다 — {', '.join(missing[:6])}", front_build.STAGE)
            return None
        found = oneshot.turned(store, "critic", CRITIC_PROMPT, critique_text(spec, look, shown), "oneshot-critique",
                               lambda value: critique_of(value, spec), tree, tuple(shown))
        if found is None:   # a refused or failed image turn is never a critique that saw nothing wrong
            stopped = store.get().get("stopped") or {}
            if stopped.get("reason") == "block":
                store.stop("critique", stopped.get("detail") or "")
            return None
        return lambda: judged(repo, store, spec, look, found)

    review.holding(repo, "화면 비평", body, "front_built_head", front_build.STAGE)


def judged(repo: str, store, spec: dict, look: dict, found: list[dict]):
    """The round applied; what follows once the tree is free: a second look, or the fixes' build turn."""

    o = spec["oneshot"]
    rounds = [*(o.get("critiques") or []), {"look": look["round"], "head": look["head"], "findings": found,
                                            "clean": not any(f["severity"] == "high" for f in found + look["findings"]),
                                            "ts": time.time()}]
    ours = rounds[int(o.get("critique_base") or 0):]
    if len(ours) >= 2 and ours[-1]["clean"] and ours[-2]["clean"]:
        return oneshot_spec.saved(repo, oneshot={"critiques": rounds, "stage": "r3", "working": None,
                                                 "r3": {"round": (o.get("r3") or {}).get("round", 0) + 1, "decisions": {},
                                                        "marks": (o.get("r3") or {}).get("next_marks") or {}}})
    if len(ours) >= MAX_ROUNDS:
        store.put(critiques=rounds, critique_base=len(rounds))   # `[재개]` starts a fresh count
        store.stop("critique", f"{MAX_ROUNDS}회차에도 높은 발견이 남았다 — " + " / ".join(
            f"{f['route']} {f['viewport']} {f['state']}: {f['what']}" for f in found if f["severity"] == "high"))
        return None
    if ours[-1]["clean"] and not found:   # nothing to fix: a second look at the same shots, `working` still set
        oneshot_spec.saved(repo, oneshot={"critiques": rounds})
        return lambda: oneshot_spec.handoff(repo, "critique") or True   # the next look holds the tree itself
    todo = found + [f for f in look["findings"] if f["severity"] == "high"]   # a mechanical high outranks a clean critique
    # `working` names the build and `fix` keeps its findings before the turn is sent: a server that dies in
    # between, or a session that cannot open, leaves a `[재개]` that sends the same fixes.
    spec = oneshot_spec.saved(repo, oneshot={"critiques": rounds, "stage": front_build.STAGE, "attempts": 0,
                                             "working": front_build.STAGE,
                                             "fix": {"who": "An independent read-only critic judged the screenshots",
                                                     "findings": todo}})
    return lambda: oneshot_build.send(repo, front_build.STAGE, front_turn(spec))


def front_turn(spec: dict) -> str:
    """The build-front turn, with the fixes it is sent for until a build of them passes the harness."""

    fix = spec["oneshot"].get("fix")
    return fixing(spec, fix["findings"], fix["who"]) if fix else front_build.build_front_text(spec)


def fixing(spec: dict, found: list[dict], who: str) -> str:
    ordered = sorted(found, key=lambda f: SEVERITIES.index(f.get("severity", "low")))
    return (f"{who}. Fix every finding below, high first, within the approved design, and finish the turn "
            f"again with its `done-report`.\n\n```json\n{json.dumps(ordered, ensure_ascii=False, indent=2)}\n```\n\n"
            + front_build.build_front_text(spec))


oneshot_spec.WORKERS["critique"] = critique
oneshot_spec.RUNNING["critique"] = "critique"
oneshot_spec.SHEETS.add("r3")   # an approval the worktree refused: `[재개]` puts the sheet back, decisions kept
oneshot_build.STAGES[front_build.STAGE] = (*oneshot_build.STAGES[front_build.STAGE][:2], front_turn, begin)


# -- R3 ------------------------------------------------------------------------------------------

def r3_sheet(spec: dict, shot) -> str:
    """The R3 sheet in Markdown; `shot(round, name)` gives a screenshot's URL."""

    o = spec["oneshot"]
    front, views = o["front"], front_build.views(o["profile"])
    passed = [x for x in o["looks"] if not any(f["severity"] == "high" for f in x["findings"])]
    first_look, last = passed[0], passed[-1]
    marks = (o.get("r3") or {}).get("marks") or {}
    mark = lambda i: " (고친 화면)" if marks.get(i) else ""   # noqa: E731
    primary = next(s for s in front["screens"] if s["primary"])
    lines = [f"# 완성된 화면 — {spec['repo']}", "", f"## 첫 화면 {primary['id']}{mark(primary['id'])} `{primary['route']}`",
             "", *[f"![{primary['id']} {v}]({shot(last['round'], f'{primary['id']}-{v}-full.png')})" for v in views], "",
             "## 기능이 놓인 자리", "", "| 칸 | 기능 | 화면 |", "| --- | --- | --- |"]
    for f in o["features"]:
        where = [s["id"] for s in front["screens"] if f["id"] in s["features"]]
        lines.append(f"| {screens.CLASS_NAMES[o['frequency'][f['id']]]} | {f['name']} (`{f['id']}`) | {', '.join(where) or '없음'} |")
    left = [*(o.get("critiques") or [{}])[-1].get("findings", []), *last["findings"]]
    lines += ["", "## 남긴 발견", "", "높은 발견이 없는 회차가 두 번 이어져 멈춤 규칙이 남긴 것이다." if left else "없다.", ""]
    lines += [f"- [{f['severity']}] {f.get('route') or f.get('screen')} {f['viewport']} {f['state']}: {f['what']}"
              + (f" → {f['fix']}" if f.get("fix") else "") for f in left]
    lines += ["", "<details><summary>다른 화면, 상태, 회차마다의 전후, 기계 검사 기록</summary>", ""]
    for s in front["screens"]:
        lines += [f"### {s['id']}{mark(s['id'])} `{s['route']}` — {s['purpose']}", "",
                  *[f"![{s['id']} {state}]({shot(last['round'], f'{s['id']}-{views[0]}-{state}.png')})" for state in STATES],
                  "", f"전: ![{s['id']} 처음]({shot(first_look['round'], f'{s['id']}-{views[0]}-full.png')}) "
                  f"후: ![{s['id']} 마지막]({shot(last['round'], f'{s['id']}-{views[0]}-full.png')})", ""]
    for x in o["looks"]:
        counts = {sev: sum(f["severity"] == sev for f in x["findings"]) for sev in SEVERITIES}
        lines.append(f"- {x['round']}회차 `{x['head'][:12]}`: 높음 {counts['high']}, 보통 {counts['medium']}, 낮음 {counts['low']}")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


class Decisions(BaseModel):
    decisions: dict[str, dict]
    reading: dict = {}


def extra(spec: dict) -> dict:
    o = spec["oneshot"]
    if o.get("stage") != "r3":
        return {"critiques": o.get("critiques") or []} if o.get("critiques") else {}
    return {"critiques": o.get("critiques") or [], "r3_items": [s["id"] for s in o["front"]["screens"]],
            "r3_sheet": r3_sheet(spec, lambda n, name: f"/api/oneshot/spec/{spec['repo']}/look/{n}/{name}")}


oneshot_spec.VIEWS.append(extra)


@router.post("/api/oneshot/spec/{repo}/r3")
def decide(repo: str, body: Decisions) -> dict:
    """Every screen approved moves the run to its pull request; edits go to the builder as one turn and
    the loop runs again from shooting, its stop rule counted afresh."""

    with oneshot._lock:
        spec = oneshot_spec.spec_of(repo)
        o = spec["oneshot"]
        oneshot_spec.idle(o, "r3")
        decisions, comments = oneshot_spec.decided([s["id"] for s in o["front"]["screens"]], o["r3"]["decisions"],
                                                   body.decisions)
        reading = oneshot_spec.read(o, "r3", body.reading, comments)
        if not comments:
            oneshot_spec.saved(repo, oneshot={"r3": {**o["r3"], "decisions": decisions}, "reading": reading})
            approved(repo)
        else:
            # `working` and `fix` are saved with the decisions: a server that dies before the turn is sent, or a
            # session that cannot open, leaves a `[재개]` that sends the owner's edits again.
            spec = oneshot_spec.saved(repo, oneshot={
                "stage": front_build.STAGE, "attempts": 0, "critique_base": len(o.get("critiques") or []),
                "working": front_build.STAGE, "reading": reading,
                "fix": {"who": "The owner reviewed the finished screens (R3)",
                        "findings": [{"severity": "high", "screen": k, "what": v} for k, v in comments.items()]},
                "r3": {**o["r3"], "decisions": decisions, "next_marks": {k: "edited" for k in comments}}})
    if not comments:
        if oneshot_spec.spec_of(repo)["oneshot"]["stage"] == oneshot_pr.STAGE:   # outside the lock: the turn takes the worktree's hold
            oneshot_pr.begin(repo)
        return oneshot_spec.view(oneshot_spec.spec_of(repo))
    oneshot_build.send(repo, front_build.STAGE, front_turn(spec))
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def approved(repo: str) -> None:
    """Every screen approved: the run moves to its pull request on the HEAD the critic judged. It holds the
    worktree as a turn does, so a tree another turn holds refuses the approval (409, the decisions kept),
    and a tree that moved since goes back to the build's checks."""

    def moving(store, spec, tree) -> bool:
        # `working` names the README turn before it is sent: a server that dies in between leaves it for
        # `recover`, and the turn that follows sends the queued instruction when it ends.
        oneshot_spec.saved(repo, oneshot={"stage": oneshot_pr.STAGE, "working": oneshot_pr.STAGE})
        return True

    review.approval(repo, "R3 승인", moving, "front_built_head", front_build.STAGE)


@router.get("/api/oneshot/spec/{repo}/look/{n}/{name}")
def look_shot(repo: str, n: int, name: str) -> FileResponse:
    oneshot_spec.spec_of(repo)   # a repository with a oneshot spec, never a path
    path = SHOT.fullmatch(name) and oneshot_spec.within(oneshot_spec.store(repo).folder, "build", f"r{n}", name)
    if not path or not path.is_file():   # a link or a junction on the way never serves a file outside the run
        raise HTTPException(404, "그런 화면 사진이 없다")
    return FileResponse(path, media_type="image/png")
