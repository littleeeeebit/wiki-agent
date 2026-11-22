"""oneshot's owner's desktop round for a PC app, and the one guarded entry to R3 for every device round
(plan 9e5).

`docs/plans/oneshot/9-kinds.md` ("The owner's desktop round") is the design. After two clean critiques, a run
with a pending `owner` check waits at `pc` for the owner's one-time approval of exactly what the round will
start and read: the HEAD, the frozen build, the checks, the app and each check's fixture, and each capture with
the surface it reads. The approval is checked again against the run before it is recorded, and a restart, a new
HEAD or a new build sends the run back to waiting. The approval starts the round (9e5b): for each check the
server's fixture in a job of its own, the frozen app from a fresh start beside it, driven without a shot, then
what the check observes, each source bound and proven again before it is read; the critic judges a capture. A
pass goes on to R3, a failure goes to the build as findings, and a check that cannot be read here stays pending.

R3 opens through `onward` alone: the phone round of an Android app, then this round, each due until it passed on
the current HEAD and build.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import oneshot, oneshot_desktop, oneshot_desktop_look as desktop_look, oneshot_front_build as front_build, \
    oneshot_review as review, oneshot_spec, runtime, specs
from .oneshot_profile import kinds_of
from .query import ROOT

router = APIRouter()

STAGE = "pc"
FIXTURE = "oneshot-fixture"   # the server's own window of random English words, one for each check
RECT = [80, 80, 960, 540]   # where the fixture is placed on the primary monitor, its client area's size, physical pixels
JUDGE = "oneshot-owner-judge"
JUDGE_PROMPT = (ROOT / "tool/prompts/oneshot-owner-judge.md").read_text(encoding="utf-8")
MAX_REPORT = 1 << 20   # bytes of an app's report file the round reads
_halts: dict[str, threading.Event] = {}
# One round on the owner's desktop at a time, whichever repository it is for, from its look's start through its
# cleanup. A round whose look's cleanup was not proven leaves its record held on disk, and every repository's
# records are read before any round starts (`_held`): this server's or an earlier one's, they keep every round off
# the desktop until `desktop_session.recover` settles them.
_desk = threading.Lock()


def _held() -> list[str]:
    """Why an earlier round's look, any repository's and from any run of the server, is not settled: every
    record under each repository's `looks`, through `desktop_session.recover`. That clears one only once its proof
    completed, or once a new boot or the end of its logon session proved its processes ended. Read under
    `oneshot._lock`, which a stage folder's move between the roots takes, so none moves past the read; a folder
    that cannot be listed is a reason too."""

    import desktop_session

    def listed(folder: Path) -> list[Path]:   # never `glob`, which passes over a folder it cannot read
        try:
            return sorted(Path(entry.path) for entry in os.scandir(folder))
        except FileNotFoundError:
            return []

    reasons = []
    with oneshot._lock:
        for root in sorted({oneshot.RUNS, oneshot.WORK or oneshot.RUNS}):   # one that could not move stays there
            try:
                for looks in (f / STAGE / "looks" for f in listed(root) if f.name.startswith("spec-")):
                    if listed(looks):
                        reasons += desktop_session.recover_all(looks)
            except OSError as exc:
                reasons.append(f"{root}: 읽지 못했다 — {exc}")
    return reasons


class Stale(Exception):
    """The approved build no longer stands once the round has the owner's desktop: approved anew."""


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
    manifest and identity record. Unlike `oneshot_spec.standing`, a read that fails confirms nothing. HEAD is read
    again last, so it held through every other read."""

    if not at_head(repo, build.get("head")) or not oneshot_spec.standing(repo, build):
        return False
    try:
        if oneshot_desktop.altered(build):
            return False
    except (OSError, ValueError):
        return False
    return at_head(repo, build.get("head"))


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
    if (round_ := due(o)) == "phone":
        return oneshot_phone.waits(repo, fields)
    if round_:
        return waits(repo, fields)
    return oneshot_spec.briefing(repo, "r3", {**fields, "stage": "r3", "r3": oneshot_critique.opening(o)})


def due(o: dict) -> str | None:
    """The device round R3 still waits on, in the guard's order: `phone` for an Android app's phone round not passed
    on its current build, `pc` for a PC app's pending `owner` checks; None once neither is. R3's approval asks it
    again, so a sheet open before a pass was cleared never reaches the pull request."""

    if "android" in kinds_of(o) and not phoned(o):
        return "phone"
    return STAGE if oneshot_desktop.owner_owed(o) and not passed(o) else None


def fresh(detail: str = "") -> dict:
    """A wait for the owner's approval, its own attempt: an approval of another wait never applies to it."""

    return {"status": "waiting", "detail": detail, "attempt": uuid.uuid4().hex}


