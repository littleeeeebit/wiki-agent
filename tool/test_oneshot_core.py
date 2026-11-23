"""oneshot step 7b: an Android app's core path — the S2 stack and capabilities, the S3 module design and R1
module table, S4's host-side runner with its lint and debug APK in the gate, and the stop after the review.

See `docs/plans/oneshot/7-android.md` ("S2" to "S4"). Model turns (`oneshot.ask`) are stand-ins; Git runs in a
temporary worktree.
"""

import json
import subprocess
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import (oneshot, oneshot_brief as brief, oneshot_build, oneshot_profile, oneshot_review,
                  oneshot_sheet as sheet, oneshot_spec, specs)
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import fenced, o, on_sheet, world  # noqa: F401 — `world` is the temporary worktree's fixture

STACK = [{"part": p, "choice": c, "why": "주인이 골랐다", "status": "confirmed"} for p, c in (
    ("language", "Kotlin 2"), ("ui", "Jetpack Compose"), ("core", "ML Kit 글자 인식"), ("data_store", "Room"),
    ("tests", "Gradle JVM 단위 테스트"), ("build", "Gradle, 디버그 APK"))]
CAPTURE = {"permission": "MediaProjection", "prompt": "화면 녹화를 시작할지 묻는 창이 뜬다",
           "risk": "Play 심사에서 화면을 읽는 이유를 밝혀야 한다"}
REQS = [{"id": "R1", "text": "만화를 넘기면 새 말풍선만 번역이 뜬다", "source": {"kind": "original", "locator": "주인의 문장"},
         "status": "confirmed", "postconditions": ["이미 번역한 말풍선은 다시 번역하지 않는다"]},
        {"id": "R2", "text": "다른 앱의 화면을 읽는다", "source": {"kind": "policy", "locator": "R1 에 필요한 권한"},
         "status": "assumed", "capability": CAPTURE, "depends_on": ["R1"]}]
GLOSSARY = [{"term": "말풍선", "meaning": "만화 속 대사 한 칸", "avoid": ["bubble"]}]
DESIGN = {
    "oneshot-modules": [
        {"id": "M1", "name": "PageReader", "contract": "화면 한 장을 받아 말풍선 글자 상자 목록을 돌려준다",
         "inputs": {"frame": "Bitmap"}, "outputs": {"boxes": "List<TextBox>"}, "errors": ["빈 화면"], "state": "",
         "stores": "", "calls": ["M2"], "depends_on": ["R1", "R2"]},
        {"id": "M2", "name": "SeenBoxes", "contract": "글자 상자가 이미 번역한 것인지 알려 준다",
         "inputs": {"box": "TextBox"}, "outputs": {"seen": "Boolean"}, "errors": [], "state": "이번 화의 상자 해시",
         "stores": "번역한 상자 해시와 번역문", "calls": ["X1"]},
    ],
    "oneshot-outside": [{"id": "X1", "target": "번역 API", "does": "일본어 대사를 한국어로 옮긴다",
                         "failure": "실패하면 원문을 그대로 두고 다음 화면에서 다시 시도한다", "depends_on": ["R1"]}],
    "oneshot-assumptions": [{"id": "A1", "what": "세로 스크롤과 페이지 넘김을 화면 이동량으로 가른다",
                             "why": "주인이 두 방식을 다 읽는다", "rejected": "앱마다 따로 설정", "depends_on": ["R1"]}],
    "oneshot-tests": [{"id": "T1", "test_name": "test_a_box_already_translated_is_skipped", "given": "번역한 상자 하나",
                       "when": "같은 상자가 다시 보이면", "then": "번역을 부르지 않는다", "depends_on": ["R1", "M2"]}],
}


@pytest.fixture
def android(world):  # noqa: F811 — the imported fixture, by its name
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "만화 화면 번역기를 만든다.", "kind": "android"},
                                          "profile": {"auth": "none", "visibility": "private", "docs_language": "ko",
                                                      "device": "galaxy-s21"}})
    return world


