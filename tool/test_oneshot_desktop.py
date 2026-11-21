"""oneshot steps 9e1 and 9e2: a PC app (`desktop`) through S2 — its six stack parts, the `build` part's declared
`tools` confirmed with it — then its core design and R1, S4 with only its declared tools, the backend review with
its `look` and `owner` deferrals and the split; and the stop this version holds a PC app at, held at every
hand-off, on `[재개]` and in recovery. S6 and R2 are in `test_oneshot_desktop_front.py`.

See `docs/plans/oneshot/9-kinds.md` ("How 9e builds `desktop`"). Model turns (`oneshot.ask`) are stand-ins; Git
runs in a temporary worktree.
"""

import os
import re
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_build, oneshot_front, oneshot_profile, oneshot_review, oneshot_sheet as sheet, \
    oneshot_spec, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import DESIGN, GLOSSARY, REQS, answer as designed
from test_oneshot_spec import fenced, o, on_sheet, world  # noqa: F401 — `world` is the temporary worktree's fixture

TOOLS = [{"name": "cargo", "setup": "preinstalled"},
         {"name": "npm", "setup": {"uses": "actions/setup-node@v4", "with": {"node-version": "22"}}}]
STACK = [{"part": p, "choice": c, "why": "주인이 골랐다", "status": "confirmed"} for p, c in (
    ("language", "Rust 1.98, TypeScript 5"), ("ui", "Tauri 2 웹뷰"), ("core", "Rust 크레이트"), ("data_store", "SQLite"),
    ("tests", "cargo test, JUnit 보고서"), ("build", "cargo tauri build, NSIS 설치 파일"))]
STACK[-1]["tools"] = TOOLS
START = oneshot_spec.start   # the real one: the `world` fixture replaces it with a stand-in
BEGIN = oneshot_build.begin   # likewise
SEND = oneshot_build.send


def git_head(tree) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True).stdout.strip()


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

def test_the_pc_app_conversation_is_told_its_tools_and_hands_off_to_its_core_design(desktop):
    confirmed(STACK)
    told, started = [], []
    with patch.object(oneshot, "ask", lambda store, role, system, text, *a, **k: told.append(system) or (
            turn(STACK), {"cost_usd": 0.1}, False)), \
            patch.object(oneshot_spec, "start", lambda *a, **k: started.append((a, k))):
        oneshot_spec.talk("budget")
    assert "Windows program" in told[0] and "`tools`" in told[0] and "`capability`" in told[0]
    assert started == [(("budget", "design"), {"stage": "design"})]
    assert o()["stack"][-1]["tools"] == TOOLS and not o().get("stopped")


# -- S3 and R1: the core, then the stop before S4 ----------------------------------------------

def test_a_pc_apps_core_design_is_a_module_sheet_that_speaks_of_the_pc(desktop):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY, "stack": STACK, "stage": "design"})
    told = []
    with patch.object(oneshot, "ask", lambda store, role, system, text, *a, **k: told.append(system) or (
            designed(), {"cost_usd": 0.1}, False)):
        oneshot_spec.design("budget")
    assert "core of a new Windows program" in told[0] and "Android" not in told[0] and o()["stage"] == "r1"
    assert set(o()["design"]) == {"modules", "outside", "assumptions", "tests"}   # a core, never a web backend
    text = sheet.sheet(specs.load("budget", oneshot.BRANCH))
    assert text.startswith("# 핵심 설계 — budget") and "| M2 | SeenBoxes |" in text
    assert "- PC에 저장하는 것: SeenBoxes: 번역한 상자 해시와 번역문" in text and "## PC 밖으로 나가는 호출" in text
    assert "폰" not in text
    with pytest.raises(ValueError, match="`oneshot-modules` 블록"):   # a web app's blocks are not a PC app's core
        oneshot_spec.design_turn(fenced("oneshot-api", []), 1, specs.load("budget", oneshot.BRANCH))


def test_a_pc_app_the_last_version_stopped_before_its_design_is_taken_on(desktop):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY, "stack": STACK, "stage": "design",
                                                             "stopped": {"reason": "kind", "state": "design",
                                                                         "detail": "다음 버전에서 이어진다"}})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    started = []
    with patch.object(oneshot_spec, "start", lambda repo, *a, **k: started.append(a)):
        oneshot_spec.resume("budget")
    assert started == [("design",)]


@pytest.mark.parametrize("kinds", [["desktop"], ["android", "desktop"]])
def test_an_approved_pc_app_core_is_written_and_goes_on_to_s4(desktop, kinds):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "intent": {**o()["intent"], "kind": kinds[0], "kinds": kinds}, "glossary": GLOSSARY, "stack": STACK,
        "stage": "r1", "design": sheet.parsed(DESIGN, kinds), "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": []}})
    items = sheet.decidable(o()["design"], REQS)
    oneshot_spec.decide("budget", on_sheet(decisions={i: {"approve": True} for i in items}))
    assert (o()["stage"], o()["working"], o().get("stopped")) == ("tests", "tests", None) and o()["design_head"]
    assert (desktop / "docs/design/core.md").read_text(encoding="utf-8").startswith("# 핵심 설계")


