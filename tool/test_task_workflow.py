"""Task branches, persistent diffs and editable requirements without repository copies."""

import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import ChatSession
from main import loop, specs, survey, work
from test_main import client, no_machine_settings, settled  # noqa: F401
from test_specs import PASS, Remote, Worker, made, repo as repository_fixture, spec_block, started
from workspace import create, worktrees

repo = repository_fixture


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def test_tasks_use_original_repo_and_keep_diff_and_conversation_across_switches(repo):
    web = client()
    Worker.replies, Worker.made = ["First task"], []
    with patch.object(work, "ChatSession", Worker):
        first = made(repo, spec_block(slug="first"))[0]["id"]
        path = started(web, first)
        assert Path(path) == repo and git(repo, "branch", "--show-current") == first
        assert len(git(repo, "worktree", "list", "--porcelain").split("worktree ")) == 2
        assert not (repo.parent / "proj-worktrees").exists()
        (repo / "a.txt").write_text("edited\n", encoding="utf-8")
        git(repo, "commit", "-qam", "edit")
        work._runs.clear()  # The diff remains after restarting the server.
        diff = web.get("/api/work/diff", params={"path": path}).json()
        assert "-a" in diff["diff"] and "+edited" in diff["diff"]
        Worker.replies = ["Second task"]
        second = made(repo, spec_block(slug="second"))[0]["id"]
        assert started(web, second) == path
        assert specs.owner(repo)["id"] == second
        assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "main")
        assert (repo / "a.txt").read_text(encoding="utf-8") == "a\n"
        assert not web.get("/api/work/diff", params={"path": path}).json()["diff"]
        assert len(worktrees(repo)) == 1
        assert web.post(f"/api/specs/{first}/checkout").status_code == 200
        assert specs.owner(repo)["id"] == first
        assert work.recall(repo)[-1]["text"] == "First task"
        assert "+edited" in web.get("/api/work/diff", params={"path": path}).json()["diff"]
        (repo / "precious.txt").write_text("keep this\n", encoding="utf-8")
        assert web.post(f"/api/specs/{second}/checkout").status_code == 409
        assert (repo / "precious.txt").read_text(encoding="utf-8") == "keep this\n"
        assert web.post("/api/worktrees/remove", json={"path": path, "force": True}).status_code == 409
        assert (repo / ".git").exists()
    work.close_all()


def test_active_spec_revision_records_old_requirements_and_refreshes_agent(repo):
    web = client()
    Worker.replies, Worker.made = ["Working"], []
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        old_chat = work._sessions[str(repo)]
        spec = specs.load(repo.name, sid)
        specs.update(repo.name, sid, state="머지 가능", rounds=[{"verdict": "allow", "head": "old"}],
                     validation={"final": {"ok": True}})
        saved = web.put(f"/api/specs/{sid}", json={"rev": spec["rev"], "goal": "Handle the discovered edge case",
                                                  "out": [], "done": ["Check the edge case"]})
        assert saved.status_code == 200, saved.text
        saved = saved.json()
        assert saved["rev"] == 2 and saved["state"] == "작업 중"
        assert saved["done"] == [PASS, "Check the edge case"]
        assert saved["revisions"][0]["goal"] == spec["goal"]
        assert saved["approved"] is None and saved["validation"] is None
        chat = work.session(repo, "", "")
        assert chat is not old_chat and not old_chat.alive
        assert '"rev": 2' in chat.system and "Handle the discovered edge case" in chat.system
        assert "Do not push" not in chat.system and "Pushing the task branch" in chat.system
        # After a planner hands off, revised implementation must still publish.
        specs.update(repo.name, sid, planning={"phase": "handoff"})
        Worker.replies = ["```done-report\n" + json.dumps([
            {"item": PASS, "pass": True}, {"item": "Check the edge case", "pass": True}]) + "\n```"]
        with patch.object(specs, "sh", Remote()), patch.object(specs, "korean", side_effect=lambda value: value):
            assert web.post("/api/work/say", json={"path": str(repo), "text": "Finish the revised task"}).status_code == 200
            settled(str(repo))
        assert specs.load(repo.name, sid)["pr"]["number"] == 7
    work.close_all()


