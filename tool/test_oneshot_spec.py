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
from fastapi.responses import JSONResponse

from main import oneshot, oneshot_sheet as sheet, oneshot_spec, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse

PROFILE = {"auth": "none", "data_store": "sqlite", "backend": "fastapi", "visibility": "private",
           "docs_language": "ko"}
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
                    "oneshot": {"version": oneshot.VERSION, "run": "0123456789abcdef", "stage": "spec", "spend": oneshot.ledger(None),
                                "sentence": "가계부 만들어줘", "intent": {"restated": "가계부를 만든다.", "kind": "webapp"},
                                "profile": PROFILE}})
        with patch.object(oneshot, "RUNS", tmp_path / "oneshot"), patch.object(oneshot, "codex_role", lambda role="", store=None: False), \
             patch("main.oneshot_build.begin", lambda repo: None), \
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
    ({("oneshot-entities", 0, "relations"): 3}, "문자열 목록"),
    ({("oneshot-api", 0, "errors"): 3}, "문자열 목록"),
    ({("oneshot-api", 1, "method"): "POST"}, "같은 `POST /spendings`"),
    ({("oneshot-api", 0, "path"): "/a|b"}, "/ 로 시작"),
    ({("oneshot-entities", 0, "fields"): [{"name": "id", "type": "int", "key": "false"}]}, "true 나 false"),
])
def test_each_design_check_refuses(change, said):
    with pytest.raises(ValueError, match=said):
        sheet.checked(sheet.parsed(design_with(change)), REQS, GLOSSARY, PROFILE)


def test_a_valid_design_passes_and_a_password_profile_needs_an_auth_endpoint():
    design = sheet.parsed(DESIGN)
    sheet.checked(design, REQS, GLOSSARY, PROFILE)
    with pytest.raises(ValueError, match="인증 엔드포인트가 없다"):
        sheet.checked(design, REQS, GLOSSARY, {**PROFILE, "auth": "password"})
    authors = sheet.parsed(design_with({("oneshot-api", 0, "path"): "/authors"}))
    sheet.checked(authors, REQS, GLOSSARY, PROFILE)   # a word containing `auth` is not a sign-in path
    with pytest.raises(ValueError, match="인증 엔드포인트가 없다"):
        sheet.checked(authors, REQS, GLOSSARY, {**PROFILE, "auth": "password"})


def test_a_cycle_through_the_requirements_and_a_malformed_avoid_list_are_refused():
    cyclic = [{**REQS[0], "depends_on": ["R2"]}, REQS[1]]
    with pytest.raises(ValueError, match="돌고 돈다"):
        sheet.checked(sheet.parsed(DESIGN), cyclic, GLOSSARY, PROFILE)
    with pytest.raises(ValueError, match="문자열 목록"):
        sheet.requirement_block({"requirements": REQS, "glossary": [{**GLOSSARY[0], "avoid": 3}]})


@pytest.mark.parametrize("bad, named", [
    ([{**REQS[0], "owner": "x"}], "R1: 모르는 키 — owner"),
    ([REQS[0], REQS[0]], "R1: id 가 겹친다"),
    ([{**REQS[0], "depends_on": ["R7"]}], "R1: `depends_on` 이 정의되지 않은 id 를 가리킨다 — R7"),
    ([{**REQS[0], "source": {"kind": "hunch", "locator": "x"}}], "R1: `source`"),
    ([{**REQS[0], "status": "maybe"}], "R1: `status`"),
    ([{**REQS[0], "id": "A1"}], "`id` 는 R1, R2"),
    ([{**REQS[0], "verify": [{"item": "x"}]}], "R1: `verify`"),
    ([{**REQS[0], "postconditions": [""]}], "R1: `postconditions`"),
])
def test_a_malformed_requirement_is_refused_with_the_item_and_key_named(bad, named):
    with pytest.raises(ValueError) as caught:
        sheet.requirement_block({"requirements": bad, "glossary": GLOSSARY})
    assert named in str(caught.value)


def test_a_requirement_reads_back_normalized():
    loose = {**REQS[1], "text": "  지난 달   지출을 고르면\n그 달 목록이 보인다 ", "verify": [{"item": " 목록 ", "expected": "보인다"}]}
    reqs, _ = sheet.requirement_block({"requirements": [REQS[0], loose], "glossary": GLOSSARY})
    assert reqs[0] == REQS[0]
    assert reqs[1]["text"] == REQS[1]["text"] and reqs[1]["verify"] == [{"item": "목록", "expected": "보인다"}]


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
                 "고른 달의 지출 목록을 돌려준다", "핸들을 잡는다", "아들이 온다"):
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


