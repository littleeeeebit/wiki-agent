"""oneshot step 3b: the contract tests first, then the build, checked by the server.

See `docs/plans/oneshot/3-spec-backend.md` ("S4") and `6-free-stack.md`. The work turns are stand-ins:
each test writes what a session would have written, then hands the turn to the
check. Git and the project's test tool, pytest writing a JUnit report here, run for real in a temporary worktree.
"""

import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_build, oneshot_sheet as sheet, oneshot_spec, runtime as runtime_mod, specs, work
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import DESIGN, GLOSSARY, REQS, fenced, world  # noqa: F401 — `world` is a fixture

RUNNER = f'"{sys.executable}" -m pytest -p no:cacheprovider -q --junitxml={{report}}'
GATE = RUNNER.replace("{report}", oneshot_build.REPORT)
SEND = oneshot_build.send   # the fixture replaces it; one test sends for real
TESTS = '''
def test_spending_adds_to_the_total():
    from app import add
    assert add(3000) == 3000


def test_a_month_lists_its_spendings():
    from app import month
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


def elsewhere(fn):
    """As `work.run_turn` ends a turn: on the turn's own thread, never the one that sent it."""

    thread = threading.Thread(target=fn)
    thread.start()
    thread.join()


def git(tree, *args):
    return subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def built_world(world):  # noqa: F811 — the imported fixture, requested by name
    (world / ".gitignore").write_text("__pycache__/\nartifacts/\n", encoding="utf-8")
    git(world, "add", ".gitignore")
    git(world, "commit", "-m", "design")
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stage": "tests", "design": sheet.parsed(DESIGN), "design_head": git(world, "rev-parse", "HEAD"),
        "working": "tests"})
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        yield world, sent


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def spec() -> dict:
    return specs.load("budget", oneshot.BRANCH)


def told(contract=("test_budget.py",), config=(), command=RUNNER, **more) -> str:
    """A tests turn's answer: its `oneshot-runner` block."""

    return "썼다.\n" + fenced("oneshot-runner", {"command": command, "contract": list(contract), "config": list(config), **more})


def check(tree, final=None):
    return oneshot_build.checked(tree, Turn(), spec(), told() if final is None else final)


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
    assert git(tree, "status", "--porcelain") == "" and o()["runner"] == RUNNER and o()["gate"] == GATE
    assert f"gate_cmd = {json.dumps(GATE)}" in (tree / ".wiki/adapter.toml").read_text(encoding="utf-8")
    assert spec()["done"] == [GATE, "R1: 지출을 적으면 이번 달 합계에 더해진다", "R2: 지난 달 지출을 고르면 그 달 목록이 보인다"]
    assert o()["tests_head"] in sent[-1][2] and "`test_budget.py`" in sent[-1][2]


@pytest.mark.parametrize("files, final, said", [
    ({"test_budget.py": TESTS}, "썼다.", "`oneshot-runner` 블록이 하나 있어야 한다"),
    ({"test_budget.py": TESTS}, told(command=GATE), "`{report}`"),
    ({"test_budget.py": TESTS}, told(contract=["tests/test_budget.py"]), "작업트리에 없다"),
    ({"test_budget.py": TESTS}, told(contract=["../outside.py"]), "작업트리에 없다"),
    ({"test_budget.py": TESTS}, told(command=f'"{sys.executable}" -c "pass" {{report}}'), "JUnit 보고서를 쓰지 않았다"),
    ({"test_budget.py": TESTS.replace('assert month("2026-10") == []', "assert True")
      .replace("from app import month", "pass")}, None, "아무것도 확인하지 않는다"),
    ({"test_budget.py": TESTS.split("\n\n\ndef test_a_month")[0]}, None, "test_a_month_lists_its_spendings"),
    ({"test_budget.py": "import pytest\n" + TESTS.replace("def test_a_month", "@pytest.mark.skip\ndef test_a_month")},
     None, "건너뛴"),
    ({"test_budget.py": "import app\n" + TESTS}, None, "JUnit 보고서에 없는"),   # a module that cannot be collected
])
def test_a_tests_turn_is_refused_with_the_reason_and_sent_back(built_world, files, final, said):
    tree, sent = built_world
    (tree.parent / "outside.py").write_text(TESTS, encoding="utf-8")
    for name, text in files.items():
        write(tree, name, text)
    then = check(tree, final)
    assert o()["stage"] == "tests" and o()["attempts"] == 1
    then()
    assert sent[-1][:2] == ("budget", "tests") and said in sent[-1][2]


def test_each_parameter_case_is_pinned_and_only_the_turns_files_are_committed(built_world):
    tree, sent = built_world
    write(tree, "app.py", APP)
    git(tree, "add", "app.py")
    (tree / "app.py").unlink()   # staged, but gone from the worktree: not a change the turn may commit
    write(tree, "test_budget.py", "import pytest\n" + TESTS.replace(
        "def test_a_month", '@pytest.mark.parametrize("m", ["2026-10", "2026-11"])\ndef test_a_month')
        .replace('month("2026-10")', "month(m)").replace("spendings():", "spendings(m):"))
    # xunit1 names each case's file; the default xunit2 does not, and the file is then empty
    assert check(tree, told(command=RUNNER + " -o junit_family=xunit1")) is not None and o()["cases"] == [
        ["test_budget", "test_a_month_lists_its_spendings[2026-10]", "test_budget.py"],
        ["test_budget", "test_a_month_lists_its_spendings[2026-11]", "test_budget.py"],
        ["test_budget", "test_spending_adds_to_the_total", "test_budget.py"]]
    assert git(tree, "show", "--name-only", "--format=", "HEAD").split() == [".wiki/adapter.toml", "test_budget.py"]


def test_a_case_from_another_file_or_reported_twice_does_not_stand_in_for_a_pinned_one(built_world):
    pin = ["tests.a.b.test_total", "test_spending_adds_to_the_total"]
    other = (*pin, "tests/a/b/test_total.py", "passed")
    assert "돌지 않음" in oneshot_build.green([[*pin, "tests/a.b/test_total.py"]], [other])
    # with no file in the report the two cannot be told apart: only both running shows it
    assert "두 번 이상" in oneshot_build.green([[*pin, ""]], [(*pin, "", "passed")] * 2)
    assert oneshot_build.green([[*pin, ""]], [(*pin, "", "passed")]) == ""
    month = ("tests.a.b.test_total", "test_a_month_lists_its_spendings", "", "failure")
    assert "구별하지 못하는" in oneshot_build.red(spec(), [month, (*pin, "", "failure"), (*pin, "", "failure")])


def test_a_planned_name_matches_in_any_case_and_with_a_parameter_but_not_as_a_prefix():
    assert oneshot_build.named("test_add", "Test_Add") and oneshot_build.named("test_add", "test_add[1]")
    assert oneshot_build.named("test_add", "test_add (one)")
    assert not oneshot_build.named("test_add", "test_add_more") and not oneshot_build.named("test_add", "test_ad")
    for other in ("test_add unrelated scenario", "test_add (one", "test_add[1", "test_add.extra", "test_add/sub"):
        assert not oneshot_build.named("test_add", other), other


def test_the_report_path_reaches_the_runner_as_written(tmp_path):
    shown = tmp_path / "it's $HOME, $(x) and it’s ‘quoted’ ‚so‛" / "run.xml"
    command = f'"{sys.executable}" -c "import sys; print(ascii(sys.argv[1]))" ' + oneshot_build.literal(shown.as_posix())
    _, text, cut = specs.gate(command, tmp_path, threading.Event(), timeout=60)
    assert not cut and ascii(shown.as_posix()) in text, text


def test_a_reports_relative_file_is_read_from_the_runners_declared_root_and_nothing_else(tmp_path):
    for f in ("web/tests/test_budget.py", "api/tests/test_budget.py", "tests/test_budget.py"):
        write(tmp_path, f, "")   # the same ending three times: none is guessed
    assert oneshot_build.source(tmp_path, "tests\\test_budget.py", "web") == "web/tests/test_budget.py"   # `cd web`
    assert oneshot_build.source(tmp_path, "tests/test_budget.py", "") == "tests/test_budget.py"
    assert oneshot_build.source(tmp_path, "./web/tests/test_budget.py", "") == "web/tests/test_budget.py"
    assert oneshot_build.source(tmp_path, "../api/tests/test_budget.py", "web") == "api/tests/test_budget.py"
    assert oneshot_build.source(tmp_path, str(tmp_path / "web/tests/test_budget.py"), "api") == "web/tests/test_budget.py"


@pytest.mark.parametrize("more, ok", [({"root": "web"}, True), ({}, False)])
def test_a_runner_below_the_root_pins_its_cases_only_from_the_folder_it_declares(built_world, more, ok):
    tree, sent = built_world
    write(tree, "web/test_budget.py", TESTS)
    # pytest names each case's file from its rootdir, as a runner started with `cd web` would
    command = RUNNER + " -o junit_family=xunit1 --rootdir web web"
    check(tree, told(contract=["web/test_budget.py"], command=command, **more))()
    if ok:
        assert o()["stage"] == "build" and {c[2] for c in o()["cases"]} == {"test_budget.py"}
    else:
        assert o()["stage"] == "tests" and "`contract` 에 없는 파일" in sent[-1][2] and "— test_budget.py" in sent[-1][2]


@pytest.mark.parametrize("root", ["..", "missing", "web/test_budget.py", 3])
def test_a_root_outside_the_worktree_or_not_a_folder_is_refused(built_world, root):
    tree, sent = built_world
    write(tree, "web/test_budget.py", TESTS)
    check(tree, told(contract=["web/test_budget.py"], root=root))()
    assert "`root` 는" in sent[-1][2] and o()["stage"] == "tests"


def test_a_planned_case_from_a_file_left_out_of_the_contract_is_refused(built_world):
    tree, sent = built_world
    write(tree, "test_budget.py", TESTS)
    generated = [("tests.generated", n, "tests/generated.py", "failure")
                 for n in ("test_a_month_lists_its_spendings", "test_spending_adds_to_the_total")]
    with patch.object(oneshot_build, "junit", lambda *a: generated):
        check(tree)()
    assert "`contract` 에 없는 파일" in sent[-1][2] and "tests/generated.py" in sent[-1][2] and o()["stage"] == "tests"


def test_a_run_from_before_version_2_that_reaches_the_tests_turn_carries_on_as_version_2(built_world):
    tree, sent = built_world
    oneshot_spec.saved("budget", oneshot={"version": 1})
    write(tree, "test_budget.py", TESTS)
    assert check(tree) is not None and o()["stage"] == "build" and o()["version"] == 2
    oneshot_spec.supported(o())


def test_a_test_file_changed_while_its_run_is_checked_is_refused(built_world):
    tree, sent = built_world
    write(tree, "test_budget.py", TESTS)
    real = oneshot_build.junit

    def junit(path, *a):
        write(path, "test_budget.py", TESTS + "\n# changed\n")
        return real(path, *a)

    with patch.object(oneshot_build, "junit", junit):
        check(tree)()
    assert "바뀌었다" in sent[-1][2] and o()["stage"] == "tests" and git(tree, "log", "-1", "--format=%s") == "design"


@pytest.mark.parametrize("when", ["during the commit", "after the check, while the turn is recorded"])
def test_a_halt_before_the_next_turn_stops_instead_of_sending_it(built_world, when):
    tree, sent = built_world
    write(tree, "test_budget.py", TESTS)
    real, run = oneshot_build.tested, Turn()

    def tested(path, run, spec, final):
        why = real(path, run, spec, final)
        if when == "during the commit":
            run.halt.set()
        return why

    with patch.dict(oneshot_build.STAGES, {"tests": (tested, *oneshot_build.STAGES["tests"][1:])}):
        then = oneshot_build.checked(tree, run, spec(), told())
    run.halt.set()   # either way, set by the time `work.run_turn` calls `then`
    then()
    assert not sent and o()["stage"] == "build" and o()["stopped"]["reason"] == "block"
    oneshot_spec.resume("budget")
    assert sent[-1][:2] == ("budget", "build")


def test_each_case_is_pinned_by_its_class_through_inheritance_and_any_parameter_id(built_world):
    tree, sent = built_world
    write(tree, "tests/helpers.py", "import pytest\n\n\nclass Base:\n"
                                    "    @pytest.mark.parametrize('case', [pytest.param(3000, id='lookup::empty')])\n"
                                    "    def test_spending_adds_to_the_total(self, case):\n"
                                    "        from app import add\n        assert add(case) == case\n")
    write(tree, "tests/test_budget.py", "from helpers import Base\n\n\nclass TestApi(Base):\n    pass\n\n\n"
                                        "def test_a_month_lists_its_spendings():\n"
                                        "    from app import month\n    assert month('2026-10') == []\n")
    check(tree, told(contract=["tests/test_budget.py", "tests/helpers.py"]))()
    (month, month_case, _), (add, add_case, _) = o()["cases"]
    assert (month_case, add_case) == ("test_a_month_lists_its_spendings", "test_spending_adds_to_the_total[lookup::empty]")
    assert add == month + ".TestApi"
    write(tree, "app.py", APP)
    git(tree, "add", "app.py")
    git(tree, "commit", "-m", "build")
    assert check(tree, "만들었다.\n" + report()) is not None and o()["stage"] == "review"


def test_a_restart_between_a_refusal_and_its_resend_leaves_the_stage_resumable(built_world):
    tree, sent = built_world
    oneshot_spec.saved("budget", oneshot={"working": "tests"})
    write(tree, "app.py", APP)
    assert check(tree) is not None and not sent   # the resend is returned; the server stops before it goes
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"


def test_the_third_refusal_stops_the_stage(built_world):
    tree, sent = built_world
    oneshot_spec.saved("budget", oneshot={"attempts": 2})
    write(tree, "app.py", APP)
    assert check(tree) is None and not sent
    assert o()["stopped"]["reason"] == "tests" and o()["working"] is None


# -- The build ----------------------------------------------------------------------

def test_a_build_with_a_full_report_a_green_gate_and_every_planned_test_passing_moves_to_review(built_world):
    tree, sent = built_world
    to_build(tree, sent)
    write(tree, "app.py", APP)
    git(tree, "add", "app.py")
    git(tree, "commit", "-m", "build")
    then = check(tree, "만들었다.\n" + report())
    assert o()["stage"] == "review" and o()["built_head"] == git(tree, "rev-parse", "HEAD")
    begun = []
    with patch("main.oneshot_review.begin", begun.append):
        then()
    assert begun == ["budget"]   # the backend review follows


def test_a_server_that_dies_between_the_passed_build_and_its_review_resumes_the_review(built_world):
    tree, sent = built_world
    to_build(tree, sent)
    write(tree, "app.py", APP)
    git(tree, "add", "app.py")
    git(tree, "commit", "-m", "build")
    check(tree, "만들었다.\n" + report())   # its review never started: the server went down
    assert o()["working"] == "review"
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    begun = []
    with patch("main.oneshot_review.begin", begun.append):
        oneshot_spec.resume("budget")
    assert begun == ["budget"]


@pytest.mark.parametrize("weaken, said", [
    (lambda tree: write(tree, "test_budget.py", TESTS.replace("== 3000", "== 0")), "바뀌었다"),
    (lambda tree: write(tree, "test_budget.py", TESTS + "\nimport pytest\npytest.skip('x', allow_module_level=True)\n"),
     "바뀌었다"),
    (lambda tree: (tree / "test_budget.py").unlink(), "없다"),
    # A configuration the tests turn never named is not pinned, but what it does to the pinned cases shows.
    (lambda tree: write(tree, "conftest.py", "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                                             "    for item in items:\n        item.add_marker(pytest.mark.skip)\n"),
     "(skipped)"),
    (lambda tree: (write(tree, "pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-k total"\n'),
                   write(tree, "test_extra.py", "def test_a_month_lists_its_spendings():\n    pass\n")),
     "test_budget::test_a_month_lists_its_spendings(돌지 않음)"),
    (lambda tree: write(tree, ".pytest.ini", "[pytest]\naddopts = -k total\n"), "(돌지 않음)"),
])
def test_a_weakened_contract_is_refused_even_when_the_gate_is_green(built_world, weaken, said):
    tree, sent = built_world
    to_build(tree, sent)
    write(tree, "app.py", APP)
    weaken(tree)
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "build")
    then = check(tree, "만들었다.\n" + report())
    assert o()["stage"] == "build" and o()["attempts"] == 1
    then()
    assert sent[-1][:2] == ("budget", "build") and said in sent[-1][2]


def test_a_same_named_test_in_another_file_does_not_stand_in_for_the_contract(built_world):
    tree, sent = built_world
    to_build(tree, sent)
    write(tree, "app.py", APP.replace("return amount", "return 0"))
    write(tree, "test_extra.py", "def test_spending_adds_to_the_total():\n    pass\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "build")
    green = {"ok": True, "head": git(tree, "rev-parse", "HEAD"), "reason": "", "tail": ""}
    with patch.object(specs, "judge", lambda *a, **k: green):   # a gate that runs only some of the tests
        check(tree, "만들었다.\n" + report())()
    assert "test_budget::test_spending_adds_to_the_total(failure)" in sent[-1][2]


def test_a_named_configuration_file_is_pinned_with_the_contract(built_world):
    tree, sent = built_world
    write(tree, "test_budget.py", TESTS)
    write(tree, "pytest.ini", "[pytest]\n")
    check(tree, told(config=["pytest.ini"]))()
    assert o()["contract"] == ["test_budget.py", "pytest.ini"]
    write(tree, "app.py", APP)
    write(tree, "pytest.ini", "[pytest]\naddopts = -q\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "build")
    check(tree, "만들었다.\n" + report())()
    assert "`pytest.ini` 가 바뀌었다" in sent[-1][2]


ANYIO = '''import pytest


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_spending_adds_to_the_total(anyio_backend):
    from app import add
    assert add(3000) == 3000
