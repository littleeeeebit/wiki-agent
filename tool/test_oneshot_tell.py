"""The owner speaks to a run while it is built (`docs/plans/oneshot/14-owner-speaks-mid-build.md`)."""

import copy
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_build, oneshot_sheet as sheet, oneshot_spec, oneshot_tell, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import DESIGN, GLOSSARY, REQS, world  # noqa: F401 — `world` is a fixture


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def at(stage: str, **more) -> None:
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stage": stage, "design": sheet.parsed(DESIGN), "design_head": "d" * 40, "working": None,
        **more})


def say(text: str) -> dict:
    return oneshot_tell.tell("budget", oneshot_tell.Tell(text=text))


def test_the_owners_words_wait_for_the_next_step_from_the_first_build_turn_to_r3(world):  # noqa: F811 — the imported fixture, requested by name
    for stage in ("design", "pr"):   # the design talk and R1 take the words before; the pull request is published after
        at(stage)
        with pytest.raises(HTTPException) as refused:
            say("모델을 바꿔")
        assert refused.value.status_code == 409
    at("build", working="build")   # a build turn the server holds, none attached here: the words wait
    say("  번역 모델을 더 큰 것으로 바꿔  ")
    assert o()["tell_pending"] == [{"text": "번역 모델을 더 큰 것으로 바꿔", "stage": "build"}]
    assert o()["told"][0]["into_turn"] is False and o()["working"] == "build"   # nothing else started while it runs
    store = oneshot_spec.store("budget")
    assert not oneshot_tell.diverted(store, "retell")
    assert oneshot.diverted(store, "measure") and o()["working"] == "retell"   # the next step turns aside first
    at("board", tell_pending=[{"text": "더 크게", "stage": "r2"}])   # said while the screens are designed: they wait
    assert not oneshot.diverted(store, "frontend")
    at("split", tell_pending=[{"text": "더 크게", "stage": "review"}])   # said in the backend's review: taken in first
    assert oneshot.diverted(store, "split") and o()["working"] == "retell"
    at("pr", tell_pending=[{"text": "더 크게", "stage": "r3"}])   # said at R3: never published past
    assert oneshot.diverted(store, "pr")
    at("measure")
    say("소리를 줄여")   # an idle run takes them in at once
    assert o()["working"] == "retell"
    at("measure")
    with patch.object(oneshot_spec, "start", side_effect=HTTPException(503, "서버가 종료 중이다")):
        say("소리를 줄여")   # a start refused stops the run with its reason: no `working` is left without a worker
    assert o()["working"] is None and o()["stopped"]["reason"] == "block"


def retold(made, reqs, stage="build", tests_head="t" * 40, during=None, committed="h" * 40, **more):
    at(stage, tests_head=tests_head, tell_pending=[{"text": "번역 모델을 바꿔", "stage": stage}], attempts=3,
       working="retell", **more)
    sent, recorded, asked = [], [], []

    def turned(store, role, *a, **k):
        asked.append(oneshot.role_config(role, store))
        if during:
            say(during)
        return made, reqs, GLOSSARY, []
    with patch.object(oneshot, "turned", turned), patch.object(oneshot_spec, "design_text", lambda spec: ""), \
         patch.object(oneshot_spec, "design_committed",
                      lambda repo, message, spec: recorded.append(spec["requirements"]) or committed), \
         patch.object(oneshot_build, "send", lambda *a: sent.append(a)), \
         patch.object(oneshot_build, "tests_text", lambda spec: "TESTS"), \
         patch.object(oneshot_build, "build_text", lambda spec: "BUILD"):
        oneshot_tell.retell("budget")
    return sent, recorded, asked


def changed_reqs() -> list:
    reqs = copy.deepcopy(REQS)
    reqs[0]["text"] += " 번역은 더 큰 모델로 한다."
    return reqs


def test_a_retold_design_is_committed_then_recorded_and_built_on_from_the_code_there_is(world):  # noqa: F811 — the imported fixture, requested by name
    sent, recorded, asked = retold(sheet.parsed(DESIGN), changed_reqs(), during="그리고 소리도 줄여",
                                   design_author={"model": "author-model", "effort": "high"})
    assert asked[0]["model"] == "author-model"   # the model that wrote the design rewrites it
    assert recorded[0][0]["text"].endswith("더 큰 모델로 한다.") and sent == [("budget", "tests", "TESTS")]
    assert o()["stage"] == "tests" and o()["retell_to"] == "build" and o()["design_head"] == "h" * 40
    assert o()["attempts"] == 0 and o()["retold"][0]["changed"] and o()["retold"][0]["words"] == ["번역 모델을 바꿔"]
    assert o()["tell_pending"] == [{"text": "그리고 소리도 줄여", "stage": "build"}]   # said during the turn: next
    assert specs.load("budget", oneshot.BRANCH)["requirements"][0]["text"].endswith("더 큰 모델로 한다.")


def test_a_design_that_could_not_be_committed_leaves_the_record_as_the_files_are(world):  # noqa: F811 — the imported fixture, requested by name
    sent, recorded, _ = retold(sheet.parsed(DESIGN), changed_reqs(), committed=None)
    assert recorded and not sent and specs.load("budget", oneshot.BRANCH)["requirements"] == REQS
    assert o()["design_head"] == "d" * 40 and o()["tell_pending"] and not o().get("retold")   # the words still wait


def test_words_that_change_no_item_go_straight_to_the_build_and_a_late_run_to_the_frontend_build(world):  # noqa: F811 — the imported fixture, requested by name
    sent, recorded, _ = retold(sheet.parsed(DESIGN), REQS)
    assert not recorded and sent == [("budget", "build", "BUILD")] and o()["retold"][0]["changed"] == []
    with patch("main.oneshot_front_build.front_text", lambda spec: "FRONT"), \
         patch("main.oneshot_review.later_text", lambda o, kinds: ""):
        sent, _, _ = retold(sheet.parsed(DESIGN), REQS, stage="pc")
    assert sent[0][:2] == ("budget", "build-front") and sent[0][2].startswith("FRONT")
    assert "번역 모델을 바꿔" in sent[0][2] and sent[0][2].count(oneshot_tell.OWNER) == 1   # the words, told once
    sent, _, _ = retold(sheet.parsed(DESIGN), REQS, stage="tests", retell_to="build-front")
    assert sent[0][:2] == ("budget", "tests") and o()["retell_to"] == "build-front"   # the frontend's, kept


def test_a_retold_tests_turn_may_find_a_planned_test_passing_but_never_skipped(world):  # noqa: F811 — the imported fixture, requested by name
    at("tests")
    spec = specs.load("budget", oneshot.BRANCH)
    found = [("test_budget.py", t["test_name"], "", "passed") for t in oneshot_build.planned(spec)]
    assert oneshot_build.red(spec, found) and not oneshot_build.red(spec, found, again=True)
    skipped = [(*found[0][:3], "skipped"), *found[1:]]
    assert "건너뛴" in oneshot_build.red(spec, skipped, again=True)
