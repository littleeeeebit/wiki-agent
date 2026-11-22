"""oneshot's owner's desktop round for a PC app, and the one guarded entry to R3 for every device round
(plan 9e5).

`docs/plans/oneshot/9-kinds.md` ("The owner's desktop round") is the design. After two clean critiques, a run
with a pending `owner` check waits at `pc` for the owner's one-time approval of exactly what the round will
start and read: the HEAD, the frozen build, the checks, the app and each check's fixture, and each capture with
the surface it reads. The approval is checked again against the run before it is recorded, and a restart, a new
HEAD or a new build sends the run back to waiting. This version (9e5a) records the approval and stops there;
the round on the owner's desktop is 9e5b.

R3 opens through `onward` alone: the phone round of an Android app, then this round, each due until it passed on
the current HEAD and build.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import oneshot, oneshot_desktop, oneshot_spec, specs
from .oneshot_profile import held, kinds_of

router = APIRouter()

STAGE = "pc"
FIXTURE = "oneshot-fixture"   # the server's own window of random English words, one for each check


def plan(o: dict) -> dict:
    """What the owner approves, read off the run now: this wait's attempt, the frozen build by its HEAD, identity
    and freeze, the `owner` checks the round runs, what it starts and each capture: who reads (the round, or the
    app it starts) and what, a window or a surface of the screen."""

    build, runs = o.get("desktop_build") or {}, oneshot_desktop.owner_scripted(o)
    captures = []
    for x in runs:   # what the app captures, then the round's own capture, if it observes one
        (what, seen), = x["observe"].items()
        captures.append({"check": x["id"], "by": "app", **(
            {"kind": "surface", "reads": f"{FIXTURE} 가 덮은 화면 영역"} if seen["from"] == "screen"
            else {"kind": "window", "reads": f"{FIXTURE} 창"})})
        if what == "window":
            captures.append({"check": x["id"], "by": "round", "kind": "window",
                             "reads": "앱의 주 창이 가진 창" if seen.get("in") == "owned" else "앱의 주 창"})
        elif what == "composition":
            captures.append({"check": x["id"], "by": "round", "kind": "surface", "reads": f"{FIXTURE} 가 덮은 화면 영역"})
    return {"attempt": (o.get("pc") or {}).get("attempt"), "head": build.get("head"), "build": build.get("identity"),
            "freeze": build.get("freeze"), "checks": [x["id"] for x in runs],
            "starts": [(build.get("launch") or {}).get("program"), *(f"{FIXTURE} ({x['id']})" for x in runs)],
            "captures": captures}


def at_head(repo: str, head) -> bool:
    """The worktree is read, now, at `head`: a HEAD that cannot be read is never taken for it."""

    try:
        done = specs.sh(["git", "rev-parse", "HEAD"], Path(oneshot_spec.spec_of(repo)["worktree"]))
    except Exception:   # a timeout, a missing Git or worktree
        return False
    return done.returncode == 0 and bool(head) and done.stdout.strip() == head


def confirmed(repo: str, build: dict) -> bool:
    """The frozen build stands, every part of it read: the worktree at its HEAD, its copy there and still what its
    manifest and identity record. Unlike `oneshot_spec.standing`, a read that fails confirms nothing."""

    if not at_head(repo, build.get("head")) or not oneshot_spec.standing(repo, build):
        return False
    try:
        return not oneshot_desktop.altered(build)
    except (OSError, ValueError):
        return False


def approved(o: dict) -> dict | None:
    """The owner's approval, while it still names exactly what the round would start and read now, in this wait."""

    given = (o.get("pc") or {}).get("approval")
    now = plan(o)
    return given if given and all(given.get(k) == now[k] for k in now) else None


def unrun(o: dict) -> list[str]:
    """The pending `owner` checks the round cannot run: no step or nothing it observes (an earlier version's
    review), or a surface of the screen the approval does not allow, the round's or the app's. They stay pending,
    and R3 stays closed."""

    runs, given = {x["id"] for x in oneshot_desktop.owner_scripted(o)}, approved(o)
    surfaced = {c["check"] for c in plan(o)["captures"] if c["kind"] == "surface"}
    return [x["id"] for x in oneshot_desktop.owner_owed(o)
            if x["id"] not in runs or given and not given.get("surface") and x["id"] in surfaced]


def passed(o: dict) -> bool:
    """The round passed every pending `owner` check on the current HEAD, frozen build and accepted build."""

    r, build = o.get("pc") or {}, o.get("desktop_build") or {}
    return r.get("status") == "passed" and r.get("head") == build.get("head") and r.get("freeze") == build.get("freeze") \
        and bool(o.get("front_build")) and r.get("front_build") == o["front_build"] \
        and {x["id"] for x in oneshot_desktop.owner_owed(o)} <= set(r.get("passed") or [])


