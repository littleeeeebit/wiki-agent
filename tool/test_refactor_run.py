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
from test_main import client, no_machine_settings, until  # noqa: F401 — the fixture is autouse
from test_refactor import _git
from test_refactor_survey import Host, settled, spec, start, surveyed  # noqa: F401 — `surveyed` is a fixture
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


GATE = ("import pathlib, sys\nrows = pathlib.Path('failing.txt').read_text().split()\n"
        "[print('FAILED ' + r) for r in rows]\nsys.exit(1 if rows else 0)\n")


@pytest.mark.parametrize("extra, opens", [("", True), ("t.py::b\n", False)])
def test_only_a_failure_the_baseline_did_not_have_stops_the_pr(running, extra, opens):
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
    assert card["refactor_check"]["pre_existing"] == ["t.py::a"]
    if opens:
        assert card["state"] == "PR #7" and "시작 전부터 있던 실패 1건" in remote.body
    else:
        assert card["refactor_check"]["new"] == ["t.py::b"] and "새 실패" in card["fault"]
        assert not remote.created()


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
                       ({**spec("x"), "id": sid}, "모드")):
        given = {**source, "refactor_mode": "simplify"} if why == "모드" else source
        assert why in specs.answered(surveyed, [{"name": "spec", "value": block}], given)[0]["error"]
    assert specs.load("proj", sid)["rev"] == 2


def test_recheck_rewrites_a_stale_card_or_sets_it_aside(surveyed):
    run = surveyed_in("preserve", spec("orders-split"))
    sid = run["specs"][0]
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    head, api = _git(surveyed, "rev-parse", "HEAD"), client()
    Host.answers = {"survey": {**ONE, "specs": [spec("orders-split", title="Split place again")]}}
    body = {"request_id": "recheck-req-0001", "mode": "preserve", "role": {"model": "claude-test", "effort": ""}}
    again = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck", json=body).json()["id"])
    assert again["state"] == "done" and again["recheck"] == sid
    assert Host.made[-1].rest["system"] == refactor_survey.RECHECK_PROMPT
    card = specs.load("proj", sid)
    assert card["rev"] == 2 and card["refactor"]["base"] == head and card["refactor"]["survey"] == again["id"]
    assert refactor_run.startable(surveyed, card, "") == []
    assert specs.load("proj", sid)["state"] == "정리됨", "a recheck retires no card of its own survey"
    Host.answers = {"survey": {**ONE, "specs": [], "nothing": "The split already happened"}}
    gone = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck",
                                 json={**body, "request_id": "recheck-req-0002"}).json()["id"])
    assert gone["state"] == "done" and gone["specs"] == [] and specs.load("proj", sid) is None
