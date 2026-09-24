"""Wrap Claude's live process and Codex's explicit resume as one conversation.

The two hosts keep a conversation in different ways and the screen must not
have to know which. What reaches the screen is read by a person, so those
strings stay Korean.
"""

from __future__ import annotations

import json
import subprocess
import threading
import queue
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

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

BOOT_TIMEOUT = 120.0   # the first turn is slow: hooks, and loading
TURN_TIMEOUT = 600.0


@dataclass
class Event:
    """Only what the screen needs to know. Every other event kind is dropped here."""

    kind: str          # "delta" | "tool" | "done" | "error"
    text: str = ""
    meta: dict = field(default_factory=dict)


def _blocks(message: dict) -> list[dict]:
    content = (message or {}).get("content")
    return content if isinstance(content, list) else []


class ChatSession:
    """One channel's live conversation. One turn runs at a time."""

    def __init__(self, repo: Path, tools: str = READ_TOOLS,
                 system: str = "", model: str | None = None,
                 effort: str | None = None, resume: str | None = None,
                 isolated: bool = False) -> None:
        self.repo = Path(repo)
        self.tools = tools
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

    @property
    def is_codex(self) -> bool:
        return bool(self.model and self.model.startswith("codex:"))

    # -- Lifetime -----------------------------------------------------------

    def _spawn(self) -> None:
        cmd = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--tools", self.tools,
            "--allowedTools", self.tools,
        ]
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
        if self.is_codex:
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
            cmd, cwd=str(self.repo),
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
            try:
                self._proc.stdin.write(text if self.is_codex else json.dumps(payload, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
                if self.is_codex:
                    self._proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                self.close()
                yield Event("error", "프로세스가 죽었다. 다시 보내면 새로 띄운다.")
                return
            for event in self._drain():
                completed = event.kind == "done"
                yield event
        finally:
            if self.is_codex or not completed:
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
