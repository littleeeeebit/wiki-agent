"""oneshot 9e3a: a PC app's screens — S6 designs them with the stack's own preview, which runs on the owner's own
desktop and is checked against the card's window, R2 shows and approves them with every other kind's, and the run
waits at the frontend build.

See `docs/plans/oneshot/9-kinds.md` ("Screens"). Model turns are stand-ins; each preview is a real stand-in script
run by the real launcher, the PC one in a real look on the owner's desktop; the web mocks are shot by Playwright when it is
installed.
"""

from __future__ import annotations

import os
import shutil
import sys
from unittest.mock import patch

import pytest

from main import (oneshot, oneshot_brief as brief, oneshot_front as front, oneshot_profile, oneshot_screens as screens,
                  oneshot_sheet as sheet, oneshot_spec, specs)
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_android_front import SHOTS, android, blocks as phone_blocks, mocks  # noqa: F401 — `android`: fixture
from test_oneshot_core import DESIGN
from test_oneshot_front import answer, head, o, playwright_or_skip, replies
from test_oneshot_kinds_front import PAGE
from test_oneshot_spec import world  # noqa: F401 — `world` is the temporary worktree's fixture

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="a PC app is built on Windows only")

PYTHON = [{"name": "python"}]   # the stand-in preview's one host program, found on `PATH` as a real tool is
KINDS = ["webapp", "android", "desktop"]
FEATURES = [
    {"id": "start-translation", "name": "번역 켜기", "parts": ["R2", "M1"], "class": "every", "why": "만화를 열 때마다 켠다",
     "kinds": ["android", "desktop"]},
    {"id": "history", "name": "번역 기록", "parts": ["M3"], "class": "every", "why": "웹에서 늘 본다", "kinds": ["webapp"]},
    {"id": "seen-boxes", "name": "번역한 말풍선 기억", "parts": ["M2"], "class": "never", "why": "사람이 만질 일이 없다",
     "kinds": ["webapp", "android", "desktop"]},
]
FREQUENCY = {"start-translation": "every", "history": "every", "seen-boxes": "never"}


def desktop_name() -> str:
    """The name of the desktop this process runs on."""

    import ctypes

    u, name = ctypes.windll.user32, ctypes.create_unicode_buffer(256)
    u.GetUserObjectInformationW(u.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId()), 2, name, 512, None)
    return name.value


def drawer(ids, width: int, height: int, window: bool = True) -> str:
    """A stand-in preview tool that writes each screen as a grey PNG while a window of it shows, as a headed browser's
    does; with no `window`, as a headless one draws. It refuses to draw anywhere but on the desktop the server itself
    runs on: the owner's, where they can see it."""

    return ("import ctypes, pathlib, struct, zlib\n"
            "u = ctypes.windll.user32\n"
            "name = ctypes.create_unicode_buffer(256)\n"
            "u.GetUserObjectInformationW(u.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId()), 2, name,"
            " 512, None)\n"
            f"assert name.value == {desktop_name()!r}, name.value\n"
            "assert not pathlib.Path('.git').exists() and pathlib.Path('README.md').is_file()\n"
            "def chunk(kind, data):\n"
            "    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))\n"
            f"w, h = {width}, {height}\n"
            "png = (b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)) +\n"
            "       chunk(b'IDAT', zlib.compress(b'\\x00' * ((w + 1) * h))) + chunk(b'IEND', b''))\n"
            f"out = pathlib.Path('{SHOTS}')\nout.mkdir(parents=True, exist_ok=True)\n"
            f"for sid in {list(ids)!r}:\n"
            "    (out / f'{sid}.png').write_bytes(png)\n" + (
                "import time, tkinter\nshown = tkinter.Tk()\nshown.geometry('400x300')\nshown.update()\n"
                "time.sleep(1.5)\nshown.destroy()\n" if window else ""))


