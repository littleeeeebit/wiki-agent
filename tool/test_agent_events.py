"""Provider progress, hooks, steering and background result attribution."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name

import json
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from agent import ChatSession, chat_busy, chat_session
from test_agent import CODEX_BYPASS, CODEX_START, CLAUDE_ANSWERS, run, tree  # noqa: F401 -- shared fixture


def test_background_child_text_does_not_replace_the_parent_answer(tree):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "assistant", "parent_tool_use_id": "bg-1", "message": {"content": [{"type": "text", "text": "Child progress"}]}})
say({"type": "stream_event", "parent_tool_use_id": "bg-1", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Child delta"}}})
say({"type": "result", "result": "Parent answer", "session_id": "cli-1"})
sys.stdin.read()
'''
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert events[-1].text == "Parent answer"
    assert not any(e.kind in ("progress", "delta") for e in events)
    assert any("Child progress" in e.text for e in events if e.kind == "tool")


@pytest.mark.parametrize("origin", [{"kind": "human"}, None])
def test_fast_background_completion_does_not_close_on_foreground_result(tree, origin):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1"})
say({"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "completed"})
say({"type": "result", "origin": ORIGIN, "result": "Foreground", "session_id": "cli-1"})
say({"type": "user", "isReplay": True, "isSynthetic": True, "origin": {"kind": "task-notification"}, "message": {"content": []}})
say({"type": "result", "origin": {"kind": "task-notification"}, "result": "Follow-up", "session_id": "cli-1"})
sys.stdin.read()
'''.replace("ORIGIN", repr(origin))
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert [event.text for event in events if event.kind == "done"] == ["Follow-up"]


@pytest.mark.parametrize("batched", [True, False])
def test_multiple_background_results_drain_each_notification_before_final_response(tree, batched):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
for task in ("bg-1", "bg-2"):
    say({"type": "system", "subtype": "task_started", "task_id": task})
    say({"type": "system", "subtype": "task_notification", "task_id": task, "status": "completed"})
say({"type": "result", "origin": {"kind": "human"}, "result": "Foreground", "session_id": "cli-1"})
say({"type": "result", "origin": {"kind": "task-notification"}, "num_turns": FIRST_TURNS, "result": "First finished", "session_id": "cli-1"})
say({"type": "result", "origin": {"kind": "task-notification"}, "num_turns": 1, "result": "Both finished", "session_id": "cli-1"})
sys.stdin.read()
'''.replace("FIRST_TURNS", "0" if batched else "1")
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert [event.text for event in events if event.kind == "done"] == ["Both finished"]


def test_background_work_does_not_hide_a_provider_error_result(tree):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1"})
say({"type": "result", "is_error": True, "result": "API failed", "session_id": "cli-1"})
sys.stdin.read()
'''
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert events[-1].kind == "done" and events[-1].meta["error"]


@pytest.mark.parametrize("stopped", [
    # What CLI 2.1.316 streams: the reason only on the partial message delta.
    '{"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "refusal"}}}',
    # What its transcript keeps, should a later CLI stream it the same way.
    '{"type": "assistant", "message": {"stop_reason": "refusal", "content": [{"type": "thinking", "thinking": ""}]}}',
])
def test_a_response_the_safeguards_stopped_marks_the_turn_and_fails_only_an_empty_one(tree, stopped):
    from main import work

    # PR #272 R1: the CLI went on past the refusal and ended on an ordinary
    # result, so the loop read a format failure and sent the request again.
    # Its second R1 turn went on to a complete answer, which #273 then threw away.
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say(STOPPED)'''.replace("STOPPED", stopped) + '''
say({"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Not repaired."}]}})
say({"type": "result", "result": "Not repaired.", "session_id": "cli-1"})
sys.stdin.read()
'''
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    done = events[-1]
    assert done.kind == "done" and done.meta["refused"] and not done.meta["error"]
    assert work.REFUSED.search(done.text) and done.text.endswith("Not repaired.")
    _, events = run(ChatSession(tree, write=True, bypass=True),
                    fixture.replace('"result": "Not repaired."', '"result": ""'), tree)
    assert events[-1].meta["refused"] and events[-1].meta["error"]
    # A child's refusal is the child's: the parent that went on and finished is not failed (#273 R2).
    _, events = run(ChatSession(tree, write=True, bypass=True),
                    fixture.replace(stopped, stopped[:-1] + ', "parent_tool_use_id": "bg-1"}'), tree)
    assert not events[-1].meta["error"] and not events[-1].meta["refused"]


def test_failed_background_turn_cannot_complete_the_next_prompt_with_stale_events(tree):
    failed = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1"})
say({"type": "result", "is_error": True, "result": "API failed", "session_id": "cli-1"})
say({"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "completed"})
say({"type": "result", "origin": {"kind": "task-notification"}, "result": "Stale response", "session_id": "cli-1"})
if sys.stdin.readline():
    say({"type": "result", "result": "Fresh second answer", "session_id": "cli-1"})
sys.stdin.read()
'''
    fresh = '''import json, sys
sys.stdin.readline()
print(json.dumps({"type": "result", "result": "Fresh second answer", "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''
    session = ChatSession(tree, write=True, bypass=True)
    real_popen, processes = subprocess.Popen, []

    def spawn(command, **kwargs):
        process = real_popen([sys.executable, "-X", "utf8", "-c", failed if not processes else fresh], **kwargs)
        processes.append(process)
        return process

    try:
        with patch.object(chat_session.subprocess, "Popen", spawn), \
             patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
            first = list(session.say("First prompt"))
            assert first[-1].meta["error"] and not session.alive
            second = list(session.say("Second prompt"))
        assert [event.text for event in second if event.kind == "done"] == ["Fresh second answer"]
        assert len(processes) == 2 and processes[0].poll() is not None
    finally:
        session.close()


@pytest.mark.parametrize("replay", [True, False])
def test_unattributed_overlapping_tasks_settle_each_followup_while_other_tasks_run(tree, replay):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
for task in ("bg-1", "bg-2"):
    say({"type": "system", "subtype": "task_started", "task_id": task})
say({"type": "result", "result": "Foreground", "session_id": "cli-1"})
for task in ("bg-1", "bg-2"):
    say({"type": "system", "subtype": "task_notification", "task_id": task, "status": "completed"})
    if REPLAY:
        say({"type": "user", "isReplay": True, "isSynthetic": True, "message": {"content": []}})
    say({"type": "result", "result": task + " done", "session_id": "cli-1"})
sys.stdin.read()
'''.replace("REPLAY", repr(replay))
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert [event.text for event in events if event.kind == "done"] == ["bg-2 done"]


def test_unattributed_steered_human_result_does_not_consume_background_followup(tree):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
first = json.loads(sys.stdin.readline())
say({"type": "user", "isReplay": True, "message": first["message"]})
say({"type": "system", "subtype": "task_started", "task_id": "bg-1"})
steer = json.loads(sys.stdin.readline())
say({"type": "result", "result": "Foreground", "session_id": "cli-1"})
say({"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "completed"})
say({"type": "user", "isReplay": True, "message": steer["message"]})
say({"type": "result", "result": "Steered response", "session_id": "cli-1"})
say({"type": "user", "isReplay": True, "isSynthetic": True, "message": {"content": []}})
say({"type": "result", "result": "Background finished", "session_id": "cli-1"})
sys.stdin.read()
'''
    session = ChatSession(tree, write=True, bypass=True)
    _, events = run(session, fixture, tree, each=lambda event: session.steer("More")
                    if event.meta.get("status") == "started" else None)
    assert [event.text for event in events if event.kind == "done"] == ["Background finished"]


CODEX_STEER = CODEX_START + '''say({"method": "turn/started", "params": {"turn": {"id": "t-1"}}})
say({"method": "item/started", "params": {"item": {"type": "commandExecution", "command": "x"}}})
m = read()
assert m["method"] == "turn/steer" and m["params"]["expectedTurnId"] == "t-1", m
say({"id": m["id"], "error": {"message": "no active turn"}})
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": m["params"]["input"][0]["text"]}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
'''


# The steer repeats the prompt word for word, and comes before the prompt's
# replay: a match by text would count the prompt's replay as the steer's.
CLAUDE_ECHO = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
first = json.loads(sys.stdin.readline())
say({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "x"}}]}})
steer = json.loads(sys.stdin.readline())
say({"type": "user", "isReplay": True, "message": first["message"]})
say({"type": "result", "result": "one", "session_id": "cli-1"})
say({"type": "user", "isReplay": True, "message": steer["message"]})
say({"type": "result", "result": "two", "session_id": "cli-1"})
sys.stdin.read()
'''


def test_a_steer_that_repeats_the_prompt_is_still_waited_for(tree):
    session = ChatSession(tree, write=True, bypass=True)
    _, events = run(session, CLAUDE_ECHO, tree, each=lambda e: e.kind == "tool" and session.steer("write it"))
    assert [e.kind for e in events].count("done") == 1 and events[-1].text == "two"


def test_a_codex_steer_names_the_turn_and_its_refusal_does_not_end_it(tree):
    session = ChatSession(tree, model="codex:m", write=True, bypass=True)
    _, events = run(session, CODEX_STEER, tree,
                    each=lambda e: e.kind == "tool" and e.text == "x" and session.steer("more"))
    assert any(e.kind == "tool" and e.text.startswith("끼어들기 실패") for e in events)
    assert events[-1].kind == "done" and events[-1].text == "more"


# A hook that spoke, one that injected, one silent; then a question.
CLAUDE_QUESTION = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
hook = lambda event, output: say({"type": "system", "subtype": "hook_response", "hook_event": event,
                                  "output": output, "stderr": "", "exit_code": 0})
hook("UserPromptSubmit", json.dumps({"systemMessage": "위키 주입: a", "hookSpecificOutput": {"additionalContext": "rules"}}))
hook("SessionStart", "plain context")
hook("Stop", "")
q = {"question": "Pick", "header": "P", "options": [{"label": "A"}, {"label": "B"}], "multiSelect": False}
say({"type": "control_request", "request_id": "q1",
     "request": {"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [q]}}})
got = json.loads(sys.stdin.readline())["response"]["response"]
say({"type": "result", "result": json.dumps(got["updatedInput"]["answers"]), "session_id": "cli-1"})
sys.stdin.read()
'''


def test_claude_hooks_show_and_a_question_takes_its_answers(tree):
    session = ChatSession(tree, write=True, bypass=True)

    def answer(event):
        # An "answer" with no answer is refused and leaves the question waiting.
        for empty in ([], [" "], ["B", "A"]):
            with pytest.raises(ValueError):
                session.answer(event.meta["id"], True, answers=empty)
        assert session.answer(event.meta["id"], True, answers=["B"])

    command, events = run(session, CLAUDE_QUESTION, tree, answer)
    assert "AskUserQuestion" in command[command.index("--tools") + 1]
    assert command[command.index("--permission-prompt-tool") + 1] == "stdio"   # kept under bypass
    hooks = [(e.text, e.meta.get("context")) for e in events if e.kind == "hook"]
    assert hooks == [("위키 주입: a", "rules"), ("SessionStart · 문맥 13자", "plain context")]
    assert json.loads(events[-1].text) == {"Pick": "B"}


CODEX_QUESTION = CODEX_START + '''say({"method": "hook/completed", "params": {"run": {"eventName": "userPromptSubmit", "entries": [
     {"kind": "warning", "text": "위키 주입: a"}, {"kind": "context", "text": "rules"}]}}})
say({"id": 7, "method": "item/tool/requestUserInput", "params": {"questions": [
     {"id": "fruit", "header": "F", "question": "Pick", "options": [{"label": "A", "description": ""}]}]}})
got = read()["result"]["answers"]
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": json.dumps(got)}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
'''


def test_codex_hooks_show_and_a_question_is_answered_by_its_id(tree):
    session = ChatSession(tree, model="codex:m", write=True)
    _, events = run(session, CODEX_QUESTION, tree, lambda e: session.answer(e.meta["id"], True, answers=["A"]))
    assert [(e.text, e.meta.get("context")) for e in events if e.kind == "hook"] == [("위키 주입: a", "rules")]
    asked = next(e for e in events if e.kind == "approval")
    assert asked.meta["tool"] == "requestUserInput" and not asked.meta["session"]
    assert json.loads(events[-1].text) == {"fruit": {"answers": ["A"]}}


def test_a_tool_line_shows_the_command_beside_what_it_is_for():
    """How long a step may take is read off the command, not its description."""

    brief = chat_session._tool_brief
    assert brief({"name": "Bash", "input": {"description": "Run the tests", "command": "python -m pytest -q tool"}}) \
        == "Bash · Run the tests · $ python -m pytest -q tool"
    assert brief({"name": "Bash", "input": {"command": "git  status\n"}}) == "Bash · $ git  status"
    assert brief({"name": "Bash", "input": {"description": "Read files\nThen check", "command": "git status\ngit diff"}}) \
        == "Bash · Read files\nThen check · $ git status\ngit diff"
    assert brief({"name": "Read", "input": {"file_path": "a.py"}}) == "Read · a.py"
    assert brief({"name": "TodoWrite", "input": {}}) == "TodoWrite"


def test_a_file_edit_carries_the_lines_it_takes_out_and_puts_in():
    """The agent pane colours a write as it streams; the record keeps the same lines."""

    from agent import chat_events
    from main import work

    edit = chat_events._tool_meta({"name": "Edit", "input": {"file_path": "a.py", "old_string": "a\nb\nc", "new_string": "a\nB\nc"}})
    assert edit == {"tool": "Edit", "diff": " a\n-b\n+B\n c"}
    # A Write's old lines are on disk until it runs: its result carries the diff, as Claude's CLI wrote it.
    assert chat_events._tool_meta({"name": "Write", "input": {"file_path": "n.py", "content": "x\n"}}) == {"tool": "Write"}
    one = {"message": {"content": [{"type": "tool_result", "tool_use_id": "w"}]}}
    made = {**one, "tool_use_result": {"type": "create", "filePath": "n.py", "content": "x\ny\n", "structuredPatch": []}}
    assert chat_events._result_meta(made) == {"diff": "+x\n+y"}
    replaced = {**one, "tool_use_result": {"type": "update", "filePath": "a.py", "content": "new_code = 2\nkeep = 0\n",
                                    "structuredPatch": [{"oldStart": 1, "lines": ["-old_code = 1", "+new_code = 2", " keep = 0"]},
                                                        {"oldStart": 9, "lines": ["-z", "+Z"]}]}}
    assert chat_events._result_meta(replaced) == {"diff": "-old_code = 1\n+new_code = 2\n keep = 0\n⋯\n-z\n+Z"}
    assert chat_events._result_meta({"tool_use_result": {"type": "text", "file": {}}}) == {}
    assert chat_events._result_meta({"tool_use_result": "Error"}) == {}
    # A malformed patch drops its diff, never the turn; two results in one event leave whose it is unsaid.
    broken = {**one, "tool_use_result": {"type": "update", "structuredPatch": [{"lines": 7}, {"lines": True}, {"lines": ["+k", 3]}]}}
    assert chat_events._result_meta(broken) == {"diff": "+k"}
    two = {"message": {"content": [{"type": "tool_result", "tool_use_id": "w"}, {"type": "tool_result", "tool_use_id": "r"}]}}
    assert chat_events._result_meta({**two, "tool_use_result": made["tool_use_result"]}) == {}
    multi = chat_events._tool_meta({"name": "MultiEdit", "input": {"edits": [
        {"old_string": "p", "new_string": "q"}, {"old_string": "r", "new_string": "s"}]}})["diff"]
    assert multi == "-p\n+q\n⋯\n-r\n+s"
    assert chat_events._tool_meta({"name": "Read", "input": {"file_path": "a.py"}}) == {"tool": "Read"}
    long = chat_events._result_meta({**one, "tool_use_result": {"type": "create", "content": "\n".join(map(str, range(250)))}})["diff"]
    assert long.count("\n") == chat_events.DIFF_LINES and long.endswith("50줄 더 — 코드 변경 현황에서 전체를 본다")

    patch = chat_events._patch_meta([
        {"path": "new.txt", "kind": {"type": "add"}, "diff": "hello\n"},
        {"path": "old.py", "kind": {"type": "update", "move_path": None},
         "diff": "--- a/old.py\n+++ b/old.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"},
        {"path": "gone.txt", "kind": "delete", "diff": "bye\n"}])
    assert patch["diff"] == "▸ new.txt\n+hello\n▸ old.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n▸ gone.txt\n-bye"
    assert chat_events._patch_meta([{"path": "e.txt", "kind": "add", "diff": ""}]) == {}

    record = [{"kind": "tool", "text": "Edit · a.py", "meta": edit}]
    assert work.steps(record) == [{"kind": "tool", "text": "Edit · a.py", "diff": " a\n-b\n+B\n c"}]


def test_codex_command_output_and_mcp_progress_reach_the_agent_stream(tree):
    fixture = CODEX_BYPASS.replace(
        'say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "ran"}}})',
        # A progress bar: many writes of one command, the screen's one step.
        'for n in range(1, 301): say({"method": "item/commandExecution/outputDelta", "params": {"itemId": "command-1", "delta": f"\\r{n}/300"}})\n'
        'say({"method": "item/commandExecution/outputDelta", "params": {"itemId": "command-1", "delta": "\\n40 percent\\n"}})\n'
        'say({"method": "item/mcpToolCall/progress", "params": {"itemId": "mcp-1", "message": "Collecting results"}})\n'
        'say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "ran"}}})')
    _, events = run(ChatSession(tree, model="codex:m", write=True, bypass=True), fixture, tree)
    streamed = [(e.text, e.meta["tool"]) for e in events if e.meta.get("item_id")]
    assert len(streamed) < 10 and streamed[-1] == ("Collecting results", "mcpToolCall")
    output = "".join(text for text, tool in streamed if tool == "commandExecution")
    assert output.endswith("\r300/300\n40 percent\n")
    assert events[-1].kind == "done" and events[-1].text == "ran"


def test_two_commands_streaming_at_once_stay_one_event_and_one_step_each():
    from agent.chat_events import Event, merged
    from main import work

    now = [0.0]

    def interleaved():
        for i in range(30000):
            now[0] = i * 0.001
            yield Event("tool", f"{i}\r", {"tool": "commandExecution", "item_id": "ab"[i % 2]})
        yield Event("done", "ran")

    events = list(merged(interleaved(), lambda: now[0]))
    assert len(events) < 200 and events[-1].kind == "done"
    assert events[-3].text.endswith("29998\r") and events[-2].text.endswith("29999\r")
    record = [{"kind": e.kind, "text": e.text, "meta": e.meta} for e in events]
    record.append({"kind": "tool", "text": "gate", "meta": {"tool": "commandExecution", "item_id": "a"}})
    steps = work.steps(record)
    # Folded up to the answer, never across it: `answered` counts the steps before it.
    assert [s["text"][-6:] for s in steps] == ["29998\r", "29999\r", "gate"]
    assert len(work.steps(record[:-1])) == 2


@pytest.mark.parametrize("provider", ["claude", "codex", "codex-legacy"])
def test_completed_progress_is_separate_from_the_final_answer(tree, provider):
    text = "First progress.\nSecond line."
    if provider.startswith("codex"):
        fixture = CODEX_BYPASS.replace(
            'say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "ran"}}})',
            'say({"method": "item/agentMessage/delta", "params": {"delta": "First progress."}})\n'
            'say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "phase": "commentary", '
            '"text": "First progress.\\nSecond line."}}})\n'
            'say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "phase": "final_answer", "text": "ran"}}})')
        if provider == "codex-legacy":
            fixture = fixture.replace('"phase": "commentary", ', '')
    else:
        fixture = CLAUDE_ANSWERS.replace(
            'print(json.dumps({"type": "result"',
            'print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", '
            '"text": "First progress.\\nSecond line."}]}}), flush=True)\nprint(json.dumps({"type": "result"')
    _, events = run(ChatSession(tree, model="codex:m" if provider.startswith("codex") else "", write=True, bypass=True), fixture, tree)
    assert [e.text for e in events if e.kind == "progress"] == [text]
    assert events[-1].kind == "done" and events[-1].text == "ran"


def _busy_session(answers, monkeypatch):
    """A session whose turns answer `answers` in order, one list of events per turn."""

    from agent.chat_events import Event

    chat = ChatSession.__new__(ChatSession)
    chat.id, chat.parent_id, chat.isolated, sent = "s", None, False, []

    def turn(text, halt=None, again=False):
        sent.append(chat_busy.CONTINUE if again else text)   # as `_say` with its thread resumed
        yield from (Event(*answer) for answer in answers[len(sent) - 1])

    monkeypatch.setattr(chat, "_say", turn)
    monkeypatch.setattr(chat_busy, "WAITS", (0,))
    return chat, sent


def test_a_turn_refused_at_capacity_is_sent_again_until_it_is_taken(monkeypatch):
    """rfg_rag #70's review loop stopped on this error after one immediate retry."""

    full = [("error", "Selected model is at capacity. Please try a different model.")]
    chat, sent = _busy_session([full, full, full, [("done", "reviewed")]], monkeypatch)
    events = list(chat.say("review", threading.Event()))
    assert sent == ["review"] * 4
    # A `tool` step: `context` on record would cut the conversation's resume.
    assert [e.kind for e in events] == ["tool", "tool", "tool", "done"]
    from main import query
    assert query.line("c", events[0]) is None   # nothing remembered as a conversation break
    rows = [{"role": "assistant", "session_id": "saved-thread", "provider": "claude"},
            {"role": "assistant", "text": "", "error": "멈췄다."}]
    assert query.resumable(rows, codex=False) == "saved-thread"


