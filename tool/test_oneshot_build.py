"""oneshot step 3b: the contract tests first, then the build, checked by the server.

See `docs/plans/oneshot/3-spec-backend.md` ("S4"). The work turns are stand-ins:
each test writes what a session would have written, then hands the turn to the
check. Git and pytest run for real in a temporary worktree.
"""

import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from main import oneshot, oneshot_build, oneshot_sheet as sheet, oneshot_spec, specs, work
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import DESIGN, GLOSSARY, REQS, fenced, world  # noqa: F401 — `world` is a fixture

GATE = f'"{sys.executable}" -m pytest'
TESTS = '''
def test_spending_adds_to_the_total():
    from budget_app import add
    assert add(3000) == 3000


def test_a_month_lists_its_spendings():
    from budget_app import month
    assert month("2026-10") == []
'''
APP = '''
def add(amount):
    return amount


def month(name):
    return []
'''


class Turn:
    def __init__(self, person=False):
        self.events, self.halt, self.person = [], threading.Event(), person
        self.chat = SimpleNamespace(id="c", parent_id=None)

    def put(self, payload):
        self.events.append(payload)


def git(tree, *args):
    return subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def built_world(world):  # noqa: F811 — the imported fixture, requested by name
    (world / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    git(world, "add", ".gitignore")
    git(world, "commit", "-m", "design")
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stage": "tests", "design": sheet.parsed(DESIGN), "design_head": git(world, "rev-parse", "HEAD"),
        "working": "tests"})
    sent = []
    with patch.object(oneshot_build, "gate_cmd", lambda profile: GATE), \
         patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        yield world, sent


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def spec() -> dict:
    return specs.load("budget", oneshot.BRANCH)


def check(tree, final="썼다."):
    return oneshot_build.checked(tree, Turn(), spec(), final)


def write(tree, name, text):
    (tree / name).parent.mkdir(parents=True, exist_ok=True)
    (tree / name).write_text(text, encoding="utf-8")


def report() -> str:
    return fenced("done-report", [{"item": item, "pass": True, "evidence": "pytest"} for item in spec()["done"]])


def to_build(tree, sent):
    write(tree, "test_budget.py", TESTS)
    then = check(tree)
    assert then is not None and o()["stage"] == "build"
    then()
    assert sent[-1][:2] == ("budget", "build")


# -- Tests first --------------------------------------------------------------------

def test_failing_planned_tests_are_committed_pinned_and_followed_by_the_build_turn(built_world):
    tree, sent = built_world
    to_build(tree, sent)
    assert o()["tests_head"] == git(tree, "rev-parse", "HEAD") and o()["contract"] == ["test_budget.py"]
    assert git(tree, "status", "--porcelain") == ""
    assert spec()["done"] == [GATE, "R1: 지출을 적으면 이번 달 합계에 더해진다", "R2: 지난 달 지출을 고르면 그 달 목록이 보인다"]
    assert o()["tests_head"] in sent[-1][2] and "`test_budget.py`" in sent[-1][2]


@pytest.mark.parametrize("files, said", [
    ({"test_budget.py": TESTS, "budget_app.py": APP}, "테스트와 테스트 설정만"),
    ({"test_budget.py": TESTS.replace('assert month("2026-10") == []', "assert True")
      .replace("from budget_app import month", "pass")}, "아무것도 확인하지 않는다"),
    ({"test_budget.py": TESTS.split("\n\n\ndef test_a_month")[0]}, "test_a_month_lists_its_spendings"),
    ({"test_budget.py": "import pytest\n" + TESTS.replace("def test_a_month", "@pytest.mark.skip\ndef test_a_month")},
     "건너뛴"),
    ({"test_budget.py": "import budget_app\n" + TESTS}, "수집이 실패했다"),
])
def test_a_tests_turn_is_refused_with_the_reason_and_sent_back(built_world, files, said):
    tree, sent = built_world
    for name, text in files.items():
        write(tree, name, text)
    then = check(tree)
    assert o()["stage"] == "tests" and o()["attempts"] == 1
    then()
    assert sent[-1][:2] == ("budget", "tests") and said in sent[-1][2]


def test_the_third_refusal_stops_the_stage(built_world):
    tree, sent = built_world
    oneshot_spec.saved("budget", oneshot={"attempts": 2})
    write(tree, "budget_app.py", APP)
    assert check(tree) is None and not sent
    assert o()["stopped"]["reason"] == "tests" and o()["working"] is None


# -- The build ----------------------------------------------------------------------