'''
UNITTEST = '''import unittest


class TestBudget(unittest.TestCase):
    def test_spending_adds_to_the_total(self):
        from app import add
        self.assertEqual(add(3000), 3000)
'''
READS = '''import json
from pathlib import Path


def test_spending_adds_to_the_total():
    from app import add
    amount = json.loads((Path(__file__).parent / "data" / "payload.json").read_text())["amount"]
    assert add(amount) == amount
'''


@pytest.mark.parametrize("files", [
    {"tests/test_add.py": ANYIO, "pytest.ini": "[pytest]\naddopts = -p anyio\n"},   # loaded with autoload off and on
    {"tests/test_add.py": UNITTEST},
    {"tests/test_add.py": READS, "tests/data/payload.json": '{"amount": 3000}\n'},
], ids=["anyio", "unittest", "data-file"])
def test_async_unittest_and_data_reading_contracts_go_from_red_to_green(built_world, files):
    tree, sent = built_world
    write(tree, "tests/test_month.py", TESTS.split("\n\n\n")[1])
    for name, text in files.items():
        write(tree, name, text)
    contract, config = ["tests/test_add.py", "tests/test_month.py"], [n for n in files if not n.startswith("tests/")]
    check(tree, told(contract, config))()
    assert o()["stage"] == "build", sent[-1][2]
    assert o()["contract"] == contract + config
    write(tree, "app.py", APP)
    git(tree, "add", "app.py")
    git(tree, "commit", "-m", "build")
    assert check(tree, "만들었다.\n" + report()) is not None and o()["stage"] == "review"


CLEANUP = '''import pytest


