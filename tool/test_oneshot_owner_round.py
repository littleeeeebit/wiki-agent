"""The owner's desktop round's Windows side (`owner_round.py`, plan 9e5b): bindings proven before every read, the
window-bound read's order (item, proof, frame pool), the fixture's words. The capture helper runs as a stand-in;
the two real runs here touch no app of the owner's: the helper is built and asked `--check`, and the fixture shows
on the owner's desktop, where every run is seen (plan 13f), one fixture at a time across the test workers (`alone`)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

import owner_round


@contextmanager
def alone():
    """One fixture window on the shared desktop at a time, across the test workers: the round itself refuses a second
    window by the fixture's title (`owner_round.fixture`), so two tests showing theirs at once would see each other."""

    import msvcrt

    with open(Path(tempfile.gettempdir()) / "wiki-owner-round-fixture.lock", "a+b") as held:
        held.seek(0)   # the byte the release names, whatever the file holds
        deadline = time.monotonic() + 600
        while True:
            try:
                msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        try:
            yield
        finally:
            held.seek(0)
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)

APP = {"hwnd": 101, "pid": 7, "class": "AppWindow", "title": "App", "rect": [0, 0, 800, 600]}
PROCESS = {"pid": 7, "created": 1, "exe": "c:\\app\\app.exe"}


class Job:
    def end(self, seconds):
        return {"clean": True, "reasons": [], "members": []}


class Helper:
    """The capture helper's protocol as a stand-in: an item, then the server's answer, then a frame only on `go`."""

    def __init__(self, empty: bool = False, refused: bool = False, extra: dict | None = None):
        self.empty, self.refused, self.extra, self.answers = empty, refused, extra or {}, []

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
        (folder / "result.json").write_text(json.dumps({"read": go, **({} if go else {"why": "the binding was not proven"}),
                                                        **self.extra}))


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


def test_a_stop_accepted_just_before_the_go_is_published_reaches_the_helper_as_stop(desk, tmp_path):
    binding, halt = owner_round.bind("app1", 101, [PROCESS]), threading.Event()

    class Gate:   # the stop endpoint took the lock first and set the halt; the `go` waits for it
        def __enter__(self):
            halt.set()

        def __exit__(self, *exc):
            return False

    session = Session(tmp_path, Helper())
    got = owner_round.window_read(session, "capture1", Path("helper.exe"), binding, lambda: [PROCESS], halt, Gate())
    assert got == {"read": False, "why": "the round was stopped"}
    assert list(tmp_path.rglob("stop")) and not list(tmp_path.rglob("go"))


def test_a_window_read_whose_other_proof_fails_with_the_item_open_is_never_sampled(desk, tmp_path):
    binding, helper = owner_round.bind("app1", 101, [PROCESS]), Helper()
    session = Session(tmp_path, helper)
    got = owner_round.window_read(session, "capture1", Path("helper.exe"), binding, lambda: [PROCESS],
                                  threading.Event(), also=lambda: "the fixture is gone")
    assert got == {"read": False, "why": "the fixture is gone"} and helper.answers == ["stop"]


def test_a_read_the_helper_could_not_let_go_of_wholly_passes_nothing(desk, tmp_path):
    binding = owner_round.bind("app1", 101, [PROCESS])
    with pytest.raises(RuntimeError, match="the memory DC"):
        read(tmp_path, binding, [PROCESS], helper=Helper(extra={"unreleased": ["the memory DC"]}))


def test_a_release_failed_where_the_helper_could_not_get_ready_passes_nothing_either(desk, tmp_path):
    class Leaky(Helper):   # no watch to install, and the screen DC it had taken not let go
        def __call__(self, argv):
            folder = Path(argv[-1])
            (folder / "item.json").write_text(json.dumps({"empty": True, "why": "the fixture cannot be watched"}))
            (folder / "result.json").write_text(json.dumps({"read": False, "unreleased": ["the screen DC"]}))

    binding = owner_round.bind("app1", 101, [PROCESS])
    with pytest.raises(RuntimeError, match="the screen DC"):
        read(tmp_path, binding, [PROCESS], helper=Leaky())