@pytest.mark.parametrize("kinds", [["desktop"], ["android", "desktop"]])
def test_a_built_pc_app_core_is_reviewed(desktop, kinds):
    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": kinds[0], "kinds": kinds},
                                          "stage": "review", "working": "review", "built_head": "abc"})
    started = []
    with patch.object(oneshot, "start_worker", lambda store, work, *a, **k: started.append(work)):
        oneshot_build.reviewed("budget")   # what follows the build's pass
    assert started == ["review"] and not o().get("stopped")


# -- 9e2c: the backend review's `desktop` deferral, and the split ------------------------------

LOOK = {"location": "core/src/capture.rs:18", "severity": "P1", "reason": "번역 켜기가 꺼진 채 남는다", "evidence": "x",
        "area": "toggle", "device": {"kind": "desktop", "requirement": "R1", "check": "`translate` 토글이 켜진다",
                                     "source": "look", "act": {"action": "toggle", "id": "translate"},
                                     "expect": {"id": "translate", "toggle": "On"}}}
OWNER = {**LOOK, "device": {**{k: v for k, v in LOOK["device"].items() if k not in ("act", "expect")},
                            "check": "고정 창을 캡처한 결과가 `{data}/seen.txt` 에 그 글을 담는다", "source": "owner",
                            "act": {"action": "invoke", "id": "capture"}, "observe": {"report": {"file": "seen.txt"}}}}


def test_a_pc_apps_deferral_names_where_its_check_reads_the_evidence(desktop):
    devices = oneshot_review.deferrable({"oneshot": o(), "requirements": REQS})
    assert devices == {"desktop": {"R1", "R2"}}
    approve = lambda *f: {"verdict": "approve", "head": "h", "findings": list(f)}   # noqa: E731
    found = oneshot_review.verdict_of(approve(LOOK, OWNER), "h", devices)
    assert [f["device"]["source"] for f in found["findings"]] == ["look", "owner"]
    assert [x["source"] for x in oneshot_review.owing(found)] == ["look", "owner"]
    assert [x.get("expect") for x in oneshot_review.owing(found)] == [{"id": "translate", "toggle": "On"}, None]
    for bad in ({"source": None}, {"source": "screen"}, {"source": "Look"}):   # neither source: no deferral
        with pytest.raises(ValueError, match="`source`"):
            oneshot_review.verdict_of(approve({**LOOK, "device": {**LOOK["device"], **bad}}), "h", devices)
    for bad in ({"expect": None}, {"expect": {"id": "translate"}}, {"expect": {"name": "번역"}}, {"act": None},
                {"act": {"action": "click", "id": "translate"}}, {"act": {"action": "set_value", "id": "translate"}}):
        with pytest.raises(ValueError, match="`expect`"):   # a `look` check names its step and what it brings about
            oneshot_review.verdict_of(approve({**LOOK, "device": {**LOOK["device"], **bad}}), "h", devices)
    for bad in ({"expect": LOOK["device"]["expect"]}, {"act": None}, {"observe": None}, {"observe": {}},
                {"observe": {"report": {"file": "../seen.txt"}}}, {"observe": {"report": {"id": "seen", "in": "other"}}},
                {"observe": {"window": {"shows": " "}}}, {"observe": {"composition": {"shows": "글", "in": "owned"}}},
                {"observe": {"report": {"id": "seen"}, "window": {"shows": "글"}}}):
        with pytest.raises(ValueError, match="`observe`"):   # an owner's names its step and what it observes
            oneshot_review.verdict_of(approve({**OWNER, "device": {**OWNER["device"], **bad}}), "h", devices)
    with pytest.raises(ValueError, match="`source`"):   # a `look` one observes nothing
        oneshot_review.verdict_of(approve({**LOOK, "device": {**LOOK["device"], "observe": OWNER["device"]["observe"]}}),
                                  "h", devices)
    for fine in ({"report": {"id": "seen", "in": "owned"}}, {"window": {"shows": "고정 창의 글을 번역한 문장"}},
                 {"composition": {"shows": "고정 창 위에 번역문이 겹쳐 보인다"}}):
        assert oneshot_review.verdict_of(approve({**OWNER, "device": {**OWNER["device"], "observe": fine}}), "h", devices)
    assert oneshot_review.owing(found)[1]["observe"] == {"report": {"file": "seen.txt"}}
    assert oneshot_review.owing(found)[0]["act"] == {"action": "toggle", "id": "translate"}
    gone = {**LOOK, "device": {k: v for k, v in LOOK["device"].items() if k not in ("source", "act", "expect")}}
    with pytest.raises(ValueError, match="`source`"):
        oneshot_review.verdict_of(approve(gone), "h", devices)
    phone = {"android": {"R1"}}   # an Android deferral names none
    with pytest.raises(ValueError, match="`source`"):
        oneshot_review.verdict_of(approve({**LOOK, "device": {**LOOK["device"], "kind": "android"}}), "h", phone)
    assert oneshot_review.verdict_of(approve({**gone, "device": {**gone["device"], "kind": "android"}}), "h", phone)


