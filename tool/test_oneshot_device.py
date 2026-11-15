"""oneshot step 7e: S7 on an emulator the run owns (`docs/plans/oneshot/7-android.md`, "How 7e builds it").

A fake phone stands in for `adb`: it answers the commands the harness sends through `android_owned.call`,
draws each screen's hierarchy for the screen and state the launch extras ask for, and follows a tap to the
screen a control leads to. `android_owned.boot` is replaced by a context that only records it ran; the
owned emulator itself is `test_android_owned`'s. The build check runs for real on a Git worktree.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch
from xml.sax.saxutils import quoteattr

import pytest

import android_owned
from main import oneshot_build, oneshot_critique as critique, oneshot_device as device, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_front_build import Run, git, o, report, spec, staged, write  # noqa: F401 — `staged` is a fixture
from test_oneshot_spec import fenced, world  # noqa: F401 — `world` is the temporary worktree's fixture

PKG, SIZE = "com.example.budget", {"width": 1080, "height": 2400, "dpi": 420}
DEVICE = {"apk": "app/app-debug.apk", "package": PKG, "activity": ".MainActivity",
          "tests": {"apk": "app/app-debug-androidTest.apk", "runner": f"{PKG}.test/androidx.test.runner.AndroidJUnitRunner"}}
SCREENS = [{"id": "S1", "surface": "screen", "purpose": "오늘 쓴 돈을 적는다", "features": ["add"], "primary": True,
            "reached": "start"},
           {"id": "S2", "surface": "screen", "purpose": "달별로 본다", "features": ["months"], "primary": False,
            "reached": "one-click"},
           {"id": "S3", "surface": "notification", "purpose": "내보내기를 알린다", "features": ["export"], "primary": False,
            "reached": "deeper"}]
FREQUENCY = {"add": "every", "months": "sometimes", "export": "rarely", "sync": "never"}
REQS = [{"id": "R1", "text": "지출을 적으면 합계에 더해진다", "postconditions": ["합계가 늘었다"]},
        {"id": "R2", "text": "알림을 띄운다", "capability": {"permission": "android.permission.POST_NOTIFICATIONS",
                                                         "prompt": "알림 허용", "risk": "낮음"}}]
PASSED = ("INSTRUMENTATION_STATUS: class=com.example.budget.ScenarioTest\n"
          "INSTRUMENTATION_STATUS: test=scenario_R1_addsToTheTotal\nINSTRUMENTATION_STATUS_CODE: 1\n"
          "INSTRUMENTATION_STATUS: class=com.example.budget.ScenarioTest\n"
          "INSTRUMENTATION_STATUS: test=scenario_R1_addsToTheTotal\nINSTRUMENTATION_STATUS_CODE: 0\n"
          "INSTRUMENTATION_RESULT: stream=OK (1 test)\nINSTRUMENTATION_CODE: -1\n")


def node(fid="", text="", box=(48, 300, 1032, 444), clickable=False, package=PKG, desc="", leads="", children=()):
    return {"fid": fid, "text": text, "box": box, "clickable": clickable, "package": package, "desc": desc,
            "leads": leads, "children": list(children)}


def xml(nodes) -> str:
    def one(n):
        attrs = {"resource-id": f"feature:{n['fid']}" if n["fid"] and not n["desc"] else "", "text": n["text"],
                 "content-desc": n["desc"], "package": n["package"], "clickable": str(n["clickable"]).lower(),
                 "class": "android.view.View", "visible-to-user": "true",
                 "bounds": "[{}, {}][{}, {}]".format(*n["box"]).replace(" ", "")}
        inner = "".join(one(c) for c in n["children"])
        return "<node " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items()) + f">{inner}</node>"

    return "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">" + \
        "".join(one(n) for n in nodes) + "</hierarchy>"


def clean_app() -> dict:
    """Each screen's nodes: S1 carries `add` and a tab leading to S2, S2 carries `months`, S3 is the shade."""

    return {"S1": [node("add", "적기", clickable=True), node(text="달별", box=(48, 500, 1032, 644), clickable=True, leads="S2")],
            "S2": [node("months", "11월", clickable=True)],
            "S3": [node("export", "내보냈다", package="com.android.systemui", desc="feature:export")]}


