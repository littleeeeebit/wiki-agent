"""oneshot 9e4a: a PC app's look — the frozen build started once for each screen and state, its windows shot and read
by the driver, and the mechanical, placement and coverage findings off the reports (docs/plans/oneshot/9-kinds.md,
"The look loop", "Screens").

The program's starts are a stand-in that answers each with a driver report as the driver writes it, and writes the
shots it names. The findings are read by the real `desktop_driver.findings`.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from main import oneshot_desktop_look as pc_look

SCREENS = [{"id": "S1", "kind": "desktop", "surface": "window", "purpose": "기록", "features": ["history", "search"],
            "primary": True, "reached": "start"},
           {"id": "S2", "kind": "desktop", "surface": "dialog", "purpose": "설정", "features": ["export"],
            "primary": False, "reached": "S1"}]
FREQUENCY = {"history": "every", "search": "sometimes", "export": "rarely", "admin": "never"}
MAIN, DIALOG = 1, 2


def node(fid: str, window: int = MAIN, **over) -> dict:
    return {"window": window, "depth": 1, "foreign": False, "type": "Button", "name": fid, "id": f"feature:{fid}",
            "enabled": True, "offscreen": False, "rect": [10, 10, 100, 40], "patterns": ["Invoke"], **over}


def window(hwnd: int) -> dict:
    return {"window": hwnd, "depth": 0, "foreign": False, "type": "Window", "name": "app", "id": "", "enabled": True,
            "offscreen": False, "rect": [0, 0, 1280, 800], "patterns": []}


def report(nodes: list[dict], windows=(MAIN,), sid="S1", steps=()) -> dict:
    """A finished driver report whose start state shows `windows`, each drawn and read, holding `nodes`."""

    def state(i: int, name: str, held: list[dict]) -> dict:
        return {"id": f"s{i:02d}", "name": name,
                "windows": [{"hwnd": h, "frame": [0, 0, 1280, 800]} for h in windows],
                "shots": [{"hwnd": h, "drawn": True, "file": f"s{i:02d}-{h}.png"} for h in windows],
                "reads": [{"hwnd": h, "read": True} for h in windows],
                "nodes": [window(h) for h in windows] + held}

    states = [state(0, "start", nodes)] + [state(i, f"after-{s['name']}", s.pop("shows")) for i, s in enumerate(steps, 1)]
    return {"phase": "done", "main": {"hwnd": MAIN}, "size": {"asked": [1280, 800], "moved": True,
                                                              "frame": [0, 0, 1280, 800]},
            "states": states, "steps": [{**s, "acted": True, "state": f"s{i:02d}"} for i, s in enumerate(steps, 1)],
            "ended": None, "end": None, "last": "done", "sid": sid}


class Runs:
    """The program's starts: each answered by `answer(sid, state, steps)`, its shots written as the driver would."""

    def __init__(self, evidence: Path, answer):
        self.evidence, self.answer, self.n, self.started = evidence, answer, 0, []

    def drive(self, sid, state, steps):
        self.n += 1
        self.started.append((sid, state, [s.get("id") for s in steps]))
        got = self.answer(sid, state, steps)
        folder = self.evidence / f"driver{self.n}"
        folder.mkdir(parents=True)
        for s in got.get("states", []):
            for shot in s["shots"]:
                (folder / shot["file"]).write_bytes(f"{sid}-{state}-{shot['hwnd']}".encode())
        return got, []


def shown(sid, state, steps):
    """A program that shows every feature where the owner's map wants it: `search` one invocation away."""

    if steps:   # only `open-search` opens it
        after = [node("search")] if steps[0]["id"] == "open-search" else [node("history")]
        return report([node("history")], steps=[{"name": "try", "action": "invoke", "id": steps[0]["id"],
                                                 "expect": steps[0]["expect"], "shows": after}])
    if sid == "S1":
        return report([node("history"), node("open-search", id="open-search", name="찾기")])
    return report([node("export", window=DIALOG)], windows=(MAIN, DIALOG))


def inspect(tmp_path, answer=shown, frequency=FREQUENCY):
    runs = Runs(tmp_path / "evidence", answer)
    spec = {"oneshot": {"front": {"screens": SCREENS}, "frequency": frequency}}
    found = pc_look.inspect(runs, spec, tmp_path / "shots", tmp_path / "evidence")
    return found, runs


def test_every_screen_and_state_is_started_and_shot_and_a_program_that_follows_the_map_passes(tmp_path):
    (tmp_path / "shots").mkdir()
    found, runs = inspect(tmp_path)
    assert found == []
    assert [(sid, state) for sid, state, steps in runs.started if not steps] == [
        (sid, state) for sid in ("S1", "S2") for state in ("empty", "full", "loading", "error")]
    assert runs.started[-1] == ("S1", "full", ["open-search"])   # `search`: one invocation from the first screen
    shots = tmp_path / "shots"
    assert sorted(p.name for p in shots.iterdir()) == sorted(
        f"{sid}-window-{state}.png" for sid in ("S1", "S2") for state in ("empty", "full", "loading", "error"))
    assert (shots / "S2-window-full.png").read_bytes() == b"S2-full-2"   # a dialog screen is its own window's shot
    assert (shots / "S1-window-full.png").read_bytes() == b"S1-full-1"


def test_a_driver_report_that_misses_an_approved_state_is_not_a_clean_look(tmp_path):
    (tmp_path / "shots").mkdir()

    def stops_early(sid, state, steps):
        got = shown(sid, state, steps)
        return {**got, "phase": "read s00", "states": []} if (sid, state) == ("S2", "loading") else got

    found, _ = inspect(tmp_path, stops_early)
    assert found == [{"severity": "high", "screen": "S2", "viewport": "window", "state": "loading",
                      "what": "the driver stopped before finishing, at done"}]


