"""A survey spec runs as an ordinary task held to its mode
(`docs/plans/refactor/7-purpose-modes.md`, Execution): the start checks, the
characterization freeze of `preserve`, the test contract, the final command
against the survey's baseline, the measurement, and the conversation's and
the recheck's changes landing on the same card.

The survey host is `test_refactor_survey.Host`, the work session
`test_specs.Worker`; GitHub is `test_specs.Remote`. Git, the gate and the
final command run.
"""
# ruff: noqa: F811 — a borrowed fixture is named again by each test that takes it

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import refactor_profile
from main import query, refactor, refactor_run, refactor_survey, specs, work
from test_main import client, no_machine_settings, parse, until  # noqa: F401 — the fixture is autouse
from test_main import settled as settled_task
from test_refactor_survey import ROLES, Host, _git, settled, spec, start, surveyed  # noqa: F401 — `surveyed` is a fixture
from test_specs import Remote, Worker, _adapter, fresh_workers, report, repo, started  # noqa: F401

PY = Path(sys.executable).as_posix()
ONE = {"coverage": {"scope": "src", "unchecked": [], "excluded": []}}


@pytest.fixture
def running(surveyed, tmp_path, monkeypatch):
    """The surveyed project with a bare origin; work turns and GitHub stand in."""

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    _git(surveyed, "remote", "add", "origin", origin.as_posix())
    _git(surveyed, "push", "-q", "-u", "origin", "main")
    _git(surveyed, "remote", "set-head", "origin", "main")
    monkeypatch.setattr(refactor_profile, "STORE", tmp_path / "frozen")
    remote = Remote()
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote):
        yield surveyed, remote


def surveyed_in(mode: str, *specs_: dict) -> dict:
    query.config("refactor", "proj")["refactor_mode"] = mode
    Host.answers = {"survey": {**ONE, "specs": list(specs_)}}
    api = client()
    run = settled(api, start(api, f"survey-{mode}-0001", mode).json()["id"])
    assert run["state"] == "done", run["stopped"]
    return run


def edited(rel: str, text: str, message: str = "change"):
    def turn(path, halt):
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
        _git(path, "add", rel)
        _git(path, "commit", "-qm", message)
        return report(True, True)
    return turn


def test_a_card_runs_as_a_task_and_its_pr_carries_the_measured_check(running):
    repo, remote = running
    measure = {"metric": "wall time", "task": "one call", "argv": [PY, "-c", "print('ok')"], "repeat": 2}
    run = surveyed_in("rework", spec("orders-fast", measure=measure))
    Worker.replies = [edited("src/orders.py", "def place(order):\n    return order  # faster\n")]
    started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    assert card["state"] == "PR #7", card["fault"]
    check = card["refactor_check"]
    assert check["final"] == "local" and check["ok"] and check["new"] == [] and check["changed"] == []
    assert len(check["measured"]["before"]["ms"]) == 2 and check["measured"]["after"]["median_ms"] >= 0
    assert _git(Path(card["worktree"]), "branch", "--show-current") == card["id"], "measuring switched back"
    assert "## 리팩터링 검증" in remote.body and "측정 wall time" in remote.body and "새 실패 0건" in remote.body
    assert "`rework`" in Worker.made[0].system and "orders-fast" in Worker.made[0].system
    assert Worker.made[0].model == ROLES["worker"]["model"], "the work cell runs on the role fixed at the survey"
    work.session(Path(card["worktree"]), "opus", "low")   # the screen's toolbar, or a loop's turn
    assert specs.load("proj", card["id"])["cell"] == {**ROLES["worker"], "fast": False}, "no turn moves it"


