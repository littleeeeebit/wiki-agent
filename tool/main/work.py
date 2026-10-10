"""work — the selected checkout and isolated trees, with task-specific sessions.

The screen names a worktree by path, and a path from the screen is never
opened as it is: it has to be on `workspace`'s own list for the selected
project. Implementation agents have full access; product questions still arrive
as interactive events. Task branches share a directory, not a conversation.

A turn runs in its own thread, not in the response that started it. Its
events pile up in a `Run`, and every response only tails that buffer, so a
reloaded window reattaches and a closed tab stops nothing.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import ChatSession, claude_usage, codex_usage
from common.process import background_options
from common import errorlog
from workspace import remove, worktrees

# One lock with the wiki query's. A project switch reads every hold and
# changes the project under it, so a hold can never land in between.
from . import memory, runtime
from .query import ROOT, _lock, current_repo, hold, keep, project, resumable, sse, streaming

LOGS = ROOT / "raw" / "work"
MAX_REPLAY = 200
# An answer that closes the work: a task's completion report, a fix round's
# dispositions. A background task still running after one is left over, and
# the turn does not wait on it (`ChatSession.settled`).
SETTLING = re.compile(r"^```(?:done-report|disposition)[ \t]*\r?$", re.M)
KEEPALIVE = 15.0   # seconds a tail waits before it checks the screen is still there
HALT_WAIT = 30.0   # seconds a forced removal waits for what it stopped to let go

router = APIRouter()

_sessions: dict[str, ChatSession] = {}    # worktree path -> its session
_busy: dict[str, object] = {}   # worktree path -> the hold of its running turn
_runs: dict[str, "Run"] = {}    # worktree path -> its last turn, kept until the next one starts
_turns: dict[threading.Thread, "Run"] = {}  # includes completed turns still running their callbacks
_queued: dict[str, "Order"] = {}   # worktree path -> the instruction waiting for it to be let go
_provider_usage: dict = {}
_provider_usage_lock = threading.Lock()


def close_all(wait: bool = True) -> list[threading.Thread]:
    with _lock:
        alive = list(_sessions.values())
        _sessions.clear()
        turns = list(_turns.items())
        _queued.clear()
    for _, run in turns:
        run.halt.set()
        run.chat.stop(run.halt)
    for chat in alive:
        chat.close()
    threads = [thread for thread, _ in turns if thread is not threading.current_thread()]
    for thread in threads if wait else []:
        thread.join()
    return threads


def ours(path: str, repo: Path | None = None) -> Path:
    """`path` as `workspace` lists it for `repo` — the selected project unless
    a request already took its repository with its hold — or 404."""

    for row in worktrees(repo or current_repo()):
        if str(row["path"]) == path:
            return row["path"]
    raise HTTPException(404, "선택한 프로젝트의 작업트리가 아니다")


def known(path: str) -> Path:
    """A path to read from: one the server made a session for, whichever
    project that was in, or else one of the selected project's. The session's
    path went through `ours` once, when it was made; a loop in another project
    keeps its worktree readable after a switch."""

    return Path(path) if path in _sessions else ours(path)


@router.get("/api/providers/{provider}/usage")
def provider_usage(provider: Literal["claude", "codex"], path: str = "") -> dict:
    if path:
        known(path)
        chat = _sessions.get(path)
        if chat:
            return {"provider": "codex" if chat.is_codex else "claude", **chat.status()}
    with _provider_usage_lock:
        cached = _provider_usage.get(provider)
        if not cached or time.monotonic() - cached["at"] >= (60 if provider == "codex" else 300):
            try:
                data = {"quota": codex_usage() if provider == "codex" else claude_usage(), "error": ""}
            except Exception as exc:
                errorlog.record("provider-usage", exc, provider=provider)
                data = {"quota": [], "error": "계정 사용량을 받지 못했다"}
            cached = _provider_usage[provider] = {"at": time.monotonic(), "data": data}
        return {"provider": provider, "live": False, "usage": {}, "connection_ms": None, **cached["data"]}


@router.get("/api/providers/usage")
def all_provider_usage() -> dict:
    from . import loop, query

    with _lock:
        chats = list(_sessions.values()) + list(query._sessions.values())
        chats += list(loop._cells.values())
        chats += [run.chat for run in loop._review_runs.values()]
    providers = []
    for provider in ("claude", "codex"):
        matching = [chat for chat in chats if chat.is_codex == (provider == "codex")]
        if not matching:
            providers.append(provider_usage(provider))
            continue
        windows = {}
        for chat in sorted(matching, key=lambda chat: chat._quota_at):
            for window in chat.quota:
                windows[window["name"]] = window
        latest = max(matching, key=lambda chat: chat._quota_at)
        status = latest.status()
        for window in status["quota"]:
            windows[window["name"]] = window
        providers.append({"provider": provider, **status, "quota": list(windows.values())})
    return {"providers": providers}


def diff_git(path: Path, *args: str, codes=(0,)) -> str:
    command = ["git", "-c", "core.quotepath=false", "-C", str(path), *args]
    expired = threading.Event()
    preview = args[0] == "diff" and "--numstat" not in args
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=errors, text=True,
            encoding="utf-8", errors="replace", **background_options()) as proc:
        def timeout():
            expired.set()
            proc.kill()

        timer = threading.Timer(10, timeout)
        timer.daemon = True
        timer.start()
        try:
            # One extra character carries truncation; never capture the full patch.
            output = proc.stdout.read(200_001 if preview else -1)
            truncated = preview and len(output) > 200_000
            if truncated:
                proc.kill()
            proc.wait()
            if expired.is_set():
                raise subprocess.TimeoutExpired(command, 10)
            if not truncated and proc.returncode not in codes:
                errors.seek(0)
                raise RuntimeError(errors.read(2000).decode("utf-8", "replace").strip() or "Git 변경 현황을 읽지 못했다")
            return output
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()


@router.get("/api/work/diff")
def changes(path: str, file: str = "", preview: bool = True) -> dict:
    """Read actual files, including shell edits, the index and new files.

    A running turn pins HEAD before the host starts, so committing does not
    erase its changes from the screen. No index mutation or external diff driver.
    """
    root = known(path)
    run = _runs.get(path)
    from . import specs

    spec = specs.owner(root)
    base = (spec or {}).get("start_head") or getattr(run, "diff_base", None)
    if not base:
        base = next((r.get("diff_base") for r in reversed(recall(root)) if r.get("diff_base")), None) or "HEAD"
    flags = ("--no-ext-diff", "--no-textconv", "--no-color", "--no-renames")
    try:
        branch = ((spec or {}).get("pr") or {}).get("base") or (spec or {}).get("return_branch")
        if branch:
            baseline = ""
            for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
                try:
                    candidate = diff_git(root, "merge-base", ref, "HEAD").strip()
                    if not baseline or diff_git(root, "merge-base", baseline, candidate).strip() == baseline:
                        baseline = candidate
                except RuntimeError:
                    continue  # An unfetched remote can use the selected local base.
            base = baseline or base
        totals = {"files": 0, "added": 0, "deleted": 0, "binary": 0, "unknown": 0}
        entries = []

        def count(output, untracked=""):
            rows = iter(output.split("\0"))
            for row in rows:
                if not row:
                    continue
                added, deleted, name = row.split("\t", 2)
                if not name:
                    # --no-index uses a NUL-separated old/new pair even with
                    # rename detection disabled. Neither path is another stat.
                    next(rows)
                    name = next(rows)
                entries.append({"path": untracked or name, "added": None if added == "-" else int(added),
                                "deleted": None if deleted == "-" else int(deleted),
                                "binary": added == "-", "untracked": bool(untracked)})
                totals["files"] += 1
                if added == "-" or deleted == "-":
                    totals["binary"] += 1
                else:
                    totals["added"] += int(added)
                    totals["deleted"] += int(deleted)

        count(diff_git(root, "diff", "--numstat", "-z", *flags, base, "--"))
        patch = "" if file or not preview else diff_git(root, "diff", *flags, base, "--")
        untracked = diff_git(root, "ls-files", "--others", "--exclude-standard", "-z")
        omitted = []
        for name in untracked.split("\0"):
            if not name:
                continue
            target = root / name
            # A symlink may target a private file outside this worktree.
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                totals["files"] += 1
                totals["unknown"] += 1
                omitted.append(name)
                entries.append({"path": name, "added": None, "deleted": None, "binary": False, "untracked": True})
                continue
            count(diff_git(root, "diff", "--no-index", "--numstat", "-z", *flags,
                           "--", "/dev/null", name, codes=(0, 1)), name)
            if target.stat().st_size > 1_000_000:
                omitted.append(name)
            elif preview and (not file or file == name) and len(patch) <= 200_000:
                patch += diff_git(root, "diff", "--no-index", *flags, "--", "/dev/null", name, codes=(0, 1))
        if file:
            if file not in {entry["path"] for entry in entries}:
                raise HTTPException(404, "변경된 파일이 아니다")
            if file not in untracked.split("\0"):
                patch = diff_git(root, "diff", *flags, base, "--", ":(literal)" + file)
        # ponytail: cap the preview at 200k characters; paginate if large patches become common.
        return {"diff": patch[:200_000], "base": base, "truncated": len(patch) > 200_000,
                "omitted": omitted, "totals": totals, "files": entries}
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, f"변경 현황을 읽지 못했다 — {exc}") from exc


def record(path: Path) -> Path:
    """Use the owning task's log, or the legacy repository worktree folder.
    Task names repeat across repositories; a project switch cannot redirect writes."""

    from . import specs

    spec = specs.owner(path)
    if spec and spec.get("workspace_mode") == "branch":
        return LOGS / spec["repo"] / f"{spec['id']}.jsonl"
    return LOGS / path.parent.name / f"{path.name}.jsonl"


def remember(path: Path, role: str, text: str, **extra) -> None:
    memory.append(record(path), {"ts": time.time(), "role": role, "text": text, "path": str(path), **extra})


def recall(path: Path) -> list[dict]:
    """This worktree's turns since the last clear. Rows of a removed worktree
    that had the same name are left out by path."""

    file = record(path)
    if not file.exists():
        return []
    rows = []
    for line in file.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("path") == str(path):
            rows.append(row)
    return memory.since_clear(rows)[-MAX_REPLAY:]


# -- Worktrees --------------------------------------------------------------

class Where(BaseModel):
    path: str


@router.get("/api/worktrees")
def listing() -> dict:
    # The project's name rides along, so the screen can tell a list of the
    # project it shows from one of the project the server has switched to.
    with _lock:
        name, repo = project(), current_repo()
    try:
        rows = worktrees(repo)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    out = []
    for row in rows:
        from . import specs

        if row["path"].resolve() == repo.resolve() and specs.dismissed(repo.name, row["branch"]):
            continue
        chat = _sessions.get(str(row["path"]))
        out.append({**row, "path": str(row["path"]), "name": row["branch"] or row["path"].name,
                    "primary": row["path"].resolve() == repo.resolve(),
                    "live": bool(chat and chat.alive), "busy": str(row["path"]) in _busy})
    return {"project": name, "repo": str(repo), "rows": out}


class Removal(Where):
    # A person's explicit delete: what runs there is stopped first, and
    # uncommitted changes go with the worktree.
    force: bool = False


def halt_all(path: str, repo: Path) -> None:
    """Stop a spec's loop in `path` of `repo`, then its running turn, and
    wait for the turn to let go of the worktree. `repo` is the one the path
    was checked against: a switch meanwhile must not aim this at another
    project's spec of the same id."""

    from . import loop, specs  # both import this module

    with _lock:
        _queued.pop(path, None)
    spec = specs.owner(Path(path))
    if spec and loop.LOOPING.fullmatch(spec["state"]):
        loop.halt_loop(repo.name, spec["id"])
    run = _runs.get(path)
    if run is not None and not run.done:
        run.halt.set()
        run.chat.stop(run.halt)
        with run.wake:
            run.wake.wait_for(lambda: run.done, HALT_WAIT)


