"""Write sessions: every write the CLI wants reaches the person as an approval,
the answer goes back, and nothing outside the worktree is even asked about.

The CLIs are stand-in child processes that speak each host's protocol.
"""

import json
import os
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from agent import ChatSession, chat_session
from workspace import create
from agent import read_tools

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
assert (m["params"]["sandbox"], m["params"]["approvalPolicy"]) == ("read-only", "on-request"), m
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

CODEX_START = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read(); say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
m = read(); say({"id": m["id"], "result": {"turn": {}}})
'''

CODEX_ASKS = CODEX_START.replace('import json, sys\n', 'import json, sys\ntree, out = sys.argv[1], sys.argv[2]\n', 1) + '''said = []
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
    return create(repo, "task", linked=True)


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
        # From another thread, as the screen does, and after the turn deadline:
        # the turn is waiting on a person, not hanging.
        time.sleep(0.5)
        answered.append(session.answer(approval_id, True))
        answered.append(session.answer(approval_id, True))  # once only

    def answer(event):
        threading.Thread(target=later, args=(event.meta["id"],)).start()

    # Starting Python on Windows can exceed 200ms; the short turn deadline
    # still proves that waiting for a person's answer does not time out.
    with patch.object(chat_session, "BOOT_TIMEOUT", 2.0), patch.object(chat_session, "TURN_TIMEOUT", 0.2):
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
    assert command[:4] == ["codex", "app-server", "--enable", "default_mode_request_user_input"]
    approvals = [e for e in events if e.kind == "approval"]
    # the command ran in a cwd outside the worktree: declined unasked
    assert [(e.meta["id"], e.meta["tool"], e.meta.get("by")) for e in approvals] == [
        ("100", "fileChange", None), ("101", "command", "outside")]
    assert events[-1].kind == "done" and events[-1].text == "accept,decline,True"
    assert events[-1].meta["session_id"] == "th-1"


def test_writes_open_in_repository_roots_and_refuse_non_repositories(tree, tmp_path):
    repo = tree.parent.parent / "demo"
    elsewhere = tree.parent.parent / "orca" / "task"   # someone else's worktree
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "other", str(elsewhere)], check=True)
    for path in (repo, elsewhere):
        ChatSession(path, write=True)
        ChatSession(path)  # reading there is fine
    with pytest.raises(ValueError):
        ChatSession(tmp_path, write=True)


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


@pytest.mark.parametrize("model", ["opus", "codex:test-model"])
@pytest.mark.parametrize("fast", [False, True])
def test_native_fast_mode_is_explicit_and_reconfiguration_preserves_the_session(tmp_path, model, fast):
    session = ChatSession(tmp_path, model=model, fast=fast, resume="existing-thread")
    commands = []

    def spawn(command, **kwargs):
        commands.append(command)
        raise OSError("Captured launch without starting a real model")

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        with pytest.raises(OSError):
            session.ensure()
    if model.startswith("codex:"):
        assert f'service_tier="{"fast" if fast else "default"}"' in commands[0]
        calls = []
        session._call = lambda method, params: calls.append((method, params)) or {"thread": {"id": "existing-thread"}}
        session._send = lambda request: True
        session._open_thread()
        assert calls[-1][1]["serviceTier"] == ("priority" if fast else "default")
    else:
        command = commands[0]
        assert json.loads(command[command.index("--settings") + 1])["fastMode"] is fast
    session.reconfigure(model, "high", not fast)
    assert session.fast is not fast and session.session_id == "existing-thread" and session.effort == "high"


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
m = read()
assert m["params"]["approvalPolicy"] == "never", m
assert m["params"]["sandboxPolicy"] == {"type": "dangerFullAccess"}, m
say({"id": m["id"], "result": {"turn": {}}})
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
    assert "--dangerously-skip-permissions" in command
    _, events = run(ChatSession(tree, model="codex:m", write=True, bypass=True), CODEX_BYPASS, tree)
    assert events[-1].kind == "done" and events[-1].text == "ran"
    assert not ChatSession(tree, bypass=True).bypass   # a read session never bypasses