def test_a_start_waits_for_its_prerequisite_its_mode_and_unchanged_files(running):
    repo, _ = running
    run = surveyed_in("preserve", spec("shared-check", "shared:src/shared.py", files=["src/shared.py"]),
                      spec("orders-split", depends=["shared-check"]))
    shared, orders = (specs.load("proj", sid) for sid in run["specs"])
    api = client()
    refused = api.post(f"/api/specs/{orders['id']}/start", json={})
    assert refused.status_code == 409 and "머지된 뒤" in refused.json()["detail"]
    (repo / "notes.txt").write_text("elsewhere\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-qm", "unrelated")
    assert refactor_run.startable(repo, shared, "") == ["notes.txt"], "an unrelated change is listed, not refused"
    (repo / "src/shared.py").write_text("def check(value):\n    return bool(value)\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "the target moved")
    with pytest.raises(HTTPException, match="다시 확인"):
        refactor_run.startable(repo, shared, "")
    query.remember("refactor", "user", "Split the order intake", repo=repo)
    settings = {"repo": "proj", "model": "opus", "effort": "high", "refactor_mode": "simplify"}
    assert api.post("/api/config/refactor", json=settings).json()["kept"]
    assert query.recall("refactor")[0]["text"] == "Split the order intake", "the conversation stays"
    api.post("/api/config/refactor", json={**settings, "refactor_mode": "preserve"})
    with pytest.raises(HTTPException, match="모드가 바뀌었다"):
        refactor_run.startable(repo, orders, "")
    assert specs.load("proj", shared["id"])["state"] == "정리됨" and not Worker.made, "nothing ran"


def test_a_restart_keeps_the_mode_and_generation_its_cards_were_made_in(running, monkeypatch):
    run = surveyed_in("simplify", spec("orders-names"))
    settings = {"repo": "proj", "model": "opus", "effort": "high"}
    client().post("/api/config/refactor", json={**settings, "refactor_mode": "rework"})
    client().post("/api/config/refactor", json={**settings, "refactor_mode": "simplify"})
    assert query.config("refactor", "proj")["refactor_generation"] == 2
    monkeypatch.setattr(query, "_config", {})   # what a restart leaves
    assert {k: query.config("refactor", "proj")[k] for k in ("refactor_mode", "refactor_generation")} == \
        {"refactor_mode": "simplify", "refactor_generation": 2}
    with pytest.raises(HTTPException, match="모드가 바뀌었다"):
        refactor_run.startable(running[0], specs.load("proj", run["specs"][0]), "")


class Talk:
    """The refactoring conversation's session: only its model is looked at."""

    def __init__(self, repo, model=None, effort=None, **_):
        self.model, self.effort, self.is_codex, self.session_id, self.alive = model, effort, False, None, True

    def reconfigure(self, model, effort, fast=None):
        self.model, self.effort = model, effort

    def close(self):
        pass


def test_the_conversation_refines_cards_on_the_survey_model_until_the_mode_changes(surveyed, monkeypatch):
    monkeypatch.setattr(query, "ChatSession", Talk)
    query.config("refactor", "proj").update(model="haiku", effort="low")   # the chat toolbar's pick
    query.session("refactor")   # a conversation already open on it
    surveyed_in("preserve", spec("orders-split"))
    assert query.session("refactor").model == ROLES["survey"]["model"], "refined on the survey model"
    api, toolbar = client(), {"repo": "proj", "model": "haiku", "effort": "low"}
    refused = api.post("/api/config/refactor", json=toolbar)
    assert refused.status_code == 409 and "고정" in refused.json()["detail"]
    monkeypatch.setattr(query, "_config", {})   # what a restart leaves
    assert {k: query.config("refactor", "proj")[k] for k in ("model", "refactor_pinned")} == \
        {"model": ROLES["survey"]["model"], "refactor_pinned": True}
    assert api.post("/api/config/refactor", json={**toolbar, "refactor_mode": "simplify"}).status_code == 200
    assert query.session("refactor").model == "haiku" and "refactor_pinned" not in query.config("refactor", "proj")


def test_a_card_from_before_roles_neither_starts_nor_is_rechecked_or_refined(surveyed):
    run = surveyed_in("preserve", spec("orders-split"))
    sid = run["specs"][0]
    card = specs.load("proj", sid)
    specs.save({**card, "refactor": {k: v for k, v in card["refactor"].items() if k != "roles"}})
    with pytest.raises(HTTPException, match="고정하기 전"):
        refactor_run.startable(surveyed, specs.load("proj", sid), "")
    refused = client().post(f"/api/refactors/specs/{sid}/recheck", json={"request_id": "recheck-old-0001", "mode": "preserve"})
    assert refused.status_code == 409 and "고정하기 전" in refused.json()["detail"]
    source = {"focus": "refactor", "turn": 2, "session": "talk", "refactor_mode": "preserve",
              "refactor_generation": query.config("refactor", "proj").get("refactor_generation", 0)}
    narrowed = {**spec("ignored", title="Narrow it"), "id": sid}
    assert "고정하기 전" in specs.answered(surveyed, [{"name": "spec", "value": narrowed}], source)[0]["error"]


def characterize(test: str, also: str = ""):
    def turn(path, halt):
        (path / "tests").mkdir(exist_ok=True)
        (path / test).write_text("from src.orders import place\nassert place(1) == 1\n", encoding="utf-8")
        if also:
            (path / also).write_text("def place(order):\n    return order or 0\n", encoding="utf-8")
        _git(path, "add", "-A")
        _git(path, "commit", "-qm", "characterize")
        # `-B`: no `__pycache__` left in a repository without a `.gitignore`.
        block = {"tests": [test], "test_argv": [PY, "-B", "-c", f"import runpy; runpy.run_path('{test}')"]}
        return "Pinned.\n\n```refactor-tests\n" + json.dumps(block) + "\n```"
    return turn


def test_preserve_freezes_its_characterization_before_the_refactoring(running):
    repo, remote = running
    run = surveyed_in("preserve", spec("orders-split"), spec("orders-tidy"))
    first, second = run["specs"]
    Worker.replies = [characterize("tests/test_orders.py"),
                      edited("src/orders.py", "def place(order):\n    result = order\n    return result\n")]
    started(client(), first)
    until(lambda: specs.load("proj", first)["state"] != "작업 중" or specs.load("proj", first)["fault"])
    card = specs.load("proj", first)
    frozen = card["refactor_frozen"]
    assert frozen["tests"] == ["tests/test_orders.py"] and Path(frozen["file"]).is_file()
    assert json.loads(Path(frozen["file"]).read_text(encoding="utf-8"))["head"] == frozen["head"]
    assert card["state"] == "PR #7", (card["fault"], card["gate"])
    assert "frozen.json" in card["validation"]["final"]["command"], "the frozen tests run in the final command"
    assert "동작 고정 테스트 `tests/test_orders.py`" in remote.body
    assert any("frozen at" in h for w in Worker.made for h in w.heard), "the refactoring turn was asked after"
    assert specs.load("proj", second)["state"] == "정리됨", "the other card never ran"


def test_a_characterization_that_touches_code_is_refused(running):
    run = surveyed_in("preserve", spec("orders-split"))
    Worker.replies = [characterize("tests/test_orders.py", also="src/orders.py")]
    started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    assert "테스트 밖을 바꿨다" in card["fault"] and not card.get("refactor_frozen") and card["pr"] is None


def test_an_existing_test_changes_only_within_the_contract(running):
    repo, remote = running
    (repo / "tests").mkdir()
    (repo / "tests/test_billing.py").write_text("assert 1 == 1\n", encoding="utf-8")
    _git(repo, "add", "tests")
    _git(repo, "commit", "-qm", "billing test")
    _git(repo, "push", "-q", "origin", "main")
    run = surveyed_in("simplify", spec("billing-names", "billing", files=["src/billing.py"]))
    Worker.replies = [edited("tests/test_billing.py", "assert 2 == 2\n")]
    started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    assert "테스트 계약 밖의 변경" in card["fault"] and "test_billing.py" in card["fault"]
    assert not remote.created()


OLD_TESTS = "def test_guard():\n    assert True\n\n\ndef test_other():\n    assert True\n"


def contracted(repo: Path, new: str | None, *declared: str, old: str = OLD_TESTS) -> str:
    """`contract` on one existing test file changed to `new` (None deletes it), with `declared` removals."""
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests/test_guard.py").write_text(old, encoding="utf-8")
    _git(repo, "add", "tests")
    _git(repo, "commit", "-qm", "guard tests")
    start = _git(repo, "rev-parse", "HEAD")
    if new is None:
        _git(repo, "rm", "-q", "tests/test_guard.py")
    else:
        (repo / "tests/test_guard.py").write_text(new, encoding="utf-8")
    _git(repo, "commit", "-qam", "edit")
    card = {"start_head": start, "refactor": {"detail": {"tests": {
        "remove": [{"test": d, "reason": "exact duplicate"} for d in declared]}}}}
    return refactor_run.contract(repo, card)


@pytest.mark.parametrize("new, declared, why", [
    (OLD_TESTS + "\n\ndef test_new():\n    assert True\n", (), ""),
    ("import pytest\n\n\n@pytest.mark.skip(reason='off')\n" + OLD_TESTS, (), "기존 테스트 변경"),
    (OLD_TESTS + "\npytestmark = __import__('pytest').mark.skip\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\n\ndef test_guard():\n    pass\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\ntest_guard = lambda: None\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\ndel test_other\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\nfrom os import sep as test_guard\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\n\ndef helper():\n    global test_guard\n    test_guard = None\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\ntry:\n    raise RuntimeError()\nexcept RuntimeError as test_guard:\n    pass\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\nif True:\n    pytestmark = __import__('pytest').mark.skip\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\nCASES = [(test_guard := n) for n in range(3)]\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\nmatch 1:\n    case test_guard:\n        pass\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\nfrom os.path import *\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\n\ndef helper(arg: (test_guard := lambda: None)):\n    pass\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\n\ndef helper() -> (test_guard := lambda: None):\n    pass\n", (), "기존 테스트 변경"),
    (OLD_TESTS + "\nCASES = [test_guard for test_guard in range(3)]\n", (), ""),   # the comprehension's own
    (OLD_TESTS + "\n\ndef test_new(test_guard=None):\n    test_other = 1\n", (), ""),    # the function's own
    ("def test_guard():\n    assert True\n", ("tests/test_guard.py::test_other",), ""),
    ("def test_guard():\n    pass\n", ("tests/test_guard.py::test_other",), "기존 테스트 변경"),
    (None, ("tests/test_guard.py::test_other",), "파일 단위"),
    (None, ("tests/test_guard.py",), ""),
    (OLD_TESTS + "\n\ndef test_new():\n    assert True\n", ("tests/test_guard.py::test_gone",), "시작 커밋에 없다"),
])
def test_a_test_contract_frees_only_what_it_names(surveyed, new, declared, why):
    found = contracted(surveyed, new, *declared)
    assert (why in found) if why else found == "", found


def test_a_type_comment_is_part_of_the_test_contract(surveyed):
    typed = OLD_TESTS + "\nvalue = []  # type: list[int]\n"
    assert "기존 테스트 변경" in contracted(surveyed, OLD_TESTS + "\nvalue = []\n", old=typed)


@pytest.mark.parametrize("quiet", [True, False])
def test_a_pytest_count_line_is_explained_by_no_more_failures_than_the_baseline(tmp_path, quiet):
    warn = "import warnings\n\n\ndef test_w{n}():\n    warnings.warn('w')\n"
    (tmp_path / "test_x.py").write_text("def test_old():\n    assert False\n\n\n" + warn.format(n=1), encoding="utf-8")

    def tail():   # a real pytest run's closing lines
        argv = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:xdist", *(["-q"] if quiet else [])]
        return subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True).stdout

    base = tail()
    (tmp_path / "test_x.py").write_text((tmp_path / "test_x.py").read_text() + "\n\n" + warn.format(n=2))
    assert refactor_survey.unexplained(tail(), base, (tmp_path,)) == [], "one more warning is no failure"
    for grown in ("91 failed, 1 passed in 1.08s", "==== 1 failed, 2 passed, 1 error in 1.20s ===="):
        assert refactor_survey.unexplained(grown, base.replace("1 failed", "90 failed"), ()) == [grown]
    old = "FAILED t.py::a - x\nFAILED docs/case1 notes\nERROR conda.cli.main_run:execute(127): failed.\n"
    assert refactor_survey.unexplained(old, old, ()) == []
    # A FAILED line that names no node is compared digit for digit.
    assert refactor_survey.unexplained(old + "FAILED docs/case2 notes\n", old, ()) == ["FAILED docs/case2 notes"]
    # An indented ERROR line names its node, so the name comparison sees it as new.
    indented = old + "    ERROR checks/bootstrap.py - FATAL cache initialization failed\n"
    assert refactor_survey.failures({"code": 1, "tail": indented}) == ["checks/bootstrap.py", "t.py::a"]
    for sentence in ("1 passed health check, ERROR cache initialization in 0.05s",
                     "1 failed health check: FATAL database unavailable in 0.05s"):   # counts, but not pytest's list
        assert not refactor_survey.SUMMARY.match(sentence)
        assert refactor_survey.unexplained(sentence, base, ()) == [sentence]


