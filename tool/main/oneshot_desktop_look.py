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

from common import masked

from . import oneshot_desktop, oneshot_look, oneshot_needs, oneshot_spec
from .oneshot_device import MAX_TAPS, STATES

VIEW = "window"
PREFIX = "feature:"
END_SECONDS = 30
SETTLE_MS = 1000
PROOF = 7   # how a look proves a `look` check: a look saved with another proves none of them
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

    def __init__(self, look, build: dict, window: tuple[int, int], stopped, shoot: bool = True, ready=None,
                 gate=None, given: dict | None = None):
        self.look, self.build, self.window, self.stopped, self.n = look, build, window, stopped, 0
        self.shoot = shoot   # false on the owner's desktop: no PrintWindow there
        self.ready = ready   # called with each start's process made but not yet run: raising never runs it
        self.gate = gate   # held over each start's last stop check and its resume
        self.given = given or {}   # each secret by its `env`, `None` for one not given (`oneshot_needs.given_in`)
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

    def drive(self, sid: str, state: str, steps: list[dict], then=None) -> dict:
        """One start of the program opening `sid` in `state`, driven through `steps`: the driver's report. No
        start follows one whose end is not proven (RuntimeError, which stops the stage), or one that changed the
        frozen copy (ValueError, back to the build). A start Windows refuses goes back to the build too. `then`,
        called with the report and the app's job key while the app still runs, puts its answer in `observed`."""

        import desktop_driver   # Windows only, as a PC app is
        import desktop_session

        self.due()
        self.n += 1
        key = f"app{self.n}"
        launch, folder = self.build["launch"], Path(self.build["folder"])
        self.data = data = str(self.look.reserve(f"data{self.n}"))   # this start's `{data}`, for `then` to read
        argv = [str(folder / launch["program"]), *(a.replace("{data}", data) for a in launch["args"])]
        # The owner's secrets over any spelling of their names the build's launch settings carry.
        secret = {k.upper() for k in self.given}
        extra = {**{k: v.replace("{data}", data) for k, v in launch["env"].items()
                    if k.upper() not in oneshot_desktop.LOOK_ENV and k.upper() not in secret},
                 **self.given, "ONESHOT_SCREEN": sid, "ONESHOT_STATE": state}
        # A step's `baseline`, a report file under `{data}`, is read by the driver just before that step acts.
        steps = [{**s, "baseline": str(Path(data) / s["baseline"])} if s.get("baseline") else s for s in steps]
        report, failed = {}, None
        try:
            self.look.start(key, argv, folder / launch["cwd"], extra, halt=self.halt, ready=self.ready,
                            gate=self.gate)
            report = desktop_driver.drive(self.look, key, self.window, steps, settle_ms=SETTLE_MS,
                                          key=f"driver{self.n}", halt=self.halt, shoot=self.shoot)
            if then is not None:
                report["observed"] = then(report, key)
        except desktop_session.Refused as exc:
            failed = ValueError(f"PC 앱을 띄우지 못했다 — {exc}")
            failed.__cause__ = exc
        except Exception as exc:   # the ends are proven first: an unproven one stops the stage, never a retry
            failed = exc
        self.look.observe()   # its members recorded first, so recovery names them however the end goes
        job = self.look.jobs.get(key)
        end = job.end(END_SECONDS) if job is not None else None
        for who, proof in (("앱을", end), ("드라이버를", report.get("end"))):
            if proof and not proof["clean"]:
                raise RuntimeError(f"PC 앱 화면 검사에서 {who} 끝내지 못했다 — {'; '.join(proof['reasons'])}") \
                    from failed
        self.due()   # the ends proven: a start or driver halted by the deadline goes back to the build, as an overrun
        if isinstance(failed, desktop_session.Halted):
            halted(lambda: True)
        if failed is not None:
            raise failed
        try:
            why = oneshot_desktop.altered(self.build)
        except OSError as exc:   # unreadable as settlement finds it: the build's to fix, never a new approval
            raise ValueError(f"얼린 PC 앱 빌드를 다시 읽지 못했다 — {exc}") from exc
        if why:
            raise ValueError(why)
        return report


def look(spec: dict, build: dict, folder: Path, stopped=never, *, env: dict | None = None,
         hide: dict[str, str] | None = None) -> list[dict]:
    """Every screen in every state of the frozen `build` shot into `folder` as `<screen>-window-<state>.png`, with
    the mechanical, placement and coverage findings. The look is cleaned and proven on every exit. Each start gets
    the owner's secrets `env` holds (a `gate_env` result); a screen whose read text holds a value of `hide` is a
    high finding and is never kept, and neither a finding nor a refusal carries one (`common.masked`)."""

    try:
        # The names the look and its desktop set themselves keep their values; every other secret is the owner's.
        own = oneshot_desktop.LOOK_ENV | oneshot_desktop.OWNED_ENV
        found = _look(spec, build, folder, stopped, oneshot_needs.given_in(spec, env, own), hide or {})
    except (ValueError, RuntimeError) as exc:
        if hide and (said := masked(str(exc), hide)) != str(exc):
            raise type(exc)(said) from None
        raise
    return [{**f, "what": masked(f["what"], hide)} for f in found] if hide else found


