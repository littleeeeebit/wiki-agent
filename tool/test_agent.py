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
    """Drive one turn against a stand-in CLI. `answer` sees each approval."""

    real_popen, commands, events = subprocess.Popen, [], []

    def spawn(command, **kwargs):
        commands.append(command)
        return real_popen([sys.executable, "-X", "utf8", "-c", fixture, str(tree),
                           str(tree.parent / "elsewhere.txt")], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        for event in session.say("write it"):
            events.append(event)
            if event.kind == "approval" and answer:
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
    assert [(e.meta["id"], e.meta["tool"]) for e in approvals] == [("r1", "Write")]
    assert any(e.kind == "tool" and "작업트리 밖" in e.text for e in events)
    assert events[-1].kind == "done" and events[-1].text == "allow,deny"
    assert {(e.session_id, e.parent_id) for e in events} == {(session.id, "coordinator")}
    assert answered == [True, False]


def test_a_read_session_refuses_without_asking(tmp_path):
    _, events = run(ChatSession(tmp_path), CLAUDE, tmp_path)
    assert not [e for e in events if e.kind == "approval"]
    assert events[-1].text == "deny,deny"


def test_codex_write_runs_app_server_and_answers_by_id(tree):
    session = ChatSession(tree, model="codex:test-model", write=True)
    command, events = run(session, CODEX, tree, lambda e: session.answer(e.meta["id"], True))
    assert command == ["codex", "app-server"]
    approvals = [e for e in events if e.kind == "approval"]
    assert [(e.meta["id"], e.meta["tool"]) for e in approvals] == [("100", "fileChange")]
    # the command ran in a cwd outside the worktree: declined unasked
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
        asker, reply = session._pending[approval_id]

        def racing(allow):
            # Between `answer` taking the reply and sending it, the turn is
            # abandoned and the next one starts.
            session.close()
            session.ensure()
            return reply(allow)

        session._pending[approval_id] = (asker, racing)
        restarted.append(session.answer(approval_id, True))

    with patch.object(chat_session, "BOOT_TIMEOUT", 1), patch.object(chat_session, "TURN_TIMEOUT", 1):
        run(session, CLAUDE, tree, restart_midway)
    assert restarted[0] is False  # the new process was never told "allow"


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