def test_every_round_holds_a_survey_spec_to_its_test_contract(surveyed):
    contracted(surveyed, "def test_guard():\n    pass\n")
    start = _git(surveyed, "rev-parse", "HEAD~1")
    card = {"id": "x", "repo": "proj", "done": ["the gate passes"], "start_head": start,
            "refactor": {"survey": "abcd1234", "mode": "simplify", "detail": {"tests": {}}}}
    verdict, record = specs.rounded(surveyed, surveyed, card, "main", __import__("threading").Event())
    assert not verdict["ok"] and "테스트 계약 밖의 변경" in verdict["reason"] and not record["ok"]


GATE = ("import pathlib, sys\nrows = pathlib.Path('failing.txt').read_text().split()\n"
        "[print('FAILED ' + r) for r in rows if '::' in r]\n"
        "[print('FATAL an unnamed error') for r in rows if r in ('FATAL', 'QUIET')]\n"
        "[pathlib.Path('src/orders.py').write_text('VALUE = 999\\n') for r in rows if r == 'WRITE']\n"
        "sys.exit(2 if 'FATAL' in rows else 1 if rows else 0)\n")


@pytest.mark.parametrize("extra, why", [
    ("", ""), ("t.py::b\n", "새 실패"), ("FATAL\n", "2 로 끝났다"),
    ("QUIET\n", "기준 게이트 출력에 없다"),   # an unnamed error under the baseline's own exit code
    ("WRITE\n", "작업트리나 HEAD"),           # the old failure, but the command rewrote the code it judged
])
def test_only_a_failure_the_baseline_did_not_have_stops_the_pr(running, extra, why):
    repo, remote = running
    (repo / "gate.py").write_text(GATE, encoding="utf-8")
    (repo / "failing.txt").write_text("t.py::a\n", encoding="utf-8")
    _git(repo, "add", "gate.py", "failing.txt")
    _git(repo, "commit", "-qm", "a gate with a known failure")
    _git(repo, "push", "-q", "origin", "main")
    _adapter(repo, f"{PY} gate.py")
    run = surveyed_in("simplify", spec("orders-names"))
    assert run["baseline"]["failures"] == ["t.py::a"] and run["baseline"]["parsed"]
    Worker.replies = [edited("failing.txt", "t.py::a\n" + extra) if extra else
                      edited("src/orders.py", "def place(order):\n    return order  # named\n")]
    started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    if not why:
        assert card["state"] == "PR #7" and "시작 전부터 있던 실패 1건" in remote.body
        return
    assert why in (card["fault"] or ""), card["fault"]
    assert not remote.created()
    if extra != "WRITE\n":
        check = card["refactor_check"]
        assert check["pre_existing"] == ["t.py::a"] and check["new"] == (["t.py::b"] if "::" in extra else [])