def test_a_blit_this_desktop_gives_no_pixels_leaves_the_check_pending(desk, tmp_path):
    binding = owner_round.bind("app1", 101, [PROCESS])
    with pytest.raises(owner_round.Unavailable, match="BitBlt failed"):
        read(tmp_path, binding, [PROCESS], helper=Helper(extra={"read": False, "unavailable": True, "why": "BitBlt failed"}))


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
    fixture = {**owner_round.bind("fixture1", 101, [PROCESS], owner_round.FIXTURE), "marks": "marks.txt"}
    session = Session(tmp_path, Helper())
    got = owner_round.surface_read(session, "capture1", Path("helper.exe"), fixture, [80, 80, 960, 540],
                                   lambda: [PROCESS], threading.Event())
    # The fixture's handle, process and marks go with its frame, so the helper checks it itself around the blit.
    assert got["read"] and session.started[0][1:9] == ["surface", "101", "7", "80", "80", "960", "540", "marks.txt"]
    helper = Helper()

    def moved():
        windows[101] = {**windows[101], "rect": [1200, 80, 960, 540]}   # while the helper was starting
        return [PROCESS]

    got = owner_round.surface_read(Session(tmp_path / "b", helper), "capture1", Path("helper.exe"), fixture,
                                   [80, 80, 960, 540], moved, threading.Event())
    assert got == {"read": False, "why": "the fixture moved"} and helper.answers == ["stop"]
    assert not list((tmp_path / "b").rglob("surface.png"))


def test_a_fixture_already_titled_on_the_desktop_starts_no_other(monkeypatch, tmp_path):
    started, titled = [], [[41]]

    class Look:
        record = {"jobs": {"fixture1": {"members": []}}}

        def reserve(self, key):
            (tmp_path / key).mkdir(exist_ok=True)
            return tmp_path / key

        def start(self, key, argv, cwd, halt=None):
            started.append(key)
            read.append((tmp_path / key / "words.txt").read_text(encoding="utf-8"))   # as the fixture reads it

        def observe(self):
            pass

    read = []
    monkeypatch.setattr(owner_round, "fixtures", lambda: titled.pop(0))
    monkeypatch.setattr(owner_round, "_waited", lambda out, seconds, halt: {"hwnd": 42, "rect": [1, 2, 3, 4]})
    monkeypatch.setattr(owner_round, "bind", lambda job, hwnd, members, title=None: {"job": job, "hwnd": hwnd})
    with pytest.raises(RuntimeError, match="앞선 회차의 고정 창이 아직 떠 있다"):   # an earlier round's
        owner_round.fixture(Look(), "fixture1", ["apple"], [0, 0, 640, 360], threading.Event())
    assert started == []
    titled[:] = [[], [41, 42]]   # its own up, and another by its title beside it
    with pytest.raises(RuntimeError, match="이 검사의 것 하나가 아니다"):
        owner_round.fixture(Look(), "fixture1", ["apple"], [0, 0, 640, 360], threading.Event())
    titled[:] = [[], [42]]
    binding, rect = owner_round.fixture(Look(), "fixture1", ["apple"], [0, 0, 640, 360], threading.Event())
    assert binding["hwnd"] == 42 and rect == [1, 2, 3, 4] and started == ["fixture1", "fixture1"]
    # The fixture had its page; once it is up, no file the app could read holds the words.
    assert read[-1] == "apple" and not (tmp_path / "fixture1" / "words.txt").exists()