def answer(design=DESIGN) -> str:
    return "설계했다.\n\n" + "".join(fenced(k, v) for k, v in design.items())


# -- S2 --------------------------------------------------------------------------------

def test_the_android_conversation_asks_its_own_parts_and_ends_on_a_confirmed_bundle(android):
    with pytest.raises(ValueError, match="language"):
        oneshot_profile.stacked([{**STACK[0], "part": "backend"}], "android")
    assert oneshot_spec.ended(REQS, STACK[:5], "android") == "주인이 아직 고르지 않은 스택: build"
    oneshot_spec.saved("budget", oneshot={"stack": [{**s, "status": "assumed"} for s in STACK], "talk": [
        {"role": "agent", "text": "묶음을 고르세요.", "choices": {"questions": [{"question": "어느 묶음?", "options": [
            {"label": "Kotlin + Compose"}, {"label": "Flutter"}]}]}, "ts": 1},
        {"role": "owner", "text": "Kotlin + Compose", "ts": 2}]})
    told = []
    reply = "정했다.\n\n" + fenced("oneshot-spec", {"stack": STACK, "requirements": REQS, "glossary": GLOSSARY})
    with patch.object(oneshot, "ask", lambda store, role, system, text, *a, **k: told.append(system) or (
            reply, {"cost_usd": 0.1}, False)):
        oneshot_spec.talk("budget")
    assert "Android app" in told[0] and "`capability`" in told[0]
    assert o()["stage"] == "design" and specs.load("budget", oneshot.BRANCH)["requirements"][1]["capability"] == CAPTURE


@pytest.mark.parametrize("ability", [{"permission": "MediaProjection", "prompt": "창이 뜬다"}, "MediaProjection",
                                     {**CAPTURE, "risk": " "}])
def test_a_capability_without_its_prompt_and_risk_is_refused(ability):
    with pytest.raises(ValueError, match="capability"):
        sheet.requirements([{**REQS[1], "capability": ability, "depends_on": []}])


# -- S3 and R1 -----------------------------------------------------------------------------

def core_with(changes: dict) -> dict:
    blocks = json.loads(json.dumps(DESIGN))
    for (block, n, key), value in changes.items():
        blocks[block][n][key] = value
    return blocks


@pytest.mark.parametrize("change, said", [
    ({("oneshot-modules", 1, "calls"): []}, "X1: 이 바깥 호출을 부르는 모듈이 없다"),
    ({("oneshot-modules", 0, "calls"): ["M9"]}, "M1: `calls`"),
    ({("oneshot-modules", 0, "calls"): []}, "M2: 어떤 요구에도"),
    ({("oneshot-modules", 0, "depends_on"): ["R2"]}, "R1: 이 시나리오를 맡은 모듈이 없다"),
    ({("oneshot-tests", 0, "depends_on"): ["R1"]}, "T1: 시험하는 요구와"),
    ({("oneshot-modules", 0, "contract"): "bubble 을 찾는다"}, "용어집의 `말풍선`"),
])
def test_each_core_check_refuses(change, said):
    design = sheet.parsed(core_with(change), "android")
    with pytest.raises(ValueError) as refused:
        sheet.checked(design, REQS, GLOSSARY, {}, "android")
    assert said in str(refused.value)


def test_a_module_name_holding_an_approved_term_inside_a_longer_name_is_refused():
    glossary = [*GLOSSARY, {"term": "bubble_finder", "meaning": "말풍선을 찾는 모듈", "avoid": []}]
    named = {("oneshot-modules", 0, "name"): "bubble_finder"}
    sheet.checked(sheet.parsed(core_with(named), "android"), REQS, glossary, {}, "android")
    with pytest.raises(ValueError, match="용어집의 `말풍선`"):
        sheet.checked(sheet.parsed(core_with({("oneshot-modules", 0, "name"): "Auto_bubble_finder"}), "android"),
                      REQS, glossary, {}, "android")