@pytest.mark.parametrize("kinds", [["desktop"], ["android", "desktop"]])
def test_a_pc_apps_split_reads_its_modules_and_shows_the_board(desktop, kinds):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "intent": {**o()["intent"], "kind": kinds[0], "kinds": kinds, "first_screen": "번역을 켠다"},
        "stage": "split", "working": "split", "design": sheet.parsed(DESIGN, kinds)})
    spec = specs.load("budget", oneshot.BRANCH)
    ops = oneshot_front.inventory(spec)
    assert {"M1", "R2"} <= set(ops)
    value = [{"id": f"f{n}", "name": "기능", "parts": [p], "class": "every", "why": "늘"} for n, p in enumerate(ops)]
    asked, saves, save = [], [], oneshot_spec.saved   # each turn's instructions; the split's answer is `value`
    with patch.object(oneshot, "turned", lambda store, role, system, *a: asked.append(system) or a[-2](value)), \
            patch.object(oneshot_spec, "saved", lambda repo, **f: saves.append(f.get("oneshot")) or save(repo, **f)):
        oneshot_front.split_turn("budget", oneshot_spec.store("budget"), spec)()
    # The board is the owner's, and S6 follows it since 9e3a: no stop rides on its save.
    assert len(saves) == 1 and saves[0]["stage"] == "board" and "stopped" not in saves[0]
    assert asked == [oneshot.prompt("split", oneshot_profile.shape(o()))]
    assert o()["stage"] == "board" and not o().get("stopped") and not oneshot_profile.held(o())
    assert [f["parts"] for f in o()["features"]] == [[p] for p in ops]


# -- 9e2b: the declared tools, found once per stage, and nothing else run -----------------------

@contextmanager
def turns(found: dict[str, str | None]):
    """A real `send` whose session is a stand-in: `which` finds each tool at `found`'s path, or not at all;
    each turn that would go out is recorded with its text, and nothing installs anything."""

    sent = []

    def begin(path, chat, text, release, turn):
        sent.append(text)
        release()
        turn.ended.set()

    chat = lambda *a: SimpleNamespace(id="c", parent_id=None, repo="", settled=None)   # noqa: E731
    with patch.object(oneshot_build.shutil, "which", lambda name: found.get(name)), \
            patch.object(oneshot_build.work, "notice", lambda *a: None), \
            patch.object(oneshot_build.work, "begin", begin), patch.object(oneshot_build.work, "session", chat), \
            patch.object(oneshot, "role_config", lambda *a: {"model": "m", "effort": "e"}), \
            patch.object(oneshot_build.specs, "gate", lambda *a, **k: pytest.fail("no command runs")):
        yield sent


def test_a_missing_tool_stops_the_first_stage_before_its_turn_and_installs_nothing(desktop):
    oneshot_spec.saved("budget", oneshot={"stack": STACK, "stage": "tests", "working": "tests"})
    with turns({"cargo": "C:/rust/cargo.exe"}) as sent:   # npm is not on PATH
        SEND("budget", "tests", "x")
    assert sent == [] and o()["stopped"]["reason"] == "toolchain" and "npm" in o()["stopped"]["detail"]
    assert "cargo" not in o()["stopped"]["detail"] and not o().get("working")


def test_a_tool_removed_between_two_stages_stops_the_second_before_it_runs(desktop):
    oneshot_spec.saved("budget", oneshot={"stack": STACK, "stage": "tests", "working": "tests"})
    both = {"cargo": "C:/rust/cargo.exe", "npm": "C:/node/npm.cmd"}
    with turns(both) as sent:
        SEND("budget", "tests", "x")
    assert sent == ["x"] and o()["toolpaths"] == {"stage": "tests", "paths": {k: os.path.abspath(v) for k, v in both.items()}}
    oneshot_spec.saved("budget", oneshot={"stage": "build", "working": "build"})
    with turns({"npm": "C:/node/npm.cmd"}) as sent:   # cargo was uninstalled after the tests stage
        SEND("budget", "build", "y")
    assert sent == [] and o()["stopped"]["reason"] == "toolchain" and "cargo" in o()["stopped"]["detail"]