# -- Review round 1 of #320 ----------------------------------------------------------------

def test_the_end_rule_reads_the_sources_as_stored_and_an_original_cannot_be_dropped(world):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY})
    swapped = [{**REQS[0], "source": {"kind": "policy", "locator": "x"}, "postconditions": []},
               {**REQS[1], "source": {"kind": "original", "locator": "x"}, "depends_on": []}]
    with replies(fenced("oneshot-spec", {"requirements": swapped, "glossary": GLOSSARY})):
        oneshot_spec.talk("budget")
    assert o()["stage"] == "spec" and "R1" in o()["open"]
    dropped = fenced("oneshot-spec", {"requirements": [{**REQS[1], "depends_on": []}], "glossary": GLOSSARY})
    with replies(dropped, dropped):
        oneshot_spec.talk("budget")
    assert "지웠다: R1" in o()["stopped"]["detail"]


def test_the_ending_turn_drops_its_question_and_keeps_no_blocks_in_the_conversation(world):
    ask = {"questions": [{"question": "더 볼까요?", "options": [{"label": "예"}]}]}
    with replies("다 정했다.\n" + fenced("oneshot-spec", {"requirements": REQS, "glossary": GLOSSARY}) +
                 fenced("choices", ask)):
        oneshot_spec.talk("budget")
    last = o()["talk"][-1]
    assert o()["stage"] == "design" and last["choices"] is None and last["text"] == "다 정했다."


def test_a_redesign_may_not_move_items_outside_its_reach_or_rename_the_owners_term(world):
    at_r1(world)
    oneshot_spec.saved("budget", oneshot={"sentence": "지출을 적는 가계부"})
    outside = answer(design_with({("oneshot-api", 0, "response"): {"sum": "int"},
                                  ("oneshot-assumptions", 0, "what"): "달은 월급날로 나눈다"}))
    with replies(outside, outside):
        oneshot_spec.redesign("budget", {"P1": "sum 으로"}, ["F1", "P1", "T1"])
    assert "범위 밖" in o()["stopped"]["detail"] and "A1" in o()["stopped"]["detail"]
    renamed = answer(DESIGN, fenced("oneshot-spec", {"requirements": REQS,
                                                     "glossary": [{"term": "cost", "meaning": "쓴 돈"}]}))
    with replies(renamed, renamed):
        oneshot_spec.redesign("budget", {"R1": "고쳐라"}, sorted(sheet.dependents(sheet.parsed(DESIGN), REQS, {"R1"})))
    assert "지출" in o()["stopped"]["detail"]


def test_a_model_answer_of_the_wrong_type_is_refused_and_an_unforeseen_failure_stops(world):
    oneshot_spec.saved("budget", requirements=REQS, oneshot={"glossary": GLOSSARY, "stage": "design"})
    broken = answer(design_with({("oneshot-entities", 0, "relations"): 3}))
    with replies(broken, broken):
        oneshot_spec.design("budget")
    assert o()["stopped"]["reason"] == "block" and "문자열 목록" in o()["stopped"]["detail"]
    store = oneshot_spec.store("budget")
    oneshot.start_worker(store, "design", lambda repo: (_ for _ in ()).throw(RuntimeError("boom")), ("budget",), {})
    oneshot._workers[store.key].join()
    assert o()["working"] is None and "RuntimeError: boom" in o()["stopped"]["detail"]


def test_a_first_turn_the_hand_over_never_started_is_left_resumable(world, tmp_path):
    oneshot.RUNS.mkdir(parents=True, exist_ok=True)
    oneshot_spec.recover()
    assert not o().get("stopped")   # the run's commit point is not written: the bootstrap still owns it
    (oneshot.RUNS / "0123456789abcdef.json").write_text(json.dumps({"committed": True}), encoding="utf-8")
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    oneshot_spec.resume("budget")
    assert o()["working"] == "talk"


