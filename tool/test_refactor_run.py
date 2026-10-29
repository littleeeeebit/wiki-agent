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
from test_task_workflow import until
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


def landed(sid: str) -> dict:
    """The card once the turn after its gate, the PR text's, published it or a fault stopped it."""

    return until(lambda: (c := specs.load("proj", sid))["state"].startswith("PR #") or c["fault"]) and specs.load("proj", sid)


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
    card = landed(run["specs"][0])
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
    run = surveyed_in("simplify", spec("shared-check", "shared:src/shared.py", files=["src/shared.py"]),
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
    settings = {"repo": "proj", "model": "opus", "effort": "high", "refactor_mode": "preserve"}
    assert api.post("/api/config/refactor", json=settings).json()["kept"]
    assert query.recall("refactor")[0]["text"] == "Split the order intake", "the conversation stays"
    api.post("/api/config/refactor", json={**settings, "refactor_mode": "simplify"})
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


def test_an_unstarted_card_takes_new_work_and_review_models_and_a_started_one_keeps_its_own(running):
    # User's choice: a quota running low on one model moves the cards still to start, one by one.
    first, second = surveyed_in("simplify", spec("orders-split"), spec("billing-names", "billing"))["specs"]
    api = client()
    cheap = {"worker": {"model": "claude-cheap", "effort": "low"}, "reviewer": {"model": "codex:gpt-cheap", "effort": ""},
             "was": {k: ROLES[k] for k in ("worker", "reviewer")}}
    empty = api.post(f"/api/refactors/specs/{first}/roles", json={**cheap, "worker": {"model": " ", "effort": ""}})
    assert empty.status_code == 400 and "구현 모델" in empty.json()["detail"]
    card = api.post(f"/api/refactors/specs/{first}/roles", json=cheap).json()
    assert card["refactor"]["roles"] == {"survey": ROLES["survey"], "worker": cheap["worker"],
                                         "reviewer": cheap["reviewer"]}, "the survey model stays"
    assert card["cell"] == {**cheap["worker"], "fast": False} and card["reviewer"] == cheap["reviewer"]
    assert card["refactor"]["role_changes"][0]["from"] == {k: ROLES[k] for k in ("worker", "reviewer")}
    assert specs.load("proj", second)["cell"] == {**ROLES["worker"], "fast": False}, "only the card asked"
    Worker.replies = [edited("src/orders.py", "def place(order):\n    return order  # split\n")]
    started(api, first)
    assert Worker.made[0].model == "claude-cheap", "the work cell runs on the model chosen again"
    refused = api.post(f"/api/refactors/specs/{first}/roles", json=cheap)
    assert refused.status_code == 409 and "끝까지" in refused.json()["detail"]


def test_a_card_keeps_its_chosen_models_through_a_refinement_a_recheck_and_another_screen(surveyed, monkeypatch):
    # Review of #236: a refinement and a recheck remade the card from its survey record, back on the
    # models it started with; an editor opened before another screen's change saved over it.
    sid = surveyed_in("preserve", spec("orders-split", "all"))["specs"][0]
    api, path = client(), f"/api/refactors/specs/{sid}/roles"
    cheap = {"worker": {"model": "claude-cheap", "effort": "low"}, "reviewer": {"model": "codex:gpt-cheap", "effort": ""}}
    assert api.post(path, json={**cheap, "was": {k: ROLES[k] for k in ("worker", "reviewer")}}).status_code == 200
    source = {"focus": "refactor", "turn": 2, "session": "talk", "refactor_mode": "preserve",
              "refactor_generation": query.config("refactor", "proj").get("refactor_generation", 0)}
    # Review round 2 of #244: narrowed here, its recheck back to `all` kept one pipeline's report.
    with pytest.raises(ValueError, match="저장소 전체 명세"):
        refactor_survey.refined(surveyed, {**spec("ignored", title="Split intake"), "id": sid}, source)
    assert refactor_survey.refined(surveyed, {**spec("ignored", "all", title="Split intake"), "id": sid}, source) == sid
    card = specs.load("proj", sid)
    assert card["rev"] == 2 and card["cell"] == {**cheap["worker"], "fast": False} and card["reviewer"] == cheap["reviewer"]
    assert len(card["refactor"]["role_changes"]) == 1
    later = {"worker": {"model": "claude-later", "effort": ""}, "reviewer": cheap["reviewer"]}
    real = refactor_survey.asked

    def asked(*args, **kwargs):   # another screen's change lands while the recheck reads the code
        if args[2] == "recheck":
            assert api.post(path, json={**later, "was": cheap}).status_code == 200
        return real(*args, **kwargs)

    monkeypatch.setattr(refactor_survey, "asked", asked)
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    Host.answers = {"survey": {**ONE, "specs": [spec("orders-split", "all", title="Split place again")]}}
    again = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck",
                                  json={"request_id": "recheck-roles-0001", "mode": "preserve"}).json()["id"])
    card = specs.load("proj", sid)
    assert again["state"] == "done" and card["refactor"]["survey"] == again["id"]
    assert set(again["pipelines"]) == {"orders", "billing"}, "a whole card's recheck keeps every report"
    assert card["cell"]["model"] == "claude-later" and card["refactor"]["roles"]["survey"] == ROLES["survey"]
    assert [c["to"]["worker"]["model"] for c in card["refactor"]["role_changes"]] == ["claude-cheap", "claude-later"]
    stale = api.post(path, json={**cheap, "was": cheap})
    assert stale.status_code == 409 and "다른 화면" in stale.json()["detail"]


