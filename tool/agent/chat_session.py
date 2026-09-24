"""Wrap Claude's live process and Codex's explicit resume as one conversation.

The two hosts keep a conversation in different ways and the screen must not
have to know which. What reaches the screen is read by a person, so those
strings stay Korean.

A session reads by default. `write=True` opens one in a worktree, where every
write the CLI wants to make arrives as an `approval` event and waits for
`answer`. Codex asks only through `app-server`, so its write session runs that
instead of `exec`.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import queue
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from common import worktree_home

from .chat_local import cli_command

# Something opened in a browser that edits files is not a chat, it is a remote
# shell. So `Edit` and `Write` are not on the list.
#
# ponytail: `Bash` is. Calling `git log` and `tool/*.py` needs it, and without
# it four of the channels are half of themselves. Being a shell it can write
# in principle, but the target repository's `permissions.deny` still applies —
# `git reset --hard`, `sed -i`, secrets. To close it further, dig per-tool
# endpoints instead of Bash.
READ_TOOLS = "Bash,Read,Glob,Grep"

# A write session. Everything outside `ASK_FREE` goes through an approval
# event, so `Bash` here is a shell a person approves command by command.
WRITE_TOOLS = "Bash,Read,Glob,Grep,Edit,Write"
ASK_FREE = "Read,Glob,Grep"
# Claude's tools that name the file they write.
WRITES_PATH = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
DECLINED = "The person declined this."

BOOT_TIMEOUT = 120.0   # the first turn is slow: hooks, and loading
TURN_TIMEOUT = 600.0


@dataclass
class Event:
    """Only what the screen needs to know. Every other event kind is dropped here.

    `session_id` is this program's id for the session, fixed at construction.
    The CLI's own id is known only after its first reply and changes on a
    reconnect, so it cannot tell whose a late event is; it rides in
    `done.meta` for `--resume`. `parent_id` is the session that started this
    one — room for a coordinator, `None` until there is one.
    """

    kind: str          # "delta" | "tool" | "approval" | "done" | "error"
    text: str = ""
    meta: dict = field(default_factory=dict)
    session_id: str = ""
    parent_id: str | None = None


def our_worktree(repo: Path) -> bool:
    """Is `repo` the top of a worktree in `worktree_home` of its own repository?

    Not just any `git worktree`: Orca and `claude -w` make those too, and they
    are somebody's work. The original checkout never passes — it is not inside
    its own `-worktrees` folder.
    """

    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
         "--show-toplevel", "--git-common-dir"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    lines = done.stdout.splitlines()
    if done.returncode or len(lines) != 2:
        return False
    top, common = (Path(line).resolve() for line in lines)
    return (top == Path(repo).resolve() and common.name == ".git"
            and top.parent == worktree_home(common.parent))


def _blocks(message: dict) -> list[dict]:
    content = (message or {}).get("content")
    return content if isinstance(content, list) else []


class ChatSession:
    """One channel's live conversation. One turn runs at a time."""

    def __init__(self, repo: Path, tools: str = READ_TOOLS,
                 system: str = "", model: str | None = None,
                 effort: str | None = None, resume: str | None = None,
                 isolated: bool = False, write: bool = False,
                 parent_id: str | None = None) -> None:
        self.repo = Path(repo)
        # Writes go to a worktree, never to the checkout a person works in.
        if write and not our_worktree(self.repo):
            raise ValueError(f"쓰기 세션은 workspace 가 만든 작업트리에서만 연다: {self.repo}")
        self.write = write
        self.tools = WRITE_TOOLS if write else tools
        self.id = uuid.uuid4().hex
        self.parent_id = parent_id
        # The CLI's login is whatever its environment points at (`CODEX_HOME`,
        # Claude's config). Held from here, so a restart or a `--resume` goes
        # on as the same account even if the server's environment changed.
        self._env = dict(os.environ)
        # A channel's character goes in as a system prompt. The first version
        # sent it as the opening turn and that one turn took two minutes — the
        # model reads the introduction and starts going through files. A
        # system prompt costs no turn and applies from the first utterance.
        self.system = system.strip()
        # The model and the effort are fixed when the process starts. Changing
        # either means `reconfigure` starting it again, and it reconnects with
        # `--resume` so the conversation is not lost.
        self.model = model or None
        self.effort = effort or None
        self.isolated = isolated
        self.session_id: str | None = resume
        self.model_name = ""
        self._resume: str | None = resume
        self._proc: subprocess.Popen | None = None
        self._events: queue.Queue[dict] = queue.Queue()
        self._turn = threading.Lock()
        self._start = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=20)
        # Approvals the CLI is waiting on: id -> (the process that asked, the
        # reply for `allow`). The reply goes to that process only.
        self._pending: dict[str, tuple] = {}
        self._stdin = threading.Lock()
        self._rpc_id = 0
        # Codex fileChange items by id, so an approval can see their paths.
        self._changes: dict[str, list[str]] = {}

    @property
    def is_codex(self) -> bool:
        return bool(self.model and self.model.startswith("codex:"))

    @property
    def app(self) -> bool:
        """A Codex write session: `codex app-server`, since `exec` cannot ask."""
        return self.is_codex and self.write

    # -- Lifetime -----------------------------------------------------------

    def _spawn(self) -> None:
        cmd = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--tools", self.tools,
            "--allowedTools", ASK_FREE if self.write else self.tools,
        ]
        if self.write:
            # Named, so a `defaultMode` of `acceptEdits` in someone's settings
            # cannot skip the question.
            cmd += ["--permission-mode", "default", "--permission-prompt-tool", "stdio"]
        if self.isolated:
            # `--bare` would skip the subscription login too. The sign-in is
            # kept; only the settings, hooks and tools are isolated.
            cmd += ["--setting-sources", "", "--settings", '{"disableAllHooks":true}',
                    "--strict-mcp-config", "--no-session-persistence"]
        if self.system:
            cmd += ["--system-prompt" if self.isolated else "--append-system-prompt", self.system]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        if self._resume:
            cmd += ["--resume", self._resume]
        if self.app:
            cmd = ["codex", "app-server"]
            self.model_name = self.model.removeprefix("codex:")
        elif self.is_codex:
            cmd = ["codex", "exec", "--model", self.model.removeprefix("codex:"),
                   "--json", "--sandbox", "read-only",
                   "-c", 'approval_policy="never"', "--disable", "multi_agent",
                   "-c", "developer_instructions=" + json.dumps(self.system, ensure_ascii=False)]
            if self.effort:
                cmd += ["-c", "model_reasoning_effort=" + json.dumps(self.effort)]
            if self.isolated:
                cmd += ["--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                        "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                        "--disable", "shell_tool", "--disable", "apps", "--disable", "plugins",
                        "--disable", "memories"]
            elif self._resume:
                cmd += ["resume", self._resume]
            cmd.append("-")
            self.model_name = self.model.removeprefix("codex:")
        self._stderr = deque(maxlen=20)
        cmd = [*cli_command(cmd[0]), *cmd[1:]]
        self._proc = subprocess.Popen(
            cmd, cwd=str(self.repo), env=self._env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
        threading.Thread(target=self._pump, args=(self._proc, self._events), daemon=True).start()
        threading.Thread(target=self._pump_stderr, args=(self._proc, self._stderr), daemon=True).start()

    def _pump(self, proc, events) -> None:
        # The previous process's exit notice must not land in the next
        # process's queue.
        assert proc.stdout
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                events.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        # With the process gone, whoever is waiting never wakes up. Close the
        # door for them.
        events.put({"type": "__closed__"})

    @staticmethod
    def _pump_stderr(proc, errors) -> None:
        if proc.stderr:
            for line in proc.stderr:
                errors.append(line)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure(self) -> None:
        with self._start:
            if not self.alive:
                # With a session to reconnect to, start again carrying its id.
                # Without one, this is a new conversation.
                self._resume = self.session_id
                self._events = queue.Queue()
                self._spawn()
                if self.app:
                    try:
                        self._open_thread()
                    except Exception:
                        self.close()
                        raise

    def _open_thread(self) -> None:
        """`app-server` holds no conversation until asked to start or resume one.

        `read-only` with `untrusted` is what makes Codex ask: anything but a
        known read-only command becomes an approval request.
        """

        self._call("initialize", {"clientInfo": {"name": "wiki-agent", "version": "0.1.0"}})
        self._send({"method": "initialized"})
        params = {"cwd": str(self.repo), "sandbox": "read-only", "approvalPolicy": "untrusted",
                  "model": self.model_name}
        if self.system:
            params["developerInstructions"] = self.system
        if self._resume:
            result = self._call("thread/resume", {"threadId": self._resume, **params})
        else:
            result = self._call("thread/start", params)
        self.session_id = result["thread"]["id"]

    def _request(self, method: str, params: dict) -> dict:
        self._rpc_id += 1
        return {"id": self._rpc_id, "method": method, "params": params}

    def _call(self, method: str, params: dict) -> dict:
        """One request before any turn, waited for by id. What else arrives is
        start-up chatter and is dropped."""

        message = self._request(method, params)
        if not self._send(message):
            raise RuntimeError(f"Codex 에 보내지 못했다: {method}")
        while True:
            try:
                reply = self._events.get(timeout=BOOT_TIMEOUT)
            except queue.Empty as exc:
                raise RuntimeError(f"Codex 응답 시간 초과: {method}") from exc
            if reply.get("type") == "__closed__":
                raise RuntimeError(f"Codex 가 닫혔다: {method}. {''.join(self._stderr)[-400:]}".strip())
            if reply.get("id") == message["id"] and "method" not in reply:
                if "error" in reply:
                    raise RuntimeError(f"Codex {method} 실패: {reply['error']}")
                return reply.get("result") or {}

    def _send(self, message: dict, proc=None) -> bool:
        """One line to the CLI — to `proc` when given, else the current one.
        Approvals are answered from another thread, so writes take turns."""

        with self._stdin:
            try:
                proc = proc or self._proc
                proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                proc.stdin.flush()
                return True
            except (AttributeError, BrokenPipeError, OSError, ValueError):
                return False

    def answer(self, approval_id: str, allow: bool) -> bool:
        """Answer an `approval` event. `False` when nothing waits under that id:
        answered already, or the process that asked is gone.

        Written to the process that asked, never to whatever runs now: after a
        restart a late "allow" would land on the new process, and Codex
        numbers its requests afresh in each one.
        """

        proc, reply = self._pending.pop(approval_id, (None, None))
        return proc is not None and self._send(reply(allow), proc)

    def _outside(self, path) -> str:
        """The path, resolved, when it lies outside this worktree. Else ``""``."""

        if not path:
            return ""
        target = Path(path)
        target = (target if target.is_absolute() else self.repo / target).resolve()
        root = self.repo.resolve()
        return "" if target == root or root in target.parents else str(target)

    def _approval(self, key: str, reply, tool: str, args: dict, text: str, outside: str) -> Event:
        """An approval event for the person, or a refusal nobody is asked about.

        Refused without asking: a write outside the worktree, and anything a
        read session is asked — it has no one to answer and would sit out the
        turn's deadline.
        """

        if outside or not self.write:
            self._send(reply(False))
            return Event("tool", f"거절 · 작업트리 밖: {outside}" if outside else f"거절 · {text}")
        self._pending[key] = (self._proc, reply)
        return Event("approval", text, {"id": key, "tool": tool, "input": args})

    def _codex_asks(self, rid, method: str, params: dict) -> Event | None:
        """A request from `app-server`. Two kinds are approvals; the rest are
        refused so the turn does not wait on an answer that never comes."""

        def reply(allow, rid=rid):
            return {"id": rid, "result": {"decision": "accept" if allow else "decline"}}

        reason = params.get("reason")
        if method == "item/commandExecution/requestApproval":
            command = str(params.get("command") or "")
            return self._approval(str(rid), reply, "command",
                                  {"command": command, "cwd": params.get("cwd"), "reason": reason},
                                  command[:300], self._outside(params.get("cwd")))
        if method == "item/fileChange/requestApproval":
            paths = self._changes.get(str(params.get("itemId")), [])
            outside = next((o for o in map(self._outside, paths) if o), "")
            return self._approval(str(rid), reply, "fileChange", {"paths": paths, "reason": reason},
                                  ("파일 변경 · " + ", ".join(paths))[:300], outside)
        self._send({"id": rid, "error": {"code": -32601, "message": f"{method} is not handled here"}})
        return None

    def reconfigure(self, model: str | None, effort: str | None) -> None:
        """Change the model or the effort without losing the conversation.

        Both are fixed at start-up, so the process has to start again — and
        simply restarting it loses everything said so far. It reconnects with
        `--resume`, carrying the current session id. The restart happens on
        the next utterance: there is no reason to spin up a channel nobody is
        going to ask anything.
        """

        if (model or None) == self.model and (effort or None) == self.effort:
            return
        self.model = model or None
        self.effort = effort or None
        self.close()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        self._pending.clear()
        self._changes.clear()
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait(timeout=5)

    # -- One turn -----------------------------------------------------------

    def say(self, text: str):
        """Send one utterance and stream the events. One turn runs at a time."""

        turn = self._say(text)
        try:
            for event in turn:
                event.session_id, event.parent_id = self.id, self.parent_id
                yield event
        finally:
            turn.close()

    def _say(self, text: str):
        if not self._turn.acquire(blocking=False):
            yield Event("error", "앞 턴이 아직 안 끝났다.")
            return
        completed = False
        try:
            self.ensure()
            assert self._proc and self._proc.stdin
            # An abandoned turn is closed in `finally`. A queue that already
            # holds a start event is not drained.
            payload = {"type": "user", "message": {
                "role": "user", "content": [{"type": "text", "text": text}]}}
            if self.app:
                sent = self._send(self._request("turn/start", {
                    "threadId": self.session_id, "input": [{"type": "text", "text": text}],
                    **({"effort": self.effort} if self.effort else {})}))
            elif self.is_codex:
                try:
                    self._proc.stdin.write(text)
                    self._proc.stdin.close()
                    sent = True
                except (BrokenPipeError, OSError, ValueError):
                    sent = False
            else:
                sent = self._send(payload)
            if not sent:
                self.close()
                yield Event("error", "프로세스가 죽었다. 다시 보내면 새로 띄운다.")
                return
            for event in self._drain():
                completed = event.kind == "done"
                yield event
        finally:
            # `codex exec` is one process per turn; the others live on.
            if (self.is_codex and not self.app) or not completed:
                self.close()
            self._turn.release()

    def _drain(self):
        deadline = BOOT_TIMEOUT if self.session_id is None else TURN_TIMEOUT
        started = time.monotonic()
        final = ""
        while True:
            try:
                ev = self._events.get(timeout=deadline)
            except queue.Empty:
                # The CLI is silent because it waits on a person. Not a hang.
                if self._pending:
                    continue
                self.close()
                yield Event("error", f"{deadline:.0f}초 안에 답이 없다.")
                return
            deadline = TURN_TIMEOUT

            kind = ev.get("type")
            if kind == "__closed__":
                err = "".join(self._stderr)[-800:]
                self.close()
                yield Event("error", f"프로세스가 닫혔다. {err}".strip())
                return

            if self.app:
                method, params = ev.get("method"), ev.get("params") or {}
                item = params.get("item") or {}
                if method and "id" in ev:
                    event = self._codex_asks(ev["id"], method, params)
                    if event:
                        yield event
                elif "id" in ev and ev.get("error"):
                    # `turn/start` refused, so no `turn/completed` is coming.
                    error = ev["error"]
                    yield Event("error", str(error.get("message") if isinstance(error, dict) else error))
                    return
                elif method == "item/agentMessage/delta" and params.get("delta"):
                    yield Event("delta", str(params["delta"]))
                elif method == "item/started" and item.get("type") in (
                    "commandExecution", "fileChange", "mcpToolCall", "webSearch",
                ):
                    if item["type"] == "fileChange":
                        paths = [str(c.get("path")) for c in item.get("changes") or [] if c.get("path")]
                        self._changes[str(item.get("id"))] = paths
                        yield Event("tool", ("파일 변경 · " + ", ".join(paths))[:120])
                    else:
                        yield Event("tool", str(item.get("command") or item.get("tool")
                                                or item.get("query") or item["type"])[:120])
                elif method == "item/completed" and item.get("type") == "agentMessage":
                    final = str(item.get("text") or "")
                elif method == "turn/completed":
                    turn = params.get("turn") or {}
                    if turn.get("status") == "failed":
                        yield Event("error", str((turn.get("error") or {}).get("message") or "Codex 요청 실패"))
                        return
                    yield Event("done", final, {
                        "ms": round((time.monotonic() - started) * 1000),
                        "session_id": self.session_id, "model": self.model_name,
                        "error": not final.strip(),
                    })
                    return
                continue

            if self.is_codex:
                if kind == "thread.started":
                    self.session_id = ev.get("thread_id") or self.session_id
                elif kind == "item.completed" and ev.get("item", {}).get("type") == "agent_message":
                    final = str(ev["item"].get("text") or "")
                elif kind == "item.started" and ev.get("item", {}).get("type") in (
                    "command_execution", "mcp_tool_call", "web_search",
                ):
                    item = ev["item"]
                    yield Event("tool", str(item.get("command") or item.get("tool") or item["type"])[:120])
                elif kind == "turn.completed":
                    usage = ev.get("usage") or {}
                    yield Event("done", final, {
                        "ms": round((time.monotonic() - started) * 1000),
                        "session_id": self.session_id, "model": self.model_name,
                        "error": not bool(final.strip()),
                        "tokens": {"in": usage.get("input_tokens"), "out": usage.get("output_tokens"),
                                   "cache_read": usage.get("cached_input_tokens")},
                    })
                    return
                elif kind in ("turn.failed", "error"):
                    error = ev.get("error") or {}
                    yield Event("error", str(error.get("message") if isinstance(error, dict) else error)
                                or str(ev.get("message") or "Codex 요청 실패"))
                    return
                continue

            if kind == "system" and ev.get("subtype") == "init":
                self.session_id = ev.get("session_id") or self.session_id
                # When "default" was chosen, this is the only place that knows
                # what actually got attached.
                self.model_name = str(ev.get("model") or "")

            elif kind == "stream_event":
                inner = ev.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield Event("delta", delta["text"])

            elif kind == "control_request":
                rid, request = str(ev.get("request_id")), ev.get("request") or {}
                if request.get("subtype") != "can_use_tool":
                    self._send({"type": "control_response", "response": {
                        "subtype": "error", "request_id": rid, "error": "unsupported"}})
                    continue
                name, args = str(request.get("tool_name") or "?"), request.get("input") or {}

                def reply(allow, rid=rid, args=args):
                    said = ({"behavior": "allow", "updatedInput": args} if allow
                            else {"behavior": "deny", "message": DECLINED})
                    return {"type": "control_response",
                            "response": {"subtype": "success", "request_id": rid, "response": said}}

                outside = self._outside(args.get(WRITES_PATH[name])) if name in WRITES_PATH else ""
                yield self._approval(rid, reply, name, args, _tool_brief({"name": name, "input": args}), outside)

            elif kind == "assistant":
                for block in _blocks(ev.get("message") or {}):
                    if block.get("type") == "tool_use":
                        yield Event("tool", _tool_brief(block))

            elif kind == "result":
                usage = ev.get("usage") or {}
                yield Event(
                    "done",
                    str(ev.get("result") or ""),
                    {"ms": ev.get("duration_ms"),
                     "error": bool(ev.get("is_error")),
                     "session_id": ev.get("session_id"),
                     "model": self.model_name,
                     # This wiki is cost-sensitive enough to draw injected
                     # character counts as node sizes. What an effort level
                     # costs has to be visible here too.
                     "cost_usd": ev.get("total_cost_usd"),
                     "tokens": {
                         "in": usage.get("input_tokens"),
                         "out": usage.get("output_tokens"),
                         "cache_read": usage.get("cache_read_input_tokens"),
                         "cache_write": usage.get("cache_creation_input_tokens"),
                     }},
                )
                return


def _tool_brief(block: dict) -> str:
    """One line per tool. All it has to show is what is being done."""

    name = str(block.get("name") or "?")
    args = block.get("input") or {}
    for key in ("description", "file_path", "pattern", "path", "command"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"{name} · {' '.join(value.split())[:90]}"
    return name


def explain(answer: str, model: str = "", effort: str = ""):
    """Hand only the answer to a fresh session.

    Not the search prompt, not the channel, not the conversation so far — the
    plain explanation is written from the answer alone.
    """
    prompt = (Path(__file__).resolve().parents[1] / "prompts/chat-explain.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="wiki-explain-") as folder:
        chat = ChatSession(Path(folder), tools="", system=prompt, model=model,
                           effort=effort, isolated=True)
        try:
            yield from chat.say(json.dumps({"source_answer": answer}, ensure_ascii=False))
        finally:
            chat.close()


def demo() -> None:
    """Do two turns continue in one process? That is this file's premise."""

    import sys
    import time

    sys.stdout.reconfigure(encoding="utf-8")
    here = Path(__file__).resolve().parents[2]
    chat = ChatSession(here)

    # Measuring the wording of an answer goes red on one turn of phrase. What
    # has to be measured is whether the second turn remembers the first, so it
    # is handed one token to remember and only that is compared.
    token = "MARMOT-77"

    said = []
    t0 = time.time()
    for ev in chat.say(f"이 표를 기억해라: {token}. '알겠다' 한 마디만 답해라."):
        if ev.kind == "done":
            said.append(ev.text)
    first = time.time() - t0

    t1 = time.time()
    for ev in chat.say("방금 기억하라고 한 표를 그대로 적어라. 그것만."):
        if ev.kind == "done":
            said.append(ev.text)
    second = time.time() - t1

    chat.close()
    assert len(said) == 2, said
    assert token in said[1], said[1]  # one process is holding the first turn
    print(f"ok  두 턴이 이어진다 — {first:.1f}s → {second:.1f}s")
    print(f"    1: {said[0][:60]}")
    print(f"    2: {said[1][:60]}")


if __name__ == "__main__":
    demo()