class Phone:
    """The emulator and the app as `adb` sees them."""

    def __init__(self, screens: dict):
        self.screens, self.at, self.sent, self.events, self.frame = screens, None, [], "", b""
        self.instrument, self.packages = PASSED, set()

    def call(self, folder, args, *, device=True, timeout=0, binary=False, stream=False):
        self.sent.append(args)
        out = ""
        if args[:2] == ["shell", "am"] and args[2] == "start":
            extras = dict(zip(args[args.index("--es") + 1::3], args[args.index("--es") + 2::3]))
            self.at = extras["oneshot.screen"]
            out = "Starting: Intent\nStatus: ok\nLaunchState: COLD\n"
        elif args[:2] == ["exec-out", "cat"]:
            out = xml(self.screens.get(self.at, []))
        elif args[:3] == ["shell", "input", "tap"]:
            x, y = int(args[3]), int(args[4])
            hit = [n for n in self.screens.get(self.at, []) if n["clickable"] and n["leads"]
                   and n["box"][0] <= x <= n["box"][2] and n["box"][1] <= y <= n["box"][3]]
            self.at = hit[0]["leads"] if hit else self.at
        elif args[:2] == ["exec-out", "screencap"]:
            return b"\x89PNG\r\n\x1a\nshot" if "-p" in args else self.frame
        elif args[0] == "install":
            self.packages.add(args[-1])
            out = "Success\n"
        elif args[:3] == ["shell", "pm", "path"]:
            out = f"package:/data/app/{args[3]}/base.apk\n"
        elif args[:2] == ["logcat", "-d"]:
            out = self.events
        elif args[:3] == ["shell", "am", "instrument"]:
            out = self.instrument
        return out.encode() if binary else out


@pytest.fixture
def phone(tmp_path, monkeypatch):
    p = Phone(clean_app())
    booted, cleaned = [], []

    @contextmanager
    def boot(folder, tools, device=None, renderer="auto"):
        booted.append((folder, device))
        yield "emulator-5586"

    monkeypatch.setattr(android_owned, "call", p.call)
    monkeypatch.setattr(android_owned, "boot", boot)
    monkeypatch.setattr(android_owned, "cleanup", lambda folder: cleaned.append(folder) or [])
    monkeypatch.setattr(device, "tools", lambda: {"adb": {}, "emulator": {}, "image": {}})
    monkeypatch.setattr(device, "SETTLE_SECONDS", 0)
    p.booted, p.cleaned, p.tree = booted, cleaned, tmp_path / "apks"
    p.tree.mkdir()
    return p


def android_spec(frequency=FREQUENCY, reqs=REQS) -> dict:
    return {"repo": "budget", "requirements": reqs,
            "oneshot": {"profile": {"device": "medium-phone"}, "frequency": frequency,
                        "front": {"screens": SCREENS}, "intent": {"kind": "android"}}}


def looked(phone, tmp_path, s=None):
    return device.look(phone.tree, DEVICE, s or android_spec(), tmp_path / "device", tmp_path / "shots")


def whats(findings) -> str:
    return " / ".join(f["what"] for f in findings)


# -- The look ------------------------------------------------------------------------------------------

def test_every_screen_in_every_state_is_shot_on_an_owned_emulator_and_a_clean_app_passes(phone, tmp_path):
    assert looked(phone, tmp_path) == []
    assert phone.booted[0][1] == SIZE   # the reference phone's size and density
    assert sorted(p.name for p in (tmp_path / "shots").iterdir()) == sorted(
        f"{s}-phone-{state}.png" for s in ("S1", "S2", "S3") for state in device.STATES)
    # Only what the requirements state is granted; the extras open each screen in each state.
    grants = [a for a in phone.sent if a[:2] in (["shell", "pm"], ["shell", "appops"]) and "path" not in a]
    assert grants == [["shell", "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS"]]
    assert ["shell", "cmd", "statusbar", "expand-notifications"] in phone.sent
    assert ["shell", "am", "start", "-W", "-S", "-n", f"{PKG}/.MainActivity", "--es", "oneshot.screen", "S2",
            "--es", "oneshot.state", "loading"] in phone.sent


def test_an_earlier_looks_emulator_is_cleaned_first_and_one_left_behind_stops_the_look(phone, tmp_path, monkeypatch):
    (tmp_path / "device" / "r1").mkdir(parents=True)
    looked(phone, tmp_path)
    assert phone.cleaned == [tmp_path / "device" / "r1"]
    monkeypatch.setattr(android_owned, "cleanup", lambda folder: ["emulator"])
    with pytest.raises(ValueError, match="정리가 끝나지 않았다"):
        looked(phone, tmp_path)