@pytest.fixture
def lifecycle():
    yield
    {cleanup}


def test_spending_adds_to_the_total(lifecycle):
    from app import add
    assert add(3000) == 3000
'''


def test_a_skipped_teardown_is_no_red_test_and_no_green_one(built_world):
    tree, sent = built_world
    write(tree, "test_month.py", TESTS.split("\n\n\n")[1])
    write(tree, "test_budget.py", CLEANUP.format(cleanup='pytest.skip("cleanup unavailable")'))
    both = told(contract=["test_budget.py", "test_month.py"])
    check(tree, both)()
    assert o()["stage"] == "tests" and "건너뛴" in sent[-1][2]
    write(tree, "test_budget.py", CLEANUP.format(cleanup='import importlib.util\n'
                                                 '    if importlib.util.find_spec("app"):\n'
                                                 '        pytest.skip("cleanup unavailable")'))
    check(tree, both)()
    assert o()["stage"] == "build"
    write(tree, "app.py", APP)
    git(tree, "add", "app.py")
    git(tree, "commit", "-m", "build")
    check(tree, "만들었다.\n" + report())()
    assert o()["stage"] == "build" and "test_budget::test_spending_adds_to_the_total(skipped)" in sent[-1][2]


def test_a_planned_test_in_a_file_the_runner_block_left_out_is_refused(built_world):
    tree, sent = built_world
    write(tree, "test_budget.py", TESTS.split("\n\n\n")[0])
    write(tree, "test_month.py", TESTS.split("\n\n\n")[1])
    check(tree)()
    assert o()["stage"] == "tests" and "`contract` 에 적는다 — test_month.py" in sent[-1][2]


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
        oneshot_spec.saved("budget", oneshot={"inflight": {"role": "tests", "turn": turn.token}})   # as `send` writes it
        turn.put({"kind": "error", "text": "사람이 멈춤", "meta": {}})
        elsewhere(turn.finish)
    assert o()["stopped"] == {**o()["stopped"], "reason": "block"} and "사람이 멈춤" in o()["stopped"]["detail"]
    assert o()["spend"]["calls"] == 1 and o()["spend"]["unknown"] == 1


def test_a_late_end_of_an_earlier_turn_leaves_the_resumed_turn_alone(built_world):
    tree, _ = built_world
    chat = SimpleNamespace(id="c", parent_id=None, repo=str(tree))
    with patch.object(work, "notice", lambda *a: None):
        old, new = (oneshot_build.Turn(chat, "budget", "tests", False) for _ in range(2))
        oneshot_spec.saved("budget", oneshot={"inflight": {"role": "tests", "turn": new.token}})
        elsewhere(old.finish)
        assert o()["inflight"]["turn"] == new.token and not o().get("stopped") and o()["spend"]["calls"] == 1
        new.ended.set()


@pytest.mark.parametrize("where", ["before", "inside"])
def test_a_turn_that_cannot_be_begun_leaves_nothing_in_flight_and_charges_nothing(built_world, where):
    tree, _ = built_world
    real = work.begin

    def refuse(*a):
        if where == "before":
            raise RuntimeError("세션이 없다")
        # The real `work.begin` refuses a thread while the server stops, ending the turn itself first.
        with patch.object(runtime_mod.stopping, "is_set", lambda: True):
            return real(*a)

    with patch.object(work, "notice", lambda *a: None), patch.object(work, "begin", refuse), \
         patch.object(work, "remember", lambda *a, **k: None), \
         patch.object(work, "session", lambda *a: SimpleNamespace(id="c", parent_id=None, repo=str(tree))), \
         patch.object(oneshot, "role_config", lambda *a: {"model": "m", "effort": "e"}), \
         patch.object(oneshot, "codex_role", lambda *a: False):
        SEND("budget", "tests", "x")
    assert o()["inflight"] is None and "보내지 못했다" in o()["stopped"]["detail"]
    assert not (o().get("spend") or {}).get("calls")   # no model ran
    assert str(tree) not in work._busy   # the worktree is free for the resumed turn


def test_a_work_turn_runs_on_the_build_model_the_run_fixed(built_world):
    tree, _ = built_world
    fixed = {"spec": {"model": "opus", "effort": "high"}, "build": {"model": "sonnet", "effort": "medium"},
             "review": {"model": "", "effort": ""}}
    oneshot_spec.saved("budget", oneshot={"models": fixed})
    opened = []

    def session(path, model, effort, *a):
        opened.append((model, effort))
        return SimpleNamespace(id="c", parent_id=None, repo=str(tree))

    with patch.object(work, "notice", lambda *a: None), patch.object(work, "remember", lambda *a, **k: None), \
         patch.object(work, "begin", lambda *a: (_ for _ in ()).throw(RuntimeError("멈춤"))), \
         patch.object(work, "session", session), \
         patch("main.query.config", lambda *a: {"model": "window", "effort": "e"}):
        SEND("budget", "tests", "x")
    assert opened == [("sonnet", "medium")]


def test_a_resume_waits_while_a_stopped_turn_is_still_being_charged(built_world):
    oneshot_spec.store("budget").stop("block", "멈춤")
    oneshot_spec.saved("budget", oneshot={"inflight": {"role": "tests", "turn": "t"}})
    with pytest.raises(HTTPException, match="비용을 아직 매기는 중"):
        oneshot_spec.resume("budget")


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
    s = {"oneshot": {"design": sheet.parsed(DESIGN), "profile": {"backend": "fastapi"}, "tests_head": "abc",
                     "contract": ["tests/test_a.py"], "gate": "uv run pytest"}}
    tests = oneshot_build.tests_text(s)
    assert "test_spending_adds_to_the_total" in tests and '"backend": "fastapi"' in tests and "oneshot-runner" in tests
    assert "{tests}" not in tests and "{profile}" not in tests and "uv run pytest" in oneshot_build.build_text(s)
    assert json.loads(tests.split("```json\n")[1].split("```")[0])[0]["id"] == "T1"
    assert "`tests/test_a.py`" in oneshot_build.build_text(s) and "abc" in oneshot_build.build_text(s)