def test_a_safety_refusal_that_mentions_a_429_is_never_sent_again(monkeypatch):
    """PR #273 R1: the answer after a refusal named an HTTP 429 handler and read as capacity."""

    refusal = [("done", "A response was blocked by our safety systems.\n\nCould not repair the HTTP 429 handler.",
                {"error": True, "refused": True})]
    chat, sent = _busy_session([refusal, [("done", "Unexpected retry")]], monkeypatch)
    events = list(chat.say("repair", threading.Event()))
    assert sent == ["repair"]
    assert [e.kind for e in events] == ["done"] and events[0].meta["refused"]


def test_a_turn_cut_off_at_capacity_after_it_worked_is_continued_never_redone(monkeypatch):
    """ai-coding-wiki's Codex coding turn stopped twice in one task, thousands of steps in;
    the person had to type `이어서 해라.` each time."""

    full = [("error", "Selected model is at capacity. Please try a different model.")]
    chat, sent = _busy_session([[("tool", "edit"), *full], full, [("tool", "test"), ("done", "committed")]],
                               monkeypatch)
    events = list(chat.say("refactor", threading.Event()))
    # The request once; then the continuation, sent again while it is refused before it works.
    assert sent == ["refactor", chat_busy.CONTINUE, chat_busy.CONTINUE]
    assert [e.kind for e in events] == ["tool", "tool", "tool", "tool", "done"] and events[-1].text == "committed"
    assert "이어서 하라고" in events[1].text and "이어서 하라고" in events[2].text
    assert not any(e.kind == "error" for e in events), "the refusal itself never ends the turn"