@router.post("/api/worktrees/remove")
def clear(body: Removal) -> dict:
    # The repository is read once and used to the end — for the check, the
    # stops, the hold and the removal. Read again later, a switch in between
    # handed it another repository, and a stop aimed at another project.
    with _lock:
        repo = current_repo()
    if Path(body.path).resolve() == repo.resolve():
        raise HTTPException(409, "원본 저장소는 삭제하지 않는다. 작업 브랜치는 Git에서 관리해라")
    if body.force:
        # Only that project's worktree is stopped: a path from anywhere else
        # is a 404 before anything halts.
        ours(body.path, repo)
        halt_all(body.path, repo)
    # Held for the whole removal, as a turn holds it. Checked and let go, a
    # new instruction was accepted while the worktree was being deleted.
    # Forced, the hold is tried for a while: a stopped loop lets go of the
    # worktree once its step sees the halt.
    deadline = time.monotonic() + (HALT_WAIT if body.force else 0)
    while True:
        try:
            with _lock:
                release = hold(_busy, _lock, body.path, "에이전트가 도는 동안은 지우지 않는다")
            break
        except HTTPException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
    try:
        path = ours(body.path, repo)
        forget(path)
        try:
            text = remove(repo, path, force=body.force)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        from . import specs  # `specs` imports this module

        specs.forsaken(path)
        # The same task name makes the same path again, and the record is
        # keyed by it: a new `t1` came up with the old one's conversation, and
        # `--resume` carried the old CLI session into the new work. The record
        # is set aside, not deleted.
        file = record(path)
        if file.exists():
            file.rename(file.with_name(f"{path.name}.{time.time_ns()}.jsonl"))
        return {"text": text}
    finally:
        release()


