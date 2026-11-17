"""oneshot step 9c4a: R3 of several kinds — one sheet with a section for each kind, in the owner's order, each read
off that kind alone; the brief's lines name each finding's kind and screen; an edit reaches the builder with its
kind; the approval waits before the pull request, which is the next step's.

See `docs/plans/oneshot/9-kinds.md` ("S7 to R3 — each kind's loop, one R3").
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_brief as brief, oneshot_critique as critique, oneshot_pr, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_critique import LOW, o
from test_oneshot_kinds_critique import kinds, look  # noqa: F401 — `kinds` is a fixture
from test_oneshot_spec import world  # noqa: F401 — `world` is a fixture

WEB_HIGH = {"severity": "high", "screen": "S2", "viewport": "desktop", "state": "full", "what": "표가 넘친다",
            "kind": "webapp"}
ON_PHONE = {"severity": "low", "screen": "S1", "viewport": "phone", "state": "full", "what": "단추가 작다", "kind": "android"}
WEB_LEFT = {**LOW, "route": "/", "what": "목록 여백이 좁다"}
DROID_LEFT = {**LOW, "route": "S1", "viewport": "phone", "what": "단추 그림자가 흐리다"}
PHONE = {"status": "passed", "round": 1, "model": "SM-G991N", "size": {"width": 1080, "height": 2400, "dpi": 420},
         "timing": {"frames": 120, "janky": 2, "janky_percent": 1.6, "p50_ms": 8, "p90_ms": 12, "p99_ms": 20},
         "memory_kb": 81920, "findings": []}


def shot(n, name):
    return f"/look/{n}/{name}"


def phone_shot(n, name):
    return f"/phone/{n}/{name}"


@pytest.fixture
def at_r3(kinds):  # noqa: F811 — the imported fixture, requested by name
    head = o()["looks"][-1]["head"]
    rounds = [{"look": 2, "head": head, "kind": k, "clean": True, "findings": found}
              for k, found in [("android", []), ("android", [DROID_LEFT]), ("webapp", []), ("webapp", [WEB_LEFT])]]
    oneshot_spec.saved("budget", oneshot={
        "stage": "r3", "working": None, "looks": [look(head, 1, [WEB_HIGH]), look(head, 2, [ON_PHONE])],
        "critiques": rounds, "phone": PHONE, "r3": critique.opening(o())})
    # The view's R1 sheet and brief read a whole core design, which this fixture leaves out.
    real = brief.shown
    with patch.object(oneshot_spec.sheet, "sheet", lambda spec: ""), \
            patch.object(oneshot_spec.sheet, "decidable", lambda design, reqs: []), \
            patch.object(brief, "shown", lambda spec, gate="r1": {} if gate == "r1" else real(spec, gate)):
        yield kinds


def on_r3(**fields) -> critique.Decisions:
    return critique.Decisions(source=brief.source(brief.snapshot(oneshot_spec.spec_of("budget"), "r3")), **fields)


def test_the_sheet_has_a_section_for_each_kind_each_with_only_its_own(at_r3):
    sheet = critique.r3_sheet(oneshot_spec.spec_of("budget"), shot, phone_shot)
    droid, web = sheet.split("## 웹앱")
    assert sheet.startswith("# 완성된 화면 — budget") and "## Android 앱" in droid and sheet.count("\n# ") == 0
    assert "### 첫 화면 S1" in droid and "/look/2/S1-phone-full.png" in droid and "/phone/1/S1-phone-full.png" in droid
    assert "단추 그림자가 흐리다" in droid and "단추가 작다" in droid and "목록 여백이 좁다" not in droid
    assert "### 휴대폰에서 — SM-G991N" in droid and "번역 켜기" in droid and "번역 기록" not in droid
    assert "### 첫 화면 S2" in web and "/look/2/S2-desktop-full.png" in web and "/phone/" not in web
    assert "목록 여백이 좁다" in web and "단추" not in web and "휴대폰에서" not in web and "번역 기록" in web
    # The look the web app failed is no kind's "before": both compare round 2 with itself.
    assert "/look/1/" not in sheet.replace("- 1회차", "")
    shown = oneshot_spec.view(oneshot_spec.spec_of("budget"))
    assert shown["r3_items"] == ["S1", "S2"] and shown["r3_sheet"].count("## ") >= 2


def test_the_briefs_lines_name_each_findings_kind_and_its_pictures_its_kinds_shots(at_r3):
    snap = brief.snapshot(oneshot_spec.spec_of("budget"), "r3")
    assert [(f["key"], f["kind"], f["screen"], f["from"]) for f in snap["findings"]] == [
        ("L1", "android", "S1", "critic"), ("L2", "android", "S1", "harness"), ("L3", "webapp", "S2", "critic")]
    assert snap["looks"] == {"before": 2, "after": 2} and snap["phone"]["model"] == "SM-G991N"
    assert "S2 of the 웹앱" in brief.about(snap, "L3") and "S1 of the Android 앱" in brief.about(snap, "L1")
    pictures = critique.brief_pictures(oneshot_spec.spec_of("budget"), shot, phone_shot)
    assert pictures["화면"] == ["/look/2/S1-phone-full.png"] * 2 + ["/look/2/S2-desktop-full.png"] * 2 \
        + ["/phone/1/S1-phone-full.png"]
    assert pictures["L3"] == ["/look/2/S2-desktop-full.png"] and pictures["L2"] == ["/look/2/S1-phone-full.png"]


def test_an_edit_reaches_the_builder_with_its_kind_and_the_approval_waits_before_the_pull_request(at_r3):
    _, sent = at_r3
    critique.decide("budget", on_r3(decisions={"S1": {"approve": True}, "S2": {"edit": "표를 접어라"}}))
    assert o()["stage"] == "build-front" and o()["fix"]["findings"] == [
        {"severity": "high", "screen": "S2", "kind": "webapp", "what": "표를 접어라"}]
    assert "표를 접어라" in sent[-1][2]
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None, "fix": None})
    with patch.object(oneshot_pr, "begin", lambda repo: pytest.fail("the pull request is the next step's")):
        critique.decide("budget", on_r3(decisions={"S2": {"approve": True}}))
    assert o()["stage"] == "pr" and o()["stopped"]["reason"] == "kind" and not o()["working"]
    with patch.object(oneshot, "restarted", lambda store: pytest.fail("a waiting stage is not restarted")):
        oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "kind"
    with pytest.raises(HTTPException):
        oneshot_spec.resume("budget")
    # A stop that replaced the wait (a restart, an owner's stop) gives the wait back, and nothing resumes S8.
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "restart", "detail": "서버를 다시 켰다", "state": "pr"}})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "kind"
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "block", "detail": "멈췄다", "state": "pr"}})
    with pytest.raises(HTTPException):
        oneshot_spec.resume("budget")
    assert o()["stopped"]["reason"] == "kind" and o()["stage"] == "pr"


def test_a_run_an_earlier_version_stopped_before_r3_opens_its_sheet(at_r3):
    oneshot_spec.saved("budget", oneshot={"r3": None, "stopped": {
        "reason": "kind", "state": "r3", "detail": "여러 종류의 R3 시트는 다음 버전에서 이어진다"}})
    oneshot_spec.recover()
    assert o()["stage"] == "r3" and not o().get("stopped") and o()["r3"]["round"] == 1
    assert "r3_sheet" in oneshot_spec.view(oneshot_spec.spec_of("budget"))
