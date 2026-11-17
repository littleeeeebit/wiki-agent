"""oneshot step 9b2: several kinds share one core — S3's module design with a web app's endpoints among them, the
R1 sheet with what each kind uses, and the approval that commits it and waits before the core's tests.

See `docs/plans/oneshot/9-kinds.md` ("S3, R1 and S4 — one core"). Model turns (`oneshot.ask`) are stand-ins; Git
runs in a temporary worktree.
"""

import json
import subprocess
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_brief as brief, oneshot_build, oneshot_sheet as sheet, oneshot_spec, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_core import DESIGN, GLOSSARY, REQS, answer
from test_oneshot_spec import fenced, o, on_sheet, world  # noqa: F401 — `world` is the temporary worktree's fixture

KINDS = ["android", "webapp"]
STACK = [{"part": p, "choice": c, "why": "주인이 골랐다", "status": "confirmed"} for p, c in (
    ("core", "Rust"), ("data_store", "SQLite"), ("tests", "cargo test"), ("android.ui", "Tauri 2"),
    ("android.build", "Gradle"), ("webapp.backend", "Axum"), ("webapp.frontend", "Svelte"),
    ("webapp.styling", "CSS"), ("webapp.deploy", "로컬만"))]
KINDS_REQS = [{**REQS[0], "kinds": ["android"]}, {**REQS[1], "kinds": ["android"]},
              {"id": "R3", "text": "웹에서 번역한 기록을 본다", "source": {"kind": "original", "locator": "주인의 문장"},
               "status": "confirmed", "kinds": ["webapp"], "postconditions": ["번역한 말풍선이 시간 순서로 보인다"]}]
KINDS_DESIGN = {
    **DESIGN,
    "oneshot-modules": [*DESIGN["oneshot-modules"],
                        {"id": "M3", "name": "History", "contract": "번역한 말풍선을 시간 순서로 돌려준다",
                         "inputs": {}, "outputs": {"rows": "List<Row>"}, "errors": [], "state": "", "stores": "",
                         "calls": ["M2"], "depends_on": ["R3"]}],
    "oneshot-tests": [*DESIGN["oneshot-tests"],
                      {"id": "T2", "test_name": "test_history_is_in_time_order", "given": "번역 두 개",
                       "when": "기록을 부르면", "then": "먼저 번역한 것이 먼저 온다", "depends_on": ["R3", "M3"]}],
    "oneshot-api": [{"id": "P1", "method": "GET", "path": "/history", "does": "번역 기록을 돌려준다", "request": {},
                     "response": {"rows": "list"}, "errors": [], "depends_on": ["R3", "M3"]}],
}


@pytest.fixture
def several(world):  # noqa: F811 — the imported fixture, by its name
    oneshot_spec.saved("budget", requirements=KINDS_REQS, oneshot={
        "intent": {"restated": "만화 화면 번역기를 폰 앱과 웹앱으로 만든다.", "kind": "android", "kinds": KINDS},
        "profile": {"auth": "none", "visibility": "private", "docs_language": "ko", "device": "galaxy-s21",
                    "responsive": "both"}, "glossary": GLOSSARY, "stack": STACK, "stage": "design"})
    return world


def test_several_kinds_design_one_core_with_the_web_apps_endpoints(several):
    told = []
    with patch.object(oneshot, "ask", lambda store, role, system, text, *a, **k: told.append(system) or (
            answer(KINDS_DESIGN), {"cost_usd": 0.1}, False)):
        oneshot_spec.design("budget")
    assert "several kinds at once" in told[0] and o()["stage"] == "r1" and o()["design"]["api"][0]["id"] == "P1"
    text = sheet.sheet(specs.load("budget", oneshot.BRANCH))
    assert "| Android 앱 화면 | Tauri 2 |" in text and "| 웹앱 백엔드 | Axum |" in text   # the stack's own parts
    # What each kind uses: an Android-only capability is Android's, and only the web app has the server.
    assert "- Android 앱: 모듈 M1, M2 / 권한 `MediaProjection`" in text
    assert "- 웹앱: 모듈 M2, M3 / 권한 없음 / 엔드포인트 P1" in text
    assert "| P1 | GET | `/history` | 번역 기록을 돌려준다 |" in text and "## 앱 밖으로 나가는 호출" in text
    assert brief.snapshot(specs.load("budget", oneshot.BRANCH))["profile"]["kinds"] == KINDS


@pytest.mark.parametrize("change, said", [
    ({"oneshot-api": None}, "`oneshot-api` 블록"),   # a web app among the kinds has its server
    ({"oneshot-api": [{**KINDS_DESIGN["oneshot-api"][0], "depends_on": ["M3"]}]}, "범위 밖"),
    ({"oneshot-api": [{**KINDS_DESIGN["oneshot-api"][0], "path": "/login"}]}, "로그인 없음"),
    ({"oneshot-modules": [{**KINDS_DESIGN["oneshot-modules"][0], "calls": ["P9"]},
                          *KINDS_DESIGN["oneshot-modules"][1:]]}, r"엔드포인트\(P\)"),
    # The web app's history calls the Android capture adapter: it would lean on Android's permission.
    ({"oneshot-modules": [*KINDS_DESIGN["oneshot-modules"][:2],
                          {**KINDS_DESIGN["oneshot-modules"][2], "calls": ["M1"]}]}, "M1 → R2"),
])
def test_each_check_on_several_kinds_core_refuses(change, said):
    design = {k: v for k, v in {**KINDS_DESIGN, **change}.items() if v is not None}
    with pytest.raises(ValueError, match=said):
        sheet.checked(sheet.parsed(design, KINDS), KINDS_REQS, GLOSSARY, {"auth": "none"}, KINDS)


