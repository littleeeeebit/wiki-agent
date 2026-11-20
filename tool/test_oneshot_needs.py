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

from main import oneshot_needs as needs, oneshot_sheet as sheet, oneshot_spec, release, specs, oneshot
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import DESIGN, GLOSSARY, REQS, STACK, android  # noqa: F401 — `android` is the fixture
from test_oneshot_spec import o, on_sheet, world  # noqa: F401 — `world` is the temporary worktree's fixture

ENV = f"ONESHOT_TEST_{uuid.uuid4().hex.upper()}"
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
    release.secret_delete(needs.name("budget", ENV))


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
    needs.give("budget", "N1", needs.Given(value=f" {value}\n"))
    needs.give("budget", "N2", needs.Given(confirmed=True))
    needs.give("budget", "N3", needs.Given(value=sys.executable))
    shown = needs.give("budget", "N4", needs.Given(value=str(sample)))
    assert all(n["given"] for n in shown["needs"])
    assert release.secret_read(needs.name("budget", ENV)) == value
    record = (specs.load("budget", oneshot.BRANCH), shown)
    assert value not in json.dumps(record, ensure_ascii=False)   # the spec and the screen never hold it
    approve()
    assert o()["stage"] == "tests"
    assert value not in (at_r1 / "docs/design/core.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("nid, body, said", [
    ("N1", needs.Given(value="  "), "1–4096"),
    ("N2", needs.Given(), "확인해야"),
    ("N3", needs.Given(value="no-such-program-anywhere"), "PATH"),
    ("N4", needs.Given(value="relative/page.png"), "절대 경로"),
    ("N9", needs.Given(value="x"), "설계에 없는"),
])
def test_a_need_given_wrong_is_refused_and_nothing_is_kept(at_r1, nid, body, said):
    with pytest.raises(HTTPException, match=said):
        needs.give("budget", nid, body)
    assert not o().get("given") and release.secret_read(needs.name("budget", ENV)) is None


def test_a_need_can_be_given_only_at_r1_with_no_turn_running(at_r1):
    oneshot_spec.saved("budget", oneshot={"working": "redesign"})
    with pytest.raises(HTTPException) as e:
        needs.give("budget", "N2", needs.Given(confirmed=True))
    assert e.value.status_code == 409 and not o().get("given")


def test_a_redesign_that_changes_what_a_need_is_asks_again_but_a_secret_stays_under_its_env(at_r1):
    needs.give("budget", "N1", needs.Given(value="kept"))
    needs.give("budget", "N2", needs.Given(confirmed=True))
    renamed = with_needs({**KEY, "what": "새 번역 API 키"}, {**ACCOUNT, "what": "유료 번역 계정"})
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(renamed, "android")})
    assert [(n["id"], n["given"]) for n in oneshot_spec.status("budget")["needs"]] == [("N1", True), ("N2", False)]


def test_what_was_given_can_be_taken_back(at_r1, tmp_path):
    sample = tmp_path / "page.png"
    sample.write_bytes(b"x")
    needs.give("budget", "N1", needs.Given(value="gone soon"))
    needs.give("budget", "N4", needs.Given(value=str(sample)))
    needs.take_back("budget", "N1")
    shown = needs.take_back("budget", "N4")
    assert release.secret_read(needs.name("budget", ENV)) is None and "N4" not in o()["given"]
    assert not any(n["given"] for n in shown["needs"])
    needs.give("budget", "N4", needs.Given(value=str(sample)))
    sample.unlink()   # a file that is gone is not given
    assert not next(n for n in oneshot_spec.status("budget")["needs"] if n["id"] == "N4")["given"]
