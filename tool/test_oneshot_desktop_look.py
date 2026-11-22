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


def report(nodes: list[dict], windows=(MAIN,), sid="S1", steps=(), owners=None) -> dict:
    """A finished driver report whose start state shows `windows`, each drawn and read, holding `nodes`; each
    window but the main one owned by the main window unless `owners` says otherwise."""

    owners = owners or {}

    def state(i: int, name: str, held: list[dict]) -> dict:
        return {"id": f"s{i:02d}", "name": name,
                "windows": [{"hwnd": h, "frame": [0, 0, 1280, 800], "owner": owners.get(h, 0 if h == MAIN else MAIN)}
                            for h in windows],
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
        return got


def shown(sid, state, steps):
    """A program that shows every feature where the owner's map wants it: `search` one invocation away."""

    if steps:   # a fresh start, as the first screen shows it; only `open-search` opens it
        after = [node("search")] if steps[0]["id"] == "open-search" else [node("history")]
        return report([node("history"), node("open-search", id="open-search", name="찾기")],
                      steps=[{"name": "try", "action": "invoke", "id": steps[0]["id"], "expect": steps[0]["expect"],
                              "shows": after}])
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
    looks, checked = tmp_path / "store", []
    with patch.object(pc_look.oneshot_spec, "store", lambda repo: type("S", (), {"folder": looks})()), \
            patch.object(pc_look.oneshot_desktop, "altered", lambda b: checked.append(b) or ""):   # Python's folder
        found = pc_look.look(spec, build, tmp_path / "shots")
    assert found == [], found
    assert checked == [build] * 4   # the copy checked after every start
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
def test_a_look_whose_clean_teardown_ends_past_its_deadline_goes_back_to_the_build(tmp_path, monkeypatch):
    import desktop_session

    @contextmanager
    def slow(root, name):
        yield type("Look", (), {"folder": tmp_path})()
        monkeypatch.setattr(pc_look, "LOOK_SECONDS", 0)   # the clock crosses the deadline while the desktop ends

    build = {"identity": "f" * 64, "folder": str(tmp_path), "launch": {}}
    spec = {"repo": "budget", "oneshot": {"profile": {"window": "1280x800"}}}
    with patch.object(desktop_session, "look", slow), patch.object(pc_look, "inspect", lambda *a: []), \
            patch.object(pc_look, "checks", lambda *a: []), \
            patch.object(pc_look.oneshot_spec, "store", lambda repo: type("S", (), {"folder": tmp_path})()), \
            pytest.raises(ValueError, match="0분 안에 끝나지 않았다"):
        pc_look.look(spec, build, tmp_path / "shots")


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


class Desk:
    """A look's desktop as `Launches` uses it: each start recorded, each end as clean as `clean`."""

    def __init__(self, folder: Path, clean=True, refuse=None):
        self.folder, self.clean, self.refuse, self.jobs, self.started = folder, clean, refuse, {}, []

    def reserve(self, name):
        (self.folder / name).mkdir(exist_ok=True)
        return self.folder / name

    def start(self, key, argv, cwd, extra=None, halt=None, ready=None, gate=None):
        if self.refuse:
            raise self.refuse
        if ready is not None:   # the process made, not yet run
            ready()
        self.started.append(extra)
        self.jobs[key] = type("Job", (), {"end": lambda job, s: {"clean": self.clean, "reasons": ["pid 9 runs"]}})()

    def observe(self):
        pass


def launches(tmp_path, desk, env=None, altered=""):
    import desktop_driver

    build = {"folder": str(tmp_path), "launch": {"program": "app.exe", "args": ["{data}"], "cwd": ".",
                                                 "env": env or {}}}
    runs = pc_look.Launches(desk, build, (1280, 800), pc_look.never)
    return runs, patch.object(desktop_driver, "drive", lambda *a, **k: shown("S1", "full", [])), \
        patch.object(pc_look.oneshot_desktop, "altered", lambda b: altered)


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
def test_a_look_past_its_whole_deadline_halts_its_driver_and_goes_back_to_the_build(tmp_path, monkeypatch):
    desk = Desk(tmp_path)
    runs, driving, kept = launches(tmp_path, desk)
    with driving, kept:
        runs.drive("S1", "full", [])   # within the deadline
        monkeypatch.setattr(pc_look, "LOOK_SECONDS", 0)
        assert runs.halt.is_set()   # a driver still running ends at once
        with pytest.raises(ValueError, match="0분 안에 끝나지 않았다"):
            runs.drive("S1", "empty", [])
    assert len(desk.started) == 1   # no start after the deadline


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
def test_a_start_the_deadline_halts_says_so_and_any_other_halt_is_a_stop(tmp_path, monkeypatch):
    import desktop_session

    desk = Desk(tmp_path, refuse=desktop_session.Halted("halted before app.exe ran"))
    runs, driving, kept = launches(tmp_path, desk)
    with driving, kept:
        with pytest.raises(ValueError, match="검사 중에 멈췄다"):   # the owner's stop, the deadline not reached
            runs.drive("S1", "full", [])
        monkeypatch.setattr(pc_look, "LOOK_SECONDS", 0)
        with pytest.raises(ValueError, match="0분 안에 끝나지 않았다"):   # the deadline's, after the end is read
            runs.drive("S1", "full", [])


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
def test_a_start_whose_ready_check_refuses_never_runs_and_raises_what_it_raised(tmp_path):
    class Moved(Exception):
        pass

    def ready():
        raise Moved("HEAD moved")

    desk = Desk(tmp_path)
    runs, driving, kept = launches(tmp_path, desk)
    runs.ready = ready
    with driving, kept, pytest.raises(Moved, match="HEAD moved"):
        runs.drive("S1", "full", [])
    assert desk.started == []


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
def test_a_start_whose_end_is_not_proven_stops_the_look_before_another_start(tmp_path):
    desk = Desk(tmp_path, clean=False)
    runs, driving, kept = launches(tmp_path, desk)
    spec = {"oneshot": {"front": {"screens": SCREENS}, "frequency": FREQUENCY}}
    with driving, kept, pytest.raises(RuntimeError, match="앱을 끝내지 못했다 — pid 9 runs"):
        pc_look.inspect(runs, spec, tmp_path / "shots", tmp_path)
    assert len(desk.started) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
def test_the_look_alone_names_the_screen_and_state_and_a_changed_copy_or_a_refused_start_goes_back(tmp_path):
    import desktop_session

    desk = Desk(tmp_path)
    runs, driving, kept = launches(tmp_path, desk, env={"oneshot_screen": "S9", "Oneshot_State": "full"})
    with driving, kept:
        runs.drive("S2", "error", [])
    assert {k: v for k, v in desk.started[0].items() if k.upper().startswith("ONESHOT")} == {
        "ONESHOT_SCREEN": "S2", "ONESHOT_STATE": "error"}
    runs, driving, kept = launches(tmp_path, Desk(tmp_path), altered="PC 앱이 얼린 사본의 `a.txt` 를 바꿨다")
    with driving, kept, pytest.raises(ValueError, match="`a.txt` 를 바꿨다"):
        runs.drive("S1", "full", [])
    runs, driving, kept = launches(tmp_path, Desk(tmp_path, refuse=desktop_session.Refused("CreateProcessW: 193")))
    with driving, kept, pytest.raises(ValueError, match="띄우지 못했다 — CreateProcessW: 193"):
        runs.drive("S1", "full", [])


def copy(tmp_path) -> tuple[Path, dict]:
    """A frozen copy of one program, its manifest beside it, and the build the run saved for it."""

    import hashlib
    import json

    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"exe")
    files, launch = {"app.exe": hashlib.sha256(b"exe").hexdigest()}, {"program": "app.exe"}
    identity = hashlib.sha256(json.dumps({"files": files, "launch": launch}, sort_keys=True).encode()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    return app, {"folder": str(app), "identity": identity, "launch": launch}


def test_a_copy_is_altered_when_a_file_changes_goes_or_is_added(tmp_path):
    app, build = copy(tmp_path)
    assert pc_look.oneshot_desktop.altered(build) == ""
    (app / "settings.ini").write_text("x")
    assert "`settings.ini`" in pc_look.oneshot_desktop.altered(build)
    (app / "settings.ini").unlink()
    (app / "app.exe").write_bytes(b"changed")
    assert "`app.exe`" in pc_look.oneshot_desktop.altered(build)


@pytest.mark.parametrize("probe", ["timed-out", "already-shown"])
def test_a_sometimes_feature_counts_only_when_this_invocation_showed_it_on_a_complete_read(tmp_path, probe):
    (tmp_path / "shots").mkdir()

    def answer(sid, state, steps):
        got = shown(sid, state, steps)
        if steps and probe == "timed-out":   # the feature in the partial tree of a probe cut at its deadline
            got.update(phase="act s01", ended="act s01")
        elif steps:   # visible already at this start: the invocation showed nothing
            got["states"][0]["nodes"].append(node("search"))
        return got

    found, _ = inspect(tmp_path, answer)
    what = [f["what"] for f in found]
    assert "가끔 쓰는 기능 `search` 가 한 번 눌러서 보이지 않는다" in what
    assert ("the driver passed its deadline at act s01" in what) == (probe == "timed-out")


@pytest.mark.parametrize("windows, owners, said", [
    ((MAIN, DIALOG, 4), {4: 0}, None),   # an unrelated window opened too: the owned one is the dialog
    ((MAIN, DIALOG, 4), {}, "`dialog` 화면으로 볼 창이 2개다 — 메인 창은 그 화면의 창 하나만 소유한다"),
    ((MAIN, 4), {4: 0}, "`dialog` 화면의 창이 찍히지 않았다"),
])
def test_a_dialog_screen_is_the_one_window_the_main_window_owns(tmp_path, windows, owners, said):
    (tmp_path / "shots").mkdir()

    def answer(sid, state, steps):
        if sid == "S2" and not steps:
            return report([node("export", window=windows[1])], windows=windows, owners=owners)
        return shown(sid, state, steps)

    found, _ = inspect(tmp_path, answer)
    assert [f["what"] for f in found if f["screen"] == "S2"] == ([said] * 4 if said else [])
    if not said:
        assert (tmp_path / "shots" / "S2-window-full.png").read_bytes() == b"S2-full-2"


def test_a_feature_in_any_state_is_covered_and_absence_needs_reads_that_completed(tmp_path):
    (tmp_path / "shots").mkdir()

    def error_only(sid, state, steps):
        got = shown(sid, state, steps)
        if sid == "S2" and state != "error":   # `export` only in the dialog's error state
            got["states"][0]["nodes"] = [n for n in got["states"][0]["nodes"] if n["id"] != "feature:export"]
        return got

    assert inspect(tmp_path, error_only)[0] == []
    (tmp_path / "second" / "shots").mkdir(parents=True)

    def unread(sid, state, steps):
        got = shown(sid, state, steps)
        if (sid, state) == ("S1", "full") and not steps:
            got["states"][0]["reads"] = [{"hwnd": MAIN, "read": False, "error": "the tree could not be read"}]
            got["states"][0]["nodes"] = []
        return got

    what = [f["what"] for f in inspect(tmp_path / "second", unread)[0]]
    assert "start: window 1's tree was not read: the tree could not be read" in what
    assert "첫 화면의 `full` 상태를 읽지 못해 배치를 확인하지 못했다" in what
    assert not [w for w in what if "트리에도 없다" in w or "보이지 않는다" in w]


def test_a_state_that_shows_no_window_is_no_evidence_of_absence(tmp_path):
    (tmp_path / "shots").mkdir()

    def empty(sid, state, steps):
        got = shown(sid, state, steps)
        if (sid, state) == ("S1", "full") and not steps:
            got["states"][0].update(windows=[], shots=[], reads=[], nodes=[])
        return got

    what = [f["what"] for f in inspect(tmp_path, empty)[0]]
    assert "start: no app window to shoot" in what and "첫 화면의 `full` 상태를 읽지 못해 배치를 확인하지 못했다" in what
    assert not [w for w in what if "트리에도 없다" in w or "보이지 않는다" in w]


@pytest.mark.parametrize("failure", ["not-acted", "never-seen"])
def test_a_probe_that_did_not_act_is_a_finding_and_a_never_control_it_saw_is_one(tmp_path, failure):
    (tmp_path / "shots").mkdir()
    probes = []

    def answer(sid, state, steps):
        got = shown(sid, state, steps)
        if steps:
            probes.append(steps[0])
            if failure == "not-acted":   # two elements matched, or the control was disabled
                got["steps"][0].update(acted=False, state=None)
            else:
                got["states"][-1]["nodes"].append(node("admin"))
        return got

    what = [f["what"] for f in inspect(tmp_path, answer)[0]]
    assert all("in" not in step for step in probes)   # the first screen is the main window: searched there
    if failure == "not-acted":
        assert "step try: it was not performed" in what and "가끔 쓰는 기능 `search` 가 한 번 눌러서 보이지 않는다" in what
    else:
        assert "화면에 없기로 한 `admin` 의 컨트롤이 화면에 있다" in what


@pytest.mark.skipif(sys.platform != "win32", reason="a PC app is looked at on Windows only")
@pytest.mark.parametrize("how", ["driver-raised", "driver-end"])
def test_an_unproven_end_stops_the_look_even_when_the_driver_failed(tmp_path, how):
    import desktop_driver

    desk = Desk(tmp_path)   # the app's end unproven only once the driver raised
    runs, _, kept = launches(tmp_path, desk)

    def drive(*a, **k):
        if how == "driver-raised":
            desk.clean = False
            raise OSError("the driver's folder is gone")
        return {**shown("S1", "full", []), "end": {"clean": False, "reasons": ["pid 7 runs"]}}

    who = "드라이버를" if how == "driver-end" else "앱을"
    with patch.object(desktop_driver, "drive", drive), kept, pytest.raises(RuntimeError, match=f"{who} 끝내지 못했다"):
        runs.drive("S1", "full", [])


def scroller(items: int | None, realized: int, step: int = 40) -> dict:
    """A state whose list shows 4 rows of 40 pixels at a time, holds `items` rows as its provider counts them, and
    `realized` of them in its tree, the first ones."""

    rows = [node(f"row{i}", depth=2, type="ListItem", rect=[0, 100 + i * step, 200, step], patterns=[], id=f"row{i}")
            for i in range(realized)]
    box = {**node("list", depth=1, type="List", id="list", patterns=["Scroll", "ItemContainer"]),
           "rect": [0, 100, 200, 160], "items": items, "unread": None if items is None else items - realized}
    return {"windows": [{"hwnd": MAIN}], "nodes": [window(MAIN), box, *rows]}


def test_a_list_whose_tree_holds_fewer_items_than_its_provider_counts_is_partial():
    assert pc_look.partial(scroller(items=80, realized=6), {MAIN})   # virtualized: 6 of 80 realized
    assert pc_look.partial(scroller(items=22, realized=20), {MAIN})   # the last two not in the tree
    assert pc_look.partial(scroller(items=None, realized=20), {MAIN})   # a count the provider would not give
    assert not pc_look.partial(scroller(items=80, realized=80), {MAIN})   # not virtualized: every row read
    assert not pc_look.partial(scroller(items=80, realized=6), {DIALOG})   # another window's list
    state = scroller(items=20, realized=20)
    state["nodes"][-1]["id"] = "feature:history"   # the last row, scrolled out of view
    assert pc_look.features(state, shown=False) == {"history"} and pc_look.features(state, shown=True) == set()


def test_a_probe_never_starts_from_a_control_scrolled_out_of_view(tmp_path):
    (tmp_path / "shots").mkdir()
    tried = []

    def clipped(sid, state, steps):   # the opener lies in a list, below its view
        got = shown(sid, state, steps)
        tried.extend(step["id"] for step in steps)
        if sid == "S1":
            got["states"][0]["nodes"] = [*scroller(items=1, realized=0)["nodes"][1:2],
                                         *got["states"][0]["nodes"]]
            opener = next(n for n in got["states"][0]["nodes"] if n["id"] == "open-search")
            opener.update(depth=2, rect=[0, 400, 200, 40])
            got["states"][0]["nodes"].remove(opener)
            got["states"][0]["nodes"].insert(1, opener)
        return got

    what = [f["what"] for f in inspect(tmp_path, clipped)[0]]
    assert "open-search" not in tried and "가끔 쓰는 기능 `search` 가 한 번 눌러서 보이지 않는다" in what


def test_a_probe_is_proven_by_the_control_it_acted_on_never_one_in_another_window(tmp_path):
    (tmp_path / "shots").mkdir()

    def elsewhere(sid, state, steps):   # at the fresh start the opener is disabled; an owned window shows a copy
        got = shown(sid, state, steps)
        if steps:
            fresh = got["states"][0]
            next(n for n in fresh["nodes"] if n["id"] == "open-search")["enabled"] = False
            fresh["windows"].append({"hwnd": DIALOG, "frame": [0, 0, 300, 200], "owner": MAIN})
            fresh["shots"].append({"hwnd": DIALOG, "drawn": True, "file": f"{fresh['id']}-{DIALOG}.png"})
            fresh["reads"].append({"hwnd": DIALOG, "read": True})   # complete: only the window scope refuses it
            fresh["nodes"] += [window(DIALOG), node("open-search", window=DIALOG, id="open-search", name="찾기")]
        return got

    what = [f["what"] for f in inspect(tmp_path, elsewhere)[0]]
    assert "가끔 쓰는 기능 `search` 가 한 번 눌러서 보이지 않는다" in what


def test_a_control_scrolled_past_its_window_is_reachable_not_outside_it():
    import desktop_driver

    row = {**node("row", type="ListItem", rect=[0, 900, 200, 40], patterns=[], id="row"), "scrolled": True}
    state = {"id": "s00", "name": "start", "windows": [{"hwnd": MAIN, "frame": [0, 0, 1280, 800]}],
             "shots": [{"hwnd": MAIN, "drawn": True, "file": "s00-1.png"}], "reads": [{"hwnd": MAIN, "read": True}],
             "nodes": [window(MAIN), row]}
    got = {**report([]), "states": [state]}
    assert desktop_driver.findings(got) == []
    state["nodes"][1]["scrolled"] = False
    assert desktop_driver.findings(got) == ["start: ListItem 'row' lies outside its window"]


def test_a_folder_in_the_copy_that_cannot_be_listed_is_never_taken_for_an_unchanged_copy(tmp_path, monkeypatch):
    app, build = copy(tmp_path)
    (app / "extra").mkdir()
    (app / "extra" / "added.dll").write_bytes(b"x")
    walk = pc_look.oneshot_desktop.os.walk

    def denied(top, onerror=None):   # `extra` cannot be listed: what is in it is unknown
        for folder, dirs, names in walk(top, onerror=onerror):
            if Path(folder).name == "extra":
                onerror(PermissionError(13, "denied", folder))
                continue
            yield folder, dirs, names

    monkeypatch.setattr(pc_look.oneshot_desktop.os, "walk", denied)
    with pytest.raises(PermissionError):
        pc_look.oneshot_desktop.altered(build)


def test_while_the_copy_is_held_neither_a_file_nor_the_manifest_can_be_changed_and_after_both_can(tmp_path):
    app, build = copy(tmp_path)
    with pc_look.oneshot_desktop.held(build) as (why, moved):
        assert why == "" and not moved()
        for path in (app / "app.exe", tmp_path / "manifest.json"):
            with pytest.raises(PermissionError):
                path.write_bytes(b"other")
            with pytest.raises(PermissionError):
                path.rename(path.with_name("moved"))
            with pytest.raises(PermissionError):
                path.unlink()
    (app / "app.exe").write_bytes(b"other")   # let go: the next read sees it
    assert "`app.exe`" in pc_look.oneshot_desktop.altered(build)


def test_a_file_added_beside_the_held_ones_is_reported_moved(tmp_path):
    app, build = copy(tmp_path)
    (app / "sub").mkdir()
    with pc_look.oneshot_desktop.held(build) as (why, moved):
        assert why == "" and not moved()
        (app / "sub" / "added.dat").write_bytes(b"x")   # a new name: no held file is touched
        assert moved()


def test_a_handle_whose_wrapping_fails_is_closed(tmp_path, monkeypatch):
    app, build = copy(tmp_path)
    import msvcrt

    monkeypatch.setattr(msvcrt, "open_osfhandle", lambda *a: (_ for _ in ()).throw(OSError(24, "no descriptor")))
    with pytest.raises(OSError, match="no descriptor"):
        pc_look.oneshot_desktop.altered(build)
    (tmp_path / "manifest.json").write_bytes(b"{}")   # let go: nothing still holds it


def test_a_copy_another_holds_open_for_writing_is_never_read_as_unchanged(tmp_path):
    app, build = copy(tmp_path)
    with open(app / "app.exe", "r+b"), pytest.raises(PermissionError):
        pc_look.oneshot_desktop.altered(build)


def test_a_manifest_that_no_longer_digests_to_the_saved_identity_is_altered(tmp_path):
    _, build = copy(tmp_path)
    assert pc_look.oneshot_desktop.altered(build) == ""
    assert "식별자와 맞지 않는다" in pc_look.oneshot_desktop.altered({**build, "launch": {"program": "other.exe"}})
    assert "식별자와 맞지 않는다" in pc_look.oneshot_desktop.altered({**build, "identity": "0" * 64})


ON = {"id": "translate-on", "toggle": "On"}
OWED = [{"id": "O1", "kind": "desktop", "source": "look", "requirement": "R2", "check": "번역을 켜면 켜짐으로 보인다",
         "act": {"action": "toggle", "id": "translate-on"}, "expect": ON},
        {"id": "O2", "kind": "desktop", "source": "owner", "requirement": "R3", "check": "캡처에 글이 있다"},
        {"id": "O3", "kind": "desktop", "source": "look", "requirement": "R3", "check": "이전 버전의 미룬 검사"}]
SCENARIO = {"O1": {"screen": "S1", "state": "full", "steps": [
    {"name": "on", "action": "toggle", "id": "translate-on", "expect": ON}]}}
def test_a_postcondition_is_refuted_only_off_a_read_that_proves_it_false():
    on = {"id": "translate-on", "toggle": "On", "in": "owned"}
    switch = node("translate-on", window=DIALOG, id="translate-on", toggle="On")
    two = report([switch], windows=(MAIN, DIALOG, 3))   # Settings and Help both owned: which one is unproven
    assert not pc_look.refuted(two, two["states"][0], on)
    one = report([{**switch, "toggle": "Off"}], windows=(MAIN, DIALOG))
    assert pc_look.refuted(one, one["states"][0], on) and not pc_look.refuted(
        one, {**one["states"][0], "nodes": [*one["states"][0]["nodes"], {**switch, "toggle": None}]}, on)
    alone = report([])
    assert pc_look.refuted(alone, alone["states"][0], on)   # no owned window yet: it appears with the step
    gone = report([], windows=(3,), owners={3: 0})   # the main window lost: no owned window proves nothing
    assert not pc_look.refuted({**gone, "main": {"hwnd": MAIN}}, gone["states"][0], {**on, "in": "owned"})


UNSCOPED = ("a step's postcondition was not read on one element of its window after the step before, "
            "or was not read false before the last step")


@pytest.mark.parametrize("case, why", [
    ("on", None), ("off", "step on: its postcondition was not read"),
    ("in the dialog", UNSCOPED),   # the main window's switch stayed off, a dialog's same-named one is on
    ("stale", UNSCOPED),           # the step points back at the start state, which already showed it on
    ("already on", UNSCOPED),      # on before the review's step too: the step brought nothing about
    ("unread before", UNSCOPED),   # its toggle state unread before the step: never proven off
    ("its own expect", "step on: its postcondition was not read"),   # the review's toggle is read, not the name
    ("its own act", None),         # the review's toggle is what runs, not the builder's `refresh`
    ("unscripted", "빌드에 그 시나리오가 없다")])
def test_a_look_check_passes_only_when_the_reviews_step_brings_its_postcondition_about(tmp_path, case, why):
    started, scenarios = [], {} if case == "unscripted" else SCENARIO
    if case in ("its own expect", "its own act"):
        mine = {"expect": {"id": "translate-on", "name": "번역"}} if case == "its own expect" else \
            {"action": "invoke", "id": "refresh"}
        scenarios = {"O1": {**SCENARIO["O1"], "steps": [{**SCENARIO["O1"]["steps"][0], **mine}]}}

    def answer(sid, state, steps):   # the toggle leaves the main window's switch on, or not; waiting, as it was
        started.append([(s["name"], s["action"], s.get("id")) for s in steps])
        switch = node("translate-on", id="translate-on", name="번역", patterns=["Toggle"], toggle="Off")
        after = [{**switch, "toggle": "On" if case in ("on", "already on", "unread before", "its own act") else "Off"}]
        if case == "in the dialog":
            return report([switch], windows=(MAIN, DIALOG), steps=[
                {**steps[0], "shows": [*after, {**switch, "window": DIALOG, "toggle": "On"}]}])
        start = {**switch, "toggle": {"stale": "On", "already on": "On", "unread before": None}.get(case, "Off")}
        got = report([start], steps=[{**steps[0], "shows": after}])
        return {**got, "steps": [{**got["steps"][0], "state": "s00"}]} if case == "stale" else got

    spec = {"oneshot": {"front": {"screens": SCREENS}, "owed": OWED}}
    found = pc_look.checks(Runs(tmp_path / "evidence", answer), spec, {"checks": scenarios})
    ran = [[("on", "toggle", "translate-on")]] if scenarios else []   # O2 and O3 are the owner's
    assert started == ran
    assert [f["what"] for f in found] == ([] if why is None else [
        f"PC 앱에서 확인할 O1(R2) 이 통과하지 못했다 — {why} · 확인: 번역을 켜면 켜짐으로 보인다"])
    assert [x["id"] for x in pc_look.oneshot_desktop.owner_owed(spec["oneshot"])] == ["O2", "O3"]
