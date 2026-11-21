"""oneshot plan 11a1: what the owner gives an outside call — checked on the design, kept where it belongs, and every
one given before R1 is approved.

See `docs/plans/oneshot/11-real-outside.md` ("11a1"). Secrets go through this machine's real Credential Manager under
a name no run uses, removed afterwards; the files and programs are real ones.
"""

import json
import sys
import uuid

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_brief as brief, oneshot_needs as needs, oneshot_sheet as sheet, oneshot_spec, release, specs
from test_main import client, no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import DESIGN, GLOSSARY, REQS, STACK, android  # noqa: F401 — `android` is the fixture
from test_oneshot_spec import o, on_sheet, world  # noqa: F401 — `world` is the temporary worktree's fixture

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
