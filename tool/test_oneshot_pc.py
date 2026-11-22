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
          "act": {"action": "invoke", "id": "capture"}, "observe": {"report": {"id": "seen", "from": "fixture"}}}
SURFACE = {**REPORT, "id": "O3", "observe": {"composition": {"shows": "고정 창 위에 번역문이 겹친다", "from": "fixture"}}}
SCREEN = {**REPORT, "id": "O4", "observe": {"report": {"file": "seen.txt", "from": "screen"}}}   # the app reads it
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
                                          "owed": [LOOK, REPORT, SURFACE], "desktop_build": BUILD, "front_build": "b1"})
    pc.waits("budget", {"critiques": [{"clean": True}] * 2})
    return world


def approval(surface: bool = True, **changed) -> pc.Approval:
    """The approval of the plan the screen shows now, with `changed` fields as a stale screen would hold them."""

    return pc.Approval(plan={**pc.plan(o()), **changed}, surface=surface)


def test_the_owner_approves_once_exactly_what_the_round_starts_and_reads(held):
    assert o()["stage"] == "pc" and o()["stopped"]["reason"] == "kind" and oneshot_profile.held(o())
    assert "O2, O3" in o()["stopped"]["detail"] and "승인하면" in o()["stopped"]["detail"]
    plan = pc.extra(oneshot_spec.spec_of("budget"))["pc_plan"]
    assert plan["checks"] == ["O2", "O3"] and plan["starts"] == ["app.exe", "oneshot-fixture (O2)", "oneshot-fixture (O3)"]
    app = {"by": "app", "kind": "window", "reads": "oneshot-fixture 창"}
    assert plan["captures"] == [{"check": "O2", **app}, {"check": "O3", **app},
                                {"check": "O3", "by": "round", "kind": "surface", "reads": "oneshot-fixture 가 덮은 화면 영역"}]
    assert pc.approved(o()) is None   # absent: nothing recorded, nothing runs
    owned = {"check": "O3", "by": "round", "kind": "window", "reads": "앱의 주 창이 가진 창"}
    for stale in ({"head": "c" * 40}, {"build": "d" * 64}, {"freeze": "f0"}, {"checks": ["O2"]}, {"attempt": "old"},
                  {"captures": [*plan["captures"][:2], owned]}):   # the same ids, another window to read
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


def test_an_approval_of_a_build_the_worktree_has_left_is_refused(held):
    oneshot_spec.saved("budget", oneshot={"desktop_build": {**BUILD, "head": "c" * 40}})   # the worktree moved on
    with pytest.raises(HTTPException, match="다시 빌드한 뒤"):
        pc.approve("budget", approval())
    assert not o()["pc"].get("approval")


def test_an_approval_is_refused_while_the_worktrees_head_cannot_be_read(held, monkeypatch):
    import subprocess

    def timed_out(*a, **k):
        raise subprocess.TimeoutExpired("git", 60)

    monkeypatch.setattr(pc.specs, "sh", timed_out)
    assert oneshot_spec.standing("budget", BUILD)   # the startup's rule keeps it; consent never rests on an unread HEAD
    with pytest.raises(HTTPException, match="다시 빌드한 뒤"):
        pc.approve("budget", approval())
    assert not o()["pc"].get("approval")


def test_a_restart_or_a_new_build_after_the_approval_sends_the_run_back_to_waiting(held):
    before = approval()   # the screen's request, prepared before the restart
    pc.approve("budget", approval())
    oneshot_spec.saved("budget", oneshot={"desktop_build": {**BUILD, "freeze": "f2"}})   # rebuilt: another freeze
    assert pc.approved(o()) is None   # never applied to a different launch
    oneshot_spec.saved("budget", oneshot={"desktop_build": BUILD})
    assert pc.approved(o())
    oneshot_spec.recover()
    assert o()["stage"] == "pc" and o()["pc"]["status"] == "waiting" and not o()["pc"].get("approval")
    assert o()["stopped"]["reason"] == "kind" and "승인하면" in o()["stopped"]["detail"]
    with pytest.raises(HTTPException, match="지금 회차와 다르다"):   # another wait: the earlier request is stale
        pc.approve("budget", before)
    pc.approve("budget", approval())   # approved afresh, off the plan shown now
    assert pc.approved(o())