def test_a_continued_turn_reports_what_its_cut_off_attempts_spent_too(monkeypatch):
    """Planning and refactoring charge their allowance from the last `done`: the
    continuation's usage alone would let a phase run past its ceiling."""

    full = ("done", "Selected model is at capacity.",
            {"error": True, "tokens": {"in": 1000, "out": 500}, "cost_usd": 0.30})
    chat, _ = _busy_session([[("delta", "research"), full],
                             [("done", "planned", {"tokens": {"in": 10, "out": 5}, "cost_usd": 0.01})]], monkeypatch)
    done = list(chat.say("plan", threading.Event()))[-1]
    assert done.meta["tokens"] == {"in": 1010, "out": 505} and done.meta["cost_usd"] == pytest.approx(0.31)
    # A cut-off attempt that worked and left no usage makes the whole unknown, never the last part's.
    chat, _ = _busy_session([[("tool", "edit"), ("error", "Selected model is at capacity.")],
                             [("done", "ok", {"tokens": {"in": 10, "out": 5}})]], monkeypatch)
    assert list(chat.say("x", threading.Event()))[-1].meta["tokens"]["in"] is None
    # So does a cost it did not report; a count only it reported is kept.
    chat, _ = _busy_session([[("tool", "edit"), ("done", "Selected model is at capacity.",
                                                   {"error": True, "tokens": {"in": 1, "out": 1, "cache_write": 700}})],
                             [("done", "ok", {"tokens": {"in": 10, "out": 5}, "cost_usd": 0.01})]], monkeypatch)
    done = list(chat.say("x", threading.Event()))[-1]
    assert done.meta["cost_usd"] is None and done.meta["tokens"] == {"in": 11, "out": 6, "cache_write": 700}
    # A count it reported as unknown stays unknown, whatever the taken turn reports.
    chat, _ = _busy_session([[("tool", "edit"), ("done", "Selected model is at capacity.",
                                                   {"error": True, "tokens": {"in": 1, "out": 1, "cache_write": None}})],
                             [("done", "ok", {"tokens": {"in": 10, "out": 5, "cache_write": 700}})]], monkeypatch)
    assert list(chat.say("x", threading.Event()))[-1].meta["tokens"] == {"in": 11, "out": 6, "cache_write": None}