def test_another_kinds_permission_reached_through_a_requirement_or_an_assumption_is_refused():
    calls_m1 = [*KINDS_DESIGN["oneshot-modules"][:2], {**KINDS_DESIGN["oneshot-modules"][2], "calls": ["M1"]}]
    m1_only_r1 = [{**calls_m1[0], "depends_on": ["R1"]}, *calls_m1[1:]]
    chained = [{**KINDS_REQS[0], "depends_on": ["R2"]}, {**KINDS_REQS[1], "depends_on": []}, KINDS_REQS[2]]
    via_a1 = [{**calls_m1[0], "depends_on": ["R1", "A1"]}, *calls_m1[1:]]
    a1_r2 = [{**KINDS_DESIGN["oneshot-assumptions"][0], "depends_on": ["R2"]}]
    for modules, reqs, assumptions in ((m1_only_r1, chained, KINDS_DESIGN["oneshot-assumptions"]),
                                       (via_a1, KINDS_REQS, a1_r2)):
        design = {**KINDS_DESIGN, "oneshot-modules": modules, "oneshot-assumptions": assumptions}
        with pytest.raises(ValueError, match="M1 → R2"):
            sheet.checked(sheet.parsed(design, KINDS), reqs, GLOSSARY, {"auth": "none"}, KINDS)
        parsed = sheet.parsed(design, KINDS)
        assert ("M1", "R2") in sheet.asked_by(parsed, reqs, "android")   # the sheet's list reads the same chain


def test_an_r1_edit_keeps_each_requirements_kinds_as_the_owner_settled_them(several):
    spec = {**specs.load("budget", oneshot.BRANCH)}
    spec["oneshot"] = {**spec["oneshot"], "r1": {"round": 1}, "design": sheet.parsed(KINDS_DESIGN, KINDS)}
    for scope in ({"kinds": ["webapp"]}, {}):   # moved to another kind, or widened to every kind
        moved = [{k: v for k, v in r.items() if k != "kinds"} | (scope if r["id"] == "R2" else
                                                                 {"kinds": r["kinds"]}) for r in KINDS_REQS]
        found = {block: value for block, value in KINDS_DESIGN.items()}
        final = answer(found) + fenced("oneshot-spec", {"requirements": moved, "glossary": GLOSSARY})
        _, reqs, _, _ = oneshot_spec.design_turn(final, 1, spec, set(sheet.every(sheet.parsed(KINDS_DESIGN, KINDS),
                                                                                  KINDS_REQS)))
        assert [r.get("kinds") for r in reqs] == [["android"], ["android"], ["webapp"]]


def test_several_kinds_without_a_web_app_have_no_endpoints():
    assert "oneshot-api" not in sheet.blocks(["android", "desktop"])
    sheet.checked(sheet.parsed(DESIGN, ["android", "desktop"]), REQS, GLOSSARY, {"auth": "password"})


def test_approving_several_kinds_core_commits_it_and_waits_before_its_tests(several):
    oneshot_spec.saved("budget", oneshot={"stage": "r1", "design": sheet.parsed(KINDS_DESIGN, KINDS),
                                          "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": []}})
    items = sheet.decidable(o()["design"], KINDS_REQS)
    with patch.object(oneshot_build, "begin", lambda repo: pytest.fail("the tests turn is the next step's")):
        oneshot_spec.decide("budget", on_sheet(decisions={i: {"approve": True} for i in items}))
    assert o()["stage"] == "tests" and o()["stopped"]["reason"] == "kind" and not o().get("working")
    assert json.loads((several / "docs/design/core.json").read_text(encoding="utf-8"))["api"][0]["id"] == "P1"
    assert "/history" in json.loads((several / "docs/design/api.yaml").read_text(encoding="utf-8"))["paths"]
    shown = subprocess.run(["git", "show", "--stat", "HEAD"], cwd=several, capture_output=True, text=True).stdout
    assert "core.md" in shown and "api.yaml" in shown and "core design" in shown
    with pytest.raises(HTTPException):   # neither `[재개]` nor a restart takes the tests on
        oneshot_spec.resume("budget")
    with patch.object(oneshot, "restarted", lambda store: pytest.fail("a waiting stage is not restarted")):
        oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "kind"


def test_a_run_of_several_kinds_an_earlier_version_stopped_at_the_design_is_taken_on(several):
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "kind", "detail": "다음 버전", "state": "design"}})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    started = []
    with patch.object(oneshot_spec, "start", lambda repo, *a, **k: started.append(a)):
        oneshot_spec.resume("budget")
    assert started == [(oneshot_spec.RUNNING["design"],)]
