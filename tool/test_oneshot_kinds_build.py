"""oneshot step 9c3a: several kinds' screen build — one turn builds every kind on the core, one gate runs the core's
tests and each kind's build, then each kind's screens are checked in the owner's order, each against only what
applies to it; a kind that fails sends the build back, and the next HEAD checks every kind again from the first.

See `docs/plans/oneshot/9-kinds.md` ("S7 to R3 — each kind's loop, one R3"). The gate and Git run for real; each
kind's look (the browser's, the emulator's) is a stand-in that records what it was given.
"""

import json
import sys
from unittest.mock import patch

import pytest

from main import oneshot_build, oneshot_device as device, oneshot_front_build as front_build, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_front_build import GATE, Run, adapter, git, o, report, spec, staged, write  # noqa: F401 — `staged`
from test_oneshot_device import SUFFIX
from test_oneshot_kinds import KINDS_REQS
from test_oneshot_spec import fenced, world  # noqa: F401 — `world` is the temporary worktree's fixture

KINDS = ["android", "webapp"]
DEVICE = {"apk": "android/app-debug.apk", "package": f"com.example.budget{SUFFIX}", "activity": "com.example.budget.Main",
          "tests": {"apk": "android/app-test.apk",
                    "runner": f"com.example.budget{SUFFIX}.test/androidx.test.runner.AndroidJUnitRunner"},
          "fixture": {"apk": "android/fixture.apk", "package": f"com.example.budget.fixture{SUFFIX}"}}   # R2 reads the screen
SCREENS = [{"id": "S1", "kind": "android", "surface": "overlay", "purpose": "만화 위에 단추를 띄운다", "features": ["read"],
            "primary": True, "reached": "start"},
           {"id": "S2", "kind": "webapp", "route": "/", "purpose": "지난 번역을 본다", "features": ["history"],
            "primary": True, "reached": "start"}]
FEATURES = [{"id": "read", "name": "번역 켜기", "parts": ["R2", "M1"], "class": "every", "why": "늘", "kinds": ["android"]},
            {"id": "history", "name": "번역 기록", "parts": ["P1"], "class": "every", "why": "늘", "kinds": ["webapp"]}]
STATES = {s["id"]: {"empty": "빈", "loading": "로딩", "error": "오류", "full": "찬"} for s in SCREENS}
BUILD = f'"{sys.executable}" --version'   # a kind's build, as far as the gate runs it
KINDS_GATE = f"{GATE} && {BUILD} && {BUILD}"


@pytest.fixture
def kinds_build(staged, monkeypatch):  # noqa: F811 — the imported fixture, requested by name
    oneshot_spec.saved("budget", requirements=KINDS_REQS, oneshot={
        "intent": {"kind": "android", "kinds": KINDS}, "features": FEATURES,
        "frequency": {"read": "every", "history": "every"},
        "first_screen": {"android": "만화를 읽으며 번역을 켠다", "webapp": "지난 번역을 찾아본다"},
        "profile": {**o()["profile"], "device": "medium-phone"},
        "design": {**o()["design"], "api": [{"id": "P1", "method": "GET", "path": "/history", "does": "기록"}]},
        "front": {**o()["front"], "screens": SCREENS, "states": STATES, "mocks": {"S2": "<p>기록</p>"},
                  "preview": {"files": {}, "screens": {"S1": "mock/S1.kt"}, "command": "x", "shots": "out"}}})
    monkeypatch.setattr(oneshot_build.oneshot_android, "build_env", lambda: None)   # the gate runs as the test's Python
    monkeypatch.setattr(device, "built", lambda tree, d: "")
    write(staged, ".wiki/adapter.toml", adapter(gate=KINDS_GATE))
    git(staged, "add", "-A")
    git(staged, "commit", "-m", "screens")
    return staged


def looks(found: dict[str, list[dict]]):
    """A stand-in for each kind's look: it records what the kind was given and shoots each of its screens."""

    seen = []

    def look(path, kind, one, live, d, shots, stopped, apks):
        o = one["oneshot"]
        seen.append({"kind": kind, "screens": [s["id"] for s in o["front"]["screens"]], "features": sorted(o["frequency"]),
                     "requirements": [r["id"] for r in one["requirements"]], "inventory": o.get("inventory"),
                     "views": front_build.views(o["profile"]), "device": d, "live": live})
        shots.mkdir(parents=True, exist_ok=True)
        for s in o["front"]["screens"]:
            (shots / f"{s['id']}-{front_build.views(o['profile'])[0]}-full.png").write_bytes(b"png")
        if kind == "android":
            apks["android/app-debug.apk"] = "digest"
        return found.get(kind, [])

    return seen, patch.object(front_build, "looked", look)


