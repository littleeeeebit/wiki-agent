"""The owner's desktop round: its approval, the round it starts and the one guarded entry to R3 for every device
round (plan 9e5), on stand-ins: nothing starts on the owner's desktop here. The Windows side's own fakes are
`test_oneshot_owner_round.py`."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import owner_round
from main import oneshot, oneshot_build, oneshot_critique, oneshot_desktop, oneshot_pc as pc, oneshot_profile, \
    oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import o, world  # noqa: F401 — `world` is the temporary worktree's fixture

BUILD: dict = {}   # the run's frozen build, a real copy at the worktree's HEAD (`held`)
STEP = {"name": "go", "action": "invoke", "id": "capture", "expect": {"id": "capture", "name": "캡처"}}
SCENARIO = {"screen": "S1", "state": "full", "steps": [STEP]}
PAGE = ["apple", "river", "garden"]   # the fixture's words, drawn for the round as `owner_round.words` would
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
    # The worker's start as `start_worker` saves it; the tests run the worker in their own thread (`ran`).
    monkeypatch.setattr(oneshot_spec, "start", lambda repo, work, **fields: oneshot_spec.saved(
        repo, oneshot={**fields, "working": work, "stopped": None}))
    BUILD.clear()
    BUILD.update(frozen_in(world, tmp_path), freeze="f1", checks={"O2": SCENARIO, "O3": SCENARIO})
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "화면 번역기", "kind": "desktop"}, "features": [],
                                          "front": {"screens": [{"id": "S1", "kind": "desktop", "primary": True}]},
                                          "profile": {**o()["profile"], "window": "1280x800"},
                                          "owed": [LOOK, REPORT, SURFACE], "desktop_build": BUILD, "front_build": "b1",
                                          "front_built_head": BUILD["head"]})
    pc.waits("budget", {"critiques": [{"clean": True}] * 2})
    return world


def approval(surface: bool = True, **changed) -> pc.Approval:
    """The approval of the plan the screen shows now, with `changed` fields as a stale screen would hold them."""

    return pc.Approval(plan={**pc.plan(o()), **changed}, surface=surface)


def test_the_owner_approves_once_exactly_what_the_round_starts_and_reads(held):
    assert o()["stage"] == "pc" and not o()["stopped"] and not oneshot_profile.held(o())   # this version runs it
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
    assert given["checks"] == ["O2", "O3"] and given["surface"] and o()["pc"]["status"] == "running"
    assert o()["working"] == "pc" and not o()["stopped"]   # the approval starts the round
    with pytest.raises(HTTPException, match="기다리고 있지 않다"):   # a second press while it runs
        pc.approve("budget", approval())
    oneshot_spec.saved("budget", oneshot={"working": None})   # a round that never started: its worktree was busy
    pc.approve("budget", approval())   # approved again, it starts
    assert o()["working"] == "pc"


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
    oneshot_spec.recover()   # the round the approval started was cut with the server
    assert o()["stage"] == "pc" and o()["pc"]["status"] == "waiting" and not o()["pc"].get("approval")
    assert o()["stopped"]["reason"] == "restart" and not o()["working"]
    oneshot_spec.resume("budget")   # `[재개]` puts the approval back, never the cut round
    assert o()["stage"] == "pc" and not o()["stopped"] and not o()["working"]
    with pytest.raises(HTTPException, match="지금 회차와 다르다"):   # another wait: the earlier request is stale
        pc.approve("budget", before)
    pc.approve("budget", approval())   # approved afresh, off the plan shown now
    assert pc.approved(o())


def test_an_earlier_versions_kind_stop_at_the_approval_is_let_go_on_recovery(held):
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "kind", "state": "pc", "detail": "9e5a"},
                                          "pc": {**o()["pc"], "status": "approved", "approval": pc.plan(o())}})
    oneshot_spec.recover()
    assert o()["stage"] == "pc" and not o()["stopped"] and not o()["pc"].get("approval")   # 9e5a's never runs
    pc.approve("budget", approval())
    assert o()["working"] == "pc"


def test_an_expired_approval_never_blocks_the_approval_of_the_plan_shown_now(held):
    pc.approve("budget", approval())
    oneshot_spec.saved("budget", oneshot={"working": None, "owed": [
        LOOK, REPORT, {**SURFACE, "observe": {"window": {"shows": "글", "from": "fixture"}}}]})
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
    assert o()["stage"] == "pc" and o()["phone"]["status"] == "passed" and not o()["stopped"]
    assert not o().get("r3")
    # The owner's round passed on this build: the phone's pass on this HEAD still stands, and R3 opens.
    oneshot_spec.saved("budget", oneshot={"pc": {"status": "passed", "head": BUILD["head"], "freeze": "f1",
                                                 "front_build": "b1", "passed": ["O2", "O3"]}})
    assert callable(pc.onward("budget", {})) and o()["stage"] == "r3" and o()["r3"]["round"] == 1
    assert not o()["stopped"] and o()["working"] == "brief"   # its brief, written once the tree is let go
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


# -- the round ---------------------------------------------------------------------------------------------------

FIXTURE = {"job": "fixture1", "hwnd": 201, "pid": 5, "class": "TkTopLevel", "title": "oneshot-fixture",
           "rect": [80, 80, 976, 579], "process": {"pid": 5, "created": 1, "exe": "python.exe"}}


def driven(value: str | None = None, acted: bool = True, before: str | None = None, whole: bool = True,
           baseline: dict | None = None, **over) -> dict:
    """A driver's report of the scenario's one step on the owner's desktop: read, never shot. States: the start,
    the one read with the step's target found just before it acted (`before`, the element's value then), and the
    one after it (`whole`, whether it was read whole). `baseline` is the report file as the driver read it just
    before the step (proven absent by default)."""

    def state(value, read=True):
        return {"windows": [{"hwnd": 101, "pid": 7, "class": "App"}],
                "shots": [{"hwnd": 101, "drawn": False, "unshot": True}], "reads": [{"hwnd": 101, "read": read}],
                "nodes": [{"window": 101, "id": "seen", "value": value, "type": "Text", "depth": 1},
                          {"window": 101, "id": "capture", "name": "캡처", "type": "Button", "depth": 1}]}

    return {"phase": "done", "main": {"hwnd": 101, "pid": 7, "class": "App"},
            "states": [{**state(None), "id": "s00", "name": "start"}, {**state(before), "id": "s01", "name": "before-go"},
                       {**state(value, whole), "id": "s02", "name": "after-go"}],
            "steps": [{"name": "go", "acted": acted, "state": "s02", "before": "s01",
                       "baseline": baseline or {"absent": True}}], **over}


class Runs:
    """`Launches` on the owner's desktop as a stand-in: each start drives `report`, then calls `then` with it."""

    def __init__(self, report: dict, data: Path):
        self.report, self.data, self.halt, self.drove = report, str(data), threading.Event(), []

    def drive(self, sid, state, steps, then):
        self.drove.append((sid, state, steps))
        return {**self.report, "observed": then(self.report, "app1")}

    def due(self):
        pass


