"""oneshot 7c: an Android app's feature split read off its approved core, its screens drawn by the stack's own
preview tool with no emulator, and R2's approval stopping before the frontend build.

`docs/plans/oneshot/7-android.md` ("S5 and S6") is the design.
"""

from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import zlib
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_front as front, oneshot_screens as screens, oneshot_sheet as sheet, oneshot_spec, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import DESIGN, GLOSSARY, REQS
from test_oneshot_front import answer, head, o, replies
from test_oneshot_spec import fenced, world  # noqa: F401 — `world` is the temporary worktree's fixture

INVENTORY = ["M1", "M2", "R2"]
FEATURES = [
    {"id": "start-translation", "name": "번역 켜기", "parts": ["R2", "M1"], "class": "every", "why": "만화를 열 때마다 켠다"},
    {"id": "seen-boxes", "name": "번역한 말풍선 기억", "parts": ["M2"], "class": "never", "why": "사람이 만질 일이 없다"},
]
FREQUENCY = {"start-translation": "every", "seen-boxes": "never"}
SHOTS = "build/mock-shots"


def shooter(width: int = 1080, height: int = 2400, whole: bool = True) -> str:
    """A stand-in preview tool: it refuses to run inside a Git checkout, and writes each screen as a grey PNG
    (or, not `whole`, as the PNG's first 29 bytes alone)."""

    return ("import pathlib, struct, zlib\n"
            "assert not pathlib.Path('.git').exists() and pathlib.Path('README.md').is_file()\n"
            "def chunk(name, data):\n"
            "    return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))\n"
            f"w, h = {width}, {height}\n"
            "png = (b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)) +\n"
            "       chunk(b'IDAT', zlib.compress(b'\\x00' * ((w + 1) * h))) + chunk(b'IEND', b''))\n"
            f"png = png if {whole} else png[:29]\n"
            f"out = pathlib.Path('{SHOTS}')\nout.mkdir(parents=True, exist_ok=True)\n"
            "for sid in ('S1', 'S2'):\n"
            "    (out / f'{sid}.png').write_bytes(png)\n")


def mocks(**files) -> dict:
    drawn = {"mock/S1.kt": 'Box(Modifier.testTag("feature:start-translation")) { Text("번역 켜기") }',
             "mock/S2.kt": 'Column { Text("설정") }', "shoot.py": shooter(), **files}
    return {"files": drawn, "screens": {"S1": "mock/S1.kt", "S2": "mock/S2.kt"}, "command": "python shoot.py",
            "shots": SHOTS}


def blocks(**changes) -> dict:
    found = {
        "oneshot-screens": [
            {"id": "S1", "surface": "overlay", "purpose": "만화 위에 번역 단추를 띄운다", "features": ["start-translation"],
             "primary": True, "reached": "start"},
            {"id": "S2", "surface": "screen", "purpose": "번역할 언어를 고른다", "features": [], "primary": False,
             "reached": "deeper"}],
        "oneshot-states": [{"screen": s, "empty": "말풍선이 아직 없다", "loading": "단추가 돈다", "error": "다시 해 보라는 줄",
                            "full": "말풍선 열 개"} for s in ("S1", "S2")],
        "oneshot-tokens": {"palettes": [
            {"id": "C1", "name": "밝은 종이", "colors": {"primary": "#2f6f4e", "surface": "#ffffff", "text": "#1a1a1a",
                                                  "accent": "#e0a000"}},
            {"id": "C2", "name": "밤 읽기", "colors": {"primary": "#1f3a68", "surface": "#111111", "text": "#f7f7f5",
                                                 "accent": "#d0473a"}}],
            "type": ["14sp", "16sp", "20sp"], "spacing": [4, 8, 16]},
        "oneshot-mocks": mocks(),
    }
    found.update(changes)
    return found