def busy() -> bool:
    """Is a short request — making, removing, resetting — holding a worktree?
    The project stays put while one is. A running turn does not count: it
    took its repository with its hold, and its session keeps its path
    reachable after a switch."""

    with _lock:
        return any(h.kind == "short" for h in _busy.values())


def waiting(path: str) -> bool:
    """Is the running turn of `path` waiting on a person's answer? An
    approval that came already answered — by a session rule, or refused
    outside the worktree — carries `by` and waits on nobody."""

    run = _runs.get(path)
    if run is None or run.done:
        return False
    with run.wake:
        asked = {e["meta"].get("id") for e in run.events if e["kind"] == "approval" and "by" not in e["meta"]}
        answered = {e["meta"].get("id") for e in run.events if e["kind"] == "answered"}
    return bool(asked - answered)


def forget(path: Path) -> None:
    """Close the worktree's session and drop its last turn, before the
    worktree goes. The record stays."""

    with _lock:
        chat = _sessions.pop(str(path), None)
        _runs.pop(str(path), None)
        _queued.pop(str(path), None)
    if chat:
        chat.close()


# -- The agent --------------------------------------------------------------

class Order(BaseModel):
    path: str
    text: str
    model: str = ""
    effort: str = ""
    fast: bool = False


class Answer(BaseModel):
    path: str
    session_id: str      # this program's id for the session that asked
    id: str
    allow: bool
    scope: Literal["once", "session"] = "once"
    answers: list[str] | None = None   # a question's, one per question