def pc_mocks(ids=("S1", "S2"), width: int = 1280, height: int = 770, **files) -> dict:
    drawn = {f"mock/{sid}.tsx": f'<button id="feature:start-translation">번역 켜기</button> {sid}' for sid in ids}
    return {"files": {**drawn, "shoot.py": drawer(ids, width, height), **files},
            "screens": {sid: f"mock/{sid}.tsx" for sid in ids}, "command": "python shoot.py", "shots": SHOTS}


def pc_blocks(**changes) -> dict:
    """The Android design's two screens, as a PC app's: a window kept over the comic reader, and a settings window."""

    found = phone_blocks()
    found["oneshot-screens"] = [{**found["oneshot-screens"][0], "surface": "floating"},
                                {**found["oneshot-screens"][1], "surface": "window"}]
    found["oneshot-mocks"] = pc_mocks()
    found.update(changes)
    return found


@pytest.fixture(autouse=True)
def bounded(monkeypatch):
    """A stand-in preview ends in seconds: one that does not fails its test in a minute, its reason in the stop,
    instead of holding the suite for the half hour a first Gradle run is given."""

    monkeypatch.setattr(front, "PREVIEW_SECONDS", 60)


def looks() -> list:
    folder = oneshot_spec.store("budget").folder / "looks"
    return sorted(folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def pc(android):  # noqa: F811 — the imported fixture, by its name
    """A PC app whose split is done: its card's window, its build's one declared tool, the board submitted."""

    stack = [{"part": "build", "choice": "python", "why": "주인이 골랐다", "status": "confirmed", "tools": PYTHON}]
    oneshot_spec.saved("budget", oneshot={
        "intent": {**o()["intent"], "kind": "desktop"}, "design": sheet.parsed(DESIGN, "desktop"), "stack": stack,
        "profile": {k: v for k, v in o()["profile"].items() if k != "device"} | {"window": "1280x800"},
        "stage": "frontend", "features": [{k: v for k, v in f.items() if k != "kinds"} for f in FEATURES[::2]],
        "frequency": {"start-translation": "every", "seen-boxes": "never"}, "first_screen": "만화를 읽으며 번역을 켠다"})
    return android


def test_a_pc_apps_preview_runs_on_the_owners_desktop_and_r2_shows_its_window_shots(pc):
    heard, asking = replies(answer(pc_blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2", o().get("stopped")
    assert sorted(p.name for p in front.shots("budget", 1).iterdir()) == ["S1-window.png", "S2-window.png"]
    told = heard[0][1]
    assert "1280x800 physical pixels" in told and "- `python`" in told and "Galaxy" not in told
    assert o()["toolpaths"]["stage"] == "frontend" and o()["toolpaths"]["paths"]["python"] == os.path.abspath(
        shutil.which("python"))
    [look] = looks()   # one look, proven gone
    record = oneshot_spec.store("budget").folder / "looks" / look.name / "desktop-session.json"
    assert '"state": "proven"' in record.read_text(encoding="utf-8")
    shown = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))
    assert "## 첫 화면 S1 다른 프로그램 위" in shown["front_sheet"] and "/shot/1/S1-window.png" in shown["front_sheet"]
    assert "### S2 창 —" in shown["front_sheet"] and "핵심 리뷰" in shown["front_sheet"]
    assert str(front.shot("budget", 1, "S2-window.png").path).endswith("S2-window.png")


@pytest.mark.parametrize("found, said", [
    (pc_mocks(width=1400, height=800), "S1: `build/mock-shots/S1.png` 가 창보다 크지 않은 온전한 PNG 로 없다"),
    (pc_mocks(width=1000, height=800), "창 1280×800px 로 찍히지 않았다 — S1: `build/mock-shots/S1.png` 가 1000×800px 다"),
    (pc_mocks(**{"shoot.py": "raise SystemExit(4)"}), "4 로 끝났다"),
    (pc_mocks(**{"shoot.py": drawer(("S1", "S2"), 1280, 770, window=False)}), "주인 화면에 창을 하나도 띄우지 않았다"),
])
def test_a_pc_preview_of_the_wrong_size_or_failing_is_sent_back_once(pc, found, said):
    heard, asking = replies(answer(pc_blocks(**{"oneshot-mocks": found})), answer(pc_blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2" and said in heard[1][1]


def test_a_pc_preview_starting_an_undeclared_program_is_sent_back_and_nothing_runs(pc):
    heard, asking = replies(answer(pc_blocks(**{"oneshot-mocks": {**pc_mocks(), "command": "node shoot.js"}})),
                            answer(pc_blocks()))
    started = []
    with asking, patch.object(front, "on_desktop", lambda *a: started.append(a) or (0, "", "")):
        front.frontend("budget")
    assert "`node` 는 확정한 도구도 작업트리 안의 파일도 아니" in heard[1][1]
    assert len(started) == 1 and "python" in started[0][1].lower() and not looks()


def test_a_missing_declared_tool_stops_the_screens_before_their_turn(pc):
    _, asking = replies()
    with asking, patch.object(front.oneshot_build.shutil, "which", lambda name: None):
        front.frontend("budget")
    assert o()["stopped"]["reason"] == "toolchain" and "python" in o()["stopped"]["detail"] and o()["stage"] == "frontend"


def test_a_look_that_cannot_prove_its_cleanup_stops_the_stage_instead_of_asking_again(pc):
    import desktop_session

    def held(*a):
        raise desktop_session.Refused("the desktop look is held: no exit proven for pid 7")

    heard, asking = replies(answer(pc_blocks()), answer(pc_blocks()))
    # Raised past the turn's refusals: the worker's guard stops the stage with it (`oneshot.start_worker`).
    with asking, patch.object(desktop_session.Look, "run", held), pytest.raises(RuntimeError, match="정리하지 못했다"):
        front.frontend("budget")
    assert len(heard) == 1 and o()["stage"] == "frontend"


@pytest.mark.parametrize("kinds", [["desktop"], ["android", "desktop"]])
def test_a_board_an_earlier_version_held_is_the_owners_to_submit_after_a_restart(pc, kinds):
    features = [{**f, "kinds": kinds} for f in o()["features"]]
    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": kinds[0], "kinds": kinds},
                                          "stage": "board", "features": features, "frequency": None,
                                          "stopped": oneshot_spec.kind_stop("board")})
    oneshot_spec.recover()
    assert o()["stage"] == "board" and not o().get("stopped") and not o().get("working")
    first = {k: "번역을 켠다" for k in kinds} if len(kinds) > 1 else "번역을 켠다"
    started = []
    with patch.object(oneshot_spec, "start", lambda repo, worker, **k: started.append((worker, k["stage"]))):
        front.submit_board("budget", front.Board(frequency={f["id"]: f["class"] for f in features},
                                                 first_screen=first))
    assert started == [("frontend", "frontend")]
    # A board stopped some other way is resumed the way a sheet is: it stands, and the stop goes.
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "restart", "detail": "다시 켜졌다", "state": "board"}})
    oneshot_spec.resume("budget")
    assert o()["stage"] == "board" and not o().get("stopped")


