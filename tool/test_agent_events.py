"""Provider progress, hooks, steering and background result attribution."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent import ChatSession, chat_session
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


def test_codex_command_output_and_mcp_progress_reach_the_agent_stream(tree):
    fixture = CODEX_BYPASS.replace(
        'say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "ran"}}})',
        'say({"method": "item/commandExecution/outputDelta", "params": {"itemId": "command-1", "delta": "40 percent\\n"}})\n'
        'say({"method": "item/mcpToolCall/progress", "params": {"itemId": "mcp-1", "message": "Collecting results"}})\n'
        'say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "ran"}}})')
    _, events = run(ChatSession(tree, model="codex:m", write=True, bypass=True), fixture, tree)
    assert [(e.text, e.meta["tool"]) for e in events if e.meta.get("item_id")] == [
        ("40 percent\n", "commandExecution"), ("Collecting results", "mcpToolCall")]
    assert events[-1].kind == "done" and events[-1].text == "ran"


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