class Session:
    record = {"jobs": {"fixture1": {"members": [FIXTURE["process"]]}, "app1": {"members": []}}}

    def observe(self):
        pass


@pytest.fixture
def desk(monkeypatch, tmp_path):
    """The Windows side as stand-ins: the fixture shows `PAGE`, every binding stands, and each read is counted."""

    reads = {"window": [], "surface": [], "bound": [], "ended": []}
    capture = tmp_path / "capture.png"
    capture.write_bytes(b"PNG")
    monkeypatch.setattr(owner_round, "words", lambda: list(PAGE))
    monkeypatch.setattr(owner_round, "fixture", lambda session, key, page, rect, halt: (FIXTURE, FIXTURE["rect"]))
    monkeypatch.setattr(owner_round, "proven", lambda binding, members: "")
    monkeypatch.setattr(owner_round, "bind", lambda job, hwnd, members, title=None, seen=None:
                        reads["bound"].append(seen) or {"job": job, "hwnd": hwnd})
    monkeypatch.setattr(owner_round, "window_read", lambda *a, **k: reads["window"].append(a) or (
        {"read": False, "why": why} if (why := k["also"]() if k.get("also") else "") else   # proven before the go
        {"read": True, "file": str(capture)}))
    monkeypatch.setattr(owner_round, "surface_read", lambda *a: reads["surface"].append(a) or {"read": True, "file": str(capture)})
    monkeypatch.setattr(owner_round, "ended", lambda session, key, what="": reads["ended"].append(key))
    monkeypatch.setattr(owner_round, "shown", lambda members: {101})   # the app shows only the window the driver read
    (tmp_path / "keep").mkdir()
    (tmp_path / "data").mkdir()
    return reads, tmp_path


def checked(desk, x: dict, report: dict, exe="helper.exe") -> dict:
    reads, root = desk
    return pc.one(Session(), Runs(report, root / "data"), 1, x, SCENARIO, exe, "도우미가 없다", root / "keep",
                  lambda: None)


@pytest.mark.parametrize("value", [None, "", "capture active", "apple river"])
def test_valid_controls_whose_report_is_blank_or_wrong_fail_the_check(held, desk, value):
    # The scenario ran and its controls held: only what the app reports it captured is evidence.
    assert checked(desk, REPORT, driven(value))["status"] == "failed"


def test_a_report_holding_the_whole_page_passes_from_an_element_or_a_file_under_data(held, desk):
    assert checked(desk, REPORT, driven("Captured:\nApple, River,\nGARDEN."))["status"] == "passed"
    # The page as it reads: a fixed text naming every word, or the page out of order, is no capture of it.
    for fixed in (" ".join(sorted(owner_round.WORDS)), "garden river apple", "apple river and garden"):
        assert checked(desk, REPORT, driven(fixed))["status"] == "failed"
    _, root = desk
    (root / "data" / "seen.txt").write_text("apple river garden", encoding="utf-8")
    assert checked(desk, SCREEN, driven())["status"] == "passed"
    outside = {**SCREEN, "observe": {"report": {"file": "../keep/seen.txt", "from": "screen"}}}
    (root / "keep" / "seen.txt").write_text("apple river garden", encoding="utf-8")
    assert checked(desk, outside, driven())["status"] == "failed"   # never a file outside the app's `{data}`


def test_a_report_counts_only_as_the_capture_steps_own_from_a_whole_read(held, desk):
    page = "apple river garden"
    assert checked(desk, REPORT, driven(page, before="waiting"))["status"] == "passed"
    assert checked(desk, REPORT, driven(page, before=page))["status"] == "failed"   # it held the page before the act
    assert checked(desk, REPORT, driven(page, whole=False))["status"] == "failed"   # a read that ended partway
    unknown = [  # what the element held just before the step acted was never proven
        lambda r: r["states"][1]["reads"][0].update(read=False),
        lambda r: r["states"][1]["nodes"].append({**r["states"][1]["nodes"][0]}),   # two by that id
        lambda r: r["states"][1]["nodes"][0].update(value=None, unvalued=True),   # its value unread
        lambda r: r["states"][1]["nodes"][0].pop("value"),   # there, but offering no value: a text it shows unread
        lambda r: r["states"][1]["nodes"].insert(0, {"window": 101, "type": "List", "items": 3, "unread": 1}),
        lambda r: r["states"][2]["nodes"].insert(0, {"window": 101, "type": "List", "items": 3, "unread": 1}),
        lambda r: r["steps"][0].pop("before"),   # no state read with the target found: the start is not that
    ]
    for change in unknown:
        report = driven(page)
        change(report)
        assert checked(desk, REPORT, report)["status"] == "failed"
    _, root = desk
    (root / "data" / "seen.txt").write_text(page + "\n", encoding="utf-8")
    assert checked(desk, SCREEN, driven(baseline={"text": page}))["status"] == "failed"   # the page, rewritten
    assert checked(desk, SCREEN, driven(baseline={"text": "waiting"}))["status"] == "passed"   # no page before
    assert checked(desk, SCREEN, driven(baseline={"error": "denied"}))["status"] == "failed"   # unknown before
    assert checked(desk, SCREEN, driven(baseline={"sha256": "0" * 64}))["status"] == "failed"   # bytes are no text
    (root / "data" / "seen.txt").write_bytes(b"\xff" + page.encode())   # the words, in a file that is not UTF-8
    assert checked(desk, SCREEN, driven())["status"] == "failed"