def test_a_final_command_that_never_judged_the_head_opens_nothing(running):
    repo, remote = running
    (repo / "gate.py").write_text("import pathlib\npathlib.Path('src/orders.py').write_text('x = 1\\n')\n",
                                  encoding="utf-8")
    _git(repo, "add", "gate.py")
    _git(repo, "commit", "-qm", "a gate that writes into the tree")
    _git(repo, "push", "-q", "origin", "main")
    _adapter(repo, f"{PY} gate.py")
    run = surveyed_in("simplify", spec("orders-names"))
    Worker.replies = [edited("src/billing.py", "def charge(order):\n    return order  # named\n")]
    started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    assert "최종 검증이 무효다" in card["fault"] and not remote.created()


def test_a_ci_repository_still_measures_a_rework_spec(running, monkeypatch):
    repo, remote = running
    measure = {"metric": "wall time", "task": "one call", "argv": [PY, "-c", "print('ok')"], "repeat": 1}
    run = surveyed_in("rework", spec("orders-fast", measure=measure))
    passing = f'{PY} -c "print(1)"'
    monkeypatch.setattr(specs.verify, "policy", lambda path, project=None: {"local": passing, "round": passing})
    monkeypatch.setattr(specs.verify, "backend", lambda path, cfg: "ci")
    Worker.replies = [edited("src/orders.py", "def place(order):\n    return order  # faster\n")]
    started(client(), run["specs"][0])
    until(lambda: specs.load("proj", run["specs"][0])["state"] != "작업 중" or specs.load("proj", run["specs"][0])["fault"])
    card = specs.load("proj", run["specs"][0])
    check = card["refactor_check"]
    assert check.get("final") == "ci" and len(check["measured"]["after"]["ms"]) == 1, (card["state"], card["fault"], check)


