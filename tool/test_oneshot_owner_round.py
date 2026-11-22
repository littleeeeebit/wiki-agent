"""The owner's desktop round's Windows side (`owner_round.py`, plan 9e5b): bindings proven before every read, the
window-bound read's order (item, proof, frame pool), the fixture's words. The capture helper runs as a stand-in;
the two real runs here never touch the owner's desktop: the helper is built and asked `--check`, and the fixture
shows on a desktop the test owns."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import owner_round

APP = {"hwnd": 101, "pid": 7, "class": "AppWindow", "title": "App", "rect": [0, 0, 800, 600]}
PROCESS = {"pid": 7, "created": 1, "exe": "c:\\app\\app.exe"}


class Job:
    def end(self, seconds):
        return {"clean": True, "reasons": [], "members": []}


class Helper:
    """The capture helper's protocol as a stand-in: an item, then the server's answer, then a frame only on `go`."""

    def __init__(self, empty: bool = False, refused: bool = False):
        self.empty, self.refused, self.answers = empty, refused, []

    def __call__(self, argv: list[str]) -> None:
        folder = Path(argv[-1])
        if self.refused:   # Windows refused the item: the helper's catch writes only its result
            (folder / "result.json").write_text(json.dumps({"read": False, "why": "COMException: E_ACCESSDENIED"}))
            return
        (folder / "item.json").write_text(json.dumps({"empty": self.empty}))
        if self.empty:
            return
        while not any((folder / a).exists() for a in ("go", "stop")):
            time.sleep(0.01)
        go = (folder / "go").exists()
        self.answers.append("go" if go else "stop")
        if go:   # the frame pool, or the blit, starts only now
            (folder / ("surface.png" if argv[1] == "surface" else "window.png")).write_bytes(b"PNG")
        (folder / "result.json").write_text(json.dumps({"read": go, **({} if go else {"why": "the binding was not proven"})}))


class Session:
    def __init__(self, root: Path, helper: Helper):
        self.root, self.helper, self.jobs, self.started = root, helper, {}, []

    def reserve(self, key: str) -> Path:
        folder = self.root / f"{key}-{len(self.started)}"
        folder.mkdir(parents=True)
        return folder

    def start(self, key, argv, cwd, extra=None, halt=None):
        self.started.append(argv)
        self.jobs[key] = Job()
        threading.Thread(target=self.helper, args=(argv,), daemon=True).start()

    def observe(self):
        pass


@pytest.fixture
def desk(monkeypatch):
    """The windows Windows would answer for, by handle, and each process's identity, by pid."""

    windows, processes = {APP["hwnd"]: dict(APP)}, {7: dict(PROCESS)}
    monkeypatch.setattr(owner_round, "window", lambda hwnd: dict(windows[hwnd]) if hwnd in windows else None)
    monkeypatch.setattr(owner_round.native_process, "identity", lambda pid: processes.get(pid))
    return windows, processes


def read(tmp_path, binding, members, helper=None, then=None, halt=None):
    helper = helper or Helper()
    session = Session(tmp_path, helper)

    def proving():
        if then:
            then()
        return members

    halt = halt or threading.Event()
    return owner_round.window_read(session, "capture1", Path("helper.exe"), binding, proving, halt), session, helper


def test_a_window_whose_binding_stands_is_read_through_its_own_item(desk, tmp_path):
    binding = owner_round.bind("app1", 101, [PROCESS])
    assert binding["process"] == PROCESS and binding["rect"] is None   # only the fixture's frame is held still
    got, session, helper = read(tmp_path, binding, [PROCESS])
    assert got["read"] and Path(got["file"]).name == "window.png" and helper.answers == ["go"]
    assert session.started == [["helper.exe", "window", "101", session.started[0][-1]]]   # a handle, no region


@pytest.mark.parametrize("change, why", [
    # The same window replaced after the first proof: its handle is gone, and the item is never sampled.
    (lambda w, p: w.pop(101), "the window is gone"),
    # The handle recycled to another app's window after the proof, before the capture.
    (lambda w, p: w.update({101: {**APP, "pid": 9, "class": "Other"}}), "the handle names another window"),
    # The app's pid reused by another process with the same window class.
    (lambda w, p: p.update({7: {**PROCESS, "created": 2}}), "its process is not the one bound"),
])
def test_a_binding_that_changed_after_the_first_proof_is_never_read(desk, tmp_path, change, why):
    windows, processes = desk
    binding = owner_round.bind("app1", 101, [PROCESS])
    got, _, helper = read(tmp_path, binding, [PROCESS], then=lambda: change(windows, processes))
    assert got == {"read": False, "why": why} and helper.answers == ["stop"]
    assert not list(tmp_path.rglob("window.png"))


def test_a_process_out_of_the_rounds_job_is_never_read(desk, tmp_path):
    binding = owner_round.bind("app1", 101, [PROCESS])
    got, _, _ = read(tmp_path, binding, [])
    assert got == {"read": False, "why": "its process is not in the round's job"}


def test_an_empty_item_leaves_the_check_pending_and_reads_nothing(desk, tmp_path):
    binding = owner_round.bind("app1", 101, [PROCESS])
    with pytest.raises(owner_round.Unavailable, match="준비를 하지 못했다"):
        read(tmp_path, binding, [PROCESS], helper=Helper(empty=True))
    assert list(tmp_path.rglob("stop")) and not list(tmp_path.rglob("window.png"))


def test_an_item_windows_refused_leaves_the_check_pending_not_failed(desk, tmp_path):
    binding = owner_round.bind("app1", 101, [PROCESS])
    with pytest.raises(owner_round.Unavailable, match="E_ACCESSDENIED"):   # only its result: no item to answer
        read(tmp_path, binding, [PROCESS], helper=Helper(refused=True))