def test_a_report_file_grown_past_its_bound_after_the_size_check_is_no_report(held, desk, monkeypatch):
    _, root = desk
    (root / "data" / "seen.txt").write_text("apple river garden", encoding="utf-8")
    monkeypatch.setattr(pc, "MAX_REPORT", 10)   # the file's 18 bytes pass a size check that read 10 or fewer
    size = Path.stat
    monkeypatch.setattr(Path, "stat", lambda p, *a, **k: os.stat_result((*size(p, *a, **k)[:6], 0, 0, 0, 0))
                        if p.name == "seen.txt" else size(p, *a, **k))
    assert checked(desk, SCREEN, driven())["status"] == "failed"


def test_a_report_file_still_open_for_writing_is_never_read_as_one_version(held, desk):
    _, root = desk
    seen = root / "data" / "seen.txt"
    seen.write_text("apple river garden", encoding="utf-8")
    with open(seen, "r+b"):   # the app could still be rewriting it
        assert checked(desk, SCREEN, driven())["status"] == "failed"
    assert checked(desk, SCREEN, driven())["status"] == "passed"


def test_an_owned_report_window_counts_absent_before_the_step_only_when_there_was_none(held, desk):
    page, owned = "apple river garden", {**REPORT, "observe": {"report": {"id": "seen", "in": "owned", "from": "fixture"}}}

    def report(before_windows: int) -> dict:
        got = driven(page)
        for state, n in ((got["states"][1], before_windows), (got["states"][2], 1)):
            state["windows"] += [{"hwnd": 200 + i, "pid": 7, "class": "App", "owner": 101} for i in range(n)]
            state["reads"] += [{"hwnd": 200 + i, "read": True} for i in range(n)]
            state["nodes"] = [{"window": 200, "id": "seen", "value": page, "type": "Text", "depth": 1},
                              *(n for n in state["nodes"] if n["id"] == "capture")]   # the step's own control stays
        return got

    assert checked(desk, owned, report(0))["status"] == "passed"   # it appeared with the step
    assert checked(desk, owned, report(2))["status"] == "failed"   # two before: which one held what is unknown


@pytest.mark.parametrize("report, why", [
    (driven("apple river garden", acted=False), "단계가 다 돌지"),
    (driven("apple river garden", ended="act go"), "시간을 넘겼다"),
    (driven("apple river garden", phase="acting", error="boom"), "끝나지 못했다"),
])
def test_a_scenario_that_did_not_run_whole_is_no_ground_for_any_observation(held, desk, report, why):
    got = checked(desk, REPORT, report)
    assert got["status"] == "failed" and why in got["why"]


def test_each_checks_fixture_is_ended_before_the_check_returns_whatever_its_outcome(held, desk):
    reads, root = desk
    assert checked(desk, REPORT, driven("apple river garden"))["status"] == "passed"
    assert checked(desk, REPORT, driven("garden"))["status"] == "failed"
    assert reads["ended"] == ["fixture1", "fixture1"]

    class Broken(Runs):
        def drive(self, sid, state, steps, then):
            raise RuntimeError("the app's look could not be cleaned")

    with pytest.raises(RuntimeError):
        pc.one(Session(), Broken(driven(), root / "data"), 2, REPORT, SCENARIO, "helper.exe", "", root / "keep",
               lambda: None)
    assert reads["ended"][-1] == "fixture2"   # so the next check never finds two windows by the fixture's title


def test_a_fixture_replaced_before_the_read_fails_and_nothing_is_read(held, desk, monkeypatch):
    monkeypatch.setattr(owner_round, "proven", lambda binding, members: "the window is gone")
    window = {**REPORT, "observe": {"window": {"shows": "번역문", "from": "fixture"}}}
    got = checked(desk, window, driven())
    assert got["status"] == "failed" and "고정 창" in got["why"] and desk[0] == {"window": [], "surface": [], "bound": [], "ended": ["fixture1"]}


def test_an_app_window_replaced_after_the_drivers_read_is_never_read(held, desk, monkeypatch):
    window = {**REPORT, "observe": {"window": {"shows": "번역문", "from": "fixture"}}}
    checked(desk, window, driven())   # bound against the window exactly as the driver read it, each time
    assert desk[0]["bound"] == [{"hwnd": 101, "pid": 7, "class": "App"}] * 3   # up before, captured, up after

    def gone(job, hwnd, members, title=None, seen=None):
        raise ValueError(f"window {hwnd} is not one of {job}'s")

    monkeypatch.setattr(owner_round, "bind", gone)
    desk[0]["window"].clear()
    got = checked(desk, window, driven())
    assert got["status"] == "failed" and not desk[0]["window"]