def waits(repo: str, fields: dict) -> dict:
    """The run at `pc`, with `fields` saved, waiting for the owner's approval: a stop an earlier version held the
    run with is let go."""

    return oneshot_spec.saved(repo, oneshot={**fields, "stage": STAGE, "working": None, "stopped": None,
                                             "pc": fresh()})


def earlier(repo: str, o: dict) -> None:
    """A restart sends the run back to waiting: an approval is never applied after it, and an earlier version's
    `kind` stop here (9e5a's) is let go, so the approval starts the round. A run an earlier version held before R3
    with a pending `owner` check goes on by the guard instead: to the phone while its round is due, else here."""

    if o.get("stage") == STAGE:   # a new attempt even with none approved: a request prepared before it is stale
        given = (o.get("pc") or {}).get("approval") or (o.get("pc") or {}).get("ran")
        pc = fresh("서버가 다시 켜졌다 — 회차를 다시 승인한다" if given else "")
        kind = (o.get("stopped") or {}).get("reason") == "kind"
        oneshot_spec.saved(repo, oneshot={"pc": {**pc, "last": (o.get("pc") or {}).get("last")},
                                          **({"stopped": None} if kind else {})})
    elif "desktop" in kinds_of(o) and o.get("stage") == "r3" and not o.get("working") and not o.get("r3") \
            and oneshot_desktop.owner_owed(o) and not passed(o):
        onward(repo, {})   # the guard's own order: a phone round still due comes first


def base(repo: str) -> Path:
    return oneshot_spec.store(repo).folder / STAGE


def ran(report: dict, n: int) -> str:
    """Why the driver's run of a scenario is no ground for an observation, or empty: it passed a deadline, failed
    or stopped before `done`, chose no main window, read no state, or did not act every one of its `n` steps.
    On the owner's desktop no window is shot, so a read without a shot counts."""

    steps = report.get("steps") or []
    if report.get("ended"):
        return f"드라이버가 {report['ended']} 에서 시간을 넘겼다"
    if report.get("error") or report.get("phase") != "done":
        return f"드라이버가 끝나지 못했다 — {report.get('error') or report.get('refusal') or report.get('last')}"
    if not report.get("main") or not report.get("states"):
        return "앱의 주 창을 읽지 못했다"
    if len(steps) != n or not all(s.get("acted") and not s.get("error") for s in steps):
        return "시나리오의 단계가 다 돌지 않았다"
    return ""


def reported(report: dict, seen: dict, data: Path) -> str | None:
    """The app's own report of what it captured: the value of the one element `id` names in the window it names,
    in the last state, or the UTF-8 file under its `{data}`; None when there is none to read."""

    import desktop_driver

    if "file" in seen:
        file = (data / seen["file"]).resolve()
        if not file.is_relative_to(data.resolve()) or not file.is_file() or file.stat().st_size > MAX_REPORT:
            return None
        return file.read_text(encoding="utf-8", errors="replace")
    last = report["states"][-1]
    window = desktop_driver.scope(report, last, seen.get("in", "main"))
    nodes = [n for n in last["nodes"] if not n.get("foreign") and n.get("window") == window and n.get("id") == seen["id"]]
    return nodes[0].get("value") if window is not None and len(nodes) == 1 else None