def test_agent_spec_update_and_completion_open_or_reuse_pr(repo):
    web, remote = client(), Remote()
    change = {"rev": 1, "reason": "The discovered edge case needs a different check",
              "goal": "Handle an edge case", "out": [], "done": ["Edge case passes"]}
    report = [{"item": PASS, "pass": True}, {"item": "Edge case passes", "pass": True}]
    Worker.replies = ["```spec-update\n" + json.dumps(change) + "\n```\n\n```done-report\n"
                      + json.dumps(report) + "\n```"]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote), \
         patch.object(specs, "korean", side_effect=lambda spec: spec):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        saved = specs.load(repo.name, sid)
        assert saved["rev"] == 2 and saved["pr"]["number"] == 7
        assert remote.pushes() == 1 and remote.created()
        assert "Handle an edge case" in remote.body
        run = work._runs[str(repo)]
        with patch.object(specs, "existing_pr", return_value=(7, "https://github.com/o/proj/pull/7")):
            remote.calls.clear()
            specs.opened(repo, repo, run, saved)
            assert remote.pushes() == 1 and not remote.created()
            edit = next(call for call in remote.calls if call[:3] == ["gh", "pr", "edit"])
            assert edit[edit.index("--title") + 1] == "Handle an edge case"
            assert "Handle an edge case" in remote.body and "Edge case passes" in remote.body
    work.close_all()


def test_saved_permission_switch_cannot_downgrade_agent_and_fix_prompts_allow_push(repo):
    loop.store(bypass=False)
    assert work.settings()["bypass"]
    assert client().post("/api/work/settings", json={"bypass": False}).json()["bypass"]
    assert "do not push" not in loop.fixing(1, [], "refuse").lower()
    assert "do not push" not in loop.repair(PASS, {"reason": "failure", "tail": "failed"}).lower()
    assert "push the task branch" in loop.repair(PASS, {"reason": "failure", "tail": "failed"}).lower()
    assert "do not push" not in survey.LEAD.lower()


def test_full_access_answers_execution_permissions_but_preserves_product_questions(repo):
    chat = ChatSession(repo, model="codex:m", write=True, bypass=True)
    with patch.object(chat, "_send") as sent:
        permissions = {"network": {"enabled": True}}
        chat._codex_asks(1, "item/permissions/requestApproval", {"permissions": permissions})
        assert sent.call_args.args[0] == {"id": 1, "result": {"permissions": permissions, "scope": "session"}}
        reply = chat._codex_asks(2, "item/commandExecution/requestApproval",
                                 {"command": "git push", "cwd": str(repo.parent)})
        assert reply.meta["answer"] == "allow" and not chat._pending
        assert sent.call_args.args[0] == {"id": 2, "result": {"decision": "accept"}}
        question = chat._codex_asks(3, "item/tool/requestUserInput",
                                   {"questions": [{"id": "choice", "question": "Which output?"}]})
        assert question.kind == "approval" and "answer" not in question.meta
        assert "3" in chat._pending


def test_branch_switches_wait_for_review_and_survey_but_restart_releases_survey(repo):
    web = client()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker):
        first = made(repo, spec_block(slug="first"))[0]["id"]
        started(web, first)
        second = made(repo, spec_block(slug="second"))[0]["id"]
        specs.update(repo.name, first, state="리뷰 대기")
        assert web.post(f"/api/specs/{second}/start", json={}).status_code == 409
        assert git(repo, "branch", "--show-current") == first
        specs.update(repo.name, first, state="작업 중", survey={"running": True})
        assert web.post(f"/api/specs/{second}/start", json={}).status_code == 409
        with patch.object(survey, "progress"):
            survey.recover()
        assert not specs.load(repo.name, first)["survey"]["running"]
        Worker.replies = ["Second task"]
        assert started(web, second) == str(repo)
    work.close_all()


def test_new_task_branch_preserves_dirty_checkout_and_works_in_linked_checkout(repo, tmp_path):
    (repo / "uncommitted.txt").write_text("keep", encoding="utf-8")
    try:
        create(repo, "must-wait")
    except ValueError:
        pass
    else:
        raise AssertionError("Dirty checkout was switched")
    assert git(repo, "branch", "--show-current") == "main"
    assert (repo / "uncommitted.txt").read_text(encoding="utf-8") == "keep"
    # An externally selected checkout is already a worktree; no nested copy is needed.
    path = tmp_path / "selected-checkout"
    git(repo, "worktree", "add", "-qb", "selected", str(path))
    assert create(path, "task") == path.resolve()
    assert git(path, "branch", "--show-current") == "task"


