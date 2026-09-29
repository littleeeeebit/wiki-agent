"""Write sessions: every write the CLI wants reaches the person as an approval,
the answer goes back, and nothing outside the worktree is even asked about.

The CLIs are stand-in child processes that speak each host's protocol.
"""

import json
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from agent import ChatSession, chat_session
from workspace import create

CLAUDE = '''import json, sys
tree, out = sys.argv[1], sys.argv[2]
sys.stdin.readline()
def ask(rid, tool, args):
    print(json.dumps({"type": "control_request", "request_id": rid,
                      "request": {"subtype": "can_use_tool", "tool_name": tool, "input": args}}), flush=True)
    return json.loads(sys.stdin.readline())["response"]["response"]["behavior"]
inside = ask("r1", "Write", {"file_path": tree + "/a.txt", "content": "x"})
outside = ask("r2", "Edit", {"file_path": out, "old_string": "a", "new_string": "b"})
print(json.dumps({"type": "result", "result": inside + "," + outside, "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''

CODEX = '''import json, sys
tree, out = sys.argv[1], sys.argv[2]
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
assert read()["method"] == "initialized"
m = read()
assert m["method"] == "thread/start", m
assert (m["params"]["sandbox"], m["params"]["approvalPolicy"]) == ("read-only", "untrusted"), m
say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
m = read(); assert m["params"]["threadId"] == "th-1"; say({"id": m["id"], "result": {"turn": {}}})
say({"method": "item/started", "params": {"item": {"type": "fileChange", "id": "f1",
     "changes": [{"path": tree + "/a.txt", "kind": "add", "diff": ""}]}}})
say({"id": 100, "method": "item/fileChange/requestApproval", "params": {"itemId": "f1"}})
a = read()["result"]["decision"]
say({"id": 101, "method": "item/commandExecution/requestApproval", "params": {"command": "rm -rf x", "cwd": out}})
b = read()["result"]["decision"]
say({"id": 102, "method": "item/tool/call", "params": {}})
c = "error" in read()
say({"method": "item/agentMessage/delta", "params": {"delta": "ok"}})
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": f"{a},{b},{c}"}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
'''


# Asks what `ASKS` lists, in order, and reports each answer.
CLAUDE_ASKS = '''import json, sys
tree, out = sys.argv[1], sys.argv[2]
sys.stdin.readline()
def ask(rid, tool, args):
    print(json.dumps({"type": "control_request", "request_id": rid,
                      "request": {"subtype": "can_use_tool", "tool_name": tool, "input": args}}), flush=True)
    return json.loads(sys.stdin.readline())["response"]["response"]["behavior"]
said = [ask("a1", "Write", {"file_path": tree + "/a.txt", "content": "x"}),
        ask("a2", "Write", {"file_path": tree + "/b.txt", "content": "y"}),
        ask("a3", "Edit", {"file_path": tree + "/a.txt", "old_string": "x", "new_string": "z"}),
        ask("a4", "Write", {"file_path": out, "content": "x"}),
        ask("a5", "Bash", {"command": "pytest -q"}),
        ask("a6", "Bash", {"command": "pytest -q"}),
        ask("a7", "Bash", {"command": "pytest -q -x"})]