def test_a_resume_after_a_tool_disappeared_looks_again_and_goes_on_once_it_is_back(desktop):
    oneshot_spec.saved("budget", oneshot={"stack": STACK, "stage": "build", "tests_head": "abc", "gate": "cargo test",
                                          "contract": ["tests/core.rs"], "stopped": {"reason": "toolchain", "detail": "npm"}})
    with turns({"cargo": "C:/rust/cargo.exe"}) as sent, patch.object(oneshot_build, "send", SEND):
        oneshot_spec.resume("budget")
    assert sent == [] and o()["stopped"]["reason"] == "toolchain"
    with turns({"cargo": "C:/rust/cargo.exe", "npm": "C:/node/npm.cmd"}) as sent, \
            patch.object(oneshot_build, "send", SEND):
        oneshot_spec.resume("budget")
    assert len(sent) == 1 and "- `cargo`" in sent[0] and "- `npm`" in sent[0] and not o().get("stopped")


def test_recovery_looks_for_the_tools_of_a_stage_the_server_went_down_in(desktop):
    oneshot_spec.saved("budget", oneshot={"stack": STACK, "stage": "build", "working": "build"})
    with turns({"cargo": "C:/rust/cargo.exe"}):   # npm went away while the server was down
        oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "toolchain" and "npm" in o()["stopped"]["detail"]
    oneshot_spec.saved("budget", oneshot={"working": "build", "stopped": None})
    with turns({"cargo": "C:/rust/cargo.exe", "npm": "C:/node/npm.cmd"}):   # all there: a plain restart
        oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"


def test_an_undeclared_command_goes_back_to_its_author_and_nothing_runs(desktop):
    (desktop / "tests").mkdir()
    (desktop / "tests/core.rs").write_text("fn test_a_box_already_translated_is_skipped() {}", encoding="utf-8")
    (desktop / "scripts").mkdir()
    (desktop / "scripts/junit.ps1").write_text("", encoding="utf-8")
    runner = lambda command: fenced("oneshot-runner", {"command": command, "contract": ["tests/core.rs"]})  # noqa: E731
    tools = oneshot_build.tools({"intent": o()["intent"], "stack": STACK})
    for command in ("cargo test && scripts/junit.ps1 -to {report}", "NPM.cmd test --reporter {report}",
                    "cargo.exe test {report}"):
        assert oneshot_build.declared(desktop, runner(command), "desktop", tools)["command"] == command
    for command, bad in (("winget install Rustlang.Rustup && cargo test {report}", "winget"),
                         ("cargo test && python -m junit {report}", "python"),
                         ("cargo test && ../outside.ps1 {report}", "../outside.ps1"),
                         ("cargo test && scripts/missing.ps1 {report}", "scripts/missing.ps1")):
        with pytest.raises(ValueError, match=f"도구도 작업트리 안의 파일도 아닌 프로그램을 부른다: {re.escape(bad)}"):
            oneshot_build.declared(desktop, runner(command), "desktop", tools)
    # In the stage: the refusal is the turn's, sent back to it, and no command of it ran.
    oneshot_spec.saved("budget", oneshot={"stack": STACK, "stage": "tests", "design_head": git_head(desktop)})
    with patch.object(oneshot_build.specs, "gate", lambda *a, **k: pytest.fail("no command runs")), \
            pytest.raises(ValueError, match="winget"):   # `checked` sends this reason back as the turn's refusal
        oneshot_build.tested(desktop, None, specs.load("budget", oneshot.BRANCH),
                             runner("winget install x && cargo test {report}"))


def launch(at: str) -> str:
    return ("& " if os.name == "nt" else "") + oneshot_build.literal(at)


def test_a_gate_starts_every_program_by_the_explicit_path_its_check_chose(desktop):
    (desktop / "scripts dir").mkdir()
    (desktop / "scripts dir/junit.ps1").write_text("", encoding="utf-8")
    (desktop / "npm").write_text("", encoding="utf-8")   # a root file named like a declared tool
    (desktop / "build.cmd").write_text("", encoding="utf-8")
    cargo, npm = "C:/Program Files/rust/cargo.exe", "C:/node/npm.cmd"
    o_ = {"intent": o()["intent"], "stack": STACK, "toolpaths": {"stage": "build", "paths": {"cargo": cargo, "npm": npm}}}
    ran = oneshot_build.run_as("cargo\ttest --workspace && \"scripts dir/junit.ps1\" -to 'r.xml' && CARGO.EXE fmt"
                               " && npm test && build.cmd --release", o_, desktop)
    script, build = str((desktop / "scripts dir/junit.ps1").resolve()), str((desktop / "build.cmd").resolve())
    assert ran == (f"{launch(cargo)}\ttest --workspace && {launch(script)} -to 'r.xml' && {launch(cargo)} fmt"
                   f" && {launch(npm)} test && {launch(build)} --release")   # a bare declared name is the tool
    # What the check accepted is what runs: the same words pass `declared` and nothing is left for the shell.
    tools = oneshot_build.tools(o_)
    assert oneshot_build.undeclared(desktop, "cargo\ttest && \"scripts dir/junit.ps1\" -to x && build.cmd", tools) == []
    (desktop / "build.cmd").unlink()   # moved after S4 checked it: the saved runner is read again and refused
    for gone in ("build.cmd --release", "cargo test && python -m x"):
        with pytest.raises(ValueError, match="아무것도 실행하지 않았다"):
            oneshot_build.run_as(gone, o_, desktop)
    with pytest.raises(ValueError, match="찾은 경로가 없다"):   # a declared tool with no path found for the stage
        oneshot_build.run_as("cargo test", {**o_, "toolpaths": None}, desktop)
    assert oneshot_build.run_as("gradlew.bat test", {}, desktop) == "gradlew.bat test"   # no PC app: run as written