def test_the_approval_commits_only_the_design_files_and_the_sheet_escapes_what_it_shows(world):
    at_r1(world)
    (world / "other.txt").write_text("staged meanwhile", encoding="utf-8")
    subprocess.run(["git", "add", "other.txt"], cwd=world, check=True)
    oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions=everything()))
    shown = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=world, capture_output=True,
                           text=True).stdout.split()
    assert shown == ["docs/design/api.yaml", "docs/design/backend.md"]
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=world, capture_output=True, text=True)
    assert staged.stdout.split() == ["other.txt"]
    odd = design_with({("oneshot-flows", 0, "steps"): [{"from": "사용자 앱", "to": "서버", "say": "금액;\n```보낸다",
                                                         "api": "P1"}]})
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(odd)})
    text = sheet.sheet(specs.load("budget", oneshot.BRANCH))
    assert "    사용자_앱->>서버: 금액, '''보낸다" in text


# -- Review round 2 of #320 ----------------------------------------------------------------

def test_a_requirement_a_redesign_adds_is_shown_and_must_be_decided_even_without_a_scenario(world):
    at_r1(world)
    rule = {"id": "R3", "text": "기록은 90일 동안 둔다", "source": {"kind": "policy", "locator": "x"},
            "status": "assumed", "invariants": ["90일 동안 둔다"], "depends_on": ["R1"]}
    reach = sorted(sheet.dependents(sheet.parsed(DESIGN), REQS, {"R1"}))
    with replies(answer(DESIGN, fenced("oneshot-spec", {"requirements": [*REQS, rule], "glossary": GLOSSARY}))):
        oneshot_spec.redesign("budget", {"R1": "기록을 얼마나 둘지 정해라"}, reach)
    spec = specs.load("budget", oneshot.BRANCH)
    assert spec["requirements"][2]["source"]["kind"] == "decision" and o()["r1"]["marks"]["R3"] == "consequence"
    assert "기록은 90일 동안 둔다 (지켜야 할 조건" in sheet.sheet(spec)
    with pytest.raises(HTTPException, match="R3"):
        oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions={k: {"approve": True} for k in reach}))


def test_a_cycle_among_the_requirements_is_refused_in_the_conversation_where_it_can_be_fixed(world):
    cyclic = fenced("oneshot-spec", {"requirements": [{**REQS[0], "depends_on": ["R2"]}, REQS[1]],
                                     "glossary": GLOSSARY})
    with replies(cyclic, fenced("oneshot-spec", {"requirements": REQS, "glossary": GLOSSARY})):
        oneshot_spec.talk("budget")
    assert o()["stage"] == "design"


def test_a_design_file_that_is_a_link_stops_the_approval_before_anything_is_written(world, tmp_path):
    at_r1(world)
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")
    (world / "docs/design").mkdir(parents=True)
    try:
        (world / "docs/design/backend.md").symlink_to(outside)
    except OSError:
        pytest.skip("this account cannot make symbolic links")
    oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions=everything()))
    assert o()["stopped"]["reason"] == "folder" and outside.read_text(encoding="utf-8") == "keep"


def test_each_submission_records_how_its_sheet_was_read_and_the_rail_shows_what_waits(world):
    at_r1(world)
    oneshot.RUNS.mkdir(parents=True, exist_ok=True)
    (oneshot.RUNS / "0123456789abcdef.json").write_text(json.dumps({"committed": True, "spec": {"repo": "budget"}}),
                                                         encoding="utf-8")
    assert oneshot_spec.listing() == {"specs": [{"repo": "budget", "stage": "r1", "working": None, "stopped": None,
                                                 "waits": True}]}
    oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions=everything(P1="응답 필드를 sum 으로 바꿔라"),
                                                         reading={"seconds": 92.04, "opened": ["흐름", 3, "흐름"]}))
    assert o()["reading"] == {"r1": [{"round": 1, "seconds": 92.0, "opened": ["흐름"], "edited": ["P1"],
                                      "edits": {"P1": "응답 필드를 sum 으로 바꿔라"}}]}
    assert oneshot_spec.listing()["specs"][0]["waits"] is False   # the edit turn runs


@pytest.mark.parametrize("seconds", [1e309, 10**400, float("nan"), True, "90"])
def test_a_reading_time_json_cannot_carry_is_kept_as_unmeasured(seconds):
    entry = oneshot_spec.read({"r1": {"round": 1}}, "r1", {"seconds": seconds}, [])["r1"][0]
    assert entry["seconds"] is None
    JSONResponse(entry)   # the state view sends the reading back: a non-finite number would fail it
