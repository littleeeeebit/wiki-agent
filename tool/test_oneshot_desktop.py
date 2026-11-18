"""oneshot step 9e1: a PC app (`desktop`) through S2 — its six stack parts, the `build` part's declared `tools`
confirmed with it, and the stop before its core design, held at every hand-off, on `[재개]` and in recovery.

See `docs/plans/oneshot/9-kinds.md` ("How 9e builds `desktop`"). Model turns (`oneshot.ask`) are stand-ins; Git
runs in a temporary worktree.
"""

import sys
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_front, oneshot_profile, oneshot_spec, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import GLOSSARY, REQS
from test_oneshot_spec import fenced, o, world  # noqa: F401 — `world` is the temporary worktree's fixture

TOOLS = [{"name": "cargo", "setup": "preinstalled"},
         {"name": "npm", "setup": {"uses": "actions/setup-node@v4", "with": {"node-version": "22"}}}]
STACK = [{"part": p, "choice": c, "why": "주인이 골랐다", "status": "confirmed"} for p, c in (
    ("language", "Rust 1.98, TypeScript 5"), ("ui", "Tauri 2 웹뷰"), ("core", "Rust 크레이트"), ("data_store", "SQLite"),
    ("tests", "cargo test, JUnit 보고서"), ("build", "cargo tauri build, NSIS 설치 파일"))]
STACK[-1]["tools"] = TOOLS
START = oneshot_spec.start   # the real one: the `world` fixture replaces it with a stand-in


@pytest.fixture
def desktop(world):  # noqa: F811 — the imported fixture, by its name
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "만화 화면 번역기를 만든다.", "kind": "desktop"},
                                          "profile": {"auth": "none", "visibility": "private", "docs_language": "ko",
                                                      "window": "1280x800", "ci": "none"}})
    return world


def confirmed(stack, build=True) -> None:
    """The owner answered the bundle question that offered `stack` (assumed), and with `build` pressed the build
    part's own button on it (`confirm_build`)."""

    offered = [{**s, "status": "assumed"} for s in stack]
    pressed = {"choice": stack[-1]["choice"], "tools": stack[-1]["tools"]} if build else None
    oneshot_spec.saved("budget", oneshot={"stack": offered, "confirmed_build": pressed, "talk": [
        {"role": "agent", "text": "묶음을 고르세요.", "choices": {"questions": [{"question": "어느 묶음?", "options": [
            {"label": "Tauri 2"}, {"label": ".NET WinUI 3"}]}]}, "ts": 1},
        {"role": "owner", "text": "Tauri 2", "ts": 2}]})


def turn(stack) -> str:
    return "정했다.\n\n" + fenced("oneshot-spec", {"stack": stack, "requirements": REQS, "glossary": GLOSSARY})


# -- the declared tools -------------------------------------------------------------------

def test_a_pc_app_takes_androids_six_parts_and_its_build_declares_its_tools():
    assert oneshot_profile.parts("desktop") == oneshot_profile.ANDROID_PARTS
    assert oneshot_profile.stacked(STACK, "desktop")[-1]["tools"] == TOOLS
    with pytest.raises(ValueError, match="`tools` 목록"):
        oneshot_profile.stacked([{**STACK[-1], "tools": []}], "desktop")
    with pytest.raises(ValueError, match="`tools` 목록"):
        oneshot_profile.stacked([{k: v for k, v in STACK[-1].items() if k != "tools"}], "desktop")
    with pytest.raises(ValueError, match="`build` 에만"):
        oneshot_profile.stacked([{**STACK[1], "tools": TOOLS}], "desktop")
    with pytest.raises(ValueError, match="`build` 에만"):   # an Android app's build has none
        oneshot_profile.stacked([{**STACK[-1], "choice": "Gradle"}], "android")
    for name in ("C:/tools/cargo.exe", "../npm", "", " ", "a b"):
        with pytest.raises(ValueError, match="경로가 아니다"):
            oneshot_profile.stacked([{**STACK[-1], "tools": [{"name": name}]}], "desktop")
    with pytest.raises(ValueError, match="둘 이상"):
        oneshot_profile.stacked([{**STACK[-1], "tools": [{"name": "npm"}, {"name": "NPM"}]}], "desktop")