def test_an_expired_approval_never_blocks_the_approval_of_the_plan_shown_now(held):
    pc.approve("budget", approval())
    oneshot_spec.saved("budget", oneshot={"owed": [LOOK, REPORT, {**SURFACE, "observe": {"window": {"shows": "글", "from": "fixture"}}}]})
    assert pc.approved(o()) is None   # O3 now reads another capture
    pc.approve("budget", approval())
    assert pc.approved(o())["captures"][2]["kind"] == "window"


def test_a_surface_the_owner_does_not_allow_leaves_its_check_pending_and_r3_closed(held):
    # O5: the app reads the screen and shows it in its own window, which is all the round captures.
    preview = {**REPORT, "id": "O5", "observe": {"window": {"shows": "번역문", "from": "screen"}}}
    oneshot_spec.saved("budget", oneshot={"owed": [LOOK, REPORT, SURFACE, SCREEN, preview]})
    surface = {"by": "app", "kind": "surface", "reads": "oneshot-fixture 가 덮은 화면 영역"}
    assert pc.plan(o())["captures"][3:] == [{"check": "O4", **surface}, {"check": "O5", **surface},
                                            {"check": "O5", "by": "round", "kind": "window", "reads": "앱의 주 창"}]
    pc.approve("budget", approval(surface=False))
    assert pc.unrun(o()) == ["O3", "O4", "O5"]   # the round's surface and the app's alike
    passed = {"status": "passed", "head": BUILD["head"], "freeze": BUILD["freeze"], "front_build": "b1", "passed": ["O2"]}
    oneshot_spec.saved("budget", oneshot={"pc": {**o()["pc"], **passed}})
    assert not pc.passed(o())   # O3 to O5 still pending
    oneshot_spec.saved("budget", oneshot={"pc": {**o()["pc"], "passed": ["O2", "O3", "O4", "O5"]}})
    assert pc.passed(o()) and not pc.passed({**o(), "desktop_build": {**BUILD, "freeze": "f2"}})
    assert not pc.passed({**o(), "front_build": "another"})   # a rebuild of the same freeze's files
    # A build an earlier version accepted names no generation: neither its pass nor the phone's counts for it.
    legacy = {**o(), "front_build": None, "pc": {**o()["pc"], "front_build": None}}
    assert not pc.passed(legacy)
    assert not pc.phoned({**legacy, "front_built_head": "h", "apks": {}, "phone": {"status": "passed", "head": "h",
                                                                                 "apks": {}}})


def test_an_owner_check_from_an_earlier_review_stays_pending_and_is_never_approved_to_run(held):
    bare = {k: v for k, v in REPORT.items() if k not in ("act", "observe")}
    oneshot_spec.saved("budget", oneshot={"owed": [bare]})
    assert pc.unrun(o()) == ["O2"] and pc.plan(o())["checks"] == []
    with pytest.raises(HTTPException, match="돌릴 수 있는 검사가 없다"):
        pc.approve("budget", approval())
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
                                          "front_build": "b1", "phone": {"status": "running"}})
    passed = {"status": "passed", "detail": "", "head": BUILD["head"], "apks": {"app.apk": "x"}, "front_build": "b1"}
    assert not callable(oneshot_phone.settled("budget", passed))   # the phone passed first: the owner's round is next
    assert o()["stage"] == "pc" and o()["phone"]["status"] == "passed" and o()["stopped"]["reason"] == "kind"
    assert not o().get("r3")
    # The owner's round passed on this build: the phone's pass on this HEAD still stands, and R3 opens (held here).
    oneshot_spec.saved("budget", oneshot={"pc": {"status": "passed", "head": BUILD["head"], "freeze": "f1",
                                                 "front_build": "b1", "passed": ["O2", "O3"]}})
    assert pc.onward("budget", {}) is None and o()["stage"] == "r3" and o()["stopped"]["reason"] == "kind"
    # A rebuild of the same HEAD and the same APK digests clears both passes: the phone is due again first.
    oneshot_spec.saved("budget", oneshot={"stage": "critique", "front_build": "b2",
                                          "desktop_build": {**BUILD, "freeze": "f2"}})
    pc.onward("budget", {})
    assert o()["stage"] == "phone" and o()["phone"]["status"] == "waiting"


