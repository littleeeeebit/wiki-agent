"""work — the selected project's worktrees, and one write session in each.

The screen names a worktree by path, and a path from the screen is never
opened as it is: it has to be on `workspace`'s own list for the selected
project. Every write the agent wants arrives as an `approval` event and waits
for a person.

A turn runs in its own thread, not in the response that started it. Its
events pile up in a `Run`, and every response only tails that buffer, so a
reloaded window reattaches and a closed tab stops nothing.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import ChatSession
from workspace import remove, worktrees

# One lock with the wiki query's. A project switch reads every hold and
# changes the project under it, so a hold can never land in between.
from . import memory
from .query import ROOT, _lock, current_repo, hold, keep, project, resumable, sse, streaming

LOGS = ROOT / "raw" / "work"
MAX_REPLAY = 200
KEEPALIVE = 15.0   # seconds a tail waits before it checks the screen is still there
HALT_WAIT = 30.0   # seconds a forced removal waits for what it stopped to let go

router = APIRouter()

_sessions: dict[str, ChatSession] = {}    # worktree path -> its session
_busy: dict[str, object] = {}   # worktree path -> the hold of its running turn
_runs: dict[str, "Run"] = {}    # worktree path -> its last turn, kept until the next one starts
_queued: dict[str, "Order"] = {}   # worktree path -> the instruction waiting for it to be let go


def close_all() -> None:
    with _lock:
        alive = list(_sessions.values())
        _sessions.clear()
    for chat in alive:
        chat.close()


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


def record(path: Path) -> Path:
    """One file per worktree, under its `<repo>-worktrees` folder's name. A
    task name is only unique within its repository, and the project selection
    can change while a turn is still being written."""

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
        chat = _sessions.get(str(row["path"]))
        out.append({**row, "path": str(row["path"]), "name": row["path"].name,
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
        with _lock:
            chat = _sessions.pop(body.path, None)
            _runs.pop(body.path, None)
            _queued.pop(body.path, None)
        if chat:
            chat.close()
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


class Answer(BaseModel):
    path: str
    session_id: str      # this program's id for the session that asked
    id: str
    allow: bool
    scope: Literal["once", "session"] = "once"
    answers: list[str] | None = None   # a question's, one per question


def session(path: Path, model: str, effort: str) -> ChatSession:
    """The worktree's write session, started if there is none.

    Another model of the same CLI reconnects with `--resume`. Another CLI is
    a new conversation — neither can resume the other's. A worktree a spec
    owns gets the spec as its system prompt, every time a session is made.
    """

    from . import specs  # `specs` imports this module

    key = str(path)
    bypass = settings()["bypass"]
    with _lock:
        chat = _sessions.get(key)
        if chat is not None and chat.is_codex != model.startswith("codex:"):
            _sessions.pop(key)
            chat.close()
            remember(path, "context", "CLI 변경")
            chat = None
        if chat is None:
            chat = ChatSession(path, model=model, effort=effort, write=True, system=specs.system(path),
                               bypass=bypass)
            chat.session_id = resumable(recall(path), chat.is_codex)
            _sessions[key] = chat
        else:
            if chat.bypass != bypass:
                # Fixed at start-up: the next turn starts it again and resumes.
                chat.bypass = bypass
                chat.close()
            chat.reconfigure(model, effort)
        return chat


# -- Settings ---------------------------------------------------------------

WORK_DEFAULTS = {"bypass": True}


def settings() -> dict:
    from . import loop  # `loop` imports this module

    return loop.settings(WORK_DEFAULTS)


class WorkSettings(BaseModel):
    bypass: bool


@router.get("/api/work/settings")
def get_settings() -> dict:
    return settings()


@router.post("/api/work/settings")
def set_settings(body: WorkSettings) -> dict:
    from . import loop

    loop.store(bypass=body.bypass)
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
    cfg = {"model": chat.model or "", "effort": chat.effort or ""} if chat and body.keep == "memory" else {}
    return {"ok": True, **keep(repo, f"work-{path.name}", rows, cfg, body)}


class Run:
    """One worktree's turn, as a buffer of its events in order.

    Each event is the screen's payload plus `seq`, its place here from 0,
    `turn`, so a screen that reattaches after `seq` k gets k+1 onwards and
    can tell this turn's events from the next one's, and `ts`, when it came:
    a screen that reattaches still knows how long the last step has run.
    """

    def __init__(self, chat: ChatSession) -> None:
        self.turn = uuid.uuid4().hex
        self.chat = chat
        self.session_id = chat.id
        self.events: list[dict] = []
        self.done = False
        self.halt = threading.Event()   # set by a stop; the session reads it too
        self.wake = threading.Condition()

    def put(self, payload: dict) -> None:
        with self.wake:
            self.events.append({**payload, "seq": len(self.events), "turn": self.turn, "ts": time.time()})
            self.wake.notify_all()

    def finish(self) -> None:
        with self.wake:
            self.done = True
            self.wake.notify_all()


class Feed:
    """What the server changes on its own — a spec's state, a turn it
    started — as one buffer every screen tails. Shaped like a `Run`, so
    `tail` serves it; it never finishes.

    ponytail: every event is kept for the server's life, a few hundred a day.
    Drop the oldest and let a screen that asks from before them reread the
    lists, if a server ever runs for months.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self.wake = threading.Condition()

    def put(self, payload: dict) -> None:
        with self.wake:
            self.events.append({**payload, "seq": len(self.events)})
            self.wake.notify_all()