def test_a_build_that_moves_a_feature_from_its_approved_place_is_refused(phone, tmp_path):
    phone.screens["S1"] = [node(text="달별", box=(48, 500, 1032, 644), clickable=True, leads="S2")]
    phone.screens["S2"] = [node("months", "11월", clickable=True), node("add", "적기", box=(48, 700, 1032, 844), clickable=True)]
    found = whats(looked(phone, tmp_path))
    assert "늘 쓰는 기능 `add` 가 첫 화면에 바로 보이지 않는다" in found


@pytest.mark.parametrize("leak, said", [
    (None, None),   # a `never` feature with no control anywhere is correctly absent
    ("S2", "화면에 없기로 한 `sync` 의 컨트롤이 화면에 있다"),
    ("S1", "`sync` 는 첫 화면에 없어야 한다"),
])
def test_a_never_feature_passes_without_a_control_and_is_refused_with_one(phone, tmp_path, leak, said):
    if leak:
        phone.screens[leak].append(node("sync", "동기화", box=(48, 900, 1032, 1044), clickable=True))
    found = looked(phone, tmp_path)
    assert (found == []) if said is None else said in whats(found)


def test_a_sometimes_feature_more_than_one_tap_away_and_a_feature_never_exported_are_refused(phone, tmp_path):
    phone.screens["S1"] = [node("add", "적기", clickable=True)]   # no way to S2 from the first screen
    phone.screens["S3"] = [node(text="내보냈다", package="com.android.systemui")]   # the tag never reached the hierarchy
    found = whats(looked(phone, tmp_path))
    assert "가끔 쓰는 기능 `months` 가 한 번 눌러서 보이지 않는다" in found
    assert "`feature:export` 가 어느 화면의 계층에도 없다" in found


def test_a_crash_and_a_failed_or_missing_scenario_test_are_refused(phone, tmp_path):
    phone.events = f"11-15 12:00:00.000 I am_crash: [123,0,{PKG},1,java.lang.IllegalStateException]\n"
    phone.instrument = PASSED.replace("STATUS_CODE: 0", "STATUS_CODE: -2\nINSTRUMENTATION_STATUS: stack=AssertionError")
    found = whats(looked(phone, tmp_path, android_spec(reqs=[*REQS, {"id": "R3", "text": "x", "postconditions": ["y"]}])))
    assert "앱이 죽거나 응답하지 않았다" in found and "scenario_R1_addsToTheTotal` 가 실패했다" in found
    assert "시나리오 R3 의 계측 테스트" in found


def test_another_scenarios_test_never_stands_in_for_a_missing_one(phone, tmp_path):
    phone.instrument = PASSED.replace("scenario_R1_", "scenario_R10_")
    found = whats(looked(phone, tmp_path, android_spec(reqs=[*REQS, {"id": "R10", "text": "x", "postconditions": ["y"]}])))
    assert "시나리오 R1 의 계측 테스트" in found and "시나리오 R10" not in found
    assert device.covers("scenario_R1", "R1") and device.covers("SCENARIO_r1_adds", "R1")
    assert not device.covers("scenario_R10_adds", "R1") and not device.covers("scenario_R1adds", "R1")


@pytest.mark.parametrize("output", [
    PASSED.split("INSTRUMENTATION_RESULT:")[0] + "INSTRUMENTATION_ABORTED: System has crashed.\n",
    PASSED.replace("INSTRUMENTATION_CODE: -1", "INSTRUMENTATION_CODE: 0"),
    PASSED.split("INSTRUMENTATION_RESULT:")[0],   # cut before the run reported its end
])
def test_a_run_that_did_not_reach_its_end_fails_even_after_every_test_passed(phone, tmp_path, output):
    phone.instrument = output
    assert "시나리오 테스트가 끝까지 돌지 못했다" in whats(looked(phone, tmp_path))


@pytest.mark.parametrize("state", ["empty", "loading", "error"])
def test_a_never_control_in_any_state_is_refused(phone, tmp_path, state):
    call, at = phone.call, {}

    def stateful(folder, args, **options):
        if args[:3] == ["shell", "am", "start"]:
            at["state"] = args[-1]
        if args[:2] == ["exec-out", "cat"] and phone.at == "S2" and at.get("state") == state:
            return xml(phone.screens["S2"] + [node("sync", "동기화", box=(48, 900, 1032, 1044), clickable=True)])
        return call(folder, args, **options)

    with patch.object(android_owned, "call", stateful):
        assert "화면에 없기로 한 `sync`" in whats(looked(phone, tmp_path))


