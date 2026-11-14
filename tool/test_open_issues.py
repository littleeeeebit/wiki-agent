"""Regressions for repository issues repaired together in November 2026."""

# ruff: noqa: F811 — pytest injects the imported fixtures by name

import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from agent import ChatSession
from main import maintenance, refactor_imports, refactor_run, specs
from test_agent import Scripted, tree  # noqa: F401 — fixture
from test_loop import (  # noqa: F401 — fixtures
    Worker, git, no_machine_settings, template, world as git_world,
)
from test_refactor_run import (  # noqa: F401 — fixture
    _git, client, edited, landed, report, running, spec, started, surveyed_in, until,
)
from test_refactor_survey import surveyed  # noqa: F401 — fixture
from test_specs import fresh_workers, repo  # noqa: F401 — fixtures


def test_a_closing_answer_is_not_replaced_by_a_completed_background_tasks_followup(tree):
    report_text = "Done.\n```done-report\n[]\n```"
    session = ChatSession(tree, write=True, bypass=True)
    session.session_id, session._open, session._events = "cli-1", True, Scripted([
        {"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": True},
        {"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "completed"},
        {"type": "result", "origin": {"kind": "human"}, "result": report_text},
        {"type": "result", "origin": {"kind": "task-notification"}, "result": "Notification only"},
    ])
    session.settled = lambda answer: "```done-report" in answer
    with patch.object(session, "close"):
        events = list(session._drain())
    assert [event.text for event in events if event.kind == "done"] == [report_text]


def test_hub_output_fingerprint_ignores_indexes_rebuilt_by_the_stop_hook(tmp_path):
    assert not maintenance.set_aside(tmp_path, {})
    path = tmp_path / "hub"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / ".gitignore").write_text(".wiki/*\n", encoding="utf-8")
    git(path, "add", ".gitignore")
    git(path, "commit", "-qm", "start")
    wiki = path / ".wiki"
    wiki.mkdir()
    corpus, graph = wiki / "corpus.json", wiki / "graph.json"
    corpus.write_text('{"docs": []}\n', encoding="utf-8")
    graph.write_text('{"nodes": []}\n', encoding="utf-8")
    with patch.object(maintenance.query, "ROOT", path):
        before = maintenance.outputs(path, path)
        legacy = {**before, "files": {**before["files"], ".wiki/corpus.json": "old", ".wiki/graph.json": "old"}}
        assert maintenance.comparable(legacy, path) == before
        head, branch = git(path, "rev-parse", "HEAD"), git(path, "branch", "--show-current")
        receipt = {"rev": 1, "base": "main", "pr": 1, "head": head, "source_head": head,
                   "state": "interrupted", "outputs": legacy}
        spec = {"repo": "hub", "id": branch, "rev": 1, "pr": {"number": 1, "branch": branch},
                "maintenance": receipt}
        with patch.object(maintenance.specs, "update"), patch.object(maintenance.specs, "merge_progress"), \
             patch.object(maintenance.debt, "tighten", side_effect=RuntimeError("retry reached")), \
             pytest.raises(RuntimeError, match="retry reached"):
            maintenance.prepare(path, path, spec, head, "main")
        graph.write_text('{"nodes": ["rebuilt"]}\n', encoding="utf-8")
        after = maintenance.outputs(path, path)
    assert before == after and not ({".wiki/corpus.json", ".wiki/graph.json"} & before["files"].keys())


IMPORTS_OLD = ("from app.engine import REQUEST_HEADROOM, WORKER_CONNECTIONS, build_engine\n\n\n"
               "def _ceiling():\n    return REQUEST_HEADROOM + WORKER_CONNECTIONS\n\n\n"
               "def test_pool():\n    assert build_engine\n")
IMPORTS_CLEAN = "from app.engine import build_engine\n\n\ndef test_pool():\n    assert build_engine\n"


def test_an_import_allowance_cannot_replace_a_binding_or_hide_a_wildcard():
    def parsed(source):
        return ast.parse(source).body

    assert not refactor_run._kept(parsed("import engine"), parsed("engine = object()"), set(), {"engine"})
    assert refactor_run._amendable(parsed("def test_a(): assert False"),
                                   parsed("def test_a(): assert False\nfrom helpers import *"), set()) is None
    old = parsed("def test_keep(): assert False\n\ndef test_other(): assert True")
    shadowed = parsed("def test_keep(): assert False\n\ndef test_other(): assert True\nfrom math import pi as test_keep")
    assert refactor_run._amendable(old, shadowed, set()) is None
    assert not refactor_run._kept(old, shadowed, set(), {"test_keep"})
    assert refactor_run._kept(old, shadowed, {("test_keep",)}, {"test_keep"})
    kept = refactor_imports.without(parsed("from app import removed, kept"), {"removed"})
    assert kept[0].lineno == 1 and [alias.name for alias in kept[0].names] == ["kept"]


def test_a_person_declares_exact_module_import_cleanup_with_the_changed_helper(running):
    repo, remote = running
    (repo / "tests").mkdir()
    (repo / "tests/test_pool.py").write_text(IMPORTS_OLD, encoding="utf-8")
    _git(repo, "add", "tests")
    _git(repo, "commit", "-qm", "pool tests")
    _git(repo, "push", "-q", "origin", "main")
    sid, = surveyed_in("simplify", spec("pool-imports", "billing", files=["src/billing.py"]))["specs"]
    Worker.replies = [edited("tests/test_pool.py", IMPORTS_CLEAN), *[report(True, True)] * refactor_run.REPAIRS]
    started(client(), sid)
    until(lambda: "[테스트 계약 변경 승인]" in (specs.load("proj", sid).get("fault") or ""))
    assert not remote.created()
    api = client()
    seen = api.get(f"/api/refactors/specs/{sid}/amendments").json()
    assert [(f["file"], f["names"], f["imports"]) for f in seen["files"]] == [
        ("tests/test_pool.py", ["_ceiling"], ["REQUEST_HEADROOM", "WORKER_CONNECTIONS"])]
    stale = api.post(f"/api/refactors/specs/{sid}/amendments",
                     json={"head": seen["head"], "digest": "0" * 64})
    assert stale.status_code == 409 and "다시 열어" in stale.json()["detail"]
    approved = api.post(f"/api/refactors/specs/{sid}/amendments",
                        json={key: seen[key] for key in ("head", "digest")})
    assert approved.status_code == 200, approved.json()
    card = approved.json()
    tests = card["refactor"]["detail"]["tests"]
    assert tests["imports"] == ["tests/test_pool.py::REQUEST_HEADROOM", "tests/test_pool.py::WORKER_CONNECTIONS"]
    assert tests["contract"][-1]["test"] == "tests/test_pool.py::_ceiling"
    assert refactor_run.contract(Path(card["worktree"]), card) == ""
    card = landed(sid)
    path = Path(card["worktree"])
    (path / "tests/test_pool.py").write_text(IMPORTS_CLEAN.replace("app.engine", "app.other"), encoding="utf-8")
    _git(path, "commit", "-qam", "change unrelated import")
    assert "기존 테스트 변경" in refactor_run.contract(path, specs.load("proj", sid))