def test_a_card_saved_with_a_dotted_path_still_sees_its_file_change(surveyed):
    run = surveyed_in("preserve", spec("orders-split"))
    card = specs.load("proj", run["specs"][0])
    card = {**card, "refactor": {**card["refactor"], "files": ["./src/orders.py"], "evidence": ["src/../src/orders.py"]}}
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    with pytest.raises(HTTPException, match="다시 확인"):
        refactor_run.startable(surveyed, card, "")


def test_a_clone_without_origin_head_asks_github_and_a_rerun_keeps_no_old_verdict(running):
    repo, remote = running
    (repo / "gate.py").write_text("import pathlib, sys\nsys.exit(pathlib.Path('stop.txt').exists())\n", encoding="utf-8")
    _git(repo, "add", "gate.py")
    _git(repo, "commit", "-qm", "a gate that names no failure")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "-d")   # what `gh repo create --push` leaves
    _adapter(repo, f"{PY} gate.py")
    run = surveyed_in("simplify", spec("orders-names"))

    def unstop(path, halt):
        _git(path, "rm", "-q", "stop.txt")
        _git(path, "commit", "-qm", "unstop")
        return report(True, True)
    Worker.replies = [edited("stop.txt", "x\n"), unstop]
    path = started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    assert "가리지 못해" in card["fault"] and card["refactor_check"]["unparsed"], "the command ran and failed"
    parse(client().post("/api/work/say", json={"path": path, "text": "again"}).text)
    settled_task(path)
    card = specs.load("proj", run["specs"][0])
    assert card["state"] == "PR #7", card["fault"]
    assert card["refactor_check"]["ok"] and "unparsed" not in card["refactor_check"], "no verdict of the first run"
    assert ["gh", "repo", "view"] in [c[:3] for c in remote.calls]