CODEX_SOURCE = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); assert m["params"]["capabilities"]["experimentalApi"]
say({"id": m["id"], "result": {}}); read()
m = read(); assert m["method"] == "config/read"
say({"id": m["id"], "result": {"config": {"mcp_servers": {"private": {"enabled": True}}}}})
m = read(); p = m["params"]
assert p["approvalPolicy"] == "never" and p["sandbox"] == "read-only"
assert p["config"]['mcp_servers.private.enabled'] is False
assert {t["name"] for t in p["dynamicTools"]} == {"repo_read", "repo_glob", "repo_grep"}
assert all(t["type"] == "function" for t in p["dynamicTools"])
say({"id": m["id"], "result": {"thread": {"id": "source-1"}}})
m = read(); assert m["params"]["approvalPolicy"] == "never"
assert m["params"]["sandboxPolicy"]["type"] == "readOnly"
say({"id": m["id"], "result": {"turn": {}}})
say({"id": 100, "method": "item/tool/call", "params": {"tool": "repo_read", "arguments": {"path": "source.py"}}})
assert read()["result"] == {"contentItems": [{"type": "inputText", "text": "1: safe"}], "success": True}
say({"id": 101, "method": "item/tool/call", "params": {"tool": "shell", "arguments": {}}})
assert read()["result"]["success"] is False
say({"id": 102, "method": "item/permissions/requestApproval", "params": {}})
assert read()["result"] == {"permissions": {}, "scope": "turn"}
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "read safely"}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
'''


def test_codex_source_profile_has_no_shell_connectors_or_permission_dialog(tree):
    def bounded_call(repo, tool, args):
        if tool == "repo_read" and args == {"path": "source.py"}:
            return "1: safe"
        raise ValueError("not a read tool")

    session = ChatSession(tree, model="codex:m", tools="Read,Glob,Grep", bypass=True)
    with patch.object(read_tools, "call", side_effect=bounded_call):
        command, events = run(session, CODEX_SOURCE, tree)
    for feature in ("shell_tool", "unified_exec", "apps", "plugins", "computer_use", "request_permissions_tool"):
        assert command[command.index(feature) - 1] == "--disable"
    assert not session.bypass and not session._pending
    assert not any(e.kind in {"approval", "error"} for e in events)
    assert events[-1].kind == "done" and events[-1].text == "read safely"


def test_source_tools_read_tracked_text_but_not_private_ignored_or_outside_files(tree, tmp_path):
    (tree / "source.py").write_text("first\nneedle\nlast\n", encoding="utf-8")
    (tree / ".env").write_text("private", encoding="utf-8")
    (tree / "private.txt").write_text("private", encoding="utf-8")
    subprocess.run(["git", "-C", str(tree), "add", "source.py", ".env"], check=True)
    assert "2: needle" in read_tools.call(tree, "repo_read", {"path": "source.py", "offset": 2, "limit": 1})
    assert read_tools.call(tree, "repo_glob", {"pattern": "*"}) == "source.py"
    assert read_tools.call(tree, "repo_grep", {"text": "needle"}) == "source.py:2: needle"
    for path in (".env", "private.txt", "../elsewhere.txt", str(tmp_path / "private.txt")):
        with pytest.raises(ValueError):
            read_tools.call(tree, "repo_read", {"path": path})
    for tool, args in (("shell", {}), ("repo_read", {"path": "source.py", "limit": 501}),
                       ("repo_grep", {"text": ""}), ("repo_read", {"path": "source.py", "offset": True})):
        with pytest.raises(ValueError):
            read_tools.call(tree, tool, args)
    with patch.object(read_tools.Path, "resolve", side_effect=[tree.resolve(), tmp_path / "private.txt"]):
        # A tracked symlink/junction cannot escape the worktree.
        with pytest.raises(ValueError):
            read_tools.call(tree, "repo_read", {"path": "source.py"})


CODEX_VERIFICATION = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}}); read()
m = read(); assert m["method"] == "config/read"
say({"id": m["id"], "result": {"config": {"mcp_servers": {"private": {"enabled": True}}}}})
m = read(); p = m["params"]
assert p["approvalPolicy"] == "never" and p["sandbox"] == "workspace-write"
assert p["config"]["mcp_servers.private.enabled"] is False
assert p["config"]["sandbox_workspace_write.network_access"] is True
assert len(p["config"]["sandbox_workspace_write.writable_roots"]) == 1
assert "dynamicTools" not in p
say({"id": m["id"], "result": {"thread": {"id": "verification-1"}}})
m = read(); p = m["params"]
assert p["approvalPolicy"] == "never"
assert p["sandboxPolicy"]["type"] == "workspaceWrite"
assert p["sandboxPolicy"]["networkAccess"] is True
assert len(p["sandboxPolicy"]["writableRoots"]) == 2
say({"id": m["id"], "result": {"turn": {}}})
for rid, method in ((100, "item/commandExecution/requestApproval"), (101, "item/fileChange/requestApproval")):
    say({"id": rid, "method": method, "params": {"command": "unsafe escalation", "cwd": sys.argv[2]}})
    assert read()["result"]["decision"] == "decline"
say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "execution enabled"}}})
say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
sys.stdin.read()
'''