def test_an_explanation_started_over_after_capacity_is_shown_from_its_start(tmp_path, monkeypatch):
    """Isolated, it starts over: the screen drops the cut-off attempt's text (`simple_start`)."""

    from types import SimpleNamespace
    from unittest.mock import Mock
    from agent.chat_events import Event
    from main import query, work
    chat, _ = _busy_session([[("delta", "Old partial. "), ("error", "Selected model is at capacity.")],
                             [("delta", "Fresh."), ("done", "Fresh.")]], monkeypatch)
    chat.isolated = True

    class Answer:
        def say(self, text, halt=None):
            yield Event("done", "Original answer")
    monkeypatch.setattr(query, "session", lambda *a: Answer())
    monkeypatch.setattr(query, "explain", lambda *a: chat.say("payload", a[-1]))
    for name in ("unseen", "hits_for"):
        monkeypatch.setattr(query, name, lambda *a: [])
    monkeypatch.setattr(query, "remember", Mock())
    monkeypatch.setattr(work, "notice", Mock())
    events = []
    run = SimpleNamespace(repo=tmp_path, cfg=SimpleNamespace(mode="off"), id="fixture", cancel=threading.Event(),
                          sealed=True, redact=lambda value: value, put=events.append, step=Mock(), seal=lambda: True,
                          called=Mock(), finish=Mock())
    query.ask("wiki", SimpleNamespace(propose=False), "Question", {"model": "", "effort": ""}, run, Mock())
    simple = [(e["kind"], e["text"]) for e in events if e["kind"].startswith("simple_")]
    assert simple == [("simple_start", ""), ("simple_delta", "Old partial. "), ("simple_start", ""),
                      ("simple_delta", "Fresh."), ("simple_done", "Fresh.")]