def test_the_fixtures_marks_run_along_every_edge_inside_it_in_fresh_colours():
    marks = [line.split() for line in owner_round.marks(960, 540)]
    boxes = [tuple(map(int, m[:4])) for m in marks]
    assert all(x >= 0 and y >= 0 and x + w <= 960 and y + h <= 540 and w > 0 and h > 0 for x, y, w, h in boxes)
    band = owner_round.BAND
    # Every pixel of the outer band belongs to a mark, and the inside, where the page and the overlay are, to none.
    edge = {(x, y) for x in range(960) for y in range(540) if min(x, y, 959 - x, 539 - y) < band}
    covered = {(px, py) for x, y, w, h in boxes for px in range(x, x + w) for py in range(y, y + h)}
    assert covered == edge
    colours, again = [m[4] for m in marks], [line.split()[4] for line in owner_round.marks(960, 540)]
    assert colours != again   # drawn afresh for each check
    # Apart from each other and from the page's white and black by more than twice the helper's tolerance, so a
    # fixture shifted by a pixel shows a wrong colour at some mark's edge.
    rgb = [bytes.fromhex(c) for c in ["ffffff", "000000", *colours]]
    assert all(max(abs(x - y) for x, y in zip(a, b)) > 2 * owner_round.TOLERANCE
               for i, a in enumerate(rgb) for b in rgb[i + 1:])


def test_the_fixtures_page_is_drawn_afresh_and_must_appear_whole():
    page = owner_round.words()
    assert len(page) == owner_round.COUNT == len(set(page)) and set(page) <= set(owner_round.WORDS)
    assert owner_round.contains("Seen: " + ", ".join(w.upper() for w in page), page)
    assert owner_round.contains("Seen:\n" + "\n".join(page), page)   # broken over lines, as the fixture wraps
    assert not owner_round.contains(" ".join(page[:-1]), page)   # one word short
    assert not owner_round.contains(" ".join(reversed(page)), page)   # out of order
    assert not owner_round.contains(" ".join(owner_round.WORDS), page)   # every word, but not the page
    assert not owner_round.contains(" ".join(w + "é" for w in page), page)   # each word misread by a letter
    assert not owner_round.contains("1".join(page), page)   # joined into one token
    assert not owner_round.contains(" ".join(w + "́" for w in page), page)   # a combining accent, decomposed
    assert not owner_round.contains("‍".join(page), page)   # joined by a zero-width joiner
    assert owner_round.contains("​".join(page), page)   # a zero-width space does part words
    assert owner_round.contains("⁦‎" + " ".join(page) + "⁩", page)   # direction marks are layout only
    assert owner_round.contains("캡처: " + " ".join(page) + ".", page)   # other text beside it is no matter
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
    with alone(), desktop_session.look(tmp_path, "fixture") as session:   # on the owner's desktop, seen
        folder = session.reserve("files")
        (folder / "words.txt").write_text(" ".join(page), encoding="utf-8")
        (folder / "marks.txt").write_text("\n".join(owner_round.marks(640, 360)), encoding="utf-8")
        session.start("fixture1", [sys.executable, str(owner_round.FIXTURE_PY), "--title", owner_round.FIXTURE,
                                   "--words", str(folder / "words.txt"), "--marks", str(folder / "marks.txt"),
                                   "--rect", "40", "40", "640", "360", "--out", str(folder / "window.json")], folder)
        named = owner_round._waited(folder / "window.json", owner_round.FIXTURE_SECONDS, threading.Event())
        # A handle is read only from its own desktop: the probe runs on the owner's, as the round's server does.
        probe = (f"import json, owner_round; seen = owner_round.window({named['hwnd']}); print(json.dumps({{**seen, "
                 f"'titled': owner_round.fixtures(), 'none': sorted(owner_round.shown([])), "
                 f"'shown': sorted(owner_round.shown([{{'pid': seen['pid']}}]))}}))")
        code, out, why = session.run("probe", [sys.executable, "-c", probe], Path(owner_round.__file__).parent, 60,
                                     threading.Event(), {"PYTHONPATH": str(Path(owner_round.__file__).parent)})
        seen = json.loads(out.strip().splitlines()[-1])
        session.observe()
        members = session.record["jobs"]["fixture1"]["members"]
        assert code == 0 and seen["title"] == owner_round.FIXTURE and seen["pid"] in {m["pid"] for m in members}
        assert seen["titled"] == [named["hwnd"]]   # found by its title, the only window by it (`alone`)
        assert named["hwnd"] in seen["shown"] and seen["none"] == []   # its process's visible windows, no other's
        # Its frame is placed at the rect's corner; what it reports is its client area, inside that frame.
        (fx, fy, fw, fh), (cx, cy, cw, ch) = seen["rect"], named["rect"]
        assert [fx, fy] == [40, 40] and [cw, ch] == [640, 360]
        assert fx < cx and fy < cy and cx + cw < fx + fw and cy + ch < fy + fh