def test_a_notification_control_system_ui_draws_is_checked_as_the_apps_own(phone, tmp_path):
    phone.screens["S3"] = [node("export", "", box=(10, 10, 20, 20), clickable=True, package="com.android.systemui",
                                desc="feature:export"),
                           node(text="시계", box=(-30, 0, 10, 10), package="com.android.systemui")]   # System UI's own
    found = whats(looked(phone, tmp_path))
    assert "48dp 보다 작다" in found and "읽을 이름" in found   # an identity is not a label
    assert "시계" not in found


def test_a_runner_the_test_apk_does_not_hold_and_an_activity_that_does_not_open_are_findings(phone, tmp_path):
    call = phone.call

    def failing(folder, args, **options):
        if args[:3] == ["shell", "am", "instrument"]:
            raise ValueError("adb shell am failed: INSTRUMENTATION_FAILED: com.nope.test/Runner")
        if args[:3] == ["shell", "am", "start"] and "S2" in args:
            return "Error: Activity class {com.example.budget/.MainActivity} does not exist."
        return call(folder, args, **options)

    with patch.object(android_owned, "call", failing):
        found = whats(looked(phone, tmp_path))
    assert "시나리오 테스트가 끝까지 돌지 못했다" in found and "앱을 열지 못했다 — Error: Activity class" in found


def test_a_stop_during_the_look_ends_it_inside_the_owned_emulator(phone, tmp_path):
    with pytest.raises(ValueError, match="멈췄다"):
        device.look(phone.tree, DEVICE, android_spec(), tmp_path / "device", tmp_path / "shots", stopped=lambda: True)
    assert phone.booted   # the boot's own exit cleans what it started


# -- The mechanical checks ------------------------------------------------------------------------------

def root(*nodes):
    from xml.etree import ElementTree
    return ElementTree.fromstring(xml(nodes))


