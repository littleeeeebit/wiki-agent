"""oneshot step 9e6a: R3 of a PC app — the sheet's `PC 앱` section with where each deferred check passed, the owner's
desktop round's included; the approval waits before the pull request, which is the next step's; a run an earlier
version held before R3 opens it by the guard.

See `docs/plans/oneshot/9-kinds.md` ("R3 and S8", "Steps and their stops").
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot_brief as brief, oneshot_critique as critique, oneshot_pc as pc, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_pc import BUILD, approval, held  # noqa: F401 — `held` is a fixture
from test_oneshot_spec import o, world  # noqa: F401 — `world` is a fixture

PNG = b"\x89PNG\r\n\x1a\n"


def shot(n, name):
    return f"/look/{n}/{name}"


def looked(n: int, findings=()) -> dict:
    shots = critique.folder("budget", n)
    shots.mkdir(parents=True, exist_ok=True)
    for state in critique.STATES:
        (shots / f"S1-window-{state}.png").write_bytes(PNG)
    return {"round": n, "head": BUILD["head"], "findings": list(findings), "freeze": BUILD["freeze"]}


@pytest.fixture
def passed_round(held, monkeypatch):  # noqa: F811 — the imported fixture, requested by name
    """A PC app whose look passed twice clean and whose owner's desktop round passed: R3 opens on it."""

    left = {"severity": "low", "screen": "S1", "viewport": "window", "state": "full", "what": "여백이 좁다", "kind": "desktop"}
    screens = [{**s, "purpose": "번역한다", "surface": "window", "features": []} for s in o()["front"]["screens"]]
    oneshot_spec.saved("budget", oneshot={
        "looks": [looked(1), looked(2)], "frequency": {}, "front": {**o()["front"], "screens": screens},
        "critiques": [{"look": 2, "head": BUILD["head"], "kind": "desktop", "clean": True, "findings": found}
                      for found in ([], [left])]})
    oneshot_spec.saved("budget", oneshot={"pc": {"status": "passed", "head": BUILD["head"], "freeze": BUILD["freeze"],
                                                 "front_build": "b1", "passed": ["O2", "O3"]}})
    real = brief.shown
    with patch.object(oneshot_spec.sheet, "sheet", lambda spec: ""), \
            patch.object(oneshot_spec.sheet, "decidable", lambda design, reqs: []), \
            patch.object(brief, "shown", lambda spec, gate="r1": {} if gate == "r1" else real(spec, gate)), \
            patch.object(oneshot_spec, "briefed", lambda repo, gate="r1": oneshot_spec.saved(repo, oneshot={"working": None})):
        yield held


def opened():
    """The guarded entry, as the owner's round's pass calls it: R3's sheet, its brief written at once."""

    if callable(then := pc.onward("budget", {})):
        then()
    return o()


def test_the_sheet_has_the_pc_apps_shots_and_where_each_deferred_check_passed(passed_round):
    after = opened()
    assert after["stage"] == "r3" and after["r3"]["round"] == 1 and not after.get("stopped")
    sheet = critique.r3_sheet(oneshot_spec.spec_of("budget"), shot)
    assert "/look/2/S1-window-full.png" in sheet and "여백이 좁다" in sheet and "휴대폰" not in sheet
    pc_part = sheet.split("## PC에서 확인한 지적")[1].split("<details>")[0]
    assert f"O1 R1: 켜진다 — PC 세션 2회차 `{BUILD['head'][:12]}` 에서 통과" in pc_part
    assert "O2 R1: 캡처한 글이 보인다 — 주인 화면 회차" in pc_part and "앱이 낸 보고" in pc_part
    assert "O3" in pc_part and "화면 합성 캡처를 비평가가 봤다" in pc_part
    assert "에뮬레이터" not in sheet and "기기에서 확인한 지적" not in sheet
    shown = critique.extra(oneshot_spec.spec_of("budget"))   # `held` stands the whole view in for the spec
    assert shown["r3_items"] == ["S1"] and "## PC에서 확인한 지적" in shown["r3_sheet"]


def test_the_approval_waits_before_the_pull_request_with_the_kind_stop(passed_round, monkeypatch):
    opened()
    monkeypatch.setattr(oneshot_spec, "start", lambda *a, **k: pytest.fail("the pull request is the next step's"))
    on = critique.Decisions(source=brief.source(brief.snapshot(oneshot_spec.spec_of("budget"), "r3")),
                            decisions={"S1": {"approve": True}})
    with patch.object(critique.oneshot_pr, "begin", lambda repo: pytest.fail("S8 is the next step's")):
        critique.decide("budget", on)
    after = o()
    assert after["stage"] == "pr" and not after["working"] and after["stopped"]["reason"] == "kind"
    assert "풀 리퀘스트는 다음 버전에서" in after["stopped"]["detail"]
    oneshot_spec.recover()   # held there: a restart keeps the stop, and `[재개]` is refused
    assert o()["stopped"]["reason"] == "kind" and o()["stage"] == "pr"
    with pytest.raises(HTTPException, match="다음 버전"):
        oneshot_spec.resume("budget")


def test_a_run_an_earlier_version_held_before_r3_opens_it_on_recovery(passed_round):
    # 9e5b's stop: the round passed, R3 held with no opening written.
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None, "r3": None, "stopped": {
        "reason": "kind", "state": "r3", "detail": "화면 비평과 기기 회차를 마쳤다 — R3 는 다음 버전에서 이어진다"}})
    oneshot_spec.recover()
    after = o()
    assert after["stage"] == "r3" and not after.get("stopped") and after["r3"]["round"] == 1
    assert "## PC에서 확인한 지적" in critique.extra(oneshot_spec.spec_of("budget"))["r3_sheet"]


def test_a_run_held_before_r3_whose_owner_round_no_longer_stands_waits_for_it_again(passed_round):
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None, "r3": None, "front_build": "b2", "stopped": {
        "reason": "kind", "state": "r3", "detail": "화면 비평과 기기 회차를 마쳤다 — R3 는 다음 버전에서 이어진다"}})
    oneshot_spec.recover()   # the round's pass was of build b1: the guard sends it back to the owner's round
    after = o()
    assert after["stage"] == "pc" and after["pc"]["status"] == "waiting" and not after.get("r3")
    assert not after.get("stopped")