def test_a_hub_card_runs_in_a_linked_worktree(running, monkeypatch):
    repo, _ = running
    run = surveyed_in("simplify", spec("orders-names"))
    monkeypatch.setattr(refactor, "scope_of", lambda _: "hub")
    started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    assert Path(card["worktree"]) != repo and _git(repo, "branch", "--show-current") == "main"
    assert "workspace_mode" not in card


def test_the_conversation_refines_the_same_card_and_splits_off_only_the_new_part(surveyed):
    run = surveyed_in("preserve", spec("orders-split"))
    sid = run["specs"][0]
    source = {"focus": "refactor", "turn": 2, "session": "talk", "refactor_mode": "preserve",
              "refactor_generation": query.config("refactor", "proj").get("refactor_generation", 0)}
    narrowed = {**spec("ignored", title="Split the intake out of place"), "id": sid}
    split = {**spec("orders-check", title="Move the order check beside place", depends=[sid]), "split_from": sid}
    shown = specs.answered(surveyed, [{"name": "spec", "value": [narrowed, split]}], source)
    assert [s.get("id") for s in shown] == [sid, f"orders-check-{run['id']}"], shown
    card = specs.load("proj", sid)
    assert card["rev"] == 2 and card["refactor"]["detail"]["key"] == "orders-split"
    assert card["refactor"]["detail"]["title"] == "Split the intake out of place"
    assert specs.load("proj", shown[1]["id"])["refactor"]["depends"] == [sid]
    for block, why in (({**spec("free")}, "조사 없이"), ({**spec("x", files=["src/missing.py"]), "id": sid}, "없다"),
                       ({**spec("x"), "id": sid}, "모드"), ({**spec("x", depends=[shown[1]["id"]]), "id": sid}, "돌아온다")):
        given = {**source, "refactor_mode": "simplify"} if why == "모드" else source
        assert why in specs.answered(surveyed, [{"name": "spec", "value": block}], given)[0]["error"]
    other = specs.load("proj", shown[1]["id"])
    specs.save({**other, "refactor": {**other["refactor"], "survey": "recheck-two"}})   # what a recheck gives it
    looped = {**spec("x", depends=[other["id"]]), "id": sid}
    assert "돌아온다" in specs.answered(surveyed, [{"name": "spec", "value": looped}], source)[0]["error"]
    assert specs.load("proj", sid)["rev"] == 2