def test_a_new_survey_deletes_the_unstarted_cards_of_an_earlier_one_at_its_start(running):
    # User's choice: at the new survey's start, not at its end (which sets every older card aside, `carded`),
    # and deleted, not set aside — even when the new survey then stops without cards.
    first, second = surveyed_in("simplify", spec("orders-split"), spec("billing-names", "billing"))["specs"]
    Worker.replies = [edited("src/orders.py", "def place(order):\n    return order  # split\n")]
    started(client(), first)
    Host.answers = {"survey": {**ONE, "specs": [{"key": "broken"}]}}
    api = client()
    stopped = settled(api, start(api, "survey-simplify-0002", "simplify").json()["id"])
    assert stopped["state"] == "stopped" and not stopped["specs"]
    assert specs.load("proj", second) is None and not list(specs.SPECS.glob(f"proj/dropped/{second}.*")), "deleted"
    assert specs.load("proj", first)["state"] != "정리됨", "a started card goes on"


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
    surveyed_in("preserve", spec("orders-split", "all"))
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
    run = surveyed_in("preserve", spec("orders-split", "all"))
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
    first, = surveyed_in("preserve", spec("orders-split", "all"))["specs"]
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


def test_a_characterization_that_touches_code_is_refused(running):
    run = surveyed_in("preserve", spec("orders-split", "all"))
    Worker.replies = [characterize("tests/test_orders.py", also="src/orders.py")]
    started(client(), run["specs"][0])
    card = specs.load("proj", run["specs"][0])
    assert "테스트 밖을 바꿨다" in card["fault"] and not card.get("refactor_frozen") and card["pr"] is None


def test_a_characterization_beside_a_spec_update_is_still_frozen(running):
    """ai-companion-voice-agent preserve-repository-boundaries: the turn that pinned the tests also revised
    the spec, the revision's report request ended the turn, and the `refactor-tests` block was never read."""
    first, = surveyed_in("preserve", spec("orders-split", "all"))["specs"]
    card = specs.load("proj", first)
    change = {"rev": card["rev"], "reason": "Name the live limits", **{k: card[k] for k in ("goal", "out", "done")}}
    pin = characterize("tests/test_orders.py")
    Worker.replies = [lambda path, halt: pin(path, halt) + "\n\n```spec-update\n" + json.dumps(change) + "\n```",
                      edited("src/orders.py", "def place(order):\n    result = order\n    return result\n")]
    started(client(), first)
    until(lambda: specs.load("proj", first)["state"] != "작업 중" or specs.load("proj", first)["fault"])
    card = specs.load("proj", first)
    assert card["refactor_frozen"]["tests"] == ["tests/test_orders.py"] and card["rev"] == change["rev"] + 1
    assert card["state"] == "PR #7", (card["fault"], card["gate"])


def test_a_spec_update_that_only_mentions_the_characterization_still_asks_for_the_report():
    """PR #245 round 1: the marker in prose, or on a card that is not an unfrozen `preserve` one, is no
    characterization, and the revision's report request must still go out."""
    card = {"refactor": {"mode": "preserve", "survey": "s", "roles": {}}}
    block = "```refactor-tests\n{}\n```"
    assert refactor_run.characterizing(card, block)
    assert not refactor_run.characterizing(card, "I will supply ```refactor-tests later.")
    assert not refactor_run.characterizing({**card, "refactor_frozen": {"head": "a"}}, block)
    assert not refactor_run.characterizing({"refactor": {**card["refactor"], "mode": "simplify"}}, block)
    assert not refactor_run.characterizing({}, block)


MANIFEST = {"name": "web", "scripts": {"test": "vitest run"}, "dependencies": {"react": "18.3.1"}, "config": {"flag": 1}}
DEV = {**MANIFEST, "devDependencies": {"react-test-renderer": "18.3.1"}}
LOCK = {"name": "web", "lockfileVersion": 3, "packages": {"": {k: MANIFEST[k] for k in ("name", "dependencies")},
                                                          "node_modules/react": {"version": "18.3.1"}}}
DEV_LOCK = {**LOCK, "packages": {**LOCK["packages"], "": {**LOCK["packages"][""], "devDependencies": DEV["devDependencies"]},
                                 "node_modules/react-test-renderer": {"version": "18.3.1", "dev": True}}}
REPINNED = {**DEV_LOCK, "packages": {**DEV_LOCK["packages"], "node_modules/react": {"version": "18.3.2"}}}


