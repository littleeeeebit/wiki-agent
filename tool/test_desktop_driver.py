"""The desktop look's driver: the main window chosen once and sized, each state shot and read, steps through
patterns only, a deadline per phase, and the mechanical findings (docs/research/oneshot-desktop-session.md)."""

import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktops")

if sys.platform == "win32":
    import desktop_driver
    import desktop_session

WINDOW = ("import tkinter; root = tkinter.Tk(); root.title('fixture'); tkinter.Label(root, text='ready').pack(); "
          "root.after(60000, root.destroy); root.mainloop()")
TWO = ("import tkinter; root = tkinter.Tk(); root.title('one'); other = tkinter.Toplevel(root); other.title('two'); "
       "root.after(60000, root.destroy); root.mainloop()")
NONE = "import time; time.sleep(60)"


def python(code):
    return [sys.executable, "-c", code]


def test_the_main_window_is_sized_to_the_card_shot_and_read(tmp_path):
    with desktop_session.look(tmp_path, "tk") as look:
        look.start("app", python(WINDOW), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], settle_ms=500)

    assert report["phase"] == "done" and report["main"]["title"] == "fixture", report
    assert report["size"]["frame"][2:] == [640, 480] and desktop_driver.findings(report) == []
    start = report["states"][0]
    assert start["name"] == "start" and start["shots"][0]["drawn"]
    assert (tmp_path / "tk" / "evidence" / "driver" / start["shots"][0]["file"]).stat().st_size > 0
    assert start["nodes"][0]["type"] == "Window" and start["nodes"][0]["name"] == "fixture"


def test_two_candidate_windows_at_startup_are_a_refusal(tmp_path):
    with desktop_session.look(tmp_path, "two") as look:
        look.start("app", python(TWO), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], settle_ms=500)

    assert report["refusal"] == "2 candidate main windows at startup"
    assert desktop_driver.findings(report) == ["refused: 2 candidate main windows at startup"]


def test_an_app_that_shows_no_window_is_a_refusal(tmp_path):
    with desktop_session.look(tmp_path, "none") as look:
        look.start("app", python(NONE), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], wait=2, settle_ms=200)

    assert report["refusal"] == "the app showed no window on the owned desktop"


STUCK = """
import json, sys, time
from pathlib import Path
request = json.loads(Path(sys.argv[-1]).read_text(encoding="utf-8"))
(Path(request["out"]) / "driver.json").write_text(json.dumps({"phase": "sized"}), encoding="utf-8")
time.sleep(120)
"""


def test_a_phase_past_its_deadline_ends_the_driver_and_is_named(tmp_path, monkeypatch):
    stuck = tmp_path / "stuck.py"
    stuck.write_text(STUCK, encoding="utf-8")
    monkeypatch.setattr(desktop_driver, "POWERSHELL", sys.executable)
    monkeypatch.setattr(desktop_driver, "SCRIPT", stuck)
    monkeypatch.setattr(desktop_driver, "PHASE_SECONDS", 1)
    monkeypatch.setattr(desktop_driver, "arguments", lambda script, request: [str(script), str(request)])

    with desktop_session.look(tmp_path, "stuck") as look:
        look.start("app", python(NONE), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], wait=1, settle_ms=0)
        assert look.jobs["driver"].finished()

    assert report["ended"] == "sized"
    assert desktop_driver.findings(report) == ["the driver passed its deadline at sized"]


def node(**fields):
    return {"window": 1, "depth": 1, "type": "Button", "name": "Go", "id": "go", "enabled": True,
            "offscreen": False, "rect": [10, 10, 40, 30], "patterns": ["Invoke"], **fields}


def test_the_mechanical_findings_read_off_a_report():
    report = {"size": {"asked": [800, 600], "frame": [0, 0, 786, 593]},
              "states": [{"name": "start", "windows": [{"hwnd": 1, "frame": [0, 0, 800, 600]}], "nodes": [
                  node(depth=0, type="Window", rect=[-7, 0, 814, 607], patterns=[]),   # the window itself
                  node(),
                  node(name="", id="blank"),
                  node(id="small", rect=[10, 10, 11, 11]),
                  node(id="outside", rect=[790, 10, 40, 30]),
                  node(id="hidden", rect=[900, 900, 1, 1], offscreen=True),
                  node(type="Text", name="", id="label", patterns=[], rect=[0, 0, 5, 5]),
                  node(type="Edit", name="", id="hidden-edit", rect=None, patterns=["Value"])]}],
              "steps": [{"name": "go", "error": None, "postcondition": False},
                        {"name": "set", "error": "2 elements match name"}]}

    assert desktop_driver.findings(report) == [
        "the main window is 786x593, not 800x600",
        "start: Button 'blank' has no name",
        "start: Button 'small' is 11x11, under 24x24",
        "start: Button 'outside' lies outside its window",
        "step go: its postcondition was not read",
        "step set: 2 elements match name",
    ]


def test_the_request_names_only_the_apps_own_processes(tmp_path, monkeypatch):
    seen = {}

    def starting(look, key, argv, cwd, extra=None):
        seen.update(json.loads((look.folder / "evidence" / key / "request.json").read_text(encoding="utf-8")))
        raise desktop_session.Refused("stop here")

    with desktop_session.look(tmp_path, "request") as look:
        root = look.start("app", python(NONE), tmp_path)
        monkeypatch.setattr(desktop_session.Look, "start", starting)
        with pytest.raises(desktop_session.Refused, match="stop here"):
            desktop_driver.drive(look, "app", (640, 480), [{"name": "go", "action": "invoke", "id": "go"}])

    assert seen["pids"] == [root["pid"]] and seen["window"] == [640, 480] and seen["steps"][0]["id"] == "go"


EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
FIXTURE = desktop_driver.Path(__file__).parent.parent / "docs" / "research" / "oneshot-desktop-edge-run" / "fixture.html" \
    if sys.platform == "win32" else None


@pytest.mark.skipif(sys.platform != "win32" or not __import__("os").environ.get("WIKI_DESKTOP_EDGE"),
                    reason="a real Edge run, on request: WIKI_DESKTOP_EDGE=1")
def test_the_edge_fixture_is_driven_through_a_pattern_and_its_cleanup_is_proven(tmp_path):
    with desktop_session.look(tmp_path, "edge") as look:
        profile = look.reserve("edge")
        look.start("app", [EDGE, f"--user-data-dir={profile}", "--no-first-run", "--lang=en-US", "--force-renderer-accessibility",
                           "--window-size=800,600", f"--app={FIXTURE.as_uri()}"], tmp_path)
        report = desktop_driver.drive(look, "app", (800, 600),
                                      [{"name": "go", "action": "invoke", "id": "go", "expect": {"name": "clicked"}}],
                                      settle_ms=1500)

    record = desktop_session.load(tmp_path / "edge")
    assert report["phase"] == "done" and report["steps"][0]["postcondition"] is True, report
    assert record["state"] == "proven" and record["proof"]["clean"], record["proof"]
    found = desktop_driver.findings(report)
    assert "start: Button 'tiny' is 11x11, under 24x24" in found and "start: Button 'tiny' has no name" in found
    assert record["jobs"]["app"]["proof"]["total"] == record["jobs"]["app"]["proof"]["joined"] > 1
