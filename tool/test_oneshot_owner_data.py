"""A oneshot build turn on a shared machine: the owner's data folders watched over it, its session marked, its
GPU jobs ended at its check (plan 15a)."""

import os
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gpu_queue
import owner_data
from main import oneshot, oneshot_build, oneshot_measure as measure, oneshot_owner_data as watched, oneshot_spec, \
    specs, work
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_build import SEND, TESTS, Turn, built_world, check, o, to_build, write  # noqa: F401
from test_oneshot_measure import at_measure, given  # noqa: F401
from test_oneshot_spec import fenced, world  # noqa: F401 — `world` is a fixture

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the watch is Windows'")


@pytest.fixture
def owner(built_world, tmp_path, monkeypatch):  # noqa: F811 — the imported fixture, requested by name
    """The world, its owner's data folder `AppData\\Roaming\\tree` already there, and no watch left over."""

    roaming, local = tmp_path / "AppData" / "Roaming", tmp_path / "AppData" / "Local"
    roaming.mkdir(parents=True)
    local.mkdir(parents=True)
    monkeypatch.setattr(owner_data, "bases", lambda: [roaming, local])
    (roaming / "tree").mkdir()
    yield (*built_world, roaming / "tree")
    for repo in list(watched._watches):
        watched.cleared(repo)


def begun(tree) -> None:
    """The watch `send` starts before the turn, for the fixture's turn (`Turn.token`)."""

    watched.begin("budget", tree, oneshot_spec.store("budget"), "turn", "x", None)


def test_a_change_refuses_the_turn_before_any_stage_is_saved_and_a_clean_retry_passes(owner):
    tree, sent, data = owner
    begun(tree)
    assert o()["owner_watch"]["turn"] == "turn"   # the hold, written before the turn is sent
    write(tree, "test_budget.py", TESTS)
    (data / "settings.json").write_text("{}", encoding="utf-8")   # the turn ran the product against real data
    then = check(tree)
    assert o()["stage"] == "tests" and o()["attempts"] == 1 and not o().get("tests_head")
    assert o()["owner_watch"] is None and "budget" not in watched._watches   # closed with the check
    then()
    assert sent[-1][:2] == ("budget", "tests") and "주인의 실제 데이터 폴더가 바뀌었다" in sent[-1][2]
    assert "settings.json" in sent[-1][2]
    begun(tree)
    to_build(tree, sent)   # nothing written there this time: the first clean retry passes


def test_a_contract_test_writing_there_is_read_before_the_tests_are_pinned(owner):
    tree, sent, data = owner
    begun(tree)
    write(tree, "test_budget.py", f"open(r'{data / 'cache.bin'}', 'w').write('x')\n" + TESTS)
    then = check(tree)
    assert o()["stage"] == "tests" and not o().get("tests_head")   # the gate ran, then the watch was read
    then()
    assert "cache.bin" in sent[-1][2]


def test_a_question_after_a_change_is_refused_rather_than_asked(owner):
    tree, sent, data = owner
    to_build(tree, sent)
    begun(tree)
    (data / "x").write_text("x", encoding="utf-8")
    asked = fenced("choices", {"question": "합계는 세금 포함인가?", "options": [{"label": "포함"}, {"label": "제외"}]})
    then = check(tree, "물어본다.\n\n" + asked)
    assert (o().get("stopped") or {}).get("reason") != "asked" and then is not None
    then()
    assert sent[-1][1] == "build" and "주인의 실제 데이터 폴더가 바뀌었다" in sent[-1][2]


def test_a_turn_that_never_reached_its_check_tells_the_next_build_turn(owner):
    tree, _, data = owner
    begun(tree)
    (data / "late.txt").write_text("x", encoding="utf-8")
    watched.settle(SimpleNamespace(token="turn", repo="budget", turn="t1"), tree)
    assert "late.txt" in o()["owner_note"] and o()["owner_watch"] is None
    watched.settle(SimpleNamespace(token="other", repo="budget"), tree)   # another turn's end: nothing of this one
    told = []
    with stubbed(told):
        SEND("budget", "tests", "x")
    assert told[0].startswith("The server's watch of the owner's data folders") and "late.txt" in told[0]
    assert told[0].endswith("x") and o()["owner_note"] is None


def stubbed(told: list):
    """`send`'s outside calls stubbed, the turn's text kept as `work.begin` got it, which then refuses it."""

    from contextlib import ExitStack

    def begin(path, chat, text, release, turn):
        told.append(text)
        raise RuntimeError("멈춤")

    stack = ExitStack()
    for target, name, value in (
            (work, "notice", lambda *a: None), (work, "remember", lambda *a, **k: None), (work, "begin", begin),
            (work, "session", lambda *a: SimpleNamespace(id="c", parent_id=None, repo="")),
            (oneshot, "role_config", lambda *a: {"model": "m", "effort": "e"}), (oneshot, "codex_role", lambda *a: False)):
        stack.enter_context(patch.object(target, name, value))
    return stack