def test_an_earlier_versions_android_run_held_at_r3_reopens_only_on_a_pass_of_its_current_build(held):
    from main import oneshot_phone

    def held_at_r3(head: str, phone: dict) -> None:
        oneshot_spec.saved("budget", oneshot={"intent": {"restated": "화면 번역기", "kind": "android"}, "stage": "r3",
                                              "working": None, "front_built_head": head, "apks": {"app.apk": "x"},
                                              "phone": phone, "pc": None, "r3": None,
                                              "stopped": {"reason": "kind", "state": "r3"}})
        oneshot_phone.earlier("budget", o())

    held_at_r3(BUILD["head"], {"status": "passed", "head": "c" * 40, "apks": {"app.apk": "old"}, "front_build": "b1"})
    assert o()["stage"] == "phone" and o()["phone"]["status"] == "waiting" and not o().get("r3")
    # Every saved field agrees, but the worktree has moved off that HEAD since.
    held_at_r3("c" * 40, {"status": "passed", "head": "c" * 40, "apks": {"app.apk": "x"}, "front_build": "b1"})
    assert o()["stage"] == "phone" and not o().get("r3")
    held_at_r3(BUILD["head"], {"status": "passed", "head": BUILD["head"], "apks": {"app.apk": "x"}, "front_build": "b1"})
    assert o()["stage"] == "r3" and o()["r3"]


def test_a_build_an_earlier_version_accepted_gets_its_generation_and_its_rerun_pass_counts(held):
    legacy = {"status": "passed", "head": BUILD["head"], "apks": {"app.apk": "x"}}   # no generation named
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "화면 번역기", "kind": "android"}, "stage": "r3",
                                          "working": None, "front_built_head": BUILD["head"], "apks": {"app.apk": "x"},
                                          "front_build": None, "phone": legacy, "pc": None, "r3": None,
                                          "stopped": {"reason": "kind", "state": "r3", "detail": "7f2"}})
    oneshot_spec.recover()
    stamped = o()["front_build"]
    assert stamped and o()["stage"] == "phone" and not o().get("r3")
    oneshot_spec.recover()   # once: a second start keeps it, so a pass of the rerun still names it
    assert o()["front_build"] == stamped
    # The rerun's pass names that generation, as `drive` records the run's (`test_oneshot_phone`): it counts.
    assert pc.phoned({**o(), "phone": {**legacy, "front_build": stamped}})


def test_an_earlier_versions_mixed_run_held_at_r3_waits_for_the_phone_before_the_owners_round(held):
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "화면 번역기", "kind": "android",
                                                     "kinds": ["android", "desktop"]},
                                          "stage": "r3", "working": None, "front_built_head": BUILD["head"],
                                          "apks": {"app.apk": "x"}, "front_build": None, "pc": None, "r3": None,
                                          "phone": {"status": "passed", "head": BUILD["head"], "apks": {"app.apk": "x"}},
                                          "stopped": {"reason": "kind", "state": "r3", "detail": "9e4b"}})
    oneshot_spec.recover()
    assert o()["stage"] == "phone" and o()["phone"]["status"] == "waiting" and not o().get("r3")