def test_a_tool_declared_with_its_extension_is_found_and_run_by_that_name(desktop):
    cmd_tools = [{"name": "npm.cmd", "setup": "preinstalled"}]
    o_ = {"intent": o()["intent"], "stack": [{**STACK[-1], "tools": cmd_tools}]}
    assert oneshot_build.undeclared(desktop, "npm.cmd test && NPM run x && npm.CMD y", cmd_tools) == []
    oneshot_spec.saved("budget", oneshot={"stack": o_["stack"], "stage": "tests", "working": "tests"})
    asked = []
    with turns({}) as sent, patch.object(oneshot_build.shutil, "which", lambda n: asked.append(n) or "C:/node/npm.cmd"):
        SEND("budget", "tests", "x")
    npm = os.path.abspath("C:/node/npm.cmd")
    assert asked == ["npm.cmd"] and sent == ["x"] and o()["toolpaths"]["paths"] == {"npm.cmd": npm}
    assert oneshot_build.run_as("npm test", o(), desktop) == f"{launch(npm)} test"
    with pytest.raises(ValueError, match="확장자만 다른"):   # S2: `npm` and `npm.cmd` are one program
        oneshot_profile.tools_of([{"name": "npm"}, {"name": "npm.cmd"}], ci=False)


def test_a_tool_found_through_a_relative_path_entry_is_saved_where_it_was_found(desktop, tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.chdir(host)   # the server's folder, where the lookup ran; the gate runs in `desktop`
    oneshot_spec.saved("budget", oneshot={"stack": STACK, "stage": "tests"})
    with patch.object(oneshot_build.shutil, "which", lambda n: os.path.join(".", f"{n}.exe")):
        assert oneshot_build.resolve_tools("budget", "tests")
    assert o()["toolpaths"]["paths"] == {t["name"]: str(host / f"{t['name']}.exe") for t in oneshot_build.tools(o())}


@pytest.mark.skipif(os.name != "nt", reason="PowerShell's quoting of a program path")
def test_a_program_word_joined_to_a_quote_is_refused_and_a_checked_one_runs_as_itself(desktop):
    (desktop / "scripts").mkdir()
    (desktop / "scripts/checked.cmd").write_text("@echo CHECKED\r\n", encoding="utf-8")
    (desktop / "scripts/checked.cmd'").mkdir()   # what PowerShell's `''` would walk through
    (desktop.parent / "outside.cmd").write_text("@echo OUTSIDE\r\n", encoding="utf-8")
    o_ = {"intent": o()["intent"], "stack": STACK, "toolpaths": {"stage": "build", "paths": {"cargo": "c", "npm": "n"}}}
    tools = oneshot_build.tools(o_)
    for joined in ("'scripts/checked.cmd''/../../outside.cmd' -x", "\"scripts/checked.cmd\"'x' -x",
                   "scripts/checked.cmd'x' -x"):
        assert oneshot_build.undeclared(desktop, joined, tools)
        with pytest.raises(ValueError, match="아무것도 실행하지 않았다"):
            oneshot_build.run_as(joined, o_, desktop)
    ran = subprocess.run(["powershell", "-NoProfile", "-Command", oneshot_build.run_as("'scripts/checked.cmd' -x", o_, desktop)],
                         cwd=desktop, capture_output=True, text=True, timeout=60)
    assert ran.stdout.strip() == "CHECKED", ran


def test_a_quote_inside_a_quoted_program_is_its_name_and_a_quoted_and_sign_does_not_cut_the_chain(desktop):
    (desktop / "'lead.cmd").write_text("", encoding="utf-8")
    (desktop / "lead.cmd").write_text("", encoding="utf-8")   # what stripping every quote would pick instead
    assert oneshot_build.word_of("\"'lead.cmd\" -x") == ("'lead.cmd", " -x")
    o_ = {"intent": o()["intent"], "stack": STACK, "toolpaths": {"stage": "build", "paths": {"cargo": "c", "npm": "n"}}}
    lead = str((desktop / "'lead.cmd").resolve())
    assert oneshot_build.run_as("\"'lead.cmd\" -x", o_, desktop) == f"{launch(lead)} -x"
    chain = "cargo test --label \"R && D\" && npm test 'a&&b'"
    assert oneshot_build.links(chain) == ["cargo test --label \"R && D\" ", " npm test 'a&&b'"]
    assert oneshot_build.undeclared(desktop, chain, oneshot_build.tools(o_)) == []
    assert oneshot_build.run_as(chain, o_, desktop) == f"{launch('c')} test --label \"R && D\" && {launch('n')} test 'a&&b'"
    for quoted in ("cargo test --label \"Research && return to results\"", "cargo test --label 'R && && D'"):
        assert oneshot_build.undeclared(desktop, quoted, oneshot_build.tools(o_)) == []


def test_on_windows_the_chain_check_reads_powershell_quotes():
    with patch.object(oneshot_build.os, "name", "nt"):
        # The chain check reads the parts `links` cuts: a quoted `&& return` or `&& &&` is one argument.
        for quoted in ("cargo test --label \"Research && return to results\"", "cargo test --label 'R && && D'"):
            assert oneshot_build.chained(quoted)
        assert not oneshot_build.chained("cargo test && return")
        # A typographic quote PowerShell could read as a delimiter is refused: outside ASCII quotes, or inside a
        # string of its own family, which it closes (`'a’ && b` is two commands to PowerShell).
        for curly in ("cargo test --label ‘R && D’", "cargo test --label “R”", "echo 'a’ && echo b",
                      "echo \"a” && return \"", "echo @'\nx’\n'@"):
            assert not oneshot_build.chained(curly), curly
        # After `--%` PowerShell reads an apostrophe as text, so the masker's string would hide `&& return`.
        assert not oneshot_build.chained("git log --% --grep=it's && return '")
        # Nesting deeper than the masker reads is a refusal, never an error that stops the run.
        assert not oneshot_build.chained("echo " + "[" * 3000 + "]" * 3000)
        # Inside a string of the other family it is only a letter.
        assert oneshot_build.chained("npm test -- \"Editor’s preview\"")
        assert oneshot_build.chained("npm test -- 'say “hi”'")


def test_on_a_posix_shell_the_chain_check_cuts_at_every_and_sign():
    with patch.object(oneshot_build.os, "name", "posix"):
        # Bash reads `'it'\''s'` as one word; PowerShell's masker would hide the second `&&` and its `return`.
        assert not oneshot_build.chained("echo 'it'\\''s' && echo \"a\" && return")
        assert oneshot_build.chained("npm test --label ‘R’")   # a letter to bash, not a quote
        with patch.object(oneshot_build, "syntax", lambda *a: pytest.fail("POSIX never reads PowerShell")):
            assert oneshot_build.chained("npm test --label x")


def test_a_build_whose_gate_names_a_program_gone_from_the_worktree_is_refused_before_it_runs(desktop):
    oneshot_spec.saved("budget", oneshot={"stack": STACK, "stage": "build", "gate": "scripts/gone.ps1 && cargo test",
                                          "toolpaths": {"stage": "build", "paths": {"cargo": "c", "npm": "n"}}})
    with patch.object(oneshot_build, "reported", lambda *a: ""), patch.object(oneshot_build, "pinned", lambda *a: ""), \
            patch.object(oneshot_build.specs, "judge", lambda *a, **k: pytest.fail("no gate runs")):
        why = oneshot_build.built(desktop, SimpleNamespace(halt=None), specs.load("budget", oneshot.BRANCH), "")
    assert "scripts/gone.ps1" in why and "아무것도 실행하지 않았다" in why


def test_the_pc_app_s4_prompts_name_its_tools_and_a_run_without_one_gets_none(desktop):
    spec = {**specs.load("budget", oneshot.BRANCH)}
    spec["oneshot"] = {**spec["oneshot"], "stack": STACK, "design": sheet.parsed(DESIGN, "desktop"),
                       "tests_head": "abc", "gate": "cargo test", "contract": ["tests/core.rs"]}
    for text in (oneshot_build.tests_text(spec), oneshot_build.build_text(spec)):
        assert "Windows program" in text and "- `cargo`\n- `npm`" in text and "{tools}" not in text
        assert "Android" not in text and "{toolchain}" not in text
    several = {**spec, "oneshot": {**spec["oneshot"], "intent": {"kind": "webapp", "kinds": ["webapp", "android"]}}}
    assert "{tools}" not in oneshot_build.tests_text(several) and "host programs" not in oneshot_build.tests_text(several)
    mixed = {**spec, "oneshot": {**several["oneshot"], "intent": {"kind": "webapp", "kinds": ["webapp", "desktop"]},
                                 "stack": [{**STACK[-1], "part": "desktop.build"}]}}
    assert "- `cargo`" in oneshot_build.tests_text(mixed) and "- `npm`" in oneshot_build.build_text(mixed)


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


def test_an_s2_question_whose_answer_could_not_say_which_option_was_picked_is_sent_back(desktop):
    spec = specs.load("budget", oneshot.BRANCH)
    offered = turn([{**s, "status": "assumed"} for s in STACK])   # a bundle offered, nothing confirmed yet
    twins = {"question": "어느 묶음?", "options": [{"label": "Tauri 2", "note": "기기 안 OCR"},
                                                 {"label": "Tauri 2 ", "note": "외부 OCR 서비스"}]}
    joined = {"question": "무엇을 내보낼까?", "multi": True, "options": [
        {"label": "CSV, JSON", "note": "한 파일에 둘 다"}, {"label": "CSV"}, {"label": "JSON"}]}
    for asked_ in (twins, {"questions": [joined]}):
        with pytest.raises(ValueError, match="`label` 이 서로 달라야"):
            oneshot_spec.spec_turn(offered + fenced("choices", asked_), spec)
    oneshot_spec.spec_turn(offered + fenced("choices", {**joined, "multi": False}), spec)   # one pick: no join


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
    # A PC app runs through its critique and the phone round, alone or among other kinds (9e4b), and is held at the
    # owner's desktop round (9e5a).
    assert oneshot_profile.waits_at(o()) == "pc" and not oneshot_profile.held({**o(), "stage": "phone"})
    oneshot_spec.saved("budget", oneshot={"stage": "critique",
                                          "intent": {**o()["intent"], "kinds": ["webapp", "desktop"]}})
    assert oneshot_profile.waits_at(o()) == "pc" and not oneshot_profile.held(o())
    for stage in ("pc", "r3"):
        with patch.object(oneshot, "start_worker", lambda *a, **k: pytest.fail("a held run starts nothing")), \
                pytest.raises(HTTPException, match="다음 버전"):
            START("budget", "design", stage=stage)
    assert o()["stage"] == "critique"


def frozen_in(tree, tmp_path) -> dict:
    """A frozen build's record whose copy is still there and still what its manifest and identity record, at the
    worktree's HEAD."""

    import hashlib
    import json

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "app.exe").write_bytes(b"MZ")
    files, launch = {"app.exe": hashlib.sha256(b"MZ").hexdigest()}, {"program": "app.exe"}
    (tmp_path / "manifest.json").write_text(json.dumps({"files": files}))
    identity = hashlib.sha256(json.dumps({"files": files, "launch": launch}, sort_keys=True).encode()).hexdigest()
    return {"identity": identity, "folder": str(tmp_path / "app"), "head": git_head(tree), "launch": launch}


