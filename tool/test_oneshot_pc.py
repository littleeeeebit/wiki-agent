"""The owner's desktop round's approval and the one guarded entry to R3 for every device round (plan 9e5a), on
stand-ins: nothing starts on the owner's desktop in this version."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from main import oneshot_desktop, oneshot_pc as pc, oneshot_profile, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import o, world  # noqa: F401 — `world` is the temporary worktree's fixture

BUILD: dict = {}   # the run's frozen build, a real copy at the worktree's HEAD (`held`)
REPORT = {"id": "O2", "kind": "desktop", "source": "owner", "requirement": "R1", "check": "캡처한 글이 보인다",
          "act": {"action": "invoke", "id": "capture"}, "observe": {"report": {"id": "seen"}}}
SURFACE = {**REPORT, "id": "O3", "observe": {"composition": {"shows": "고정 창 위에 번역문이 겹친다"}}}
LOOK = {"id": "O1", "kind": "desktop", "source": "look", "requirement": "R1", "check": "켜진다",
        "act": {"action": "toggle", "id": "on"}, "expect": {"id": "on", "toggle": "On"}}


@pytest.fixture
def held(world, monkeypatch, tmp_path):  # noqa: F811 — the imported fixture, by its name
    """A PC app's run after its two clean critiques, at the owner's desktop round with two `owner` checks. The
    screen's whole view is the sheet tests'; these read the round's own part of it (`extra`)."""

    from test_oneshot_desktop import frozen_in

    monkeypatch.setattr(oneshot_spec, "view", lambda spec: spec)
    BUILD.clear()
    BUILD.update(frozen_in(world, tmp_path), freeze="f1")
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "화면 번역기", "kind": "desktop"}, "features": [],
                                          "front": {"screens": [{"id": "S1", "kind": "desktop", "primary": True}]},
                                          "owed": [LOOK, REPORT, SURFACE], "desktop_build": BUILD})
    pc.waits("budget", {"critiques": [{"clean": True}] * 2})
    return world


def approval(**changed) -> pc.Approval:
    return pc.Approval(**{"head": BUILD["head"], "build": BUILD["identity"], "freeze": BUILD["freeze"],
                          "checks": ["O2", "O3"], "surface": True, **changed})


def test_the_owner_approves_once_exactly_what_the_round_starts_and_reads(held):
    assert o()["stage"] == "pc" and o()["stopped"]["reason"] == "kind" and oneshot_profile.held(o())
    assert "O2, O3" in o()["stopped"]["detail"] and "승인하면" in o()["stopped"]["detail"]
    plan = pc.extra(oneshot_spec.spec_of("budget"))["pc_plan"]
    assert plan["checks"] == ["O2", "O3"] and plan["starts"] == ["app.exe", "oneshot-fixture (O2)", "oneshot-fixture (O3)"]
    assert plan["captures"] == [{"check": "O3", "kind": "surface", "reads": "oneshot-fixture 가 덮은 화면 영역"}]
    assert pc.approved(o()) is None   # absent: nothing recorded, nothing runs
    for stale in ({"head": "c" * 40}, {"build": "d" * 64}, {"freeze": "f0"}, {"checks": ["O2"]}):
        with pytest.raises(HTTPException, match="지금 회차와 다르다"):
            pc.approve("budget", approval(**stale))
    assert not o()["pc"].get("approval")
    pc.approve("budget", approval())
    given = pc.approved(o())
    assert given["checks"] == ["O2", "O3"] and given["surface"] and o()["pc"]["status"] == "approved"
    assert o()["stopped"]["reason"] == "kind" and "승인했다" in o()["stopped"]["detail"]
    with pytest.raises(HTTPException, match="이미 승인했다"):   # a duplicate: one approval, one round
        pc.approve("budget", approval())
    with pytest.raises(HTTPException, match="다음 버전"):   # the round itself is the next version's
        oneshot_spec.resume("budget")


def test_a_restart_or_a_new_build_after_the_approval_sends_the_run_back_to_waiting(held):
    pc.approve("budget", approval())
    oneshot_spec.saved("budget", oneshot={"desktop_build": {**BUILD, "freeze": "f2"}})   # rebuilt: another freeze
    assert pc.approved(o()) is None   # never applied to a different launch
    oneshot_spec.saved("budget", oneshot={"desktop_build": BUILD})
    assert pc.approved(o())
    oneshot_spec.recover()
    assert o()["stage"] == "pc" and o()["pc"]["status"] == "waiting" and not o()["pc"].get("approval")
    assert o()["stopped"]["reason"] == "kind" and "승인하면" in o()["stopped"]["detail"]
    pc.approve("budget", approval())   # approved afresh
    assert pc.approved(o())


