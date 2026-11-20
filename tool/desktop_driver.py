"""The desktop look's driver, run from the server: it starts `desktop_driver.ps1` as a process of its own on the
look's owned desktop, holds each of its phases to a deadline and ends its job when one passes, and reads the
mechanical checks from the tree it saved (docs/research/oneshot-desktop-session.md, "Reading and driving it")."""

import json
import os
import time
from pathlib import Path

from common import write_record

POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
SCRIPT = Path(__file__).with_name("desktop_driver.ps1")
PHASE_SECONDS = 10   # each operation: a resize, one window's shot, one window's tree read, an action, a check
LAUNCH_SECONDS = 30   # Windows PowerShell's start and its compile of the driver's types, before the first operation
ENDED_SECONDS = 5   # the driver's job proving its end, once a deadline passed
RESULT = "result.json"   # the server's final report: the driver's report, its last operation, deadline and end
INTERACTIVE = {"Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem", "MenuItem", "RadioButton",
               "Slider", "Spinner", "SplitButton", "TabItem", "TreeItem"}
ACTING = {"Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "Value"}
SMALLEST = 24   # physical pixels, each side


def arguments(script: Path, request: Path) -> list[str]:
    """Windows PowerShell 5.1's arguments for the driver: the Store PowerShell starts outside the job."""

    return ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Request", str(request)]


def _read(file: Path) -> dict | None:
    try:
        return json.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None   # not written yet, caught between its two writes, or held by the writer's replace


def _allowed(op: str | None, wait: float, settle_ms: int) -> float:
    """One operation's deadline: the launch, the wait for a first window and a settle each get their own."""

    if op is None:
        return LAUNCH_SECONDS
    if op == "wait for a window":
        return wait + PHASE_SECONDS
    if op == "settle":
        return settle_ms / 1000 + PHASE_SECONDS
    return PHASE_SECONDS


def drive(look, app: str, window: tuple[int, int], steps: list[dict], *, wait: float = 30, settle_ms: int = 1000,
          key: str = "driver", halt=None) -> dict:
    """Drive the app `look` started under `app`: choose its main window, size it to `window`, then shoot and read
    each state, before and after every step. The driver names each operation before it starts, and each is held
    to its own deadline. The driver's report, with `last` naming the last operation it entered and `ended`
    naming one whose deadline passed; the driver's job is then ended, and `end` holds the proof of that end.
    That report is also saved as `result.json` beside the driver's own files, so the evidence a reader opens
    later carries the deadline and the end proof, which the driver's `driver.json` cannot. A `halt` set while it
    runs ends the driver at once, `ended` then `halted`."""

    look.check_key(key)   # before any path is made from it
    folder = look.folder / "evidence" / key
    folder.mkdir()
    request = {"out": str(folder), "job": look.jobs[app].name, "window": list(window), "steps": steps,
               "wait_seconds": wait, "settle_ms": settle_ms}
    (folder / "request.json").write_text(json.dumps(request, indent=1), encoding="utf-8")
    look.start(key, [str(POWERSHELL), *arguments(SCRIPT, folder / "request.json")], folder, halt=halt)
    ended, end = _watch(look.jobs[key], folder / "progress.json", wait, settle_ms, halt)
    report = _read(folder / "driver.json") or {"phase": None}
    marker = _read(folder / "progress.json")
    report.update(last=marker["op"] if _valid(marker) else None, ended=ended, end=end)
    write_record(folder / RESULT, report)   # a name the driver never writes: the evidence holds the server's verdict
    return report


def _watch(job, progress: Path, wait: float, settle_ms: int, halt=None) -> tuple[str | None, dict | None]:
    """Hold the driver's operations to their deadlines until its job finished. The clock restarts only for a
    valid, strictly newer sequence: a missed or torn read keeps the operation and the clock it had. The operation
    whose deadline passed (`halted` when `halt` was set) and the proof of the job's end, or two Nones."""

    seq, op, since = 0, None, time.monotonic()
    while not job.finished():
        if halt is not None and halt.is_set():
            return "halted", job.end(ENDED_SECONDS)
        marker = _read(progress)
        if _valid(marker) and marker["seq"] > seq:
            seq, op, since = marker["seq"], marker["op"], time.monotonic()
        if time.monotonic() - since > _allowed(op, wait, settle_ms):
            return op or "launch", job.end(ENDED_SECONDS)
        time.sleep(0.1)
    return None, None


def _valid(marker) -> bool:
    """A progress marker the watchdog may act on: an object with a positive integer sequence and an operation."""

    return (isinstance(marker, dict) and type(marker.get("seq")) is int and marker["seq"] > 0
            and isinstance(marker.get("op"), str) and bool(marker["op"].strip()))


def expectation(expect) -> tuple[str, str] | None:
    """The one property a postcondition looks for: a non-blank `id`, else a non-blank `name`; None when it names
    neither."""

    for key in ("id", "name"):
        value = expect.get(key) if isinstance(expect, dict) else None
        if isinstance(value, str) and value.strip():
            return key, value
    return None


def holds(state: dict, expect: dict) -> bool:
    """A step's postcondition, read off the state it left: an element of the app with the expected id or name. A
    node of another process, an embedded fragment, never satisfies one."""

    key, value = expectation(expect)
    return any(not node.get("foreign") and node.get(key) == value for node in state["nodes"])


def findings(report: dict) -> list[str]:
    """The mechanical findings in a driver's report: an interactive control without a name, a control outside its
    window, a target smaller than 24×24 physical pixels, a refused start, a wrong size, a step that failed. What
    the look could not prove is a finding too: a driver that left no report, stopped or failed before `done`, or
    whose end was not proven; a size not read back; no start state; a state with no app window; a window not
    drawn or not read; a step not performed, without its state, or without a postcondition read. An empty list
    therefore means a finished run whose every observation was made."""

    found = []
    if report.get("ended"):
        found.append(f"the driver passed its deadline at {report['ended']}")
    if report.get("end") and not report["end"]["clean"]:
        found.append(f"the driver's end was not proven: {'; '.join(report['end']['reasons'])}")
    if report.get("phase") is None:
        return [*found, "the driver left no report"]
    if report.get("refusal"):
        found.append(f"refused: {report['refusal']}")
    if report.get("error"):
        found.append(f"the driver failed at {report.get('last')}: {report['error']}")
    elif report["phase"] not in ("done", "refused") and not report.get("ended"):
        found.append(f"the driver stopped before finishing, at {report.get('last')}")
    size = report.get("size") or {}
    if report["phase"] == "done" and not size.get("frame"):
        found.append("the main window's size was not read back")
    elif size and not size.get("moved"):
        found.append("the main window could not be sized")
    if size.get("frame") and size["frame"][2:] != size["asked"]:
        found.append(f"the main window is {size['frame'][2]}x{size['frame'][3]}, not {size['asked'][0]}x{size['asked'][1]}")
    if report["phase"] == "done" and not any(state["name"] == "start" for state in report.get("states", [])):
        found.append("no start state was read")
    for state in report.get("states", []):
        if not state["windows"]:
            found.append(f"{state['name']}: no app window to shoot")
        reads = {read["hwnd"]: read for read in state.get("reads", [])}
        for shot in state["shots"]:
            if not shot["drawn"]:
                found.append(f"{state['name']}: window {shot['hwnd']} was not drawn: {shot.get('error')}")
            elif not reads.get(shot["hwnd"], {}).get("read"):
                why = reads.get(shot["hwnd"], {}).get("error", "no read was recorded")
                found.append(f"{state['name']}: window {shot['hwnd']}'s tree was not read: {why}")
        frames = {window["hwnd"]: window["frame"] for window in state["windows"]}
        for node in state["nodes"]:
            if node.get("foreign"):   # another process's fragment: not the app's evidence
                continue
            rect, frame = node["rect"], frames.get(node["window"])
            if not rect:   # not laid out at all, such as Edge's hidden edit: nothing a person can reach
                continue
            acting = node["type"] in INTERACTIVE or ACTING & set(node["patterns"])
            where = f"{state['name']}: {node['type']} {node['id'] or node['name']!r}"
            if acting and not node["name"].strip():
                found.append(f"{where} has no name")
            if node["offscreen"] or node["depth"] == 0:   # the window itself: its frame is the bound
                continue
            if frame and (rect[0] < frame[0] or rect[1] < frame[1] or rect[0] + rect[2] > frame[0] + frame[2]
                          or rect[1] + rect[3] > frame[1] + frame[3]):
                found.append(f"{where} lies outside its window")
            if acting and (rect[2] < SMALLEST or rect[3] < SMALLEST):
                found.append(f"{where} is {rect[2]}x{rect[3]}, under {SMALLEST}x{SMALLEST}")
    states = {state["id"]: state for state in report.get("states", []) if state.get("id")}
    for step in report.get("steps", []):   # each read here, off the state the step names, never off a flag
        state = states.get(step.get("state"))
        if step.get("error"):
            found.append(f"step {step['name']}: {step['error']}")
        elif not step.get("acted"):
            found.append(f"step {step['name']}: it was not performed")
        elif not state:
            found.append(f"step {step['name']}: its state was not read")
        elif not expectation(step.get("expect")):
            found.append(f"step {step['name']}: it names no postcondition")
        elif not holds(state, step["expect"]):
            found.append(f"step {step['name']}: its postcondition was not read")
    return found