def test_a_report_counts_only_while_the_app_that_wrote_it_is_still_up_once_it_is_read(held, desk, monkeypatch):
    _, root = desk
    (root / "data" / "seen.txt").write_text("apple river garden", encoding="utf-8")
    assert checked(desk, SCREEN, driven())["status"] == "passed"
    assert desk[0]["bound"][-1] == {"hwnd": 101, "pid": 7, "class": "App"}   # the main window as the driver read it

    def crashed(job, hwnd, members, title=None, seen=None):   # its window gone with its process
        raise ValueError(f"window {hwnd} is not one of {job}'s")

    monkeypatch.setattr(owner_round, "bind", crashed)
    got = checked(desk, SCREEN, driven())
    assert got["status"] == "failed" and "살아 있지" in got["why"], got


@pytest.mark.parametrize("gone_at", [1, 2])   # the app's window gone before the capture, or by the time it is read
def test_a_capture_counts_only_while_the_driven_app_is_up_before_and_after_it(held, desk, monkeypatch, gone_at):
    assert checked(desk, SURFACE, driven())["status"] == "judge"
    calls, bind = [], owner_round.bind

    def app(job, hwnd, members, title=None, seen=None):
        calls.append(job)
        if job.startswith("app") and sum(c.startswith("app") for c in calls) >= gone_at:
            raise ValueError(f"window {hwnd} is not one of {job}'s")
        return bind(job, hwnd, members, title, seen)

    monkeypatch.setattr(owner_round, "bind", app)
    desk[0]["surface"].clear()
    got = checked(desk, SURFACE, driven())
    assert got["status"] == "failed" and "살아 있지" in got["why"], got
    assert bool(desk[0]["surface"]) is (gone_at == 2)   # never sampled once the app was found gone


@pytest.mark.parametrize("left", [
    lambda nodes: nodes.pop(),   # the step's control is not there after it
    lambda nodes: nodes[-1].update(name="중지"),   # there, but not as the step promised
    lambda nodes: nodes.append(dict(nodes[-1])),   # two by its id: which one the step left is unknown
])
def test_an_observation_counts_only_once_every_step_left_the_state_it_promised(held, desk, left):
    _, root = desk
    (root / "data" / "seen.txt").write_text("apple river garden", encoding="utf-8")
    assert checked(desk, SCREEN, driven())["status"] == "passed"
    report = driven()
    left(report["states"][2]["nodes"])
    got = checked(desk, SCREEN, report)
    assert got["status"] == "failed" and "약속한 상태" in got["why"], got


def test_a_steps_postcondition_off_a_read_that_was_not_whole_proves_nothing(held, desk):
    _, root = desk
    (root / "data" / "seen.txt").write_text("apple river garden", encoding="utf-8")
    report = driven()
    report["states"][2]["nodes"].insert(0, {"window": 101, "type": "List", "items": 3, "unread": 1})
    assert "약속한 상태" in checked(desk, SCREEN, report)["why"]


@pytest.mark.parametrize("x, gone_at", [
    (SCREEN, 2),   # the report: proven before it is read, gone by the time it was
    ({**REPORT, "observe": {"window": {"shows": "번역문", "from": "fixture"}}}, 2),   # gone before the helper's go
    ({**REPORT, "observe": {"window": {"shows": "번역문", "from": "fixture"}}}, 3),   # gone once the frame was read
])
def test_a_read_counts_only_while_the_fixture_still_stands_through_it(held, desk, monkeypatch, x, gone_at):
    _, root = desk
    (root / "data" / "seen.txt").write_text("apple river garden", encoding="utf-8")
    assert checked(desk, x, driven())["status"] in ("passed", "judge")
    calls = []

    def proven(binding, members):   # the fixture's proofs counted; the app's stand
        if binding is FIXTURE:
            calls.append(1)
            return "the window is gone" if len(calls) >= gone_at else ""
        return ""

    monkeypatch.setattr(owner_round, "proven", proven)
    got = checked(desk, x, driven())
    assert got["status"] == "failed" and "고정 창" in got["why"], got


def test_a_handle_naming_another_class_than_the_driver_read_is_never_bound(monkeypatch):
    # Already replaced when the capture path starts: the job's own process, another window class.
    member = {"pid": 7, "created": 1, "exe": "app.exe"}
    monkeypatch.setattr(owner_round, "window", lambda hwnd: {"hwnd": hwnd, "pid": 7, "class": "Replacement",
                                                            "title": "x", "rect": [0, 0, 1, 1]})
    monkeypatch.setattr(owner_round.native_process, "identity", lambda pid: member)
    with pytest.raises(ValueError, match="driver read"):
        owner_round.bind("app1", 101, [member], seen={"hwnd": 101, "pid": 7, "class": "App"})
    with pytest.raises(ValueError, match="driver read"):   # a window the driver never read is never bound
        owner_round.bind("app1", 101, [member], seen={})


def test_a_window_check_reads_only_through_the_window_and_a_composition_only_the_fixtures_frame(held, desk):
    window = checked(desk, {**REPORT, "observe": {"window": {"shows": "번역문", "from": "fixture"}}}, driven())
    assert window["status"] == "judge" and window["page"] == PAGE and Path(window["file"]).is_file()
    assert len(desk[0]["window"]) == 1 and not desk[0]["surface"]   # whatever moves over it never enters the read
    composed = checked(desk, SURFACE, driven())
    # Exactly the fixture's frame, with its binding, which the helper proves itself right before and after the blit.
    assert composed["status"] == "judge" and desk[0]["surface"][0][3:5] == (FIXTURE, FIXTURE["rect"])
    # Each `go` is decided under the lock a stop is accepted under.
    assert desk[0]["window"][0][6] is oneshot._lock and desk[0]["surface"][0][7] is oneshot._lock


def test_no_capture_helper_leaves_a_capture_check_pending(held, desk):
    got = checked(desk, SURFACE, driven(), exe=None)
    assert got == {"status": "pending", "why": "도우미가 없다"} and desk[0] == {
        "window": [], "surface": [], "bound": [{"hwnd": 101, "pid": 7, "class": "App"}], "ended": ["fixture1"]}


