"""work — the selected project's worktrees, and one write session in each.

The screen names a worktree by path, and a path from the screen is never
opened as it is: it has to be on `workspace`'s own list for the selected
project. Every write the agent wants arrives as an `approval` event and waits
for a person.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import ChatSession
from workspace import create, remove, worktrees

from .query import ROOT, current_repo, resumable, sse, streaming

LOGS = ROOT / "raw" / "work"
MAX_REPLAY = 200

router = APIRouter()

_sessions: dict[str, ChatSession] = {}    # worktree path -> its session
_lock = threading.Lock()
_busy: set[str] = set()


def close_all() -> None:
    with _lock:
        alive = list(_sessions.values())
        _sessions.clear()
    for chat in alive:
        chat.close()


def ours(path: str) -> Path:
    """`path` as `workspace` lists it for the selected project, or 404."""

    for row in worktrees(current_repo()):
        if str(row["path"]) == path:
            return row["path"]
    raise HTTPException(404, "선택한 프로젝트의 작업트리가 아니다")


def record(path: Path) -> Path:
    """One file per worktree, under its `<repo>-worktrees` folder's name. A
    task name is only unique within its repository, and the project selection
    can change while a turn is still being written."""

    return LOGS / path.parent.name / f"{path.name}.jsonl"


def remember(path: Path, role: str, text: str, **extra) -> None:
    file = record(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.time(), "role": role, "text": text, "path": str(path), **extra}
    with file.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def recall(path: Path) -> list[dict]:
    """This worktree's turns. Rows of a removed worktree that had the same
    name are left out by path."""

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
    return rows[-MAX_REPLAY:]


# -- Worktrees --------------------------------------------------------------

class Task(BaseModel):
    task: str


class Where(BaseModel):
    path: str


@router.get("/api/worktrees")
def listing() -> dict:
    repo = current_repo()
    try:
        rows = worktrees(repo)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    out = []
    for row in rows:
        chat = _sessions.get(str(row["path"]))
        out.append({**row, "path": str(row["path"]), "name": row["path"].name,
                    "live": bool(chat and chat.alive), "busy": str(row["path"]) in _busy})
    return {"repo": str(repo), "rows": out}


@router.post("/api/worktrees")
def make(body: Task) -> dict:
    try:
        path = create(current_repo(), body.task)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"path": str(path)}


@router.post("/api/worktrees/remove")
def clear(body: Where) -> dict:
    path = ours(body.path)
    with _lock:
        if body.path in _busy:
            raise HTTPException(409, "에이전트가 도는 동안은 지우지 않는다")
        chat = _sessions.pop(body.path, None)
    if chat:
        chat.close()
    try:
        text = remove(current_repo(), path)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    # The same task name makes the same path again, and the record is keyed by
    # it: a new `t1` came up with the old one's conversation, and `--resume`
    # carried the old CLI session into the new work. The record is set aside,
    # not deleted.
    file = record(path)
    if file.exists():
        file.rename(file.with_name(f"{path.name}.{time.time_ns()}.jsonl"))
    return {"text": text}


def busy() -> bool:
    """Is any agent turn running? The project stays put while one is: its
    worktrees, and the approvals waiting in them, belong to that project."""

    with _lock:
        return bool(_busy)


# -- The agent --------------------------------------------------------------

class Order(BaseModel):
    path: str
    text: str
    model: str = ""
    effort: str = ""


class Answer(BaseModel):
    path: str
    session_id: str      # this program's id for the session that asked
    id: str
    allow: bool


def session(path: Path, model: str, effort: str) -> ChatSession:
    """The worktree's write session, started if there is none.

    Another model of the same CLI reconnects with `--resume`. Another CLI is
    a new conversation — neither can resume the other's.
    """

    key = str(path)
    with _lock:
        chat = _sessions.get(key)
        if chat is not None and chat.is_codex != model.startswith("codex:"):
            _sessions.pop(key)
            chat.close()
            remember(path, "context", "CLI 변경")
            chat = None
        if chat is None:
            chat = ChatSession(path, model=model, effort=effort, write=True)
            chat.session_id = resumable(recall(path), chat.is_codex)
            _sessions[key] = chat
        else:
            chat.reconfigure(model, effort)
        return chat


@router.get("/api/work/log")
def log(path: str) -> dict:
    where = ours(path)
    chat = _sessions.get(path)
    return {"rows": [r for r in recall(where) if r.get("role") in ("user", "assistant")],
            "session_id": chat.id if chat else "", "busy": path in _busy}


@router.post("/api/work/reset")
def reset(body: Where) -> dict:
    path = ours(body.path)
    with _lock:
        if body.path in _busy:
            raise HTTPException(409, "에이전트가 도는 동안은 비우지 않는다")
        chat = _sessions.pop(body.path, None)
    remember(path, "context", "사용자가 문맥 지우기")
    if chat:
        chat.close()
    return {"ok": True}


@router.post("/api/work/say")
def say(body: Order) -> StreamingResponse:
    """One instruction to the worktree's agent, and its events.

    Every payload carries the session's own `session_id` beside the event, so
    the screen can drop a late one and an approval can name who asked.
    """

    path = ours(body.path)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "빈 지시")
    with _lock:
        if body.path in _busy:
            raise HTTPException(409, "이 작업트리의 에이전트가 아직 돌고 있다")

    def stream():
        # The worktree is held from inside the body, never before it. A body
        # that never starts — the client gone right after the headers — runs
        # no `finally`, and a hold taken outside one stayed for good: every
        # later instruction, reset and removal of that worktree got 409.
        with _lock:
            taken = body.path not in _busy
            _busy.add(body.path)
        if not taken:
            yield sse({"kind": "error", "text": "이 작업트리의 에이전트가 아직 돌고 있다", "meta": {},
                       "session_id": "", "parent_id": None})
            return
        final, failed, tools, meta, chat = "", "", [], {}, None
        # ponytail: the turn lives as long as this response. A reloaded window
        # cuts it; a per-session event buffer the stream tails would let it
        # reattach.
        try:
            chat = session(path, body.model, body.effort)
            remember(path, "user", text)
            for ev in chat.say(text):
                if ev.kind == "tool":
                    tools.append(ev.text)
                elif ev.kind == "done":
                    final, meta = ev.text, dict(ev.meta)
                    if meta.pop("error", False):
                        failed = final or "완료된 답이 없다"
                elif ev.kind == "error":
                    failed = ev.text
                yield sse({"kind": ev.kind, "text": ev.text, "meta": ev.meta,
                           "session_id": ev.session_id, "parent_id": ev.parent_id})
        except Exception as exc:  # a cut stream still owes the screen a reason
            failed = f"{type(exc).__name__}: {exc}"
            yield sse({"kind": "error", "text": failed, "meta": {},
                       "session_id": chat.id if chat else "", "parent_id": None})
        finally:
            try:
                if chat:
                    remember(path, "assistant", final, error=failed, tools=tools,
                             provider="codex" if chat.is_codex else "claude", **meta)
            finally:
                with _lock:
                    _busy.discard(body.path)

    return streaming(stream())


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
    if not chat.answer(body.id, body.allow):
        raise HTTPException(409, "이미 답했거나, 물은 프로세스가 내려갔다")
    return {"ok": True}
