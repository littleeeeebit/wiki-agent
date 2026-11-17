"""oneshot step 9c2: several kinds' screens — S6 designs each kind's screens its own way, each kind checked on its
own features, and R2 shows them in one sheet grouped by kind; its approval commits them and waits before the build.

See `docs/plans/oneshot/9-kinds.md` ("S5 to R2 — screens for each kind"). Model turns are stand-ins; the Android
preview is a stand-in script, and the web mocks are shot by the real Playwright when it is installed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import (oneshot, oneshot_brief as brief, oneshot_front as front, oneshot_screens as screens,
                  oneshot_sheet as sheet, oneshot_spec, specs)
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_android_front import android, blocks as phone_blocks, mocks  # noqa: F401 — `android`: fixture
from test_oneshot_front import answer, head, o, playwright_or_skip, replies
from test_oneshot_kinds import KINDS, KINDS_DESIGN, KINDS_REQS
from test_oneshot_spec import world  # noqa: F401 — `world` is the temporary worktree's fixture

FEATURES = [
    {"id": "start-translation", "name": "번역 켜기", "parts": ["R2", "M1"], "class": "every", "why": "만화를 열 때마다 켠다",
     "kinds": ["android"]},
    {"id": "history", "name": "번역 기록", "parts": ["P1", "M3"], "class": "every", "why": "웹에서 늘 본다",
     "kinds": ["webapp"]},
    {"id": "seen-boxes", "name": "번역한 말풍선 기억", "parts": ["M2"], "class": "never", "why": "사람이 만질 일이 없다",
     "kinds": ["android", "webapp"]},
]
FREQUENCY = {"start-translation": "every", "history": "every", "seen-boxes": "never"}
PAGE = '<!doctype html><html><body><main data-feature="history">번역 기록</main></body></html>'


def blocks(**changes) -> dict:
    """Two Android screens drawn by the preview (S1, S2) and one web page (S3)."""

    phone = phone_blocks()
    found = {
        "oneshot-screens": [{**s, "kind": "android"} for s in phone["oneshot-screens"]] + [
            {"id": "S3", "kind": "webapp", "route": "/", "purpose": "번역한 말풍선을 시간 순서로 본다",
             "features": ["history"], "primary": True, "reached": "start"}],
        "oneshot-states": phone["oneshot-states"] + [{**phone["oneshot-states"][0], "screen": "S3"}],
        "oneshot-tokens": phone["oneshot-tokens"],
        "oneshot-mocks": {"android": mocks(), "webapp": {"S3": PAGE}},
    }
    found.update(changes)
    return found


@pytest.fixture
def kinds(android):  # noqa: F811 — the imported fixture, by its name
    oneshot_spec.saved("budget", requirements=KINDS_REQS, oneshot={
        "intent": {**o()["intent"], "kinds": KINDS}, "design": sheet.parsed(KINDS_DESIGN, KINDS),
        "profile": {**o()["profile"], "responsive": "desktop"}, "stage": "frontend", "features": FEATURES,
        "frequency": FREQUENCY, "first_screen": {"android": "만화를 읽으며 번역을 켠다", "webapp": "지난 번역을 찾아본다"}})
    return android


def checked(found: dict) -> dict:
    return screens.design_kinds(found, FEATURES, FREQUENCY, KINDS, ["GET /history"])


def test_each_kind_is_checked_on_its_own_features():
    made = checked(blocks())
    assert [(s["id"], s["kind"]) for s in made["screens"]] == [("S1", "android"), ("S2", "android"), ("S3", "webapp")]
    assert made["preview"]["screens"] == {"S1": "mock/S1.kt", "S2": "mock/S2.kt"} and made["mocks"]["S3"] == PAGE
    assert set(made["states"]) == {"S1", "S2", "S3"}


def screen(sid: str, **change) -> list[dict]:
    return [{**s, **change} if s["id"] == sid else s for s in blocks()["oneshot-screens"]]


@pytest.mark.parametrize("change, said", [
    # A web feature on an Android screen: each kind's screens hold only its own features.
    ({"oneshot-screens": screen("S1", features=["start-translation", "history"])}, "android: 화면 S1: 없는 기능이다 — history"),
    ({"oneshot-screens": screen("S3", primary=False, reached="one-click")}, r"webapp: 첫 화면\(`primary`"),
    ({"oneshot-screens": screen("S3", route="/history")}, "webapp: 화면 경로가 API 경로와 겹친다"),
    ({"oneshot-screens": screen("S3", kind="desktop")}, "고른 종류"),
    ({"oneshot-screens": screen("S3", id="S2")}, "통틀어 겹치지"),
    ({"oneshot-screens": blocks()["oneshot-screens"][:2], "oneshot-states": blocks()["oneshot-states"][:2]},
     "webapp 의 화면이 없다"),
    ({"oneshot-mocks": mocks()}, "종류마다"),
    ({"oneshot-mocks": {"android": mocks(), "webapp": {"S3": "<p>기록</p>"}}}, "webapp: S3 목업: `data-feature`"),
])
def test_a_kinds_screen_outside_its_kinds_rules_is_refused(change, said):
    with pytest.raises(ValueError, match=said):
        checked(blocks(**change))


def test_each_kind_is_designed_and_shot_its_own_way_and_r2_groups_them(kinds):
    playwright_or_skip()
    heard, asking = replies(answer(blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2", o().get("stopped")
    assert sorted(p.name for p in front.shots("budget", 1).iterdir() if p.suffix == ".png") == [
        "S1-phone.png", "S2-phone.png", "S3-desktop.png"]
    told = heard[0][1]
    assert "Galaxy S21" in told and "The web viewports shot: desktop" in told and '"webapp": "지난 번역을 찾아본다"' in told
    shown = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))
    text = shown["front_sheet"]
    assert text.index("## Android 앱") < text.index("### 첫 화면 S1 다른 앱 위") < text.index("## 웹앱") \
        < text.index("> 첫 화면은 지난 번역을 찾아본다") < text.index("### 첫 화면 S3 `/`")
    # Each kind's placement table lists only that kind's features.
    android_part = text[text.index("## Android 앱"):text.index("## 웹앱")]
    assert "번역 켜기" in android_part and "번역 기록 (`history`)" not in android_part
    assert shown["front_items"] == ["S1", "S1.states", "S2", "S2.states", "S3", "S3.states"]
    assert shown["front_brief"]["pictures"]["S3"][0].endswith("/S3-desktop.png")
    assert shown["front_brief"]["pictures"]["S1"][0].endswith("/S1-phone.png")


def test_an_edit_keeps_the_web_pages_and_the_previews_shared_files(kinds):
    playwright_or_skip()
    _, asking = replies(answer(blocks()))
    with asking:
        front.frontend("budget")
    edit = blocks(**{"oneshot-screens": screen("S3", purpose="지난 번역을 날짜별로 본다")})
    heard, asking = replies(answer(edit))
    with asking:
        front.refront("budget", {"S3": "날짜별로"}, ["S3", "S3.states"])
    current = json.loads(heard[0][1].split("The current frontend design:\n\n")[1].split("\n\n")[0])
    assert current["mocks"] == {"webapp": {"S3": PAGE}} and o()["r2"]["round"] == 2


def test_the_approved_screens_of_every_kind_are_committed_and_the_run_waits_before_the_build(kinds):
    playwright_or_skip()
    _, asking = replies(answer(blocks()))
    with asking:
        front.frontend("budget")
    with patch.object(front.oneshot_front_build, "begin", lambda repo: pytest.fail("the build is the next step's")):
        front.decide("budget", front.R2(decisions={i: {"approve": True} for i in screens.decidable(o()["front"])},
                                        palette="C1", source=brief.source(brief.snapshot(oneshot_spec.spec_of("budget"), "r2"))))
    assert o()["stage"] == "build-front" and o()["stopped"]["reason"] == "kind" and not o().get("working")
    for name in ("docs/design/screens.md", "docs/design/mock/mock/S1.kt", "docs/design/mock/S3.html",
                 "docs/design/shots/S1-phone.png", "docs/design/shots/S3-desktop.png"):
        assert (kinds / name).is_file(), name
    assert o()["front_head"] == head(kinds)
    with pytest.raises(HTTPException):   # `[재개]` does not take the build on either
        oneshot_spec.resume("budget")
    with patch.object(oneshot, "restarted", lambda store: pytest.fail("a waiting stage is not restarted")):
        oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "kind" and o()["stage"] == "build-front"