def _two_turns(first, second):
    """`say` against a stand-in CLI whose first process is `first`, then `second`."""

    real_popen, processes = subprocess.Popen, []

    def spawn(command, **kwargs):
        processes.append(real_popen([sys.executable, "-X", "utf8", "-c", second if processes else first], **kwargs))
        return processes[-1]

    return spawn, processes


@pytest.mark.parametrize("codex", [False, True])
def test_a_raw_capacity_refusal_from_either_cli_is_sent_again(tree, codex, monkeypatch):
    """Claude says it on a `result` with `is_error`, Codex on the refused `turn/start`."""

    full = "Selected model is at capacity. Please try a different model."
    if codex:
        refused = CODEX_START.rsplit("m = read()", 1)[0] + (
            'm = read(); say({"id": m["id"], "error": {"message": "FULL"}})\nsys.stdin.read()\n').replace("FULL", full)
        taken = CODEX_BYPASS
    else:
        refused = ('import json, sys\nsys.stdin.readline()\nprint(json.dumps({"type": "result", "is_error": True, '
                   '"result": "FULL", "session_id": "cli-1"}), flush=True)\nsys.stdin.read()\n').replace("FULL", full)
        taken = CLAUDE_ANSWERS
    monkeypatch.setattr(chat_busy, "WAITS", (0,))
    session = ChatSession(tree, model="codex:m" if codex else "", write=True, bypass=True)
    spawn, processes = _two_turns(refused, taken)
    try:
        with patch.object(chat_session.subprocess, "Popen", spawn), \
             patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
            events = list(session.say("review", threading.Event()))
    finally:
        session.close()
    assert len(processes) == 2
    assert [e.kind for e in events][0] == "tool" and full in events[0].text
    assert events[-1].kind == "done" and events[-1].text == "ran" and not events[-1].meta.get("error")