CHANGES = r'''
import ctypes, json, sys
from ctypes import wintypes as w
u, g = ctypes.windll.user32, ctypes.windll.gdi32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
u.SendMessageW.argtypes = [w.HWND, ctypes.c_uint, w.WPARAM, ctypes.c_void_p]
hwnd, marks = int(sys.argv[1]), [line.split() for line in open(sys.argv[2], encoding="utf-8") if line.strip()]

def state():
    box, title, shape = w.RECT(), ctypes.create_unicode_buffer(64), w.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(box))
    u.GetWindowTextW(hwnd, title, 64)
    kind = u.GetWindowRgnBox(hwnd, ctypes.byref(shape))
    return {"rect": [box.left, box.top, box.right - box.left, box.bottom - box.top], "title": title.value,
            "shape": [kind, shape.left, shape.top, shape.right - shape.left, shape.bottom - shape.top],
            "visible": bool(u.IsWindowVisible(hwnd)), "iconic": bool(u.IsIconic(hwnd)), "ex": u.GetWindowLongW(hwnd, -20)}

before = state()
u.SetWindowPos(hwnd, 0, 500, 300, 200, 100, 0x14)          # moved and resized
u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x13)                 # no longer topmost
u.ShowWindow(hwnd, 0)                                      # hidden
u.ShowWindow(hwnd, 6)                                      # minimized
u.SendMessageW(hwnd, 0x000C, 0, ctypes.c_wchar_p("Notepad"))   # retitled
u.SetWindowLongW(hwnd, -20, before["ex"] | 0x80000)        # made layered, to be see-through
u.SendMessageW(hwnd, 0x0112, 0xF060, None)                 # closed by its system menu
u.SendMessageW(hwnd, 0x0010, 0, None)                      # closed
u.SetWindowPos(hwnd, 0, before["rect"][0] + 1, before["rect"][1], 0, 0, 0x415)   # a pixel, past its guard
u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x413)                # no longer topmost, past its guard
u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x497)                 # hidden, past its guard
import time; time.sleep(0.5)                               # what passed the guard, undone by the fixture at once
after = state()
# The marks, where the fixture paints them: read off the window on the owner's desktop, where the test shows it.
box, origin = w.RECT(), w.POINT()
u.GetWindowRect(hwnd, ctypes.byref(box))
u.ClientToScreen(hwnd, ctypes.byref(origin))
screen = u.GetDC(None)
dc, bits = g.CreateCompatibleDC(screen), g.CreateCompatibleBitmap(screen, box.right - box.left, box.bottom - box.top)
g.SelectObject(dc, bits)
u.PrintWindow(hwnd, dc, 2)
wrong = []
for mx, my, mw, mh, color in marks:   # every pixel of every mark, as the helper reads them
    want = (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
    for px in range(int(mx), int(mx) + int(mw)):
        for py in range(int(my), int(my) + int(mh)):
            got = g.GetPixel(dc, origin.x - box.left + px, origin.y - box.top + py)
            seen = (got & 0xFF, got >> 8 & 0xFF, got >> 16 & 0xFF)
            if any(abs(a - b) > 8 for a, b in zip(seen, want)):
                wrong.append([px, py, color, "%02x%02x%02x" % seen])
client = w.RECT()
u.GetClientRect(hwnd, ctypes.byref(client))
print(json.dumps({"before": before, "after": after, "marks": len(marks), "wrong": wrong[:5],
                  "client": [origin.x, origin.y, client.right, client.bottom]}))
'''


