"""oneshot S7's look at a PC app: the frozen build started on a desktop the run owns, once for each screen and
state, its windows shot and read by the driver, and the mechanical, placement and coverage findings.

`docs/plans/oneshot/9-kinds.md` ("The look loop", "Screens") is the design, and the research note
`docs/research/oneshot-desktop-session.md` the session it runs in. Every start launches the frozen copy with its
recorded settings, `{data}` a fresh folder the look owns, and `ONESHOT_SCREEN`/`ONESHOT_STATE` naming what to
open. A finding is high by definition, as an Android app's is. The look's cleanup proof gates the round: a look
whose cleanup is not proven stops the stage instead of passing it.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import oneshot_desktop, oneshot_spec
from .oneshot_device import MAX_TAPS, STATES

VIEW = "window"
PREFIX = "feature:"
END_SECONDS = 30
SETTLE_MS = 1000
LOOK_SECONDS = 30 * 60   # a whole look: every screen and state, the probes and the owed checks
TRY = "try"   # the one step a `sometimes` probe takes; a probe that does not show the feature misses this one
MISSED = f"step {TRY}: its postcondition was not read"


def finding(sid: str, state: str, what: str) -> dict:
    return {"severity": "high", "screen": sid, "viewport": VIEW, "state": state, "what": what}


def never() -> bool:
    return False


def halted(stopped) -> None:
    if stopped():
        raise ValueError("PC 앱 화면 검사 중에 멈췄다 — [재개] 로 빌드부터 다시 검사한다")


def size(profile: dict) -> tuple[int, int]:
    width, height = profile["window"].split("x")
    return int(width), int(height)


def inside(rect, box) -> bool:
    x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    return box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]


def walk(state: dict):
    """Each of the state's own nodes with the scrolling containers it lies in. The nodes come depth first, each
    window's from depth 0, so a container's descendants are the nodes after it deeper than it."""

    stack = []
    for node in state["nodes"]:
        while stack and stack[-1]["depth"] >= node["depth"]:
            stack.pop()
        if not node.get("foreign"):
            yield node, list(stack)
            if "Scroll" in node.get("patterns", []) and node.get("rect"):
                stack.append(node)


def visible(node: dict, scrollers: list) -> bool:
    """Laid out on screen, and not scrolled out of any container it lies in."""

    return bool(node.get("rect")) and not node.get("offscreen") and all(inside(node["rect"], s["rect"]) for s in scrollers)


def features(state: dict, shown: bool, windows: set | None = None) -> set[str]:
    """The feature ids a state's own nodes carry as `feature:<id>`, in `windows` if named; with `shown`, only
    those `visible`."""

    return {node["id"][len(PREFIX):] for node, scrollers in walk(state)
            if (node.get("id") or "").startswith(PREFIX) and (windows is None or node["window"] in windows)
            and (not shown or visible(node, scrollers))}


def partial(state: dict, windows: set) -> bool:
    """Whether an open item container in `windows` holds items its provider counts that the read did not reach
    (`unread`, matched by identity, groups and headers aside), or will not say: a virtualized list or branch,
    whose unrealized items the tree does not hold, so absence off it is not proven."""

    return any(node["window"] in windows and "unread" in node and node["unread"] != 0 for node, _ in walk(state))


def drawn(state: dict, hwnd) -> bool:
    """Whether `hwnd` is among the state's windows, shot and its tree read."""

    return (any(w["hwnd"] == hwnd for w in state["windows"])
            and any(sh["hwnd"] == hwnd and sh.get("drawn") for sh in state["shots"])
            and any(r["hwnd"] == hwnd and r.get("read") for r in state.get("reads", [])))


def complete(report: dict) -> bool:
    """Whether the driver finished and made every observation: every state shows a window, each drawn and read,
    and the driver's own end was proven. What a read found (an unnamed control, a small target) leaves it
    complete; absence counts only off such a read."""

    states = report.get("states") or []
    return (report.get("phase") == "done" and not report.get("ended") and not report.get("error") and bool(states)
            and not (report.get("end") and not report["end"]["clean"]) and not report.get("unsaved")
            and all(st["windows"] and all(drawn(st, w["hwnd"]) for w in st["windows"]) for st in states))