def one(session, runs, n: int, x: dict, scenario: dict, exe, missing: str, keep: Path, standing) -> dict:
    """One `owner` check: its fixture, then the app from a fresh start of its scenario, then what it observes while
    both still run, each source proven again first. `passed`, `failed` or `pending` with why, or `judge` with the
    capture copied into `keep` for the critic. Once the fixture is up and bound, its job is ended, its exit proven,
    before this returns or raises, so the next check's fixture is the only window by that title. A fixture that
    never came up or could not be bound raises, and the round's look ends it with the rest. `standing` raises
    Stale once the approved build no longer stands: called before the fixture starts, and by `runs` with the app's
    process made but not yet run."""

    import owner_round

    standing()
    page, fixture_key = owner_round.words(), f"fixture{n}"
    fixture, rect = owner_round.fixture(session, fixture_key, page, RECT, runs.halt)
    (what, seen), = x["observe"].items()
    steps = scenario["steps"]

    def members(key: str) -> list[dict]:
        session.observe()
        return session.record["jobs"][key]["members"]

    def then(report: dict, key: str) -> dict:
        if why := ran(report, len(steps)):
            return {"status": "failed", "why": why}
        if why := owner_round.proven(fixture, members(fixture_key)):   # what the app read was not the round's page
            return {"status": "failed", "why": f"고정 창이 바뀌었다 — {why}"}
        if what == "report":
            return {"status": "passed"} if owner_round.contains(reported(report, seen, Path(runs.data)), page) else \
                {"status": "failed", "why": "앱이 캡처했다고 낸 것에 고정 창의 낱말이 다 들어 있지 않다"}
        if exe is None:
            return {"status": "pending", "why": missing}
        try:
            if what == "window":
                import desktop_driver
                last = report["states"][-1]
                hwnd = desktop_driver.scope(report, last, seen.get("in", "main"))
                read = next((w for w in last.get("windows") or [] if hwnd and w.get("hwnd") == hwnd), None)
                try:   # the window as the driver read it: a handle that names another since is never read
                    binding = owner_round.bind(key, hwnd, members(key), seen=read or {})
                except ValueError as exc:
                    return {"status": "failed", "why": f"앱의 창을 묶지 못했다 — {exc}"}
                got = owner_round.window_read(session, f"capture{n}", exe, binding, lambda: members(key), runs.halt,
                                              oneshot._lock)   # as `stop` takes it: a stop accepted first is a `stop`
            else:
                got = owner_round.surface_read(session, f"capture{n}", exe, fixture, rect,
                                               lambda: members(fixture_key), runs.halt, oneshot._lock)
        except owner_round.Unavailable as exc:
            return {"status": "pending", "why": str(exc)}
        if not got.get("read"):
            return {"status": "failed", "why": f"캡처를 읽지 못했다 — {got.get('why')}"}
        file = keep / f"c{n}-{what}.png"
        shutil.copyfile(got["file"], file)   # the session's folders go with its cleanup
        return {"status": "judge", "file": str(file), "shows": seen["shows"], "page": page}

    try:
        report = runs.drive(scenario["screen"], scenario["state"], steps, then)
    finally:   # each check's fixture gone, its end proven, before the next one shows: one window by that title
        owner_round.ended(session, fixture_key, "고정 창을")
    return report.get("observed") or {"status": "failed", "why": "검사를 끝내지 못했다"}


