"""oneshot step 9e6a: R3 of a PC app — the sheet's `PC 앱` section with where each deferred check passed, the owner's
desktop round's included; the approval waits before the pull request, which is the next step's; a run an earlier
version held before R3 opens it by the guard.

See `docs/plans/oneshot/9-kinds.md` ("R3 and S8", "Steps and their stops").
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from main import oneshot_brief as brief, oneshot_critique as critique, oneshot_desktop_look, oneshot_pc as pc, \
    oneshot_spec
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
    return {"round": n, "head": BUILD["head"], "findings": list(findings), "build": BUILD["identity"],   # as the look's
            "freeze": BUILD["freeze"], "proven": ["O1"], "proof": oneshot_desktop_look.PROOF}


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
    # Only a pass the look recorded by this version's proof is said to be one.
    for unproven in ({"proven": []}, {"proof": "an earlier version's"}):
        looks = o()["looks"]
        oneshot_spec.saved("budget", oneshot={"looks": [*looks[:-1], {**looks[-1], **unproven}]})
        assert "O1 R1: 켜진다 — PC 세션 2회차가 돌린 기록이 없다" in critique.r3_sheet(oneshot_spec.spec_of("budget"), shot)
        oneshot_spec.saved("budget", oneshot={"looks": looks})
    shown = critique.extra(oneshot_spec.spec_of("budget"))   # `held` stands the whole view in for the spec
    assert shown["r3_items"] == ["S1"] and "## PC에서 확인한 지적" in shown["r3_sheet"]


def test_the_approval_starts_s8s_readme_turn(passed_round):
    opened()
    on = critique.Decisions(source=brief.source(brief.snapshot(oneshot_spec.spec_of("budget"), "r3")),
                            decisions={"S1": {"approve": True}})
    begun = []
    with patch.object(critique.oneshot_pr, "begin", begun.append):
        critique.decide("budget", on)
    assert begun == ["budget"] and o()["stage"] == "pr" and not o().get("stopped")


def test_a_run_an_earlier_version_held_after_r3_resumes_at_the_readme_turn(passed_round):
    # 9e6a's stop: R3 approved, the run held at `pr` before its README turn.
    oneshot_spec.saved("budget", oneshot={"stage": "pr", "working": None, "stopped": {
        "reason": "kind", "state": "pr", "detail": "R3 를 승인했다 — 풀 리퀘스트는 다음 버전에서 이어진다"}})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    sent = []
    with patch.object(critique.oneshot_pr.oneshot_build, "send", lambda repo, stage, text: sent.append((stage, text))), \
            patch.object(critique.oneshot_pr, "readme_text", lambda spec: "the README turn"):
        oneshot_spec.resume("budget")
    assert sent == [("pr", "the README turn")]


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