def test_a_surface_the_owner_does_not_allow_leaves_its_check_pending_and_r3_closed(held):
    pc.approve("budget", approval(surface=False))
    assert pc.unrun(o()) == ["O3"]
    passed = {"status": "passed", "head": BUILD["head"], "freeze": BUILD["freeze"], "passed": ["O2"]}
    oneshot_spec.saved("budget", oneshot={"pc": {**o()["pc"], **passed}})
    assert not pc.passed(o())   # O3 still pending
    oneshot_spec.saved("budget", oneshot={"pc": {**o()["pc"], "passed": ["O2", "O3"]}})
    assert pc.passed(o()) and not pc.passed({**o(), "desktop_build": {**BUILD, "freeze": "f2"}})


def test_an_owner_check_from_an_earlier_review_stays_pending_and_is_never_approved_to_run(held):
    bare = {k: v for k, v in REPORT.items() if k not in ("act", "observe")}
    oneshot_spec.saved("budget", oneshot={"owed": [bare]})
    assert pc.unrun(o()) == ["O2"] and pc.plan(o())["checks"] == []
    with pytest.raises(HTTPException, match="돌릴 수 있는 검사가 없다"):
        pc.approve("budget", approval(checks=[]))
    assert not pc.passed({**o(), "pc": {"status": "passed", "head": BUILD["head"], "freeze": "f1", "passed": []}})


def test_the_frontend_build_scripts_each_owner_check_ending_on_the_reviews_step(held):
    spec = {"oneshot": {**o(), "front": {"screens": [{"id": "S1", "kind": "desktop"}]}}}
    step = {"name": "go", "action": "invoke", "id": "capture", "expect": {"id": "capture", "name": "캡처"}}
    look = {"name": "on", **LOOK["act"], "expect": LOOK["expect"]}
    full = {"O1": {"screen": "S1", "state": "full", "steps": [look]},
            "O2": {"screen": "S1", "state": "full", "steps": [step]}, "O3": {"screen": "S1", "state": "full", "steps": [step]}}
    assert oneshot_desktop.scripted(full, spec) == ""
    assert "O3" in oneshot_desktop.scripted({k: v for k, v in full.items() if k != "O3"}, spec)
    other = {**full, "O2": {**full["O2"], "steps": [{**step, "id": "refresh"}]}}
    assert "리뷰가 정한 동작" in oneshot_desktop.scripted(other, spec)


def test_the_guarded_entry_holds_r3_until_every_due_device_round_passed(held):
    from main import oneshot_phone

    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "kind": "android", "kinds": ["android", "desktop"]},
                                          "stage": "phone", "working": "phone", "stopped": None, "pc": None,
                                          "front_built_head": BUILD["head"], "apks": {"app.apk": "x"},
                                          "phone": {"status": "running"}})
    passed = {"status": "passed", "detail": "", "head": BUILD["head"], "apks": {"app.apk": "x"}}
    assert not callable(oneshot_phone.settled("budget", passed))   # the phone passed first: the owner's round is next
    assert o()["stage"] == "pc" and o()["phone"]["status"] == "passed" and o()["stopped"]["reason"] == "kind"
    assert not o().get("r3")
    # The owner's round passed on this build: the phone's pass on this HEAD still stands, and R3 opens (held here).
    oneshot_spec.saved("budget", oneshot={"pc": {"status": "passed", "head": BUILD["head"], "freeze": "f1",
                                                 "passed": ["O2", "O3"]}})
    assert pc.onward("budget", {}) is None and o()["stage"] == "r3" and o()["stopped"]["reason"] == "kind"
    # A rebuild on a new HEAD clears both passes: the phone is due again first.
    oneshot_spec.saved("budget", oneshot={"stage": "critique", "front_built_head": "c" * 40,
                                          "desktop_build": {**BUILD, "head": "c" * 40, "freeze": "f2"}})
    pc.onward("budget", {})
    assert o()["stage"] == "phone" and o()["phone"]["status"] == "waiting"