def test_the_critic_judges_a_capture_against_its_sentence_and_the_page(held, desk, monkeypatch):
    asked = []
    monkeypatch.setattr(oneshot, "turned", lambda store, role, system, text, name, check, tree, images, hide:
                        asked.append((role, text, images, hide)) or check({"shows": False, "why": "검게 비었다"}))
    result = {"status": "judge", "file": str(desk[1] / "capture.png"), "shows": "번역문이 겹친다", "page": PAGE}
    got = pc.judged(oneshot_spec.store("budget"), held, "O3", result)
    assert got == {"status": "failed", "why": "비평가: 검게 비었다"} and asked[0][3] == {}
    # The round's masks reach the turn, so a failed turn's saved reason shows no secret.
    pc.judged(oneshot_spec.store("budget"), held, "O3", result, {"s3cr3t": "[N1]"})
    assert asked[1][3] == {"s3cr3t": "[N1]"}
    role, text, images, _ = asked[0]
    assert role == "critic" and "apple river garden" in text and "번역문이 겹친다" in text and images[0].name == "capture.png"
    # A verdict without its reason is refused, so `turned` asks again instead of taking it.
    monkeypatch.setattr(oneshot, "turned", lambda *a: a[5]({"shows": True}))
    with pytest.raises(ValueError, match="why"):
        pc.judged(oneshot_spec.store("budget"), held, "O3", result)


def ran(monkeypatch, results=None, error: Exception | None = None, during=None, given=None) -> dict:
    """The approved round run by its worker in this thread, `observe` a stand-in returning `results`; `given`, a
    list, gets what each app would start with and what the round masks."""

    def observe(repo, o_, stopped, carry=None, hide=None):
        if given is not None:
            given.append((carry, hide))
        if during:
            during()
        if error:
            raise error
        return dict(results)

    monkeypatch.setattr(pc, "observe", observe)
    pc.approve("budget", approval())
    pc.owner_round("budget")
    return o()


def test_a_round_that_passes_every_owner_check_opens_r3(held, monkeypatch):
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "passed"}})
    assert after["stage"] == "r3" and not after.get("stopped") and after["r3"]["round"] == 1
    assert after["pc"]["status"] == "passed" and after["pc"]["passed"] == ["O2", "O3"] and pc.passed(after)
    assert pc.due(after) is None


def test_a_pass_whose_frozen_copy_changed_since_the_run_goes_back_to_the_build_never_to_r3(held, monkeypatch):
    def changed():   # after the app's own post-run check, before the pass is taken
        (Path(o()["desktop_build"]["folder"]) / "dropped.dll").write_bytes(b"x")

    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "passed"}}, during=changed)
    assert after["stage"] == pc.front_build.STAGE and not pc.passed(after) and after["pc"].get("status") != "passed"


def test_a_pass_whose_head_moved_while_its_copy_was_read_goes_back_to_the_build(held, monkeypatch):
    moved = []
    monkeypatch.setattr(pc, "at_head", lambda repo, head: not moved)
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "passed"}}, during=lambda: moved.append(1))
    assert after["stage"] == pc.front_build.STAGE and not pc.passed(after)


@pytest.mark.parametrize("cut", [False, True])
def test_the_round_starts_its_apps_with_the_owners_secrets_and_keeps_none_of_them(held, monkeypatch, cut):
    # The environment is read once: the apps hold exactly what the round's findings and errors are masked with.
    reads = []
    monkeypatch.setattr(pc.oneshot_needs, "gate_env", lambda spec, env: reads.append(env) or {"API_KEY": "s3cr3t"})
    monkeypatch.setattr(pc.oneshot_needs, "given_in", lambda spec, env, own: {"API_KEY": env["API_KEY"],
                                                                                "own": sorted(own)})
    monkeypatch.setattr(pc.oneshot_needs, "hidden", lambda spec, env: {env["API_KEY"]: "[N1]"})
    monkeypatch.setattr(oneshot_build, "send", lambda *a: None)
    monkeypatch.setattr(oneshot_critique, "front_turn", lambda s: json.dumps(s["oneshot"]["fix"], ensure_ascii=False))
    given = []
    # A failed check's finding, or the app's refusal (`rebuild`), both back to the build.
    after = ran(monkeypatch, error=ValueError("앱이 s3cr3t 로 멈췄다")) if cut else \
        ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "failed", "why": "앱이 s3cr3t 를 보였다"}},
            given=given)
    assert reads == [None] and "s3cr3t" not in str(after) and "[N1]" in str(after)
    if not cut:
        assert given == [({"API_KEY": "s3cr3t", "own": sorted(pc.oneshot_desktop.LOOK_ENV | pc.oneshot_desktop.OWNED_ENV)},
                          {"s3cr3t": "[N1]"})]
        (finding,) = after["fix"]["findings"]
        assert "[N1]" in finding["what"]


def test_a_check_whose_app_shows_a_secret_fails_and_is_never_captured(held, desk):
    reads, root = desk
    hide = {"s3cr3t": "[N1]"}
    report = driven()
    report["states"][-1]["nodes"].append({"window": 101, "id": "token", "name": "S3CR3T", "type": "Text", "depth": 1})
    window = {**REPORT, "observe": {"window": {"shows": "번역문", "from": "fixture"}}}
    for x in (window, REPORT):
        got = pc.one(Session(), Runs(report, root / "data"), 1, x, SCENARIO, "helper.exe", "", root / "keep",
                     lambda: None, hide)
        assert got["status"] == "failed" and "[N1]" in got["why"] and "s3cr3t" not in got["why"].lower(), got
    assert not reads["window"] and not list((root / "keep").iterdir())
    # Without a secret to hide, the same app is checked as before.
    assert checked(desk, window, report)["status"] == "judge"