def test_a_stop_asked_while_the_binding_is_proven_never_lets_the_helper_sample(desk, tmp_path):
    binding, halt = owner_round.bind("app1", 101, [PROCESS]), threading.Event()
    got, _, _ = read(tmp_path, binding, [PROCESS], then=halt.set, halt=halt)
    assert got == {"read": False, "why": "the round was stopped"}
    assert list(tmp_path.rglob("stop")) and not list(tmp_path.rglob("go")) and not list(tmp_path.rglob("window.png"))


def test_a_window_of_another_job_or_title_is_never_bound(desk):
    with pytest.raises(ValueError, match="not one of"):
        owner_round.bind("app1", 101, [{**PROCESS, "pid": 8}])
    with pytest.raises(ValueError, match="not titled"):
        owner_round.bind("fixture1", 101, [PROCESS], owner_round.FIXTURE)


def test_the_fixture_that_moved_or_was_retitled_is_no_longer_the_rounds(desk):
    windows, _ = desk
    windows[101] = {**APP, "title": owner_round.FIXTURE}
    binding = owner_round.bind("fixture1", 101, [PROCESS], owner_round.FIXTURE)
    assert owner_round.proven(binding, [PROCESS]) == ""
    windows[101] = {**windows[101], "rect": [10, 0, 800, 600]}
    assert owner_round.proven(binding, [PROCESS]) == "the fixture moved"
    windows[101] = {**windows[101], "rect": APP["rect"], "title": "Notepad"}
    assert owner_round.proven(binding, [PROCESS]) == "the fixture's title changed"


def test_a_surface_read_names_exactly_the_fixtures_frame_and_is_never_sampled_once_it_moved(desk, tmp_path):
    windows, _ = desk
    windows[101] = {**APP, "title": owner_round.FIXTURE}
    fixture = owner_round.bind("fixture1", 101, [PROCESS], owner_round.FIXTURE)
    session = Session(tmp_path, Helper())
    got = owner_round.surface_read(session, "capture1", Path("helper.exe"), fixture, [80, 80, 960, 540],
                                   lambda: [PROCESS], threading.Event())
    # The fixture's handle and process go with its frame, so the helper checks it itself around the blit.
    assert got["read"] and session.started[0][1:8] == ["surface", "101", "7", "80", "80", "960", "540"]
    helper = Helper()

    def moved():
        windows[101] = {**windows[101], "rect": [1200, 80, 960, 540]}   # while the helper was starting
        return [PROCESS]

    got = owner_round.surface_read(Session(tmp_path / "b", helper), "capture1", Path("helper.exe"), fixture,
                                   [80, 80, 960, 540], moved, threading.Event())
    assert got == {"read": False, "why": "the fixture moved"} and helper.answers == ["stop"]
    assert not list((tmp_path / "b").rglob("surface.png"))


def test_the_fixtures_page_is_drawn_afresh_and_must_appear_whole():
    page = owner_round.words()
    assert len(page) == owner_round.COUNT == len(set(page)) and set(page) <= set(owner_round.WORDS)
    assert owner_round.contains("Seen: " + ", ".join(w.upper() for w in page), page)
    assert owner_round.contains("Seen:\n" + "\n".join(page), page)   # broken over lines, as the fixture wraps
    assert not owner_round.contains(" ".join(page[:-1]), page)   # one word short
    assert not owner_round.contains(" ".join(reversed(page)), page)   # out of order
    assert not owner_round.contains(" ".join(owner_round.WORDS), page)   # every word, but not the page
    assert not owner_round.contains(" ".join(page).replace(page[0], page[0] + "s"), page)   # whole words only
    assert not owner_round.contains("", []) and not owner_round.contains(None, page)


@pytest.mark.skipif(sys.platform != "win32", reason="the helper is a Windows program")
def test_the_capture_helper_builds_from_the_systems_metadata_and_starts(tmp_path):
    if not (owner_round.FRAMEWORK / "csc.exe").is_file():
        pytest.skip("no .NET Framework compiler here")
    exe = owner_round.helper(tmp_path)
    assert owner_round.helper(tmp_path) == exe   # built once for each source
    done = subprocess.run([str(exe), "--check"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "owner_capture ok"


@pytest.mark.skipif(sys.platform != "win32", reason="the fixture is a Windows window")
def test_the_fixture_shows_its_page_and_is_bound_by_its_job_and_title(tmp_path):
    import desktop_session

    page = owner_round.words()
    with desktop_session.look(tmp_path, "fixture") as session:   # a desktop the test owns, never the owner's
        folder = session.reserve("files")
        (folder / "words.txt").write_text(" ".join(page), encoding="utf-8")
        session.start("fixture1", [sys.executable, str(owner_round.FIXTURE_PY), "--title", owner_round.FIXTURE,
                                   "--words", str(folder / "words.txt"), "--rect", "40", "40", "640", "360",
                                   "--out", str(folder / "window.json")], folder)
        named = owner_round._waited(folder / "window.json", owner_round.FIXTURE_SECONDS, threading.Event())
        # A handle is read only from its own desktop: the round's server shares the owner's, so a probe there reads it.
        probe = f"import json, owner_round; print(json.dumps(owner_round.window({named['hwnd']})))"
        code, out, why = session.run("probe", [sys.executable, "-c", probe], Path(owner_round.__file__).parent, 60,
                                     threading.Event(), {"PYTHONPATH": str(Path(owner_round.__file__).parent)})
        seen = json.loads(out.strip().splitlines()[-1])
        session.observe()
        members = session.record["jobs"]["fixture1"]["members"]
        assert code == 0 and seen["title"] == owner_round.FIXTURE and seen["rect"] == named["rect"]
        assert seen["pid"] in {m["pid"] for m in members} and named["rect"][:2] == [40, 40]