@pytest.fixture
def android(world):  # noqa: F811 — the imported fixture, by its name
    (world / "README.md").write_text("만화 번역기\n", encoding="utf-8")   # the built core, as far as the preview reads it
    identity = {f"GIT_{who}_{what}": value for who in ("AUTHOR", "COMMITTER")
                for what, value in (("NAME", "Oneshot test"), ("EMAIL", "oneshot@example.test"))}
    with patch.dict(os.environ, identity):
        for args in (["add", "README.md"], ["commit", "-m", "core"]):
            subprocess.run(["git", *args], cwd=world, check=True, capture_output=True)
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "intent": {**o()["intent"], "kind": "android"}, "design": sheet.parsed(DESIGN, "android"), "glossary": GLOSSARY,
        "profile": {k: v for k, v in o()["profile"].items() if k != "responsive"} | {"device": "galaxy-s21"},
        "built_head": head(world)})
    found = {k: f"C:/app/{k.lower()}" for k in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_USER_HOME", "GRADLE_USER_HOME")}
    with patch.object(front.oneshot_android, "build_env", lambda: {**os.environ, **found}):
        yield world


@pytest.fixture
def boarded(android):
    oneshot_spec.saved("budget", oneshot={"stage": "frontend", "features": FEATURES, "frequency": FREQUENCY,
                                          "first_screen": "만화를 읽으며 번역을 켠다"})
    return android


def designed() -> None:
    _, asking = replies(answer(blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2", o().get("stopped")


# -- S5 ------------------------------------------------------------------------------------

def test_the_inventory_is_the_cores_modules_and_its_capabilities(android):
    oneshot_spec.saved("budget", oneshot={"stage": "split", "intent": {**o()["intent"], "first_screen": "번역 켜기"}})
    assert front.inventory(specs.load("budget", oneshot.BRANCH)) == INVENTORY
    heard, asking = replies(fenced("oneshot-features", FEATURES))
    with asking:
        front.split("budget")
    assert o()["stage"] == "board" and o()["features"][0]["parts"] == ["R2", "M1"], o().get("stopped")
    assert "- `M1` PageReader — 화면 한 장을" in heard[0][1] and "- `R2` `MediaProjection`" in heard[0][1]


@pytest.mark.parametrize("change, said", [
    (lambda f: f[0]["parts"].append("M9"), "핵심 설계에 없는 핵심 항목"),
    (lambda f: f.pop(), "어느 기능에도 없는 핵심 항목다: M2"),
    (lambda f: f[1]["parts"].append("M1"), "둘에 들어"),
    (lambda f: f[0].pop("parts"), "`parts` 는 비지 않은"),
])
def test_a_feature_list_that_invents_drops_or_shares_a_core_part_is_refused(change, said):
    found = json.loads(json.dumps(FEATURES))
    change(found)
    with pytest.raises(ValueError, match=said):
        screens.features(found, INVENTORY, "parts")


# -- S6 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("change, said", [
    ({"oneshot-screens": [{**blocks()["oneshot-screens"][0], "surface": "widget"}]}, "`surface`"),
    ({"oneshot-mocks": mocks(**{"mock/S1.kt": "Box { }"})}, "feature:<id>` 가 빠진 기능 — start-translation"),
    ({"oneshot-mocks": {**mocks(), "files": {**mocks()["files"], "../outside.kt": "x"}}}, "상대 경로"),
    ({"oneshot-mocks": {**mocks(), "files": {**mocks()["files"], "app/.GIT/config": "x"}}}, "상대 경로"),
    ({"oneshot-mocks": {**mocks(), "screens": {"S1": "mock/S1.kt"}}}, "화면 id 마다"),
    ({"oneshot-mocks": {**mocks(), "command": " "}}, "command"),
])
def test_a_screen_or_a_mock_outside_the_rules_is_refused(change, said):
    with pytest.raises(ValueError, match=said):
        screens.design(blocks(**change), FEATURES, FREQUENCY, android=True)


def png(width: int, height: int, colour: int = 0, depth: int = 8, interlace: int = 0, filter_byte: int = 0,
        rows: list[int] | None = None) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

    lines = rows if rows is not None else screens.scanlines(width, height, screens.CHANNELS.get(colour, 1) * depth,
                                                           interlace)
    pixels = b"".join(bytes([filter_byte]) + b"\x00" * (n - 1) for n in lines)
    extra = chunk(b"PLTE", b"\x00\x00\x00") if colour == 3 else b""
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace))
            + extra + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("data, size", [
    (png(1080, 2400), (1080, 2400)),
    (png(33, 21, colour=6, interlace=1), (33, 21)),   # Adam7's seven passes, the last ones partial
    (png(1080, 2400, colour=2, depth=16), (1080, 2400)),
    (png(1080, 2400)[:29], None),   # the header alone
    (png(1080, 2400, filter_byte=255), None),   # rows of the right length that no decoder can unfilter
    (png(40, 40, rows=[41] * 39), None),   # a row short
    (png(40, 40, colour=3), None),   # a palette image
    (png(40, 40, colour=2, depth=4), None),   # a depth true colour does not allow
], ids=["grey", "interlaced", "deep", "header", "filter", "short", "palette", "depth"])
def test_a_picture_counts_only_as_a_whole_png_whose_rows_decode(data, size):
    assert screens.png_size(data) == size
    try:
        from PIL import Image
    except ImportError:
        return
    if size:   # what the server takes, a real decoder opens
        assert Image.open(io.BytesIO(data)).size == size and Image.open(io.BytesIO(data)).load()


def test_the_preview_draws_each_screen_on_the_unpacked_head_and_keeps_only_the_pictures(boarded):
    heard, asking = replies(answer(blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2", o().get("stopped")
    folder = front.shots("budget", 1)
    assert sorted(p.name for p in folder.iterdir()) == ["S1-phone.png", "S2-phone.png"]   # the project is gone
    assert "Galaxy S21, 1080×2400 px at 421 dpi" in heard[0][1] and "core parts" in heard[0][1]
    shown = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))
    assert "## 첫 화면 S1 다른 앱 위" in shown["front_sheet"] and "/shot/1/S1-phone.png" in shown["front_sheet"]
    assert "핵심 리뷰" in shown["front_sheet"] and shown["front_items"] == ["S1", "S1.states", "S2", "S2.states"]
    assert str(front.shot("budget", 1, "S1-phone.png").path).endswith("S1-phone.png")