def test_cloud_verification_enables_execution_and_files_without_unrestricted_access(tree, tmp_path):
    artifacts = tmp_path / "verification"
    chat = ChatSession(tree, model="codex:m", verification=artifacts)
    command, events = run(chat, CODEX_VERIFICATION, tree)
    assert not chat.write and not chat.bypass and not chat.source_only
    assert "Bash" in chat.tools and "Write" in chat.tools and "Edit" in chat.tools
    for feature in ("shell_tool", "unified_exec"):
        assert command[command.index(feature) - 1] == "--enable"
    assert chat._env["WIKI_VERIFICATION_ARTIFACTS"] == str(artifacts.resolve())
    assert chat._env["TEMP"] == str(artifacts.resolve() / "tmp")
    assert (artifacts / "tmp").is_dir()
    assert chat._outside(artifacts / "check.py") == ""
    assert chat._outside(tmp_path / "outside.py")
    assert not any(e.kind == "approval" for e in events) and not chat._pending
    assert events[-1].kind == "done" and events[-1].text == "execution enabled"
    with pytest.raises(ValueError):
        ChatSession(tmp_path, model="codex:m", verification=artifacts)
    claude = ChatSession(tree, verification=artifacts)
    command, events = run(claude, CLAUDE, tree)
    assert command[command.index("--allowedTools") + 1] == ChatSession.VERIFICATION_TOOLS
    assert command[command.index("--permission-mode") + 1] == "default"
    assert events[-1].text == "allow,deny"


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


@pytest.mark.parametrize("terminal", ["task_notification", "task_updated"])
def test_background_completion_drains_followup_and_reports_progress(tree, terminal):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "description": "Calibration"})
say({"type": "system", "subtype": "task_progress", "task_id": "bg-1", "description": "40 percent", "usage": {"duration_ms": 1200}})
say({"type": "result", "result": "Waiting for calibration", "session_id": "cli-1"})
say({"type": "system", "subtype": "TERMINAL", "task_id": "bg-1", "status": "completed", "patch": {"status": "completed"}})
say({"type": "system", "subtype": "task_updated", "task_id": "bg-1", "patch": {"description": "Output available"}})
say({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "Calibration finished: 10/10"}]}})
say({"type": "assistant", "message": {"content": [{"type": "text", "text": "Picking up calibration results"}]}})
say({"type": "result", "result": "Calibration verified", "session_id": "cli-1"})
sys.stdin.read()
'''.replace("TERMINAL", terminal)
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert [e.text for e in events if e.kind == "done"] == ["Calibration verified"]
    statuses = [e.meta.get("status") for e in events if e.meta.get("task_id") == "bg-1"]
    assert statuses == ["started", "running", "completed", "completed"]
    assert any("40 percent" in e.text and "1.2s" in e.text for e in events)
    assert any("Calibration finished: 10/10" in e.text for e in events)


def test_collected_background_tasks_do_not_leave_phantom_followups_on_the_next_turn(tree):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
for turn in (1, 2):
    sys.stdin.readline()
    for i in range(47):
        task = f"bg-{turn}-{i}"
        say({"type": "system", "subtype": "task_started", "task_id": task})
        say({"type": "system", "subtype": "task_updated", "task_id": task, "patch": {"status": "completed"}})
    say({"type": "result", "origin": {"kind": "human"}, "result": f"Finished {turn}", "session_id": "cli-1"})
sys.stdin.read()
'''
    session = ChatSession(tree, write=True, bypass=True)
    real_popen = subprocess.Popen
    try:
        with patch.object(chat_session.subprocess, "Popen", lambda _, **kw: real_popen(
                [sys.executable, "-X", "utf8", "-c", fixture], **kw)), \
             patch.object(chat_session, "cli_command", side_effect=lambda name: [name]), \
             patch.object(chat_session, "TURN_TIMEOUT", .2):
            first = list(session.say("First correction"))
            assert [e.text for e in first if e.kind == "done"] == ["Finished 1"]
            second = list(session.say("Next correction"))
            assert [e.text for e in second if e.kind == "done"] == ["Finished 2"]
            assert session.alive
    finally:
        session.close()