# -- several kinds: two native previews and the web, through one R2 -------------------------

@pytest.fixture
def three(android):  # noqa: F811 — the imported fixture, by its name
    stack = [{"part": "desktop.build", "choice": "python", "why": "주인이 골랐다", "status": "confirmed", "tools": PYTHON}]
    oneshot_spec.saved("budget", oneshot={
        "intent": {**o()["intent"], "kind": "webapp", "kinds": KINDS}, "stack": stack,
        "profile": {**o()["profile"], "responsive": "desktop", "window": "1024x768"},
        "stage": "frontend", "features": FEATURES, "frequency": FREQUENCY,
        "first_screen": {"webapp": "지난 번역을 찾아본다", "android": "만화를 읽으며 번역을 켠다",
                         "desktop": "만화 뷰어 위에서 번역을 켠다"}})
    return android


def three_blocks() -> dict:
    """Android's S1 and S2, the web's S3, the PC app's S4 and S5. Both previews name `shoot.py` and `mock/`."""

    phone = phone_blocks()
    pc = pc_blocks()
    desktop = [{**s, "id": f"S{n + 4}", "kind": "desktop"} for n, s in enumerate(pc["oneshot-screens"])]
    states = phone["oneshot-states"] + [{**phone["oneshot-states"][0], "screen": sid} for sid in ("S3", "S4", "S5")]
    return {"oneshot-screens": [{**s, "kind": "android"} for s in phone["oneshot-screens"]] + [
                {"id": "S3", "kind": "webapp", "route": "/", "purpose": "번역한 말풍선을 시간 순서로 본다",
                 "features": ["history"], "primary": True, "reached": "start"}] + desktop,
            "oneshot-states": states, "oneshot-tokens": phone["oneshot-tokens"],
            "oneshot-mocks": {"webapp": {"S3": PAGE}, "android": mocks(),
                              "desktop": pc_mocks(("S4", "S5"), 1024, 740)}}


