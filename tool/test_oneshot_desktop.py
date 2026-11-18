"""oneshot step 9e1: a PC app (`desktop`) through S2 — its six stack parts, the `build` part's declared `tools`
confirmed with it, and the stop before its core design, held at every hand-off, on `[재개]` and in recovery.

See `docs/plans/oneshot/9-kinds.md` ("How 9e builds `desktop`"). Model turns (`oneshot.ask`) are stand-ins; Git
runs in a temporary worktree.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_profile, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import GLOSSARY, REQS
from test_oneshot_spec import fenced, o, world  # noqa: F401 — `world` is the temporary worktree's fixture

TOOLS = [{"name": "cargo", "setup": "preinstalled"},
         {"name": "npm", "setup": {"uses": "actions/setup-node@v4", "with": {"node-version": "22"}}}]
STACK = [{"part": p, "choice": c, "why": "주인이 골랐다", "status": "confirmed"} for p, c in (
    ("language", "Rust 1.98, TypeScript 5"), ("ui", "Tauri 2 웹뷰"), ("core", "Rust 크레이트"), ("data_store", "SQLite"),
    ("tests", "cargo test, JUnit 보고서"), ("build", "cargo tauri build, NSIS 설치 파일"))]
STACK[-1]["tools"] = TOOLS


@pytest.fixture
def desktop(world):  # noqa: F811 — the imported fixture, by its name
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "만화 화면 번역기를 만든다.", "kind": "desktop"},
                                          "profile": {"auth": "none", "visibility": "private", "docs_language": "ko",
                                                      "window": "1280x800", "ci": "none"}})
    return world


def confirmed(stack, talk_stack=None) -> None:
    """The owner answered the bundle question that offered `talk_stack` (the stack, assumed)."""

    offered = [{**s, "status": "assumed"} for s in (talk_stack or stack)]
    oneshot_spec.saved("budget", oneshot={"stack": offered, "talk": [
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
                  {"run": "x", "uses": "a/b@v1"}, {"uses": "a/b@v1", "with": "x"}):
        with pytest.raises(ValueError, match="`setup`"):
            oneshot_profile.stacked([{**STACK[-1], "tools": [{"name": "cargo", "setup": setup}]}], "desktop", ci=True)
    for setup in ("preinstalled", {"run": "winget install Rustlang.Rustup"}, {"uses": "dtolnay/rust-toolchain@stable"}):
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


def test_a_changed_tools_list_under_the_same_choice_is_confirmed_again(desktop):
    confirmed(STACK)
    oneshot_spec.saved("budget", oneshot={"stack": STACK})   # the owner confirmed this stack before
    changed = [*STACK[:-1], {**STACK[-1], "tools": [*TOOLS, {"name": "dotnet", "setup": "preinstalled"}]}]
    oneshot_spec.saved("budget", oneshot={"talk": [*o()["talk"], {"role": "agent", "text": "도구를 더했다.",
                                                                   "choices": None, "ts": 3},
                                                    {"role": "owner", "text": "좋다", "ts": 4}]})
    assert oneshot_spec.unanswered(o(), oneshot_profile.stacked(changed, "desktop")) == ["build"]
    assert oneshot_spec.unanswered(o(), oneshot_profile.stacked(STACK, "desktop")) == []


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


def test_a_pc_app_has_its_own_s2_prompt_and_no_web_fallback_for_later_stages():
    text = oneshot.prompt("spec", "desktop")
    assert "Windows program" in text and "`tools`" in text and "`window`" in text and "web app" not in text
    assert "`desktop.build` also carries `tools`" in oneshot.prompt("spec-kinds")
    assert "`window`" in (oneshot.ROOT / "tool/prompts/oneshot-profile.md").read_text("utf-8")
    with pytest.raises(FileNotFoundError):
        oneshot.prompt("design", "desktop")