def surface(report: dict, kind: str) -> tuple[int | None, str]:
    """The window a screen of `kind` is, in the start state: the main window for `window`, else the one window
    the main window owns (its dialog, floating window or flyout). None, with why when it is not one window."""

    states = report.get("states") or []
    if not states or not report.get("main"):
        return None, ""
    main = report["main"]["hwnd"]
    if kind == VIEW:
        return main, ""
    owned = [w["hwnd"] for w in states[0]["windows"] if w.get("owner") == main]
    if len(owned) > 1:
        return None, f"`{kind}` 화면으로 볼 창이 {len(owned)}개다 — 메인 창은 그 화면의 창 하나만 소유한다"
    return (owned[0], "") if owned else (None, "")


def mine(report: dict, state: dict) -> set[int]:
    """The main window and the windows it owns, in `state`."""

    main = report["main"]["hwnd"]
    return {main} | {w["hwnd"] for w in state["windows"] if w.get("owner") == main}


class Launches:
    """The frozen build's starts inside one look: each a job of its own, ended before the next starts."""

    def __init__(self, look, build: dict, window: tuple[int, int], stopped):
        self.look, self.build, self.window, self.stopped, self.n = look, build, window, stopped, 0
        self.began = time.monotonic()
        # The start and the driver end at once on the owner's stop, and once the whole look is past its deadline:
        # each operation has its own, and this bounds their sum.
        self.halt = SimpleNamespace(is_set=lambda: stopped() or self.over())

    def over(self) -> bool:
        return time.monotonic() - self.began > LOOK_SECONDS

    def due(self) -> None:
        """Stopped by the owner, or the look past its deadline: a slow program goes back to the build."""

        halted(self.stopped)
        if self.over():
            raise ValueError(f"PC 앱 화면 검사가 {LOOK_SECONDS // 60}분 안에 끝나지 않았다 — 화면과 상태마다 띄우고 "
                             "읽는 데 너무 오래 걸린다")

    def drive(self, sid: str, state: str, steps: list[dict]) -> dict:
        """One start of the program opening `sid` in `state`, driven through `steps`: the driver's report. No
        start follows one whose end is not proven (RuntimeError, which stops the stage), or one that changed the
        frozen copy (ValueError, back to the build). A start Windows refuses goes back to the build too."""

        import desktop_driver   # Windows only, as a PC app is
        import desktop_session

        self.due()
        self.n += 1
        key = f"app{self.n}"
        launch, folder = self.build["launch"], Path(self.build["folder"])
        data = str(self.look.reserve(f"data{self.n}"))
        argv = [str(folder / launch["program"]), *(a.replace("{data}", data) for a in launch["args"])]
        extra = {**{k: v.replace("{data}", data) for k, v in launch["env"].items()
                    if k.upper() not in oneshot_desktop.LOOK_ENV}, "ONESHOT_SCREEN": sid, "ONESHOT_STATE": state}
        try:
            self.look.start(key, argv, folder / launch["cwd"], extra, halt=self.halt)
        except desktop_session.Halted:
            self.due()
            halted(lambda: True)
        except desktop_session.Refused as exc:
            raise ValueError(f"PC 앱을 띄우지 못했다 — {exc}") from exc
        report, failed = {}, None
        try:
            report = desktop_driver.drive(self.look, key, self.window, steps, settle_ms=SETTLE_MS,
                                          key=f"driver{self.n}", halt=self.halt)
        except Exception as exc:   # the ends are proven first: an unproven one stops the stage, never a retry
            failed = exc
        self.look.observe()   # its members recorded first, so recovery names them however the end goes
        end = self.look.jobs[key].end(END_SECONDS)
        for who, proof in (("앱을", end), ("드라이버를", report.get("end"))):
            if proof and not proof["clean"]:
                raise RuntimeError(f"PC 앱 화면 검사에서 {who} 끝내지 못했다 — {'; '.join(proof['reasons'])}") \
                    from failed
        if failed is not None:
            raise failed
        self.due()
        if why := oneshot_desktop.altered(self.build):
            raise ValueError(why)
        return report


def look(spec: dict, build: dict, folder: Path, stopped=never) -> list[dict]:
    """Every screen in every state of the frozen `build` shot into `folder` as `<screen>-window-<state>.png`, with
    the mechanical, placement and coverage findings. The look is cleaned and proven on every exit."""

    import desktop_session   # Windows only, as a PC app is

    looks = oneshot_spec.store(spec["repo"]).folder / "looks"
    looks.mkdir(parents=True, exist_ok=True)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    try:
        with desktop_session.look(looks, f"look-{build['identity'][:12]}-{uuid.uuid4().hex[:8]}") as session:
            runs = Launches(session, build, size(spec["oneshot"]["profile"]), stopped)
            return inspect(runs, spec, folder, session.folder / "evidence") + checks(runs, spec, build)
    except desktop_session.Refused as exc:
        raise RuntimeError(f"PC 앱 화면 검사의 데스크톱을 정리하지 못했다 — {exc}") from exc


