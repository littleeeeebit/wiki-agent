"""oneshot step 3a: the spec conversation, the backend design checks, the R1 sheet and its decisions.

See `docs/plans/oneshot/3-spec-backend.md`. Model turns (`oneshot.ask`) are
stand-ins; Git runs in a temporary worktree.
"""

import json
import os
import subprocess
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_sheet as sheet, oneshot_spec, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse

PROFILE = {"auth": "none", "data_store": "sqlite", "backend": "fastapi", "visibility": "private",
           "docs_language": "ko", "budget_usd": 50, "budget_hours": 10}
REQS = [{"id": "R1", "text": "지출을 적으면 이번 달 합계에 더해진다", "source": {"kind": "original", "locator": "주인의 문장"},
         "status": "confirmed", "postconditions": ["합계가 늘어난다"]},
        {"id": "R2", "text": "지난 달 지출을 고르면 그 달 목록이 보인다", "source": {"kind": "decision", "locator": "답 1"},
         "status": "confirmed", "postconditions": ["그 달 목록이 보인다"], "depends_on": ["R1"]}]
GLOSSARY = [{"term": "지출", "meaning": "돈을 쓴 한 건", "avoid": ["expense"]}]
DESIGN = {
    "oneshot-entities": [{"id": "E1", "name": "Spending", "fields": [{"name": "id", "type": "int", "key": True},
                                                                    {"name": "amount", "type": "int"}],
                          "depends_on": ["R1"]}],
    "oneshot-api": [{"id": "P1", "method": "POST", "path": "/spendings", "does": "지출 한 건을 적는다",
                     "request": {"amount": "int"}, "response": {"total": "int"}, "errors": ["422"],
                     "depends_on": ["R1", "E1"]},
                    {"id": "P2", "method": "GET", "path": "/spendings", "does": "고른 달의 지출 목록을 돌려준다",
                     "request": {}, "response": {"items": "list"}, "errors": [], "depends_on": ["R2", "E1", "A1"]}],
    "oneshot-flows": [{"id": "F1", "name": "지출 적기", "steps": [{"from": "사용자", "to": "서버", "say": "금액을 보낸다",
                                                              "api": "P1"}], "depends_on": ["P1"]}],
    "oneshot-assumptions": [{"id": "A1", "what": "달은 달력 기준으로 나눈다", "why": "주인이 월급날을 말하지 않았다",
                             "rejected": "월급날 기준", "depends_on": ["R2"]}],
    "oneshot-tests": [{"id": "T1", "test_name": "test_spending_adds_to_the_total", "given": "빈 가계부",
                       "when": "지출 3000원을 적으면", "then": "합계가 3000원이다", "depends_on": ["R1", "P1"]},
                      {"id": "T2", "test_name": "test_a_month_lists_its_spendings", "given": "두 달의 지출",
                       "when": "지난 달을 고르면", "then": "그 달 지출만 보인다", "depends_on": ["R2", "P2"]}],
}


def fenced(name: str, value) -> str:
    return f"```{name}\n{json.dumps(value, ensure_ascii=False)}\n```\n"


def answer(design=DESIGN, extra: str = "") -> str:
    return "설계했다.\n\n" + "".join(fenced(k, v) for k, v in design.items()) + extra


@pytest.fixture
def world(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    identity = {f"GIT_{who}_{what}": value for who in ("AUTHOR", "COMMITTER")
                for what, value in (("NAME", "Oneshot test"), ("EMAIL", "oneshot@example.test"))}
    with patch.dict(os.environ, identity):
        for args in (["init", "-b", oneshot.BRANCH], ["commit", "--allow-empty", "-m", "base"]):
            subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True)
        specs.save({"id": oneshot.BRANCH, "repo": "budget", "rev": 1, "goal": "가계부", "out": [], "done": [],
                    "state": "작업 중", "worktree": str(tree), "history": [{"ts": 1, "state": "작업 중"}],
                    "oneshot": {"version": 1, "run": "0123456789abcdef", "stage": "spec", "spend": oneshot.ledger(None),
                                "sentence": "가계부 만들어줘", "intent": {"restated": "가계부를 만든다.", "kind": "webapp"},
                                "profile": PROFILE}})
        with patch.object(oneshot, "RUNS", tmp_path / "oneshot"), patch.object(oneshot, "codex_role", lambda: False), \
             patch.object(oneshot_spec, "start", lambda repo, work, *a, **f: oneshot_spec.store(repo).put(
                 **f, working=work, stopped=None)):
            yield tree