@pytest.mark.parametrize("stale_remote", [False, True])
def test_task_diff_excludes_base_updates_merged_after_task_start(repo, stale_remote):
    web = client()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
    if stale_remote:
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "task.txt").write_text("agent change\n", encoding="utf-8")
    git(repo, "add", "task.txt")
    git(repo, "commit", "-qm", "task change")
    git(repo, "switch", "main")
    (repo / "upstream.txt").write_text("upstream change\n", encoding="utf-8")
    git(repo, "add", "upstream.txt")
    git(repo, "commit", "-qm", "upstream change")
    upstream = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", sid)
    git(repo, "merge", "--no-edit", "main")
    work._runs.clear()
    data = work.changes(str(repo))
    assert "+agent change" in data["diff"] and "upstream.txt" not in data["diff"]
    assert data["base"] == upstream


def test_shared_checkout_after_merge_updates_base_for_the_next_task(repo, tmp_path):
    origin = tmp_path / "origin.git"
    git(repo, "init", "--bare", "-q", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-qu", "origin", "main")
    web = client()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        (repo / "merged.txt").write_text("merged task\n", encoding="utf-8")
        git(repo, "add", "merged.txt")
        git(repo, "commit", "-qm", "task change")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "push", "-q", "origin", "HEAD:main")
        saved = specs.update(repo.name, sid, pr={"number": 7, "head": head, "branch": sid, "base": "main"})
        with patch.object(loop, "pruned", return_value="Remote task branch retained for fixture"), \
             patch.object(loop, "close_cell"):
            loop.finish(repo, saved, "main", head, "Merged fixture task")
        assert git(repo, "rev-parse", "main") == head
        assert git(repo, "rev-parse", sid) == head  # Never delete the local task branch.
        second = made(repo, spec_block(slug="second"))[0]["id"]
        assert started(web, second) == str(repo)
        assert git(repo, "rev-parse", "HEAD") == head and (repo / "merged.txt").exists()


@pytest.mark.parametrize("blocked", ["dirty", "busy", "unrelated"])
def test_merge_return_does_not_interrupt_user_work(repo, blocked):
    git(repo, "switch", "-c", "task")
    if blocked == "dirty":
        (repo / "mine.txt").write_text("keep this\n", encoding="utf-8")
    elif blocked == "busy":
        work._busy[str(repo)] = object()
    else:
        git(repo, "switch", "-c", "other")
    before = git(repo, "branch", "--show-current")
    with patch.object(specs, "sh", wraps=specs.sh) as calls:
        assert loop.forward(repo, "main", task_branch="task").startswith("원본이 뒤처짐")
        assert not any(call.args[0][:2] in (["git", "switch"], ["git", "merge"]) for call in calls.call_args_list)
    assert git(repo, "branch", "--show-current") == before


def test_failed_reused_pr_update_does_not_record_new_requirements_as_published(repo):
    remote = Remote()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(client(), sid)
    spec = specs.load(repo.name, sid)
    spec.update(report=[{"item": PASS, "pass": True}], gate={"cmd": PASS, "tail": "ok", "head": "fixture"})
    run = work._runs[str(repo)]
    run.halt = threading.Event()
    real = remote.__call__

    def refused(args, cwd, timeout=60):
        if args[:3] == ["gh", "pr", "edit"]:
            return subprocess.CompletedProcess(args, 1, "", "metadata edit refused")
        return real(args, cwd, timeout)

    with patch.object(specs, "sh", side_effect=refused), \
         patch.object(specs, "existing_pr", return_value=(7, "https://github.com/o/proj/pull/7")), \
         patch.object(specs, "korean", side_effect=lambda value: value):
        assert specs.opened(repo, repo, run, spec) is None
    saved = specs.load(repo.name, sid)
    assert "metadata edit refused" in saved["fault"] and not saved.get("pr")