def test_a_complete_report_with_a_never_feature_is_a_placement_finding(tmp_path):
    (tmp_path / "shots").mkdir()

    def admin(sid, state, steps):
        got = shown(sid, state, steps)
        if (sid, state) == ("S2", "error"):   # deep in an error state, never on the first screen
            got["states"][0]["nodes"].append(node("admin", window=DIALOG))
        return got

    found, _ = inspect(tmp_path, admin)
    assert [f["what"] for f in found] == ["화면에 없기로 한 `admin` 의 컨트롤이 화면에 있다"]


@pytest.mark.parametrize("change, said", [
    ({"history": "every", "search": "every"}, "늘 쓰는 기능 `search` 가 첫 화면에 바로 보이지 않는다"),
    ({"history": "rarely"}, "`history` 는 첫 화면에 없어야 한다"),
    ({"missing": "rarely"}, "`feature:missing` 가 어느 화면의 트리에도 없다"),
    ({"export": "sometimes"}, "가끔 쓰는 기능 `export` 가 한 번 눌러서 보이지 않는다"),
])
def test_the_owners_map_is_read_off_the_first_screen(tmp_path, change, said):
    (tmp_path / "shots").mkdir()
    found, _ = inspect(tmp_path, frequency={**FREQUENCY, **change})
    assert said in [f["what"] for f in found]


def test_a_feature_laid_out_off_screen_is_not_shown_on_the_first_screen(tmp_path):
    (tmp_path / "shots").mkdir()

    def hidden(sid, state, steps):
        got = shown(sid, state, steps)
        if sid == "S1" and not steps:
            got["states"][0]["nodes"][1]["offscreen"] = True   # `history`, in the tree but not in view
        return got

    found, _ = inspect(tmp_path, hidden)
    assert [f["what"] for f in found] == ["늘 쓰는 기능 `history` 가 첫 화면에 바로 보이지 않는다"]


def test_a_dialog_screen_without_its_own_window_is_a_finding(tmp_path):
    (tmp_path / "shots").mkdir()

    def no_dialog(sid, state, steps):
        return report([node("export")]) if sid == "S2" and not steps else shown(sid, state, steps)

    found, _ = inspect(tmp_path, no_dialog)
    assert {f["what"] for f in found if f["screen"] == "S2"} == {"`dialog` 화면의 창이 찍히지 않았다"}


PROGRAM = ("import os, pathlib, sys, tkinter; pathlib.Path(sys.argv[2]).open('a').write("   # what it was started with
           "os.environ['ONESHOT_SCREEN'] + ' ' + os.environ['ONESHOT_STATE'] + ' ' + "
           "str(pathlib.Path(sys.argv[1]).is_dir()) + '\\n'); root = tkinter.Tk(); "
           "root.title(os.environ['ONESHOT_SCREEN']); tkinter.Label(root, text=os.environ['ONESHOT_STATE']).pack(); "
           "root.after(60000, root.destroy); root.mainloop()")


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
def test_the_frozen_program_is_started_for_each_state_with_its_settings_and_the_look_is_proven_clean(tmp_path):
    python = Path(sys.executable)   # its standard library beside it: the build folder is its own
    build = {"identity": "e" * 64, "folder": str(python.parent),
             "launch": {"program": python.name, "args": ["-c", PROGRAM, "{data}", str(tmp_path / "seen.txt")],
                        "cwd": ".", "env": {}}}
    screens = [{**SCREENS[0], "features": []}]
    spec = {"repo": "budget", "oneshot": {"profile": {"window": "1024x768"},
                                          "front": {"screens": screens}, "frequency": {}}}
    looks = tmp_path / "store"
    with patch.object(pc_look.oneshot_spec, "store", lambda repo: type("S", (), {"folder": looks})()):
        found = pc_look.look(spec, build, tmp_path / "shots")
    assert found == [], found
    assert sorted(p.name for p in (tmp_path / "shots").iterdir()) == sorted(
        f"S1-window-{state}.png" for state in ("empty", "full", "loading", "error"))
    assert (tmp_path / "seen.txt").read_text().split("\n")[:-1] == [
        f"S1 {state} True" for state in ("empty", "full", "loading", "error")]   # each `{data}` a fresh folder
    import desktop_session
    [session] = (looks / "looks").iterdir()
    record = desktop_session.load(session)
    assert record["state"] == "proven" and record["proof"]["clean"], record
    assert not any(p.name.startswith("data") for p in session.iterdir())   # each `{data}` folder removed


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
def test_a_look_whose_cleanup_is_not_proven_stops_the_stage(tmp_path):
    import desktop_session

    @contextmanager
    def held(root, name):
        yield type("Look", (), {"folder": tmp_path})()
        raise desktop_session.Refused("the desktop look is held: pid 4 is still running")

    build = {"identity": "f" * 64, "folder": str(tmp_path), "launch": {}}
    spec = {"repo": "budget", "oneshot": {"profile": {"window": "1280x800"}}}
    with patch.object(desktop_session, "look", held), patch.object(pc_look, "inspect", lambda *a: []), \
            patch.object(pc_look.oneshot_spec, "store", lambda repo: type("S", (), {"folder": tmp_path})()), \
            pytest.raises(RuntimeError, match="정리하지 못했다 — the desktop look is held"):
        pc_look.look(spec, build, tmp_path / "shots")
