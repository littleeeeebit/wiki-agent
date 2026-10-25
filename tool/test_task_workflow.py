"""Task branches, persistent diffs and editable requirements without repository copies."""

import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from agent import ChatSession
from main import loop, specs, survey, work
from test_main import client, no_machine_settings, settled  # noqa: F401
from test_specs import PASS, SWEPT, Remote, Worker, made, repo as repository_fixture, spec_block, started
from workspace import create, worktrees

repo = repository_fixture


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def test_changed_file_inventory_and_individual_patches_survive_a_large_preview(repo):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "large.txt").write_text("large line\n" * 25000, encoding="utf-8")
    (repo / "한 글.txt").write_text("new line\n", encoding="utf-8")
    (repo / "binary.bin").write_bytes(b"\x00binary")
    web = client()
    path = web.get("/api/worktrees").json()["rows"][0]["path"]
    response = web.get("/api/work/diff", params={"path": path, "preview": False})
    assert response.status_code == 200, response.text
    overview = response.json()
    assert not overview["diff"]
    files = {entry["path"]: entry for entry in overview["files"]}
    assert {"a.txt", "large.txt", "한 글.txt", "binary.bin"} <= files.keys()
    assert files["binary.bin"]["binary"] and files["large.txt"]["added"] == 25000
    detail = web.get("/api/work/diff", params={"path": path, "file": "한 글.txt"}).json()
    assert "+new line" in detail["diff"] and "large line" not in detail["diff"]
    assert detail["totals"] == overview["totals"] and not detail["truncated"]
    assert web.get("/api/work/diff", params={"path": path, "file": "../outside"}).status_code == 404
    (repo / "a.txt").unlink()
    removed = web.get("/api/work/diff", params={"path": path, "file": "a.txt"}).json()
    assert "-a" in removed["diff"]


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


def test_delete_draft_inactive_and_active_branch_tasks_persists_across_restart(repo):
    web = client()
    Worker.replies, Worker.made = ["First task", "Second task"], []
    with patch.object(work, "ChatSession", Worker):
        draft = made(repo, spec_block(slug="draft"))[0]["id"]
        stale_draft = specs.load(repo.name, draft)
        assert web.post(f"/api/specs/{draft}/delete").status_code == 200
        first = made(repo, spec_block(slug="first"))[0]["id"]
        path = started(web, first)
        stale = specs.load(repo.name, first)
        second = made(repo, spec_block(slug="second"))[0]["id"]
        started(web, second)
        current_chat = work._sessions[path]
        assert web.post(f"/api/specs/{first}/delete").status_code == 200
        with pytest.raises(HTTPException) as refused:
            specs.save(stale)
        assert refused.value.status_code == 410
        assert work._sessions[path] is current_chat and current_chat.alive
        (repo / "precious.txt").write_text("Preserve changes\n", encoding="utf-8")
        assert web.post(f"/api/specs/{second}/delete").status_code == 200
        work.close_all()
        work._runs.clear()
        assert not specs.listing(repo.name)
        assert not web.get("/api/worktrees").json()["rows"]
        assert (repo / ".git").exists() and git(repo, "branch", "--show-current") == second
        assert (repo / "precious.txt").read_text(encoding="utf-8") == "Preserve changes\n"
        assert len(list((specs.SPECS / repo.name / "dropped").glob("*.json"))) == 3
        replacement = made(repo, spec_block(slug="draft", goal="A new task with the same name"), session="new")[0]["id"]
        assert replacement == draft
        with pytest.raises(HTTPException) as refused:
            specs.save(stale_draft)
        assert refused.value.status_code == 410
        assert specs.load(repo.name, draft)["goal"] == "A new task with the same name"