@pytest.mark.parametrize("files, accepted", [
    ({"web/package.json": DEV, "web/package-lock.json": DEV_LOCK}, True),
    ({"web/package.json": {**MANIFEST, "dependencies": {"react": "18.3.1", "left-pad": "1.3.0"}}}, False),
    ({"web/package.json": {**DEV, "scripts": {"test": "true"}}}, False),
    ({"web/package.json": {**DEV, "config": {"flag": True}}}, False),   # JSON's `true` is not `1`
    ({"web/package-lock.json": DEV_LOCK}, False),   # a lock without its manifest
    ({"web/package.json": DEV, "web/package-lock.json": REPINNED}, False),   # a production package moved
    ({"web/package.json": DEV, "web/yarn.lock": "# yarn"}, False),   # a lock format that is not read
    ({"other/package.json": {"devDependencies": {"vitest": "2"}}}, False),   # a new manifest
    ({"web/package.json": None}, False),   # a deleted manifest
])
def test_a_characterization_may_add_only_a_test_dependency(tmp_path, files, accepted):
    """The voice agent's studio tests needed `react-test-renderer`: `devDependencies` and an npm lock that
    moves only `dev` packages go with the tests. PR #245 round 1: nothing else does."""
    def commit(written: dict) -> str:
        for name, value in written.items():
            target = tmp_path / name
            if value is None:
                target.unlink()
                continue
            target.parent.mkdir(exist_ok=True)
            target.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-qm", "c")
        return _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    base = commit({"web/package.json": MANIFEST, "web/package-lock.json": LOCK})
    commit(files)
    assert all(refactor_run.dev_only(tmp_path, base, name, list(files)) for name in files) == accepted


