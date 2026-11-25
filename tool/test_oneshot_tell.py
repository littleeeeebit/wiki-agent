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


def test_the_owners_words_wait_for_the_next_step_from_the_first_build_turn_to_the_end(world):  # noqa: F811 — the imported fixture, requested by name
    at("design")
    with pytest.raises(HTTPException) as refused:   # the design talk and R1 take the owner's words before it
        oneshot_tell.tell("budget", oneshot_tell.Tell(text="모델을 바꿔"))
    assert refused.value.status_code == 409
    at("build", working="build")   # a build turn the server holds, none attached here: the words wait
    oneshot_tell.tell("budget", oneshot_tell.Tell(text="  번역 모델을 더 큰 것으로 바꿔  "))
    assert o()["tell_pending"] == ["번역 모델을 더 큰 것으로 바꿔"] and o()["told"][0]["into_turn"] is False
    assert o()["working"] == "build"   # nothing else started while it runs
    store = oneshot_spec.store("budget")
    assert not oneshot_tell.diverted(store, "retell")
    assert oneshot.diverted(store, "measure") and o()["working"] == "retell"   # the next step turns aside first
    at("board", tell_pending=["더 크게"])   # the screens are being designed with the owner: the words wait
    assert not oneshot.diverted(store, "frontend")
    at("measure", working=None)
    oneshot_tell.tell("budget", oneshot_tell.Tell(text="소리를 줄여"))   # an idle run takes them in at once
    assert o()["working"] == "retell"


def retold(world, made, reqs, stage="build", tests_head="t" * 40):  # noqa: F811 — the imported fixture, requested by name
    at(stage, tests_head=tests_head, tell_pending=["번역 모델을 바꿔"], attempts=3)
    sent, recorded = [], []
    with patch.object(oneshot, "turned", lambda *a, **k: (made, reqs, GLOSSARY, [])), \
         patch.object(oneshot_spec, "design_text", lambda spec: ""), \
         patch.object(oneshot_spec, "design_committed", lambda repo, message: recorded.append(message) or "h" * 40), \
         patch.object(oneshot_build, "send", lambda *a: sent.append(a)), \
         patch.object(oneshot_build, "tests_text", lambda spec: "TESTS"), \
         patch.object(oneshot_build, "build_text", lambda spec: "BUILD"):
        oneshot_tell.retell("budget")
    return sent, recorded


def test_a_retold_design_is_committed_and_built_on_from_the_code_there_is(world):  # noqa: F811 — the imported fixture, requested by name
    reqs = copy.deepcopy(REQS)
    reqs[0]["text"] += " 번역은 더 큰 모델로 한다."
    sent, recorded = retold(world, sheet.parsed(DESIGN), reqs)
    assert recorded and sent == [("budget", "tests", "TESTS")]   # the tests the change touches, then the build
    assert o()["stage"] == "tests" and o()["retell_to"] == "build" and o()["design_head"] == "h" * 40
    assert o()["tell_pending"] == [] and o()["attempts"] == 0 and o()["retold"][0]["changed"]
    assert specs.load("budget", oneshot.BRANCH)["requirements"][0]["text"].endswith("더 큰 모델로 한다.")


def test_words_that_change_no_item_go_straight_to_the_build_and_a_late_run_to_the_frontend_build(world):  # noqa: F811 — the imported fixture, requested by name
    sent, recorded = retold(world, sheet.parsed(DESIGN), REQS)
    assert not recorded and sent == [("budget", "build", "BUILD")] and o()["retold"][0]["changed"] == []
    with patch("main.oneshot_front_build.build_front_text", lambda spec: "FRONT"):
        sent, _ = retold(world, sheet.parsed(DESIGN), REQS, stage="pc")
    assert sent[0][:2] == ("budget", "build-front") and sent[0][2].startswith("FRONT")
    assert "번역 모델을 바꿔" in sent[0][2]   # the words the design follows now go with it


def test_a_retold_tests_turn_may_find_a_planned_test_passing_but_never_skipped(world):  # noqa: F811 — the imported fixture, requested by name
    at("tests")
    spec = specs.load("budget", oneshot.BRANCH)
    found = [("test_budget.py", t["test_name"], "", "passed") for t in oneshot_build.planned(spec)]
    assert oneshot_build.red(spec, found) and not oneshot_build.red(spec, found, again=True)
    skipped = [(*found[0][:3], "skipped"), *found[1:]]
    assert "건너뛴" in oneshot_build.red(spec, skipped, again=True)