def test_an_isolated_turn_cut_off_after_it_worked_starts_over_with_its_request(monkeypatch):
    """`--no-session-persistence` keeps nothing to `--resume` or continue."""

    first = """import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "init", "session_id": "ephemeral-1"})
say({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Part"}}})
say({"type": "result", "is_error": True, "result": "Selected model is at capacity.", "session_id": "ephemeral-1"})
sys.stdin.read()
"""
    second = """import json, sys
heard = sys.stdin.readline()
print(json.dumps({"type": "result", "is_error": False, "session_id": "ephemeral-2",
                  "result": "original" if "contains the facts" in heard else "other"}), flush=True)
sys.stdin.read()
"""
    spawn, processes = _two_turns(first, second)
    commands = []
    monkeypatch.setattr(chat_session.subprocess, "Popen", lambda command, **kw: commands.append(command) or spawn(command, **kw))
    monkeypatch.setattr(chat_session, "cli_command", lambda name: [name])
    monkeypatch.setattr(chat_busy, "WAITS", (0,))
    events = list(chat_session.explain("The answer contains the facts.", halt=threading.Event()))
    assert len(commands) == 2 and "--resume" not in commands[1]
    assert events[-1].kind == "done" and events[-1].text == "original"


@pytest.mark.parametrize("lost", [False, True])
def test_a_continuation_goes_only_to_the_thread_that_was_resumed(tree, monkeypatch, lost):
    """A fresh Codex thread holds neither the task nor what was steered into it: the turn stops instead."""

    first = CODEX_START + """
say({"method": "item/started", "params": {"item": {"type": "commandExecution", "command": "work"}}})
say({"method": "turn/completed", "params": {"turn": {"status": "failed", "error": {"message": "Selected model is at capacity."}}}})
sys.stdin.read()
"""
    second = """import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read()
if LOST:
    say({"id": m["id"], "error": {"code": -32600, "message": "no rollout found for thread id th-1"}})
    m = read()
    say({"id": m["id"], "result": {"thread": {"id": "fresh-thread"}}})
else:
    say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
m = read()
say({"id": m["id"], "result": {"turn": {}}})
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "phase": "final_answer",
                                                     "text": m["params"]["input"][0]["text"]}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
""".replace("LOST", repr(lost))
    monkeypatch.setattr(chat_busy, "WAITS", (0,))
    spawn, processes = _two_turns(first, second)
    chat = ChatSession(tree, tools="Bash", model="codex:m")
    try:
        with patch.object(chat_session.subprocess, "Popen", spawn), \
             patch.object(chat_session, "cli_command", lambda name: [name]):
            events = list(chat.say("Original task", threading.Event()))
    finally:
        chat.close()
    assert len(processes) == 2
    if lost:
        assert [e.kind for e in events][-2:] == ["context", "error"] and "멈췄다" in events[-1].text
    else:
        assert events[-1].kind == "done" and events[-1].text == chat_busy.CONTINUE