def test_a_characterization_with_a_test_dependency_is_frozen(running):
    repo, _ = running
    (repo / "web").mkdir()
    (repo / "web/package.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (repo / "web/package-lock.json").write_text(json.dumps(LOCK), encoding="utf-8")
    _git(repo, "add", "web")
    _git(repo, "commit", "-qm", "web")
    _git(repo, "push", "-q", "origin", "main")
    first, = surveyed_in("preserve", spec("orders-split", "all"))["specs"]
    pin = characterize("tests/test_orders.py")

    def turn(path, halt):
        (path / "web/package.json").write_text(json.dumps(DEV), encoding="utf-8")
        (path / "web/package-lock.json").write_text(json.dumps(DEV_LOCK), encoding="utf-8")
        return pin(path, halt)
    Worker.replies = [turn, edited("src/orders.py", "def place(order):\n    result = order\n    return result\n")]
    started(client(), first)
    until(lambda: specs.load("proj", first)["state"] != "작업 중" or specs.load("proj", first)["fault"])
    card = specs.load("proj", first)
    assert card["refactor_frozen"]["tests"] == ["tests/test_orders.py"], card["fault"]


RULE = ("import pathlib, sys\n"
        "bad = [p for p in pathlib.Path('tests').glob('test_*.py') if 'marked' not in p.read_text()]\n"
        "print(*['FAILED rule.py::test_every_test_is_marked'] * bool(bad))\n"
        "sys.exit(1 if bad else 0)\n")


# PR #246 round 1: a gate that edits the tree judged another version; a new unnamed error beside an old
# named failure is what the final command refuses too.
FORMATTER = ("from pathlib import Path\np = Path('tests/test_orders.py')\n"
             "if p.exists(): p.write_text(p.read_text() + '# formatted\\n')\n")
WRAPPER = ("import pathlib, sys\nprint('FAILED old.py::test_old')\n"
           "if pathlib.Path('tests/test_orders.py').exists(): print('FATAL repository marker rule failed')\n"
           "sys.exit(1)\n")


@pytest.mark.parametrize("gate, said", [
    (RULE, "새 실패: rule.py::test_every_test_is_marked — 고정 전이라 그 테스트를 아직 고칠 수 있다"),
    (FORMATTER, "동작 고정 커밋을 판정하지 못했다"),
    (WRAPPER, "FATAL repository marker rule failed — 고정 전이라"),
], ids=["new-failure", "gate-edits-tree", "unnamed-error"])
def test_a_characterization_that_breaks_a_repository_rule_is_not_frozen(running, gate, said):
    """ai-companion-voice-agent preserve-repository-boundaries: five new integration tests lacked the
    settings marker a repository test requires; the final gate found it after the freeze, when the frozen
    tests could no longer change. The baseline gate runs on the characterization head first."""
    repo, _ = running
    (repo / "rule.py").write_text(gate, encoding="utf-8")
    _git(repo, "add", "rule.py")
    _git(repo, "commit", "-qm", "rule")
    _git(repo, "push", "-q", "origin", "main")
    _adapter(repo, f"{PY} -B rule.py")
    first, = surveyed_in("preserve", spec("orders-split", "all"))["specs"]
    Worker.replies = [characterize("tests/test_orders.py")]
    started(client(), first)
    card = until(lambda: (c := specs.load("proj", first))["fault"] and c)
    assert said in card["fault"], card["fault"]
    assert not card.get("refactor_frozen")


def frozen_at_pr() -> tuple[str, Path]:
    first, = surveyed_in("preserve", spec("orders-split", "all"))["specs"]
    Worker.replies = [characterize("tests/test_orders.py"),
                      edited("src/orders.py", "def place(order):\n    result = order\n    return result\n")]
    started(client(), first)
    card = landed(first)
    assert card["state"] == "PR #7", card["fault"]
    return first, Path(card["worktree"])


def commented(remote):
    """`remote` with `gh pr comment` answered; the comments it was given."""
    said = []

    def sh(args, cwd, timeout=60):
        if args[:3] == ["gh", "pr", "comment"]:
            said.append(Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(args, 0, "", "")
        return remote(args, cwd, timeout)
    return sh, said


def approve(first: str):
    api = client()
    change = api.get(f"/api/refactors/specs/{first}/frozen").json()
    return change, api.post(f"/api/refactors/specs/{first}/refreeze", json={k: change[k] for k in ("head", "digest")})


def changed(path: Path, test: str, code: str | None = None) -> None:
    (path / "tests/test_orders.py").write_text(f"from src.orders import place\n{test}\n", encoding="utf-8")
    if code:
        (path / "src/orders.py").write_text(code, encoding="utf-8")
    _git(path, "commit", "-qam", "change")


def test_a_person_refreezes_a_changed_characterization(running):
    """ai-companion-voice-agent preserve-repository-boundaries: a frozen test needed a settings marker, and
    neither the frozen tests nor the repository's rule test could change. The person approves the diff."""
    _, remote = running
    first, path = frozen_at_pr()
    changed(path, "assert place(1) == 1  # marked")
    specs.update("proj", first, state="멈춤")
    branch = _git(path, "branch", "--show-current")
    sh, said = commented(remote)
    with patch.object(specs, "sh", sh):
        change = client().get(f"/api/refactors/specs/{first}/frozen").json()
        assert change["files"] == ["tests/test_orders.py"] and "# marked" in change["diff"]
        stale = client().post(f"/api/refactors/specs/{first}/refreeze", json={"head": change["head"], "digest": "0" * 64})
        assert stale.status_code == 409 and "다시 열어" in stale.json()["detail"]
        change, done = approve(first)
    assert done.status_code == 200, done.json()
    card = specs.load("proj", first)
    frozen = card["refactor_frozen"]
    assert frozen["head"] == change["head"] and frozen["code"] == change["from"] and not card.get("refreezing")
    assert json.loads(Path(frozen["file"]).read_text(encoding="utf-8"))["head"] == change["head"]
    assert _git(path, "branch", "--show-current") == branch and not specs.dirty(path)
    assert said and "다시 고정했다" in said[0] and "tests/test_orders.py" in said[0]
    assert client().get(f"/api/refactors/specs/{first}/frozen").json()["files"] == []


@pytest.mark.parametrize("pythonpath", [False, True])
def test_a_changed_characterization_that_fails_on_the_old_code_is_not_refrozen(running, monkeypatch, pythonpath):
    """#247 round 1: in a linked worktree the old-code run imported the active checkout through
    `PYTHONPATH`; run in the card's own worktree, `PYTHONPATH` sees the old code too."""
    repo, remote = running
    if pythonpath:
        monkeypatch.setenv("PYTHONPATH", str(repo))
    first, path = frozen_at_pr()
    changed(path, "assert place(1) == 2", "def place(order):\n    return order + 1\n")
    specs.update("proj", first, state="멈춤")
    was = specs.load("proj", first)["refactor_frozen"]["head"]
    with patch.object(specs, "sh", commented(remote)[0]):
        _, refused = approve(first)
    assert refused.status_code == 409 and "고정 전 코드에서 실패한다" in refused.json()["detail"]
    assert specs.load("proj", first)["refactor_frozen"]["head"] == was
    assert (path / "src/orders.py").read_text(encoding="utf-8").endswith("order + 1\n"), "the branch came back"


def test_a_refused_switch_keeps_an_edit_made_meanwhile(running):
    """#247 round 2 (P0): the switch to the old code refused an edit that arrived after the clean check,
    and the forced restore then erased it."""
    _, remote = running
    first, path = frozen_at_pr()
    changed(path, "assert place(1) == 1  # marked")
    specs.update("proj", first, state="멈춤")
    edit = "def place(order):\n    return 'new local work'\n"
    base, _ = commented(remote)

    def sh(args, cwd, timeout=60):
        if args[:4] == ["git", "checkout", "-q", "--detach"]:
            (path / "src/orders.py").write_text(edit, encoding="utf-8")
        return base(args, cwd, timeout)
    with patch.object(specs, "sh", sh):
        _, refused = approve(first)
    assert refused.status_code == 409 and "바꾸지 못했다" in refused.json()["detail"]
    assert (path / "src/orders.py").read_text(encoding="utf-8") == edit
    assert not specs.load("proj", first).get("refreezing")


@pytest.mark.parametrize("placeholder", [False, True])
def test_build_output_refuses_the_old_code_run(running, placeholder):
    """#247 round 2: an ignored `dist` holding the refactored code compiled ran instead of the old source.
    Round 3: moving it aside lost files, and a tracked `.gitkeep` made git list `dist`'s files, not `dist`."""
    _, remote = running
    first, path = frozen_at_pr()
    with open(path / ".git/info/exclude", "a", encoding="utf-8") as exclude:
        exclude.write("packages/lib/dist/*\n!packages/lib/dist/.gitkeep\n" if placeholder else "dist/\n")
    built = path / ("packages/lib/dist" if placeholder else "dist")
    built.mkdir(parents=True)
    if placeholder:
        (built / ".gitkeep").write_text("", encoding="utf-8")
        _git(path, "add", str(built / ".gitkeep"))
    (built / "built.txt").write_text("refactored", encoding="utf-8")
    changed(path, "assert place(1) == 1  # marked")
    specs.update("proj", first, state="멈춤")
    was = specs.load("proj", first)["refactor_frozen"]["head"]
    with patch.object(specs, "sh", commented(remote)[0]):
        _, refused = approve(first)
    assert refused.status_code == 409 and "dist" in refused.json()["detail"], refused.json()
    assert (built / "built.txt").read_text(encoding="utf-8") == "refactored"
    card = specs.load("proj", first)
    assert card["refactor_frozen"]["head"] == was and not card.get("refreezing")


def test_an_interrupted_refreeze_is_put_back_when_the_change_is_opened(running):
    """#247 round 2: a restore that failed left `refreezing`, and nothing ever cleared it."""
    _, remote = running
    first, path = frozen_at_pr()
    branch = _git(path, "branch", "--show-current")
    changed(path, "assert place(1) == 1  # marked")
    specs.update("proj", first, state="멈춤")
    base, _ = commented(remote)

    def sh(args, cwd, timeout=60):
        if args == ["git", "checkout", "-q", branch]:
            return subprocess.CompletedProcess(args, 1, "", "injected")
        return base(args, cwd, timeout)
    with patch.object(specs, "sh", sh):
        _, failed = approve(first)
    assert failed.status_code == 500 and specs.load("proj", first)["refreezing"]
    with patch.object(specs, "sh", base):
        opened = client().get(f"/api/refactors/specs/{first}/frozen")
    assert opened.status_code == 200, opened.json()
    assert _git(path, "branch", "--show-current") == branch and not specs.load("proj", first).get("refreezing")


def test_a_second_approval_still_runs_on_the_characterization_commit(running):
    """#247 round 1: the old-code run checked out the moved freeze head, so a second approval ran on code the
    first one had let through."""
    _, remote = running
    first, path = frozen_at_pr()
    code = specs.load("proj", first)["refactor_frozen"]["head"]
    sh, _ = commented(remote)
    with patch.object(specs, "sh", sh):
        changed(path, "assert place(1) == 1  # marked", "def place(order):\n    return order + 1\n")
        specs.update("proj", first, state="멈춤")
        assert approve(first)[1].status_code == 200
        changed(path, "assert place(1) == 2")
        specs.update("proj", first, state="멈춤")
        _, refused = approve(first)
    assert refused.status_code == 409 and "고정 전 코드에서 실패한다" in refused.json()["detail"]
    assert specs.load("proj", first)["refactor_frozen"]["code"] == code


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
    (None, ("tests/test_guard.py::test_other",), "[테스트 파일 삭제 승인]"),
    (None, ("tests/test_guard.py",), ""),
    # Every test named is still not the file: what pytest collects from it is not settled by syntax (#252).
    (None, ("tests/test_guard.py::test_guard", "tests/test_guard.py::test_other"), "파일 단위로 선언한다"),
    (OLD_TESTS + "\n\ndef test_new():\n    assert True\n", ("tests/test_guard.py::test_gone",), "시작 커밋에 없다"),
])
def test_a_test_contract_frees_only_what_it_names(surveyed, new, declared, why):
    found = contracted(surveyed, new, *declared)
    assert (why in found) if why else found == "", found