def test_a_resume_or_a_shutdown_after_two_clean_rounds_never_opens_r3(desktop, tmp_path):
    # An earlier version held the run before R3 with an owner check pending, then the stop was lost (a restart that
    # replaced it): the run is held, never resumable into R3, and recovery moves it to the owner's desktop round,
    # held there with its build, its stop naming the pending check.
    frozen = frozen_in(desktop, tmp_path)
    owner = {"id": "O2", "kind": "desktop", "source": "owner", "requirement": "R1", "check": "캡처에 글이 있다"}
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None, "features": [], "owed": [owner],
                                          "desktop_build": frozen,
                                          "stopped": {"reason": "restart", "detail": "서버가 다시 켜졌다", "state": "r3"}})
    assert oneshot_profile.held(o())
    with patch.object(oneshot_spec, "start", lambda *a, **k: pytest.fail("a held run starts nothing")), \
            pytest.raises(HTTPException, match="다음 버전"):
        oneshot_spec.resume("budget")
    assert o()["stopped"]["reason"] == "kind" and o()["stage"] == "r3"
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "restart", "state": "r3", "detail": "x"}})
    oneshot_spec.recover()
    assert o()["stage"] == "pc" and o()["pc"]["status"] == "waiting" and o()["desktop_build"] == frozen
    assert o()["stopped"]["reason"] == "kind" and "O2" in o()["stopped"]["detail"] and not o().get("r3")
    oneshot_spec.recover()   # held at the round: a second restart leaves it there
    assert o()["stage"] == "pc" and o()["stopped"]["reason"] == "kind"


