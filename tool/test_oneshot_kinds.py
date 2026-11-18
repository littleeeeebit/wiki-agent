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

from main import (oneshot, oneshot_brief as brief, oneshot_build, oneshot_front, oneshot_profile, oneshot_review,
                  oneshot_sheet as sheet, oneshot_spec, specs)
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


def test_a_kind_reaches_what_its_endpoints_are_built_on():
    via_p1 = [*KINDS_DESIGN["oneshot-modules"][:2], {**KINDS_DESIGN["oneshot-modules"][2], "calls": ["P1"]}]
    bridge = [{**KINDS_DESIGN["oneshot-api"][0], "depends_on": ["R3", "M1"]}]   # the server calls Android's capture
    design = {**KINDS_DESIGN, "oneshot-modules": via_p1, "oneshot-api": bridge}
    with pytest.raises(ValueError, match="M1 → R2"):
        sheet.checked(sheet.parsed(design, KINDS), KINDS_REQS, GLOSSARY, {"auth": "none"}, KINDS)
    # An endpoint built on the shared core only is fine, and its modules are the web app's.
    shared = {**design, "oneshot-api": [{**bridge[0], "depends_on": ["R3", "M2"]}]}
    sheet.checked(parsed := sheet.parsed(shared, KINDS), KINDS_REQS, GLOSSARY, {"auth": "none"}, KINDS)
    assert sheet.used_by(parsed, KINDS_REQS, "webapp") == ["M2", "M3"]
    assert sheet.reach(parsed, KINDS_REQS, "android") == {"M1", "M2"}   # Android carries no endpoint of its own
    # The web server runs every endpoint, even one only Android's requirement names.
    for_android = {**KINDS_DESIGN, "oneshot-api": [{**bridge[0], "depends_on": ["R1", "M1"]}]}
    with pytest.raises(ValueError, match="M1 → R2"):
        sheet.checked(sheet.parsed(for_android, KINDS), KINDS_REQS, GLOSSARY, {"auth": "none"}, KINDS)
    fine = {**KINDS_DESIGN, "oneshot-api": [{**bridge[0], "depends_on": ["R1", "M2"]}]}
    sheet.checked(parsed := sheet.parsed(fine, KINDS), KINDS_REQS, GLOSSARY, {"auth": "none"}, KINDS)
    assert "P1" in sheet.reach(parsed, KINDS_REQS, "webapp")


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


def test_approving_several_kinds_core_commits_it_and_starts_its_tests(several):
    oneshot_spec.saved("budget", oneshot={"stage": "r1", "design": sheet.parsed(KINDS_DESIGN, KINDS),
                                          "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": []}})
    items = sheet.decidable(o()["design"], KINDS_REQS)
    begun = []
    with patch.object(oneshot_build, "begin", begun.append):
        oneshot_spec.decide("budget", on_sheet(decisions={i: {"approve": True} for i in items}))
    assert begun == ["budget"] and o()["stage"] == "tests" and not o().get("stopped")
    assert json.loads((several / "docs/design/core.json").read_text(encoding="utf-8"))["api"][0]["id"] == "P1"
    assert "/history" in json.loads((several / "docs/design/api.yaml").read_text(encoding="utf-8"))["paths"]
    shown = subprocess.run(["git", "show", "--stat", "HEAD"], cwd=several, capture_output=True, text=True).stdout
    assert "core.md" in shown and "api.yaml" in shown and "core design" in shown


def test_several_kinds_test_and_build_the_core_alone_with_its_tests_as_the_gate(several):
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(KINDS_DESIGN, KINDS), "tests_head": "abc",
                                          "gate": "cargo test", "contract": ["core/tests/seen.rs"]})
    spec = specs.load("budget", oneshot.BRANCH)
    with patch.object(oneshot_build.oneshot_android, "build_env", lambda: pytest.fail("no Android toolchain")):
        assert oneshot_build.toolchain(spec) is None
        tests, build = oneshot_build.tests_text(spec), oneshot_build.build_text(spec)
    assert "core a new product's kinds share" in tests and "{toolchain}" not in tests and "debug APK" not in tests
    assert "every kind they\npicked shares" in build and "`cargo test`" in build
    (several / "core/tests").mkdir(parents=True)
    (several / "core/tests/seen.rs").write_text("fn test_a_box_already_translated_is_skipped() {}")
    runner = {"command": "cargo test -- -Z unstable-options --format junit > {report}", "contract": ["core/tests/seen.rs"]}
    assert oneshot_build.declared(several, fenced("oneshot-runner", runner), "kinds")["after"] == []   # no lint, no APK