def replies(*texts):
    queue = list(texts)
    return patch.object(oneshot, "ask", lambda *a: (queue.pop(0), {"cost_usd": 0.1}, False))


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


# -- S2 -------------------------------------------------------------------------------

def test_the_conversation_ends_only_when_nothing_is_undecided_and_every_original_has_a_scenario(world):
    undecided = [{**REQS[0]}, {**REQS[1], "status": "undecided"}]
    ask = {"questions": [{"question": "지난 달도 보나요?", "options": [{"label": "예"}, {"label": "아니요"}]}]}
    with replies(fenced("oneshot-spec", {"requirements": undecided, "glossary": GLOSSARY}) + fenced("choices", ask)):
        oneshot_spec.talk("budget")
    assert o()["stage"] == "spec" and "R2" in o()["open"] and o()["talk"][-1]["choices"] == ask
    bare = [{**REQS[0], "postconditions": []}]
    with replies(fenced("oneshot-spec", {"requirements": bare, "glossary": GLOSSARY})):
        oneshot_spec.talk("budget")
    assert o()["stage"] == "spec" and "시나리오가 없는" in o()["open"]
    with replies(fenced("oneshot-spec", {"requirements": REQS, "glossary": GLOSSARY})):
        oneshot_spec.talk("budget")
    assert o()["stage"] == "design" and o()["working"] == "design"
    assert specs.load("budget", oneshot.BRANCH)["requirements"][0]["id"] == "R1"


def test_a_term_the_owner_used_cannot_be_renamed_and_four_questions_are_refused(world):
    oneshot_spec.saved("budget", oneshot={"glossary": GLOSSARY, "sentence": "지출을 적는 가계부"})
    renamed = fenced("oneshot-spec", {"requirements": REQS, "glossary": [{"term": "비용", "meaning": "쓴 돈"}]})
    with replies(renamed, renamed):
        oneshot_spec.talk("budget")
    assert o()["stopped"]["reason"] == "block" and "지출" in o()["stopped"]["detail"]
    oneshot_spec.saved("budget", oneshot={"stopped": None})
    four = {"questions": [{"question": f"질문 {n}", "options": [{"label": "예"}]} for n in range(4)]}
    block = fenced("oneshot-spec", {"requirements": REQS, "glossary": GLOSSARY}) + fenced("choices", four)
    with replies(block, block):
        oneshot_spec.talk("budget")
    assert "세 개까지" in o()["stopped"]["detail"]


def test_saying_is_refused_while_a_turn_runs_and_appends_the_owners_words(world):
    oneshot_spec.saved("budget", oneshot={"working": "talk"})
    with pytest.raises(HTTPException):
        oneshot_spec.say("budget", oneshot_spec.Say(text="네"))
    oneshot_spec.saved("budget", oneshot={"working": None})
    oneshot_spec.say("budget", oneshot_spec.Say(text="지난 달도 본다"))
    assert o()["talk"][-1] == {**o()["talk"][-1], "role": "owner", "text": "지난 달도 본다"} and o()["working"] == "talk"
    with pytest.raises(HTTPException):
        oneshot_spec.status("../escape")


# -- S3 checks -------------------------------------------------------------------------

def design_with(changes: dict):
    found = json.loads(json.dumps(DESIGN))
    for (block, n, key), value in changes.items():
        found[block][n][key] = value
    return found