def session(path: Path, model: str, effort: str, fast: bool = False) -> ChatSession:
    """The worktree's write session, started if there is none.

    Another model of the same CLI reconnects with `--resume`. Another CLI is
    a new conversation — neither can resume the other's. A worktree a spec
    owns gets the spec as its system prompt, every time a session is made.
    """

    from . import specs  # `specs` imports this module

    key = str(path)
    bypass = settings()["bypass"]
    prompt = specs.system(path)
    with _lock:
        chat = _sessions.get(key)
        if chat is not None and chat.is_codex != model.startswith("codex:"):
            _sessions.pop(key)
            chat.close()
            remember(path, "context", "CLI 변경")
            chat = None
        if chat is not None and getattr(chat, "_spec_system", prompt) != prompt:
            chat.close()
            _sessions.pop(key)
            chat = None
        if chat is None:
            chat = ChatSession(path, model=model, effort=effort, write=True, system=prompt,
                               bypass=bypass)
            chat._spec_system = prompt
            chat.settled = SETTLING.search
            chat.session_id = resumable(recall(path), chat.is_codex)
            _sessions[key] = chat
        else:
            if chat.bypass != bypass:
                # Fixed at start-up: the next turn starts it again and resumes.
                chat.bypass = bypass
                chat.close()
            chat.reconfigure(model, effort)
        if getattr(chat, "fast", False) != fast:
            chat.fast = fast
            chat.close()
        spec = specs.owner(path)
        choice = {"model": model, "effort": effort, "fast": fast}
        if spec and spec.get("cell") != choice:
            specs.update(spec["repo"], spec["id"], cell=choice)
        return chat


# -- Settings ---------------------------------------------------------------

WORK_DEFAULTS = {"bypass": True}


def settings() -> dict:
    return dict(WORK_DEFAULTS)


class WorkSettings(BaseModel):
    bypass: bool


@router.get("/api/work/settings")
def get_settings() -> dict:
    return settings()


@router.post("/api/work/settings")
def set_settings(body: WorkSettings) -> dict:
    from . import loop

    loop.store(bypass=True)
    return settings()


@router.get("/api/work/log")
def log(path: str) -> dict:
    where = known(path)
    chat = _sessions.get(path)
    run = _runs.get(path)
    return {"rows": [shown(r) for r in recall(where) if r.get("role") in ("user", "assistant")],
            "session_id": chat.id if chat else "", "busy": path in _busy,
            "rules": chat.rules if chat else [],
            "queued": _queued[path].text if path in _queued else None,
            "running": None if run is None or run.done else
            {"turn": run.turn, "session_id": run.session_id, "seq": len(run.events) - 1}}


class Clearing(Where):
    keep: Literal["memory", "delete"]


@router.post("/api/work/reset")
def reset(body: Clearing) -> dict:
    # Held until the reset is on record, or a turn in between resumed the CLI
    # context the reset was meant to drop.
    with _lock:
        repo = current_repo()
        release = hold(_busy, _lock, body.path, "에이전트가 도는 동안은 비우지 않는다")
    try:
        path = ours(body.path, repo)
        with _lock:
            chat = _sessions.pop(body.path, None)
            _runs.pop(body.path, None)
        rows = memory.clear(record(path), lambda r: r.get("path") == str(path),
                            {"ts": time.time(), "role": "context", "text": memory.CLEARED, "path": str(path)},
                            body.keep == "delete")
        if chat:
            chat.close()
    finally:
        release()
    # The memory goes in the original checkout's `.wiki/`: the worktree's
    # goes with the worktree.
    feed.put({"kind": "work-record", "path": str(path)})
    cfg = {"model": chat.model or "", "effort": chat.effort or ""} if chat and body.keep == "memory" else {}
    return {"ok": True, **keep(repo, f"work-{path.name}", rows, cfg, body)}


