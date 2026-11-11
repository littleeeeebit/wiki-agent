"""oneshot step 4b: the frontend build turn, its adapter grant and the server's check.

See `docs/plans/oneshot/4-frontend.md` ("S7 — Build"). The work turns are stand-ins:
each test writes what a session would have committed, then hands the turn to the
check. Git, the gate, the guard, the app and Playwright run for real.
"""

import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import guard_grant
from guard import verdict
from main import oneshot, oneshot_build, oneshot_front_build as front_build, oneshot_sheet as sheet, oneshot_spec, specs, work
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_look import FIRST, INDEX, MAIN, OPS, SERVE
from test_oneshot_spec import DESIGN, PROFILE, fenced, world  # noqa: F401 — `world` is a fixture

RUNNER = f'"{sys.executable}" -m pytest -p no:cacheprovider -q --junitxml={{report}}'
GATE = RUNNER.replace("{report}", oneshot_build.REPORT)
LIVE = f'"{sys.executable}" -m app'
CONTRACT = "def test_spending_adds_to_the_total():\n    pass\n\n\ndef test_a_month_lists_its_spendings():\n    pass\n"
CASES = [["test_budget", n, ""] for n in ("test_a_month_lists_its_spendings", "test_spending_adds_to_the_total")]
SCREENS =[{"id": "S1", "route": "/", "purpose": "오늘 쓴 돈을 적는다", "features": ["add"], "primary": True, "reached": "start"},
           {"id": "S2", "route": "/months", "purpose": "달별로 본다", "features": ["months"], "primary": False,
            "reached": "one-click"},
           {"id": "S3", "route": "/settings", "purpose": "자료를 받는다", "features": ["export"], "primary": False,
            "reached": "deeper"}]
FRONT = {"screens": SCREENS, "states": {s["id"]: {"empty": "빈", "loading": "로딩", "error": "오류", "full": "찬"} for s in SCREENS},
         "tokens": {"palettes": [{"id": "C1", "name": "초록", "colors": {"primary": "#2f6f4e", "surface": "#ffffff",
                                                                        "text": "#111111", "accent": "#e0a000"}}],
                    "type": ["16px"], "spacing": [8]}, "mocks": {}, "shots": 1}
FEATURES = [{"id": "add", "name": "적기", "endpoints": ["POST /items"], "class": "every", "why": "늘"},
            {"id": "months", "name": "달별", "endpoints": ["GET /items"], "class": "sometimes", "why": "가끔"},
            {"id": "export", "name": "내보내기", "endpoints": [], "class": "rarely", "why": "드물게"}]
FREQUENCY = {"add": "every", "months": "sometimes", "export": "rarely"}


class Run:
    def __init__(self):
        self.events, self.halt, self.person = [], threading.Event(), False
        self.chat = SimpleNamespace(id="c", parent_id=None)

    def put(self, payload):
        self.events.append(payload)


def git(tree, *args):
    return subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True, text=True).stdout.strip()


def write(tree, name, text):
    (tree / name).parent.mkdir(parents=True, exist_ok=True)
    (tree / name).write_text(text, encoding="utf-8")


def adapter(gate=GATE, live=LIVE) -> str:
    return f"[slots]\ngate_cmd = {json.dumps(gate)}\nlive_cmd = {json.dumps(live)}\n"


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def spec() -> dict:
    return specs.load("budget", oneshot.BRANCH)


@pytest.fixture
def staged(world):  # noqa: F811 — the imported fixture, requested by name
    write(world, "test_budget.py", CONTRACT)
    write(world, ".gitignore", "__pycache__/\nartifacts/\n")
    git(world, "add", "-A")
    git(world, "commit", "-m", "contract")
    tests_head = git(world, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", done=[f"{s['id']} `{s['route']}`: {s['purpose']}" for s in SCREENS], oneshot={
        "stage": "build-front", "design": sheet.parsed(DESIGN), "tests_head": tests_head, "contract": ["test_budget.py"],
        "runner": RUNNER, "gate": GATE, "cases": CASES, "front": FRONT, "features": FEATURES, "frequency": FREQUENCY, "inventory": OPS, "r2": {"palette": "C1"}, "first_screen": "오늘 쓴 돈을 적는다",
        "profile": {**PROFILE, "responsive": "desktop"}, "working": "build-front"})
    yield world


def built(tree, first=FIRST, slots=None):
    write(tree, "app/main.py", MAIN)
    write(tree, "app/__main__.py", SERVE)
    write(tree, "app/index.html", INDEX.replace("FIRST", first))
    write(tree, "e2e/seed.json", json.dumps([{"method": "POST", "path": "/items", "body": {"name": "커피"}}]))
    write(tree, ".wiki/adapter.toml", slots or adapter())
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "frontend")


