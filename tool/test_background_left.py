"""What the moved-background check denies, and every way a moved command counts as decided."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from background_left import left, verdict  # noqa: E402


def moved(task: str, timed_out: bool = True) -> dict:
    result = {"stdout": "", "backgroundTaskId": task, **({"timedOutAfterMs": 120000} if timed_out else {})}
    return {"type": "user", "toolUseResult": result, "message": {"role": "user", "content": []}}


def used(name: str, **given) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": given}]}}


def notice(task: str) -> dict:
    return {"type": "queue-operation", "content": f"<task-notification>\n<task-id>{task}</task-id>\n"
                                                  "<status>completed</status>\n</task-notification>"}


def transcript(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "session.jsonl"
    # A first line the tail read may cut in half is skipped, as in a long transcript.
    path.write_text("\n".join(["{}"] + [json.dumps(e) for e in entries]) + "\n", encoding="utf-8")
    return path


def denied(path: Path, tool: str = "Bash") -> bool:
    answer = verdict({"tool_name": tool, "transcript_path": str(path), "tool_input": {"command": "ls"}})
    return bool(answer) and answer["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_command_its_timeout_moved_holds_every_other_tool_until_decided(tmp_path):
    path = transcript(tmp_path, used("Bash", command="du -sh cache"), moved("byvzy5up3"))
    assert left(path) == ["byvzy5up3"]
    for tool in ("Bash", "Edit", "Write", "Grep", "Agent"):
        assert denied(path, tool), tool
    assert "byvzy5up3" in verdict({"tool_name": "Bash", "transcript_path": str(path)})["systemMessage"]


@pytest.mark.parametrize("tool", ["Yield", "Monitor", "TaskStop", "TaskOutput", "Read"])
def test_the_tools_that_decide_it_go_through(tmp_path, tool):
    path = transcript(tmp_path, moved("byvzy5up3"))
    assert not denied(path, tool)


@pytest.mark.parametrize("decision", [
    used("Yield"),
    used("Monitor", command="until done; do sleep 5; done"),
    used("TaskStop", task_id="byvzy5up3"),
    used("TaskOutput", task_id="byvzy5up3"),
    used("Read", file_path="C:/Temp/claude/tasks/byvzy5up3.output"),
    notice("byvzy5up3"),
])
def test_waiting_reading_stopping_or_its_end_decides_it(tmp_path, decision):
    assert not denied(transcript(tmp_path, moved("byvzy5up3"), decision))


def test_deciding_another_task_leaves_this_one_held(tmp_path):
    path = transcript(tmp_path, moved("aaa111"), moved("bbb222"), used("TaskStop", task_id="aaa111"),
                      used("Read", file_path="C:/repo/notes.md"), notice("ccc333"))
    assert left(path) == ["bbb222"]
    assert denied(path)


def test_a_command_started_in_the_background_on_purpose_is_left_alone(tmp_path):
    assert not denied(transcript(tmp_path, moved("chosen1", timed_out=False)))


def test_a_command_moved_after_an_earlier_wait_is_held_again(tmp_path):
    path = transcript(tmp_path, moved("first1"), used("Yield"), moved("second2"))
    assert left(path) == ["second2"]


def test_no_transcript_or_one_it_cannot_read_lets_the_call_through(tmp_path):
    assert verdict({"tool_name": "Bash"}) is None
    assert not denied(tmp_path / "missing.jsonl")