def test_a_valid_core_passes_and_a_web_block_is_not_one():
    sheet.checked(sheet.parsed(DESIGN, "android"), REQS, GLOSSARY, {}, "android")
    with pytest.raises(ValueError, match="oneshot-modules"):
        sheet.parsed({"oneshot-api": []}, "android")
    assert sheet.blocks("android") == "oneshot-modules,oneshot-outside,oneshot-assumptions,oneshot-tests"
    # A case finds its planned test by name, so no two planned tests share one.
    twin = {**DESIGN["oneshot-tests"][0], "id": "T2", "then": "다른 결과"}
    with pytest.raises(ValueError, match="`test_name` 이 겹친다"):
        sheet.parsed({**DESIGN, "oneshot-tests": [*DESIGN["oneshot-tests"], twin]}, "android")


def test_an_app_that_never_leaves_the_phone_passes_with_no_outside_calls():
    offline = {**core_with({("oneshot-modules", 1, "calls"): []}), "oneshot-outside": []}
    sheet.checked(sheet.parsed(offline, "android"), REQS, GLOSSARY, {}, "android")


def test_an_edit_reaches_the_modules_a_changed_module_calls_and_is_called_by():
    design = sheet.parsed(DESIGN, "android")
    assert "M2" in sheet.dependents(design, REQS, {"R1"})            # R1's module M1 calls M2
    assert {"M1", "X1", "T1"} <= sheet.dependents(design, REQS, {"M2"})   # M2's caller, callee and test


def test_a_module_past_the_table_shows_its_contract_and_calls_on_the_sheet():
    blocks = json.loads(json.dumps(DESIGN))
    blocks["oneshot-modules"] += [{**DESIGN["oneshot-modules"][1], "id": f"M{i}", "name": f"Module{i}",
                                   "contract": f"계약{i}", "calls": ["M2"], "depends_on": ["R1"]} for i in range(3, 14)]
    spec = {"repo": "budget", "requirements": REQS, "oneshot": {
        "sentence": "만화 화면 번역기", "intent": {"kind": "android", "restated": "만화 화면 번역기를 만든다."},
        "profile": {}, "glossary": GLOSSARY, "design": sheet.parsed(blocks, "android")}}
    assert "Module13: 계약 계약13 / 부르는 것 M2 / 입력" in sheet.sheet(spec)


def test_the_core_design_turn_is_shown_as_a_module_sheet(android):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY, "stack": STACK, "stage": "design"})
    told = []
    with patch.object(oneshot, "ask", lambda store, role, system, text, *a, **k: told.append(system) or (
            answer(), {"cost_usd": 0.1}, False)):
        oneshot_spec.design("budget")
    assert "core of a new Android app" in told[0] and o()["stage"] == "r1"
    text = sheet.sheet(specs.load("budget", oneshot.BRANCH))
    assert text.startswith("# 핵심 설계 — budget") and "| 핵심 로직 | ML Kit 글자 인식 |" in text
    assert "| M2 | SeenBoxes | 글자 상자가 이미 번역한 것인지 알려 준다 | X1 |" in text
    assert "| R2 | `MediaProjection` | 화면 녹화를 시작할지 묻는 창이 뜬다 |" in text
    assert "SeenBoxes: 번역한 상자 해시와 번역문" in text and "실패하면 원문을 그대로 두고" in text
    assert sheet.decidable(o()["design"], REQS) == ["R1", "R2", "M1", "M2", "X1", "A1", "T1"]


def test_approving_the_core_writes_its_sheet_and_contracts_and_moves_to_tests(android):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stack": STACK, "stage": "r1", "design": sheet.parsed(DESIGN, "android"),
        "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": []}})
    items = sheet.decidable(o()["design"], REQS)
    oneshot_spec.decide("budget", on_sheet(decisions={i: {"approve": True} for i in items}))
    assert o()["stage"] == "tests"
    assert (android / "docs/design/core.md").read_text(encoding="utf-8").startswith("# 핵심 설계")
    assert json.loads((android / "docs/design/core.json").read_text(encoding="utf-8"))["modules"][1]["id"] == "M2"
    shown = subprocess.run(["git", "show", "--stat", "HEAD"], cwd=android, capture_output=True, text=True).stdout
    assert "docs/design/core.json" in shown and "api.yaml" not in shown and "core design" in shown


