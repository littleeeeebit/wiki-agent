"""Whether `usage` counts each response once and each person's turn once."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trajectory  # noqa: E402
import trigger_audit  # noqa: E402


def write(rows: list[dict]) -> Path:
    path = Path(tempfile.mkdtemp()) / "session.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def claude_reply(at: str, key: str, write_: int, read: int, out: int, **extra) -> dict:
    return {"type": "assistant", "timestamp": at, **extra, "message": {"id": key, "usage": {
        "input_tokens": 1, "cache_creation_input_tokens": write_,
        "cache_read_input_tokens": read, "output_tokens": out}}}


def test_claude_turns_count_a_response_once_and_skip_the_harness():
    path = write([
        {"type": "user", "timestamp": "2026-09-20T00:00:00Z", "cwd": "C:/repo", "message": {"content": "첫 발화"}},
        {"type": "attachment", "timestamp": "2026-09-20T00:00:01Z", "attachment": {
            "type": "hook_additional_context", "hookEvent": "UserPromptSubmit", "content": ["x" * 40]}},
        # One response, two content blocks, the usage repeated. The last row wins.
        claude_reply("2026-09-20T00:00:02Z", "m1", 100, 0, 5),
        claude_reply("2026-09-20T00:00:03Z", "m1", 100, 0, 9),
        {"type": "user", "timestamp": "2026-09-20T00:01:00Z",
         "message": {"content": "<task-notification>done</task-notification>"}},
        claude_reply("2026-09-20T00:01:01Z", "m2", 0, 100, 1),
        claude_reply("2026-09-20T00:01:02Z", "s1", 50, 0, 1, isSidechain=True),
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-09-20T00:02:00Z"},
        # Two hours idle, then a person comes back to a cold cache.
        {"type": "user", "timestamp": "2026-09-20T02:05:00Z", "message": {"content": "다시 왔다"}},
        claude_reply("2026-09-20T02:05:01Z", "m3", 900, 0, 1),
    ])
    read = trigger_audit.read_session("claude", path)

    assert read["cwd"] == "C:/repo"
    first, second = read["turns"]
    assert [first["text"], second["text"]] == ["첫 발화", "다시 왔다"], "하네스 주입은 사람 발화가 아니다"
    # m1 once with the last row's output, m2 and the subagent's s1 on the same turn.
    assert first["tokens"] == {"input": 3, "cache_write": 150, "cache_read": 100, "output": 11}, first
    assert first["cost"] == 3 + 2 * 150 + 0.1 * 100 + 5 * 11
    assert first["hook"] == 40 and not first["filed"] and first["idle"] is None
    assert second["idle"] > trigger_audit.IDLE and second["cold"] == {"input": 1, "cache_write": 900}
    # The subagent's context is not the session's.
    assert read["compacts"] == [(trigger_audit.when("2026-09-20T00:02:00Z"), 101)], read["compacts"]


def codex_usage(total: int, cached: int, out: int) -> dict:
    return {"input_tokens": total, "cached_input_tokens": cached,
            "cache_write_input_tokens": 0, "output_tokens": out}


def test_codex_prefers_the_usage_record_and_drops_repeated_counts():
    meta = {"type": "session_meta", "timestamp": "2026-09-20T00:00:00Z", "payload": {"cwd": "C:/repo"}}
    said = {"type": "event_msg", "timestamp": "2026-09-20T00:00:01Z", "payload": {
        "type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "고쳐라"}]}}}
    hook = {"type": "response_item", "timestamp": "2026-09-20T00:00:01Z", "payload": {
        "type": "message", "role": "developer",
        "content": [{"type": "input_text", "text": "<!-- wiki:rule-index -->\n" + "y" * 20}]}}

    def count(total: int) -> dict:
        return {"type": "event_msg", "timestamp": "2026-09-20T00:00:02Z", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"total_tokens": total},
                                            "last_token_usage": codex_usage(100, 60, 10)}}}

    old = trigger_audit.read_session("codex", write([meta, said, hook, count(110), count(110), count(220)]))
    (turn,) = old["turns"]
    assert turn["tokens"] == {"input": 80, "cache_write": 0, "cache_read": 120, "output": 20}, turn
    assert turn["hook"] == len("<!-- wiki:rule-index -->\n" + "y" * 20)

    record = {"type": "token_usage_record", "timestamp": "2026-09-20T00:00:02Z",
              "payload": {"response_id": "r1", "usage": codex_usage(500, 100, 7)}}
    new = trigger_audit.read_session("codex", write([meta, said, count(110), record]))
    assert new["turns"][0]["tokens"] == {"input": 400, "cache_write": 0, "cache_read": 100, "output": 7}


def test_tasks_draw_only_whole_runs_of_people():
    root = Path(tempfile.mkdtemp()) / "repo"
    wiki = root / ".wiki"
    for n in range(3):
        trajectory.record(wiki, f"a{n}", [], 0, "whole")
    trajectory.record(wiki, "b0", [], 0, "cut")
    # A row from before `KEEP` grew: the utterance was longer than what is kept.
    with trajectory.path_for(wiki).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": "2026-09-20T00:00:09+00:00", "session": "cut",
                                 "utterance": "b1", "chars": 900}) + "\n")
    trajectory.record(wiki, "b2", [], 0, "cut")
    trajectory.record(wiki, "<task-notification>x</task-notification>", [], 0, "short")
    trajectory.record(wiki, "c0", [], 0, "short")

    assert trigger_audit.tasks(root, 5, 2, 1) == [["a0", "a1"]]