class Run:
    """One worktree's turn, as a buffer of its events in order.

    Each event is the screen's payload plus three fields.
    `seq` is its place here from 0: a screen that reattaches after `seq` k
    gets k+1 onwards. `turn` tells this turn's events from the next one's.
    `ts` is when it came:
    a screen that reattaches still knows how long the last step has run.
    """

    def __init__(self, chat: ChatSession) -> None:
        self.turn = uuid.uuid4().hex
        self.started_at = time.time()
        self.chat = chat
        self.session_id = chat.id
        self.events: list[dict] = []
        self.done = False
        self.halt = threading.Event()   # set by a stop; the session reads it too
        self.wake = threading.Condition()
        self.diff_base: str | None = None

    def put(self, payload: dict) -> None:
        if payload.get("kind") == "error" or (payload.get("meta") or {}).get("error"):
            errorlog.record("agent", payload.get("text", "Failed turn"), turn=self.turn, session=self.session_id)
        with self.wake:
            self.events.append({**payload, "seq": len(self.events), "turn": self.turn, "ts": time.time()})
            self.wake.notify_all()
        meta = payload.get("meta") or {}
        if payload["kind"] == "approval" and not meta.get("by"):
            notice("에이전트가 답을 기다린다", Path(getattr(self.chat, "repo", getattr(self.chat, "path", "wiki-agent"))).name)

    def finish(self) -> None:
        with self.wake:
            self.done = True
            self.wake.notify_all()
        if not self.halt.is_set():
            failed = any(e["kind"] == "error" or (e.get("meta") or {}).get("error") for e in self.events)
            notice("에이전트 실행 실패" if failed else "에이전트 실행 완료",
                   Path(getattr(self.chat, "repo", getattr(self.chat, "path", "wiki-agent"))).name)


class Feed:
    """What the server changes on its own — a spec's state, a turn it
    started — as one buffer every screen tails. Shaped like a `Run`, so
    `tail` serves it; it never finishes.

    ponytail: every event is kept for the server's life, a few hundred a day.
    Drop the oldest and let a screen that asks from before them reread the
    lists, if a server ever runs for months.
    """

    def __init__(self) -> None:
        self.generation = uuid.uuid4().hex
        self.events: list[dict] = []
        self.done = False
        self.wake = threading.Condition()

    def put(self, payload: dict) -> None:
        with self.wake:
            self.events.append({**payload, "seq": len(self.events)})
            self.wake.notify_all()


feed = Feed()


def notice(title: str, body: str) -> None:
    feed.put({"kind": "notice", "title": title, "body": body, "ts": time.time()})


def tail(run: Run, after: int):
    """The run's events after `seq` `after`, as they come, until it is done.

    A screen that leaves only stops its own tail. The comment line every
    `KEEPALIVE` is what finds out it left: without a write, a tail of a turn
    waiting on an approval held its thread for as long as the approval waited.
    """

    n = after + 1
    while True:
        with run.wake:
            if n >= len(run.events) and not run.done:
                run.wake.wait(KEEPALIVE)
            new, finished = run.events[n:], run.done
        n += len(new)
        for payload in new:
            yield sse(payload)
        if finished:
            return
        if not new:
            yield ": keep-alive\n\n"


def steps(events: list[dict]) -> list[dict]:
    """What ran and what was asked, in one line in order, for the record.

    An approval's `input` is left out: a `Write` carries the whole file, and
    what was written is in the worktree and its commits.
    """

    out: list[dict] = []
    asked: dict[str, dict] = {}
    for ev in events:
        meta = ev["meta"]
        # A hook's `context` is left out too: it is the wiki's, and the wiki has it.
        if ev["kind"] in ("tool", "progress", "said", "hook", "compaction"):
            out.append({"kind": ev["kind"], "text": ev["text"],
                        **({"phase": meta.get("phase"), "pre_tokens": meta.get("pre_tokens")} if ev["kind"] == "compaction" else {}),
                        **({"command": True} if meta.get("tool") in ("commandExecution", "command_execution", "tool_result") else {})})
        elif ev["kind"] == "approval":
            # `none`: the turn ended before anyone answered.
            step = asked[str(meta.get("id"))] = {
                "kind": "approval", "tool": meta.get("tool", ""), "text": ev["text"],
                "answer": meta.get("answer", "none"), "by": meta.get("by", "person"),
                **({"input": meta["input"]} if meta.get("tool") in ("AskUserQuestion", "requestUserInput") else {})}
            out.append(step)
        elif ev["kind"] == "answered" and str(meta.get("id")) in asked:
            asked[str(meta["id"])].update(answer="allow" if meta["allow"] else "deny", by=meta["by"],
                                          **({"answers": meta["answers"]} if meta.get("answers") else {}))
        elif ev["kind"] == "done":
            # Stop hooks can arrive between the final text block and its result.
            last = next((i for i in range(len(out) - 1, -1, -1) if out[i]["kind"] != "hook"), None)
            if last is not None and out[last]["kind"] == "progress" and out[last]["text"] == ev["text"]:
                del out[last]
    return out


