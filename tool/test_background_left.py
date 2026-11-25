"""What the moved-background check denies, and every way a moved command counts as decided."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import background_left  # noqa: E402
from background_left import left, verdict  # noqa: E402


SHOWN_RUNNING = background_left.running


@pytest.fixture(autouse=True)
def own_state(tmp_path, monkeypatch):
    """Each test keeps its own saved scan, and its moved commands count as running unless it asks for the
    output-file check itself."""

    monkeypatch.setattr(background_left, "STATE", tmp_path / "state")
    monkeypatch.setattr(background_left, "running", lambda output: True)


def moved(task: str, timed_out: bool = True, output: str = "") -> dict:
    result = {"stdout": "", "backgroundTaskId": task, **({"timedOutAfterMs": 120000} if timed_out else {})}
    text = "Command did not complete within its 120s timeout and was moved to the background " \
           f"(ID: {task}). Output is being written to: {output}. You will be notified." if output else ""
    return {"type": "user", "toolUseResult": result,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"run-{task}",
                                                     "content": text}]}}


def used(name: str, use: str = "", **given) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": use or f"use-{name}",
                                                          "name": name, "input": given}]}}


def answered(use: str, error: bool) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use,
                                                     "is_error": error, "content": "failed" if error else "ok"}]}}


def notice(task: str) -> dict:
    return {"type": "queue-operation", "content": f"<task-notification>\n<task-id>{task}</task-id>\n"
                                                  "<status>completed</status>\n</task-notification>"}


def transcript(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


def appended(path: Path, *entries: dict) -> Path:
    with path.open("a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in entries))
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


@pytest.mark.parametrize("tool,given", [("TaskOutput", {"task_id": "task1"}), ("TaskStop", {"task_id": "task1"}),
                                        ("Read", {"file_path": "C:/tasks/task1.output"}), ("Yield", {})])
def test_a_decision_whose_result_is_an_error_decides_nothing(tmp_path, tool, given):
    path = transcript(tmp_path, moved("task1"), used(tool, use="d1", **given))
    assert left(path) == []   # decided while its result is still to come
    assert denied(appended(path, answered("d1", error=True)))
    assert left(appended(path, used(tool, use="d2", **given), answered("d2", error=False))) == []


def test_a_failed_decision_for_one_task_leaves_another_decided(tmp_path):
    path = transcript(tmp_path, moved("task1"), moved("task2"), used("TaskStop", use="s1", task_id="task1"),
                      used("TaskStop", use="s2", task_id="task2"), answered("s1", error=False),
                      answered("s2", error=True))
    assert left(path) == ["task2"]


def test_a_moved_command_far_back_in_a_long_transcript_is_still_held(tmp_path):
    later = [{"type": "user", "message": {"content": "x" * (1 << 20)}} for _ in range(3)]
    assert denied(transcript(tmp_path, moved("task1"), *later))


def test_the_first_record_is_read_and_later_calls_read_only_what_was_added(tmp_path):
    path = transcript(tmp_path, moved("task1"))
    assert left(path) == ["task1"]
    offset = json.loads(next((tmp_path / "state").glob("*.json")).read_text(encoding="utf-8"))["offset"]
    assert offset == path.stat().st_size
    assert left(appended(path, moved("task2"))) == ["task1", "task2"]
    assert left(appended(path, notice("task1"))) == ["task2"]


def test_a_line_still_being_written_waits_for_the_next_call(tmp_path):
    path = transcript(tmp_path)
    whole = json.dumps(moved("task1")) + "\n"
    path.write_text(whole[:40], encoding="utf-8")
    assert left(path) == []
    path.write_text(whole, encoding="utf-8")
    assert left(path) == ["task1"]


def test_parallel_decisions_on_one_task_settle_by_what_each_returned(tmp_path):
    target = {"file_path": "tasks/task1.output"}
    path = transcript(tmp_path, moved("task1"), used("Read", use="r1", **target), used("Read", use="r2", **target),
                      answered("r1", error=True), answered("r2", error=False))
    assert not denied(path)
    path = transcript(tmp_path, moved("task1"), used("Read", use="r1", **target), used("Read", use="r2", **target),
                      answered("r1", error=False), answered("r2", error=True))
    assert not denied(path)
    path = transcript(tmp_path, moved("task1"), used("Read", use="r1", **target), used("Read", use="r2", **target),
                      answered("r1", error=True), answered("r2", error=True))
    assert denied(path)


def test_a_notice_while_a_decision_is_out_ends_the_task_whatever_the_decision_returns(tmp_path):
    path = transcript(tmp_path, moved("task1"), used("Read", use="r1", file_path="tasks/task1.output"),
                      notice("task1"), answered("r1", error=True))
    assert not denied(path)


def test_a_transcript_rewritten_to_the_same_size_is_read_again_from_its_start(tmp_path):
    path = transcript(tmp_path, moved("old1"))
    assert left(path) == ["old1"]
    size = path.stat().st_size
    transcript(tmp_path, moved("new1"))
    assert path.stat().st_size == size
    assert left(path) == ["new1"]


def test_a_rewrite_before_the_last_read_window_is_seen_too(tmp_path):
    suffix = {"type": "user", "message": {"content": "x" * 5000}}
    path = transcript(tmp_path, moved("old1"), suffix)
    assert left(path) == ["old1"]
    size = path.stat().st_size
    transcript(tmp_path, moved("new1"), suffix)
    assert path.stat().st_size == size
    assert left(path) == ["new1"]


def test_a_transcript_replaced_by_another_file_is_read_again(tmp_path):
    path = transcript(tmp_path, moved("old1"))
    assert left(path) == ["old1"]
    other = tmp_path / "other.jsonl"
    other.write_text(path.read_text(encoding="utf-8").replace("old1", "new1"), encoding="utf-8")
    os.replace(other, path)
    assert left(path) == ["new1"]


@pytest.mark.skipif(os.name != "nt", reason="share modes tell a held output file only on Windows")
def test_a_reader_of_an_ended_tasks_output_does_not_hold_the_session(tmp_path, monkeypatch):
    monkeypatch.setattr(background_left, "running", SHOWN_RUNNING)
    output = tmp_path / "task1.output"
    output.write_text("finished", encoding="utf-8")
    path = transcript(tmp_path, moved("task1", output=str(output)))
    assert not denied(path)
    with output.open("r", encoding="utf-8"):
        assert not denied(path)


def test_where_a_held_output_file_cannot_be_seen_nothing_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(background_left, "running", SHOWN_RUNNING)
    monkeypatch.setattr(background_left, "LIVENESS", False)
    output = tmp_path / "task1.output"
    with output.open("a", encoding="utf-8"):
        assert not denied(transcript(tmp_path, moved("task1", output=str(output))))
    monkeypatch.setattr(background_left, "LIVENESS", os.name == "nt")
    assert not denied(transcript(tmp_path, moved("task2")))   # an unnamed output file shows nothing either


@pytest.mark.skipif(os.name != "nt", reason="share modes tell a held output file only on Windows")
def test_a_command_counts_as_running_only_while_its_output_file_is_held_open(tmp_path, monkeypatch):
    monkeypatch.setattr(background_left, "running", SHOWN_RUNNING)
    output = tmp_path / "task1.output"
    output.write_text("", encoding="utf-8")
    path = transcript(tmp_path, moved("task1", output=str(output)))
    with output.open("a", encoding="utf-8"):   # the command still writing it, as across a compaction
        assert denied(path)
    # Its process ended without a notice, as a resumed session's earlier process did: nothing holds it.
    assert not denied(appended(path, {"type": "user", "message": {"content": "resumed"}}))
    output.unlink()
    assert left(path) == []


def test_no_transcript_or_one_it_cannot_read_lets_the_call_through(tmp_path):
    assert verdict({"tool_name": "Bash"}) is None
    assert not denied(tmp_path / "missing.jsonl")
