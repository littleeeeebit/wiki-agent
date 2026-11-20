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
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import oneshot_desktop, oneshot_spec
from .oneshot_device import MAX_TAPS, STATES

VIEW = "window"
PREFIX = "feature:"
END_SECONDS = 30
SETTLE_MS = 1000
TRY = "try"   # the one step a `sometimes` probe takes; a probe that does not show the feature misses this one
MISSED = f"step {TRY}: its postcondition was not read"
# A realized item of a virtualized list: the tree holds only what is realized, so absence off it is not proven.
# ponytail: read off the realized items' pattern; a list that realizes none of its items is not seen.
VIRTUAL = "VirtualizedItem"


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


def features(state: dict, shown: bool, windows: set | None = None) -> set[str]:
    """The feature ids a state's own nodes carry as `feature:<id>`, in `windows` if named; with `shown`, only
    those laid out on screen."""

    return {node["id"][len(PREFIX):] for node in state["nodes"]
            if not node.get("foreign") and (node.get("id") or "").startswith(PREFIX)
            and (windows is None or node["window"] in windows)
            and (not shown or node.get("rect") and not node.get("offscreen"))}


def complete(report: dict) -> bool:
    """Whether the driver finished and made every observation: every state's windows drawn and read. What a
    read found (an unnamed control, a small target) leaves it complete; absence counts only off such a read."""

    states = report.get("states") or []
    return (report.get("phase") == "done" and not report.get("ended") and not report.get("error") and bool(states)
            and all(len(st.get("reads", [])) == len(st["shots"]) and all(sh.get("drawn") for sh in st["shots"])
                    and all(r.get("read") for r in st.get("reads", [])) for st in states))


def virtual(state: dict) -> bool:
    return any(VIRTUAL in node.get("patterns", []) for node in state["nodes"] if not node.get("foreign"))


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
        self.halt = SimpleNamespace(is_set=stopped)   # the start and the driver end at once on the owner's stop

    def drive(self, sid: str, state: str, steps: list[dict]) -> dict:
        """One start of the program opening `sid` in `state`, driven through `steps`: the driver's report. No
        start follows one whose end is not proven (RuntimeError, which stops the stage), or one that changed the
        frozen copy (ValueError, back to the build). A start Windows refuses goes back to the build too."""

        import desktop_driver   # Windows only, as a PC app is
        import desktop_session

        halted(self.stopped)
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
            halted(lambda: True)
        except desktop_session.Refused as exc:
            raise ValueError(f"PC 앱을 띄우지 못했다 — {exc}") from exc
        try:
            report = desktop_driver.drive(self.look, key, self.window, steps, settle_ms=SETTLE_MS,
                                          key=f"driver{self.n}", halt=self.halt)
        finally:   # its members recorded first, so recovery names them however the end goes
            self.look.observe()
            end = self.look.jobs[key].end(END_SECONDS)
        if not end["clean"]:
            raise RuntimeError(f"PC 앱 화면 검사에서 앱을 끝내지 못했다 — {'; '.join(end['reasons'])}")
        halted(self.stopped)
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
            return inspect(Launches(session, build, size(spec["oneshot"]["profile"]), stopped), spec, folder,
                           session.folder / "evidence")
    except desktop_session.Refused as exc:
        raise RuntimeError(f"PC 앱 화면 검사의 데스크톱을 정리하지 못했다 — {exc}") from exc


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
            file = next((shot["file"] for shot in (start or {"shots": []})["shots"]
                         if shot["hwnd"] == hwnd and shot.get("drawn")), None)
            if file:
                shutil.copyfile(evidence / f"driver{runs.n}" / file, folder / f"{s['id']}-{VIEW}-{state}.png")
            elif why or not found:
                found.append(why or f"`{kind}` 화면의 창이 찍히지 않았다")
            findings += [finding(s["id"], state, what) for what in found]
            if start:   # presence counts off any read; absence only off reads that all completed
                anywhere |= features(start, shown=False)
                hidden |= virtual(start)
            whole = whole and complete(report) and hwnd is not None
            if s["primary"] and state == "full" and first is None:
                first = (s, report, start, hwnd) if complete(report) and hwnd is not None else False
    out, shown = placement(runs, front, frequency, first)
    findings += out
    anywhere |= shown
    lead = next(s for s in front["screens"] if s["primary"])["id"]
    for f, cls in frequency.items():
        if cls == "never" and f in anywhere:
            findings.append(finding(lead, "full", f"화면에 없기로 한 `{f}` 의 컨트롤이 화면에 있다"))
        if cls != "never" and f not in anywhere and whole:
            findings.append(finding(lead, "full", f"`{PREFIX}{f}` 가 어느 화면의 트리에도 없다"))
    if hidden and whole and any(cls in ("rarely", "never") for cls in frequency.values()):
        findings.append(finding(lead, "full", "가상화된 목록이 있어 첫 화면에 없어야 할 기능이 없다는 것을 확인하지 "
                                              "못했다 — 화면 검사 중에는 목록 가상화를 끈다"))
    return [dict(f) for f in dict.fromkeys(tuple(f.items()) for f in findings)]   # a probe repeats a screen's