def test_foreground_task_notifications_finish_successive_turns_without_followups(tree):
    # Claude 2.1.289 reports foreground Bash lifecycle with the same terminal
    # notification as background work. It emits no synthetic answer afterward.
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
for turn in (1, 2, 3):
    message = json.loads(sys.stdin.readline())
    say({"type": "user", "isReplay": True, "message": message["message"]})
    for i in range(3):
        task = f"fg-{turn}-{i}"
        say({"type": "system", "subtype": "task_started", "task_id": task,
             "task_type": "local_bash", "is_backgrounded": False})
        say({"type": "system", "subtype": "task_notification", "task_id": task, "status": "completed"})
    say({"type": "result", "num_turns": 4, "terminal_reason": "completed",
         "result": f"Correction {turn}", "session_id": "cli-1"})
sys.stdin.read()
'''
    session = ChatSession(tree, write=True, bypass=True)
    real_popen = subprocess.Popen
    try:
        with patch.object(chat_session.subprocess, "Popen", lambda _, **kw: real_popen(
                [sys.executable, "-X", "utf8", "-c", fixture], **kw)), \
             patch.object(chat_session, "cli_command", side_effect=lambda name: [name]), \
             patch.object(chat_session, "TURN_TIMEOUT", .2):
            for turn in (1, 2, 3):
                events = list(session.say(f"Correct round {turn}"))
                assert [e.text for e in events if e.kind == "done"] == [f"Correction {turn}"]
                assert not any("waiting for" in e.text for e in events)
            assert session.alive
    finally:
        session.close()


def test_foreground_task_moved_to_background_still_drains_its_followup(tree):
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": False})
say({"type": "system", "subtype": "task_updated", "task_id": "bg-1", "patch": {"is_backgrounded": True}})
say({"type": "result", "origin": {"kind": "human"}, "result": "Waiting", "session_id": "cli-1"})
say({"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "completed"})
say({"type": "result", "origin": {"kind": "task-notification"}, "result": "Finished", "session_id": "cli-1"})
sys.stdin.read()
'''
    _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert [e.text for e in events if e.kind == "done"] == ["Finished"]