print(json.dumps({"type": "result", "result": ",".join(said), "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''

CODEX_ASKS = '''import json, sys
tree, out = sys.argv[1], sys.argv[2]
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read(); say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
m = read(); say({"id": m["id"], "result": {"turn": {}}})
said = []
for rid, cwd in ((1, tree), (2, tree), (3, tree + "/sub")):
    say({"id": rid, "method": "item/commandExecution/requestApproval", "params": {"command": "pytest -q", "cwd": cwd}})
    said.append(read()["result"]["decision"])
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": ",".join(said)}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
'''


@pytest.fixture
def tree(tmp_path, monkeypatch):
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t")
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "s"], check=True)
    return create(repo, "task")


def run(session, fixture, tree, answer=None, halt=None, each=None):
    """Drive one turn against a stand-in CLI. `answer` sees each approval a
    person is asked; `each` sees every event."""

    real_popen, commands, events = subprocess.Popen, [], []

    def spawn(command, **kwargs):
        commands.append(command)
        return real_popen([sys.executable, "-X", "utf8", "-c", fixture, str(tree),
                           str(tree.parent / "elsewhere.txt")], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        for event in session.say("write it", halt):
            events.append(event)
            if each:
                each(event)
            if event.kind == "approval" and answer and "by" not in event.meta:
                answer(event)
    session.close()
    return commands[0], events


def test_claude_write_asks_and_the_answer_reaches_it(tree):
    session = ChatSession(tree, write=True, parent_id="coordinator")

    answered = []

    def later(approval_id):
        # From another thread, as the screen does, and after both deadlines:
        # the turn is waiting on a person, not hanging.
        time.sleep(0.5)
        answered.append(session.answer(approval_id, True))
        answered.append(session.answer(approval_id, True))  # once only

    def answer(event):
        threading.Thread(target=later, args=(event.meta["id"],)).start()

    with patch.object(chat_session, "BOOT_TIMEOUT", 0.2), patch.object(chat_session, "TURN_TIMEOUT", 0.2):
        command, events = run(session, CLAUDE, tree, answer)
    assert command[command.index("--allowedTools") + 1] == "Read,Glob,Grep"
    assert "Write" in command[command.index("--tools") + 1]
    assert command[command.index("--permission-prompt-tool") + 1] == "stdio"
    assert command[command.index("--permission-mode") + 1] == "default"
    approvals = [e for e in events if e.kind == "approval"]
    # the second is outside the worktree: refused unasked, and on record as such
    assert [(e.meta["id"], e.meta["tool"], e.meta.get("by")) for e in approvals] == [
        ("r1", "Write", None), ("r2", "Edit", "outside")]
    assert approvals[1].meta["answer"] == "deny"
    assert events[-1].kind == "done" and events[-1].text == "allow,deny"
    assert {(e.session_id, e.parent_id) for e in events} == {(session.id, "coordinator")}
    assert answered == [True, False]


def test_a_read_session_refuses_without_asking(tmp_path):
    _, events = run(ChatSession(tmp_path), CLAUDE, tmp_path)
    assert [e.meta["by"] for e in events if e.kind == "approval"] == ["read", "outside"]
    assert events[-1].text == "deny,deny"


def test_codex_write_runs_app_server_and_answers_by_id(tree):
    session = ChatSession(tree, model="codex:test-model", write=True)
    command, events = run(session, CODEX, tree, lambda e: session.answer(e.meta["id"], True))
    assert command == ["codex", "app-server", "--enable", "default_mode_request_user_input"]
    approvals = [e for e in events if e.kind == "approval"]
    # the command ran in a cwd outside the worktree: declined unasked
    assert [(e.meta["id"], e.meta["tool"], e.meta.get("by")) for e in approvals] == [
        ("100", "fileChange", None), ("101", "command", "outside")]
    assert events[-1].kind == "done" and events[-1].text == "accept,decline,True"
    assert events[-1].meta["session_id"] == "th-1"


def test_writes_open_only_in_a_worktree_workspace_made(tree):
    repo = tree.parent.parent / "demo"
    elsewhere = tree.parent.parent / "orca" / "task"   # someone else's worktree
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "other", str(elsewhere)], check=True)
    for path in (repo, elsewhere):
        with pytest.raises(ValueError):
            ChatSession(path, write=True)
        ChatSession(path)  # reading there is fine


def test_a_late_answer_never_reaches_the_next_process(tree):
    """`answer` racing a restart: the reply belongs to the process that asked."""

    session = ChatSession(tree, write=True)
    restarted = []

    def restart_midway(event):
        if restarted:
            session.answer(event.meta["id"], False)  # only the first approval races
            return
        approval_id = event.meta["id"]
        asker, reply, rule = session._pending[approval_id]

        def racing(allow, answers=()):
            # Between `answer` taking the reply and sending it, the turn is
            # abandoned and the next one starts.
            session.close()
            session.ensure()
            return reply(allow, answers)

        session._pending[approval_id] = (asker, racing, rule)
        restarted.append(session.answer(approval_id, True))

    with patch.object(chat_session, "BOOT_TIMEOUT", 0.5), patch.object(chat_session, "TURN_TIMEOUT", 0.5):
        run(session, CLAUDE, tree, restart_midway)
    assert restarted[0] is False  # the new process was never told "allow"


def test_a_stop_ends_the_turn_and_keeps_the_cli_session(tree):
    """Stopped while it waits on a person: the turn ends in an error, the
    process is gone, and the CLI's id stays for the next turn's `--resume`."""

    session = ChatSession(tree, write=True)
    halt = threading.Event()
    fixture = CLAUDE.replace("sys.stdin.readline()\ndef ask",
                             'sys.stdin.readline()\nprint(json.dumps({"type": "system", "subtype": "init", '
                             '"session_id": "cli-1"}), flush=True)\ndef ask', 1)
    command, events = run(session, fixture, tree, lambda e: threading.Timer(0.2, session.stop, args=(halt,)).start(),
                          halt)
    assert "--resume" not in command
    assert [e.kind for e in events] == ["approval", "error"]
    assert not session.alive and session.session_id == "cli-1"


