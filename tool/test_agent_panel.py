"""The agent panel reads actual changes and preserves provider question values."""

import json
import subprocess
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agent.chat_local import quota_windows
from agent.chat_session import ChatSession
from main import specs, work


def test_live_diff_reads_new_staged_shell_and_committed_changes(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Panel test")
    git("config", "user.email", "panel@example.test")
    file = tmp_path / "source.py"
    file.write_text("before = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    path = str(tmp_path)
    monkeypatch.setattr(work, "_sessions", {})
    monkeypatch.setattr(work, "_runs", {path: SimpleNamespace(diff_base=base)})
    monkeypatch.setattr(work, "worktrees", lambda _: [{"path": tmp_path}])
    monkeypatch.setattr(work, "current_repo", lambda: tmp_path)
    assert work.changes(path)["diff"] == ""
    # A shell write has no structured provider file-change event.
    file.write_text("after = 2\n", encoding="utf-8")
    new = tmp_path / "새 파일.txt"
    new.write_text("new content\n", encoding="utf-8")
    data = work.changes(path)
    assert "-before = 1" in data["diff"] and "+after = 2" in data["diff"] and "+new content" in data["diff"]
    assert "새 파일.txt" in data["diff"]
    git("add", ".")
    assert "+after = 2" in work.changes(path)["diff"]
    git("commit", "-qm", "agent changes")
    assert "+after = 2" in work.changes(path)["diff"]  # Commit does not clear the preview.
    (tmp_path / "large.txt").write_bytes(b"x" * 1_000_001)
    assert "large.txt" in work.changes(path)["omitted"]
    with pytest.raises(HTTPException) as error:
        work.changes(str(tmp_path.parent))
    assert error.value.status_code == 404


def test_question_batches_keep_chapters_and_previews_on_record():
    questions = [{"header": "Layout", "question": "Choose a layout", "options": [
        {"label": "Columns", "note": "Compare side by side", "preview": "```text\n[ A ][ B ]\n```"}]},
        {"header": "Behaviour", "question": "Choose behaviour", "options": [{"label": "Live"}]}]
    text = "```choices\n" + json.dumps({"questions": questions}) + "\n```"
    _, blocks = specs.blocks(text)
    specs._shape("choices", blocks[0]["value"])
    for broken in ({"questions": []}, {"questions": [None]}, {"question": "?", "options": [{"label": "x", "preview": {}}]}):
        with pytest.raises(ValueError):
            specs._shape("choices", broken)
    ev = {"kind": "approval", "text": "questions", "meta": {
        "id": "q1", "tool": "AskUserQuestion", "input": {"questions": questions}}}
    answered = {"kind": "answered", "text": "", "meta": {
        "id": "q1", "allow": True, "by": "person", "answers": ["Columns", "Live"]}}
    record = work.steps([ev, answered])[0]
    assert record["input"]["questions"] == questions
    assert record["answers"] == ["Columns", "Live"]


def test_provider_events_capture_usage_and_reset_without_ending_turn(tmp_path):
    claude = ChatSession(tmp_path)
    claude._boot_at = time.monotonic()
    for event in [
        {"type": "system", "subtype": "init", "model": "sonnet", "session_id": "fixture"},
        {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": 0.72, "resetsAt": 1800000000}},
        {"type": "result", "result": "Finished", "usage": {"input_tokens": 12, "output_tokens": 8}, "total_cost_usd": 0.01},
    ]:
        claude._events.put(event)
    assert list(claude._drain())[-1].kind == "done"
    status = claude.status()
    assert status["connection_ms"] is not None
    assert status["quota"][0]["used_percent"] == 72
    assert status["quota"][0]["resets_at"] == 1800000000
    assert status["usage"]["input_tokens"] == 12
    data = {"rateLimitsByLimitId": {"codex": {"primary": {
        "usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1800000000}, "secondary": None}},
        "account": {"email": "private@example.test"}, "authToken": "private"}
    windows = quota_windows(data)
    assert len(windows) == 1 and windows[0]["used_percent"] == 25
    assert "private" not in json.dumps(windows)
    codex = ChatSession(tmp_path, model="codex:test")
    for event in [
        {"method": "account/rateLimits/updated", "params": data},
        {"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {
            "last": {"inputTokens": 12, "outputTokens": 8}, "total": {"inputTokens": 100, "outputTokens": 50}}}},
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "Finished"}}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ]:
        codex._events.put(event)
    assert list(codex._drain())[-1].kind == "done"
    assert codex.status()["quota"] == windows
    assert codex.usage["scope"] == "thread" and codex.usage["input_tokens"] == 100