def test_a_build_with_a_full_report_a_green_gate_and_every_planned_test_passing_moves_to_review(built_world):
    tree, sent = built_world
    to_build(tree, sent)
    write(tree, "budget_app.py", APP)
    git(tree, "add", "budget_app.py")
    git(tree, "commit", "-m", "build")
    assert check(tree, "만들었다.\n" + report()) is None
    assert o()["stage"] == "review" and o()["built_head"] == git(tree, "rev-parse", "HEAD")


@pytest.mark.parametrize("weaken, said", [
    (lambda tree: write(tree, "test_budget.py", TESTS.replace("== 3000", "== 0")), "바뀌었다"),
    (lambda tree: write(tree, "test_budget.py", TESTS + "\nimport pytest\npytest.skip('x', allow_module_level=True)\n"),
     "바뀌었다"),
    (lambda tree: (tree / "test_budget.py").unlink(), "없다"),
    (lambda tree: write(tree, "conftest.py", "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                                             "    for item in items:\n        item.add_marker(pytest.mark.skip)\n"),
     "통과하지 못한 계획 테스트"),
])
def test_a_weakened_contract_is_refused_even_when_the_gate_is_green(built_world, weaken, said):
    tree, sent = built_world
    to_build(tree, sent)
    write(tree, "budget_app.py", APP)
    weaken(tree)
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "build")
    then = check(tree, "만들었다.\n" + report())
    assert o()["stage"] == "build" and o()["attempts"] == 1
    then()
    assert sent[-1][:2] == ("budget", "build") and said in sent[-1][2]


def test_a_build_without_its_report_or_with_a_red_gate_is_refused(built_world):
    tree, sent = built_world
    to_build(tree, sent)
    assert check(tree, "다 했다.")() is None and "완료 보고" in sent[-1][2]
    check(tree, "만들었다.\n" + report())()
    assert "게이트 실패" in sent[-1][2] and o()["attempts"] == 2


# -- Turns, the hand-over and resuming -----------------------------------------------

def test_a_turn_that_ends_without_its_check_stops_the_stage_and_is_charged(built_world):
    tree, _ = built_world
    chat = SimpleNamespace(id="c", parent_id=None, repo=str(tree))
    with patch.object(work, "notice", lambda *a: None):
        turn = oneshot_build.Turn(chat, "budget", "tests", False)
        turn.put({"kind": "error", "text": "사람이 멈춤", "meta": {}})
        turn.finish()
    assert o()["stopped"] == {**o()["stopped"], "reason": "block"} and "사람이 멈춤" in o()["stopped"]["detail"]
    assert o()["spend"]["calls"] == 1 and o()["spend"]["unknown"] == 1


def test_every_oneshot_turn_is_handed_over_and_a_persons_changes_nothing(built_world):
    tree, sent = built_world
    seen = []
    with patch.object(specs, "owner", lambda path: spec()), patch.object(specs.channels, "repo_for", lambda r: tree):
        with patch.object(oneshot_build, "checked", lambda *a: seen.append(a[3]) or "next"):
            assert specs._check(tree, Turn(), "답") == "next" and seen == ["답"]
        write(tree, "test_budget.py", TESTS)
        report_and_update = report() + fenced("spec-update", {"rev": 1, "reason": "x", "goal": "y", "out": [], "done": []})
        assert specs._check(tree, Turn(person=True), report_and_update) is None
    assert o()["stage"] == "tests" and not o().get("attempts") and not sent and spec()["rev"] == 1


def test_resuming_a_stopped_build_sends_its_turn_again_with_fresh_attempts(built_world):
    tree, sent = built_world
    to_build(tree, sent)
    oneshot_spec.store("budget").stop("gate", "세 번 거부")
    oneshot_spec.saved("budget", oneshot={"attempts": 3})
    oneshot_spec.resume("budget")
    assert sent[-1][:2] == ("budget", "build") and o()["attempts"] == 0 and o()["working"] == "build"


def test_the_prompts_name_the_planned_tests_and_the_pins():
    s = {"oneshot": {"design": sheet.parsed(DESIGN), "profile": {"python_env": "uv"}, "tests_head": "abc",
                     "contract": ["tests/test_a.py"]}}
    tests = oneshot_build.tests_text(s)
    assert "test_spending_adds_to_the_total" in tests and "uv run pytest" in tests and "{tests}" not in tests
    assert json.loads(tests.split("```json\n")[1].split("```")[0])[0]["id"] == "T1"
    assert "`tests/test_a.py`" in oneshot_build.build_text(s) and "abc" in oneshot_build.build_text(s)