@pytest.mark.parametrize("what", ["window", "composition"])
def test_with_secrets_only_windows_read_whole_are_captured_for_the_critic(held, desk, what):
    # The step's postcondition reads whole in the main window; the dialog it owns is the one captured, and its read
    # failed, so its text never met the secret check: nothing of it is captured, then read whole, it is.
    reads, root = desk
    report = driven()
    last = report["states"][-1]
    last["windows"].append({"hwnd": 202, "pid": 7, "class": "Dialog", "owner": 101})
    last["reads"].append({"hwnd": 202, "read": False})
    x = {**REPORT, "observe": {what: {"shows": "번역문", "from": "fixture", "in": "owned"}}}

    def run(hide):
        return pc.one(Session(), Runs(report, root / "data"), 1, x, SCENARIO, "helper.exe", "", root / "keep",
                      lambda: None, hide)

    got = run({"s3cr3t": "[N1]"})
    assert got["status"] == "failed" and "비밀 값" in got["why"] and not reads["window"] and not reads["surface"]
    assert run(None)["status"] == "judge"   # without a secret to hide, the same capture goes on as before
    last["reads"][-1]["read"] = True
    assert run({"s3cr3t": "[N1]"})["status"] == "judge"


@pytest.mark.parametrize("late, surfaced", [([{202}], 0), ([set(), {202}], 1)])
def test_with_secrets_a_window_the_app_opens_after_its_read_holds_back_the_composition(held, desk, monkeypatch,
                                                                                         late, surfaced):
    # The read was whole and clean; a dialog the app opens after it, before the helper samples or before the frame
    # is kept, may show on the surface with text the secret check never saw: nothing is kept.
    reads, root = desk
    x = {**REPORT, "observe": {"composition": {"shows": "번역문", "from": "fixture"}}}
    opened = list(late)
    monkeypatch.setattr(owner_round, "shown", lambda members: {101, *(opened.pop(0) if opened else ())})
    got = pc.one(Session(), Runs(driven(), root / "data"), 1, x, SCENARIO, "helper.exe", "", root / "keep",
                 lambda: None, {"s3cr3t": "[N1]"})
    assert got == {"status": "failed", "why": pc.LATE} and len(reads["surface"]) == surfaced
    assert not list((root / "keep").iterdir())
    opened[:] = late
    assert checked(desk, x, driven())["status"] == "judge"   # without a secret, as before


@pytest.mark.parametrize("unread", [{"value": None, "unvalued": True}, {"text": None, "untexted": True}])
def test_with_secrets_a_window_holding_a_value_or_text_never_read_is_never_captured(held, desk, unread):
    # A log whose value or document text was past the bound or unreadable: text the secret check never saw.
    reads, root = desk
    report = driven()
    report["states"][-1]["nodes"].append({"window": 101, "id": "log", "type": "Document", "depth": 1, **unread})
    x = {**REPORT, "observe": {"window": {"shows": "번역문", "from": "fixture"}}}
    got = pc.one(Session(), Runs(report, root / "data"), 1, x, SCENARIO, "helper.exe", "", root / "keep",
                 lambda: None, {"s3cr3t": "[N1]"})
    assert got["status"] == "failed" and "비밀 값" in got["why"] and not reads["window"]
    assert checked(desk, x, report)["status"] == "judge"   # the scenario's own check is not about that text


def test_a_failed_check_goes_to_the_build_as_a_finding(held, monkeypatch):
    sent = []
    monkeypatch.setattr(oneshot_build, "send", lambda *a: sent.append(a))
    monkeypatch.setattr(oneshot_critique, "front_turn", lambda s: json.dumps(s["oneshot"]["fix"], ensure_ascii=False))
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "failed", "why": "비평가: 검게 비었다"}})
    assert after["stage"] == "build-front" and after["working"] == "build-front" and after["critique_base"] == 2
    (finding,) = after["fix"]["findings"]
    assert finding["severity"] == "high" and "O3" in finding["what"] and "검게 비었다" in finding["what"]
    assert "owner's desktop" in after["fix"]["who"] and sent and not pc.passed(after)
    assert after["pc"]["status"] == "waiting" and not after["pc"].get("approval")   # the next round is approved anew


def test_a_check_this_desktop_cannot_read_stays_pending_with_r3_closed(held, monkeypatch):
    before = o()["pc"]["attempt"]
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "pending", "why": "창 캡처 항목이 비었다"}})
    assert after["stage"] == "pc" and after["pc"]["status"] == "waiting" and after["pc"]["attempt"] != before
    assert "O3(창 캡처 항목이 비었다)" in after["pc"]["detail"] and not pc.approved(after) and pc.due(after) == "pc"


def empty_rounds(monkeypatch, tmp_path, look, standing) -> dict:
    """`observe` with no check to run: each round only takes the desktop and opens `look`. The record it reads."""

    import desktop_session

    monkeypatch.setattr(pc.oneshot, "RUNS", tmp_path)   # every repository's records, all under the test's folder
    monkeypatch.setattr(pc.oneshot, "WORK", None)
    monkeypatch.setattr(pc, "base", lambda repo: tmp_path / f"spec-{repo}" / pc.STAGE)
    monkeypatch.setattr(pc, "unrun", lambda o: [])
    monkeypatch.setattr(pc, "confirmed", lambda repo, build: standing())
    monkeypatch.setattr(owner_round, "helper", lambda folder: Path("helper.exe"))
    monkeypatch.setattr(desktop_session, "look", look)
    monkeypatch.setattr(pc.desktop_look, "Launches", lambda *a, **k: type("R", (), {"due": lambda self: None})())
    monkeypatch.setattr(pc.desktop_look, "size", lambda profile: (960, 540))
    monkeypatch.setattr(pc.oneshot_desktop, "owner_scripted", lambda o: [])
    return {"desktop_build": {"checks": {}}, "profile": {}}