CORE_BRIEF = {"sections": [
    {"name": "한 줄", "text": "만화를 넘기면 새 말풍선만 한국어로 옮겨 보여 주는 앱입니다."},
    {"name": "쓰는 장면", "text": "1. 만화 앱을 엽니다. 2. 페이지를 넘깁니다. 3. 새 말풍선 위에 번역이 뜹니다. (R1)"},
    {"name": "알아 두실 것", "text": "다른 앱 화면을 읽으려고 화면 녹화 허락을 받습니다. (R2) 대사는 번역 서비스로 보냅니다. (X1)"},
    {"name": "다 됐다고 보는 기준", "text": "이미 옮긴 말풍선은 다시 옮기지 않습니다."}],
    "confirm": [{"key": "R2", "text": "화면을 읽으려면 녹화 허락이 필요합니다."},
                {"key": "A1", "text": "스크롤과 페이지 넘김을 화면이 움직인 양으로 가릅니다."}]}


def test_the_core_brief_names_every_permission_and_outside_call_and_approves_the_modules(android):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY, "stack": STACK, "stage": "design"})
    silent = {**CORE_BRIEF, "sections": [{**s, "text": "화면 녹화 허락을 받습니다. (R2)"} if s["name"] == "알아 두실 것" else s
                                         for s in CORE_BRIEF["sections"]]}
    with pytest.raises(ValueError, match="밖으로 나가는 호출이 있다: X1"):
        brief.checked(silent, brief.snapshot({**specs.load("budget", oneshot.BRANCH), "oneshot": {
            **o(), "design": sheet.parsed(DESIGN, "android")}}), {"R2": ["R2"], "A1": ["A1"]}, 1)
    with patch.object(oneshot, "ask", lambda store, role, *r: (
            answer() if role == "design" else fenced("oneshot-brief", CORE_BRIEF), {"cost_usd": 0.1}, False)):
        oneshot_spec.design("budget")
    assert oneshot_spec.status("budget")["brief"]["lines"] == {"R2": ["R2"], "A1": ["A1"]}
    lines = {"R2": {"approve": True}, "A1": {"approve": True}}
    oneshot_spec.decide("budget", on_sheet(decisions={}, lines=lines, rest=True))
    assert o()["stage"] == "tests" and {"M1", "M2", "X1", "T1"} <= set(o()["r1"]["decisions"])


def test_a_stored_brief_that_fails_todays_checks_is_not_shown(android):
    """A brief an earlier version stored, for the same gate, that names no outside call: the full sheet decides."""

    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stack": STACK, "stage": "r1", "design": sheet.parsed(DESIGN, "android"),
        "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": []}})
    spec = specs.load("budget", oneshot.BRANCH)
    silent = [{**s, "text": "화면 녹화 허락을 받습니다. (R2)"} if s["name"] == "알아 두실 것" else s for s in CORE_BRIEF["sections"]]
    stored = {"source": brief.source(brief.snapshot(spec)), "lines": {"R2": ["R2"], "A1": ["A1"]},
              "sections": silent, "confirm": CORE_BRIEF["confirm"], "warnings": []}
    oneshot_spec.saved("budget", oneshot={"brief": {"r1": stored}})
    gate = oneshot_spec.status("budget")["brief"]
    assert "lines" not in gate and "X1" in gate["failed"]
    with pytest.raises(HTTPException) as refused:
        oneshot_spec.decide("budget", on_sheet(decisions={}, lines={"R2": {"approve": True}, "A1": {"approve": True}},
                                               rest=True))
    assert refused.value.status_code == 409 and o()["stage"] == "r1"


# -- S4 ----------------------------------------------------------------------------------------