# emotion-state-cleanup-97d92512: the survey named the one test, whose helper read a renamed attribute.
HELPED = ("import pytest\n\n\ndef _entries(engine):\n    return engine._persistent\n\n\n"
          "@pytest.mark.parametrize('n', [1, 2])\ndef test_parity(n):\n    assert _entries(n)\n")


@pytest.mark.parametrize("after", ["", "talked", "revised", "committed", "stopped", "late", "switched"])
def test_a_person_declares_a_deleted_test_file_removed_whole(running, after):
    """Keeping the file changed an undeclared helper, deleting it was refused, and the worker cannot declare
    a file: the person reads what it held and declares it, and the card goes on by itself on the report it
    kept. `talked`: a person's turn after the refusal leaves the report standing. `revised`: the refused
    answer revised the spec too, and the replay does not revise it again (#253 round 1). `committed`: a later
    commit outdates the report, and `stopped`: a stop that came as the answer ended voids it, so the worker
    is asked for one. `late`: a stop sent once the turn's record is written keeps the turn from going on by
    itself but not its recorded answer, so the report stands (#253 rounds 3–5). `switched`: the shared checkout moved to another branch between the approval and the resume,
    so nothing is sent until this card's branch is back (#253 round 2)."""
    repo, remote = running
    (repo / "tests").mkdir()
    (repo / "tests/test_parity.py").write_text(HELPED, encoding="utf-8")
    _git(repo, "add", "tests")
    _git(repo, "commit", "-qm", "parity test")
    _git(repo, "push", "-q", "origin", "main")
    tests = {"add": [], "contract": [], "remove": [{"test": "tests/test_parity.py::test_parity", "reason": "renamed"}]}
    sid, = surveyed_in("simplify", spec("parity-names", "billing", files=["src/billing.py"], tests=tests))["specs"]

    def dropped(path, halt):
        _git(path, "rm", "-q", "tests/test_parity.py")
        _git(path, "commit", "-qm", "drop the parity test")
        if after == "stopped":
            halt.set()   # the person's stop, landing as the answer ends
        if after != "revised":
            return report(True, True)
        card = specs.owner(path)
        change = {**{k: card[k] for k in ("rev", "goal", "out", "done")}, "reason": "Clarify before the report"}
        return "```spec-update\n" + json.dumps(change) + "\n```\n\n" + report(True, True)
    Worker.replies = [dropped]
    kept, late = work.remember, []

    def remembered(where, role, text, **kw):   # `late`: the person's stop, sent once the record is written
        kept(where, role, text, **kw)
        if after == "late" and role == "assistant" and "```done-report" in text:
            stopped = client().post("/api/work/stop", json={"path": str(where), "turn": kw["turn"]})
            late.append((stopped.status_code, work._runs[str(where)].halt.is_set()))
    with patch.object(work, "remember", remembered):
        started(client(), sid)
    # Checked out here: the record's own failure is caught where it is written.
    assert late == ([(200, True)] if after == "late" else []), late
    card = specs.load("proj", sid)
    assert "[테스트 파일 삭제 승인]" in card["fault"] and not remote.created()
    assert card["rev"] == 1 + (after == "revised")
    assert card["report_at"]["rev"] == card["rev"]
    stamped = next(r for r in work.recall(Path(card["worktree"])) if r.get("turn") == card["report_at"]["turn"])
    assert stamped["role"] == "assistant" and stamped["cancelled"] == (after == "stopped")
    api = client()
    path, branch = Path(card["worktree"]), card["branch"]
    if after == "talked":
        parse(api.post("/api/work/say", json={"path": str(path), "text": "왜 막혔나"}).text)
    if after == "committed":
        _git(path, "commit", "--allow-empty", "-qm", "later")
    if after in ("committed", "stopped"):
        Worker.replies = [report(True, True)]
    seen = api.get(f"/api/refactors/specs/{sid}/removals").json()
    assert [(f["file"], f["named"], f["text"]) for f in seen["files"]] == [("tests/test_parity.py", ["test_parity"], HELPED)]
    stale = api.post(f"/api/refactors/specs/{sid}/removals", json={"head": seen["head"], "digest": "0" * 64})
    assert stale.status_code == 409 and "다시 열어" in stale.json()["detail"]
    # #252 round 3: another card's branch in the shared checkout, a file put back, a start text unread.
    _git(path, "checkout", "-q", "-b", "another-card")
    assert "이 카드의 브랜치" in api.get(f"/api/refactors/specs/{sid}/removals").json()["detail"]
    _git(path, "checkout", "-q", branch)
    (path / "tests").mkdir(exist_ok=True)
    (path / "tests/test_parity.py").write_text(HELPED, encoding="utf-8")
    back = api.post(f"/api/refactors/specs/{sid}/removals", json={k: seen[k] for k in ("head", "digest")})
    assert back.status_code == 409 and "커밋하지 않은 변경" in back.json()["detail"]
    (path / "tests/test_parity.py").unlink()
    real = subprocess.run

    def unreadable(args, **kw):
        if args[:2] == ["git", "show"]:
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: unable to read blob")
        return real(args, **kw)
    with patch.object(refactor_run.subprocess, "run", unreadable):
        assert "읽지 못했다" in api.get(f"/api/refactors/specs/{sid}/removals").json()["detail"]
    approve = refactor_run.approve_removal

    def approved(*args):   # `switched`: another branch is checked out once the approval lets the worktree go
        out = approve(*args)
        if after == "switched":
            _git(path, "checkout", "-q", "-b", "another-task")
        return out
    with patch.object(refactor_run, "approve_removal", approved):
        done = api.post(f"/api/refactors/specs/{sid}/removals", json={k: seen[k] for k in ("head", "digest")})
    assert done.status_code == 200, done.json()
    card = done.json()
    remove = card["refactor"]["detail"]["tests"]["remove"]
    assert remove[-1]["test"] == "tests/test_parity.py" and remove[-1]["approved"]["head"] == seen["head"]
    assert card["fault"] is None
    if after == "switched":
        assert len([h for w in Worker.made for h in w.heard]) == 1 and not specs.resumed(card), "nothing sent"
        _git(path, "checkout", "-q", branch)
        assert specs.resumed(card)
    assert refactor_run.contract(Path(card["worktree"]), card) == ""
    # A report that still stands is checked again with no model turn; an outdated one is asked for again.
    card = landed(sid)
    assert card["state"] == "PR #7" and card["rev"] == 1 + (after == "revised"), card["fault"]
    heard = [h for w in Worker.made for h in w.heard]
    assert heard.count(specs.REPORT_APPROVED) == (after in ("committed", "stopped")), heard
    assert len(heard) == 2 + bool(after in ("talked", "committed", "stopped")), heard
    assert api.get(f"/api/refactors/specs/{sid}/removals").json()["files"] == []