def observe(repo: str, o: dict, stopped) -> dict[str, dict]:
    """Every check the approval lets run, each by `one` in the round's own look on the owner's desktop, whose
    cleanup is proven on every exit (RuntimeError when it is not). One round at a time across repositories
    (`_desk`), from the look's start through its cleanup, and none while any repository's earlier look is held
    (`_held`); the build is confirmed again once the round has the desktop, before each fixture starts, and with
    each app's process made but not yet run (Stale when it no longer stands)."""

    import desktop_session
    import owner_round

    build, skip = o["desktop_build"], set(unrun(o))
    keep = base(repo) / "rounds" / f"r{time.time_ns()}"
    keep.mkdir(parents=True)
    try:
        exe, missing = owner_round.helper(base(repo) / "tools"), ""
    except owner_round.Unavailable as exc:
        exe, missing = None, str(exc)
    looks = base(repo) / "looks"
    looks.mkdir(parents=True, exist_ok=True)
    results = {}
    while not _desk.acquire(timeout=1):   # another repository's round is on the owner's desktop
        if stopped():
            raise RuntimeError("다른 저장소의 주인 화면 회차를 기다리다 멈췄다")
    def standing() -> None:   # nothing starts on the owner's desktop from a build that no longer stands
        if not confirmed(repo, build):
            raise Stale("승인한 회차가 지금 빌드와 다르다 — 다시 승인한다")

    try:
        if held := _held():
            raise RuntimeError("앞선 주인 화면 회차의 정리가 확인되지 않았다 — " + "; ".join(held))
        standing()
        with desktop_session.look(looks, f"round-{uuid.uuid4().hex[:12]}", owned=False) as session:
            # The last stop check and the resume run under the lock a stop is accepted under: one accepted first
            # always keeps the app from running.
            runs = desktop_look.Launches(session, build, desktop_look.size(o["profile"]), stopped, shoot=False,
                                         ready=standing, gate=oneshot._lock)
            for n, x in enumerate(oneshot_desktop.owner_scripted(o), 1):
                if x["id"] not in skip:
                    runs.due()
                    results[x["id"]] = one(session, runs, n, x, build["checks"][x["id"]], exe, missing, keep,
                                           standing)
    except desktop_session.Refused as exc:
        raise RuntimeError(f"주인 화면 회차를 정리하지 못했다 — {exc}") from exc
    finally:   # a cleanup not proven stays held in the look's record, which `_held` reads
        _desk.release()
    runs.due()
    return results


def judged(store, tree: Path, cid: str, result: dict) -> dict | None:
    """The critic's verdict on a capture: `passed`, or `failed` with what it saw; None after stopping."""

    def verdict(value) -> dict:
        if not isinstance(value, dict) or not isinstance(value.get("shows"), bool) \
                or not isinstance(value.get("why"), str) or not value["why"].strip():
            raise ValueError("`oneshot-owner-judge` 는 shows(true/false) 와 why(한 문장) 를 담은 객체다")
        return value

    text = (f"The check `{cid}`. The fixture showed exactly these words: {' '.join(result['page'])}\n\n"
            f"What the capture must show: {result['shows']}\n\nThe capture, attached: `{Path(result['file']).name}`\n")
    found = oneshot.turned(store, "critic", JUDGE_PROMPT, text, JUDGE, verdict, tree, (Path(result["file"]),), {})
    if found is None:
        return None
    return {"status": "passed"} if found["shows"] else {"status": "failed", "why": f"비평가: {found['why']}"}


def outcome(o: dict, results: dict[str, dict]) -> dict:
    """The round's outcome: `failed` with a high finding for each failed check, `passed` once every pending
    `owner` check passed on this HEAD and build, else `waiting` naming what stays pending."""

    build, scripted = o["desktop_build"], {x["id"]: x for x in oneshot_desktop.owner_scripted(o)}
    passed_ = [cid for cid, r in results.items() if r["status"] == "passed"]
    if failed := {cid: r for cid, r in results.items() if r["status"] == "failed"}:
        findings = [desktop_look.finding(build["checks"][cid]["screen"], build["checks"][cid]["state"],
                                         f"주인 화면에서 확인할 {cid}({scripted[cid]['requirement']}) 이 통과하지 못했다 — {r['why']}")
                    for cid, r in failed.items()]
        return {"status": "failed", "findings": findings, "passed": passed_, "results": results}
    if {x["id"] for x in oneshot_desktop.owner_owed(o)} <= set(passed_):
        return {"status": "passed", "head": build["head"], "freeze": build["freeze"], "front_build": o["front_build"],
                "passed": passed_, "results": results}
    left = [f"{x['id']}" + (f"({results[x['id']]['why']})" if x["id"] in results else "")
            for x in oneshot_desktop.owner_owed(o) if x["id"] not in passed_]
    return {"status": "waiting", "detail": f"주인 화면에서 아직 확인하지 못한 검사 — {', '.join(left)}", "results": results}