@pytest.mark.parametrize("found, said", [
    (mocks(**{"shoot.py": shooter(width=720, height=1600)}), "S1: `build/mock-shots/S1.png` 가 720×1600px 다"),
    (mocks(**{"shoot.py": shooter(whole=False)}), "S1: `build/mock-shots/S1.png` 가 PNG 로 없다"),
    # Quiet past the shared quiet limit, which the test shortens: only the preview's deadline may cut it.
    (mocks(**{"shoot.py": "import time\ntime.sleep(1)\nraise SystemExit(3)\n"}), "3 로 끝났다"),
    (mocks(**{"shoot.py": "raise SystemExit(3)"}), "3 로 끝났다"),
    ({**mocks(), "command": "python shoot.py; del README.md"}, "` && ` 로만"),
])
def test_a_preview_that_fails_or_draws_the_wrong_size_is_sent_back_once(boarded, found, said):
    heard, asking = replies(answer(blocks(**{"oneshot-mocks": found})), answer(blocks()))
    with asking, patch.object(front.runtime, "IDLE_SECONDS", 0.3):
        front.frontend("budget")
    assert o()["stage"] == "r2" and said in heard[1][1]


def test_an_edit_keeps_the_previews_shared_files(boarded):
    designed()
    moved = mocks(**{"shoot.py": shooter() + "# another harness\n"})
    edit = blocks(**{"oneshot-mocks": moved, "oneshot-screens": [
        {**blocks()["oneshot-screens"][0], "purpose": "만화 위에 작은 번역 단추를 띄운다"}, blocks()["oneshot-screens"][1]]})
    heard, asking = replies(answer(edit), answer(blocks(**{"oneshot-screens": edit["oneshot-screens"]})))
    with asking:
        front.refront("budget", {"S1": "단추를 작게"}, ["S1", "S1.states"])
    assert "미리보기 파일·명령·사진 폴더" in heard[1][1] and "mocks" not in json.loads(
        heard[0][1].split("The current frontend design:\n\n")[1].split("\n\n")[0])
    assert o()["r2"]["round"] == 2 and o()["front"]["shots"] == 2


def test_an_edit_that_rebinds_a_screen_outside_its_reach_to_another_file_is_sent_back(boarded):
    designed()
    swapped = mocks()
    swapped["files"]["mock/S1.kt"], swapped["files"]["mock/S2.kt"] = swapped["files"]["mock/S2.kt"], swapped["files"]["mock/S1.kt"]
    swapped["screens"] = {"S1": "mock/S2.kt", "S2": "mock/S1.kt"}   # each screen's source moves with it
    heard, asking = replies(answer(blocks(**{"oneshot-mocks": swapped})), answer(blocks()))
    with asking:
        front.refront("budget", {"S1": "단추를 작게"}, ["S1", "S1.states"])
    assert "범위 밖 항목을 바꾸거나 지웠다: S2" in heard[1][1]


# -- R2------------------------------------------------------------------------------------

def test_the_approved_screens_are_committed_and_the_run_stops_before_the_frontend_build(boarded):
    designed()
    begun = []
    with patch.object(front.oneshot_front_build, "begin", begun.append):
        front.decide("budget", front.R2(decisions={i: {"approve": True} for i in screens.decidable(o()["front"])},
                                        palette="C2"))
    assert not begun and o()["stage"] == "build-front" and not o()["working"]
    assert o()["stopped"]["reason"] == "kind" and "7e" in o()["stopped"]["detail"]
    for name in ("docs/design/screens.md", "docs/design/mock/mock/S1.kt", "docs/design/mock/shoot.py",
                 "docs/design/shots/S1-phone.png"):
        assert (boarded / name).is_file(), name
    assert not (boarded / "docs/design/frontend.md").exists() and o()["front_head"] == head(boarded)
    assert "](shots/S1-phone.png)" in (boarded / "docs/design/screens.md").read_text(encoding="utf-8")
    with pytest.raises(HTTPException) as refused:
        oneshot_spec.resume("budget")
    assert refused.value.status_code == 409 and "7e" in refused.value.detail
    oneshot_spec.recover()   # a restart keeps this version's own stop
    assert o()["stopped"]["reason"] == "kind"


def test_a_core_an_earlier_version_stopped_before_the_split_resumes_into_it_after_a_restart(android):
    oneshot_spec.saved("budget", oneshot={"stage": "split", "working": None, "stopped": {
        "reason": "kind", "state": "split", "detail": "7c 가 들어오면 이어 간다"}})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart" and o()["stage"] == "split"
    oneshot_spec.resume("budget")
    assert o()["working"] == "split" and not o()["stopped"]