def test_a_hold_no_watch_backs_stops_the_run_until_the_owner_resumes(owner):
    tree, _, _ = owner
    oneshot_spec.saved("budget", oneshot={"owner_watch": {"turn": "gone", "started": 1}})   # a server stopped mid-turn
    told = []
    with stubbed(told):
        SEND("budget", "tests", "x")
    assert told == [] and o()["stopped"]["detail"] == watched.LEFTOVER and str(tree) not in work._busy
    oneshot_spec.saved("budget", oneshot={"stopped": None, "working": "tests"})
    tree, sent, _ = owner
    oneshot_build.resume("budget")   # the owner's resume clears the hold and sends the turn (the fixture's `send`)
    assert sent[-1][:2] == ("budget", "tests") and o()["owner_watch"] is None
    with stubbed(told):
        SEND("budget", "tests", "x")
    assert len(told) == 1 and o()["stopped"]["detail"] != watched.LEFTOVER


def test_an_owners_turn_and_a_build_turn_each_start_the_cli_again_marked_only_for_the_build(owner, monkeypatch):
    tree, _, _ = owner
    monkeypatch.setattr(specs, "owner", lambda path: specs.load("budget", oneshot.BRANCH))
    made = []

    class Chat:
        is_codex, fast = False, False

        def __init__(self, path, env=None, bypass=False, **k):
            made.append(dict(env or {}))
            self.bypass, self.closed = bypass, False

        def reconfigure(self, model, effort):
            pass

        def close(self):
            self.closed = True

    with patch.object(work, "ChatSession", Chat), patch.dict(work._sessions, clear=True):
        build = work.session(tree, "opus", "low", False, "build")
        assert work.session(tree, "opus", "low", False, "build") is build   # the same role: the same CLI
        mine = work.session(tree, "opus", "low")
        again = work.session(tree, "opus", "low", False, "build")
    assert build.closed and mine.closed and not again.closed
    assert [e.get("WIKI_AGENT_ROLE") for e in made] == ["build", None, "build"]


@pytest.mark.parametrize("queued", [True, False])
def test_a_build_turn_that_queued_a_gpu_job_has_its_cli_closed_before_the_check(owner, tmp_path, monkeypatch, queued):
    tree, sent, _ = owner
    monkeypatch.setenv("WIKI_AGENT_GPU_HOME", str(tmp_path / "gpu"))
    closed = []
    turn = Turn()
    turn.chat = SimpleNamespace(id="c", parent_id=None, job_uuid="ef" * 16, close=lambda: closed.append(True))
    if queued:
        gpu_queue.wrapped("ef" * 16).parent.mkdir(parents=True)
        gpu_queue.wrapped("ef" * 16).write_text("", encoding="utf-8")
    write(tree, "test_budget.py", TESTS)
    assert oneshot_build.checked(tree, turn, specs.load("budget", oneshot.BRANCH), check.__globals__["told"]())
    assert closed == ([True] if queued else []) and not gpu_queue.wrapped("ef" * 16).exists()


# -- The server's own measurement -------------------------------------------------------------------------------

@pytest.fixture
def measured(at_measure, tmp_path, monkeypatch):  # noqa: F811 — the imported fixture, requested by name
    roaming = tmp_path / "AppData" / "Roaming"
    (roaming / "tree").mkdir(parents=True)
    monkeypatch.setattr(owner_data, "bases", lambda: [roaming])
    monkeypatch.setenv("WIKI_AGENT_GPU_HOME", str(tmp_path / "gpu"))
    (tmp_path / "load.json").write_text('{"cpu": 1, "ram": [1, 32], "gpus": [], "holders": []}', encoding="utf-8")
    monkeypatch.setenv("WIKI_AGENT_LOAD_FILE", str(tmp_path / "load.json"))
    given()
    return (*at_measure, roaming / "tree")


def test_a_change_to_the_owners_folder_fails_the_servers_measurement(measured):
    tree, sent, data = measured
    real = measure.product.started

    @contextmanager
    def writing(*a, **k):   # the program, once it runs, writes into the owner's real folder
        with real(*a, **k) as build:
            (data / "state.db").write_text("x", encoding="utf-8")
            yield build

    with patch.object(measure.product, "started", writing):
        measure.measure("budget", "owner")
    record = o()["measures"][-1]
    assert record["status"] == "failed" and "주인의 실제 데이터 폴더가 바뀌었다" in record["error"]
    assert "state.db" in record["error"] and sent[-1][1] == "build"


def test_a_measurement_behind_another_gpu_job_shows_its_place_and_starts_after_it(measured):
    from main import oneshot_kept

    tree, sent, _ = measured
    hold, holding = threading.Event(), threading.Event()

    def agent_job():
        with gpu_queue.held("agent job", say=lambda line: None):
            holding.set()
            hold.wait(60)

    ahead = threading.Thread(target=agent_job)
    ahead.start()
    holding.wait(30)
    oneshot_spec.saved("budget", oneshot={"working": "measure"})
    runner = threading.Thread(target=measure.measure, args=("budget", "owner"))
    runner.start()
    try:
        deadline = time.monotonic() + 30
        while (oneshot_kept.shown(specs.load("budget", oneshot.BRANCH))["measure"]["running"] or {}).get("gpu") != 1:
            assert time.monotonic() < deadline, "the card never showed the place"
            time.sleep(0.05)
        assert not o().get("measures")   # nothing launched while it waits
    finally:
        hold.set()
        ahead.join(30)
        runner.join(120)
    assert o()["measures"][-1]["status"] == "owner"