feed = Feed()


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
        if ev["kind"] in ("tool", "said", "hook"):
            out.append({"kind": ev["kind"], "text": ev["text"]})
        elif ev["kind"] == "approval":
            # `none`: the turn ended before anyone answered.
            step = asked[str(meta.get("id"))] = {
                "kind": "approval", "tool": meta.get("tool", ""), "text": ev["text"],
                "answer": meta.get("answer", "none"), "by": meta.get("by", "person")}
            out.append(step)
        elif ev["kind"] == "answered" and str(meta.get("id")) in asked:
            asked[str(meta["id"])].update(answer="allow" if meta["allow"] else "deny", by=meta["by"],
                                          **({"answers": meta["answers"]} if meta.get("answers") else {}))
    return out


def shown(row: dict) -> dict:
    """A record row as the screen reads it. Rows from before `steps` had only
    `tools`, the lines of what ran."""

    if "tools" in row and "steps" not in row:
        row = {**row, "steps": [{"kind": "tool", "text": t} for t in row["tools"]]}
        del row["tools"]
    return row


def run_turn(path: Path, run: Run, text: str, release) -> None:
    """One turn, to its end, whoever is watching. The hold and the record are
    let go here, so a turn nobody watched is still on record."""

    chat, final, failed, meta, then = run.chat, "", "", {}, None
    try:
        for ev in chat.say(text, run.halt):
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
        if not failed:
            # Still holding the worktree: the gate runs where nothing else
            # writes, and its lines are in this turn's record.
            from . import specs

            then = specs.check(path, run, final)
    except Exception as exc:  # a turn that broke still owes the screen a reason
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
                answered = {} if at is None else {"answered": len(steps(run.events[:at]))}
            remember(path, "assistant", final, error=failed, steps=made,
                     provider="codex" if chat.is_codex else "claude", **answered, **meta)
        finally:
            # Released before the end is told, so a screen that sees the end
            # can send the next instruction at once.
            release()
            run.finish()
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
        begin(path, session(path, order.model, order.effort), order.text, release)
    except Exception as exc:
        # Nobody's request is waiting on this thread: the failure and the
        # instruction itself go on record, and screens read it again.
        release()
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
        run = begin(path, session(path, body.model, body.effort), text, release)
    except BaseException:
        release()
        raise
    return streaming(tail(run, -1))


def begin(path: Path, chat: ChatSession, text: str, release, run: Run | None = None) -> Run:
    """Start one turn of `chat` on its own thread, which owns `release` from
    here. The caller holds the worktree already. `run` is one the caller made
    first, so a stop could reach it before the thread starts."""

    run = run or Run(chat)
    remember(path, "user", text)
    with _lock:
        _runs[str(path)] = run
    try:
        threading.Thread(target=run_turn, args=(path, run, text, release), daemon=True).start()
    except BaseException as exc:
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
            _queued[body.path] = Order(path=body.path, text=text, model=body.model, effort=body.effort)
    return {"ok": True}


@router.post("/api/work/unqueue")
def unqueue(body: Where) -> dict:
    """Taken back only while it still waits. Already sent, a cancel that said
    yes would hide a turn that is running."""

    with _lock:
        if _queued.pop(body.path, None) is None:
            raise HTTPException(409, "기다리는 지시가 없다 — 이미 보냈거나 멈춤으로 버려졌다")
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
    return {"ok": True}


@router.post("/api/work/rules/clear")
def forget_rules(body: Rules) -> dict:
    """Drop every "allow for this session" of the session the screen shows."""

    chat = _sessions.get(body.path)
    if chat is None or chat.id != body.session_id:
        raise HTTPException(409, "그 세션이 이제 없다")
    chat.clear_rules()
    return {"ok": True}
