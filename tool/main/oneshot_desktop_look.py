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

from . import oneshot_spec
from .oneshot_device import MAX_TAPS, STATES

VIEW = "window"
PREFIX = "feature:"
END_SECONDS = 30
SETTLE_MS = 1000


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


def features(state: dict, shown: bool) -> set[str]:
    """The feature ids a state's own nodes carry as `feature:<id>`; with `shown`, only those laid out on screen."""

    return {node["id"][len(PREFIX):] for node in state["nodes"]
            if not node.get("foreign") and (node.get("id") or "").startswith(PREFIX)
            and (not shown or node.get("rect") and not node.get("offscreen"))}


class Launches:
    """The frozen build's starts inside one look: each a job of its own, ended before the next starts."""

    def __init__(self, look, build: dict, window: tuple[int, int], stopped):
        self.look, self.build, self.window, self.stopped, self.n = look, build, window, stopped, 0

    def drive(self, sid: str, state: str, steps: list[dict]) -> tuple[dict, list[str]]:
        """One start of the program opening `sid` in `state`, driven through `steps`: the driver's report, and
        what the end of the program's job could not prove."""

        import desktop_driver   # Windows only, as a PC app is

        halted(self.stopped)
        self.n += 1
        key = f"app{self.n}"
        launch, folder = self.build["launch"], Path(self.build["folder"])
        data = str(self.look.reserve(f"data{self.n}"))
        argv = [str(folder / launch["program"]), *(a.replace("{data}", data) for a in launch["args"])]
        extra = {**{k: v.replace("{data}", data) for k, v in launch["env"].items()},
                 "ONESHOT_SCREEN": sid, "ONESHOT_STATE": state}
        self.look.start(key, argv, folder / launch["cwd"], extra)
        try:
            report = desktop_driver.drive(self.look, key, self.window, steps, settle_ms=SETTLE_MS, key=f"driver{self.n}")
        finally:   # its members recorded first, so recovery names them however the end goes
            self.look.observe()
            end = self.look.jobs[key].end(END_SECONDS)
        return report, [] if end["clean"] else [f"앱을 끝내지 못했다: {'; '.join(end['reasons'])}"]


def picture(report: dict, surface: str) -> str | None:
    """The file of the start state's shot of the screen: the main window for a `window` screen, else the window
    the main window owns (its dialog, floating window or flyout)."""

    states = report.get("states") or []
    if not states or not report.get("main"):
        return None
    main = report["main"]["hwnd"]
    drawn = {shot["hwnd"]: shot["file"] for shot in states[0]["shots"] if shot.get("drawn")}
    if surface == "window":
        return drawn.get(main)
    return next((file for hwnd, file in drawn.items() if hwnd != main), None)


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
    findings, seen, anywhere, first = [], {f: False for f in frequency}, set(), None
    for s in front["screens"]:
        for state in STATES:
            report, ended = runs.drive(s["id"], state, [])
            found = [*desktop_driver.findings(report), *ended]
            file = picture(report, s.get("surface", "window"))
            if file:
                shutil.copyfile(evidence / f"driver{runs.n}" / file, folder / f"{s['id']}-{VIEW}-{state}.png")
            elif not found:
                found.append(f"`{s.get('surface', 'window')}` 화면의 창이 찍히지 않았다")
            findings += [finding(s["id"], state, what) for what in found]
            start = (report.get("states") or [None])[0]
            if start:
                anywhere |= features(start, shown=False)
                if state == "full":
                    for f in features(start, shown=False) & set(seen):
                        seen[f] = True
                    if s["primary"] and first is None:
                        first = (s, start)
    findings += placement(runs, front, frequency, first, seen)
    lead = next(s for s in front["screens"] if s["primary"])["id"]
    for f, cls in frequency.items():
        if cls == "never" and f in anywhere:
            findings.append(finding(lead, "full", f"화면에 없기로 한 `{f}` 의 컨트롤이 화면에 있다"))
        if cls != "never" and not seen[f]:
            findings.append(finding(lead, "full", f"`{PREFIX}{f}` 가 어느 화면의 트리에도 없다"))
    return findings


def placement(runs: Launches, front: dict, frequency: dict, first, seen: dict) -> list[dict]:
    """The owner's map read off the first screen's `full` state; each breach is high. A first screen that was
    never read is a finding of its own: absence counts only off a state that was read. A feature one invocation
    shows is marked `seen`."""

    if first is None:
        sid = next(s for s in front["screens"] if s["primary"])["id"]
        return [finding(sid, "full", "첫 화면의 `full` 상태를 읽지 못해 배치를 확인하지 못했다")]
    s, start = first
    visible, there, out = features(start, shown=True), features(start, shown=False), []
    for f, cls in frequency.items():
        if cls == "every" and f not in visible:
            out.append(finding(s["id"], "full", f"늘 쓰는 기능 `{f}` 가 첫 화면에 바로 보이지 않는다"))
        if cls in ("rarely", "never") and f in there:
            out.append(finding(s["id"], "full", f"`{f}` 는 첫 화면에 없어야 한다"))
        if cls == "sometimes" and f not in visible and not one_action(runs, s, start, f, seen):
            out.append(finding(s["id"], "full", f"가끔 쓰는 기능 `{f}` 가 한 번 눌러서 보이지 않는다"))
    return out


def one_action(runs: Launches, s: dict, start: dict, fid: str, seen: dict) -> bool:
    """Whether invoking one of the first screen's controls in view, by its `AutomationId`, shows the feature."""

    targets = [node["id"] for node in start["nodes"]
               if not node.get("foreign") and node.get("id") and node.get("rect") and not node.get("offscreen")
               and "Invoke" in node.get("patterns", [])]
    for target in list(dict.fromkeys(targets))[:MAX_TAPS]:
        report, _ = runs.drive(s["id"], "full", [{"name": "try", "action": "invoke", "id": target,
                                                  "expect": {"id": PREFIX + fid}}])
        states = {state["id"]: state for state in report.get("states") or []}
        step = (report.get("steps") or [{}])[0]
        if step.get("acted") and fid in features(states.get(step.get("state")) or {"nodes": []}, shown=True):
            seen[fid] = True
            return True
    return False