def shown(row: dict) -> dict:
    """A record row as the screen reads it. Rows from before `steps` had only
    `tools`, the lines of what ran."""

    if "tools" in row and "steps" not in row:
        row = {**row, "steps": [{"kind": "tool", "text": t} for t in row["tools"]]}
        del row["tools"]
    return row


def run_turn(path: Path, run: Run, text: str, release, decide: bool = False) -> None:
    """One turn, to its end, whoever is watching. The hold and the record are
    let go here, so a turn nobody watched is still on record.

    `decide`: a turn the server starts on its own, where Jev may choose to
    gather evidence first or to ask the person instead (`decisions.start_turn`).
    Decided here, on the turn's thread, so a stop reaches it; what is sent is
    what the record keeps."""

    chat, final, failed, meta, then = run.chat, "", "", {}, None
    try:
        events = None
        if decide:
            from . import decisions  # `decisions` reads this module's sessions

            sent, shown, kind = decisions.start_turn(path, run, text)
            if run.halt.is_set():
                sent, shown, kind = None, "사람이 멈춤", "error"
            if sent is None:
                # Nothing goes to the CLI: a question for the person, or why not.
                final, failed = (shown, "") if kind == "done" else ("", shown)
                run.put({"kind": kind, "text": shown, "meta": {}, "session_id": chat.id, "parent_id": chat.parent_id})
                events = ()
            else:
                text = sent
                remember(path, "user", text)
        for ev in chat.say(text, run.halt) if events is None else events:
            if ev.kind == "context":   # the CLI's conversation could not be resumed
                remember(path, "context", ev.text)
                ev.kind = "tool"
            if ev.kind == "done":
                final, meta = ev.text, dict(ev.meta)
                if meta.pop("error", False):
                    failed = final or "완료된 답이 없다"
            elif ev.kind == "error":
                failed = ev.text = "사람이 멈춤" if run.halt.is_set() else ev.text
            run.put({"kind": ev.kind, "text": ev.text, "meta": ev.meta,
                     "session_id": ev.session_id, "parent_id": ev.parent_id})
        if not failed and not final.strip():
            failed = "완료된 답이 없다"
            run.put({"kind": "error", "text": failed, "meta": {}, "session_id": chat.id, "parent_id": None})
        if not failed:
            # Still holding the worktree: the gate runs where nothing else
            # writes, and its lines are in this turn's record.
            from . import specs

            then = specs.check(path, run, final)
    except Exception as exc:  # a turn that broke still owes the screen a reason
        errorlog.record("agent-exception", exc, turn=run.turn, path=str(path))
        # A stop while the process started breaks the start, not the turn.
        failed = "사람이 멈춤" if run.halt.is_set() else f"{type(exc).__name__}: {exc}"
        run.put({"kind": "error", "text": failed, "meta": {}, "session_id": chat.id, "parent_id": None})
    finally:
        try:
            with run.wake:
                made = steps(run.events)
                # Where the answer stood among the steps: what came after it —
                # the gate — is shown after it, in the record too.
                at = next((i for i, e in enumerate(run.events) if e["kind"] == "done"), None)
                answered = {} if at is None else {"answered": len(steps(run.events[:at + 1]))}
            remember(path, "assistant", final, error=failed, steps=made,
                     turn=run.turn, cell=chat.id, started_at=run.started_at, cancelled=run.halt.is_set(),
                     provider="codex" if chat.is_codex else "claude", diff_base=run.diff_base, **answered, **meta)
        except Exception as exc:
            # Transcript publication is not the execution boundary. A lost
            # record must not skip an already accepted review/plan handoff.
            errorlog.record("work-record", exc, turn=run.turn, path=str(path))
        finally:
            # Released before the end is told, so a screen that sees the end
            # can send the next instruction at once.
            release()
            try:
                run.finish()
                feed.put({"kind": "work-record", "path": str(path)})
            except Exception as exc:
                errorlog.record("work-publication", exc, turn=run.turn, path=str(path))
    try:
        if then:
            then()
    finally:
        dispatch(path, run)