def report() -> str:
    return "만들었다.\n" + fenced("done-report", [{"item": item, "pass": True, "evidence": "화면"} for item in spec()["done"]])


def check(tree):
    return oneshot_build.checked(tree, Run(), spec(), report())


def playwright_or_skip():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:   # no package or no browser on this machine
        pytest.skip(f"Playwright 가 없다 — {exc}")


# -- The check -------------------------------------------------------------------------

def test_a_build_with_its_adapter_filled_a_green_gate_and_clean_shots_moves_to_critique(staged):
    playwright_or_skip()
    built(staged)
    then = check(staged)
    assert then is not None and o()["stage"] == "critique", o().get("stopped")
    assert o()["working"] == "critique"   # named before the critic starts: a restart in between is resumable
    assert o()["front_built_head"] == git(staged, "rev-parse", "HEAD") and o()["looks"][0]["round"] == 1
    assert not [f for f in o()["looks"][0]["findings"] if f["severity"] == "high"]
    folder = oneshot_spec.store("budget").folder / "build" / "r1"
    assert (folder / "S1-desktop-full.png").is_file() and (folder / "S3-desktop-error.png").is_file()


@pytest.mark.parametrize("slots, said", [
    (adapter(gate=f'"{sys.executable}" -c "pass"'), "로 시작하고"),
    (adapter(gate=f"{GATE} --version"), "로 시작하고"),   # names the command, runs none of it
    (adapter(live=""), "`live_cmd` 가 비었다"),
])
def test_an_adapter_that_lost_the_backend_tests_or_left_live_empty_is_refused(staged, slots, said):
    built(staged, slots=slots)
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        check(staged)()
    assert o()["stage"] == "build-front" and o()["attempts"] == 1 and said in sent[-1][2]


@pytest.mark.parametrize("gate, ok", [
    ("pytest -q", True), ("pytest -q && npm test", True), ("pytest -q && npm test && npm run lint", True),
    ("pytest -q --version", False), ("npm test # pytest -q", False), ("npm test && pytest -q", False),
    ("pytest -q || true", False), ("pytest -q && npm test || true", False), ("pytest -q; npm test", False),
    ("pytest -q && npm test # x", False), ("pytest -q\nnpm test", False),
    # a new line after the chain runs whatever the contract did; a lone `&` backgrounds the chain before it
    ("pytest -q && npm test\nexit 0", False), ("pytest -q &&\nnpm test", False), ("pytest -q && npm test\r\nexit 0", False),
    ("pytest -q && npm test & exit 0", False), ("pytest -q && npm test &", False), ("pytest -q &&& npm test", False),
    # a command that leaves or replaces the shell, or a substitution or block that can, ends the chain early
    ("pytest -q && exit 0", False), ("pytest -q && exec npm test", False), ("pytest -q && EXIT", False),
    ("pytest -q && npm test $(exit 0)", False), ("pytest -q && npm test (exit)", False),
    ("pytest -q && . {exit}", False), ("pytest -q && npm test `", False), ("pytest -q &&  && npm test", False),
    # the same words spelled the way the shell still finds them
    ("pytest -q && CI=1 exec npm test", False), ("pytest -q && command exec npm test", False),
    ("pytest -q && 'exec' npm test", False), ("pytest -q && \"exit\"", False),
    ("pytest -q && Microsoft.PowerShell.Utility\\Invoke-Expression 'exit 0'", False), ("pytest -q && /bin/kill.exe 1", False),
    ("pytest -q && npm --prefix web test -- --run && npm --prefix web run build", True),
    ('pytest -q && "C:\\Program Files\\nodejs\\npx.cmd" vitest run && uv run pytest && go test ./... && cargo nextest run', True),
])
def test_the_published_gate_runs_the_contracts_command_first_and_swallows_nothing(gate, ok):
    assert (oneshot_build.published(gate, "pytest -q") == "") is ok


def test_a_stop_during_the_look_passes_nothing_and_leaves_the_build_resumable(staged):
    built(staged)
    run = Run()
    with patch.object(front_build.oneshot_look, "look", lambda *a: (run.halt.set(), [])[1]):
        oneshot_build.checked(staged, run, spec(), report())
    assert o()["stage"] == "build-front" and o()["stopped"]["reason"] == "gate" and "멈췄다" in o()["stopped"]["detail"]
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        oneshot_spec.resume("budget")
    assert sent[-1][:2] == ("budget", "build-front")