DEFER = {"location": "core/src/overlay.rs:18", "severity": "P1", "reason": "상태바를 가린다", "evidence": "insets 0",
         "device": {"kind": "android", "requirement": "R1", "check": "오버레이가 상태바 아래에서 시작한다"}}


def test_a_finding_only_a_device_can_show_waits_for_it_and_blocks_nothing():
    devices = oneshot_review.deferrable({"oneshot": {"intent": {"kind": "android", "kinds": KINDS}},
                                         "requirements": KINDS_REQS})
    assert devices == {"android": {"R1", "R2"}}   # a web app has no device, and R3 is the web app's alone
    approve = lambda *fs: {"verdict": "approve", "head": "h", "findings": list(fs)}   # noqa: E731
    assert oneshot_review.verdict_of(approve(DEFER), "h", devices)["findings"] == [DEFER]
    with pytest.raises(ValueError, match="하나 이상"):
        oneshot_review.verdict_of({**approve(DEFER), "verdict": "refuse"}, "h", devices)
    for bad in ({"kind": "webapp"}, {"requirement": "R3"}, {"check": " "}, {"kind": None}):
        with pytest.raises(ValueError, match="`device`"):
            oneshot_review.verdict_of(approve({**DEFER, "device": {**DEFER["device"], **bad}}), "h", devices)
    with pytest.raises(ValueError, match="`device`"):
        oneshot_review.verdict_of(approve({**DEFER, "severity": "P2"}), "h", devices)
    alone = oneshot_review.deferrable({"oneshot": {"intent": {"kind": "webapp"}}, "requirements": REQS})
    with pytest.raises(ValueError, match="이 실행엔 없다"):
        oneshot_review.verdict_of(approve(DEFER), "h", alone)
    android = oneshot_review.deferrable({"oneshot": {"intent": {"kind": "android"}}, "requirements": REQS})
    assert android == {"android": {r["id"] for r in REQS}}


def test_the_approving_reviews_deferrals_are_kept_owed_and_never_sent_to_the_builder(several):
    oneshot_spec.saved("budget", oneshot={"stage": "review", "working": "review", "built_head": "abc",
                                          "design": sheet.parsed(KINDS_DESIGN, KINDS), "tests_head": "t",
                                          "contract": ["core/tests/seen.rs"], "gate": "cargo test"})
    store, spec = oneshot_spec.store("budget"), specs.load("budget", oneshot.BRANCH)
    blocking = {**DEFER, "location": "core/src/seen.rs:3", "reason": "같은 상자를 두 번 번역한다"}
    blocking.pop("device")
    sent = oneshot_review.fixing(spec, {"head": "abc", "findings": [blocking, DEFER]})
    assert "같은 상자를 두 번" in sent and "상태바를 가린다" not in sent
    oneshot_review.decided("budget", store, spec, {"verdict": "approve", "head": "abc", "findings": [DEFER]})
    assert o()["owed"] == [{"id": "O1", **DEFER["device"], **{k: DEFER[k] for k in ("location", "severity", "reason",
                                                                                      "evidence")}, "head": "abc"}]
    assert o()["stage"] == "split" and o()["working"] == "split" and not o().get("stopped")


def test_an_approved_review_of_several_kinds_goes_on_to_the_split(several):
    oneshot_spec.saved("budget", oneshot={"stage": "review", "working": "review", "built_head": "abc",
                                          "design": sheet.parsed(KINDS_DESIGN, KINDS), "tests_head": "t",
                                          "contract": ["core/tests/seen.rs"]})
    store, spec = oneshot_spec.store("budget"), specs.load("budget", oneshot.BRANCH)
    assert "reviewer of the core a new product's kinds" in oneshot.prompt("review", "kinds")
    assert "this repository's core build" in oneshot_review.review_text(
        {**spec, "start_head": "base"}, "abc", "read it")
    started = []
    with patch.object(oneshot_spec, "handoff", lambda *a, **k: started.append(a)):
        following = oneshot_review.decided("budget", store, spec, {"verdict": "approve", "head": "abc", "findings": []})
        assert following() and started == [("budget", "split")]
    assert o()["stage"] == "split" and o()["working"] == "split" and o()["reviews"][-1]["verdict"] == "approve"