def frame(ink: tuple[int, int, int], ground: tuple[int, int, int], box) -> tuple[int, int, bytes]:
    """A raw RGBA frame of `ground`, with `ink` filling the lower third of `box`'s rows, as text would."""

    width, height = 200, 100
    data = bytearray(bytes([*ground, 255]) * width * height)
    for y in range(box[1] + (box[3] - box[1]) * 2 // 3, box[3]):
        for x in range(box[0], box[2]):
            data[(y * width + x) * 4:(y * width + x) * 4 + 3] = bytes(ink)
    return width, height, bytes(data)


def test_mechanical_findings_are_read_off_the_apps_own_nodes():
    small = {**SIZE, "width": 200, "height": 100}
    found = device.mechanical(root(node(text="합계", box=(-10, 0, 120, 40)),
                                   node(text="+", box=(130, 0, 150, 20), clickable=True),
                                   node(box=(0, 50, 200, 100), clickable=True),
                                   node(text="시스템", box=(-50, 0, 10, 10), package="com.android.systemui")), PKG, small, None)
    assert any("화면 밖" in f and "합계" in f for f in found) and any("48dp" in f and "+" in f for f in found)
    assert any("읽을 이름" in f for f in found) and not any("시스템" in f for f in found)


def test_text_is_refused_only_when_its_ink_and_ground_lack_contrast():
    box = (10, 10, 190, 70)
    black = frame((0, 0, 0), (255, 255, 255), box)
    grey = frame((200, 200, 200), (255, 255, 255), box)
    assert device.contrast(black, box) > 20 and device.contrast(grey, box) < device.MIN_CONTRAST
    header = (200).to_bytes(4, "little") + (100).to_bytes(4, "little") + bytes(8)
    assert device.raw(header + black[2]) == black and device.raw(b"short") is None
    small = {**SIZE, "width": 200, "height": 100}
    assert any("명암비" in f for f in device.mechanical(root(node(text="흐림", box=box)), PKG, small, grey))
    assert device.mechanical(root(node(text="또렷", box=box)), PKG, small, black) == []


def test_instrumented_results_are_read_per_test():
    out = device.instrumented(PASSED + "INSTRUMENTATION_STATUS: class=A\nINSTRUMENTATION_STATUS: test=b\n"
                                       "INSTRUMENTATION_STATUS_CODE: -3\n")
    assert out == [("com.example.budget.ScenarioTest", "scenario_R1_addsToTheTotal", 0, ""), ("A", "b", -3, "")]
    assert "건너뛰었다" in whats(device.scenario_findings(PASSED.replace("CODE: 0", "CODE: -3"), ["R1"], "S1"))


# -- The build turn's block -----------------------------------------------------------------------------

@pytest.mark.parametrize("block, needs, said", [
    ({**DEVICE, "apk": "/abs/app.apk"}, False, "상대 경로"),
    ({**DEVICE, "apk": "../app.apk"}, False, "상대 경로"),
    ({**DEVICE, "package": "budget"}, False, "applicationId"),
    ({**DEVICE, "tests": {**DEVICE["tests"], "runner": "Runner"}}, False, "tests.runner"),
    (DEVICE, True, "fixture"),
    ({**DEVICE, "fixture": {"apk": "f.apk", "package": PKG}}, True, "fixture"),
])
def test_a_device_block_outside_its_shape_is_refused(tmp_path, block, needs, said):
    with pytest.raises(ValueError, match=said):
        device.declared(tmp_path, fenced("oneshot-device", block), needs)


def test_a_screen_reading_app_names_its_fixture_viewer_and_every_apk_must_be_built(tmp_path):
    block = {**DEVICE, "fixture": {"apk": "fixture/f.apk", "package": f"{PKG}.fixture"}}
    found = device.declared(tmp_path, fenced("oneshot-device", block), True)
    assert found["fixture"] == block["fixture"]
    assert "app/app-debug.apk" in device.built(tmp_path, found)
    for apk in ("app/app-debug.apk", "app/app-debug-androidTest.apk", "fixture/f.apk"):
        write(tmp_path, apk, "apk")
    assert device.built(tmp_path, found) == ""
    reads = {"capability": {"permission": "MediaProjection (android.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION)"}}
    assert device.reads_screen({"requirements": [reads]}) and not device.reads_screen({"requirements": REQS})


# -- Through the build check ----------------------------------------------------------------------------

@pytest.fixture
def android_build(staged, phone, monkeypatch):  # noqa: F811 — the imported fixture, requested by name
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "intent": {"kind": "android"}, "profile": {"device": "medium-phone"}, "frequency": FREQUENCY,
        "front": {"screens": SCREENS, "states": {}, "tokens": {"palettes": []}, "shots": 1}})
    monkeypatch.setattr(oneshot_build, "toolchain", lambda spec: None)   # the gate runs as the test's own Python
    phone.tree = staged
    for apk in (DEVICE["apk"], DEVICE["tests"]["apk"]):
        write(staged, apk, "apk")
    gate = o()["gate"]
    write(staged, ".wiki/adapter.toml", f"[slots]\ngate_cmd = {json.dumps(gate)}\n")
    git(staged, "add", "-A")
    git(staged, "commit", "-m", "screens")
    return staged


def test_an_android_build_with_its_device_block_and_clean_shots_moves_to_critique(android_build):
    then = oneshot_build.checked(android_build, Run(), spec(), report() + fenced("oneshot-device", DEVICE))
    assert then is not None and o()["stage"] == "critique", o().get("stopped")
    assert o()["device"] == DEVICE and o()["looks"][0]["findings"] == []
    assert (oneshot_spec.store("budget").folder / "build" / "r1" / "S1-phone-full.png").is_file()


def test_an_android_build_without_its_device_block_is_sent_back(android_build):
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        oneshot_build.checked(android_build, Run(), spec(), report())()
    assert o()["stage"] == "build-front" and "`oneshot-device` 블록이 하나 있어야 한다" in sent[-1][2]


def test_two_clean_android_rounds_stop_before_r3_for_the_phone_round(android_build):
    look = {"round": 2, "head": "abc", "findings": []}
    oneshot_spec.saved("budget", oneshot={"critiques": [{"look": 1, "head": "abc", "findings": [], "clean": True}],
                                          "looks": [look]})
    critique.judged("budget", oneshot_spec.store("budget"), spec(), look, [])
    assert o()["stage"] == "r3" and o()["stopped"]["reason"] == "kind" and "7f" in o()["stopped"]["detail"]
    assert "r3_sheet" not in critique.extra(spec())
    oneshot_spec.recover()   # this version's own stop survives a restart
    assert o()["stopped"]["reason"] == "kind"