def test_recheck_rewrites_a_stale_card_or_sets_it_aside(surveyed):
    run = surveyed_in("preserve", spec("orders-split"))
    sid = run["specs"][0]
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    head, api = _git(surveyed, "rev-parse", "HEAD"), client()
    Host.answers = {"survey": {**ONE, "specs": [spec("orders-split", title="Split place again")]}}
    body = {"request_id": "recheck-req-0001", "mode": "preserve"}
    again = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck", json=body).json()["id"])
    assert again["state"] == "done" and again["recheck"] == sid
    assert again["roles"] == ROLES and Host.made[-1].rest["model"] == ROLES["survey"]["model"], "the card's roles"
    assert Host.made[-1].rest["system"] == refactor_survey.RECHECK_PROMPT
    card = specs.load("proj", sid)
    assert card["rev"] == 2 and card["refactor"]["base"] == head and card["refactor"]["survey"] == again["id"]
    assert card["refactor"]["roles"] == ROLES and card["reviewer"] == ROLES["reviewer"]
    assert refactor_run.startable(surveyed, card, "") == []
    assert specs.load("proj", sid)["state"] == "정리됨", "a recheck retires no card of its own survey"
    Host.answers = {"survey": {**ONE, "specs": [], "nothing": "The split already happened"}}
    gone = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck",
                                 json={**body, "request_id": "recheck-req-0002"}).json()["id"])
    assert gone["state"] == "done" and gone["specs"] == [] and specs.load("proj", sid) is None


def test_a_recheck_keeps_a_card_that_depends_on_another_spec_of_its_survey(surveyed):
    run = surveyed_in("preserve", spec("shared-check", "shared:src/shared.py", files=["src/shared.py"]),
                      spec("orders-split", depends=["shared-check"]))
    sid = run["specs"][1]
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    Host.answers = {"survey": {**ONE, "specs": [spec("orders-split", depends=["shared-check"])]}}
    body = {"request_id": "recheck-req-0003", "mode": "preserve"}
    api = client()
    again = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck", json=body).json()["id"])
    assert again["state"] == "done", again["stopped"]
    assert specs.load("proj", sid)["refactor"]["depends"] == [run["specs"][0]]


@pytest.mark.parametrize("name, old, new, same", [
    ("tests/data.json", b'{"x": true}', b'{"x": 1}', False),
    ("tests/data.json", b'{"x": 1, "x": 2}', b'{"x": 2}', False),
    ("tests/data.json", b'{"x": 1.0000000000000000001}', b'{"x": 1.0}', False),
    ("tests/data.json", b'{"x": -0}', b'{"x": 0}', False),
    ("tests/data.json", b'{"x": 1}', b'{"x": ["number", "1"]}', False),
    ("tests/data.json", b'{"x": NaN}', b'{"x": null}', False),
    ("tests/data.json", b'{"x": 1, "y": [2]}', b'{"y": [2], "x": 1}', True),
    ("tests/ui.snap", b"expect text", b"expect", False),
    ("test_a.py", b"assert x == 1\n", b"assert True\n", False),
    ("test_a.py", b"assert x == 1\n", b"# kept\nassert (x == 1)\n", True),
    ("test_a.py", b"x = []  # type: list[int]\n", b"x = []\n", False),
])
def test_an_equivalent_test_file_keeps_every_observation(name, old, new, same):
    assert refactor_run.equivalent(name, old, new) is same