@pytest.mark.parametrize("ending", ["end", "end_all"])
def test_a_capacity_wait_ends_at_once_when_the_session_or_server_ends(monkeypatch, ending):
    """No halt — a one-off turn's case — and a wait far longer than the test."""

    chat, _ = _busy_session([[("error", "Selected model is at capacity.")]], monkeypatch)
    monkeypatch.setattr(chat_busy, "WAITS", (600,))
    chat._halting, chat._proc, out = threading.Lock(), None, []
    waiting = threading.Thread(target=lambda: [out.append(e) for e in chat.say("x")])
    waiting.start()
    while not out and waiting.is_alive():
        threading.Event().wait(0.05)
    try:
        chat.end() if ending == "end" else chat_session.end_all()
        waiting.join(timeout=5)
    finally:
        chat_session.reopen()
    assert not waiting.is_alive() and out[-1].text == "멈췄다."


def test_an_ended_wait_stays_ended_when_the_session_takes_another_turn(monkeypatch):
    full = [("error", "Selected model is at capacity.")]
    chat, sent = _busy_session([full, [("done", "new")], [("done", "old again")]], monkeypatch)
    chat._halting, chat._proc = threading.Lock(), None
    old = chat.say("old")
    assert next(old).kind == "tool"   # waiting
    chat.end()
    assert [e.text for e in chat.say("new")] == ["new"]
    assert [e.text for e in old] == ["멈췄다."] and sent == ["old", "new"]