def test_the_account_is_the_environment_at_creation(tmp_path, monkeypatch):
    """A restart goes on as the same login, whatever the server's env became."""

    monkeypatch.setenv("CODEX_HOME", "first")
    session = ChatSession(tmp_path, model="codex:test-model")
    monkeypatch.setenv("CODEX_HOME", "second")
    seen = []

    def spawn(command, **kwargs):
        seen.append(kwargs["env"]["CODEX_HOME"])
        raise OSError("stop here")

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        with pytest.raises(OSError):
            session.ensure()
    assert seen == ["first"]


def test_allowed_for_the_session_is_asked_no_more(tree):
    """A file tool is allowed by the tool, a command only by its exact text.
    The rule never reaches past the worktree."""

    session = ChatSession(tree, write=True)
    choices = {"a1": (True, "session"), "a3": (True, "once"), "a5": (True, "session"), "a7": (False, "once")}
    asked = []

    def answer(event):
        asked.append(event.meta["id"])
        allow, scope = choices[event.meta["id"]]
        assert session.answer(event.meta["id"], allow, scope)

    _, events = run(session, CLAUDE_ASKS, tree, answer)
    assert asked == ["a1", "a3", "a5", "a7"]
    unasked = {e.meta["id"]: e.meta["by"] for e in events if e.kind == "approval" and "by" in e.meta}
    assert unasked == {"a2": "session", "a4": "outside", "a6": "session"}
    assert events[-1].text == "allow,allow,allow,deny,allow,allow,deny"
    assert session.rules == [{"kind": "command", "tool": "Bash", "command": "pytest -q"},
                             {"kind": "file", "tool": "Write"}]

    session.reconfigure("opus", None)   # same session, same rules
    assert len(session.rules) == 2
    assert ChatSession(tree, write=True).rules == []   # a new one has none


def test_a_codex_command_is_allowed_only_in_the_same_cwd(tree):
    session = ChatSession(tree, model="codex:test-model", write=True)
    asked = []

    def answer(event):
        asked.append(event.meta["id"])
        assert session.answer(event.meta["id"], True, "session")

    _, events = run(session, CODEX_ASKS, tree, answer)
    assert asked == ["1", "3"]
    assert events[-1].text == "accept,accept,accept"


def test_only_what_a_rule_can_name_is_allowed_for_the_session(tree):
    session = ChatSession(tree, write=True)
    session._pending["x"] = (None, None, None)
    with pytest.raises(ValueError):
        session.answer("x", True, "session")
    assert "x" in session._pending   # still waiting for a plain answer


def test_a_stop_while_the_process_starts_sends_it_nothing(tree):
    """Round 1: pressed before `_spawn` set `_proc`, `stop()` had nothing to
    kill and the turn went on. The turn's `halt` catches it once it started."""

    session = ChatSession(tree, write=True)
    halt = threading.Event()
    real_popen = subprocess.Popen
    answers = '''import json, sys
sys.stdin.readline()
print(json.dumps({"type": "result", "result": "ran", "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''

    def spawn(command, **kwargs):
        halt.set()           # the person pressed stop while this was starting
        session.stop(halt)   # nothing to kill yet
        return real_popen([sys.executable, "-X", "utf8", "-c", answers], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        events = list(session.say("write it", halt))
    assert [e.kind for e in events] == ["error"]
    assert not session.alive


# `app-server` that answers `initialize` and then whatever `thread/resume` gets.
RESUMING = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read()
assert m["method"] == "thread/resume", m
if sys.argv[1] == "slow":
    sys.stdin.read()   # never answers; leaves once `close` shuts stdin
    sys.exit()
else:
    say({"id": m["id"], "error": {"code": -32600, "message": sys.argv[1]}})
m = read()
say({"id": m["id"], "result": {"thread": {"id": "new-thread"}}})
sys.stdin.read()
'''


@pytest.mark.parametrize("answer, starts_afresh", [
    ("no rollout found for thread id old-thread", True),
    ("slow", False),                         # no answer in time: not "gone"
    ("model is not supported", False),       # refused for another reason
])
def test_only_a_missing_thread_starts_a_new_codex_conversation(tmp_path, answer, starts_afresh):
    """Round 1: a resume that timed out started a new thread and dropped the
    conversation. Only Codex saying the thread is not there does that."""

    session = ChatSession(tmp_path, model="codex:test-model", resume="old-thread")
    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        return real_popen([sys.executable, "-X", "utf8", "-c", RESUMING, answer], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]), \
         patch.object(chat_session, "BOOT_TIMEOUT", 1):
        try:
            if starts_afresh:
                session.ensure()
                assert session.session_id == "new-thread" and session._lost == "old-thread"
            else:
                with pytest.raises(RuntimeError):
                    session.ensure()
                assert session.session_id == "old-thread" and session._lost is None
                assert not session.alive
        finally:
            session.close()