def test_rounds_of_two_repositories_take_the_owners_desktop_one_at_a_time_through_cleanup(monkeypatch, tmp_path):
    import contextlib

    order, release = [], threading.Event()

    @contextlib.contextmanager
    def look(folder, key, owned, disposable=False):
        order.append("enter")
        try:
            yield SimpleNamespace(record={"jobs": {}})
            if not release.is_set():   # the first round's look stays up until the test lets it end
                release.wait(10)
        finally:
            order.append("cleaned")

    record = empty_rounds(monkeypatch, tmp_path, look, lambda: True)
    first = threading.Thread(target=pc.observe, args=("a", record, lambda: False))
    first.start()
    while order != ["enter"]:
        threading.Event().wait(0.01)
    second = threading.Thread(target=pc.observe, args=("b", record, lambda: False))
    second.start()
    threading.Event().wait(1.5)
    assert order == ["enter"]   # the second round waits while the first one's windows may stand
    release.set()
    first.join(10)
    second.join(10)
    assert order == ["enter", "cleaned", "enter", "cleaned"]
    # A stop while another round holds the desktop: it raises before any look of its own starts.
    with pc._desk:
        with pytest.raises(RuntimeError, match="기다리다 멈췄다"):
            pc.observe("c", record, lambda: True)
    assert order == ["enter", "cleaned", "enter", "cleaned"] and not pc._desk.locked()


def test_any_repositorys_held_look_record_keeps_every_round_off_the_desktop_restart_or_not(monkeypatch, tmp_path):
    import contextlib

    import desktop_session

    order, standing, made, held, read = [], [True], [], {"spec-a": ["round-1: pid 7 is still running"]}, []
    kept = []

    @contextlib.contextmanager
    def look(folder, key, owned, disposable=False):
        order.append(folder.parent.parent.name)
        kept.append((owned, disposable))
        yield SimpleNamespace(record={"jobs": {}})

    record = empty_rounds(monkeypatch, tmp_path, look, lambda: standing[0])
    monkeypatch.setattr(pc.desktop_look, "Launches", lambda *a, **k: made.append(k) or SimpleNamespace(due=lambda: None))
    # The records on disk are all there is to go by: what a restarted server finds is what this one finds.
    monkeypatch.setattr(desktop_session, "recover_all", lambda root: read.append(root) or held.get(root.parent.parent.name, []))
    (tmp_path / "spec-a" / pc.STAGE / "looks" / "round-1").mkdir(parents=True)   # repository a's earlier round
    with pytest.raises(RuntimeError, match="정리가 확인되지 않았다 — round-1: pid 7 is still running"):
        pc.observe("b", record, lambda: False)
    assert order == [] and not pc._desk.locked() and tmp_path / "spec-a" / pc.STAGE / "looks" in read
    held.clear()   # recovery settled it
    scan = os.scandir
    monkeypatch.setattr(pc.os, "scandir", lambda folder: (_ for _ in ()).throw(PermissionError("denied"))
                        if Path(folder) == tmp_path else scan(folder))
    with pytest.raises(RuntimeError, match="읽지 못했다 — denied"):   # a root it cannot list holds the desktop
        pc.observe("b", record, lambda: False)
    monkeypatch.setattr(pc.os, "scandir", scan)
    assert pc.observe("b", record, lambda: False) == {} and order == ["spec-b"]
    assert callable(made[0]["ready"]) and made[0]["gate"] is pc.oneshot._lock
    assert made[0]["given"] is None and kept == [(False, False)]
    # The owner's secrets: each app starts with them, and the look's raw evidence goes with its cleanup.
    assert pc.observe("b", record, lambda: False, {"API_KEY": "s3cr3t"}, {"s3cr3t": "[N1]"}) == {}
    assert made[1]["given"] == {"API_KEY": "s3cr3t"} and kept[1] == (False, True)
    order.remove("spec-b")
    standing[0] = False   # changed while the round waited: no look opens, and the desktop is free again
    with pytest.raises(pc.Stale):
        pc.observe("a", record, lambda: False)
    assert order == ["spec-b"] and not pc._desk.locked()


def test_a_build_is_confirmed_only_while_its_head_held_through_every_read(monkeypatch):
    heads = iter([True, False])   # HEAD moved while the frozen copy was read
    monkeypatch.setattr(pc, "at_head", lambda repo, head: next(heads))
    monkeypatch.setattr(pc.oneshot_spec, "standing", lambda repo, build: True)
    monkeypatch.setattr(pc.oneshot_desktop, "altered", lambda build: "")
    assert pc.confirmed("r", {"head": "h"}) is False


def test_a_build_changed_before_a_check_starts_neither_its_fixture_nor_its_app(held, desk, monkeypatch):
    reads, root = desk
    monkeypatch.setattr(owner_round, "fixture", lambda *a: pytest.fail("no fixture from a stale build"))

    def standing():
        raise pc.Stale("승인한 회차가 지금 빌드와 다르다 — 다시 승인한다")

    runs = Runs(driven(), root / "data")
    with pytest.raises(pc.Stale, match="다시 승인한다"):
        pc.one(Session(), runs, 1, REPORT, SCENARIO, "helper.exe", "", root / "keep", standing)
    assert runs.drove == [] and reads["ended"] == []


def test_a_build_changed_while_the_round_waited_goes_back_to_approval(held, monkeypatch):
    after = ran(monkeypatch, error=pc.Stale("승인한 회차가 지금 빌드와 다르다 — 다시 승인한다"))
    assert after["stage"] == "pc" and after["pc"]["status"] == "waiting" and "다시 승인한다" in after["pc"]["detail"]
    assert not pc.passed(after) and not after["working"]