def owner_round(repo: str) -> None:
    """The worker: the round holds the worktree on the HEAD the look passed, as the critic does. It runs only what
    the stored approval names, still standing; a stop or a shutdown never passes it."""

    halt = _halts.setdefault(repo, threading.Event())
    stopped = lambda: halt.is_set() or runtime.stopping.is_set()   # noqa: E731

    def body(store, spec, tree):
        o = spec["oneshot"]
        if not (given := approved(o)) or not confirmed(repo, o.get("desktop_build") or {}):
            return lambda: settled(repo, {"status": "waiting", "detail": "승인한 회차가 지금 빌드와 다르다 — 다시 승인한다"})
        # Used up as the round starts: however it ends, a stop or a refused turn included, the next is approved anew.
        oneshot_spec.saved(repo, oneshot={"pc": {**fresh(), "status": "running", "ran": given,
                                                 "last": (o.get("pc") or {}).get("last")}})
        try:
            results = observe(repo, o, stopped)
        except Stale as exc:   # the build changed while the round waited for the desktop: approved anew
            anew = {"status": "waiting", "detail": str(exc)}   # `exc` is gone once out of here
            return lambda: settled(repo, anew)
        except ValueError as exc:   # the app refused, changed its frozen copy or overran: the build's to fix
            again = {"status": "rebuild", "detail": f"주인 화면 회차 — {exc}"}   # `exc` is gone once out of here
            return lambda: settled(repo, again, store)
        except Exception as exc:   # an unproven cleanup, a fixture that never showed: nothing passes
            cut = {"status": "stopped", "detail": "주인 화면 회차를 멈췄다"} if stopped() else \
                {"status": "error", "detail": f"주인 화면 회차가 끝나지 못했다 — {exc}"}
            return lambda: settled(repo, cut)
        for cid, result in results.items():   # the critic judges each capture once the round's windows are gone
            if result["status"] == "judge" and not stopped():
                if (verdict := judged(store, tree, cid, result)) is None:
                    return None
                results[cid] = {**result, **verdict}
        if stopped():   # a stop accepted before the round ended: never its outcome
            return lambda: settled(repo, {"status": "stopped", "detail": "주인 화면 회차를 멈췄다"})
        return lambda: settled(repo, outcome(o, results), store)

    # A tree moved meanwhile goes back to the build's checks as an overrun does: decided against a stop, under the lock.
    review.holding(repo, "주인 화면 회차", body, "front_built_head", front_build.STAGE,
                   again=lambda repo_, store, why, build: settled(repo_, {"status": "rebuild", "detail": why}, store))


def settled(repo: str, result: dict, store=None):
    """The outcome applied: a pass goes on through the guard to R3 (its brief written once the tree is let go), a
    failure to the build with the round's findings, `rebuild` (the app refused or overran) to the build's checks,
    anything else back to waiting with a new attempt. A stop or a shutdown accepted before it is every outcome's."""

    from . import oneshot_build, oneshot_critique   # they import this module
    with oneshot._lock:   # as `stop` takes it: a stop accepted before this point is never a pass
        if _halts.setdefault(repo, threading.Event()).is_set() or runtime.stopping.is_set():
            result = {"status": "stopped", "detail": "주인 화면 회차를 멈췄다"}
        o = oneshot_spec.spec_of(repo)["oneshot"]
        if result["status"] == "passed":   # the frozen copy, then HEAD, read once more as the pass is taken
            try:
                with oneshot_desktop.held(o["desktop_build"]) as why:   # nobody changes the copy until it is saved
                    if not why and not at_head(repo, o["desktop_build"].get("head")):
                        why = "작업 트리의 HEAD 가 승인한 빌드에서 옮겨 갔다"
                    if not why:
                        return onward(repo, {"pc": {**(o.get("pc") or {}), **result}})
            except (OSError, ValueError) as exc:
                why = f"얼린 PC 앱 빌드를 다시 읽지 못했다 — {exc}"
            result = {"status": "rebuild", "detail": f"주인 화면 회차 — {why}"}
        if result["status"] == "rebuild":
            oneshot_spec.saved(repo, oneshot={"pc": {**fresh(), "last": (o.get("pc") or {}).get("last")}})
            review.rebuild(repo, store, result["detail"], front_build.STAGE)
            return None
        pc = {**fresh(result.get("detail") or ""), "last": result}
        if result["status"] != "failed":
            oneshot_spec.saved(repo, oneshot={"pc": pc, "working": None})
            return None
        # As the phone round's: the build's fix goes through the look and the critique before the round again.
        spec = oneshot_spec.saved(repo, oneshot={
            "pc": pc, "stage": front_build.STAGE, "attempts": 0, "working": front_build.STAGE,
            "critique_base": len(o.get("critiques") or []),
            "fix": {"who": "The run's round on the owner's desktop found these", "findings": result["findings"]}})
    return lambda: oneshot_build.send(repo, front_build.STAGE, oneshot_critique.front_turn(spec))