@pytest.mark.parametrize("change, said", [
    ({("oneshot-api", 0, "depends_on"): ["R9"]}, "없는 id"),
    ({("oneshot-entities", 0, "depends_on"): ["P1"], ("oneshot-api", 0, "depends_on"): ["R1", "E1"]}, "돌고 돈다"),
    ({("oneshot-api", 1, "depends_on"): ["E1"]}, "범위 밖"),
    ({("oneshot-tests", 1, "depends_on"): ["R1", "P1"]}, "확인할 테스트가 없다"),
    ({("oneshot-tests", 0, "depends_on"): ["R1"]}, "부르는 엔드포인트"),
    ({("oneshot-flows", 0, "depends_on"): []}, "엔드포인트를 `depends_on`"),
    ({("oneshot-entities", 0, "name"): "Expense"}, "쓰지 않기로"),
    ({("oneshot-api", 0, "path"): "/login"}, "로그인 없음"),
    ({("oneshot-tests", 0, "test_name"): "spending adds"}, "test_"),
])
def test_each_design_check_refuses(change, said):
    with pytest.raises(ValueError, match=said):
        sheet.checked(sheet.parsed(design_with(change)), REQS, GLOSSARY, PROFILE)


def test_a_valid_design_passes_and_a_password_profile_needs_an_auth_endpoint():
    design = sheet.parsed(DESIGN)
    sheet.checked(design, REQS, GLOSSARY, PROFILE)
    with pytest.raises(ValueError, match="인증 엔드포인트가 없다"):
        sheet.checked(design, REQS, GLOSSARY, {**PROFILE, "auth": "password"})


@pytest.mark.parametrize("text, pattern", [
    ("목록이 표시되어진다", "이중 피동"),
    ("사용자의 가계부의 이번 달의 합계를 본다", "의 연쇄"),
    ("지출에 대해 알려 준다", "번역투 연결"),
    ("서버를 통해 저장한다", "번역투 연결"),
    ("합계를 보여 주는 것이다", "명사형 끝맺음"),
    ("항목들을 지운다", "사물에 붙은 들"),
    ("지출(expense)을 적는다", "괄호 속 영어"),
])
def test_each_translationese_pattern_is_counted_and_named(text, pattern):
    assert pattern in [name for name, _ in sheet.translationese(text)]


def test_clean_korean_passes_the_translationese_check():
    for text in ("지출을 적으면 이번 달 합계에 더해진다", "사용자들이 같은 가계부를 함께 쓴다", "목록을 만들 수 있다",
                 "고른 달의 지출 목록을 돌려준다"):
        assert sheet.translationese(text) == [], text


# -- S3 turn and the sheet ---------------------------------------------------------------

def test_the_design_turn_is_asked_again_once_for_translationese_then_shown_with_warnings(world):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY, "stage": "design"})
    awkward = design_with({("oneshot-api", 0, "does"): "지출에 대해 적는다"})
    heard = []

    def ask(*a):
        heard.append(a[3])
        return answer(awkward), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", ask):
        oneshot_spec.design("budget")
    assert "번역체" in heard[1] and o()["stage"] == "r1" and any("번역체" in w for w in o()["r1"]["warnings"])
    assert o()["design"]["api"][0]["does"] == "지출에 대해 적는다"   # counted and named, never rewritten


def test_the_sheet_keeps_its_order_and_folds_past_the_length_budget(world):
    many = json.loads(json.dumps(DESIGN))
    many["oneshot-assumptions"] = [{**DESIGN["oneshot-assumptions"][0], "id": f"A{n}"} for n in range(1, 10)]
    design = sheet.parsed(many)
    assert any("가정이 9개" in w for w in sheet.soft(design, REQS, GLOSSARY))
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY, "design": design})
    text = sheet.sheet(specs.load("budget", oneshot.BRANCH))
    order = ["> 가계부 만들어줘", "## 에이전트가 정한 가정", "## 나중에 되돌리기 어려운 것", "## 데이터와 API",
             "## 시나리오와 확인할 테스트", "<details>"]
    assert [text.index(h) for h in order] == sorted(text.index(h) for h in order)
    assert "그 밖의 가정 2개" in text and "`Spending.id`" in text and "test_spending_adds_to_the_total" in text
    assert json.loads(sheet.openapi(specs.load("budget", oneshot.BRANCH)))["paths"]["/spendings"]["post"]["operationId"] == "P1"


# -- R1 decisions ---------------------------------------------------------------------------