def test_a_mixed_run_whose_phone_round_passed_waits_before_r3_instead_of_opening_it(desktop):
    from main import oneshot_phone

    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": "android", "kinds": ["android", "desktop"]},
                                          "stage": "phone", "working": "phone", "phone": {"status": "running"}})
    assert oneshot_phone.settled("budget", {"status": "passed", "detail": ""}) is None   # no brief is written
    assert o()["stage"] == "r3" and o()["phone"]["status"] == "passed" and o()["stopped"]["reason"] == "kind"
    assert not o().get("working") and "R3 는 다음 버전에서" in o()["stopped"]["detail"]
    held = {k: v for k, v in o().items() if k != "ts"}
    oneshot_phone.earlier("budget", o())   # the phone's recovery hook leaves a run this version holds as it is
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "kind" and not o().get("r3") and o()["stage"] == held["stage"] == "r3"


def test_several_kinds_an_earlier_versions_run_held_at_the_critique_is_resumable(desktop):
    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": "android", "kinds": ["android", "desktop"]},
                                          "stage": "critique", "stopped": {"reason": "kind", "detail": "다음 버전",
                                                                           "state": "critique"}})
    assert oneshot_profile.waits_at(o()) == "pc" and not oneshot_profile.held(o())
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart" and o()["stage"] == "critique"   # `[재개]` starts the critique