def test_each_tools_setup_is_required_when_ci_is_on_and_must_be_pinned():
    bare = [{**STACK[-1], "tools": [{"name": "cargo"}]}]
    assert oneshot_profile.stacked(bare, "desktop")[0]["tools"] == [{"name": "cargo"}]   # CI off: no runner to set up
    with pytest.raises(ValueError, match="CI 를 쓰는 카드"):
        oneshot_profile.stacked(bare, "desktop", ci=True)
    for setup in ({"uses": "actions/setup-node"}, {"uses": "setup-node@v4"}, {"run": " "}, "latest",
                  {"uses": "owner//@v1"}, {"uses": "owner/repo//sub@v1"}, {"uses": "owner/repo/@v1"},
                  {"uses": "a/b@v1", "with": {"flag": True}}, {"uses": "a/b@v1", "with": {"node-version": 22}},
                  {"uses": "a/b@v1", "with": {"matrix": {"os": "windows"}}},   # inputs are text, shown as stored
                  {"run": "x", "uses": "a/b@v1"}, {"uses": "a/b@v1", "with": "x"}):
        with pytest.raises(ValueError, match="`setup`"):
            oneshot_profile.stacked([{**STACK[-1], "tools": [{"name": "cargo", "setup": setup}]}], "desktop", ci=True)
    for setup in ("preinstalled", {"run": "winget install Rustlang.Rustup"}, {"uses": "dtolnay/rust-toolchain@stable"},
                  {"uses": "github/codeql-action/init@v3"}):
        assert oneshot_profile.stacked([{**STACK[-1], "tools": [{"name": "cargo", "setup": setup}]}], "desktop",
                                       ci=True)[0]["tools"][0]["setup"] == setup


def test_among_several_kinds_the_tools_ride_on_desktop_build_alone():
    kinds = ["android", "desktop"]
    build = {"part": "desktop.build", "choice": "cargo tauri build", "why": "주인이 골랐다", "status": "confirmed"}
    assert oneshot_profile.stacked([{**build, "tools": TOOLS}], kinds)[0]["tools"] == TOOLS
    with pytest.raises(ValueError, match="`tools` 목록"):
        oneshot_profile.stacked([build], kinds)
    with pytest.raises(ValueError, match="`build` 에만"):
        oneshot_profile.stacked([{**build, "part": "android.build", "tools": TOOLS}], kinds)


# -- S2 and its stop ----------------------------------------------------------------------

def test_the_pc_app_conversation_is_told_its_tools_and_stops_before_the_core_design(desktop):
    confirmed(STACK)
    told = []
    with patch.object(oneshot, "ask", lambda store, role, system, text, *a, **k: told.append(system) or (
            turn(STACK), {"cost_usd": 0.1}, False)), \
            patch.object(oneshot_spec, "start", lambda *a, **k: pytest.fail("a held run starts nothing")):
        oneshot_spec.talk("budget")
    assert "Windows program" in told[0] and "`tools`" in told[0] and "`capability`" in told[0]
    run = o()
    assert (run["stage"], run["stopped"]["reason"], run["stopped"]["state"]) == ("design", "kind", "design")
    assert run["stack"][-1]["tools"] == TOOLS and not run.get("working")
    with pytest.raises(HTTPException, match="다음 버전"):
        oneshot_spec.resume("budget")
    oneshot_spec.recover()   # recovery keeps the stop: nothing starts the design
    assert o()["stopped"]["reason"] == "kind"


def asked(question: str, answer: str) -> None:
    """The last two talk turns: a real `choices` question and the owner's answer to it."""

    oneshot_spec.saved("budget", oneshot={"talk": [*o()["talk"], {"role": "agent", "text": question, "ts": 3, "choices": {
        "questions": [{"question": question, "options": [{"label": "예"}, {"label": "아니오"}]}]}},
        {"role": "owner", "text": answer, "ts": 4}]})


def test_an_unrelated_answer_cannot_confirm_a_changed_tools_list(desktop):
    confirmed(STACK)
    oneshot_spec.saved("budget", oneshot={"stack": STACK})   # the owner confirmed this stack before
    asked("번역 창을 늘 맨 위에 둘까요?", "예")
    spec = specs.load("budget", oneshot.BRANCH)
    for tools in ([*TOOLS, {"name": "dotnet", "setup": "preinstalled"}],   # a tool added, a setup changed
                  [TOOLS[0], {"name": "npm", "setup": {"run": "winget install OpenJS.NodeJS"}}]):
        with pytest.raises(ValueError, match="build"):
            oneshot_spec.spec_turn(turn([*STACK[:-1], {**STACK[-1], "tools": tools}]), spec)
    oneshot_spec.spec_turn(turn(STACK), spec)   # the list the owner confirmed stands