def test_a_stop_before_the_process_exists_cuts_a_slow_start(tmp_path):
    """Round 2: stopped before `Popen` returned, a Codex that did not answer
    `initialize` held the turn — and the worktree — for a boot timeout per
    request. The process is killed as soon as it exists."""

    session = ChatSession(tmp_path, model="codex:test-model")
    halt = threading.Event()
    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        halt.set()           # the stop lands while the process is being created
        session.stop(halt)   # nothing to kill yet
        return real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn),          patch.object(chat_session, "cli_command", side_effect=lambda name: [name]),          patch.object(chat_session, "BOOT_TIMEOUT", 10):
        started = time.monotonic()
        with pytest.raises(RuntimeError):   # `보내지 못했다` or `닫혔다`: either way at once
            list(session.say("x", halt))
        assert time.monotonic() - started < 5
    assert not session.alive


def test_a_late_stop_for_another_turn_leaves_this_one_running(tree):
    """Round 3: a stop that read its turn as running, then ran after the next
    turn had started on the same session, killed that next turn's process."""

    session = ChatSession(tree, write=True)
    ended = threading.Event()   # the earlier turn's stop
    ended.set()

    def answer(event):
        session.stop(ended)
        time.sleep(0.3)         # a kill would have landed by now
        session.answer(event.meta["id"], True)

    _, events = run(session, CLAUDE, tree, answer, threading.Event())
    assert events[-1].kind == "done" and events[-1].text == "allow,deny"


CODEX_BYPASS = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read()
assert (m["params"]["sandbox"], m["params"]["approvalPolicy"]) == ("danger-full-access", "never"), m
say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
m = read(); say({"id": m["id"], "result": {"turn": {}}})
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "ran"}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
'''


CLAUDE_ANSWERS = '''import json, sys
sys.stdin.readline()
print(json.dumps({"type": "result", "result": "ran", "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''


def test_bypass_opens_both_hosts_without_asking(tree):
    command, _ = run(ChatSession(tree, write=True, bypass=True), CLAUDE_ANSWERS, tree)
    assert command[command.index("--permission-mode") + 1] == "bypassPermissions"
    _, events = run(ChatSession(tree, model="codex:m", write=True, bypass=True), CODEX_BYPASS, tree)
    assert events[-1].kind == "done" and events[-1].text == "ran"
    assert not ChatSession(tree, bypass=True).bypass   # a read session never bypasses


# The turn's result comes before the steered message was taken in: the CLI
# answers it as one more turn, and that is still this turn.
CLAUDE_STEER = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
first = json.loads(sys.stdin.readline())
say({"type": "user", "isReplay": True, "message": first["message"]})
say({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "x"}}]}})
say({"type": "result", "result": "one", "session_id": "cli-1"})
m = json.loads(sys.stdin.readline())
say({"type": "user", "isReplay": True, "message": m["message"]})
say({"type": "result", "result": "two:" + m["message"]["content"][0]["text"], "session_id": "cli-1"})
sys.stdin.read()
'''


def test_a_steer_after_the_last_step_is_still_this_turn(tree):
    session = ChatSession(tree, write=True, bypass=True)
    assert not session.steer("early")   # no turn runs
    steered = []
    _, events = run(session, CLAUDE_STEER, tree,
                    each=lambda e: e.kind == "tool" and steered.append(session.steer("more")))
    assert steered == [True]
    assert [e.kind for e in events].count("done") == 1 and events[-1].text == "two:more"
    assert not session.steer("late")


CODEX_STEER = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read(); say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
m = read(); say({"id": m["id"], "result": {"turn": {}}})
say({"method": "turn/started", "params": {"turn": {"id": "t-1"}}})
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


CODEX_QUESTION = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read(); say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
m = read(); say({"id": m["id"], "result": {"turn": {}}})
say({"method": "hook/completed", "params": {"run": {"eventName": "userPromptSubmit", "entries": [
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
    assert brief({"name": "Bash", "input": {"command": "git  status\n"}}) == "Bash · $ git status"
    assert brief({"name": "Read", "input": {"file_path": "a.py"}}) == "Read · a.py"
    assert brief({"name": "TodoWrite", "input": {}}) == "TodoWrite"