@pytest.mark.skipif(sys.platform != "win32", reason="the helper and the fixture are Windows programs")
def test_the_fixture_refuses_every_change_paints_its_marks_and_the_helper_reads_it_up_to_the_blit(tmp_path):
    import desktop_session

    if not (owner_round.FRAMEWORK / "csc.exe").is_file():
        pytest.skip("no .NET Framework compiler here")
    exe, here = owner_round.helper(tmp_path / "tools"), Path(owner_round.__file__).parent
    with alone(), desktop_session.look(tmp_path, "guard") as session:   # on the owner's desktop, seen
        folder = session.reserve("files")
        (folder / "words.txt").write_text(" ".join(owner_round.words()), encoding="utf-8")
        (folder / "marks.txt").write_text("\n".join(owner_round.marks(640, 360)), encoding="utf-8")
        (folder / "changes.py").write_text(CHANGES, encoding="utf-8")
        session.start("fixture1", [sys.executable, str(owner_round.FIXTURE_PY), "--title", owner_round.FIXTURE,
                                   "--words", str(folder / "words.txt"), "--marks", str(folder / "marks.txt"),
                                   "--rect", "40", "40", "640", "360", "--out", str(folder / "window.json")], folder)
        named = owner_round._waited(folder / "window.json", owner_round.FIXTURE_SECONDS, threading.Event())
        session.observe()
        pid = session.record["jobs"]["fixture1"]["members"][0]["pid"]
        code, out, why = session.run("changes", [sys.executable, str(folder / "changes.py"), str(named["hwnd"]),
                                                 str(folder / "marks.txt")], here, 60, threading.Event())
        assert code == 0, why
        seen = json.loads(out.strip().splitlines()[-1])
        # Every change was turned into none: place, size, z-order (WS_EX_TOPMOST), visibility, title, style.
        assert seen["after"] == seen["before"] and seen["before"]["title"] == owner_round.FIXTURE
        assert seen["before"]["ex"] & 0x8 and not seen["after"]["iconic"] and seen["after"]["visible"]
        assert seen["marks"] == len(owner_round.marks(640, 360)) and seen["wrong"] == []
        # What the round reads is the client area, all of it painted: never the frame's invisible borders.
        assert named["rect"] == seen["client"] and named["rect"][2:] == [640, 360]
        # Its window region, set by itself, is one rectangle (SIMPLEREGION) of exactly the client area.
        frame = seen["before"]["rect"]
        assert seen["before"]["shape"] == [2, named["rect"][0] - frame[0], named["rect"][1] - frame[1], 640, 360]

        def surface(key: str) -> dict:
            out = session.reserve(key)
            session.start(key, [str(exe), "surface", str(named["hwnd"]), str(pid), *map(str, named["rect"]),
                                str(folder / "marks.txt"), str(out)], out)
            assert owner_round._waited(out / "item.json", 30, threading.Event()) == {"empty": False}
            (out / "go").write_text("", encoding="utf-8")
            return {**owner_round._waited(out / "result.json", 30, threading.Event()),
                    "kept": (out / "surface.png").is_file()}

        # Both checks of the window pass after those requests. On the input desktop the blit holds every mark the
        # fixture paints, where it paints it, and that frame is kept: the fixture is topmost, and no other topmost
        # window covers it here (`oneshot_fixture.py`); one that did would leave the check pending instead.
        assert surface("surface") == {"read": True, "width": 640, "height": 360, "kept": True}
        # A window region set on the fixture, which its marks along the edges would not show: never undone by the
        # fixture, so the helper's check right after the blit always finds it.
        shape = ("import ctypes; u, g = ctypes.windll.user32, ctypes.windll.gdi32; "
                 f"print(u.SetWindowRgn({named['hwnd']}, g.CreateRectRgn(0, 0, 100, 100), True))")
        code, out, why = session.run("shape", [sys.executable, "-c", shape], here, 60, threading.Event())
        assert code == 0 and out.strip().splitlines()[-1] != "0", why
        assert surface("shaped") == {"read": False, "why": "the fixture's shape changed", "kept": False}
