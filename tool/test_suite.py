"""Suite scope, live execution states, transcript history and completion handoff."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from agent.chat_session import Event
from main import channels, knowledge, loop, query, specs, suite, work
from test_main import client, no_machine_settings  # noqa: F401


def append(file, *rows):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_suite_reads_history_and_live_cells_only_for_the_selected_repository(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    monkeypatch.setattr(query, "_project", "project")
    monkeypatch.setattr(channels, "repo_for", lambda name: repo if name == "project" else tmp_path / name)
    monkeypatch.setattr(knowledge, "running", lambda _: [])
    monkeypatch.setattr(suite, "worktrees", lambda _: [{"path": repo}])
    monkeypatch.setattr(loop, "REVIEW", tmp_path / "review")
    monkeypatch.setattr(loop, "_review_runs", {})
    spec = {"id": "task", "repo": "project", "state": "고치는 중 R1", "worktree": str(repo),
            "pr": {"number": 1}, "workspace_mode": "branch"}
    monkeypatch.setattr(specs, "listing", lambda name: [spec] if name == "project" else [])
    append(work.LOGS / "project/task.jsonl",
           {"role": "user", "ts": 10, "text": "Run the checks"},
           {"role": "assistant", "ts": 11, "turn": "previous", "text": "Checks passed", "model": "work-model"})
    append(work.LOGS / "other/secret.jsonl", {"role": "assistant", "text": "Other project private output"})
    append(query.LOGS / "wiki.jsonl",
           {"role": "assistant", "repo": str(tmp_path / "other"), "text": "Other project private conversation"},
           {"role": "assistant", "repo": str(repo), "run_id": "query-history", "text": "Project answer", "ts": 9})
    append(loop.folder("project", 1) / "progress.jsonl",
           {"role": "assistant", "turn": "review-history", "text": "Review failed", "error": "Invalid report"})
    run = work.Run(SimpleNamespace(id="work-cell", model="work-model", path=repo))
    run.put({"kind": "approval", "text": "Permission", "meta": {"id": "a"}, "session_id": "work-cell"})
    monkeypatch.setattr(work, "_runs", {str(repo): run, str(tmp_path / "other"): work.Run(
        SimpleNamespace(id="foreign", model="foreign-model", path=tmp_path / "other"))})
    web = client()
    data = web.get("/api/suite").json()
    assert data["repo"] == "project" and len(data["rows"]) == 4
    assert data["rows"][0]["status"] == "waiting" and data["rows"][0]["cell"] == "work-cell"
    assert not any("private" in r["text"] or r["cell"] == "foreign" for r in data["rows"])
    assert {r["kind"] for r in data["rows"]} == {"work", "review", "query"}
    run.put({"kind": "answered", "text": "Allowed", "meta": {"id": "a", "allow": True, "by": "person"}, "session_id": "work-cell"})
    assert web.get("/api/suite").json()["rows"][0]["status"] == "running"
    run.done = True
    append(work.LOGS / "project/task.jsonl", {"role": "assistant", "ts": 12, "turn": run.turn, "text": "Finished"})
    ended = web.get("/api/suite").json()["rows"]
    assert sum(r["id"] == run.turn for r in ended) == 1
    assert next(r for r in ended if r["id"] == run.turn)["status"] == "completed"
    planned = {**spec, "id": "plan", "worktree": str(repo / "plan"), "pr": None, "planning": {"state": "ready"}}
    monkeypatch.setattr(specs, "listing", lambda name: [spec, planned] if name == "project" else [])
    append(work.LOGS / "project/plan.jsonl", {"role": "user", "text": "Plan the work", "ts": 13},
           {"role": "assistant", "turn": "plan-cell", "text": "Stopped plan", "cancelled": True, "ts": 14})
    live = SimpleNamespace(id="query-live", focus="wiki", wake=threading.Condition(), events=[
        {"kind": "tool", "text": "Lookup"}], question="Sensitive question", redact=lambda _: "Redacted question",
        started=time.monotonic())
    monkeypatch.setattr(knowledge, "running", lambda _: [{"run_id": live.id}])
    monkeypatch.setattr(knowledge, "live", lambda *_: live)
    cells = web.get("/api/suite").json()["rows"]
    assert next(r for r in cells if r["id"] == "plan-cell")["kind"] == "planning"
    assert next(r for r in cells if r["id"] == "plan-cell")["status"] == "stopped"
    assert next(r for r in cells if r["id"] == live.id)["prompt"] == "Redacted question"
    assert next(r for r in cells if r["id"] == live.id)["steps"] == [{"kind": "tool", "text": "Lookup"}]
    linked = {**spec, "id": "linked", "worktree": str(tmp_path / "project-worktrees/linked"), "workspace_mode": "linked", "pr": None}
    monkeypatch.setattr(specs, "listing", lambda name: [spec, planned, linked] if name == "project" else [])
    append(work.LOGS / "project-worktrees/linked.jsonl",
           {"role": "assistant", "path": linked["worktree"], "turn": "linked-history", "text": "Linked task answer", "ts": 15},
           {"role": "assistant", "path": str(tmp_path / "other/linked"), "text": "private reused-name output"})
    append(work.LOGS / repo.parent.name / f"{repo.name}.jsonl",
           {"role": "assistant", "path": str(repo), "turn": "primary-history", "text": "Primary checkout answer", "ts": 16})
    cells = web.get("/api/suite").json()["rows"]
    assert next(r for r in cells if r["id"] == "linked-history")["task"] == "linked"
    assert next(r for r in cells if r["id"] == "primary-history")["path"] == str(repo)
    assert not any("private" in r["text"] for r in cells)
    assert web.get("/api/suite", headers={"X-Project": "other"}).status_code == 409


@pytest.mark.parametrize("failure", ["record", "feed"])
def test_a_completed_cell_hands_off_even_when_record_or_feed_publication_fails(tmp_path, monkeypatch, failure):
    ordered, released = [], []
    chat = SimpleNamespace(id="worker", parent_id=None, is_codex=False, model="work-model", path=tmp_path,
                           say=lambda *_: iter([Event("done", "Checks passed", {}, "worker")]))
    run = work.Run(chat)
    monkeypatch.setattr(work, "feed", work.Feed())
    monkeypatch.setattr(specs, "check", lambda *_: lambda: ordered.append("handoff"))

    def remember(*_args, **_kwargs):
        if failure == "record":
            raise OSError("Transcript storage unavailable")

    put = work.feed.put

    def publish(event):
        if failure == "feed" and event["kind"] == "work-record":
            raise OSError("Feed unavailable")
        put(event)

    monkeypatch.setattr(work, "remember", remember)
    monkeypatch.setattr(work.feed, "put", publish)
    monkeypatch.setattr(work, "dispatch", lambda *_: ordered.append("queued"))
    work.run_turn(tmp_path, run, "Run checks", lambda: released.append(True))
    assert run.done and released == [True] and ordered == ["handoff", "queued"]
