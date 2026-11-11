"""oneshot step 4a: the feature split read from the approved API, the board, the frontend design and R2.

See `docs/plans/oneshot/4-frontend.md` ("S5", "S6", "R2"). Model turns (`oneshot.ask`)
are stand-ins; Git, the project's interpreter and Playwright run for real.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import (oneshot, oneshot_build, oneshot_front as front, oneshot_screens as screens, oneshot_sheet as sheet,
                  oneshot_spec, query, specs, work)
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import DESIGN, fenced, world  # noqa: F401 — `world` is a fixture

OPS = ["GET /export", "GET /spendings", "POST /spendings"]
FEATURES = [
    {"id": "add-spending", "name": "지출 적기", "endpoints": ["POST /spendings"], "class": "every", "why": "매일 쓴다"},
    {"id": "month-list", "name": "달별 목록", "endpoints": ["GET /spendings"], "class": "sometimes", "why": "가끔 본다"},
    {"id": "export", "name": "내보내기", "endpoints": ["GET /export"], "class": "rarely", "why": "한 해에 한 번"},
]
FREQUENCY = {"add-spending": "every", "month-list": "sometimes", "export": "rarely"}


def head(tree) -> str:
    """The HEAD the stages after the build read: the one its checks passed."""

    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, check=True, capture_output=True, text=True).stdout.strip()


def mock(*features, extra=""):
    buttons = "".join(f'<button data-feature="{f}">{f}</button>' for f in features)
    return f"<!doctype html><html><head><meta charset='utf-8'></head><body><main>{buttons}</main>{extra}</body></html>"


def blocks(**changes) -> dict:
    found = {
        "oneshot-screens": [
            {"id": "S1", "route": "/", "purpose": "오늘 쓴 돈을 바로 적는다", "features": ["add-spending"],
             "primary": True, "reached": "start"},
            {"id": "S2", "route": "/months", "purpose": "지난 달 지출을 훑어본다", "features": ["month-list"],
             "primary": False, "reached": "one-click"},
            {"id": "S3", "route": "/settings", "purpose": "자료를 파일로 받는다", "features": ["export"],
             "primary": False, "reached": "deeper"}],
        "oneshot-states": [{"screen": s, "empty": "아직 지출이 없다", "loading": "회색 줄 세 개", "error": "다시 시도 단추",
                            "full": "지출 스무 건"} for s in ("S1", "S2", "S3")],
        "oneshot-tokens": {"palettes": [
            {"id": "C1", "name": "차분한 초록", "colors": {"primary": "#2f6f4e", "surface": "#ffffff", "text": "#1a1a1a",
                                                     "accent": "#e0a000"}},
            {"id": "C2", "name": "짙은 남색", "colors": {"primary": "#1f3a68", "surface": "#f7f7f5", "text": "#111111",
                                                   "accent": "#d0473a"}}],
            "type": ["14px", "16px", "20px", "28px"], "spacing": [4, 8, 16, 24]},
        "oneshot-mocks": {"S1": mock("add-spending"), "S2": mock("month-list"), "S3": mock("export")},
    }
    found.update(changes)
    return found


def answer(found: dict) -> str:
    return "화면을 설계했다.\n\n" + "".join(fenced(k, v) for k, v in found.items())


def replies(*texts):
    heard, queue = [], list(texts)

    def ask(store, role, system, text, where=None, images=()):
        heard.append((role, text))
        return queue.pop(0), {"cost_usd": 0.1}, False

    return heard, patch.object(oneshot, "ask", ask)


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def playwright_or_skip():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:   # no package or no browser on this machine: the shooting tests cannot run
        pytest.skip(f"Playwright 가 없다 — {exc}")


@pytest.fixture
def boarded(world):  # noqa: F811 — the imported fixture, requested by name
    oneshot_spec.saved("budget", oneshot={
        "stage": "frontend", "features": FEATURES, "frequency": FREQUENCY, "first_screen": "오늘 쓴 돈을 적는다",
        "design": {}, "glossary": [], "profile": {**o()["profile"], "responsive": "desktop"}, "built_head": head(world)})
    return world


def designed(tree) -> None:
    playwright_or_skip()
    _, asking = replies(answer(blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2", o().get("stopped")


# -- S5 ------------------------------------------------------------------------------------

def test_the_inventory_is_the_approved_api_never_a_file_the_build_can_edit(world):  # noqa: F811
    design = sheet.parsed(DESIGN)
    oneshot_spec.saved("budget", oneshot={"design": design})
    (world / "docs" / "design").mkdir(parents=True)
    (world / "docs" / "design" / "api.yaml").write_text('{"paths": {"/all": {"delete": {}}}}', encoding="utf-8")
    assert front.served(specs.load("budget", oneshot.BRANCH)) == sorted(f"{p['method']} {p['path']}" for p in design["api"])


@pytest.mark.parametrize("change, said", [
    (lambda f: f[0]["endpoints"].append("DELETE /all"), "서버에 없는"),
    (lambda f: f.pop(), "어느 기능에도 없는"),
    (lambda f: f[1]["endpoints"].append("POST /spendings"), "둘에 들어"),
    (lambda f: f[0].update(id="Add"), "소문자"),
])
def test_a_feature_list_that_invents_drops_or_shares_an_operation_is_refused(change, said):
    found = json.loads(json.dumps(FEATURES))
    change(found)
    with pytest.raises(ValueError, match=said):
        screens.features(found, OPS)


def test_the_split_turn_names_each_operation_and_opens_the_board(world):  # noqa: F811
    oneshot_spec.saved("budget", oneshot={"stage": "split", "intent": {**o()["intent"], "first_screen": "오늘 지출"},
                                          "built_head": head(world)})
    heard, asking = replies(fenced("oneshot-features", FEATURES))
    with asking, patch.object(front, "served", lambda spec: OPS):
        front.split("budget")
    assert o()["stage"] == "board" and o()["features"][0]["id"] == "add-spending" and o()["first_screen"] == "오늘 지출"
    assert all(f"`{op}`" in heard[0][1] for op in OPS)
    board = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))["board"]
    assert board["frequency"] == FREQUENCY   # the model's proposal, until the owner submits


def test_the_split_holds_the_tree_and_sends_the_queued_instruction_when_it_lets_go(world):  # noqa: F811
    oneshot_spec.saved("budget", oneshot={"stage": "split", "built_head": head(world), "first_screen": "오늘 지출"})
    begun = []

    def served(spec):
        with pytest.raises(HTTPException):   # no turn of the person's writes the app it reads
            query.hold(work._busy, query._lock, str(world), "busy")
        work._queued[str(world)] = work.Order(path=str(world), text="다음 지시")
        return OPS

    _, asking = replies(fenced("oneshot-features", FEATURES))
    try:
        with asking, patch.object(front, "served", served), \
             patch.object(work, "session", lambda *a: SimpleNamespace(id="c", parent_id=None)), \
             patch.object(work, "begin", lambda path, chat, text, release, run: (begun.append(text), release())):
            front.split("budget")
        assert o()["stage"] == "board" and begun == ["다음 지시"] and str(world) not in work._busy
    finally:
        work._queued.pop(str(world), None)


def test_a_backend_changed_while_the_split_read_it_goes_back_to_the_builds_checks(world):  # noqa: F811
    oneshot_spec.saved("budget", oneshot={"stage": "split", "built_head": head(world), "first_screen": "오늘 지출"})

    def served(spec):
        (world / "late.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "late.py"], cwd=world, check=True)
        subprocess.run(["git", "commit", "-qm", "late"], cwd=world, check=True)
        return OPS

    _, asking = replies(fenced("oneshot-features", FEATURES))
    with asking, patch.object(front, "served", served):
        front.split("budget")
    assert o()["stage"] == "build" and o()["stopped"]["reason"] == "gate" and not o().get("features")


def test_the_board_needs_every_feature_placed_and_starts_the_design_from_scratch(world):  # noqa: F811
    oneshot_spec.saved("budget", oneshot={"stage": "board", "features": FEATURES, "front": {"old": 1}})
    with pytest.raises(HTTPException):
        front.submit_board("budget", front.Board(frequency={"add-spending": "every"}, first_screen="x"))
    front.submit_board("budget", front.Board(frequency={**FREQUENCY, "export": "never"}, first_screen=" 오늘  쓴 돈 "))
    assert o()["working"] == "frontend" and o()["frequency"]["export"] == "never" and o()["front"] is None
    assert o()["first_screen"] == "오늘 쓴 돈"


# -- S6 placement checks ---------------------------------------------------------------------

def screens_with(changes: dict):
    found = blocks()
    for (n, key), value in changes.items():
        found["oneshot-screens"][n][key] = value
    return found


@pytest.mark.parametrize("found, said", [
    (screens_with({(0, "features"): []}), "늘 쓰는 기능은 첫 화면"),
    (screens_with({(0, "features"): ["add-spending", "export"]}), "첫 화면에 있다"),
    (screens_with({(1, "reached"): "deeper"}), "한 번에 닿아야"),
    (screens_with({(2, "features"): []}), "어느 화면에도 없는"),
    (screens_with({(1, "primary"): True}), "정확히 하나"),
    (screens_with({(1, "route"): "/spendings"}), "API 경로와 겹친다"),   # the page would be the API's answer
    (blocks(**{"oneshot-mocks": {**blocks()["oneshot-mocks"], "S2": mock()}}), "data-feature"),
    (blocks(**{"oneshot-tokens": {**blocks()["oneshot-tokens"], "palettes": blocks()["oneshot-tokens"]["palettes"][:1]}}),
     "두세 개"),
])
def test_a_design_that_breaks_the_owners_placement_is_refused(found, said):
    with pytest.raises(ValueError, match=said):
        screens.design(found, FEATURES, FREQUENCY)


def test_a_never_feature_shown_on_a_screen_is_refused():
    with pytest.raises(ValueError, match="화면에 없기로"):
        screens.design(blocks(), FEATURES, {**FREQUENCY, "export": "never"})


# -- S6 the turn and its shots ---------------------------------------------------------------

def test_a_clean_design_is_shot_and_shown_as_the_r2_sheet(boarded):
    designed(boarded)
    shots = front.shots("budget", 1)
    assert {p.name for p in shots.glob("*.png")} == {"S1-desktop.png", "S2-desktop.png", "S3-desktop.png"}
    shown = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))
    assert shown["front_items"] == ["S1", "S1.states", "S2", "S2.states", "S3", "S3.states"]
    assert "/api/oneshot/spec/budget/shot/1/S1-desktop.png" in shown["front_sheet"]
    assert "| 내보내기 (`export`) | 드물게 쓴다 | S3 |" in shown["front_sheet"]
    assert str(front.shot("budget", 1, "S1-desktop.png").path).endswith("S1-desktop.png")
    with pytest.raises(HTTPException):
        front.shot("budget", 1, "../spec.json")


@pytest.mark.parametrize("extra, said", [
    ("<script>console.error('boom')</script>", "콘솔 오류"),
    ("<script>missing()</script>", "페이지 오류"),
    ("<img src='https://example.com/x.png'>", "외부 요청"),
    (f"<iframe src='{Path(__file__).resolve().as_uri()}'></iframe>", "로컬 파일 요청"),   # a file beside the mock
    ("<script>window.socket = new WebSocket('wss://example.com/mock-probe')</script>", "WebSocket 요청"),
    ("<script>window.open('https://example.com/mock-popup')</script>", "외부 요청 https://example.com/mock-popup"),
])
def test_a_mock_that_does_not_open_cleanly_is_sent_back_once(boarded, extra, said):
    playwright_or_skip()
    broken = blocks(**{"oneshot-mocks": {**blocks()["oneshot-mocks"], "S2": mock("month-list", extra=extra)}})
    heard, asking = replies(answer(broken), answer(blocks()))
    with asking:
        front.frontend("budget")
    assert o()["stage"] == "r2" and said in heard[1][1]


def test_translationese_is_sent_back_once_then_kept_as_a_warning(boarded):
    playwright_or_skip()
    stiff = screens_with({(1, "purpose"): "지난 달 지출에 대해 보여준다"})
    heard, asking = replies(answer(stiff), answer(stiff))
    with asking:
        front.frontend("budget")
    assert "번역체" in heard[1][1] and o()["stage"] == "r2" and o()["r2"]["warnings"]


# -- R2 ---------------------------------------------------------------------------------------

def approve_all(**more):
    return front.R2(decisions={i: {"approve": True} for i in screens.decidable(o()["front"])}, **more)


def test_r2_needs_every_item_and_a_palette_then_commits_the_design(boarded):
    designed(boarded)
    with pytest.raises(HTTPException, match="정하지 않은"):
        front.decide("budget", front.R2(decisions={"S1": {"approve": True}}, palette="C1"))
    with pytest.raises(HTTPException, match="색 배합"):
        front.decide("budget", approve_all())
    with pytest.raises(HTTPException, match="없는 색"):
        front.decide("budget", approve_all(palette="C9"))
    begun = []
    with patch.object(front.oneshot_front_build, "begin", begun.append):
        front.decide("budget", approve_all(palette="C2"))
    assert o()["stage"] == "build-front" and o()["r2"]["palette"] == "C2" and begun == ["budget"]
    for name in ("docs/design/frontend.md", "docs/design/mock/S1.html", "docs/design/shots/S3-desktop.png"):
        assert (boarded / name).is_file()
    sheet_md = (boarded / "docs/design/frontend.md").read_text(encoding="utf-8")
    assert "](shots/S1-desktop.png)" in sheet_md and "| C2 (고름) |" in sheet_md


def test_a_version_1_run_waiting_at_r2_is_refused_instead_of_built_without_a_runner(boarded):
    designed(boarded)
    oneshot_spec.saved("budget", oneshot={"version": 1, "tests_head": head(boarded)})   # pinned as pytest node ids
    begun = []
    with patch.object(front.oneshot_front_build, "begin", begun.append):
        for act in (lambda: front.decide("budget", approve_all(palette="C2")), lambda: oneshot_spec.resume("budget")):
            with pytest.raises(HTTPException, match="새 실행"):
                act()
    assert o()["stage"] == "r2" and begun == []


def test_a_server_that_dies_before_the_front_build_starts_resumes_it(boarded):
    designed(boarded)
    seen, sent = [], []
    with patch.object(front.oneshot_front_build, "begin", lambda repo: seen.append(o()["working"])):
        front.decide("budget", approve_all(palette="C1"))   # the build turn never left: the server went down
    assert seen == ["build-front"]
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    # what the backend's stages wrote, which this fixture skips
    oneshot_spec.saved("budget", oneshot={"tests_head": head(boarded), "contract": ["test_budget.py"],
                                          "gate": "uv run pytest"})
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        oneshot_spec.resume("budget")
    assert sent[-1][:2] == ("budget", "build-front")
    assert specs.load("budget", oneshot.BRANCH)["done"] == [   # what the resumed turn reports against
        f"{s['id']} `{s['route']}`: {s['purpose']}" for s in o()["front"]["screens"]]


def test_an_instruction_queued_before_the_approval_waits_for_the_front_build(boarded):
    designed(boarded)
    work._queued[str(boarded)] = work.Order(path=str(boarded), text="다음 지시")
    seen, begun = [], []
    try:
        with patch.object(front.oneshot_front_build, "begin", lambda repo: seen.append(str(boarded) in work._queued)), \
             patch.object(work, "begin", lambda *a: begun.append(a)):
            front.decide("budget", approve_all(palette="C1"))
        assert seen == [True] and begun == []   # the build turn sends it when it ends
    finally:
        work._queued.pop(str(boarded), None)


def test_an_edit_reaches_the_screens_states_and_keeps_the_rest_approved(boarded):
    designed(boarded)
    front.decide("budget", front.R2(decisions={**approve_all().decisions, "S2": {"edit": "목록을 표로"}}, palette="C1"))
    assert o()["working"] == "refront"
    oneshot_spec.saved("budget", oneshot={"working": None})
    moved = screens_with({(1, "purpose"): "지난 달 지출을 표로 본다"})
    moved["oneshot-mocks"]["S1"] = mock("add-spending", extra="<p>바뀜</p>")   # outside the reach
    fixed = screens_with({(1, "purpose"): "지난 달 지출을 표로 본다"})
    heard, asking = replies(answer(moved), answer(fixed))
    with asking:
        front.refront("budget", {"S2": "목록을 표로"}, ["S2", "S2.states"])
    assert "범위 밖" in heard[1][1] and "S1" in heard[1][1].split("범위 밖")[1]
    r2 = o()["r2"]
    assert r2["round"] == 2 and r2["marks"] == {"S2": "edited", "S2.states": "again"} and r2["palette"] == "C1"
    assert set(r2["decisions"]) == {"S1", "S1.states", "S3", "S3.states"} and o()["front"]["shots"] == 2


def test_an_edit_that_changes_the_tokens_is_sent_back_and_the_chosen_palette_stands(boarded):
    designed(boarded)
    front.decide("budget", front.R2(decisions={**approve_all().decisions, "S2": {"edit": "목록을 표로"}}, palette="C1"))
    oneshot_spec.saved("budget", oneshot={"working": None})
    recolored = screens_with({(1, "purpose"): "지난 달 지출을 표로 본다"})
    recolored["oneshot-tokens"]["palettes"][0]["colors"]["primary"] = "#ff0000"
    recolored["oneshot-tokens"]["type"] = ["99px"]
    fixed = screens_with({(1, "purpose"): "지난 달 지출을 표로 본다"})
    heard, asking = replies(answer(recolored), answer(fixed))
    with asking:
        front.refront("budget", {"S2": "목록을 표로"}, ["S2", "S2.states"])
    assert "tokens" in heard[1][1] and o()["front"]["tokens"] == blocks()["oneshot-tokens"] and o()["r2"]["palette"] == "C1"


def test_a_junction_on_the_way_to_the_design_stops_the_approval_before_anything_is_written(boarded, tmp_path):
    designed(boarded)
    outside = tmp_path / "outside"
    outside.mkdir()
    (boarded / "docs" / "design").mkdir(parents=True, exist_ok=True)
    try:
        import _winapi
        _winapi.CreateJunction(str(outside), str(boarded / "docs" / "design" / "mock"))
    except (ImportError, OSError):
        pytest.skip("no directory junctions here")
    front.decide("budget", approve_all(palette="C1"))
    assert o()["stopped"]["reason"] == "folder" and not any(outside.iterdir())


def test_an_approval_on_a_moved_backend_goes_back_to_the_builds_checks_before_writing(boarded):
    designed(boarded)
    (boarded / "late.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "late.py"], cwd=boarded, check=True)
    subprocess.run(["git", "commit", "-qm", "late"], cwd=boarded, check=True)
    front.decide("budget", approve_all(palette="C1"))
    assert o()["stage"] == "build" and o()["stopped"]["reason"] == "gate"
    assert not (boarded / "docs/design/frontend.md").exists()


def test_an_approval_while_another_turn_holds_the_tree_is_refused_and_keeps_the_decisions(boarded):
    designed(boarded)
    release = query.hold(work._busy, query._lock, str(boarded), "busy", kind="turn")
    try:
        with pytest.raises(HTTPException, match="다른 턴"):
            front.decide("budget", approve_all(palette="C1"))
    finally:
        release()
    assert o()["stage"] == "r2" and o()["r2"]["palette"] == "C1" and not (boarded / "docs/design").exists()
    begun = []
    with patch.object(front.oneshot_front_build, "begin", begun.append):
        front.decide("budget", approve_all())
    assert o()["stage"] == "build-front" and (boarded / "docs/design/frontend.md").is_file() and begun == ["budget"]


def test_a_split_after_a_rebuild_drops_the_old_design_and_shows_the_new_board(boarded):
    designed(boarded)
    oneshot_spec.saved("budget", oneshot={"stage": "split", "r2": {**o()["r2"], "palette": "C1"}})
    _, asking = replies(fenced("oneshot-features", FEATURES))
    with asking, patch.object(front, "served", lambda spec: OPS):
        front.split("budget")
    shown = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))
    assert o()["front"] is None and o()["r2"] is None and "front_sheet" not in shown
    assert shown["board"]["frequency"] == FREQUENCY


def test_reopening_the_board_discards_the_design_and_its_approvals(boarded):
    designed(boarded)
    front.decide("budget", front.R2(reopen=True))
    assert o()["stage"] == "board" and o()["front"] is None and o()["r2"] is None
    assert "front_sheet" not in oneshot_spec.view(specs.load("budget", oneshot.BRANCH))


def test_a_stopped_edit_turn_leaves_the_r2_sheet_standing(boarded):
    designed(boarded)
    oneshot_spec.store("budget").stop("block", "두 번 거부")
    oneshot_spec.resume("budget")
    assert o()["stage"] == "r2" and not o()["stopped"] and o()["front"]