def checked(tree, sent=None):
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a) if sent is not None else None):
        then = oneshot_build.checked(tree, Run(), spec(), report() + fenced("oneshot-device", DEVICE))
        if then:
            then()
    return then


def test_the_build_text_names_every_kind_and_only_the_android_apps_scenarios(kinds_build):
    with patch.object(oneshot_build.oneshot_android, "build_env",
                      lambda: dict.fromkeys(oneshot_build.TOOLCHAIN, "C:/app/sdk")):
        text = front_build.build_front_text(spec())
    assert "for each kind they picked" in text and "{" + "scenarios}" not in text
    design = json.loads(text.split("```json\n")[1].split("\n```")[0])
    assert [s["id"] for s in design["android"]["screens"]] == ["S1"] and design["webapp"]["features"][0]["id"] == "history"
    assert "- `R1`" in text and "- `R3`" not in text   # R3 is the web app's: no instrumented test is asked for it
    assert "The web screens are shot at\ndesktop" in text


def test_each_kind_is_looked_at_in_the_owners_order_with_only_its_own_and_the_critique_follows(kinds_build):
    seen, looking = looks({})
    sent, started = [], []
    with looking, patch.object(oneshot_spec, "start", lambda repo, worker, *a, **k: started.append(worker)):
        assert checked(kinds_build, sent) is not None
    assert [s["kind"] for s in seen] == KINDS, sent
    assert started == ["critique"]
    android, web = seen
    assert android == {**android, "screens": ["S1"], "features": ["read"], "requirements": ["R1", "R2"], "views": ["phone"],
                       "device": DEVICE}
    assert web == {**web, "screens": ["S2"], "features": ["history"], "requirements": ["R3"], "views": ["desktop"],
                   "inventory": ["GET /history"], "device": None}
    assert o()["stage"] == "critique" and o()["working"] == "critique" and not o().get("stopped")
    assert o()["device"] == DEVICE and o()["apks"] == {"android/app-debug.apk": "digest"}
    shots = oneshot_spec.store("budget").folder / "build" / "r1"
    assert sorted(p.name for p in shots.iterdir()) == ["S1-phone-full.png", "S2-desktop-full.png"]   # side by side


def test_a_kind_that_fails_sends_the_build_back_and_the_next_head_looks_at_every_kind_again(kinds_build):
    high = {"severity": "high", "screen": "S1", "viewport": "phone", "state": "full", "what": "단추가 화면 밖으로 나간다"}
    seen, looking = looks({"android": [high]})
    sent = []
    with looking:
        checked(kinds_build, sent)
    assert [s["kind"] for s in seen] == ["android"]   # the web app waits for the fix
    assert o()["stage"] == "build-front" and "Android 앱 S1 phone full: 단추가 화면 밖으로" in sent[-1][2]
    assert o()["looks"][-1]["findings"] == [{**high, "kind": "android"}]
    write(kinds_build, "android/fixed.txt", "고쳤다")
    git(kinds_build, "add", "-A")
    git(kinds_build, "commit", "-m", "fix")
    seen, looking = looks({})
    with looking:
        checked(kinds_build)
    assert [s["kind"] for s in seen] == KINDS and o()["stage"] == "critique" and o()["looks"][-1]["round"] == 2


def test_several_kinds_without_live_cmd_are_sent_back(kinds_build):
    write(kinds_build, ".wiki/adapter.toml", adapter(gate=KINDS_GATE, live=""))
    git(kinds_build, "add", "-A")
    git(kinds_build, "commit", "-m", "no live")
    sent = []
    seen, looking = looks({})
    with looking:
        checked(kinds_build, sent)
    assert not seen and "`live_cmd` 가 비었다" in sent[-1][2]


@pytest.mark.parametrize("gate", [GATE, f"{GATE} && {BUILD}"])
def test_a_gate_without_a_command_for_each_kind_is_sent_back(kinds_build, gate):
    write(kinds_build, ".wiki/adapter.toml", adapter(gate=gate))
    git(kinds_build, "add", "-A")
    git(kinds_build, "commit", "-m", "core gate only")
    sent = []
    seen, looking = looks({})
    with looking:
        checked(kinds_build, sent)
    assert not seen and "종류마다의 테스트와 빌드 명령" in sent[-1][2] and o()["stage"] == "build-front"