def test_delete_running_task_stops_it_before_archiving(repo):
    web = client()
    running = threading.Event()

    def wait_for_stop(path, halt):
        running.set()
        assert halt.wait(120)
        return "Stopped"

    Worker.replies = [wait_for_stop]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block(slug="running"))[0]["id"]
        response = web.post(f"/api/specs/{sid}/start", json={})
        assert response.status_code == 200 and running.wait(5)
        assert web.post(f"/api/specs/{sid}/delete").status_code == 200
        assert not specs.load(repo.name, sid) and str(repo) not in work._busy
        assert not work.listing()["rows"] and repo.is_dir()


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
        Worker.replies = [SWEPT + "```done-report\n" + json.dumps([
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
    Worker.replies = ["```spec-update\n" + json.dumps(change) + "\n```\n\n" + SWEPT + "```done-report\n"
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


def test_a_running_gate_is_on_its_task_only_while_it_runs(repo):
    # The rail showed nothing while a gate ran for minutes, so a task looked stopped.
    sid = made(repo, spec_block())[0]["id"]
    with specs.gating(repo.name, sid, "round"):
        shown = specs.view(repo, specs.load(repo.name, sid))["gating"]
        assert shown["kind"] == "round" and shown["since"] <= time.time()
    assert specs.view(repo, specs.load(repo.name, sid))["gating"] is None


def test_a_running_turn_names_the_task_that_owns_its_worktree(repo):
    # Under another project the screen keyed a task's work model by its path,
    # so a model picked there never reached the task in its own project.
    web = client()
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
    with patch.dict(work._runs, {str(repo): SimpleNamespace(done=False)}):
        turns = web.get("/api/loops").json()["turns"]
    assert [t["spec"] for t in turns] == [{"repo": repo.name, "id": sid}]
    # A lookup whose Git call fails keeps the row without its spec; it never fails the whole list.
    with patch.dict(work._runs, {str(repo): SimpleNamespace(done=False)}), \
         patch.object(specs, "owner", side_effect=subprocess.TimeoutExpired(["git"], 60)):
        response = web.get("/api/loops")
    assert response.status_code == 200 and [t["spec"] for t in response.json()["turns"]] == [None]
    work.close_all()


def until(ready, seconds: float = 60):
    end = time.monotonic() + seconds
    while not (value := ready()):
        assert time.monotonic() < end, "timed out"
        time.sleep(0.05)
    return value


def test_spec_update_without_done_report_asks_for_one_then_stops_with_a_fault(repo):
    # rfg_rag #76: a turn ended with only a `spec-update`; the task sat in `작업 중`
    # with nothing running. The server now asks once, and a second miss is a fault.
    web, remote = client(), Remote()
    change = {"rev": 1, "reason": "Explicit roles", "goal": "Handle an edge case", "out": [], "done": ["Edge case passes"]}
    report = [{"item": PASS, "pass": True}, {"item": "Edge case passes", "pass": True}]
    Worker.replies = ["```spec-update\n" + json.dumps(change) + "\n```", SWEPT + "```done-report\n" + json.dumps(report) + "\n```"]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote), \
         patch.object(specs, "korean", side_effect=lambda spec: spec):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        saved = until(lambda: (s := specs.load(repo.name, sid)).get("pr") and s)
        assert saved["rev"] == 2 and saved["pr"]["number"] == 7 and not saved["report_asked"]
        assert "revision 2" in work._sessions[str(repo)].heard[-1]
        Worker.replies = ["```spec-update\n" + json.dumps({**change, "rev": 2}) + "\n```", "Still working"]
        specs.update(repo.name, sid, state="작업 중")
        settled(str(repo))
        assert web.post("/api/work/say", json={"path": str(repo), "text": "Revise"}).status_code == 200
        saved = until(lambda: (s := specs.load(repo.name, sid)).get("fault") and s)
        assert saved["rev"] == 3 and saved["state"] == "작업 중" and "다시 빠졌다" in saved["fault"]
        # Answering the request with another revision only is the second miss too, not a fresh request.
        Worker.replies = ["```spec-update\n" + json.dumps({**change, "rev": rev}) + "\n```" for rev in (3, 4, 5)]
        specs.update(repo.name, sid, fault=None)
        settled(str(repo))
        assert web.post("/api/work/say", json={"path": str(repo), "text": "Revise"}).status_code == 200
        saved = until(lambda: (s := specs.load(repo.name, sid)).get("fault") and s)
        assert saved["rev"] == 5 and not saved["report_asked"] and len(Worker.replies) == 1
    work.close_all()


def committed(text: str):
    """A worker turn that commits its work, leaves the tree clean and says `text`."""

    def reply(path, halt):
        (path / "a.txt").write_text("done\n", encoding="utf-8")
        git(path, "commit", "-qam", "work")
        return text
    return reply


def test_a_turn_without_a_report_or_a_question_asks_for_the_report_once(repo):
    # Live (ai-companion-voice-agent): a Codex worker read the pending server gate as a failed item,
    # withheld its `done-report`, and the task sat in `작업 중` with nothing running.
    web, remote = client(), Remote()
    report = [{"item": PASS, "pass": True}, {"item": "돌아오는 테스트가 있다", "pass": True}]
    Worker.replies = [committed("The server gate is pending, so I cannot emit a done-report yet."),
                      SWEPT + "```done-report\n" + json.dumps(report) + "\n```"]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "sh", remote), \
         patch.object(specs, "korean", side_effect=lambda spec: spec):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        saved = until(lambda: (s := specs.load(repo.name, sid)).get("pr") and s)
        assert saved["pr"]["number"] == 7 and not saved["report_asked"]
        assert "pending server gate is not a failed item" in work._sessions[str(repo)].heard[-1]
    work.close_all()


def test_a_second_turn_without_a_report_stops_with_the_reason_and_a_persons_turn_is_not_chased(repo):
    web = client()
    Worker.replies = [committed("Still pending."), "Still pending."]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        saved = until(lambda: (s := specs.load(repo.name, sid)).get("fault") and s)
        assert saved["state"] == "작업 중" and "다시 빠졌다" in saved["fault"] and not saved["report_asked"]
        assert not Worker.replies, "asked once, then stopped instead of waiting silently"
        # A person's own turn may end in plain talk: no report request follows it.
        Worker.replies = ["Here is what the helper does.", "unsent"]
        specs.update(repo.name, sid, fault=None)
        settled(str(repo))
        assert web.post("/api/work/say", json={"path": str(repo), "text": "What does it do?"}).status_code == 200
        settled(str(repo))
        saved = specs.load(repo.name, sid)
        assert Worker.replies == ["unsent"] and not saved["fault"] and not saved["report_asked"]
    work.close_all()


def test_a_queued_order_is_a_persons_turn(repo):
    # Review round 1: an instruction sent through the queue ran without the person mark and was chased.
    seen = []
    with patch.dict(work._queued, {str(repo): work.Order(path=str(repo), text="Explain")}), \
         patch.object(work, "ChatSession", Worker), \
         patch.object(work, "begin", side_effect=lambda path, chat, text, release, run: seen.append(run) or release()):
        work.dispatch(repo, work.Run(Worker(repo)))
    assert [r.person for r in seen] == [True]
    work.close_all()


def test_a_report_request_a_person_kept_from_going_out_is_not_counted_as_asked(repo):
    # Review round 1: a person took the worktree before the request went out; it stayed marked as asked,
    # and that person's own plain answer was then failed as the second miss.
    web = client()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        committed("")(repo, None)
        run = work.Run(Worker(repo))
        follow = specs.check(repo, run, "The gate is pending.")
        assert specs.load(repo.name, sid)["report_asked"]
        release = specs.hold(work._busy, specs._lock, str(repo), "", kind="turn")   # the person's turn
        try:
            follow()
        finally:
            release()
        saved = specs.load(repo.name, sid)
        assert not saved["report_asked"] and "보내지 못했다" in saved["fault"], "never sent, so never asked"
        # Even with a request out, a person's plain answer is not the answer to it.
        specs.update(repo.name, sid, report_asked=True, fault=None)
        assert specs.check(repo, work.Run(Worker(repo), person=True), "Here is the explanation.") is None
        saved = specs.load(repo.name, sid)
        assert saved["report_asked"] and not saved["fault"]
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
        assert question.meta["request"] == "3" and question.meta["id"] in chat._pending


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
    assert create(repo, "new-task") == repo.resolve()
    assert git(repo, "branch", "--show-current") == "new-task"
    assert (repo / "uncommitted.txt").read_text(encoding="utf-8") == "keep"
    # An externally selected checkout is already a worktree; no nested copy is needed.
    path = tmp_path / "selected-checkout"
    git(repo, "worktree", "add", "-qb", "selected", str(path))
    assert create(path, "task") == path.resolve()
    assert git(path, "branch", "--show-current") == "task"


def test_task_start_preserves_staged_unstaged_and_untracked_changes(repo):
    (repo / "a.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("unstaged\n", encoding="utf-8")
    (repo / "notes.txt").write_text("user notes\n", encoding="utf-8")
    before = git(repo, "diff"), git(repo, "diff", "--cached")
    sid = made(repo, spec_block(slug="with-edits"))[0]["id"]
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker):
        assert started(client(), sid) == str(repo)
    assert git(repo, "diff") == before[0] and git(repo, "diff", "--cached") == before[1]
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "user notes\n"


def test_conflicting_task_base_refuses_switch_without_losing_changes(repo):
    import pytest
    git(repo, "switch", "-c", "other")
    (repo / "a.txt").write_text("other committed\n", encoding="utf-8")
    git(repo, "commit", "-qam", "other commit")
    (repo / "a.txt").write_text("precious edits\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        create(repo, "conflicting-task", base="main")
    assert git(repo, "branch", "--show-current") == "other"
    assert not git(repo, "branch", "--list", "conflicting-task")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "precious edits\n"


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


@pytest.mark.parametrize("linked", [False, True])
def test_shared_checkout_after_merge_updates_base_for_the_next_task(repo, tmp_path, linked):
    origin = tmp_path / "origin.git"
    git(repo, "init", "--bare", "-q", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-qu", "origin", "main")
    original = repo
    if linked:
        repo = tmp_path / "selected" / original.name
        git(original, "worktree", "add", "-qb", "selected", str(repo))
        (repo / ".wiki").mkdir()
        (repo / ".wiki/adapter.toml").write_bytes((original / ".wiki/adapter.toml").read_bytes())
    web = client()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "current_repo", return_value=repo):
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
        if linked:
            assert git(original, "branch", "--show-current") == "main"
            assert git(original, "rev-parse", "HEAD") == head  # Synchronize the clean sibling's real main too.
            assert (original / "merged.txt").read_text(encoding="utf-8") == "merged task\n"
            local_base = git(repo, "branch", "--show-current")
            assert local_base.startswith("wiki-base/")
            assert git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") == "origin/main"
        else:
            assert git(repo, "rev-parse", "main") == head
        assert not git(repo, "branch", "--list", sid)  # Reviewed merged task refs are removed.
        second = made(repo, spec_block(slug="second"))[0]["id"]
        assert started(web, second) == str(repo)
        assert git(repo, "rev-parse", "HEAD") == head and (repo / "merged.txt").exists()
        if linked:
            (repo / "second.txt").write_text("second merged task\n", encoding="utf-8")
            git(repo, "add", "second.txt")
            git(repo, "commit", "-qm", "second task")
            next_head = git(repo, "rev-parse", "HEAD")
            git(repo, "push", "-q", "origin", "HEAD:main")
            saved = specs.update(repo.name, second, pr={"number": 8, "head": next_head, "branch": second, "base": "main"})
            with patch.object(loop, "pruned", return_value="Remote task branch retained for fixture"), \
                 patch.object(loop, "close_cell"):
                loop.finish(repo, saved, "main", next_head, "Merged second fixture task")
            assert git(repo, "branch", "--show-current") == local_base
            assert git(repo, "rev-parse", "HEAD") == next_head
            assert len(git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/wiki-base").splitlines()) == 1
            assert len(git(repo, "worktree", "list", "--porcelain").split("worktree ")) == 3
            assert git(original, "rev-parse", "main") == next_head
            assert (original / "second.txt").read_text(encoding="utf-8") == "second merged task\n"


@pytest.mark.parametrize("base_state", ["ahead", "diverged", "missing upstream", "wrong upstream"])
def test_reopened_merged_task_refuses_an_unverified_sibling_base(repo, tmp_path, base_state):
    original = repo
    git(original, "init", "--bare", "-q", str(tmp_path / "origin.git"))
    git(original, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(original, "push", "-qu", "origin", "main")
    repo = tmp_path / "selected" / original.name
    git(original, "worktree", "add", "-qb", "selected", str(repo))
    (repo / ".wiki").mkdir()
    (repo / ".wiki/adapter.toml").write_bytes((original / ".wiki/adapter.toml").read_bytes())
    web = client()
    Worker.replies = ["Working"]
    with patch.object(work, "ChatSession", Worker), patch.object(specs, "current_repo", return_value=repo):
        sid = made(repo, spec_block())[0]["id"]
        started(web, sid)
        (repo / "merged.txt").write_text("merged task\n", encoding="utf-8")
        git(repo, "add", "merged.txt")
        git(repo, "commit", "-qm", "merged task")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "push", "-q", "origin", "HEAD:main")
        if base_state == "ahead":
            git(original, "merge", "--ff-only", "origin/main")
        if base_state in {"ahead", "diverged"}:
            (original / "sibling-only.txt").write_text("unpublished user work\n", encoding="utf-8")
            git(original, "add", "sibling-only.txt")
            git(original, "commit", "-qm", "sibling work")
        elif base_state == "missing upstream":
            git(original, "branch", "--unset-upstream", "main")
        else:
            git(original, "push", "-q", "origin", "main:other")
            git(original, "branch", "--set-upstream-to=origin/other", "main")
        specs.update(repo.name, sid, state="머지됨", merge={"base": "main", "head": head})
        refs = git(repo, "show-ref", "--heads")
        second = made(repo, spec_block(slug="second"))[0]["id"]
        response = web.post(f"/api/specs/{second}/start", json={})
        assert response.status_code == 409, response.text
        assert git(repo, "show-ref", "--heads") == refs
        assert git(repo, "branch", "--show-current") == sid
        assert specs.load(repo.name, second)["state"] == "정리됨"
        assert git(original, "branch", "--show-current") == "main"
        if base_state in {"ahead", "diverged"}:
            assert (original / "sibling-only.txt").read_text(encoding="utf-8") == "unpublished user work\n"


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