def checks(runs: Launches, spec: dict, build: dict) -> list[dict]:
    """Each `look` check the backend review left for the PC app, its scenario run from a fresh start of its screen
    and state: it passes only on a complete read whose every step acted and left its postcondition read off the
    state it names. A failed or unread one is a high finding for the builder, never a skip."""

    import desktop_driver

    o, out = spec["oneshot"], []
    owed = [x for x in o.get("owed") or [] if x["kind"] == "desktop" and x.get("source") == "look"]
    lead = next(s for s in o["front"]["screens"] if s["primary"])["id"] if owed else None
    for x in owed:
        failed = f"PC 앱에서 확인할 {x['id']}({x['requirement']}) 이 통과하지 못했다"
        scenario = (build.get("checks") or {}).get(x["id"])
        if scenario is None:
            out.append(finding(lead, "full", f"{failed} — 빌드에 그 시나리오가 없다 · 확인: {x['check']}"))
            continue
        report = runs.drive(scenario["screen"], scenario["state"], scenario["steps"])
        states = {s["id"]: s for s in report.get("states") or []}
        steps = report.get("steps") or []
        if complete(report) and len(steps) == len(scenario["steps"]) and all(
                s.get("acted") and s.get("state") in states and desktop_driver.holds(states[s["state"]], want["expect"])
                for s, want in zip(steps, scenario["steps"])):
            continue
        why = [f for f in desktop_driver.findings(report) if f.startswith("step ")][:2] or \
            ["the driver's read was not complete"]
        out.append(finding(scenario["screen"], scenario["state"], f"{failed} — {'; '.join(why)} · 확인: {x['check']}"))
    return out


def inspect(runs: Launches, spec: dict, folder: Path, evidence: Path) -> list[dict]:
    """The shots and findings of every screen and state, then the owner's map on the first screen."""

    import desktop_driver

    o = spec["oneshot"]
    front, frequency = o["front"], o["frequency"]
    findings, anywhere, first, whole, hidden = [], set(), None, True, False
    for s in front["screens"]:
        kind = s.get("surface", VIEW)
        for state in STATES:
            report = runs.drive(s["id"], state, [])
            found = desktop_driver.findings(report)
            hwnd, why = surface(report, kind)
            start = (report.get("states") or [None])[0]
            if hwnd is not None and not (start and drawn(start, hwnd)):
                hwnd = None   # the screen's window is evidence only once the state shows it, shot and read
            file = next(sh["file"] for sh in start["shots"] if sh["hwnd"] == hwnd) if hwnd is not None else None
            if file:
                shutil.copyfile(evidence / f"driver{runs.n}" / file, folder / f"{s['id']}-{VIEW}-{state}.png")
            elif why or not found:
                found.append(why or f"`{kind}` 화면의 창이 찍히지 않았다")
            findings += [finding(s["id"], state, what) for what in found]
            if start:   # presence counts off any read; absence only off reads that all completed
                anywhere |= features(start, shown=False)
                hidden |= partial(start, {w["hwnd"] for w in start["windows"]})
            whole = whole and complete(report) and hwnd is not None
            if s["primary"] and state == "full" and first is None:
                first = (s, report, start, hwnd) if complete(report) and hwnd is not None else False
    out, shown, observed, probed = placement(runs, front, frequency, first)
    findings += out
    anywhere |= shown | observed   # a `never` control a probe saw is one too
    lead = next(s for s in front["screens"] if s["primary"])["id"]
    for f, cls in frequency.items():
        if cls == "never" and f in anywhere:
            findings.append(finding(lead, "full", f"화면에 없기로 한 `{f}` 의 컨트롤이 화면에 있다"))
        if cls != "never" and f not in anywhere and whole:
            findings.append(finding(lead, "full", f"`{PREFIX}{f}` 가 어느 화면의 트리에도 없다"))
    if (hidden or probed) and whole and "never" in frequency.values():
        findings.append(finding(lead, "full", "목록이 내용을 다 담지 않아 쓰지 않기로 한 기능이 어디에도 없다는 것을 "
                                              "확인하지 못했다 — 화면 검사 중에는 목록 가상화를 끈다"))
    return [dict(f) for f in dict.fromkeys(tuple(f.items()) for f in findings)]   # a probe repeats a screen's