def phoned(o: dict) -> bool:
    """The phone round passed on the current HEAD and accepted build, with the APKs the emulator's look passed. A
    build an earlier version accepted names no `front_build`: no pass counts for it, and the phone runs again."""

    p = o.get("phone") or {}
    return p.get("status") == "passed" and p.get("head") == o.get("front_built_head") and p.get("apks") == o.get("apks") \
        and bool(o.get("front_build")) and p.get("front_build") == o["front_build"]


def onward(repo: str, fields: dict):
    """The one guarded entry to R3, with `fields` saved: the phone round while an Android app's is due, then the
    owner's desktop round while a PC app has a pending `owner` check, each until it passed on the current HEAD
    and build; R3's sheet once neither is due. Neither round's pass opens it alone."""

    from . import oneshot_critique, oneshot_phone   # they import this module
    o = {**oneshot_spec.spec_of(repo)["oneshot"], **fields}
    if "android" in kinds_of(o) and not phoned(o):
        return oneshot_phone.waits(repo, fields)
    if oneshot_desktop.owner_owed(o) and not passed(o):
        return waits(repo, fields)
    return oneshot_spec.briefing(repo, "r3", {**fields, "stage": "r3", "r3": oneshot_critique.opening(o)})


def fresh(detail: str = "") -> dict:
    """A wait for the owner's approval, its own attempt: an approval of another wait never applies to it."""

    return {"status": "waiting", "detail": detail, "attempt": uuid.uuid4().hex}


def waits(repo: str, fields: dict) -> dict:
    """The run at `pc`, with `fields` saved and no approval: this version holds it there with its `kind` stop."""

    o = {**oneshot_spec.spec_of(repo)["oneshot"], **fields, "stage": STAGE, "pc": fresh()}
    return oneshot_spec.saved(repo, oneshot={**fields, "stage": STAGE, "working": None, "pc": o["pc"],
                                             **({"stopped": oneshot_spec.kind_stop(STAGE, o)} if held(o) else {})})


def earlier(repo: str, o: dict) -> None:
    """A restart sends the run back to waiting: an approval is never applied after it. A run an earlier version
    held before R3 with a pending `owner` check goes on by the guard instead: to the phone while its round is due,
    else here."""

    if o.get("stage") == STAGE:   # a new attempt even with none approved: a request prepared before it is stale
        waiting = {**o, "pc": fresh("서버가 다시 켜졌다 — 회차를 다시 승인한다" if (o.get("pc") or {}).get("approval") else "")}
        oneshot_spec.saved(repo, oneshot={"pc": waiting["pc"], **(
            {"stopped": oneshot_spec.kind_stop(STAGE, waiting)} if held(waiting) else {})})
    elif "desktop" in kinds_of(o) and o.get("stage") == "r3" and not o.get("working") and not o.get("r3") \
            and oneshot_desktop.owner_owed(o) and not passed(o):
        onward(repo, {})   # the guard's own order: a phone round still due comes first


oneshot_spec.WAITS.add(STAGE)
oneshot_spec.RECOVERS.append(earlier)


# -- HTTP ----------------------------------------------------------------------------------------------

class Approval(BaseModel):
    plan: dict   # `plan` as the screen showed it, whole
    surface: bool = False


@router.post("/api/oneshot/spec/{repo}/pc/approve")
def approve(repo: str, body: Approval) -> dict:
    """The owner's one-time approval of the round exactly as the screen showed it, in this wait: refused when the
    frozen build no longer stands, when it names anything other than what the round would start and read now, or
    when one is already recorded."""

    with oneshot._lock:
        o = oneshot_spec.spec_of(repo)["oneshot"]
        if o.get("stage") != STAGE or o.get("working"):
            raise HTTPException(409, "주인 화면 회차를 기다리고 있지 않다")
        now = plan(o)
        if not now["checks"]:
            raise HTTPException(409, "주인 화면에서 돌릴 수 있는 검사가 없다")
        if not now["attempt"] or not confirmed(repo, o.get("desktop_build") or {}):
            raise HTTPException(409, "얼린 PC 앱 빌드가 지금 작업트리의 것이 아니다 — 다시 빌드한 뒤 승인한다")
        if approved(o):
            raise HTTPException(409, "이미 승인했다 — 승인 한 번은 회차 한 번에만 쓴다")
        if body.plan != now:
            raise HTTPException(409, "승인한 회차가 지금 회차와 다르다 — 화면을 다시 읽고 승인한다")
        pc = {**(o.get("pc") or {}), "status": "approved", "detail": "",
              "approval": {**now, "surface": body.surface, "ts": time.time()}}
        o = {**o, "pc": pc}
        oneshot_spec.saved(repo, oneshot={"pc": pc, **({"stopped": oneshot_spec.kind_stop(STAGE, o)} if held(o) else {})})
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def extra(spec: dict) -> dict:
    """At `pc`, what the owner approves, the approval while it stands and the checks that stay pending."""

    o = spec["oneshot"]
    return {"pc_plan": plan(o), "pc_approved": approved(o), "pc_unrun": unrun(o)} if o.get("stage") == STAGE else {}


oneshot_spec.VIEWS.append(extra)