def test_several_kinds_split_gives_each_feature_the_kinds_all_its_parts_share(several):
    oneshot_spec.saved("budget", oneshot={"design": sheet.parsed(KINDS_DESIGN, KINDS)})
    spec = specs.load("budget", oneshot.BRANCH)
    parts = oneshot_front.part_kinds(spec)
    assert parts["R2"] == ["android"] and parts["P1"] == ["webapp"] and "M3" in parts
    assert set(oneshot_front.inventory(spec)) == set(parts) and "P1" in oneshot_front.inventory(spec)
    shown = oneshot_front.listed(spec, oneshot_front.inventory(spec))
    assert "`P1` `GET /history`" in shown and "(used by: android)" in shown
    found = [{"id": "a", "parts": ["R2"]}, {"id": "b", "parts": ["P1"]}]
    assert [f["kinds"] for f in oneshot_front.with_kinds(found, parts)] == [["android"], ["webapp"]]
    with pytest.raises(ValueError, match="함께 걸린 종류가 없다 — R2\\(Android 앱\\), P1\\(웹앱\\)"):
        oneshot_front.with_kinds([{"id": "c", "parts": ["R2", "P1"]}], parts)
    assert "Never group parts that share no kind" in oneshot.prompt("split", "kinds")
    oneshot_spec.saved("budget", oneshot={"intent": {**o()["intent"], "first_screen": "번역을 켠다"}})
    spec, ops = specs.load("budget", oneshot.BRANCH), oneshot_front.inventory(spec)
    value = [{"id": f"f{n}", "name": "기능", "parts": [p], "class": "every", "why": "늘"} for n, p in enumerate(ops)]
    asked = []

    def turned(store, role, system, text, block, check, tree):
        asked.append(system)
        return check(value)

    with patch.object(oneshot, "turned", turned):
        oneshot_front.split_turn("budget", oneshot_spec.store("budget"), spec)()
    assert asked == [oneshot.prompt("split", "kinds")] and o()["stage"] == "board"
    assert {f["parts"][0]: f["kinds"] for f in o()["features"]} == parts
    assert o()["first_screen"] == {k: "번역을 켠다" for k in KINDS}


def test_several_kinds_board_takes_a_first_screen_for_each_kind_then_designs_the_screens(several):
    features = [{"id": "read", "name": "읽기", "parts": ["R2", "M1"], "class": "every", "why": "늘", "kinds": ["android"]},
                {"id": "history", "name": "기록", "parts": ["P1"], "class": "sometimes", "why": "가끔", "kinds": ["webapp"]}]
    oneshot_spec.saved("budget", oneshot={"stage": "board", "features": features, "working": None,
                                          "first_screen": {k: "번역을 켠다" for k in KINDS}})
    board = oneshot_spec.view(oneshot_spec.spec_of("budget"))["board"]
    assert board["kinds"] == [{"id": "android", "name": "Android 앱"}, {"id": "webapp", "name": "웹앱"}]
    frequency = {"read": "every", "history": "rarely"}
    with pytest.raises(HTTPException, match="종류마다"):
        oneshot_front.submit_board("budget", oneshot_front.Board(frequency=frequency, first_screen="한 줄"))
    started = []
    with patch.object(oneshot_spec, "start", lambda repo, worker, *a, **k: started.append((worker, k))):
        oneshot_front.submit_board("budget", oneshot_front.Board(frequency=frequency, first_screen={
            "android": "  번역을   켠다 ", "webapp": "기록을 본다"}))
    assert [w for w, _ in started] == ["frontend"] and not o().get("stopped")
    assert started[0][1]["first_screen"] == {"android": "번역을 켠다", "webapp": "기록을 본다"}
    assert started[0][1]["frequency"] == frequency


def test_several_kinds_with_desktop_wait_where_desktop_does(several):
    assert oneshot_profile.waits_at({"intent": {"kinds": ["webapp", "android"]}}) is None   # runs to its PR
    assert oneshot_profile.waits_at({"intent": {"kinds": ["android", "desktop"]}}) == "tests"   # plan 9e2a


@pytest.mark.parametrize("stage", ["design", "tests", "split", "build-front"])
def test_a_run_of_several_kinds_an_earlier_version_stopped_is_taken_on(several, stage):
    oneshot_spec.saved("budget", oneshot={"stage": stage, "stopped": {"reason": "kind", "detail": "다음 버전",
                                                                      "state": stage}})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    started = []
    with patch.object(oneshot_spec, "start", lambda repo, *a, **k: started.append(a)), \
            patch.object(oneshot_build, "resume", lambda repo: started.append(("tests",))):
        oneshot_spec.resume("budget")
    assert started == ([("tests",)] if stage in ("tests", "build-front") else [(oneshot_spec.RUNNING[stage],)])
