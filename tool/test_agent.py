"""Write sessions: every write the CLI wants reaches the person as an approval,
the answer goes back, and nothing outside the worktree is even asked about.

The CLIs are stand-in child processes that speak each host's protocol.
"""

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
say({"id": 102, "method": "item/tool/requestUserInput", "params": {}})
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


def run(session, fixture, tree, answer=None):
    """Drive one turn against a stand-in CLI. `answer` sees each approval a
    person is asked."""

    real_popen, commands, events = subprocess.Popen, [], []

    def spawn(command, **kwargs):
        commands.append(command)
        return real_popen([sys.executable, "-X", "utf8", "-c", fixture, str(tree),
                           str(tree.parent / "elsewhere.txt")], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        for event in session.say("write it"):
            events.append(event)
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
    assert command == ["codex", "app-server"]
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

        def racing(allow):
            # Between `answer` taking the reply and sending it, the turn is
            # abandoned and the next one starts.
            session.close()
            session.ensure()
            return reply(allow)

        session._pending[approval_id] = (asker, racing, rule)
        restarted.append(session.answer(approval_id, True))

    with patch.object(chat_session, "BOOT_TIMEOUT", 1), patch.object(chat_session, "TURN_TIMEOUT", 1):
        run(session, CLAUDE, tree, restart_midway)
    assert restarted[0] is False  # the new process was never told "allow"


def test_a_stop_ends_the_turn_and_keeps_the_cli_session(tree):
    """Stopped while it waits on a person: the turn ends in an error, the
    process is gone, and the CLI's id stays for the next turn's `--resume`."""

    session = ChatSession(tree, write=True)
    fixture = CLAUDE.replace("sys.stdin.readline()\ndef ask",
                             'sys.stdin.readline()\nprint(json.dumps({"type": "system", "subtype": "init", '
                             '"session_id": "cli-1"}), flush=True)\ndef ask', 1)
    command, events = run(session, fixture, tree, lambda e: threading.Timer(0.2, session.stop).start())
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
        halt.set()       # the person pressed stop while this was starting
        session.stop()   # nothing to kill yet
        return real_popen([sys.executable, "-X", "utf8", "-c", answers], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        events = list(session.say("write it", halt))
    assert [e.kind for e in events] == ["error"]
    assert not session.alive


# `app-server` that answers `initialize` and then whatever `thread/resume` gets.
RESUMING = '''import json, sys, time
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read()
assert m["method"] == "thread/resume", m
if sys.argv[1] == "slow":
    time.sleep(30)
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