# semantic-index-cleanup-97d92512: the change dropped `generation` from a module-level fake's signatures.
FAKED = ("class _Repository:\n    def list_bindings(self, task, *, generation):\n        return []\n\n"
         "    def record_deleted(self, task, *, generation, mem0_record_ids):\n        return None\n\n\n"
         "LIMIT = 1\n\n\ndef test_cleanup():\n    assert _Repository()\n")
UNFAKED = FAKED.replace(", *, generation)", ")").replace("*, generation, ", "*, ")


@pytest.mark.parametrize("new, declared, why", [
    (UNFAKED, (), "[테스트 계약 변경 승인]"),
    (UNFAKED, ("tests/test_guard.py::_Repository::list_bindings",
               "tests/test_guard.py::_Repository::record_deleted"), ""),
    (UNFAKED, ("tests/test_guard.py::_Repository::list_bindings",), "[테스트 계약 변경 승인]"),
    # A changed statement no name holds explains nothing a person could declare by name.
    (FAKED.replace("LIMIT = 1", "LIMIT = 2"), (), "기존 테스트 변경 (명세"),
])
def test_a_changed_test_double_is_offered_to_the_person_by_name(surveyed, new, declared, why):
    found = contracted(surveyed, new, *declared, old=FAKED)
    assert (why in found) if why else found == "", found