def test_a_cleanup_failure_never_passes_the_round(held, monkeypatch):
    after = ran(monkeypatch, error=RuntimeError("주인 화면 회차를 정리하지 못했다 — pid 7 is still running"))
    assert after["stage"] == "pc" and after["pc"]["status"] == "waiting" and "정리하지 못했다" in after["pc"]["detail"]
    assert after["pc"]["last"]["status"] == "error" and not pc.passed(after) and not after.get("r3")


def test_a_stopped_round_never_calls_the_next_stage_even_when_its_checks_passed(held, monkeypatch):
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "passed"}},
                during=lambda: pc.stop("budget"))
    assert after["stage"] == "pc" and after["pc"]["last"]["status"] == "stopped" and not pc.passed(after)
    assert not after.get("r3") and not after["working"]


def test_a_round_uses_its_approval_up_so_the_same_screen_pressed_again_is_refused(held, monkeypatch):
    screen, during = approval(), []
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "pending", "why": "비었다"}},
                during=lambda: during.append((pc.approved(o()), o()["pc"]["ran"]["checks"])))
    assert during == [(None, ["O2", "O3"])]   # used up as the round started, whatever its end
    with pytest.raises(HTTPException, match="지금 회차와 다르다"):
        pc.approve("budget", screen)
    assert after["pc"]["last"]["status"] == "waiting"


def test_a_stop_accepted_before_the_rounds_outcome_is_saved_never_passes(held, monkeypatch):
    pc.approve("budget", approval())
    pc.stop("budget")   # after the round's last check of its own halt, before its outcome is applied
    pc.settled("budget", pc.outcome(o(), {"O2": {"status": "passed"}, "O3": {"status": "passed"}}))
    assert o()["stage"] == "pc" and o()["pc"]["last"]["status"] == "stopped" and not pc.passed(o())


def test_a_shutdown_accepted_before_the_rounds_outcome_is_saved_never_passes(held, monkeypatch):
    pc.approve("budget", approval())
    monkeypatch.setattr(pc.runtime, "stopping", threading.Event())
    pc.runtime.stopping.set()   # after the body's own last check, before `settled`
    pc.settled("budget", pc.outcome(o(), {"O2": {"status": "passed"}, "O3": {"status": "passed"}}))
    assert o()["stage"] == "pc" and o()["pc"]["last"]["status"] == "stopped" and not pc.passed(o())


def test_a_tree_moved_during_the_round_goes_back_to_the_build_unless_a_stop_came_first(held, monkeypatch):
    checks, stop = [], []

    def moved(spec, tree, passed):   # the second check, after the round's body, finds the HEAD moved
        checks.append(1)
        if len(checks) % 2:
            return ""
        if stop:   # the owner's stop accepted during that check
            pc.stop("budget")
        return "HEAD changed"

    monkeypatch.setattr(pc.review, "moved", moved)
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "passed"}})
    assert after["stage"] == "build-front" and after["stopped"]["reason"] == "gate" and not pc.passed(after)
    oneshot_spec.saved("budget", oneshot={"stage": "pc", "stopped": None, "pc": pc.fresh()})
    stop.append(True)
    after = ran(monkeypatch, {"O2": {"status": "passed"}, "O3": {"status": "passed"}})
    assert after["stage"] == "pc" and after["pc"]["last"]["status"] == "stopped" and after["stopped"] is None


def test_a_shutdown_waits_for_an_outcome_being_applied(monkeypatch):
    from main import app

    monkeypatch.setattr(app.runtime, "stopping", threading.Event())
    going = threading.Thread(target=app.going_down)
    with oneshot._lock:   # an outcome being applied: the shutdown is decided after it, never in the middle
        going.start()
        going.join(0.3)
        assert not app.runtime.stopping.is_set()
    going.join(15)
    assert app.runtime.stopping.is_set()


def test_an_app_that_overran_goes_back_to_the_builds_checks_unless_a_stop_came_first(held, monkeypatch):
    after = ran(monkeypatch, error=ValueError("the app overran its deadline"))
    assert after["stage"] == "build-front" and after["stopped"]["reason"] == "gate" and "overran" in after["stopped"]["detail"]
    oneshot_spec.saved("budget", oneshot={"stage": "pc", "stopped": None, "pc": pc.fresh()})
    after = ran(monkeypatch, error=ValueError("the app overran its deadline"), during=lambda: pc.stop("budget"))
    assert after["stage"] == "pc" and after["pc"]["last"]["status"] == "stopped" and after["stopped"] is None


def test_a_round_whose_approval_no_longer_stands_runs_nothing(held, monkeypatch):
    pc.approve("budget", approval())
    oneshot_spec.saved("budget", oneshot={"desktop_build": {**BUILD, "freeze": "f2"}})   # rebuilt after it
    monkeypatch.setattr(pc, "observe", lambda *a: pytest.fail("nothing starts on a stale approval"))
    pc.owner_round("budget")
    assert o()["pc"]["status"] == "waiting" and "다시 승인한다" in o()["pc"]["detail"]


def test_a_capture_the_critic_rejects_fails_its_check(held, monkeypatch):
    monkeypatch.setattr(oneshot_build, "send", lambda *a: None)
    monkeypatch.setattr(oneshot_critique, "front_turn", lambda s: "")
    monkeypatch.setattr(oneshot, "turned", lambda *a: {"shows": False, "why": "번역문이 없다"})
    after = ran(monkeypatch, {"O2": {"status": "passed"},
                              "O3": {"status": "judge", "file": "c1.png", "shows": "번역문", "page": PAGE}})
    assert after["stage"] == "build-front" and "번역문이 없다" in after["fix"]["findings"][0]["what"]
