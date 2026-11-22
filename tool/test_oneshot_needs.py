"""oneshot plan 11a1: what the owner gives an outside call — checked on the design, kept where it belongs, and every
one given before R1 is approved.

See `docs/plans/oneshot/11-real-outside.md` ("11a1"). Secrets go through this machine's real Credential Manager under
a name no run uses, removed afterwards; the files and programs are real ones.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from agent.chat_session import Event
from common import Veil, host, masked

from main import oneshot, oneshot_android, oneshot_brief as brief, oneshot_build as build, oneshot_needs as needs, \
    oneshot_sheet as sheet, oneshot_spec, release, specs, work
from test_main import client, no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import DESIGN, GLOSSARY, REQS, STACK, android  # noqa: F401 — `android` is the fixture
from test_oneshot_spec import DESIGN as WEB, GLOSSARY as WEB_GLOSSARY, PROFILE, REQS as WEB_REQS, o, on_sheet, world  # noqa: F401 — `world` is the temporary worktree's fixture

ENV = f"ONESHOT_TEST_{uuid.uuid4().hex.upper()}"   # one per test process: workers never share a target
OURS = {ENV, f"{ENV}_NEW"}   # every target these tests write; the owner's own `budget` keys are never touched
KEY = {"id": "N1", "what": "번역 API 키", "kind": "secret", "env": ENV}
ACCOUNT = {"id": "N2", "what": "번역 서비스 계정", "kind": "account"}
PYTHON = {"id": "N3", "what": "파이썬 실행 파일", "kind": "tool"}
SAMPLE = {"id": "N4", "what": "시험용 만화 한 쪽", "kind": "file"}


def with_needs(*given) -> dict:
    return {**DESIGN, "oneshot-outside": [{**DESIGN["oneshot-outside"][0], "needs": list(given)}]}


@pytest.fixture
def at_r1(android):  # noqa: F811 — the imported fixture, by its name
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stack": STACK, "stage": "r1",
        "design": sheet.parsed(with_needs(KEY, ACCOUNT, PYTHON, SAMPLE), "android"),
        "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": []}})
    yield android
    for env in OURS:
        release.secret_delete(needs.name("budget", env))


def ours() -> set[str]:
    return set(needs.kept("budget")) & OURS


def src() -> str:
    """The R1 sheet as the owner sees it now."""

    return brief.source(brief.snapshot(specs.load("budget", oneshot.BRANCH), "r1"))


def give(nid: str, **body) -> dict:
    return needs.give("budget", nid, needs.Given(**body, source=src()))


def approve():
    items = sheet.decidable(o()["design"], REQS)
    return oneshot_spec.decide("budget", on_sheet(decisions={i: {"approve": True} for i in items}))


@pytest.mark.parametrize("need, said", [
    ({"id": "K1", "what": "키", "kind": "secret", "env": "A"}, "N1, N2"),
    ({"id": "N1", "what": "키", "kind": "password", "env": "A"}, "`kind`"),
    ({"id": "N1", "what": " ", "kind": "account"}, "`what`"),
    ({"id": "N1", "what": "키", "kind": "secret"}, "`env`"),
    ({"id": "N1", "what": "키", "kind": "secret", "env": "gemini_key"}, "`env`"),
    ({"id": "N1", "what": "계정", "kind": "account", "env": "A"}, "비밀 값에만"),
])
def test_a_malformed_need_is_refused_with_its_call_named(need, said):
    with pytest.raises(ValueError, match="X1") as e:
        sheet.parsed(with_needs(need), "android")
    assert said in str(e.value)


def test_two_calls_share_a_need_by_its_id_and_one_id_or_env_for_two_needs_is_refused():
    second = {**DESIGN["oneshot-outside"][0], "id": "X2", "depends_on": ["R2"], "needs": [KEY]}
    shared = {**with_needs(KEY), "oneshot-outside": [*with_needs(KEY)["oneshot-outside"], second]}
    design = sheet.parsed(shared, "android")
    assert [(nid, [x["id"] for x in calls]) for nid, (_, calls) in needs.owed(design).items()] == [("N1", ["X1", "X2"])]
    for clash, said in (({**KEY, "what": "다른 키"}, "같은 id"), ({**KEY, "id": "N2"}, f"같은 `{ENV}`")):
        design["outside"][1]["needs"] = [clash]
        with pytest.raises(ValueError, match=said):
            needs.owed(design)


@pytest.mark.parametrize("prompt", ["oneshot-design", "oneshot-design-android", "oneshot-design-desktop", "oneshot-design-kinds"])
def test_every_design_prompt_asks_for_needs_in_the_shape_the_server_takes(prompt):
    text = (Path(__file__).parent / "prompts" / f"{prompt}.md").read_text(encoding="utf-8")
    example = json.loads(re.search(r"- `oneshot-outside`: `(\[.*?\])`\n", text)[1])
    assert needs.parsed(example[0]["needs"], "X1") == [{"id": "N1", "what": "…", "kind": "secret", "env": "SERVICE_API_KEY"}]
    assert all(f"`{k}`" in text for k in needs.KINDS) and "for it to run for real" in text


WEB_CALL = {"id": "X1", "target": "환율 서비스", "does": "오늘 환율을 받아 온다", "failure": "어제 환율로 적고 그렇다고 보여 준다",
            "depends_on": ["R1", "P1"], "needs": [KEY]}


def test_a_web_server_s_outside_call_names_its_requirement_and_endpoint_and_its_needs_reach_the_sheet(world):  # noqa: F811
    design = sheet.parsed({**WEB, "oneshot-outside": [WEB_CALL]})
    sheet.checked(design, WEB_REQS, WEB_GLOSSARY, PROFILE)
    oneshot_spec.saved("budget", requirements=WEB_REQS, oneshot={"glossary": WEB_GLOSSARY, "design": design, "stage": "r1"})
    text = sheet.sheet(specs.load("budget", oneshot.BRANCH))
    assert "## 서버 밖으로 나가는 호출" in text and "| X1 | 환율 서비스 | 오늘 환율을 받아 온다 |" in text
    assert f"| N1 | 번역 API 키 | 비밀 값 `{ENV}` | X1 | R1 |" in text and "X1" in sheet.decidable(design, WEB_REQS)
    for bad, said in ((["P1"], "쓰는 요구"), (["R1"], "부르는 엔드포인트")):
        with pytest.raises(ValueError, match=said):
            sheet.checked(sheet.parsed({**WEB, "oneshot-outside": [{**WEB_CALL, "depends_on": bad}]}), WEB_REQS, WEB_GLOSSARY, PROFILE)
    second = {**WEB_CALL, "id": "X2", "depends_on": ["R2", "P2"], "needs": [{**KEY, "id": "N2"}]}
    with pytest.raises(ValueError, match=f"같은 `{ENV}`"):
        sheet.checked(sheet.parsed({**WEB, "oneshot-outside": [WEB_CALL, second]}), WEB_REQS, WEB_GLOSSARY, PROFILE)
    with pytest.raises(ValueError, match="`oneshot-outside` 블록"):   # the web design always says whether it leaves
        sheet.parsed({k: v for k, v in WEB.items() if k != "oneshot-outside"})
    # The server's call is named on the brief, and an edit of the call reaches the endpoint that makes it.
    with pytest.raises(ValueError, match="밖으로 나가는 호출이 있다: X1"):
        brief.design_checked({"requirements": WEB_REQS, "design": design}, [{"name": "알아 두실 것", "text": "없음"}], [])
    heard = [{**WEB_REQS[0], "capability": {"permission": "microphone", "prompt": "마이크", "risk": "없음"}}, *WEB_REQS[1:]]
    for reqs in (WEB_REQS, heard):   # a web requirement's permission is not one the brief must cite
        brief.design_checked({"requirements": reqs, "design": design}, [{"name": "알아 두실 것", "text": "X1 은 환율을 받는다"}], [])
    assert {"P1", "T1", "F1"} <= sheet.dependents(design, WEB_REQS, {"X1"}) and "X1" in sheet.dependents(design, WEB_REQS, {"P1"})
    del design["outside"]   # a web design stored before it had outside calls
    oneshot_spec.saved("budget", oneshot={"design": design})
    assert "밖으로 나가는 호출" not in sheet.sheet(specs.load("budget", oneshot.BRANCH))


def test_a_design_from_before_needs_needs_nothing():
    design = sheet.parsed(DESIGN, "android")
    assert design["outside"][0]["needs"] == [] and needs.section(design) == []
    del design["outside"][0]["needs"]   # a design stored before this step
    assert needs.owed(design) == {}


def test_the_sheet_lists_what_the_owner_gives_but_never_whether_it_is_given(at_r1):
    text = sheet.sheet(specs.load("budget", oneshot.BRANCH))
    assert "## 주인이 줄 것" in text
    assert f"| N1 | 번역 API 키 | 비밀 값 `{ENV}` | X1 | R1 |" in text
    assert "| N4 | 시험용 만화 한 쪽 | 파일 | X1 | R1 |" in text
    shown = oneshot_spec.status("budget")["needs"]
    assert [(n["id"], n["given"], n["calls"]) for n in shown] == [(i, False, ["X1"]) for i in ("N1", "N2", "N3", "N4")]


def test_approval_waits_for_every_need_and_a_secret_reaches_only_credential_manager(at_r1, tmp_path):
    with pytest.raises(HTTPException) as refused:
        approve()
    assert refused.value.status_code == 409 and "N1 번역 API 키" in refused.value.detail and o()["stage"] == "r1"
    sample = tmp_path / "page.png"
    sample.write_bytes(b"\x89PNG")
    value = f"key-{uuid.uuid4().hex}"
    give("N1", value=f" {value}\n")
    give("N2", confirmed=True)
    give("N3", value=sys.executable)
    shown = give("N4", value=str(sample))
    assert all(n["given"] for n in shown["needs"])
    assert release.secret_read(needs.name("budget", ENV)) == value
    record = (specs.load("budget", oneshot.BRANCH), shown)
    assert value not in json.dumps(record, ensure_ascii=False)   # the spec and the screen never hold it
    approve()
    assert o()["stage"] == "tests"
    assert value not in (at_r1 / "docs/design/core.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("nid, body, said", [
    ("N1", {"value": "  "}, "2560바이트"),
    ("N1", {"value": "k" * 1281}, "2560바이트"),   # 2,562 bytes as UTF-16
    ("N2", {}, "확인해야"),
    ("N3", {"value": "no-such-program-anywhere"}, "PATH"),
    ("N4", {"value": "relative/page.png"}, "절대 경로"),
    ("N9", {"value": "x"}, "설계에 없는"),
])
def test_a_need_given_wrong_is_refused_naming_nothing_typed_and_nothing_is_kept(at_r1, nid, body, said):
    with pytest.raises(HTTPException, match=said) as e:
        give(nid, **body)
    assert not body.get("value", "").strip() or body["value"] not in e.value.detail
    assert not o().get("given") and ours() == set()


def test_the_longest_secret_credential_manager_holds_is_kept_whole(at_r1):
    give("N1", value="k" * 1280)
    assert release.secret_read(needs.name("budget", ENV)) == "k" * 1280


def test_a_value_given_on_a_sheet_a_redesign_replaced_is_refused(at_r1):
    seen = src()
    moved = with_needs({**KEY, "env": f"{ENV}_NEW"})   # the same id now names another provider's key
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(moved, "android")})
    for late in (lambda: needs.give("budget", "N1", needs.Given(value="old provider", source=seen)),
                 lambda: needs.take_back("budget", "N1", seen)):
        with pytest.raises(HTTPException, match="바뀌었다") as e:
            late()
        assert e.value.status_code == 409
    assert ours() == set()


def test_a_refused_body_never_comes_back_in_the_answer():
    marker = f"typed-{uuid.uuid4().hex}"
    answer = client().post("/api/oneshot/spec/budget/needs/N1", json={"value": [marker]})
    assert answer.status_code == 422 and marker not in answer.text and answer.json()["detail"][0]["loc"]


def test_a_need_can_be_given_only_at_r1_with_no_turn_running(at_r1):
    oneshot_spec.saved("budget", oneshot={"working": "redesign"})
    with pytest.raises(HTTPException) as e:
        give("N2", confirmed=True)
    assert e.value.status_code == 409 and not o().get("given")


def test_a_redesign_that_changes_what_a_need_is_asks_again_but_a_secret_stays_under_its_env(at_r1):
    give("N1", value="kept")
    give("N2", confirmed=True)
    renamed = with_needs({**KEY, "what": "새 번역 API 키"}, {**ACCOUNT, "what": "유료 번역 계정"})
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(renamed, "android")})
    assert [(n["id"], n["given"]) for n in oneshot_spec.status("budget")["needs"]] == [("N1", True), ("N2", False)]


def test_a_secret_a_redesign_renamed_is_still_listed_and_can_be_removed_at_any_stage(at_r1):
    give("N1", value="old")
    renamed = with_needs({**KEY, "env": f"{ENV}_NEW"})
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(renamed, "android"), "stage": "build"})
    assert ENV in oneshot_spec.status("budget")["secrets_kept"]
    shown = needs.forget("budget", ENV)
    assert ENV not in shown["secrets_kept"] and release.secret_read(needs.name("budget", ENV)) is None
    with pytest.raises(HTTPException, match="대문자"):
        needs.forget("budget", "../other")


@pytest.mark.parametrize("repo", ["no-spec-here", "budget*", "a/b"])
def test_a_secret_is_removed_only_for_a_repository_with_a_oneshot_spec(at_r1, repo):
    held = needs.name(repo, ENV)   # a credential under that name, whoever wrote it
    release.secret_write(held, "stays")
    try:
        with pytest.raises(HTTPException) as e:
            needs.forget(repo, ENV)
        assert e.value.status_code == 404 and release.secret_read(held) == "stays"
    finally:
        release.secret_delete(held)


def test_a_host_without_credential_manager_shows_the_sheet_and_refuses_a_secret(at_r1, monkeypatch):
    def absent():
        raise ValueError("Windows only")

    monkeypatch.setattr(release, "_advapi", absent)   # this machine has it; the host the check is about does not
    view = oneshot_spec.status("budget")
    assert not set(view["secrets_kept"]) & OURS and not next(n for n in view["needs"] if n["id"] == "N1")["given"]
    with pytest.raises(HTTPException, match="자격 증명 관리자가 없다") as e:
        give("N1", value="x")
    assert e.value.status_code == 409


def test_what_was_given_can_be_taken_back(at_r1, tmp_path):
    sample = tmp_path / "page.png"
    sample.write_bytes(b"x")
    give("N1", value="gone soon")
    give("N4", value=str(sample))
    needs.take_back("budget", "N1", src())
    shown = needs.take_back("budget", "N4", src())
    assert release.secret_read(needs.name("budget", ENV)) is None and "N4" not in o()["given"]
    assert not any(n["given"] for n in shown["needs"])
    give("N4", value=str(sample))
    sample.unlink()   # a file that is gone is not given
    assert not next(n for n in oneshot_spec.status("budget")["needs"] if n["id"] == "N4")["given"]


def test_every_gate_the_run_runs_has_each_given_secret_and_the_prompts_name_only_its_variable(at_r1, tmp_path, monkeypatch):
    spec = lambda: specs.load("budget", oneshot.BRANCH)  # noqa: E731
    base = {k: "set" for k in build.TOOLCHAIN}   # the Android toolchain, as this host would have it
    monkeypatch.setattr(oneshot_android, "build_env", lambda: dict(base))
    assert ENV not in needs.gate_env(spec(), None) and build.toolchain(spec()) == base   # not given: none
    sample = tmp_path / "page.png"
    sample.write_bytes(b"x")
    value = f"key-{uuid.uuid4().hex}"
    give("N1", value=value)
    give("N3", value=sys.executable)
    give("N4", value=str(sample))
    assert build.toolchain(spec()) == {**base, ENV: value} == build.built_env(spec())
    server = needs.gate_env(spec(), None)   # the server's own environment, with the secret on top
    assert server[ENV] == value and server.get("PATH") == os.environ.get("PATH")
    oneshot_spec.saved("budget", oneshot={"tests_head": "a" * 40, "gate": "pytest", "contract": ["tests/test_x.py"]})
    for text in (build.tests_text(spec()), build.build_text(spec())):
        assert "{needs}" not in text and value not in text and "fake, mock, stub or stand-in" in text
        assert f"- `{ENV}`: N1 번역 API 키, a secret (X1)" in text and f"a file at `{sample.resolve()}`" in text
        assert "N2 번역 서비스 계정, an account the owner has (X1)" in text
    take = needs.take_back("budget", "N1", src())
    assert take and needs.gate_env(spec(), {"BASE": "server"}) == {"BASE": "server"}   # removed: no gate gets it


def test_a_run_whose_calls_need_nothing_is_told_so():
    assert needs.told({"repo": "budget", "oneshot": {"design": sheet.parsed(DESIGN, "android")}}) == \
        "- none: no outside call needs anything from the owner"


REPORTER = """import os, sys
value = os.environ["{env}"]
print("calling the service with", value)
name = value if sys.argv[2] == "named" else "test_translates"
open(sys.argv[1], "w", encoding="utf-8").write(
    f'<testsuite><testcase classname="c" name="{{name}}"><failure message="{{value}}">{{value}}</failure></testcase></testsuite>')
