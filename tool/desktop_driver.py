"""The desktop look's driver, run from the server: it starts `desktop_driver.ps1` as a process of its own on the
look's owned desktop, holds each of its phases to a deadline and ends its job when one passes, and reads the
mechanical checks from the tree it saved (docs/research/oneshot-desktop-session.md, "Reading and driving it")."""

import json
import os
import time
from pathlib import Path

POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
SCRIPT = Path(__file__).with_name("desktop_driver.ps1")
PHASE_SECONDS = 10   # a resize, a shot, a tree read, an action and its postcondition each
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
    except (FileNotFoundError, json.JSONDecodeError):
        return None   # not written yet, or caught between its two writes


def drive(look, app: str, window: tuple[int, int], steps: list[dict], *, wait: float = 30, settle_ms: int = 1000,
          key: str = "driver") -> dict:
    """Drive the app `look` started under `app`: choose its main window, size it to `window`, then shoot and read
    each state, before and after every step. The driver's report, with `ended` naming a phase whose deadline
    passed; the driver is then ended with its job."""

    look.observe()
    folder = look.folder / "evidence" / key
    folder.mkdir()
    request = {"out": str(folder), "pids": [member["pid"] for member in look.record["jobs"][app]["members"]],
               "window": list(window), "steps": steps, "wait_seconds": wait, "settle_ms": settle_ms}
    (folder / "request.json").write_text(json.dumps(request, indent=1), encoding="utf-8")
    look.start(key, [str(POWERSHELL), *arguments(SCRIPT, folder / "request.json")], folder)
    job, phase, since, ended = look.jobs[key], None, time.monotonic(), None
    while not job.finished():
        report = _read(folder / "driver.json") or {}
        if report.get("phase") != phase:
            phase, since = report.get("phase"), time.monotonic()
        allowed = wait + PHASE_SECONDS + settle_ms / 1000 if phase is None else PHASE_SECONDS + settle_ms / 1000
        if time.monotonic() - since > allowed:
            ended = phase or "starting"
            job.end(5)
            break
        time.sleep(0.1)
    report = _read(folder / "driver.json") or {"phase": None}
    report["ended"] = ended
    return report


def findings(report: dict) -> list[str]:
    """The mechanical findings in a driver's report: an interactive control without a name, a control outside its
    window, a target smaller than 24×24 physical pixels, a refused start, a wrong size, a step that failed."""

    found = []
    if report.get("refusal"):
        found.append(f"refused: {report['refusal']}")
    if report.get("ended"):
        found.append(f"the driver passed its deadline at {report['ended']}")
    size = report.get("size") or {}
    if size and size.get("frame") and size["frame"][2:] != size["asked"]:
        found.append(f"the main window is {size['frame'][2]}x{size['frame'][3]}, not {size['asked'][0]}x{size['asked'][1]}")
    for state in report.get("states", []):
        frames = {window["hwnd"]: window["frame"] for window in state["windows"]}
        for node in state["nodes"]:
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
    for step in report.get("steps", []):
        if step.get("error"):
            found.append(f"step {step['name']}: {step['error']}")
        elif step.get("postcondition") is False:
            found.append(f"step {step['name']}: its postcondition was not read")
    return found