RUNNER = {"command": "gradlew.bat test && gradlew.bat mergeJunit -Pto={report}", "lint": "gradlew.bat lint",
          "apk": "gradlew.bat assembleDebug", "contract": ["app/src/test/SeenBoxesTest.kt"], "config": []}


@pytest.mark.parametrize("bad", [{"lint": None}, {"apk": "gradlew.bat assembleDebug; rm -rf x"},
                                 {"lint": "gradlew.bat lint -Pr={report}"}])
def test_an_android_runner_names_its_lint_and_apk_as_plain_chains(android, bad):
    (android / "app/src/test").mkdir(parents=True)
    (android / "app/src/test/SeenBoxesTest.kt").write_text("fun test_a_box_already_translated_is_skipped() {}")
    good = oneshot_build.declared(android, fenced("oneshot-runner", RUNNER), "android")
    assert good["after"] == ["gradlew.bat lint", "gradlew.bat assembleDebug"]
    assert oneshot_build.declared(android, fenced("oneshot-runner", {"command": RUNNER["command"],
                                                                     "contract": RUNNER["contract"]}))["after"] == []
    with pytest.raises(ValueError, match="lint|apk"):
        oneshot_build.declared(android, fenced("oneshot-runner", {**RUNNER, **bad}), "android")


def test_the_contract_runs_and_the_gate_get_the_toolchain_and_the_turns_are_told_it(android):
    env = {"JAVA_HOME": "C:/app/jdk-17", "ANDROID_HOME": "C:/app/sdk", "ANDROID_USER_HOME": "C:/app/user",
           "GRADLE_USER_HOME": "C:/app/gradle", "PATH": "C:/app/jdk-17/bin"}
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(DESIGN, "android"), "tests_head": "abc",
                                          "gate": "gradlew.bat test && gradlew.bat lint", "contract": ["a.kt"]})
    spec = specs.load("budget", oneshot.BRANCH)
    seen = []

    def gate(cmd, cwd, halt, env=None, timeout=None, **_):
        seen.append(env)
        return 0, "", "cut"

    with patch.object(oneshot_build.oneshot_android, "build_env", lambda: env), patch.object(specs, "gate", gate):
        with pytest.raises(ValueError, match="끊겼다"):
            oneshot_build.junit(android, type("Run", (), {"halt": None})(), spec, "gradlew.bat test {report}")
        assert seen == [env] and oneshot_build.toolchain(spec) is env
        for text in (oneshot_build.tests_text(spec), oneshot_build.build_text(spec)):
            assert "- `JAVA_HOME=C:/app/jdk-17`" in text and "- `GRADLE_USER_HOME=C:/app/gradle`" in text
        assert "docs/design/core.json" in oneshot_build.tests_text(spec) and "debug APK" in oneshot_build.build_text(spec)
    webapp = {**spec, "oneshot": {**spec["oneshot"], "intent": {"kind": "webapp"}}}
    assert oneshot_build.toolchain(webapp) is None and "{toolchain}" not in oneshot_build.build_text(webapp)


# -- After the review ------------------------------------------------------------------------

def test_an_approved_android_core_goes_on_to_the_split(android):
    oneshot_spec.saved("budget", oneshot={"stage": "review", "working": "review", "built_head": "abc"})
    store, spec = oneshot_spec.store("budget"), specs.load("budget", oneshot.BRANCH)
    started = []
    with patch.object(oneshot_spec, "handoff", lambda *a, **k: started.append(a)):
        following = oneshot_review.decided("budget", store, spec, {"verdict": "approve", "head": "abc", "findings": []})
        assert following() and started == [("budget", "split")]
    assert o()["stage"] == "split" and o()["working"] == "split" and not o().get("stopped")


def test_every_android_prompt_exists_and_says_android():
    for name in ("spec", "design", "tests", "build", "review", "split", "frontend"):
        assert "Android" in oneshot.prompt(name, "android") and "Android" not in oneshot.prompt(name)