sys.exit(int(sys.argv[3]))
"""


def test_a_gate_never_shows_a_secret_and_its_report_is_read_once_and_removed(at_r1, tmp_path, monkeypatch):
    monkeypatch.setattr(oneshot_android, "build_env", lambda: dict(os.environ))
    value = f"key-{uuid.uuid4().hex}"
    give("N1", value=value)
    script = tmp_path / "reporter.py"
    script.write_text(REPORTER.format(env=ENV), encoding="utf-8")
    notes = []
    chat = type("Chat", (), {"id": "c", "parent_id": None})
    spec = specs.load("budget", oneshot.BRANCH)
    run = type("Run", (), {"halt": threading.Event(), "put": staticmethod(notes.append), "chat": chat})()
    reports = oneshot_spec.store("budget").folder / "junit"
    command = f'"{sys.executable}" "{script}" {{report}}'
    assert build.junit(at_r1, run, spec, command + " plain 0") == [("c", "test_translates", "", "failure")]
    for args, said in ((" named 0", "사례 이름에 비밀 값이 들어 있다 — \\[N1\\]"),):
        with pytest.raises(ValueError, match=said) as e:
            build.junit(at_r1, run, spec, command + args)
        assert value not in str(e.value)
    real, reads = release.secret_read, []
    # The store answers once: the gate is launched and masked from that one read, whatever the store says after.
    monkeypatch.setattr(release, "secret_read", lambda key: reads.append(key) or (None if len(reads) > 1 else real(key)))
    verdict = build.judged(at_r1, run, spec, [f'"{sys.executable}" "{script}" {build.REPORT} plain 1'], build.toolchain(spec))
    assert not verdict["ok"] and len(reads) == 1 and "calling the service with [N1]" in verdict["tail"]
    assert value not in json.dumps([verdict, notes], ensure_ascii=False, default=str)
    assert not (at_r1 / build.REPORT).exists() and not list(reports.glob("*.xml"))   # no report outlives its read


def test_a_secret_not_given_never_falls_back_to_a_variable_the_server_has(at_r1, monkeypatch):
    monkeypatch.setenv(ENV, "the server's own key")
    monkeypatch.setattr(oneshot_android, "build_env", lambda: {**os.environ, "JAVA_HOME": "set"})
    spec = specs.load("budget", oneshot.BRANCH)
    assert ENV not in needs.gate_env(spec, None) and ENV not in build.toolchain(spec)
    assert build.toolchain(spec)["JAVA_HOME"] == "set"
    assert needs.gate_env({"repo": "budget", "oneshot": {"design": sheet.parsed(DESIGN, "android")}}, None) is None


def test_a_secret_is_masked_in_any_casing_and_the_longest_first():
    from common import masked

    hide = {"key-in": "[N1]", "key-in-long": "[N2]"}
    assert masked("KEY-IN, Key-In-Long, key-ın, KEY-İN, key-out", hide) == "[N1], [N2], [N1], [N1], key-out"
    assert masked("text", {}) == "text" and masked("a+b", {"A+B": "[N1]"}) == "[N1]"   # literal, never a pattern
    assert masked("ΚΛΕΙΔΙ ς σ Σ", {"κλειδι": "[N1]", "σ": "[N2]"}) \
        == "[N1] [N2] [N2] [N2]"   # every sigma
    assert masked("STRASSE, straße", {"strasse": "[N1]"}) == "[N1], [N1]"   # a casing that changes length
    assert masked("İd, mark ̇ here", {"i̇d": "[N1]", "̇": "[N2]"}) == "[N1], mark [N2] here"
    assert masked("ab", {"İ": "[N1]", "": "[N2]"}) == "ab"   # nothing folds to nothing
    assert masked("İ", {"̇": "[N1]"}) == "I[N1]"   # as spelled, though the fold here drops it
    many = "k " * 20000
    started = time.monotonic()
    assert masked(many, {"k": "[N1]"}) == "[N1] " * 20000 and time.monotonic() - started < 2   # linear in matches


def test_a_secret_that_folds_to_nothing_shows_on_no_page():
    from main.oneshot_look import secrets_shown

    class Frame:
        def evaluate(self, _):
            return {"texts": ["an unrelated page ̇"], "values": []}

    page = type("Page", (), {"frames": [Frame()]})()
    assert secrets_shown(page, {"̇": "[N1]"}) and not secrets_shown(page, {"̇̇x": "[N1]"})
    Frame.evaluate = lambda self, _: {"texts": ["İ"], "values": []}
    assert secrets_shown(page, {"̇": "[N1]"})   # as spelled, though the fold here drops it
    Frame.evaluate = lambda self, _: {"texts": ["key-I\ṅx"], "values": ["key-I", "̇x"]}
    assert secrets_shown(page, {"key-İx": "[N1]"})   # as spelled, broken across lines
    Frame.evaluate = lambda self, _: {"texts": ["key-I", "̇x"], "values": []}
    assert not secrets_shown(page, {"key-İx": "[N1]"})   # never across two streams
    Frame.evaluate = lambda self, _: {"texts": ["KEY-I\n" + chr(0x307) + "X"], "values": []}
    assert secrets_shown(page, {"key-" + chr(0x130) + "x": "[N1]"})   # a casing broken where its fold depends


# -- 11b2a: the build agent's own turns --------------------------------------------------

def test_a_build_session_carries_each_given_secret_and_starts_again_when_it_changes(at_r1, monkeypatch):
    monkeypatch.setenv(ENV, "the server's own key")
    monkeypatch.setattr(specs, "owner", lambda path: specs.load("budget", oneshot.BRANCH))   # the fixture's tree
    made = []

    class Chat:
        is_codex, fast = False, False

        def __init__(self, path, env=None, bypass=False, **k):
            made.append(env)
            self.bypass, self.closed = bypass, False

        def reconfigure(self, model, effort):
            pass

        def close(self):
            self.closed = True

    turned = lambda sid: work.remember(at_r1, "assistant", "끝냈다", session_id=sid, model="claude-opus-5-5")  # noqa: E731
    turned("the conversation before")
    with patch.object(work, "ChatSession", Chat), patch.dict(work._sessions, clear=True):
        first = work.session(at_r1, "opus", "low")
        assert made == [{ENV: None}] and first.hide == {}   # not given: the server's own variable is dropped
        value = f"key-{uuid.uuid4().hex}"
        give("N1", value=value)
        second = work.session(at_r1, "opus", "low")
        assert first.closed and made[-1] == {ENV: value} and second.hide == {value: "[N1]"}
        assert second.session_id is None   # a conversation that held other values never resumes
        work._sessions.clear()   # a restart before the new conversation's first turn: still not resumed
        assert work.session(at_r1, "opus", "low").session_id is None
        turned("the new conversation")
        work._sessions.clear()   # the same values since its turn: it resumes
        second = work.session(at_r1, "opus", "low")
        assert second.session_id == "the new conversation"
        assert work.session(at_r1, "opus", "low") is second   # nothing changed: the same session goes on
        assert needs.take_back("budget", "N1", src())
        third = work.session(at_r1, "opus", "low")
        assert third is not second and second.closed and made[-1] == {ENV: None} and third.session_id is None
    kept = json.dumps([specs.load("budget", oneshot.BRANCH), work.recall(at_r1, 0)], ensure_ascii=False)
    assert value not in kept and "비밀 값이 바뀌었다" in kept   # only a digest is kept
    assert ENV not in host.environment({ENV: None})   # `None` drops the variable from the child


def test_a_turn_whose_answer_commits_or_files_hold_a_secret_is_refused_naming_only_the_need(at_r1, monkeypatch,
                                                                                             tmp_path_factory):
    value = f"Key-{uuid.uuid4().hex}"
    give("N1", value=value)
    spec = lambda: specs.load("budget", oneshot.BRANCH)  # noqa: E731
    _, hide = needs.agent(spec())

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=at_r1, check=True,
                              capture_output=True, text=True).stdout.strip()

    oneshot_spec.saved("budget", oneshot={"design_head": git("rev-parse", "HEAD")})
    assert needs.leaked(at_r1, spec(), hide, "끝냈다") == ""
    why = needs.leaked(at_r1, spec(), hide, f"키는 {value.lower()} 이다")   # any casing
    assert "N1" in why and "답" in why and value.lower() not in why.lower()
    (at_r1 / "config.txt").write_text(f"key = {value.upper()}\n", encoding="utf-8")
    assert "커밋하지 않은 파일" in needs.leaked(at_r1, spec(), hide, "끝냈다")
    git("add", "config.txt")
    git("commit", "-m", "Add the key")
    git("rm", "-q", "config.txt")
    git("commit", "-m", "Remove the key")
    why = needs.leaked(at_r1, spec(), hide, "끝냈다")   # gone from the tree, still in the history the PR pushes
    assert "커밋" in why and "다시 써서" in why and value.lower() not in why.lower()
    added = git("rev-parse", "HEAD~1")   # the removal's diff quotes the value too
    assert spec()["oneshot"]["tainted"] == sorted([added, git("rev-parse", "HEAD")])
    # Taken back or replaced, the value is no longer in the mask, but its commit stays refused.
    assert added[:12] in needs.leaked(at_r1, spec(), {}, "끝냈다")
    git("commit", "--allow-empty", "-m", f"Again {value}")   # made while the refusal stands: recorded too
    assert added[:12] in needs.leaked(at_r1, spec(), hide, "끝냈다")
    assert git("rev-parse", "HEAD") in spec()["oneshot"]["tainted"]
    nowhere = tmp_path_factory.mktemp("no-repository")   # Git cannot read it: the record is kept, never cleared
    assert "아직 기록에 있다" in needs.still_tainted(nowhere, spec()) and spec()["oneshot"]["tainted"]
    git("reset", "-q", "--hard", spec()["oneshot"]["design_head"])   # the history rewritten
    assert needs.leaked(at_r1, spec(), hide, "끝냈다") == "" and spec()["oneshot"]["tainted"] == []
    assert needs.leaked(at_r1, spec(), {}, value) == ""   # a session that carried no secret is never checked
    # A merge's resolution: in neither parent, read against the first parent.
    git("checkout", "-q", "-b", "side")
    (at_r1 / "side.txt").write_text("side\n", encoding="utf-8")
    git("add", "side.txt")
    git("commit", "-q", "-m", "Side")
    git("checkout", "-q", "-")
    git("merge", "-q", "--no-ff", "--no-commit", "side")
    (at_r1 / "side.txt").write_text(f"side {value}\n", encoding="utf-8")
    git("add", "side.txt")
    git("commit", "-q", "-m", "Merge side")
    assert "커밋" in needs.leaked(at_r1, spec(), hide, "끝냈다")
    git("reset", "-q", "--hard", spec()["oneshot"]["design_head"])
    git("branch", "-q", "-D", "side")
    assert needs.leaked(at_r1, spec(), hide, "끝냈다") == ""
    head = spec()["oneshot"]["design_head"]
    oneshot_spec.saved("budget", oneshot={"design_head": "f" * 40})   # a history git cannot read is never clean
    failed = needs.leaked(at_r1, spec(), hide, "끝냈다")   # named by what failed, never by Git's own words
    assert failed == "비밀 값이 남았는지 확인하지 못했다 — git log 이(가) 실패했다 (OSError)"
    oneshot_spec.saved("budget", oneshot={"design_head": git("rev-parse", "HEAD")})
    git("commit", "--allow-empty", "-m", f"Use {value.upper()} for the call")   # the message is pushed too
    assert "커밋" in needs.leaked(at_r1, spec(), hide, "끝냈다")
    git("reset", "-q", "--hard", "HEAD~1")   # rewritten: the message is gone from the history
    assert needs.leaked(at_r1, spec(), hide, "끝냈다") == ""
    # A stage check whose reason quotes the value sends it on as its need.
    oneshot_spec.saved("budget", oneshot={"stage": "build", "attempts": 0})
    quoted = []
    monkeypatch.setattr(build, "send", lambda repo, stage, text: quoted.append(text))
    monkeypatch.setitem(build.STAGES, "build", (lambda *a: f"게이트 실패 — {value}", *build.STAGES["build"][1:]))
    notes = []
    run = SimpleNamespace(halt=threading.Event(), put=notes.append, person=False,
                          chat=SimpleNamespace(hide=hide, id="c", parent_id=None))
    build.checked(at_r1, run, spec(), "끝냈다")()
    said = json.dumps([quoted, notes], ensure_ascii=False)
    assert "게이트 실패 — [N1]" in quoted[0] and value.lower() not in said.lower()

    def broke(*a):
        raise KeyError(value)

    monkeypatch.setitem(build.STAGES, "build", (broke, *build.STAGES["build"][1:]))
    with pytest.raises(RuntimeError) as raised:   # what `specs.check` logs and notes
        build.checked(at_r1, run, spec(), "끝냈다")
    assert "[N1]" in str(raised.value) and raised.value.__cause__ is None and raised.value.__suppress_context__
    assert value not in str(raised.value) and value not in json.dumps(spec()["oneshot"]["stopped"])
    oneshot_spec.saved("budget", oneshot={"stopped": None})
    oneshot_spec.saved("budget", oneshot={"design_head": head})
    # The stage's own check never reads a refused answer: the turn goes back with the need named.
    oneshot_spec.saved("budget", oneshot={"stage": "build", "attempts": 0})
    sent, notes = [], []
    monkeypatch.setattr(build, "send", lambda repo, stage, text: sent.append(text))
    monkeypatch.setitem(build.STAGES, "build", (lambda *a: pytest.fail("the check read a leaking answer"),
                                                *build.STAGES["build"][1:]))
    run = SimpleNamespace(halt=threading.Event(), put=notes.append, person=False,
                          chat=SimpleNamespace(hide=hide, id="c", parent_id=None))
    build.checked(at_r1, run, spec(), f"키는 {value} 이다")()
    assert "The server refused this build turn: 비밀 값 N1" in sent[0] and value.lower() not in sent[0].lower()


def test_a_turn_shows_and_keeps_each_secret_as_its_need_but_the_check_reads_the_answer_as_given(tmp_path, monkeypatch):
    value = f"key-{uuid.uuid4().hex}"
    command = {"tool": "commandExecution", "item_id": "c1"}
    events = [Event("tool", f"curl -H 'x-key: {value}'", {"tool": "Bash", "diff": f"+KEY={value}"}, "w"),
              # A value split across parts of one text, and inside an approval's structured input.
              Event("delta", f"키는 {value[:6]}", {}, "w"), Event("delta", value[6:], {}, "w"),
              Event("tool", f"out {value[:9]}", command, "w"), Event("tool", f"{value[9:]} end", command, "w"),
              Event("approval", "묻는다", {"id": f"q-{value}", "tool": "AskUserQuestion",   # nested ids are the agent's
                                          "input": {"questions": [{"id": value, "question": f"{value} 를 쓸까?"}]}}, "w"),
              Event("delta", f" 이고 {value[:3]}", {}, "w"),   # a tail that could start the value, then the end
              Event("done", f"끝냈다 — {value}", {"error": False}, "w")]
    chat = SimpleNamespace(id="w", parent_id=None, is_codex=False, model="opus", path=tmp_path,
                           hide={value: "[N1]"}, say=lambda *_: iter(events))
    recorded, checked = [], []
    monkeypatch.setattr(work, "feed", work.Feed())
    monkeypatch.setattr(work, "remember", lambda *args, **kwargs: recorded.append((args, kwargs)))
    monkeypatch.setattr(work, "dispatch", lambda *_: None)
    monkeypatch.setattr(specs, "check", lambda path, run, final: checked.append(final))
    run = work.Run(chat)
    work.run_turn(tmp_path, run, "Build it", lambda: None)
    assert checked == [f"끝냈다 — {value}"]   # the leak check sees the value, to refuse it
    # The handle the answer comes back with is the server's own, kept as it was; nothing else holds the value.
    assert [e["meta"].pop("id") for e in run.events if e["kind"] == "approval"] == [f"q-{value}"]
    kept = json.dumps([run.events, recorded], ensure_ascii=False, default=str)
    assert value not in kept and "x-key: [N1]" in kept and "+KEY=[N1]" in kept and "끝냈다 — [N1]" in kept
    assert value[6:] not in kept and value[9:] not in kept and "[N1] 를 쓸까?" in kept
    shown = lambda kind: "".join(e["text"] for e in run.events if e["kind"] == kind and e["meta"].get("item_id") in (None, "c1"))  # noqa: E731
    assert shown("delta") == f"키는 [N1] 이고 {value[:3]}" and "out [N1] end" in shown("tool")


def test_a_streamed_value_in_a_spelling_that_folds_shorter_is_held_until_it_is_whole():
    value = f"INDI{uuid.uuid4().hex}"
    spelled = value.replace("I", "I\u0307")   # folds to the same, three characters longer
    for at in range(1, len(spelled)):
        veil = Veil({value: "[N1]"})
        shown = veil.stream("d", f"x {spelled[:at]}", {}) + veil.stream("d", f"{spelled[at:]} y", {})
        assert shown + "".join(t for _, t, _ in veil.rest()) == "x [N1] y", at


def test_a_streamed_value_starting_inside_a_ligature_is_held_until_it_is_whole():
    value = f"fi{uuid.uuid4().hex}"
    text = f"x \ufb03{value[2:]} y"   # the ligature folds to `ffi`: the value starts inside it
    whole = masked(text, {value: "[N1]"})
    for at in range(1, len(text)):
        veil = Veil({value: "[N1]"})
        shown = veil.stream("d", text[:at], {}) + veil.stream("d", text[at:], {})
        assert shown + "".join(t for _, t, _ in veil.rest()) == whole and value[2:] not in whole, at


def test_a_broken_check_is_logged_and_noted_masked(monkeypatch):
    value, logged, notes = f"key-{uuid.uuid4().hex}", [], []

    def broke(*a):
        raise RuntimeError(f"push failed for {value}")

    monkeypatch.setattr(specs, "_check", broke)
    monkeypatch.setattr(specs.errorlog, "record", lambda source, error, **k: logged.append(str(error)))
    run = SimpleNamespace(turn="t", put=notes.append, chat=SimpleNamespace(hide={value: "[N1]"}, id="c", parent_id=None))
    assert specs.check(Path("."), run, "끝냈다") is None
    said = json.dumps([logged, notes], ensure_ascii=False)
    assert value not in said and "push failed for [N1]" in said


def test_every_event_a_turn_puts_is_masked_but_the_tags_the_screen_dispatches_on(monkeypatch):
    value, logged, faults = f"Ask{uuid.uuid4().hex}", [], []
    monkeypatch.setattr(work, "feed", work.Feed())
    run = work.Run(SimpleNamespace(id="w", parent_id=None, hide={value: "[N1]"}))
    # A person's answer to a question, put by the server outside the provider's events.
    run.put({"kind": "answered", "text": "", "meta": {"id": "q1", "allow": True, "by": "person", "answers": [value]}})
    run.put({"kind": "approval", "text": "묻는다", "meta": {"id": "q2", "tool": "AskUserQuestion",
                                                       "input": {"questions": [{"question": f"{value}?"}]}}})
    assert value not in json.dumps(run.events) and run.events[0]["meta"]["answers"] == ["[N1]"]
    assert run.events[1]["meta"]["tool"] == "AskUserQuestion"   # the form the screen draws, never masked
    # A returned failure: the log, the note and the spec's fault.
    monkeypatch.setattr(specs.errorlog, "record", lambda source, error, **k: logged.append(str(error)))
    monkeypatch.setattr(specs, "update", lambda repo, sid, **k: faults.append(k["fault"]))
    specs.failed(run, {"repo": "r", "id": "s"}, f"push failed: {value}")
    assert [logged, faults, run.events[-1]["text"]] == [["push failed: [N1]"], ["push failed: [N1]"], "push failed: [N1]"]