def placement(runs: Launches, front: dict, frequency: dict, first) -> tuple[list[dict], set[str], set[str], bool]:
    """The owner's map read off the first screen's `full` state, its own window's controls only; each breach is
    high. A first screen whose read did not complete is a finding of its own: absence counts only off a read
    that completed, and off a window whose lists hold all their content. The findings, the features one
    invocation showed, every feature the probes saw, and whether a probe read a list holding only part of it."""

    if not first:
        sid = next(s for s in front["screens"] if s["primary"])["id"]
        return [finding(sid, "full", "첫 화면의 `full` 상태를 읽지 못해 배치를 확인하지 못했다")], set(), set(), False
    s, report, start, hwnd = first
    visible, there = features(start, shown=True, windows={hwnd}), features(start, shown=False, windows={hwnd})
    out, shown, observed, probed = [], set(), set(), False
    if partial(start, {hwnd}) and any(cls in ("rarely", "never") for cls in frequency.values()):
        out.append(finding(s["id"], "full", "첫 화면의 목록이 내용을 다 담지 않아 첫 화면에 없어야 할 기능이 없다는 것을 "
                                            "확인하지 못했다 — 화면 검사 중에는 목록 가상화를 끈다"))
    for f, cls in frequency.items():
        if cls == "every" and f not in visible:
            out.append(finding(s["id"], "full", f"늘 쓰는 기능 `{f}` 가 첫 화면에 바로 보이지 않는다"))
        if cls in ("rarely", "never") and f in there:
            out.append(finding(s["id"], "full", f"`{f}` 는 첫 화면에 없어야 한다"))
        if cls == "sometimes" and f not in visible:
            ok, carried, saw, cut = one_action(runs, s, start, hwnd != report["main"]["hwnd"], hwnd, f)
            out += [finding(s["id"], "full", what) for what in carried]
            observed, probed = observed | saw, probed or cut
            if ok:
                shown.add(f)
            else:
                out.append(finding(s["id"], "full", f"가끔 쓰는 기능 `{f}` 가 한 번 눌러서 보이지 않는다"))
    return out, shown, observed, probed


def one_action(runs: Launches, s: dict, start: dict, owned: bool, hwnd: int, fid: str) \
        -> tuple[bool, list[str], set[str], bool]:
    """Whether invoking one of the first screen's controls in view, by its `AutomationId` within that screen's
    window (`owned`: the one window the main window owns), from a fresh start where the feature was not shown,
    shows it in the main window or a window it owns. Also what the probes could not prove, a finding and never
    a miss; every feature any probe state held; and whether one read a list holding only part of its content."""

    import desktop_driver

    targets = [node["id"] for node, scrollers in walk(start)
               if node["window"] == hwnd and node.get("id") and visible(node, scrollers) and node.get("enabled", True)
               and "Invoke" in node.get("patterns", [])]
    carried, saw, cut = [], set(), False
    for target in list(dict.fromkeys(targets))[:MAX_TAPS]:
        step = {"name": TRY, "action": "invoke", "id": target, "expect": {"id": PREFIX + fid},
                **({"in": "owned"} if owned else {})}
        report = runs.drive(s["id"], "full", [step])
        carried += [f for f in desktop_driver.findings(report) if f != MISSED and f not in carried]
        for state in report.get("states") or []:
            saw |= features(state, shown=False)
            cut = cut or partial(state, {w["hwnd"] for w in state["windows"]})
        states = {state["id"]: state for state in report.get("states") or []}
        done = (report.get("steps") or [{}])[0]
        if not complete(report) or not done.get("acted") or done.get("state") not in states:
            continue
        fresh, after = report["states"][0], states[done["state"]]
        main = report["main"]["hwnd"]   # the window the driver acted in: the main one, or the one it owns
        acted_in = mine(report, fresh) - {main} if owned else {main}
        reachable = any(node.get("id") == target and visible(node, scrollers) and node.get("enabled", True)
                        for node, scrollers in walk(fresh) if node["window"] in acted_in)
        if reachable and fid not in features(fresh, shown=True, windows=mine(report, fresh)) \
                and fid in features(after, shown=True, windows=mine(report, after)):
            return True, carried, saw, cut
    return False, carried, saw, cut
