"""Wrap Claude's live process and Codex's explicit resume as one conversation.

The two hosts keep a conversation in different ways and the screen must not
have to know which. What reaches the screen is read by a person, so those
strings stay Korean.

A session reads by default. `write=True` opens one in a repository checkout;
implementation sessions use full access, while explicitly non-bypass callers
can still route writes through `approval` and `answer`. Codex runs `app-server`,
which keeps one process across turns and is the only way Codex asks;
`exec` is left to the isolated explanation and
the refactor proposer, whose bypass writes in its candidate checkout.

`verification=<artifact directory>` is an independent Cloud review cell, not
an implementation session: it executes and creates verification files with
workspace-write permissions and no interactive approvals. Its artifacts and
temporary files are kept outside the reviewed source.
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
from common.host import INSTRUCTIONS, environment, skill_config
from common.process import SUSPENDED, contained, killed, resumed, terminated

from .chat_local import claude_usage, cli_command, codex_usage, quota_windows
from . import read_tools

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
# `AskUserQuestion` always does: its approval is the person's answer.
WRITE_TOOLS = "Bash,Read,Glob,Grep,Edit,Write,AskUserQuestion"
ASK_FREE = "Read,Glob,Grep"
# The two hosts' question tools. Their approval carries the answers.
QUESTIONS = {"AskUserQuestion", "requestUserInput"}
# Claude's tools that name the file they write.
WRITES_PATH = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
DECLINED = "The person declined this."

BOOT_TIMEOUT = 120.0   # the first turn is slow: hooks, and loading
TURN_TIMEOUT = 600.0

# What `app-server` answers `thread/resume` for a thread it cannot find
# (`-32600`, CLI 0.156.0). Only these start a new conversation.
#
# ponytail: matched on the message, since the code is the generic "invalid
# request". Reworded in a later CLI, a lost thread raises instead of starting
# afresh — loud, never a silent loss. Match on a code if Codex gives one.
NO_THREAD = ("no rollout found", "invalid session id")


class Refused(RuntimeError):
    """The CLI answered a request with an error, as opposed to not answering."""

    def __init__(self, method: str, error) -> None:
        super().__init__(f"Codex {method} 실패: {error}")
        self.error = error


@dataclass
class Event:
    """Only what the screen needs to know. Every other event kind is dropped here.

    `session_id` is this program's id for the session, fixed at construction.
    The CLI's own id is known only after its first reply and changes on a
    reconnect, so it cannot tell whose a late event is; it rides in
    `done.meta` for `--resume`. `parent_id` is the session that started this
    one — room for a coordinator, `None` until there is one.
    """

    kind: str          # "delta" | "progress" | "tool" | "hook" | "approval" | "done" | "error" | "context"
    text: str = ""
    meta: dict = field(default_factory=dict)
    session_id: str = ""
    parent_id: str | None = None


def our_worktree(repo: Path) -> bool:
    """Is `repo` the top of a worktree in `worktree_home` of its own repository?

    Not just any `git worktree`: other tools make those too, and they
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

    READ_PROFILE = read_tools.PROFILE
    VERIFICATION_PROFILE = "cloud-verification-v1"
    VERIFICATION_TOOLS = "Bash,Read,Glob,Grep,Edit,Write"

    def __init__(self, repo: Path, tools: str = READ_TOOLS,
                 system: str = "", model: str | None = None,
                 effort: str | None = None, resume: str | None = None,
                 isolated: bool = False, write: bool = False,
                 parent_id: str | None = None, bypass: bool = False,
                 verification: Path | None = None, env: dict | None = None,
                 fast: bool = False) -> None:
        self.repo = Path(repo)
        if verification is not None and not our_worktree(self.repo):
            raise ValueError(f"쓰기 세션은 workspace 가 만든 작업트리에서만 연다: {self.repo}")
        if write:
            root = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--show-toplevel"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            if root.returncode or Path(root.stdout.strip()).resolve() != self.repo.resolve():
                raise ValueError(f"쓰기 세션은 선택한 저장소 루트에서 연다: {self.repo}")
        if verification is not None and (write or isolated):
            raise ValueError("클라우드 검증 셀은 구현·격리 응답 세션과 별개다")
        self.verification = Path(verification).resolve() if verification is not None else None
        if self.verification is not None:
            self.verification.mkdir(parents=True, exist_ok=True)
        self.write = write
        # A write session that asks nobody: Claude's `bypassPermissions`, Codex
        # `danger-full-access` with `never`. The CLI then never asks, so the
        # outside-the-worktree refusal in `_approval` does not run either;
        # Claude's `permissions.deny` still does. Fixed at start-up, like the model.
        self.bypass = bypass and write
        self.tools = self.VERIFICATION_TOOLS if self.verification is not None else WRITE_TOOLS if write else tools
        self.id = uuid.uuid4().hex
        self.parent_id = parent_id
        # The CLI's login is whatever its environment points at (`CODEX_HOME`,
        # Claude's config). Held from here, so a restart or a `--resume` goes
        # on as the same account even if the server's environment changed.
        self._env = environment(env)
        if self.verification is not None:
            # Keep routine tool caches and transient output out of the reviewed source.
            temporary = self.verification / "tmp"
            temporary.mkdir(exist_ok=True)
            self._env.update(WIKI_VERIFICATION_ARTIFACTS=str(self.verification),
                             PYTHONDONTWRITEBYTECODE="1", PIP_CACHE_DIR=str(self.verification / "pip-cache"),
                             TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
        # A channel's character goes in as a system prompt. The first version
        # sent it as the opening turn and that one turn took two minutes — the
        # model reads the introduction and starts going through files. A
        # system prompt costs no turn and applies from the first utterance.
        self.system = system.strip()
        if not isolated:
            self.system += "\n" + INSTRUCTIONS
        if write:
            self.system += (
                "\nWhen asking the person, use AskUserQuestion (Claude) or request_user_input (Codex). "
                "Group independent decisions in one call with chapter headers, within the host's limits. "
                "Offer three or four meaningful alternatives when available, recommendation first, with "
                "detailed consequences, costs and concrete examples. Include Markdown or fenced text sketches "
                "in descriptions when comparing layouts. Write question prose in English for the Korean overlay; "
                "preserve code and paths. Ask dependent follow-ups after the batch is answered. "
                "Never invent alternatives just to fill the list.")
        # The model and the effort are fixed when the process starts. Changing
        # either means `reconfigure` starting it again, and it reconnects with
        # `--resume` so the conversation is not lost.
        self.model = model or None
        self.effort = effort or None
        self.fast = fast
        self.isolated = isolated
        self.session_id: str | None = resume
        self.model_name = ""
        self._resume: str | None = resume
        self._proc: subprocess.Popen | None = None
        self._job = None   # `_proc`'s Windows job: its background shells end with it
        # `settled(answer)`: the answer closes the work. Background tasks still
        # running after one are left over, not awaited (`_drain`).
        self.settled = None
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
        # The Codex thread a resume could not reach; told once, on the next turn.
        self._lost: str | None = None
        # The running turn's stop, for `_spawn`: a stop that came before the
        # process existed kills it the moment it does.
        self._halt: threading.Event | None = None
        # Guards `_halt` against `stop`: a stop checks whose turn runs and
        # kills in one step, so it never lands on the turn after its own.
        self._halting = threading.Lock()
        # "Allow for this session": kept here, never handed to the CLI. A rule
        # the CLI held would answer before `_approval` ever saw the path, and
        # Claude's `Edit` rule does not look at paths at all. Tied to this
        # object: a model change keeps it, a reset makes a new object.
        self._rules: set[tuple] = set()
        # A message a person sends into the running turn. `_open` says a turn
        # is taking them; `_steering` guards it against the turn's end, so a
        # message is either in this turn or refused, never left for the next.
        # Claude's are counted until the CLI replays them: a result that comes
        # before one was taken in is not the end of the turn. Counted, not
        # matched by text: stdin is read in order, so a turn's first replay is
        # its prompt, whatever a steer says.
        self._steering = threading.Lock()
        self._open = False
        self._unread = 0
        self._prompt_seen = False
        self._steered = False    # this turn took a steer: its answer may still be owed
        # Questions waiting on a person: id -> how many answers they need.
        self._asks: dict[str, int] = {}
        self._turn_id = ""       # Codex's id for the running turn, for `turn/steer`
        self._start_rpc = None   # the `turn/start` request, whose error ends the turn
        self.connection_ms: int | None = None
        self._boot_at = 0.0
        self.usage: dict = {}
        self.quota: list[dict] = []
        self._quota_at = 0.0
        self._quota_poll_at = 0.0
        self._quota_error = ""
        self._quota_lock = threading.Lock()
        self._compacting = False

    def status(self) -> dict:
        """Connection timing and provider usage, without login identifiers."""
        if self._quota_lock.acquire(blocking=False):
            try:
                interval = 60 if self.is_codex else 300
                last = self._quota_at if self.is_codex else self._quota_poll_at
                if time.monotonic() - last >= interval:
                    self._quota_poll_at = time.monotonic()
                    if self.is_codex:
                        self._quota_at = self._quota_poll_at
                    try:
                        self.quota = codex_usage(env=self._env, cwd=self.repo) if self.is_codex else claude_usage(env=self._env)
                        self._quota_at = time.monotonic()
                        self._quota_error = ""
                    except Exception:
                        self._quota_error = "계정 사용량을 받지 못했다 · 마지막 사용량은 오래되었을 수 있다"
            finally:
                self._quota_lock.release()
        return {"live": self.alive, "connection_ms": self.connection_ms,
                "quota": self.quota, "usage": self.usage, "error": self._quota_error}

    @property
    def is_codex(self) -> bool:
        return bool(self.model and self.model.startswith("codex:"))

    @property
    def app(self) -> bool:
        """A Codex session on `codex app-server`: every one but the isolated
        explanation, which `exec` runs with the user's config left out —
        `app-server` has no `--ignore-user-config`."""
        return self.is_codex and not self.isolated

    @property
    def source_only(self) -> bool:
        return (self.app and not self.write and self.verification is None
                and set(self.tools.split(",")) == {"Read", "Glob", "Grep"})

    # -- Lifetime -----------------------------------------------------------

    def _spawn(self) -> None:
        cmd = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            # Each message comes back once the CLI takes it in — how `steer`
            # knows one sent mid-turn was not left for the next turn.
            "--replay-user-messages",
            # What the hooks did — the wiki's injection, its auto-update — reaches
            # the screen only through these.
            "--include-hook-events",
            "--tools", self.tools,
            "--allowedTools", ASK_FREE if self.write and not self.bypass else self.tools,
        ]
        if self.bypass:
            # The prompt tool stays: without it the CLI drops `AskUserQuestion`,
            # which is the one thing a bypass session still asks.
            cmd += ["--dangerously-skip-permissions", "--permission-prompt-tool", "stdio"]
        elif self.write or self.verification is not None:
            # Named, so a `defaultMode` of `acceptEdits` in someone's settings
            # cannot skip the question.
            cmd += ["--permission-mode", "default", "--permission-prompt-tool", "stdio"]
        if self.isolated:
            # `--bare` would skip the subscription login too. The sign-in is
            # kept; only the settings, hooks and tools are isolated.
            cmd += ["--setting-sources", "", "--settings", json.dumps({"disableAllHooks": True, "fastMode": self.fast}),
                    "--strict-mcp-config", "--no-session-persistence"]
        else:
            # Shared user skills can carry the previous desktop host's routing
            # instructions. Native hooks and the app's prompts own this workflow.
            cmd += ["--disable-slash-commands"]
            cmd += ["--settings", json.dumps({"fastMode": self.fast})]
        if self.system:
            cmd += ["--system-prompt" if self.isolated else "--append-system-prompt", self.system]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        if self._resume:
            cmd += ["--resume", self._resume]
        if self.app:
            # `request_user_input` is Plan mode's only, unless this is on (CLI 0.156.0).
            cmd = ["codex", "app-server"] + (["--enable", "default_mode_request_user_input"] if self.write
                                             else ["--disable", "multi_agent"])
            if self.bypass:
                cmd += ["-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"']
            if self.source_only or self.verification is not None:
                for feature in ("apps", "plugins", "computer_use", "request_permissions_tool",
                                "default_mode_request_user_input", "memories"):
                    cmd += ["--disable", feature]
                if self.source_only:
                    cmd += ["--disable", "shell_tool", "--disable", "unified_exec", "--disable", "browser_use",
                            "-c", 'web_search="disabled"', "-c", "tools.view_image=false"]
                else:
                    cmd += ["--enable", "shell_tool", "--enable", "unified_exec"]
                    if os.name == "nt":
                        # Session-local documented fallback; no account config
                        # edits or dedicated sandbox-user setup for verification.
                        cmd += ["-c", 'windows.sandbox="unelevated"']
                # Dynamic function calls use this host in current Codex. It
                # exposes only the tools above, not a native shell or filesystem.
                cmd += ["--enable", "code_mode_host"]
            self.model_name = self.model.removeprefix("codex:")
            skills = skill_config(self.repo, self._env)
            if skills:
                cmd += ["-c", skills]
            cmd += ["-c", 'service_tier="fast"' if self.fast else 'service_tier="default"']
        elif self.is_codex:
            cmd = ["codex", "exec", "--model", self.model.removeprefix("codex:"),
                   "--json", "--sandbox", "danger-full-access" if self.bypass else "read-only",
                   "-c", 'approval_policy="never"', "--disable", "multi_agent",
                   "-c", "developer_instructions=" + json.dumps(self.system, ensure_ascii=False),
                   "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                   "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                   *(() if self.bypass else ("--disable", "shell_tool")),
                   "--disable", "apps", "--disable", "plugins", "--disable", "memories"]
            if self.effort:
                cmd += ["-c", "model_reasoning_effort=" + json.dumps(self.effort)]
            cmd += ["-c", 'service_tier="fast"' if self.fast else 'service_tier="default"']
            cmd.append("-")
            self.model_name = self.model.removeprefix("codex:")
        self._stderr = deque(maxlen=20)
        cmd = [*cli_command(cmd[0]), *cmd[1:]]
        proc = subprocess.Popen(
            cmd, cwd=str(self.repo), env=self._env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, creationflags=SUSPENDED,
        )
        # The CLI's background shells outlive it on Windows — even its own
        # `stop_task` leaves the shell's child running (2.1.291). Whatever
        # ends this process ends them through the job (`stop`, `close`).
        # Started suspended, so nothing it starts escapes the job.
        # ponytail: Windows only; elsewhere the CLI's exit is trusted. Start
        # it in its own session and `killpg` if orphans show up there.
        job = contained(proc)
        # Published as a pair under `_halting`, which `stop` holds while it
        # uses the job: a closed handle's number goes to the next job made.
        with self._halting:
            self._proc, self._job = proc, job
        try:
            resumed(self._proc)
        except OSError:
            self.close()
            raise
        # `stop()` sets the turn's halt before it looks at `_proc`. So either it
        # sees this process, or this sees its halt — never neither. Waiting for
        # `ensure()` to return left `_open_thread` waiting on a process nobody
        # would kill, for a boot timeout per request.
        if self._halt is not None and self._halt.is_set():
            self._proc.kill()
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
                if self._proc is not None or self._job:
                    # A provider that died between turns: its job still
                    # holds what it started. Gone with it, not overwritten.
                    self.close()
                # With a session to reconnect to, start again carrying its id.
                # Without one, this is a new conversation.
                self._resume = self.session_id
                self._events = queue.Queue()
                self._boot_at = time.monotonic()
                self.connection_ms = None
                self._spawn()
                if self.app:
                    try:
                        self._open_thread()
                        self.connection_ms = round((time.monotonic() - self._boot_at) * 1000)
                    except Exception:
                        self.close()
                        raise

    def _open_thread(self) -> None:
        """`app-server` holds no conversation until asked to start or resume one.

        Full-access implementation uses `danger-full-access` with `never`.
        Explicitly non-bypass write callers use `read-only` with `on-request`.
        An ordinary read session never asks — `_approval` would refuse it
        anyway. Cloud verification uses workspace-write with `never`.

        A thread Codex says is not there is not dropped in silence: a new one
        starts, and the next turn says so. Anything else — a slow answer, a
        closed process, another refusal — raises, and the thread id stays for
        the next try: a slow resume must not cost the conversation.
        """

        self._call("initialize", {"clientInfo": {"name": "wiki-agent", "version": "0.1.0"},
                                  **({"capabilities": {"experimentalApi": True}} if self.source_only else {})})
        self._send({"method": "initialized"})
        params = {"cwd": str(self.repo), "sandbox": "workspace-write" if self.verification is not None
                  else "danger-full-access" if self.bypass else "read-only",
                  "approvalPolicy": "on-request" if self.write and not self.bypass else "never",
                  "model": self.model_name, "serviceTier": "priority" if self.fast else "default"}
        if self.system:
            params["developerInstructions"] = self.system
        if self.source_only or self.verification is not None:
            # Do not inherit connector tools from global or project settings.
            # Read the effective names only; never expose config/credentials to the model.
            config = self._call("config/read", {"cwd": str(self.repo), "includeLayers": False})["config"]
            if any(not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                                   for c in name) for name in config.get("mcp_servers", {})):
                raise ValueError("리뷰 셀에서 차단할 MCP 서버 이름을 확인할 수 없다")
            params["config"] = {f"mcp_servers.{name}.enabled": False
                                for name in config.get("mcp_servers", {})}
            if self.source_only:
                params["developerInstructions"] = self.system + (
                    "\nUse repo_read, repo_glob and repo_grep for source inspection. Shell, connectors and "
                    "permission requests are unavailable. The server supplies the diff and executes checks; "
                    "never request escalated permissions or claim unexecuted tests passed.")
            else:
                params["config"].update({"sandbox_workspace_write.writable_roots": [str(self.verification)],
                                         "sandbox_workspace_write.network_access": True})
        result = None
        if self._resume:
            try:
                result = self._call("thread/resume", {"threadId": self._resume, **params})
            except Refused as exc:
                message = str(exc.error.get("message") if isinstance(exc.error, dict) else exc.error)
                if not any(mark in message for mark in NO_THREAD):
                    raise
                self._lost = self._resume
        if result is None:
            if self.source_only:
                params["dynamicTools"] = read_tools.SPECS
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
                    raise Refused(method, reply["error"])
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

    def answer(self, approval_id: str, allow: bool, scope: str = "once",
               answers: list[str] | None = None) -> bool:
        """Answer an `approval` event. `False` when nothing waits under that id:
        answered already, or the process that asked is gone. `scope="session"`
        with `allow` also lets the same thing through unasked from now on.
        A question's `answers` are one per question, in its order.

        Written to the process that asked, never to whatever runs now: after a
        restart a late "allow" would land on the new process, and Codex
        numbers its requests afresh in each one.
        """

        if scope == "session" and allow and approval_id in self._pending and self._pending[approval_id][2] is None:
            raise ValueError("이 요청은 세션 동안 허용할 수 없다")
        # A question answered is one answer per question; declining needs none.
        needed = self._asks.get(approval_id)
        if allow and needed is not None and (len(answers or []) != needed or not all(a.strip() for a in answers)):
            raise ValueError(f"질문 {needed}개에 답이 하나씩 있어야 한다")
        self._asks.pop(approval_id, None)
        proc, reply, rule = self._pending.pop(approval_id, (None, None, None))
        sent = proc is not None and self._send(reply(allow, answers or []), proc)
        if sent and scope == "session" and allow:
            self._rules.add(rule)
        return sent

    def steer(self, text: str) -> bool:
        """Say `text` into the running turn. `False` when no turn takes it —
        none runs, it is ending, or this is the isolated `exec`.

        Claude reads it from stdin between steps; Codex takes it as
        `turn/steer`. Either way the turn goes on with it, and ends once."""

        with self._steering:
            if not self._open:
                return False
            if self.app:
                if not self._turn_id:
                    return False
                return self._send(self._request("turn/steer", {
                    "threadId": self.session_id, "expectedTurnId": self._turn_id,
                    "input": [{"type": "text", "text": text}]}))
            if self.is_codex:
                return False
            sent = self._send(_user(text))
            if sent:
                self._unread += 1
                self._steered = True
            return sent

    def _closing(self) -> bool:
        """At a turn's end: may it end? Not while a steered message waits to
        be taken in. Once it may, no more are taken."""

        with self._steering:
            if self._unread:
                return False
            self._open = False
            return True

    @staticmethod
    def _rule(tool: str, args: dict) -> tuple | None:
        """What "allow for this session" covers. A file write, by the tool; a
        command, only the same text — and for Codex, in the same `cwd`."""

        if tool in WRITES_PATH or tool == "fileChange":
            return ("file", tool)
        if tool == "Bash" and args.get("command"):
            return ("command", tool, str(args["command"]))
        if tool == "command" and args.get("command"):
            return ("command", tool, str(args["command"]), str(args.get("cwd") or ""))
        return None

    @property
    def rules(self) -> list[dict]:
        return sorted(({"kind": r[0], "tool": r[1], **({"command": r[2]} if r[0] == "command" else {}),
                        **({"cwd": r[3]} if len(r) > 3 else {})} for r in self._rules),
                      key=lambda r: (r["kind"], r["tool"], r.get("command", "")))

    def clear_rules(self) -> None:
        self._rules.clear()

    def _outside(self, path) -> str:
        """The path, resolved, when it lies outside this worktree. Else ``""``."""

        if not path:
            return ""
        target = Path(path)
        target = (target if target.is_absolute() else self.repo / target).resolve()
        root = self.repo.resolve()
        roots = [root, *([self.verification] if self.verification is not None else [])]
        return "" if any(target == r or r in target.parents for r in roots) else str(target)

    def _approval(self, key: str, reply, tool: str, args: dict, text: str, outside: str) -> Event:
        """An approval event for the person, or a refusal nobody is asked about.

        Full-access writes are allowed without asking. Explicitly non-bypass
        callers refuse writes outside their checkout; read sessions refuse
        writes entirely. Session rules never extend a restricted caller's root.
        Either way it is an approval too, already answered, with `by` saying
        who answered it, so the record keeps it with the rest.
        """

        if (outside and not self.bypass) or (not self.write and self.verification is None):
            self._send(reply(False))
            return Event("approval", text, {"id": key, "tool": tool, "input": args, "answer": "deny",
                                            "by": "outside" if outside else "read"})
        if self.verification is not None and tool not in QUESTIONS:
            self._send(reply(True))
            return Event("approval", text, {"id": key, "tool": tool, "input": args,
                                            "answer": "allow", "by": "verification"})
        if self.bypass and tool not in QUESTIONS:
            self._send(reply(True))
            return Event("approval", text, {"id": key, "tool": tool, "input": args,
                                            "answer": "allow", "by": "session"})
        rule = self._rule(tool, args)
        if rule is not None and rule in self._rules:
            self._send(reply(True))
            return Event("approval", text, {"id": key, "tool": tool, "input": args, "answer": "allow",
                                            "by": "session"})
        self._pending[key] = (self._proc, reply, rule)
        if tool in QUESTIONS:
            self._asks[key] = len(args.get("questions") or [])
        return Event("approval", text, {"id": key, "tool": tool, "input": args, "session": rule is not None})

    def _codex_asks(self, rid, method: str, params: dict) -> Event | None:
        """A request from `app-server`. Two kinds are approvals; the rest are
        refused so the turn does not wait on an answer that never comes."""

        def reply(allow, answers=(), rid=rid):
            return {"id": rid, "result": {"decision": "accept" if allow else "decline"}}

        reason = params.get("reason")
        if method == "item/permissions/requestApproval" and self.bypass:
            self._send({"id": rid, "result": {"permissions": params.get("permissions") or {}, "scope": "session"}})
            return Event("tool", "작업 세션의 전체 접근 권한을 적용했다.")
        if self.verification is not None and method in {
                "item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            # Normal workspace execution never asks. An unexpected request is
            # an escalation, not permission to bypass the configured sandbox.
            self._send(reply(False))
            return Event("tool", "검증 범위를 벗어난 권한 요청은 허용하지 않는다.",
                         {"tool": method, "answer": "deny", "by": "verification"})
        if method == "item/tool/call" and self.source_only:
            tool = params.get("tool", "")
            try:
                output = read_tools.call(self.repo, tool, params.get("arguments"))
                success = True
            except (OSError, ValueError, subprocess.SubprocessError):
                # Exception text can contain private paths or file bytes.
                output, success = "읽기 실패: 추적된 소스 경로와 검색 인수를 확인해라.", False
            self._send({"id": rid, "result": {"contentItems": [{"type": "inputText", "text": output}],
                                              "success": success}})
            return Event("tool", f"소스 읽기 · {tool}"[:120], {"tool": tool, "success": success})
        if method == "item/permissions/requestApproval" and not self.write:
            self._send({"id": rid, "result": {"permissions": {}, "scope": "turn"}})
            return Event("tool", "리뷰 셀은 설정된 범위 밖의 추가 권한을 요청하지 않는다.")
        if method == "item/tool/requestUserInput":
            questions = params.get("questions") or []

            def said(allow, answers=(), rid=rid):
                # Declined, no question has an answer; the model reads that.
                return {"id": rid, "result": {"answers": {
                    str(q.get("id")): {"answers": [a]} for q, a in zip(questions, answers if allow else ())}}}

            return self._approval(str(rid), said, "requestUserInput", {"questions": questions},
                                  " · ".join(str(q.get("question") or "") for q in questions)[:300], "")
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

    def reconfigure(self, model: str | None, effort: str | None, fast: bool | None = None) -> None:
        """Change the model or the effort without losing the conversation.

        Both are fixed at start-up, so the process has to start again — and
        simply restarting it loses everything said so far. It reconnects with
        `--resume`, carrying the current session id. The restart happens on
        the next utterance: there is no reason to spin up a channel nobody is
        going to ask anything.
        """

        fast = self.fast if fast is None else fast
        if (model or None) == self.model and (effort or None) == self.effort and fast == self.fast:
            return
        self.model = model or None
        self.effort = effort or None
        self.fast = fast
        self.close()

    def stop(self, halt: threading.Event) -> None:
        """End the turn whose stop is `halt`, from another thread. Its `_drain`
        reads the process closing and ends the turn with an error. The CLI's
        session id stays, so the next turn resumes it. Before the process
        exists there is nothing to kill: `halt`, set by the caller first,
        covers that.

        Only that turn's process. A late stop for a turn that has ended found
        the next turn's process in `_proc` and killed a valid instruction.

        ponytail: kills the process rather than Claude's `interrupt` or Codex's
        `turn/interrupt` — the same on both hosts, and resuming loses nothing.
        The next turn pays the start-up; switch if stops become frequent.
        """

        with self._halting:
            proc = self._proc if self._halt is halt else None
            if proc is not None:
                # Its whole tree: a background child holding the stdout pipe
                # would otherwise keep the turn waiting after the stop.
                killed(self._job)
                if proc.poll() is None:
                    proc.kill()

    def close(self) -> None:
        # Taken from `stop` under its lock: once here, no stop still holds
        # this job's handle, so closing it cannot end a job that reuses it.
        with self._halting:
            proc, self._proc = self._proc, None
            job, self._job = self._job, None
        self._pending.clear()
        self._asks.clear()
        self._changes.clear()
        try:
            if proc is not None:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait(timeout=5)
        finally:
            # Nobody watches its background work after this: no orphans.
            terminated(job)

    # -- One turn -----------------------------------------------------------

    def say(self, text: str, halt: threading.Event | None = None):
        """Send one utterance and stream the events. One turn runs at a time.

        `halt` is this turn's stop. `stop()` can only kill a process that
        exists; one asked for while the process is still starting is caught
        here, once it has started and before anything is sent to it.
        """

        turn = self._say(text, halt)
        try:
            for event in turn:
                event.session_id, event.parent_id = self.id, self.parent_id
                yield event
        finally:
            turn.close()

    def _say(self, text: str, halt: threading.Event | None = None):
        if not self._turn.acquire(blocking=False):
            yield Event("error", "앞 턴이 아직 안 끝났다.")
            return
        completed = False
        with self._halting:
            self._halt = halt
        try:
            self.ensure()
            if halt is not None and halt.is_set():
                # From here on `_proc` is set, so a later stop reaches it.
                self.close()
                yield Event("error", "멈췄다.")
                return
            assert self._proc and self._proc.stdin
            # An abandoned turn is closed in `finally`. A queue that already
            # holds a start event is not drained.
            self._turn_id = ""
            if self.app:
                start = self._request("turn/start", {
                    "threadId": self.session_id, "input": [{"type": "text", "text": text}],
                    "serviceTier": "priority" if self.fast else "default",
                    **({"approvalPolicy": "never", "sandboxPolicy": {
                        "type": "workspaceWrite", "writableRoots": [str(self.repo.resolve()), str(self.verification)],
                        "networkAccess": True, "excludeTmpdirEnvVar": True, "excludeSlashTmp": True}}
                       if self.verification is not None else
                       {"approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly"}}
                       if not self.write else
                       {"approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}}
                       if self.bypass else {}),
                    **({"effort": self.effort} if self.effort else {})})
                self._start_rpc = start["id"]
                sent = self._send(start)
            elif self.is_codex:
                try:
                    self._proc.stdin.write(text)
                    self._proc.stdin.close()
                    sent = True
                except (BrokenPipeError, OSError, ValueError):
                    sent = False
            else:
                sent = self._send(_user(text))
            if not sent:
                self.close()
                yield Event("error", "프로세스가 죽었다. 다시 보내면 새로 띄운다.")
                return
            with self._steering:
                self._open, self._unread, self._prompt_seen, self._steered = True, 0, False, False
            if self._lost:
                # The caller writes it on record; a resume must not try that thread again.
                yield Event("context", f"Codex 이어가기 실패 — 새 대화로 시작했다 ({self._lost})")
                self._lost = None
            for event in self._drain():
                completed = event.kind == "done" and not event.meta.get("error")
                yield event
        finally:
            with self._steering:
                self._open, self._unread = False, 0
            # `codex exec` is one process per turn; the others live on.
            if (self.is_codex and not self.app) or not completed:
                self.close()
            with self._halting:
                self._halt = None
            self._turn.release()

    def _drain(self):
        deadline = BOOT_TIMEOUT if self.session_id is None else TURN_TIMEOUT
        started = time.monotonic()
        final = ""
        # `app-server` reports each model call's usage (`last`) and a running
        # total for the thread. A turn is the sum of its calls: a total taken
        # from before the turn is not there after a resume.
        used = {"in": 0, "out": 0, "cache_read": 0, "reasoning": 0}
        background = set()
        background_status = {}
        backgrounded = {}
        notified = set()
        followups = 0
        foreground_result_seen = False
        replay_human = None
        proc = self._proc
        while True:
            # A background task runs silent for as long as it needs — a test
            # suite, a model sweep. After the answer, while one is alive, its
            # own end or the process's ends the wait, never the clock: the
            # clock killed the CLI at 600s and orphaned the task. The process
            # is looked at directly: a child holding its stdout keeps the
            # pipe's end, and so `__closed__`, from ever coming.
            unbounded = foreground_result_seen and background
            try:
                ev = self._events.get(timeout=1.0 if unbounded else deadline)
            except queue.Empty:
                if unbounded:
                    if proc is not None and proc.poll() is None:
                        continue
                    ev = {"type": "__closed__"}
                # The CLI is silent because it waits on a person. Not a hang.
                elif self._pending:
                    continue
                else:
                    self.close()
                    yield Event("error", f"{deadline:.0f}초 안에 답이 없다.")
                    return
            deadline = TURN_TIMEOUT

            kind = ev.get("type")
            if kind == "user" and ev.get("isReplay"):
                replay_human = not ev.get("isSynthetic")
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
                    error = ev["error"]
                    message = str(error.get("message") if isinstance(error, dict) else error)
                    if ev["id"] != self._start_rpc:
                        # A `turn/steer` refused; the turn goes on without it.
                        yield Event("tool", f"끼어들기 실패 · {message}"[:120])
                        continue
                    # `turn/start` refused, so no `turn/completed` is coming.
                    yield Event("error", message)
                    return
                elif method == "turn/started":
                    self._turn_id = str((params.get("turn") or {}).get("id") or "")
                elif method == "hook/completed":
                    run = params.get("run") or {}
                    entries = run.get("entries") or []
                    event = _hook(str(run.get("eventName") or ""),
                                  "\n".join(e["text"] for e in entries if e.get("kind") != "context"),
                                  "\n".join(e["text"] for e in entries if e.get("kind") == "context"))
                    if event:
                        yield event
                elif method == "item/agentMessage/delta" and params.get("delta"):
                    yield Event("delta", str(params["delta"]))
                elif method == "item/commandExecution/outputDelta" and params.get("delta"):
                    yield Event("tool", str(params["delta"])[:4000],
                                {"tool": "commandExecution", "item_id": params.get("itemId")})
                elif method == "item/mcpToolCall/progress" and params.get("message"):
                    yield Event("tool", str(params["message"])[:4000],
                                {"tool": "mcpToolCall", "item_id": params.get("itemId")})
                elif method in ("item/started", "item/completed") and item.get("type") == "contextCompaction":
                    self._compacting = method == "item/started"
                    yield Event("compaction", "Context compaction started" if self._compacting else "Context compaction completed",
                                {"phase": "started" if self._compacting else "completed"})
                elif method == "thread/compacted" and self._compacting:
                    self._compacting = False
                    yield Event("compaction", "Context compaction completed", {"phase": "completed"})
                elif method == "item/started" and item.get("type") in (
                    "commandExecution", "fileChange", "mcpToolCall", "webSearch",
                ):
                    if item["type"] == "fileChange":
                        paths = [str(c.get("path")) for c in item.get("changes") or [] if c.get("path")]
                        self._changes[str(item.get("id"))] = paths
                        yield Event("tool", ("파일 변경 · " + ", ".join(paths))[:120])
                    else:
                        yield Event("tool", str(item.get("command") or item.get("tool")
                                                or item.get("query") or item["type"])[:120], {"tool": item["type"]})
                elif method == "item/completed" and item.get("type") == "agentMessage":
                    message = str(item.get("text") or "")
                    # Older app-server versions omit phase. Keep each completed
                    # message; consumers remove the final answer's duplicate.
                    if item.get("phase") in (None, "commentary"):
                        yield Event("progress", message)
                    if item.get("phase") != "commentary":
                        final = message
                elif method == "thread/tokenUsage/updated":
                    last = (params.get("tokenUsage") or {}).get("last") or {}
                    for mine, theirs in (("in", "inputTokens"), ("out", "outputTokens"),
                                         ("cache_read", "cachedInputTokens"), ("reasoning", "reasoningOutputTokens")):
                        used[mine] += int(last.get(theirs) or 0)
                    total = (params.get("tokenUsage") or {}).get("total") or {}
                    self.usage = {"input_tokens": total.get("inputTokens"), "output_tokens": total.get("outputTokens")}
                    self.usage["scope"] = "thread"
                elif method == "account/rateLimits/updated":
                    self.quota = quota_windows(params)
                    self._quota_at = time.monotonic()
                    self._quota_error = ""
                elif method == "turn/completed":
                    self._closing()   # Codex holds no queue: a late steer is refused by the CLI
                    turn = params.get("turn") or {}
                    if turn.get("status") == "failed":
                        yield Event("error", str((turn.get("error") or {}).get("message") or "Codex 요청 실패"))
                        return
                    yield Event("done", final, {
                        "ms": round((time.monotonic() - started) * 1000),
                        "session_id": self.session_id, "model": self.model_name,
                        "error": not final.strip(),
                        # No cost: Codex runs on a subscription's limit, with no price to multiply.
                        **({"tokens": used} if any(used.values()) else {}),
                    })
                    return
                continue

            if self.is_codex:   # `exec`: the isolated explanation only
                if kind == "thread.started":
                    self.session_id = ev.get("thread_id") or self.session_id
                elif kind == "item.completed" and ev.get("item", {}).get("type") == "agent_message":
                    final = str(ev["item"].get("text") or "")
                elif kind == "item.started" and ev.get("item", {}).get("type") in (
                    "command_execution", "mcp_tool_call", "web_search",
                ):
                    item = ev["item"]
                    yield Event("tool", str(item.get("command") or item.get("tool") or item["type"])[:120],
                                {"tool": item["type"]})
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
                self.connection_ms = round((time.monotonic() - self._boot_at) * 1000)

            elif kind == "rate_limit_event":
                info = ev.get("rate_limit_info") or ev.get("rateLimitInfo") or {}
                name = info.get("rateLimitType") or info.get("rate_limit_type") or "현재 한도"
                utilization = info.get("utilization")
                window = {"name": name, "used_percent": utilization * 100 if isinstance(utilization, (float, int)) else None,
                          "resets_at": info.get("resetsAt", info.get("resets_at")), "status": info.get("status")}
                previous = next((q for q in self.quota if q["name"] == name), None)
                if previous and window["used_percent"] is None and previous.get("resets_at") == window["resets_at"]:
                    window["used_percent"] = previous.get("used_percent")
                self.quota = [q for q in self.quota if q["name"] != name] + [window]
                self._quota_at = time.monotonic()

            elif kind == "system" and ev.get("subtype") == "status" and ev.get("status") == "compacting":
                self._compacting = True
                yield Event("compaction", "Context compaction started", {"phase": "started"})

            elif kind == "system" and ev.get("subtype") == "compact_boundary":
                self._compacting = False
                info = ev.get("compact_metadata") or {}
                tokens = info.get("pre_tokens")
                detail = f" · {tokens:,} tokens before compaction" if isinstance(tokens, int) else ""
                yield Event("compaction", "Context compaction completed" + detail,
                            {"phase": "completed", "trigger": info.get("trigger"), "pre_tokens": tokens})

            elif kind == "stream_event":
                inner = ev.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text") and not ev.get("parent_tool_use_id"):
                        yield Event("delta", delta["text"])

            elif kind == "system" and ev.get("subtype") in (
                    "task_started", "task_progress", "task_updated", "task_notification"):
                subtype = ev["subtype"]
                patch = ev.get("patch") or {}
                task = str(ev.get("task_id") or "")
                moved = patch.get("is_backgrounded", ev.get("is_backgrounded"))
                if isinstance(moved, bool):
                    backgrounded[task] = moved
                if backgrounded.get(task) is False:
                    background.discard(task)
                status = str(patch.get("status") or ev.get("status") or
                             ("running" if subtype == "task_progress" else background_status.get(task)) or
                             ("started" if subtype == "task_started" else "running"))
                background_status[task] = status
                if status in ("completed", "failed", "stopped", "cancelled", "killed"):
                    background.discard(task)
                    # Foreground Bash emits this notification too, then returns
                    # its tool result inside the current turn. It owes no reply.
                    # Nor does a stopped task: the CLI answers only work that
                    # ran to its end (2.1.291), so counting one waited forever.
                    if (subtype == "task_notification" and status in ("completed", "failed")
                            and backgrounded.get(task) is not False and task and task not in notified):
                        notified.add(task)
                        followups += 1
                elif (task and backgrounded.get(task) is not False
                      and (subtype in ("task_started", "task_progress") or patch.get("status")
                           or ev.get("status") or moved is True)):
                    background.add(task)
                detail = str(ev.get("summary") or ev.get("description") or patch.get("description") or "")
                usage = ev.get("usage") or patch.get("usage") or {}
                elapsed = usage.get("duration_ms")
                suffix = f" · {elapsed / 1000:.1f}s" if isinstance(elapsed, (int, float)) else ""
                yield Event("tool", f"Background {task} · {status} · {detail}{suffix}"[:4000],
                            {"tool": "background", "task_id": task, "status": status})

            elif kind == "tool_progress":
                yield Event("tool", f"{ev.get('tool_name') or 'Tool'} · {ev.get('elapsed_time_seconds', 0)}s",
                            {"tool": str(ev.get("tool_name") or ""), "task_id": ev.get("task_id")})

            elif kind == "control_request":
                rid, request = str(ev.get("request_id")), ev.get("request") or {}
                if request.get("subtype") != "can_use_tool":
                    self._send({"type": "control_response", "response": {
                        "subtype": "error", "request_id": rid, "error": "unsupported"}})
                    continue
                name, args = str(request.get("tool_name") or "?"), request.get("input") or {}

                def reply(allow, answers=(), rid=rid, args=args, name=name):
                    if allow and name == "AskUserQuestion":
                        # The CLI reads the answers off the input, by question text.
                        args = {**args, "answers": {str(q.get("question")): a
                                                    for q, a in zip(args.get("questions") or [], answers)}}
                    said = ({"behavior": "allow", "updatedInput": args} if allow
                            else {"behavior": "deny", "message": DECLINED})
                    return {"type": "control_response",
                            "response": {"subtype": "success", "request_id": rid, "response": said}}

                outside = self._outside(args.get(WRITES_PATH[name])) if name in WRITES_PATH else ""
                yield self._approval(rid, reply, name, args, _tool_brief({"name": name, "input": args}), outside)

            elif kind == "assistant":
                blocks = list(_blocks(ev.get("message") or {}))
                progress = "\n\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                if progress.strip() and ev.get("parent_tool_use_id"):
                    yield Event("tool", f"Background · {progress}"[:4000], {"tool": "background"})
                elif progress.strip():
                    yield Event("progress", progress)
                for block in blocks:
                    if block.get("type") == "tool_use":
                        # `tool` names it: whether a web search ran is read from here (`planning`).
                        yield Event("tool", _tool_brief(block), {"tool": str(block.get("name") or "")})

            elif kind == "system" and ev.get("subtype") == "hook_response":
                event = _hook(str(ev.get("hook_event") or ""), *_claude_hook(ev))
                if event:
                    yield event

            elif kind == "user" and ev.get("isReplay") and not ev.get("isSynthetic"):
                with self._steering:
                    if not self._prompt_seen:
                        self._prompt_seen = True
                    elif self._unread:
                        self._unread -= 1

            elif kind == "user":
                for block in _blocks(ev.get("message") or {}):
                    if block.get("type") == "tool_result":
                        content = block.get("content") or ""
                        if isinstance(content, list):
                            content = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
                        yield Event("tool", str(content)[:4000],
                                    {"tool": "tool_result", "tool_use_id": block.get("tool_use_id")})

            elif kind == "result":
                if not ev.get("is_error"):
                    origin = ev.get("origin") or {}
                    injected = origin.get("kind") == "task-notification" or (not origin and replay_human is False)
                    if injected:
                        followups = max(0, followups - 1)
                    else:
                        # Older CLIs omit origin: after the foreground result,
                        # terminal tasks still owe their completion response.
                        if not origin and replay_human is None and foreground_result_seen:
                            followups = max(0, followups - 1)
                        foreground_result_seen = True
                    replay_human = None
                    if background or followups or (injected and ev.get("num_turns") == 0):
                        # Not after a steer: its replay says only that it was
                        # read, not answered, and closing would lose it. A steer
                        # landing after this check is still unread, so
                        # `_closing` refuses.
                        # ponytail: any steer in the turn waits for the tasks,
                        # as before; pair steers with their results to narrow it.
                        if not (background and self.settled and self.settled(str(ev.get("result") or ""))
                                and not self._steered and self._closing()):
                            yield Event("tool", f"Background · waiting for {max(len(background), followups)} "
                                                "follow-up(s)", {"tool": "background"})
                            continue
                        # The work is reported closed, yet tasks still run: left
                        # over, and holding the turn would keep the review from
                        # starting. The process and its job take them down; the
                        # next turn resumes the conversation.
                        left = sorted(background)
                        self.close()
                        for task in left:
                            yield Event("tool", f"Background {task} · stopped · left running after the work closed",
                                        {"tool": "background", "task_id": task, "status": "stopped"})
                if not self._closing():
                    # A steered message still owns a later provider result.
                    continue
                usage = ev.get("usage") or {}
                self.usage = {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                              "cost_usd": ev.get("total_cost_usd"), "scope": "turn"}
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


def _claude_hook(ev: dict) -> tuple[str, str]:
    """What one Claude hook said to the person, and what it put in context.

    JSON output speaks by its fields; plain output of the two events that
    inject is context; a blocking exit (2) says why on stderr."""

    output = str(ev.get("output") or "").strip()
    said, context = "", ""
    try:
        data = json.loads(output) if output.startswith("{") else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        specific = data.get("hookSpecificOutput") or {}
        said = str(data.get("systemMessage") or specific.get("permissionDecisionReason")
                   or (data.get("reason") if data.get("decision") == "block" else "") or "")
        context = str(specific.get("additionalContext") or "")
    elif ev.get("hook_event") in ("SessionStart", "UserPromptSubmit"):
        context = output
    if str(ev.get("exit_code")) == "2":
        said = said or str(ev.get("stderr") or "").strip()
    return said, context


def _hook(name: str, said: str, context: str) -> Event | None:
    """One hook that said something, as the screen shows it; `None` for a
    silent one. The context rides in `meta` whole — it is what went in."""

    if not said.strip() and not context.strip():
        return None
    return Event("hook", said.strip() or f"{name} · 문맥 {len(context):,}자",
                 {"event": name, **({"context": context} if context.strip() else {})})


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _tool_brief(block: dict) -> str:
    """One line per tool. All it has to show is what is being done — and for
    a command, the command itself beside what it says it is for: how long it
    may take is read off the command, not off its description."""

    name = str(block.get("name") or "?")
    args = block.get("input") or {}
    command = args.get("command")
    ran = f" · $ {command.strip()[:160]}" if isinstance(command, str) and command.strip() else ""
    for key in ("description", "file_path", "pattern", "path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"{name} · {value.strip()[:90]}{ran}"
    return f"{name}{ran}"


def explain(answer: str, model: str = "", effort: str = ""):
    """Hand only the answer to a fresh session.

    Not the search prompt, not the channel, not the conversation so far — the
    plain explanation is written from the answer alone.
    """
    yield from oneshot("chat-explain.md", {"source_answer": answer}, model, effort)


def oneshot(prompt: str, payload: dict, model: str = "", effort: str = "", halt: threading.Event | None = None):
    """One turn of a fresh session with no tools, in an empty folder: the file
    `tool/prompts/<prompt>` as the system prompt, `payload` as JSON the only
    thing said to it. `halt`, set from any thread, stops the turn (`stop`);
    the session is this call's and is closed on every way out."""
    system = (Path(__file__).resolve().parents[1] / "prompts" / prompt).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="wiki-oneshot-") as folder:
        chat = ChatSession(Path(folder), tools="", system=system, model=model,
                           effort=effort, isolated=True)
        ended = threading.Event()

        def watch() -> None:
            # Lives as long as the turn: a halt heard stops this turn's process, and nothing after it ends.
            while not ended.is_set():
                if halt.wait(0.1):
                    chat.stop(halt)
                    return

        watcher = threading.Thread(target=watch, daemon=True) if halt is not None else None
        if watcher:
            watcher.start()
        try:
            yield from chat.say(json.dumps(payload, ensure_ascii=False), halt)
        finally:
            ended.set()
            if watcher:
                watcher.join()
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