def test_only_the_owners_button_confirms_the_build_exactly_as_offered(desktop):
    confirmed(STACK, build=False)   # the bundle was answered; its build part is offered, assumed
    for answer in ("아니오", "예"):   # no answer to any question confirms it, a refusal least of all
        asked("빌드와 도구를 이대로 확정할까요?", answer)
        with pytest.raises(ValueError, match="build"):
            oneshot_spec.spec_turn(turn(STACK), specs.load("budget", oneshot.BRANCH))
    node22 = [TOOLS[0], {**TOOLS[1], "setup": {**TOOLS[1]["setup"], "with": {"node-version": "20"}}}]
    typed = [TOOLS[0], {**TOOLS[1], "setup": {**TOOLS[1]["setup"], "with": {"node-version": 22}}}]   # 22, not "22"
    for part, choice, tools in (("build", STACK[-1]["choice"], node22), ("build", STACK[-1]["choice"], typed),
                                ("build", "dotnet publish", TOOLS), ("ui", STACK[1]["choice"], TOOLS)):
        with pytest.raises(HTTPException, match="지금 제시된 것과 다르다"):   # not what the stack shows
            oneshot_spec.confirm_build("budget", oneshot_spec.Build(part=part, choice=choice, tools=tools))
    assert not o().get("confirmed_build")
    # The turn after the bundle answer confirmed the other parts and left the build part for its button.
    oneshot_spec.saved("budget", oneshot={"stack": [*STACK[:-1], {**STACK[-1], "status": "assumed"}], "talk": [
        *o()["talk"], {"role": "agent", "text": "빌드와 도구는 옆의 버튼으로 확정한다.", "choices": None, "ts": 5}]})
    oneshot_spec.confirm_build("budget", oneshot_spec.Build(part="build", choice=STACK[-1]["choice"], tools=TOOLS))
    assert o()["confirmed_build"] == {"choice": STACK[-1]["choice"], "tools": TOOLS} and o()["working"] == "talk"
    assert o()["talk"][-1] == {**o()["talk"][-1], "role": "owner"} and "cargo, npm" in o()["talk"][-1]["text"]
    oneshot_spec.saved("budget", oneshot={"working": None})
    spec = specs.load("budget", oneshot.BRANCH)
    oneshot_spec.spec_turn(turn(STACK), spec)
    with pytest.raises(ValueError, match="build"):   # an action's inputs are part of what was confirmed
        oneshot_spec.spec_turn(turn([*STACK[:-1], {**STACK[-1], "tools": node22}]), spec)
    with pytest.raises(ValueError, match="`setup`"):   # and keep their type: a number is no input at all
        oneshot_spec.spec_turn(turn([*STACK[:-1], {**STACK[-1], "tools": typed}]), spec)
    assert not oneshot_spec.same(True, 1) and oneshot_spec.same({"a": "1", "b": ["x"]}, {"b": ["x"], "a": "1"})


def test_a_replaced_build_offer_drops_its_earlier_approval(desktop):
    confirmed(STACK)   # the owner pressed the button on list A
    other = {**STACK[-1], "tools": [TOOLS[0], {"name": "npm", "setup": {"run": "winget install  OpenJS.NodeJS"}}]}
    with patch.object(oneshot, "ask", lambda *a, **k: (turn([*STACK[:-1], {**other, "status": "assumed"}]), {}, False)):
        oneshot_spec.talk("budget")   # the owner asked for another setup: list B is offered
    assert o()["stack"][-1]["tools"] == other["tools"] and o()["confirmed_build"] is None
    with pytest.raises(ValueError, match="build"):   # going back to A needs A offered and pressed again
        oneshot_spec.spec_turn(turn(STACK), specs.load("budget", oneshot.BRANCH))
    with patch.object(oneshot, "ask", lambda *a, **k: (turn([*STACK[:-1], {**other, "status": "assumed"}]), {}, False)):
        oneshot_spec.talk("budget")   # the same offer again keeps whatever was pressed on it
    oneshot_spec.saved("budget", oneshot={"confirmed_build": {"choice": other["choice"], "tools": other["tools"]}})
    with patch.object(oneshot, "ask", lambda *a, **k: (turn([*STACK[:-1], {**other, "status": "assumed"}]), {}, False)):
        oneshot_spec.talk("budget")
    assert o()["confirmed_build"] == {"choice": other["choice"], "tools": other["tools"]}