def test_two_native_previews_sharing_paths_and_a_web_page_pass_one_r2_and_the_build_of_every_kind_follows(three):
    playwright_or_skip()
    heard, asking = replies(answer(three_blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2", o().get("stopped")
    assert sorted(p.name for p in front.shots("budget", 1).iterdir() if p.suffix == ".png") == [
        "S1-phone.png", "S2-phone.png", "S3-desktop.png", "S4-window.png", "S5-window.png"]
    told = heard[0][1]
    assert "Galaxy S21" in told and "1024x768 physical pixels" in told and "The web viewports shot: desktop" in told
    assert set(o()["front"]["previews"]) == {"android", "desktop"}
    text = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))["front_sheet"]
    assert text.index("## 웹앱") < text.index("## Android 앱") < text.index("## PC 앱") < text.index("S4 다른 프로그램 위")
    begun, sent = [], []
    with patch.object(front.oneshot_front_build, "begin", begun.append), \
            patch.object(front.review().work, "dispatch", lambda tree, run: sent.append(tree)):
        front.decide("budget", front.R2(decisions={i: {"approve": True} for i in screens.decidable(o()["front"])},
                                        palette="C1", source=brief.source(brief.snapshot(oneshot_spec.spec_of("budget"), "r2"))))
    # The approval committed every kind's mocks, each native kind's in its own folder, and the one build of every
    # kind follows (9e3b2): it waits at the PC app's look once its gate passes and the program is frozen.
    assert begun == ["budget"] and not sent and o()["stage"] == "build-front" and o()["working"] == "build-front"
    assert not o().get("stopped")
    mock = three / "docs/design/mock"
    assert (mock / "android/shoot.py").read_text(encoding="utf-8") == mocks()["files"]["shoot.py"]
    assert (mock / "desktop/shoot.py").read_text(encoding="utf-8") == three_blocks()["oneshot-mocks"]["desktop"][
        "files"]["shoot.py"]
    assert (mock / "desktop/mock/S4.tsx").is_file() and (mock / "webapp/S3.html").read_text(encoding="utf-8") == PAGE
    for name in ("S1-phone", "S3-desktop", "S4-window"):
        assert (three / f"docs/design/shots/{name}.png").is_file(), name
    assert o()["front_head"] == head(three) and not oneshot_profile.held(o())


def test_an_edit_keeps_each_native_previews_shared_files(three):
    playwright_or_skip()
    _, asking = replies(answer(three_blocks()))
    with asking:
        front.frontend("budget")
    moved = three_blocks()
    moved["oneshot-mocks"]["desktop"]["command"] = "python shoot.py --again"
    heard, asking = replies(answer(moved), answer(three_blocks()))
    with asking:
        front.refront("budget", {"S4": "단추를 작게"}, ["S4", "S4.states"])
    assert "미리보기 파일·명령·사진 폴더" in heard[1][1] and o()["r2"]["round"] == 2