def dispatch(path: Path, ended: Run) -> None:
    """The instruction that waited for this worktree, once `ended` let it go
    and after any turn the server started then — the plan row's. Taken
    meanwhile — that turn, a person's — it waits for that turn's end. A
    stopped run drops it: a stop is not "go on"."""

    key = str(path)
    with _lock:
        if ended.halt.is_set():
            _queued.pop(key, None)
            return
        order = _queued.get(key)
        if order is None or key in _busy:
            return
        del _queued[key]
        release = hold(_busy, _lock, key, "", kind="turn")
    try:
        begin(path, session(path, order.model, order.effort, order.fast), order.text, release)
    except Exception as exc:
        # Nobody's request is waiting on this thread: the failure and the
        # instruction itself go on record, and screens read it again.
        release()
        errorlog.record("work-queue", exc, path=key, turn=ended.turn)
        remember(path, "assistant", "", error=f"기다리던 지시를 보내지 못했다 — {type(exc).__name__}: {exc}"
                                                f"\n지시: {order.text}")
        feed.put({"kind": "turn", "path": key, "turn": "", "session_id": ""})


def attached(path: str) -> Run:
    """The run of a path the server already made a session for.

    Not `ours`: that reads the selected project, and a turn started before a
    project switch must stay reachable. The path went through `ours` once, when
    its session was made. 404 for a path with no session, 410 for one whose
    turn is gone."""

    run = _runs.get(path)
    if run is None and path not in _sessions:
        raise HTTPException(404, "세션이 없는 경로다")
    if run is None:
        raise HTTPException(410, "그 턴은 이제 없다")
    return run


@router.post("/api/work/say")
def say(body: Order) -> StreamingResponse:
    """One instruction to the worktree's agent. The turn runs on its own; the
    response is a tail of its events.

    Every payload carries the session's own `session_id` beside the event, so
    the screen can drop a late one and an approval can name who asked.
    """

    text = body.text.strip()
    if not text:
        raise HTTPException(400, "빈 지시")
    # Held from acceptance, and before the path is checked: from here the
    # worktree cannot be removed or reset. The repository is taken with the
    # hold and the path checked against it: a switch does not wait for a
    # turn, and one landing between the hold and a check against the selected
    # project turned this turn into a 404. From here the turn's thread owns
    # the release.
    with _lock:
        repo = current_repo()
        release = hold(_busy, _lock, body.path, "이 작업트리의 에이전트가 아직 돌고 있다", kind="turn")
    try:
        path = ours(body.path, repo)
        run = begin(path, session(path, body.model, body.effort, body.fast), text, release)
    except BaseException:
        release()
        raise
    return streaming(tail(run, -1))


def begin(path: Path, chat: ChatSession, text: str, release, run: Run | None = None, decide: bool = False) -> Run:
    """Start one turn of `chat` on its own thread, which owns `release` from
    here. The caller holds the worktree already. `run` is one the caller made
    first, so a stop could reach it before the thread starts. `decide`: the
    turn's thread decides what is sent, and records it (`run_turn`)."""

    run = run or Run(chat)
    from . import refactor_continuation

    refactor_continuation.take(path)
    try:
        run.diff_base = diff_git(path, "rev-parse", "HEAD").strip()
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass  # The diff endpoint reports its failure; the agent still runs.
    if not decide:
        remember(path, "user", text)

    def execute():
        try:
            run_turn(path, run, text, release, decide)
        finally:
            with _lock:
                _turns.pop(threading.current_thread(), None)

    thread = None
    try:
        thread = threading.Thread(target=execute, daemon=True)
        with _lock:
            if runtime.stopping.is_set():
                raise HTTPException(503, "서버가 종료 중이다. 다시 시작한 뒤 이어가라")
            _runs[str(path)] = run
            _turns[thread] = run
            thread.start()
    except BaseException as exc:
        with _lock:
            _turns.pop(thread, None)
        # No thread will end it: ended here, or it reads as running forever
        # and a screen that attaches never sees its stream close.
        run.put({"kind": "error", "text": f"턴을 시작하지 못했다 — {type(exc).__name__}: {exc}", "meta": {},
                 "session_id": chat.id, "parent_id": None})
        run.finish()
        raise
    # A turn the server started — the plan row's, a loop's — reaches a screen
    # that already shows this worktree only through here.
    feed.put({"kind": "turn", "path": str(path), "turn": run.turn, "session_id": chat.id})
    return run


@router.get("/api/work/events")
def events(path: str, turn: str, after: int = -1) -> StreamingResponse:
    """Reattach to a turn after `seq` `after`. 410 once another turn took its place."""

    run = attached(path)
    if run.turn != turn:
        raise HTTPException(410, "그 턴은 이제 없다")
    return streaming(tail(run, after))


class Rules(BaseModel):
    path: str
    session_id: str


class Stop(BaseModel):
    path: str
    turn: str