oneshot_spec.WORKERS[STAGE] = owner_round
oneshot_spec.SHEETS.add(STAGE)   # a round the server went down in: `[재개]` puts the approval back
oneshot_spec.WAITS.add(STAGE)
oneshot_spec.RECOVERS.append(earlier)


# -- HTTP ----------------------------------------------------------------------------------------------

class Approval(BaseModel):
    plan: dict   # `plan` as the screen showed it, whole
    surface: bool = False


@router.post("/api/oneshot/spec/{repo}/pc/approve")
def approve(repo: str, body: Approval) -> dict:
    """The owner's one-time approval of the round exactly as the screen showed it, in this wait, which starts it:
    refused while the round runs, when the frozen build no longer stands, or when it names anything other than what
    the round would start and read now. The round uses it up as it starts, so a second press of the same screen is
    refused by its attempt; one standing with no round running is a round that never started, approved again."""

    with oneshot._lock:
        o = oneshot_spec.spec_of(repo)["oneshot"]
        if o.get("stage") != STAGE or o.get("working"):
            raise HTTPException(409, "주인 화면 회차를 기다리고 있지 않다")
        now = plan(o)
        if not now["checks"]:
            raise HTTPException(409, "주인 화면에서 돌릴 수 있는 검사가 없다")
        if not now["attempt"] or not confirmed(repo, o.get("desktop_build") or {}):
            raise HTTPException(409, "얼린 PC 앱 빌드가 지금 작업트리의 것이 아니다 — 다시 빌드한 뒤 승인한다")
        if body.plan != now:
            raise HTTPException(409, "승인한 회차가 지금 회차와 다르다 — 화면을 다시 읽고 승인한다")
        pc = {**(o.get("pc") or {}), "status": "running", "detail": "",
              "approval": {**now, "surface": body.surface, "ts": time.time()}}
        _halts.setdefault(repo, threading.Event()).clear()   # before `working` names the round: a stop then holds
        oneshot_spec.start(repo, STAGE, pc=pc)
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/pc/stop")
def stop(repo: str) -> dict:
    """The running round stops at its next operation; what it started is ended and its cleanup proven on the way
    out, and nothing passes."""

    with oneshot._lock:   # decided against the round's own end (`settled`), which takes it too
        if oneshot_spec.spec_of(repo)["oneshot"].get("working") != STAGE:
            raise HTTPException(409, "주인 화면 회차가 돌고 있지 않다")
        _halts.setdefault(repo, threading.Event()).set()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def extra(spec: dict) -> dict:
    """At `pc`, what the owner approves, the approval while it stands and the checks that stay pending."""

    o = spec["oneshot"]
    return {"pc_plan": plan(o), "pc_approved": approved(o), "pc_unrun": unrun(o)} if o.get("stage") == STAGE else {}


oneshot_spec.VIEWS.append(extra)