def _look(spec: dict, build: dict, folder: Path, stopped, given: dict, hide: dict[str, str]) -> list[dict]:
    import desktop_session   # Windows only, as a PC app is

    looks = oneshot_spec.store(spec["repo"]).folder / "looks"
    looks.mkdir(parents=True, exist_ok=True)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    try:
        # A look whose app carried secrets keeps only the checked shots it copied: its raw evidence goes with it.
        with desktop_session.look(looks, f"look-{build['identity'][:12]}-{uuid.uuid4().hex[:8]}",
                                  disposable=bool(hide)) as session:
            runs = Launches(session, build, size(spec["oneshot"]["profile"]), stopped, given=given)
            found = inspect(runs, spec, folder, session.folder / "evidence", hide) + checks(runs, spec, build)
    except desktop_session.Refused as exc:
        raise RuntimeError(f"PC 앱 화면 검사의 데스크톱을 정리하지 못했다 — {exc}") from exc
    runs.due()   # its cleanup proven: a look that ended past its deadline is no pass either
    return found


def texts(state: dict) -> list[str]:
    """Every string a state's read carries, its windows' and its elements' alike: what the shot of it can show."""

    out: list[str] = []

    def walk(x) -> None:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k != "shots":   # the driver's own file names
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk({k: state.get(k) for k in ("windows", "nodes")})
    return out


def checks(runs: Launches, spec: dict, build: dict) -> list[dict]:
    """Each `look` check the backend review left for the PC app, its scenario run from a fresh start of its screen
    and state: it passes only on a complete read whose every step acted and left its postcondition read, on one
    element of the window it names, off a state read after the step before; the last is the review's `expect`.
    A failed or unread one is a high finding for the builder, never a skip."""

    import desktop_driver

    o, out = spec["oneshot"], []
    owed = oneshot_desktop.looked_for(o)
    lead = next(s for s in o["front"]["screens"] if s["primary"])["id"] if owed else None
    for x in owed:
        failed = f"PC 앱에서 확인할 {x['id']}({x['requirement']}) 이 통과하지 못했다"
        scenario = (build.get("checks") or {}).get(x["id"])
        if scenario is None:
            out.append(finding(lead, "full", f"{failed} — 빌드에 그 시나리오가 없다 · 확인: {x['check']}"))
            continue
        # The review's step and postcondition close the scenario, whatever the build wrote there.
        steps = [*scenario["steps"][:-1], {"name": scenario["steps"][-1]["name"], **x["act"], "expect": x["expect"]}]
        report = runs.drive(scenario["screen"], scenario["state"], steps)
        if not proved(report, [s["expect"] for s in steps]):
            why = [f for f in desktop_driver.findings(report) if f.startswith("step ")][:2] or \
                ["the driver's read was not complete" if not complete(report) else
                 "a step's postcondition was not read on one element of its window after the step before, "
                 "or was not read false before the last step"]
            out.append(finding(scenario["screen"], scenario["state"], f"{failed} — {'; '.join(why)} · 확인: {x['check']}"))
            continue
    return out


def chain(report: dict, n: int) -> list[dict] | None:
    """The start state and the state each of the `n` steps of a complete read left, each acted and read after
    the one before; None when the read is not that."""

    states = report.get("states") or []
    order, steps, at = [s["id"] for s in states], report.get("steps") or [], 0
    if not complete(report) or len(steps) != n or not states:
        return None
    out = [states[0]]
    for step in steps:
        if not step.get("acted") or step.get("state") not in order or order.index(step["state"]) <= at:
            return None
        at = order.index(step["state"])
        out.append(states[at])
    return out


def shows(report: dict, state: dict, want: dict) -> bool:
    """`want` on the one element its id names in the window it names, in `state`."""

    import desktop_driver

    window = desktop_driver.scope(report, state, want.get("in", "main"))
    return window is not None and desktop_driver.holds(state, want, window)


def proved(report: dict, wants: list[dict]) -> bool:
    """Step k's state shows `wants[k]`, and the state before the last step read the last one false, so that step
    brought it about."""

    states = chain(report, len(wants))
    return states is not None and all(shows(report, s, w) for s, w in zip(states[1:], wants)) \
        and refuted(report, states[-2], wants[-1])


def refuted(report: dict, state: dict, want: dict) -> bool:
    """Whether `state` proves `want` false, never merely unproven: its window is the one window it names, read,
    and the one element there its id names reads another value for a property it names, or a complete read of
    that window holds none; with `in: owned`, no window the main window owns is proof too."""

    import desktop_driver

    main = report["main"]["hwnd"]
    if want.get("in") == "owned":
        owned = [w["hwnd"] for w in state["windows"] if w.get("owner") == main]
        if len(owned) != 1:   # none owned is proof only while the main window itself is there and read
            return not owned and drawn(state, main)
        window = owned[0]
    else:
        window = main
    key = desktop_driver.expectation(want)
    if key is None or not drawn(state, window):
        return False
    nodes = [n for n in state["nodes"] if not n.get("foreign") and n.get("window") == window and n.get(key[0]) == key[1]]
    if not nodes:
        return not partial(state, {window})
    return len(nodes) == 1 and any(nodes[0].get(k) is not None and nodes[0][k] != v
                                   for k, v in desktop_driver.wanted(want).items())


def inspect(runs: Launches, spec: dict, folder: Path, evidence: Path, hide: dict[str, str] | None = None) -> list[dict]:
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
            # The critic sends its pictures to a model: a start whose read text holds a secret is never kept.
            if file and hide and (leaked := oneshot_needs.carried(texts(start), [], hide)):
                found.append(oneshot_look.shown_secret(leaked))
                file = None
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