def test_cancelling_the_run_ends_its_plain_explanation_waiting_on_capacity(monkeypatch):
    """The explanation's session is its own, not the focus's: the run's cancel reaches it as `halt`."""

    from agent.chat_events import Event
    sent: list = []

    def full(self, text, halt=None, again=False):
        sent.append(text)
        yield Event("error", "Selected model is at capacity.")

    monkeypatch.setattr(ChatSession, "_say", full)
    monkeypatch.setattr(chat_busy, "WAITS", (600,))
    cancel, out = threading.Event(), []
    waiting = threading.Thread(target=lambda: [out.append(e) for e in chat_session.explain("answer", halt=cancel)])
    waiting.start()
    while not out and waiting.is_alive():
        threading.Event().wait(0.05)
    cancel.set()
    waiting.join(timeout=5)
    assert not waiting.is_alive() and out[-1].text == "멈췄다." and len(sent) == 1


def test_a_capacity_wait_ends_on_halt_and_other_errors_pass_through(monkeypatch):
    halt = threading.Event()
    halt.set()
    full = [("error", "Selected model is at capacity.")]
    chat, sent = _busy_session([full], monkeypatch)
    assert [(e.kind, e.text) for e in chat.say("x", halt)][-1] == ("error", "멈췄다.") and len(sent) == 1
    # Another error is never sent twice.
    chat, sent = _busy_session([[("error", "프로세스가 닫혔다.")]], monkeypatch)
    assert [e.text for e in chat.say("x", threading.Event())] == ["프로세스가 닫혔다."] and len(sent) == 1