def test_completions_the_turn_took_in_owe_no_followup(tree):
    # Claude 2.1.296: a background task that ends before the answer is read
    # inside the turn (queue `remove`), and no follow-up comes. Waiting for one
    # kept the worktree held and the review loop stalled until a person stopped it.
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": True})
say({"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "completed"})
say({"type": "result", "origin": {"kind": "human"}, "result": "Fixed and pushed", "session_id": "cli-1"})
sys.stdin.read()
'''
    session = ChatSession(tree, write=True, bypass=True)
    with patch.object(chat_session, "FOLLOWUP_GRACE", .3), patch.object(chat_session, "TURN_TIMEOUT", 30):
        started = time.monotonic()
        _, events = run(session, fixture, tree)
    assert time.monotonic() - started < 10
    assert events[-1].kind == "done" and events[-1].text == "Fixed and pushed" and not events[-1].meta["error"]


def test_a_background_task_silent_past_the_turn_timeout_is_still_awaited(tree):
    # A suite that prints nothing for ten minutes once tripped the turn's clock:
    # the CLI was killed and the task orphaned. Its own end closes the wait.
    fixture = '''import json, sys, time
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": True})
say({"type": "result", "result": "I'll commit once the suite passes", "session_id": "cli-1"})
time.sleep(1)
say({"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "completed"})
say({"type": "result", "origin": {"kind": "task-notification"}, "result": "Committed", "session_id": "cli-1"})
sys.stdin.read()
'''
    with patch.object(chat_session, "TURN_TIMEOUT", .2):
        _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert events[-1].kind == "done" and events[-1].text == "Committed" and not events[-1].meta["error"]


def test_a_stopped_background_task_owes_no_followup(tree):
    # Claude 2.1.291: `TaskStop` or `stop_task` reports killed, then stopped,
    # and injects no answer. Waiting for one hung until the clock killed it.
    fixture = '''import json, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": True})
say({"type": "system", "subtype": "task_updated", "task_id": "bg-1", "patch": {"status": "killed"}})
say({"type": "system", "subtype": "task_notification", "task_id": "bg-1", "status": "stopped"})
say({"type": "result", "result": "Stopped it", "session_id": "cli-1"})
sys.stdin.read()
'''
    with patch.object(chat_session, "TURN_TIMEOUT", 5):
        started = time.monotonic()
        _, events = run(ChatSession(tree, write=True, bypass=True), fixture, tree)
    assert events[-1].kind == "done" and events[-1].text == "Stopped it"
    assert time.monotonic() - started < 4


def test_a_closing_answer_ends_the_turn_and_takes_left_over_background_work_down(tree, tmp_path):
    # The work is reported done while a watcher still runs: the turn ends at
    # the report, so the review can start, and the watcher's process goes too.
    survived = tmp_path / "survived"
    fixture = '''import json, subprocess, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
subprocess.Popen([sys.executable, "-c", "import pathlib, sys, time; time.sleep(1.5); pathlib.Path(sys.argv[1]).touch()",
                  SURVIVED])
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": True})
say({"type": "result", "result": "Done.\\n```done-report\\n[]\\n```", "session_id": "cli-1"})
sys.stdin.read()
'''.replace("SURVIVED", repr(str(survived)))
    session = ChatSession(tree, write=True, bypass=True)
    session.settled = lambda answer: "```done-report" in answer
    with patch.object(chat_session, "TURN_TIMEOUT", 30):
        started = time.monotonic()
        _, events = run(session, fixture, tree)
    assert time.monotonic() - started < 10
    assert events[-1].kind == "done" and events[-1].text.startswith("Done.") and not events[-1].meta["error"]
    assert any(e.meta.get("task_id") == "bg-1" and e.meta.get("status") == "stopped" for e in events)
    assert not session.alive
    if os.name == "nt":   # the job: elsewhere the CLI's own exit is trusted
        time.sleep(2.5)
        assert not survived.exists()


# A stand-in CLI whose background child holds the inherited stdout open, so the
# pipe never ends while the child lives.
HOLDS_PIPE = '''import json, subprocess, sys
say = lambda m: print(json.dumps(m), flush=True)
sys.stdin.readline()
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": True})
say({"type": "result", "result": "Waiting for the task", "session_id": "cli-1"})
EXIT
'''


def test_a_stop_ends_a_turn_waiting_on_a_child_that_holds_the_pipe(tree):
    session, halt, events = ChatSession(tree, write=True, bypass=True), threading.Event(), []
    waiting = threading.Event()

    def consume():
        for event in session.say("go", halt):
            events.append(event)
            if "waiting for" in event.text:
                waiting.set()

    real_popen = subprocess.Popen
    with patch.object(chat_session.subprocess, "Popen", lambda _, **kw: real_popen(
            [sys.executable, "-X", "utf8", "-c", HOLDS_PIPE.replace("EXIT", "sys.stdin.read()")], **kw)), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        turn = threading.Thread(target=consume, daemon=True)
        turn.start()
        try:
            assert waiting.wait(120)
            halt.set()
            session.stop(halt)
            turn.join(5)
            assert not turn.is_alive() and events[-1].kind == "error"
        finally:
            session.close()


def test_a_provider_that_exits_while_its_child_holds_the_pipe_ends_the_turn(tree):
    with patch.object(chat_session, "TURN_TIMEOUT", 30):
        started = time.monotonic()
        _, events = run(ChatSession(tree, write=True, bypass=True), HOLDS_PIPE.replace("EXIT", ""), tree)
    assert events[-1].kind == "error" and time.monotonic() - started < 10


def test_a_closing_answer_does_not_drop_a_steer_already_read(tree):
    fixture = '''import json, sys, time
say = lambda m: print(json.dumps(m), flush=True)
prompt = json.loads(sys.stdin.readline())
say({"type": "user", "isReplay": True, "message": prompt["message"]})
say({"type": "system", "subtype": "task_started", "task_id": "bg-1", "is_backgrounded": True})
steer = json.loads(sys.stdin.readline())
say({"type": "user", "isReplay": True, "message": steer["message"]})
say({"type": "result", "result": "Old answer\\n```done-report\\n[]\\n```", "session_id": "cli-1"})
time.sleep(.4)
say({"type": "system", "subtype": "task_updated", "task_id": "bg-1", "patch": {"status": "completed"}})
say({"type": "result", "result": "Steered request completed", "session_id": "cli-1"})
sys.stdin.read()
'''
    session = ChatSession(tree, write=True, bypass=True)
    session.settled = lambda answer: "```done-report" in answer
    _, events = run(session, fixture, tree, each=lambda e: session.steer("One more thing")
                    if e.meta.get("status") == "started" else None)
    assert [e.text for e in events if e.kind == "done"] == ["Steered request completed"]


def test_a_close_waits_for_a_stop_still_using_the_job(tree):
    # Windows hands a closed handle's number to the next job: a close that
    # ran while a stop held the handle let that stop kill an unrelated job.
    order, inside = [], threading.Event()

    def slow_kill(job):
        inside.set()
        time.sleep(.5)
        order.append("kill")

    def close(job):
        order.append("close")
        terminated(job)   # the real one: the job's handle is not left open

    terminated = chat_session.terminated
    session, halt, real_popen = ChatSession(tree, write=True, bypass=True), threading.Event(), subprocess.Popen
    with patch.object(chat_session.subprocess, "Popen", lambda _, **kw: real_popen(
            [sys.executable, "-X", "utf8", "-c", "import sys; sys.stdin.read()"], **kw)), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]), \
         patch.object(chat_session, "killed", side_effect=slow_kill), \
         patch.object(chat_session, "terminated", side_effect=close):
        session.ensure()
        session._halt = halt
        halt.set()
        stopper = threading.Thread(target=session.stop, args=(halt,))
        stopper.start()
        assert inside.wait(120)
        session.close()
        stopper.join(5)
    assert order == ["kill", "close"]


# Starts a child that marks a file 1.5 s later unless its tree is killed first.
MARKS = '''import json, subprocess, sys
subprocess.Popen([sys.executable, "-c", "import pathlib, sys, time; time.sleep(1.5); pathlib.Path(sys.argv[1]).touch()",
                  MARKED], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(json.dumps({"type": "system", "subtype": "task_started", "task_id": "bg-1"}), flush=True)
sys.stdin.read()
'''


@pytest.mark.skipif(os.name != "nt", reason="the job is Windows only")
def test_a_reconnect_after_the_provider_died_takes_down_what_it_started(tree, tmp_path):
    marked = tmp_path / "marked"
    session, real_popen = ChatSession(tree, write=True, bypass=True), subprocess.Popen
    with patch.object(chat_session.subprocess, "Popen", lambda _, **kw: real_popen(
            [sys.executable, "-X", "utf8", "-c", MARKS.replace("MARKED", repr(str(marked)))], **kw)), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        try:
            session.ensure()
            assert session._events.get(timeout=10)["task_id"] == "bg-1"
            session._proc.kill()
            session._proc.wait(5)
            session.ensure()   # the old job is gone here, not overwritten
        finally:
            session.close()
    time.sleep(2.5)
    assert not marked.exists()


@pytest.mark.skipif(os.name != "nt", reason="the job is Windows only")
def test_nothing_the_provider_starts_escapes_its_job(tree, tmp_path):
    # Assigned a second late: a provider that ran from its spawn would have
    # started its child outside the job by then.
    marked = tmp_path / "marked"
    session, real_popen = ChatSession(tree, write=True, bypass=True), subprocess.Popen
    late = chat_session.contained
    with patch.object(chat_session.subprocess, "Popen", lambda _, **kw: real_popen(
            [sys.executable, "-X", "utf8", "-c", MARKS.replace("MARKED", repr(str(marked)))], **kw)), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]), \
         patch.object(chat_session, "contained", side_effect=lambda proc: time.sleep(1) or late(proc)):
        try:
            session.ensure()
            assert session._events.get(timeout=10)["task_id"] == "bg-1"
        finally:
            session.close()
    time.sleep(2.5)
    assert not marked.exists()