def test_the_build_button_is_never_an_answer_to_a_waiting_question(desktop):
    confirmed(STACK, build=False)
    oneshot_spec.saved("budget", oneshot={"talk": [*o()["talk"], {"role": "agent", "text": "묶음을 고르세요.", "ts": 3,
                                                                   "choices": {"questions": [{"question": "어느 묶음?", "options": [
                                                                       {"label": "Tauri 2"}, {"label": ".NET WinUI 3"}]}]}}]})
    before = o()
    with pytest.raises(HTTPException, match="아직 답하지 않은 질문"):
        oneshot_spec.confirm_build("budget", oneshot_spec.Build(part="build", choice=STACK[-1]["choice"], tools=TOOLS))
    assert o()["talk"] == before["talk"] and not o().get("confirmed_build") and not o().get("working")
    # Had the press counted as the answer, the other offered parts would now pass as confirmed: they stay unasked.
    assert oneshot_spec.unanswered(o(), STACK) == [p for p in oneshot_profile.ANDROID_PARTS]


def test_a_saved_pc_app_spec_takes_no_turn_on_another_host(desktop):
    confirmed(STACK, build=False)
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "restart", "detail": "서버가 다시 켜졌다", "state": "spec"}})
    with patch.object(sys, "platform", "linux"), \
            patch.object(oneshot, "start_worker", lambda *a, **k: pytest.fail("no turn runs off Windows")), \
            patch.object(oneshot_spec, "start", START):
        for call in (lambda: oneshot_spec.resume("budget"), lambda: oneshot_spec.say("budget", oneshot_spec.Say(text="예")),
                     lambda: oneshot_spec.confirm_build("budget", oneshot_spec.Build(
                         part="build", choice=STACK[-1]["choice"], tools=TOOLS))):
            with pytest.raises(HTTPException, match="Windows 에서만"):
                call()


def test_the_shared_start_refuses_a_move_into_the_held_stage(desktop):
    assert o()["stage"] == "spec"   # the last stage a PC app has in this version
    with patch.object(oneshot, "start_worker", lambda *a, **k: pytest.fail("a held run starts nothing")), \
            pytest.raises(HTTPException, match="다음 버전"):
        START("budget", "design", stage="design")
    assert o()["stage"] == "spec" and not o().get("stopped")


def test_a_resume_or_a_shutdown_between_the_save_and_the_stop_never_starts_the_design(desktop):
    # The stop was lost (a restart that replaced it): the run is still held, never resumable into the design.
    oneshot_spec.saved("budget", oneshot={"stage": "design", "working": None,
                                          "stopped": {"reason": "restart", "detail": "서버가 다시 켜졌다", "state": "design"}})
    with patch.object(oneshot_spec, "start", lambda *a, **k: pytest.fail("a held run starts nothing")), \
            pytest.raises(HTTPException, match="다음 버전"):
        oneshot_spec.resume("budget")
    assert o()["stopped"]["reason"] == "kind"


def test_several_kinds_an_earlier_version_held_at_the_screens_stay_held(desktop):
    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": "android", "kinds": ["android", "desktop"]},
                                          "stage": "frontend", "stopped": {"reason": "kind", "detail": "다음 버전",
                                                                           "state": "frontend"}})
    assert oneshot_profile.waits_at(o()) == "design" and oneshot_profile.held(o())
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "kind"
    with pytest.raises(HTTPException, match="다음 버전"):
        oneshot_spec.resume("budget")


@pytest.mark.parametrize("stage", ["board", "frontend", "tests"])
def test_a_held_mixed_run_whose_stop_was_lost_starts_no_later_stage(desktop, stage):
    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": "android", "kinds": ["android", "desktop"]},
                                          "stage": stage, "working": None, "stopped": None, "features": []})
    with patch.object(oneshot, "start_worker", lambda *a, **k: pytest.fail("a held run starts nothing")), \
            patch.object(oneshot_spec, "start", START):
        with pytest.raises(HTTPException, match="다음 버전"):   # the board's endpoint, past `idle`
            oneshot_front.submit_board("budget", oneshot_front.Board(frequency={}, first_screen="한 줄"))
        with pytest.raises(HTTPException, match="다음 버전"):   # the shared start every stage goes through
            START("budget", "frontend", stage="frontend")
    assert o()["stopped"]["reason"] == "kind" and o()["stage"] == stage


def test_a_pc_app_has_its_own_s2_prompt_and_no_web_fallback_for_later_stages():
    text = oneshot.prompt("spec", "desktop")
    assert "Windows program" in text and "`tools`" in text and "`window`" in text and "web app" not in text
    assert "`desktop.build` also carries `tools`" in oneshot.prompt("spec-kinds")
    assert "`window`" in (oneshot.ROOT / "tool/prompts/oneshot-profile.md").read_text("utf-8")
    with pytest.raises(FileNotFoundError):
        oneshot.prompt("design", "desktop")