def test_a_tree_changed_while_the_app_ran_is_not_recorded_as_passed(staged):
    built(staged)

    def look(*a):
        write(staged, "app/built.js", "x")   # what a `live_cmd` that builds into the tree leaves
        git(staged, "add", "-A")
        git(staged, "commit", "-m", "built")
        return []

    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)), patch.object(front_build.oneshot_look, "look", look):
        check(staged)()
    assert o()["stage"] == "build-front" and "작업트리가 바뀌었다" in sent[-1][2] and not o().get("front_built_head")


def test_a_high_finding_from_the_shots_is_sent_back_and_kept(staged):
    playwright_or_skip()
    built(staged, FIRST.replace('<a href="/months">달별</a>', ""))
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        check(staged)()
    assert o()["stage"] == "build-front" and "한 번 눌러서" in sent[-1][2] and o()["looks"][0]["findings"]


# -- The adapter grant -----------------------------------------------------------------

@pytest.fixture
def linked(tmp_path, monkeypatch):
    """An original checkout named after the repository, and the run's linked worktree beside it, with the
    spec stored where the real guard reads it."""

    hub = tmp_path / "hub"
    monkeypatch.setattr(specs, "SPECS", hub / "raw" / "specs")
    monkeypatch.setattr(guard_grant, "HERE", hub / "tool")
    monkeypatch.setenv("WIKI_AGENT_MANAGED", "1")
    original, tree = tmp_path / "budget", tmp_path / "wt"
    original.mkdir()
    identity = {f"GIT_{who}_{what}": value for who in ("AUTHOR", "COMMITTER")
                for what, value in (("NAME", "Oneshot test"), ("EMAIL", "oneshot@example.test"))}
    with patch.dict(os.environ, identity):
        git(original, "init", "-b", "main")
        write(original, ".wiki/adapter.toml", adapter())
        git(original, "add", "-A")
        git(original, "commit", "-m", "base")
        git(original, "worktree", "add", "-b", oneshot.BRANCH, str(tree))
    specs.save({"id": oneshot.BRANCH, "repo": "budget", "rev": 1, "goal": "가계부", "out": [], "done": [],
                "worktree": str(tree), "branch": oneshot.BRANCH, "state": "작업 중", "history": [{"ts": 1, "state": "작업 중"}],
                "oneshot": {"stage": "build-front", "spend": oneshot.ledger(None), "profile": PROFILE}})
    with patch.object(oneshot, "RUNS", tmp_path / "oneshot"), patch.object(oneshot, "codex_role", lambda role="", store=None: False):
        yield original, tree


def writes(top) -> bool:
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(top / ".wiki/adapter.toml"), "content": ""},
               "cwd": str(top)}
    return verdict(payload, host="claude") is None


def model_error(turn):
    turn.put({"kind": "error", "text": "모델 오류", "meta": {}})


def owner_stop(turn):
    turn.halt.set()
    turn.put({"kind": "error", "text": "사람이 멈춤", "meta": {}})


def refused_check(turn):   # the check ran and sent the turn back: the next turn sets the grant again
    turn.checked = True


@pytest.mark.parametrize("ending", [model_error, owner_stop, refused_check])
def test_the_grant_opens_the_worktrees_adapter_only_while_a_build_turn_runs(linked, ending):
    original, tree = linked
    assert not writes(tree)
    begun = []
    with patch.object(work, "session", lambda *a: SimpleNamespace(id="c", parent_id=None, repo=str(tree))), \
         patch.object(work, "begin", lambda path, chat, text, release, turn: begun.append((turn, release))), \
         patch.object(work, "notice", lambda *a: None):
        oneshot_build.send("budget", "build-front", "build")
        assert spec()["adapter"] is True and writes(tree) and not writes(original)
        turn, release = begun[0]
        ending(turn)
        release()   # the server lets the tree go before it tells the end: the next turn there gets no grant
        assert spec()["adapter"] is False and not writes(tree)
        turn.finish()
    assert spec()["adapter"] is False and not writes(tree)


def test_a_restart_clears_a_grant_its_turn_never_cleared(linked):
    _, tree = linked
    oneshot_spec.saved("budget", adapter=True)
    assert writes(tree)
    oneshot_spec.recover()
    assert spec()["adapter"] is False and not writes(tree)


def test_resuming_a_stopped_build_front_sends_its_turn_again(staged):
    sent = []
    oneshot_spec.store("budget").stop("gate", "세 번 거부")
    oneshot_spec.saved("budget", oneshot={"attempts": 3})
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        oneshot_spec.resume("budget")
    assert sent[-1][:2] == ("budget", "build-front") and o()["attempts"] == 0
    assert '"palette"' in sent[-1][2] and "`test_budget.py`" in sent[-1][2] and "{design}" not in sent[-1][2]