def test_a_person_declares_the_changed_definitions_in_the_test_contract(running):
    """A spec-update cannot reach the contract: the person reads the diff and declares the changed definitions
    by name, and the card goes on by itself on the report it kept. Another edit in the file is still refused."""
    repo, remote = running
    (repo / "tests").mkdir()
    (repo / "tests/test_cleanup.py").write_text(FAKED, encoding="utf-8")
    _git(repo, "add", "tests")
    _git(repo, "commit", "-qm", "cleanup test")
    _git(repo, "push", "-q", "origin", "main")
    sid, = surveyed_in("simplify", spec("cleanup-names", "billing", files=["src/billing.py"]))["specs"]
    Worker.replies = [edited("tests/test_cleanup.py", UNFAKED)]
    started(client(), sid)
    card = specs.load("proj", sid)
    assert "[테스트 계약 변경 승인]" in card["fault"] and not remote.created()
    api = client()
    seen = api.get(f"/api/refactors/specs/{sid}/amendments").json()
    names = ["_Repository::list_bindings", "_Repository::record_deleted"]
    assert [(f["file"], f["names"]) for f in seen["files"]] == [("tests/test_cleanup.py", names)]
    assert "-    def list_bindings(self, task, *, generation):" in seen["files"][0]["diff"]
    stale = api.post(f"/api/refactors/specs/{sid}/amendments", json={"head": seen["head"], "digest": "0" * 64})
    assert stale.status_code == 409 and "다시 열어" in stale.json()["detail"]
    # #255 rounds 1–2: the person reads Git's own patch as text, whatever diff or attributes the repository sets,
    # and a patch that cannot be shown approves nothing.
    attributes = Path(card["worktree"]).parent / "review-attributes"
    attributes.write_text("tests/test_cleanup.py -diff\n", encoding="utf-8")
    _git(Path(card["worktree"]), "config", "diff.external", "missing-review-diff-executable")
    _git(Path(card["worktree"]), "config", "core.attributesFile", attributes.as_posix())
    assert api.get(f"/api/refactors/specs/{sid}/amendments").json() == seen
    shell = specs.sh

    def blind(args, *rest, **kw):
        if args[:5] == ["git", "-c", "core.quotepath=off", "diff", "--text"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: diff failed")
        return shell(args, *rest, **kw)
    with patch.object(specs, "sh", blind):
        assert "보이지 못했다" in api.get(f"/api/refactors/specs/{sid}/amendments").json()["detail"]
        refused = api.post(f"/api/refactors/specs/{sid}/amendments", json={k: seen[k] for k in ("head", "digest")})
    assert refused.status_code == 409 and "보이지 못했다" in refused.json()["detail"]
    done = api.post(f"/api/refactors/specs/{sid}/amendments", json={k: seen[k] for k in ("head", "digest")})
    assert done.status_code == 200, done.json()
    card = done.json()
    declared = card["refactor"]["detail"]["tests"]["contract"]
    assert [c["test"] for c in declared] == [f"tests/test_cleanup.py::{n}" for n in names]
    assert all(c["approved"]["head"] == seen["head"] for c in declared) and card["fault"] is None
    card = landed(sid)
    assert card["state"] == "PR #7", card["fault"]
    path = Path(card["worktree"])
    (path / "tests/test_cleanup.py").write_text(UNFAKED.replace("LIMIT = 1", "LIMIT = 2"), encoding="utf-8")
    _git(path, "commit", "-qam", "more")
    assert "기존 테스트 변경" in refactor_run.contract(path, specs.load("proj", sid))


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
    card = landed(run["specs"][0])
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
    run = surveyed_in("preserve", spec("orders-split", "all"))
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
    card = until(lambda: (c := specs.load("proj", run["specs"][0]))["state"].startswith("PR #") and c)   # the first fault stays
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
    run = surveyed_in("preserve", spec("orders-split", "all"))
    sid = run["specs"][0]
    source = {"focus": "refactor", "turn": 2, "session": "talk", "refactor_mode": "preserve",
              "refactor_generation": query.config("refactor", "proj").get("refactor_generation", 0)}
    narrowed = {**spec("ignored", "all", title="Split the intake out of place"), "id": sid}
    split = {**spec("orders-check", "all", title="Move the order check beside place", depends=[sid]), "split_from": sid}
    shown = specs.answered(surveyed, [{"name": "spec", "value": [narrowed, split]}], source)
    assert [s.get("id") for s in shown] == [sid, f"orders-check-{run['id']}"], shown
    card = specs.load("proj", sid)
    assert card["rev"] == 2 and card["refactor"]["detail"]["key"] == "orders-split"
    assert card["refactor"]["detail"]["title"] == "Split the intake out of place"
    assert specs.load("proj", shown[1]["id"])["refactor"]["depends"] == [sid]
    for block, why in (({**spec("free")}, "조사 없이"), ({**spec("x", files=["src/missing.py"]), "id": sid}, "없다"),
                       ({**spec("x"), "id": sid}, "모드"), ({**spec("x", "all", depends=[shown[1]["id"]]), "id": sid}, "돌아온다"),
                       ({**spec("x"), "id": sid}, "저장소 전체 명세")):
        given = {**source, "refactor_mode": "simplify"} if why == "모드" else source
        assert why in specs.answered(surveyed, [{"name": "spec", "value": block}], given)[0]["error"]
    other = specs.load("proj", shown[1]["id"])
    specs.save({**other, "refactor": {**other["refactor"], "survey": "recheck-two"}})   # what a recheck gives it
    looped = {**spec("x", "all", depends=[other["id"]]), "id": sid}
    assert "돌아온다" in specs.answered(surveyed, [{"name": "spec", "value": looped}], source)[0]["error"]
    assert specs.load("proj", sid)["rev"] == 2


def test_a_card_is_refined_only_on_its_own_survey_model(surveyed):
    run = surveyed_in("preserve", spec("orders-split", "all"))
    sid = run["specs"][0]
    card = specs.load("proj", sid)
    api = client()
    later = {**ROLES, "survey": {"model": "haiku", "effort": "low"}}
    second = settled(api, start(api, "survey-later-0001", "preserve", roles=later).json()["id"])
    assert second["state"] == "stopped" and specs.load("proj", sid) is None, "the later start deleted it"
    specs.save(card)   # one left from before a start deleted the earlier survey's unstarted cards
    assert query.config("refactor", "proj")["model"] == "haiku", "the conversation now runs on the later survey"
    source = {"focus": "refactor", "turn": 3, "session": "talk", "refactor_mode": "preserve",
              "refactor_generation": query.config("refactor", "proj").get("refactor_generation", 0)}
    for block in ({**spec("x"), "id": sid}, {**spec("orders-check"), "split_from": sid}):
        assert "claude-test" in specs.answered(surveyed, [{"name": "spec", "value": block}], source)[0]["error"]
    assert specs.load("proj", sid)["rev"] == 1


def test_saving_the_pinned_settings_again_keeps_an_empty_effort(surveyed):
    from main import channels
    run = surveyed_in("preserve", spec("orders-split", "all"))
    sid, codex = run["specs"][0], {"model": "codex:test-model", "effort": ""}
    card = specs.load("proj", sid)
    specs.save({**card, "refactor": {**card["refactor"], "roles": {**ROLES, "survey": codex}}})
    query.config("refactor", "proj").update(codex)   # what a Codex survey with no effort picked pins
    models = [{"id": "codex:test-model", "default_effort": "high", "efforts": [{"id": e} for e in ("", "high")]}]
    with patch.object(channels, "codex_models", return_value=models):
        saved = client().post("/api/config/refactor", json={"repo": "proj", **codex, "refactor_mode": "preserve"})
    assert saved.status_code == 200 and saved.json()["effort"] == "", "the default effort does not replace the pin's"
    source = {"focus": "refactor", "turn": 3, "session": "talk", "refactor_mode": "preserve",
              "refactor_generation": query.config("refactor", "proj").get("refactor_generation", 0)}
    assert specs.answered(surveyed, [{"name": "spec", "value": {**spec("x", "all"), "id": sid}}], source)[0].get("id") == sid


def test_recheck_rewrites_a_stale_card_or_sets_it_aside(surveyed):
    run = surveyed_in("preserve", spec("orders-split", "all"))
    sid = run["specs"][0]
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    head, api = _git(surveyed, "rev-parse", "HEAD"), client()
    Host.answers = {"survey": {**ONE, "specs": [spec("orders-split", "all", title="Split place again")]}}
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


def test_a_recheck_of_the_whole_repository_spec_reads_every_pipeline_report(surveyed):
    whole = spec("tidy-all", "all", files=["src/orders.py", "src/billing.py"])
    sid = surveyed_in("preserve", whole)["specs"][0]
    (surveyed / "src/billing.py").write_text("def charge(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    reports = list(refactor.load("proj", specs.load("proj", sid)["refactor"]["survey"])["pipelines"].values())
    api, body = client(), {"request_id": "recheck-all-0001", "mode": "preserve"}
    Host.answers = {"survey": {**ONE, "specs": [spec("tidy-all")]}}   # narrowed to one pipeline
    narrowed = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck", json=body).json()["id"])
    assert narrowed["state"] == "stopped" and "`all` 여야 한다" in narrowed["stopped"]["detail"], narrowed
    Host.answers = {"survey": {**ONE, "specs": [whole]}}
    again = settled(api, api.post(f"/api/refactors/specs/{sid}/recheck",
                                  json={**body, "request_id": "recheck-all-0002"}).json()["id"])
    assert again["state"] == "done", again["stopped"]
    assert set(again["pipelines"]) == {"orders", "billing"}
    # Review round 1: the map names both pipelines too, so read the report section itself.
    asked = Host.made[-1].text
    sent = asked.split("The pipeline report from the older survey:\n", 1)[1].split("\n\nThe spec:\n", 1)[0]
    assert json.loads(sent) == reports, "every report goes to the recheck"


def test_a_recheck_keeps_a_card_that_depends_on_another_spec_of_its_survey(surveyed):
    run = surveyed_in("simplify", spec("shared-check", "shared:src/shared.py", files=["src/shared.py"]),
                      spec("orders-split", depends=["shared-check"]))
    sid = run["specs"][1]
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    Host.answers = {"survey": {**ONE, "specs": [spec("orders-split", depends=["shared-check"])]}}
    body = {"request_id": "recheck-req-0003", "mode": "simplify"}
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