@router.post("/api/work/stop")
def stop(body: Stop) -> dict:
    """End a running turn. The CLI's session stays, so the next turn resumes it."""

    run = attached(body.path)
    if run.turn != body.turn:
        raise HTTPException(409, "지금 도는 턴이 아니다")
    if not run.done:
        run.halt.set()   # first: a process not started yet is stopped by this
        run.chat.stop(run.halt)   # only this turn's process, not the next one's
    return {"ok": True}


class Steer(Stop):
    text: str


@router.post("/api/work/steer")
def steer(body: Steer) -> dict:
    """Say something into a running turn. It lands in that turn's buffer as
    `said`, before anything the agent does with it, and in its record."""

    text = body.text.strip()
    if not text:
        raise HTTPException(400, "빈 지시")
    run = attached(body.path)
    if run.turn != body.turn:
        raise HTTPException(409, "지금 도는 턴이 아니다")
    with run.wake:
        if run.done or not run.chat.steer(text):
            raise HTTPException(409, "턴이 막 끝났다. 새 지시로 보내라")
        run.put({"kind": "said", "text": text, "meta": {}, "session_id": run.chat.id,
                 "parent_id": run.chat.parent_id})
    return {"ok": True}


class Queued(Steer):
    model: str = ""
    effort: str = ""
    fast: bool = False


@router.post("/api/work/queue")
def queue(body: Queued) -> dict:
    """The next instruction, written after the answer while the run still
    holds the worktree — the gate. The agent reads nothing more in this turn,
    so it is not steered: `dispatch` sends it once the worktree is let go. One
    waits per worktree; a second is refused, not put in its place — the
    first was already told it would go."""

    text = body.text.strip()
    if not text:
        raise HTTPException(400, "빈 지시")
    # A new instruction, as `say` takes one: only in the selected project's
    # worktrees. A turn left in another after a switch is read, stopped and
    # answered, never given more work.
    with _lock:
        repo = current_repo()
    ours(body.path, repo)
    run = attached(body.path)
    # Under the run's lock: `finish` takes it too, so an instruction taken
    # here is one `dispatch`, after the finish, finds.
    with run.wake:
        if run.turn != body.turn or run.done:
            raise HTTPException(409, "턴이 막 끝났다. 새 지시로 보내라")
        with _lock:
            if body.path in _queued:
                raise HTTPException(409, "기다리는 지시가 이미 있다. 그것을 취소하고 다시 보내라")
            _queued[body.path] = Order(path=body.path, text=text, model=body.model, effort=body.effort, fast=body.fast)
    feed.put({"kind": "work-state", "path": body.path})
    return {"ok": True}


@router.post("/api/work/unqueue")
def unqueue(body: Where) -> dict:
    """Taken back only while it still waits. Already sent, a cancel that said
    yes would hide a turn that is running."""

    with _lock:
        if _queued.pop(body.path, None) is None:
            raise HTTPException(409, "기다리는 지시가 없다 — 이미 보냈거나 멈춤으로 버려졌다")
    feed.put({"kind": "work-state", "path": body.path})
    return {"ok": True}


@router.post("/api/work/answer")
def answer(body: Answer) -> dict:
    """A person's answer to one approval, sent to the session that asked.

    A session that was reset or replaced in between has a new `session_id`,
    and an answer meant for the old one is refused rather than handed to it.
    The session is the whole check: one exists only for a path `ours` took,
    and asking `workspace` again made the answer depend on which project is
    selected now, and cost a `git status` per worktree per click.
    """

    chat = _sessions.get(body.path)
    if chat is None or chat.id != body.session_id:
        raise HTTPException(409, "그 승인을 물은 세션이 이제 없다")
    run = _runs.get(body.path)
    run = run if run is not None and run.chat is chat else None
    # Under the run's lock, so the answer is in the buffer before anything the
    # CLI does with it — the record is read from there.
    with run.wake if run else nullcontext():
        try:
            sent = chat.answer(body.id, body.allow, body.scope, body.answers)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not sent:
            raise HTTPException(409, "이미 답했거나, 물은 프로세스가 내려갔다")
        if run:
            # Another window, and a reattached one, draw the card as answered.
            run.put({"kind": "answered", "text": "", "meta": {"id": body.id, "allow": body.allow, "by": "person",
                                                              **({"answers": body.answers} if body.answers else {})},
                     "session_id": chat.id, "parent_id": chat.parent_id})
    if body.scope == "session":
        feed.put({"kind": "work-state", "path": body.path})
    return {"ok": True}


@router.post("/api/work/rules/clear")
def forget_rules(body: Rules) -> dict:
    """Drop every "allow for this session" of the session the screen shows."""

    chat = _sessions.get(body.path)
    if chat is None or chat.id != body.session_id:
        raise HTTPException(409, "그 세션이 이제 없다")
    chat.clear_rules()
    feed.put({"kind": "work-state", "path": body.path})
    return {"ok": True}