def placement(runs: Launches, front: dict, frequency: dict, first) -> tuple[list[dict], set[str]]:
    """The owner's map read off the first screen's `full` state, its own window's controls only; each breach is
    high. A first screen whose read did not complete is a finding of its own: absence counts only off a read
    that completed. The findings, and the features one invocation showed."""

    if not first:
        sid = next(s for s in front["screens"] if s["primary"])["id"]
        return [finding(sid, "full", "첫 화면의 `full` 상태를 읽지 못해 배치를 확인하지 못했다")], set()
    s, report, start, hwnd = first
    visible, there = features(start, shown=True, windows={hwnd}), features(start, shown=False, windows={hwnd})
    out, shown = [], set()
    for f, cls in frequency.items():
        if cls == "every" and f not in visible:
            out.append(finding(s["id"], "full", f"늘 쓰는 기능 `{f}` 가 첫 화면에 바로 보이지 않는다"))
        if cls in ("rarely", "never") and f in there:
            out.append(finding(s["id"], "full", f"`{f}` 는 첫 화면에 없어야 한다"))
        if cls == "sometimes" and f not in visible:
            ok, carried = one_action(runs, s, start, hwnd, f)
            out += [finding(s["id"], "full", what) for what in carried]
            if ok:
                shown.add(f)
            else:
                out.append(finding(s["id"], "full", f"가끔 쓰는 기능 `{f}` 가 한 번 눌러서 보이지 않는다"))
    return out, shown


def one_action(runs: Launches, s: dict, start: dict, hwnd: int, fid: str) -> tuple[bool, list[str]]:
    """Whether invoking one of the first screen's controls in view, by its `AutomationId`, from a fresh start
    where the feature was not shown, shows it in the main window or a window it owns; and what the probes could
    not prove, which is a finding, never a miss."""

    import desktop_driver

    targets = [node["id"] for node in start["nodes"]
               if not node.get("foreign") and node["window"] == hwnd and node.get("id") and node.get("rect")
               and not node.get("offscreen") and "Invoke" in node.get("patterns", [])]
    carried = []
    for target in list(dict.fromkeys(targets))[:MAX_TAPS]:
        report = runs.drive(s["id"], "full", [{"name": TRY, "action": "invoke", "id": target,
                                               "expect": {"id": PREFIX + fid}}])
        carried += [f for f in desktop_driver.findings(report) if f != MISSED and f not in carried]
        if not complete(report) or not report.get("steps"):
            continue
        states = {state["id"]: state for state in report["states"]}
        fresh, after = report["states"][0], states[report["steps"][0]["state"]]
        if fid not in features(fresh, shown=True, windows=mine(report, fresh)) \
                and fid in features(after, shown=True, windows=mine(report, after)):
            return True, carried
    return False, carried