def at_r1(world):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stage": "r1", "design": sheet.parsed(DESIGN),
        "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": []}})


def everything(**edits) -> dict:
    items = sheet.decidable(sheet.parsed(DESIGN), REQS)
    return {i: ({"edit": edits[i]} if i in edits else {"approve": True}) for i in items}


def test_a_submission_with_any_item_undecided_is_refused(world):
    at_r1(world)
    partial = everything()
    partial.pop("F1")   # a folded item counts as much as an unfolded one
    with pytest.raises(HTTPException) as refused:
        oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions=partial))
    assert refused.value.status_code == 400 and "F1" in refused.value.detail
    with pytest.raises(HTTPException):
        oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions={**everything(), "Z9": {"approve": True}}))


def test_approving_every_item_writes_and_commits_the_design_and_moves_to_tests(world):
    at_r1(world)
    oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions=everything()))
    assert o()["stage"] == "tests" and o()["design_head"]
    assert (world / "docs/design/backend.md").read_text(encoding="utf-8").startswith("# 백엔드 설계")
    shown = subprocess.run(["git", "show", "--stat", "HEAD"], cwd=world, capture_output=True, text=True).stdout
    assert "docs/design/api.yaml" in shown and "docs/design/backend.md" in shown


@pytest.mark.parametrize("edited, reached", [
    ("R1", {"R1", "R2", "A1", "E1", "P1", "P2", "F1", "T1", "T2"}),   # A1 rests on R2, which rests on R1
    ("P1", {"P1", "F1", "T1"}),
    ("A1", {"A1", "P2", "T2"}),
])
def test_an_edit_reaches_everything_built_on_it(edited, reached):
    assert sheet.dependents(sheet.parsed(DESIGN), REQS, {edited}) == reached


def test_an_edited_endpoint_regenerates_its_dependents_and_resets_their_approvals(world):
    at_r1(world)
    oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions=everything(P1="응답 필드를 sum 으로 바꿔라")))
    assert o()["working"] == "redesign"
    renamed = design_with({("oneshot-api", 0, "response"): {"sum": "int"},
                            ("oneshot-tests", 0, "then"): "sum 이 3000원이다"})
    with replies(answer(renamed)):
        oneshot_spec.redesign("budget", {"P1": "응답 필드를 sum 으로 바꿔라"}, ["F1", "P1", "T1"])
    r1 = o()["r1"]
    assert r1["round"] == 2 and r1["marks"] == {"P1": "edited", "T1": "consequence", "F1": "again"}
    assert set(r1["decisions"]) == set(everything()) - {"P1", "T1", "F1"}
    assert o()["design"]["api"][0]["response"] == {"sum": "int"}


def test_an_edited_scenario_may_change_the_requirements_in_the_same_turn(world):
    at_r1(world)
    changed = [{**REQS[0], "text": "지출을 적으면 오늘 합계에 더해진다"}, REQS[1]]
    with replies(answer(DESIGN, fenced("oneshot-spec", {"requirements": changed, "glossary": GLOSSARY}))):
        oneshot_spec.redesign("budget", {"R1": "이번 달이 아니라 오늘"}, sorted(
            sheet.dependents(sheet.parsed(DESIGN), REQS, {"R1"})))
    assert specs.load("budget", oneshot.BRANCH)["requirements"][0]["text"] == "지출을 적으면 오늘 합계에 더해진다"
    assert o()["r1"]["marks"]["R1"] == "edited" and o()["r1"]["marks"]["T1"] == "again"


def test_a_stopped_edit_turn_resumes_to_the_sheet_and_a_cut_turn_is_recovered(world):
    at_r1(world)
    oneshot_spec.store("budget").stop("block", "블록이 두 번 거부됐다")
    oneshot_spec.resume("budget")
    assert o()["stopped"] is None and o()["stage"] == "r1"
    oneshot_spec.saved("budget", oneshot={"working": "redesign", "inflight": {"role": "design", "codex": False,
                                                                              "sent": 1.0}})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart" and o()["inflight"] is None and o()["spend"]["unknown"] == 1