@pytest.mark.parametrize("stop", [None, {"reason": "kind", "detail": "다음 버전", "state": "build-front"}])
def test_an_earlier_versions_frozen_run_at_the_build_is_resumable(desktop, tmp_path, stop):
    # 9e3b2 held a frozen run at `build-front`; this version looks at that build in the same check.
    oneshot_spec.saved("budget", oneshot={"stage": "build-front", "working": None, "stopped": stop, "features": [],
                                          "desktop_build": frozen_in(desktop, tmp_path)})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart" and not oneshot_profile.held(o())
    from main import oneshot_build

    check, stop, _, after = oneshot_build.STAGES["build-front"]
    sent = []
    with patch.dict(oneshot_build.STAGES, {"build-front": (check, stop, lambda spec: "the build", after)}), \
            patch.object(oneshot_build, "send", lambda repo, stage, text: sent.append(stage)):   # no S6 here
        oneshot_spec.resume("budget")
    assert sent == ["build-front"]


def test_a_held_mixed_run_whose_stop_was_lost_starts_no_later_stage_nor_its_build_again(desktop, tmp_path):
    # Two clean critiques passed and the run waits before R3 (9e4b): neither the hand-off nor a start moves it back.
    stage = "r3"
    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": "android", "kinds": ["android", "desktop"]},
                                          "stage": stage, "working": None, "stopped": None, "features": [],
                                          "desktop_build": frozen_in(desktop, tmp_path)})
    with patch.object(oneshot, "start_worker", lambda *a, **k: pytest.fail("a held run starts nothing")), \
            patch.object(oneshot_spec, "start", START):
        SEND("budget", "build-front", "the build")   # the hand-off after R2: the stop is put back, nothing sent
        assert o()["stopped"]["reason"] == "kind"
        oneshot_spec.saved("budget", oneshot={"stopped": None})
        with pytest.raises(HTTPException, match="다음 버전"):   # the shared start every stage goes through
            START("budget", "build-front", stage="build-front")
    assert o()["stopped"]["reason"] == "kind" and o()["stage"] == stage


def test_a_pc_app_has_its_own_prompts_through_s4_and_no_web_fallback_for_later_stages():
    text = oneshot.prompt("spec", "desktop")
    assert "Windows program" in text and "`tools`" in text and "`window`" in text and "web app" not in text
    assert "`desktop.build` also carries `tools`" in oneshot.prompt("spec-kinds")
    assert "`window`" in (oneshot.ROOT / "tool/prompts/oneshot-profile.md").read_text("utf-8")
    design = oneshot.prompt("design", "desktop")
    assert "Windows program" in design and "oneshot-modules" in design and "Android" not in design
    assert "a Windows app" in (oneshot.ROOT / "tool/prompts/oneshot-brief.md").read_text("utf-8")
    for stage in ("tests", "build"):
        assert "Windows program" in oneshot.prompt(stage, "desktop") and "{tools}" in oneshot.prompt(stage, "desktop")
    review, split = oneshot.prompt("review", "desktop"), oneshot.prompt("split", "desktop")
    assert "Windows program" in review and "`source`" in review and "`look`" in review and "`owner`" in review
    assert "Windows program" in split and "oneshot-features" in split and "Android" not in review + split
    mixed = " ".join(oneshot.prompt("review", "kinds").split())
    for said in ("`source`", "where the program reports what it captured", "`{\"composition\": {\"shows\": \"…\"}}`",
                 "drawn at random for the round"):
        assert said in mixed and said in " ".join(review.split())   # a mixed run's `owner` check is as runnable
    screens = oneshot.prompt("frontend", "desktop")
    assert "Windows program" in screens and "`window`" in screens and "AutomationId" in screens
    assert "a PC app" in oneshot.prompt("frontend", "kinds")
    build = oneshot.prompt("build-front", "desktop")
    assert "Windows program" in build and "oneshot-desktop" in build and "{tools}" in build and "Android" not in build
