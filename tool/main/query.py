"""query — ask the wiki, under one of three focuses, and read the grounds.

Finding the answer and saying it simply are separate calls, so they are never
the same turn. Everything that reaches the screen is read by a person and
stays Korean.
"""

from __future__ import annotations

import contextvars
import datetime as dt
from contextvars import ContextVar
from urllib.parse import quote
import json
import re
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import ChatSession, explain
from search import refresh
from session_state import run
from wiki import label, match_pages, pages
import decision
import translate

from . import channels, memory
from . import knowledge
from .knowledge import Run, grounded, prepare

ROOT = channels.WIKI
LOGS = ROOT / "raw" / "chat"
CORRECTIONS = ROOT / "raw" / "corrections.jsonl"

MAX_REPLAY = 200  # How many past turns the screen restores

router = APIRouter()

_sessions: dict[tuple[str, str], ChatSession] = {}
# Reentrant: a stream's release can run from garbage collection while this
# very thread is inside `with _lock:`, and a plain lock waited on itself.
_lock = threading.RLock()
_busy: dict[str, object] = {}   # focus -> the hold of the answer running in it


def close_all() -> None:
    with _lock:
        alive = list(_sessions.values())
        _sessions.clear()
    for chat in alive:
        chat.close()


# Every focus shares the project selection, and so does the work pane. The
# conversation and the model settings are per project and per focus.
_config: dict[tuple[str, str], dict] = {}
_project: str | None = None


# The project the screen that sent this request is showing (`X-Project`), set
# by `app.only_this_screen` for every route but the switch itself.
claimed: ContextVar[str | None] = ContextVar("claimed", default=None)


def project() -> str:
    """The selected project — and the check that the asking screen shows it.

    The selection is one for the whole server, and another window can move
    it. A screen that had not noticed yet sent a question into the other
    project's conversation. Every project-scoped path reads the project here,
    and those that hold (`say`, `make`, `reset`, `remove`) read it under or
    after their hold, so the check and the work cannot be split by a switch.
    """
    global _project
    if _project is None:
        path = LOGS / "project.json"
        name = json.loads(path.read_text(encoding="utf-8")) if path.exists() else channels.WIKI.name
        if not isinstance(name, str):
            raise HTTPException(409, "저장된 프로젝트 설정을 읽을 수 없습니다")
        _project = name
    screen = claimed.get()
    if screen is not None and screen != _project:
        raise HTTPException(409, f"다른 창이 프로젝트를 {_project} 로 바꿨다. 화면을 맞춘다",
                            headers={"X-Project-Moved": quote(_project)})
    return _project


def current_repo() -> Path:
    repo = channels.repo_for(project())
    if repo is None:
        raise HTTPException(409, "선택한 프로젝트를 찾을 수 없습니다")
    return repo


def session_key(cid: str) -> tuple[str, str]:
    return (str(current_repo()), cid)


def known(cid: str) -> None:
    if cid not in channels.BY_ID:
        raise HTTPException(404, "그런 초점이 없다")


def config(cid: str, name: str | None = None) -> dict:
    """The model settings of a focus in project `name`, the selected one by default."""

    name = name or project()
    key = (name, cid)
    if key not in _config:
        channel = channels.get(cid)
        model = channels.LOCAL.get("model", channel.model)
        effort = channel.effort
        if model.startswith("codex:"):
            try:
                selected = next(m for m in channels.codex_models() if m["id"] == model)
                if effort not in {e["id"] for e in selected["efforts"]}:
                    effort = selected["default_effort"]
            except Exception as exc:
                raise HTTPException(503, "기본 Codex 모델을 확인할 수 없습니다. 설치 명령을 다시 실행하세요.") from exc
        _config[key] = {"repo": name, "model": model, "effort": effort}
    return _config[key]


# How a focus finds evidence before it reads, on both hosts.
#
# No first pass on a cheaper model. In the public copy a Haiku `scout`
# subagent was built and measured on 2026-09-25 over ten real questions: where
# it was called, input tokens rose 2.4x — it loads its own context, and the
# main model re-reads what it cites anyway. Searching itself, the main model's
# median input fell 20% and correct citations rose from 29.5 to 39.5.
SEARCH_NOTE = """## Search command
In Bash: {bash} '<query>' [--k 8]
In PowerShell: & {pwsh} '<query>' [--k 8]
Keep the query in single quotes, so `$`, backticks and `$(...)` stay text; a
quote inside it is written '\\'' in Bash and '' in PowerShell.
It returns matching sections with `path:line` and the pages linked to each.
For the configured Jev retrieval controller, run the same command with
`tool/jev_search.py` in place of `tool/search` and add
--state '<brief current state>'. Its JSON dossier includes evidence, routing,
the requirements it covered and its status. Only `ready` means covered; a
partial, unavailable, cancelled or exhausted status requires further
verification. Jev judgments never override hook rules or authorize actions.
The hub's pages are English and many repository documents are Korean, so
search with terms in both languages."""


def search_note(repo: Path) -> str:
    """The note with this server's Python and this repository in the command.

    Forward slashes: Claude's `Bash` is Git Bash on Windows. Both shells'
    forms are given, not the one for the focus's host: a quoted first word is
    a string, not a command, in Codex's PowerShell and needs `&`, which Bash
    reads as "run in the background" — and `reconfigure` can move a focus
    between Claude and Codex while its system prompt stays (review round 1).

    Every path in single quotes, which neither shell expands: a repository
    at `C:/Repos/R&D` or `C:/Team $Ops` reaches Python as it is (round 2).
    A quote inside a path is escaped each shell's own way. The query's
    placeholder is single-quoted too, and the note says how: a model that
    copies `"<query>"` hands `$HOME` or `$(...)` to the shell (round 3).
    """

    paths = [Path(sys.executable), channels.WIKI / "tool/search", repo.resolve()]

    def command(escape) -> str:
        python, script, project = (f"'{escape(p.as_posix())}'" for p in paths)
        return f"{python} {script} --project {project}"

    return SEARCH_NOTE.format(bash=command(lambda s: s.replace("'", "'\\''")),
                              pwsh=command(lambda s: s.replace("'", "''")))


def session(cid: str) -> ChatSession:
    """A focus's live conversation, started if there is none.

    The focus's introduction goes in as a system prompt. Sent as the first
    turn it took two minutes — the model reads the introduction and starts
    going through files — against six seconds for the actual question.

    A dead process is never swapped for a new object. Swapping it throws away
    the `session_id` that object was holding, leaving `--resume` nothing to
    attach to, and the conversation then disappeared quietly on every model or
    effort change. Reviving it is `ensure`'s job.
    """

    with _lock:
        key = session_key(cid)
        chat = _sessions.get(key)
        if chat is None:
            cfg = config(cid)
            repo = current_repo()
            system = "\n\n".join((channels.ANSWER_PROMPT, channels.get(cid).preamble, search_note(repo)))
            chat = ChatSession(repo, system=system, model=cfg["model"], effort=cfg["effort"])
            # Restarting the server is not clearing the conversation either.
            # Only the same CLI, after an explicit reset, is resumed.
            chat.session_id = resumable(recall(cid, include_context=True), chat.is_codex)
            _sessions[key] = chat
        return chat


def resumable(rows: list[dict], codex: bool) -> str | None:
    """The CLI session id the last conversation left, if it was this CLI's."""

    for row in reversed(rows):
        if row.get("role") == "context":
            return None
        if row.get("session_id"):
            provider = row.get("provider") or ("claude" if str(row.get("model", "")).startswith("claude-") else "codex")
            return row["session_id"] if provider == ("codex" if codex else "claude") else None
    return None


# -- Records ----------------------------------------------------------------
# No database. On a localhost one person uses, there is nothing a line of
# jsonl cannot do.

def remember(cid: str, role: str, text: str, error: str = "", repo: Path | None = None, **extra) -> None:
    """`repo` for a row written outside a request — a spec's result, from a
    turn's thread — which names its repository rather than the selected one."""

    row = {"ts": time.time(), "role": role, "text": text, "repo": str(repo or current_repo()), **extra}
    if error:
        row["error"] = error
    memory.append(LOGS / f"{cid}.jsonl", row)


def recall(cid: str, legacy: bool = False, include_context: bool = False) -> list[dict]:
    """This project's rows since the last clear. A cleared conversation came
    back on the next switch of focus when this read the whole file."""

    path = LOGS / f"{cid}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (not row.get("repo")) if legacy else row.get("repo") == str(current_repo()):
            rows.append(row)
    rows = memory.since_clear(rows)
    if not include_context:
        rows = [r for r in rows if r.get("role") in memory.SAID]
    return rows[-MAX_REPLAY:]


def unseen(cid: str) -> list[dict]:
    """The result rows the CLI has not been told: those after the last thing
    said to it. Told once, in front of the next thing said."""

    rows = recall(cid, include_context=True)
    last = max((i for i, r in enumerate(rows) if r.get("role") == "user"), default=-1)
    return [r for r in rows[last + 1:] if r.get("role") == "result"]


def sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def streaming(events) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Held:
    """One hold. `kind` is what a project switch reads: a `turn` — an agent
    turn or a loop — goes on in the repository it took, and a switch does not
    wait for it; a `short` request reads the selected project partway through,
    and a switch waits until it ends."""

    __slots__ = ("kind",)

    def __init__(self, kind: str) -> None:
        self.kind = kind


def hold(busy: dict, lock: threading.Lock, key: str, refused: str, kind: str = "short"):
    """Take `key` for a stream from the moment the request is accepted, or 409.

    Returns the release; call it from the stream's `finally` and pass it to
    `held`. Two ways out, and both are covered. A body that runs releases in
    its `finally`. A body that never starts — the client gone before the first
    byte — runs no `finally`, so the release also rides on the body being
    collected. Taken only inside the body, the key was free between accepting
    and starting, and a project switch or a removal walked through that gap.
    The token makes a second release, or a late one after a new hold, a no-op.
    It rides on the release as `release.held`, so a hold can change its kind.
    """

    with lock:
        if key in busy:
            raise HTTPException(409, refused)
        token = busy[key] = Held(kind)

    def release() -> None:
        with lock:
            if busy.get(key) is token:
                del busy[key]

    release.held = token
    return release


def held(events, release) -> StreamingResponse:
    weakref.finalize(events, release)
    return streaming(events)


# -- API ------------------------------------------------------------------

# What the screen reads while a draft is checked (stage 7 of `docs/plans/jev/`):
# a line of what ran, like a tool's. The draft itself is never sent.
PROGRESS = {"draft": "검증 · 근거에 기대어 초안을 쓴다",
            "verify": "검증 · 주장마다 인용과 근거를 대조한다",
            "retrieve": "검증 · 저장소 사실이 필요해 다시 찾는다",
            "repair": "검증 · 거절된 주장을 한 번 고쳐 쓴다"}


class Failed(Exception):
    """The host's turn ended without an answer: an error event, or `done` flagged as one."""

    def __init__(self, text: str, meta: dict | None = None):
        super().__init__(text)
        self.meta = meta or {}


def line(cid: str, ev) -> dict | None:
    """What the screen is told of an event that is not the answer, or `None`."""

    # A read session answers every approval itself, with a refusal.
    # The query screen shows that as what it is: a line of what ran.
    if ev.kind == "approval":
        return {"kind": "tool", "text": f"거절 · {ev.text}"}
    if ev.kind == "hook":   # a line of what ran, as the work pane has it
        return {"kind": "tool", "text": f"훅 · {ev.text}"}
    if ev.kind == "context":   # the CLI's conversation could not be resumed
        remember(cid, "context", ev.text)
        return {"kind": "tool", "text": ev.text}
    return None


def drafting(cid: str, lead: str, spent: dict, halt: threading.Event | None = None):
    """`generate` for `knowledge.grounded`: one turn of the focus's session,
    its tool lines passed on and its text held back — the draft reaches no
    screen. The first turn carries `lead`, what the person said. Returns the
    finished text; `Failed` when there is none. What the turns cost adds up
    in `spent`. `halt` is the run's stop, which ends the turn."""

    said = [lead]

    def generate(message: str):
        if said:
            message = f"{said.pop()}\n\n{message}"
        for ev in session(cid).say(message, halt):
            if (shown := line(cid, ev)) is not None:
                yield shown
            elif ev.kind == "tool":
                yield {"kind": "tool", "text": ev.text, **ev.meta}
            elif ev.kind == "error":
                raise Failed(ev.text)
            elif ev.kind == "done":
                if ev.meta.get("error"):
                    raise Failed(ev.text or "완료된 답변이 없습니다", ev.meta)
                for name in ("ms", "cost_usd"):
                    if isinstance(ev.meta.get(name), (int, float)):
                        ev.meta[name] += spent.get(name, 0)
                spent.update(ev.meta)
                return ev.text
        raise Failed("답변 생성이 완료되지 않았습니다")

    return generate


class Say(BaseModel):
    text: str = ""
    propose: bool = False    # `[후보 내기]`: the server gathers the materials


class Config(BaseModel):
    repo: str
    model: str = ""
    effort: str = ""


@router.get("/api/options")
def options() -> dict:
    """Everything the screen can choose between."""

    codex, error = [], ""
    try:
        codex = channels.codex_models()
    except Exception as exc:
        error = f"Codex 모델 목록을 불러오지 못했습니다: {exc}"
    return {"projects": channels.projects(),
            "models": channels.MODELS + codex,
            "efforts": channels.EFFORTS, "codex_error": error}


@router.get("/api/jev")
def jev_status() -> dict:
    """The Jev settings as this process reads them now: mode, model, whether a
    key exists and where from — and which of this server's choices Jev
    controls, by entry point and operation (stage 8). Sends nothing."""

    from . import decisions  # `decisions` imports this module

    return {**decision.config().status(), "agent_decisions": decisions.coverage()}


@router.post("/api/jev/probe")
def jev_probe() -> dict:
    """One live request with a synthetic question — the same probe as
    `python tool/jev_probe.py --live`, from inside the server process."""

    return decision.probe(decision.config())


class JevSettings(BaseModel):
    mode: str | None = None          # none: `.env` decides
    disabled_sources: list[str] = []
    limits: dict = {}


@router.post("/api/jev/settings")
def jev_settings(body: JevSettings) -> dict:
    """The app's Jev settings (stage 9): mode, the source families a
    question may search, and a question's limits. A new run reads them; a
    run in flight keeps what it started with. The key is not among them."""

    try:
        decision.save(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, f"설정을 저장하지 않았다 — {exc}") from exc
    return jev_status()


# -- Runs (stage 9 of `docs/plans/jev/`) --------------------------------------
#
# A question's run belongs to the repository it ran in: every route reads the
# selected project and answers only for its runs, so knowing a run id is not
# enough to read another project's evidence.

@router.get("/api/knowledge/status")
def knowledge_status() -> dict:
    return knowledge.status(current_repo())


@router.get("/api/knowledge/runs/{run_id}")
def knowledge_run(run_id: str) -> dict:
    """A finished run's summary, each file it cited checked against the file
    now; a live one's stage and last `seq`."""

    repo = current_repo()
    run = knowledge.live(run_id, repo)
    if run is not None:
        return knowledge.standing(run.summary) if run.done else run.brief()
    summary, events = knowledge.stored(run_id, repo)
    if summary is not None:
        return knowledge.standing(summary)
    if events:
        return {"schema_version": knowledge.RUN_SUMMARY, "run_id": run_id, "done": True, "outcome": "interrupted",
                "reason": "server_stopped", "events": len(events)}
    raise HTTPException(404, "이 프로젝트에 그런 실행이 없다")


@router.get("/api/knowledge/runs/{run_id}/events")
def knowledge_events(run_id: str, after: int = -1) -> StreamingResponse:
    """The run's events after `seq` `after`: tailed while it runs, read from
    its trace once it is over. A run the server went down with ends in an
    `interrupted` error."""

    from .work import tail  # `work` imports this module

    repo = current_repo()
    run = knowledge.live(run_id, repo)
    if run is not None:
        return streaming(tail(run, after))
    summary, events = knowledge.stored(run_id, repo)
    if summary is None and not events:
        raise HTTPException(404, "이 프로젝트에 그런 실행이 없다")
    rest = [e for e in events if e.get("seq", -1) > after]
    if summary is None:
        rest.append({"kind": "error", "code": "interrupted", "run_id": run_id,
                     "text": "서버가 내려가며 이 실행이 끊겼다. 다시 물어라"})
    return streaming(iter([sse(e) for e in rest]))


@router.post("/api/knowledge/runs/{run_id}/cancel")
def knowledge_cancel(run_id: str) -> dict:
    """Stop a run through its owner: the run's stop, which retrieval,
    verification and the host's turn all read, and the turn's process."""

    run = knowledge.live(run_id, current_repo())
    if run is None:
        raise HTTPException(404, "이 프로젝트에 도는 그런 실행이 없다")
    # First: a turn not started yet is stopped by this. After the answer is
    # published a stop only cuts the plain explanation (`published`).
    published = not run.stop()
    if not run.done:
        chat = _sessions.get(session_key(run.focus))
        if chat is not None:
            chat.stop(run.cancel)
    return {"ok": True, "done": run.done, "published": published}


def shadow(cid: str, query: str, repo: Path, context: str, cfg: decision.Config) -> None:
    """Shadow mode: Jev decides beside the turn and its dossier is recorded,
    while the turn runs on baseline behaviour and waits for none of it."""

    def record():
        try:
            dossier = prepare(query, repo, context, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — a shadow never touches the turn
            dossier = {"status": "fallback", "trace": [{"fallback": type(exc).__name__}]}
        remember(cid, "retrieval", "Jev shadow decision", repo=repo, dossier=knowledge.redact(dossier, cfg.key),
                 shadow=True)

    threading.Thread(target=record, daemon=True).start()


@router.get("/api/channels")
def focuses() -> list[dict]:
    return [
        {"id": c.id, "label": c.label, "blurb": c.blurb,
         "live": session_key(c.id) in _sessions and _sessions[session_key(c.id)].alive,
         "model_name": _sessions[session_key(c.id)].model_name if session_key(c.id) in _sessions else "",
         # Used to turn a SHA in an answer into a commit link. Empty with no
         # remote.
         "remote": repo_url(current_repo()),
         **config(c.id)}
        for c in channels.CHANNELS
    ]


@router.post("/api/config/{cid}")
def configure(cid: str, body: Config) -> dict:
    """The project applies to every focus and to the work pane; the model and
    effort apply to this focus of the selected project."""
    global _project

    known(cid)
    if channels.repo_for(body.repo) is None:
        raise HTTPException(400, f"그런 저장소가 없다: {body.repo}")
    models = channels.MODELS
    if body.model.startswith("codex:"):
        try:
            models = channels.codex_models()
        except Exception as exc:
            raise HTTPException(503, f"Codex 모델 목록 확인 실패: {exc}") from exc
    selected = next((m for m in models if m["id"] == body.model), None)
    if selected is None and not body.model.startswith("codex") and channels.CLAUDE_MODEL.fullmatch(body.model):
        # Typed in by hand. The list only holds aliases, which already follow
        # the latest model; a name that is not on it goes to `--model` as it
        # is and the CLI says whether it knows it.
        selected = {"id": body.model}
    if selected is None:
        raise HTTPException(400, "지원하지 않는 모델 선택")
    efforts = {e["id"] for e in selected.get("efforts", channels.EFFORTS)}
    if body.effort not in efforts:
        raise HTTPException(400, "이 모델이 지원하지 않는 effort")

    from . import work  # `work` imports this module; the project is shared

    with _lock:
        switched = body.repo != project()
        if cid in _busy or (switched and _busy):
            raise HTTPException(409, "답변 생성이 끝난 뒤 설정을 바꿔 주세요")
        # A running turn or loop is not waited for: it took its repository
        # when it was held, and its events, stop and approvals follow the
        # session, not the selection. A short request — making, removing,
        # resetting — reads the project partway through, and is waited for.
        if switched and work.busy():
            raise HTTPException(409, "작업트리를 만들거나 지우는 중에는 프로젝트를 바꾸지 않는다")
        if switched:
            # Changed only while no turn is being recorded, and only once
            # everything that can fail has been done against the new project by
            # name. `config` fails when Codex cannot list its models; the
            # selection used to move first, and a listing that reads without
            # the lock saw a project the switch was about to refuse. A failed
            # disk write keeps the old selection too.
            cfg = config(cid, body.repo)
            LOGS.mkdir(parents=True, exist_ok=True)
            path = LOGS / "project.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(body.repo, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(path)
            _project = body.repo
            # This request moved the project itself; the rest of it works in
            # the new one. Its screen follows from the answer.
            claimed.set(body.repo)
        else:
            cfg = config(cid)
        moved = not switched and body.model.startswith("codex:") != cfg["model"].startswith("codex:")
        if not switched:
            cfg.update(model=body.model, effort=body.effort or selected.get("default_effort", ""))
        key = session_key(cid)
        if moved:
            remember(cid, "context", "CLI 변경")
        chat = _sessions.pop(key, None) if moved else _sessions.get(key)
        inactive = [s for (repo, _), s in _sessions.items() if repo != key[0]] if switched else []
    for old in inactive:
        old.close()  # the session_id is kept, so coming back resumes it
    if chat is not None:
        if moved:
            chat.close()
        else:
            chat.reconfigure(cfg["model"], cfg["effort"])
    return {"kept": not moved, "switched": switched, **cfg}


@router.get("/api/log/{cid}")
def log(cid: str, legacy: bool = False) -> list[dict]:
    known(cid)
    return recall(cid, legacy)


class Clear(BaseModel):
    # `memory`: the pair in the repository's `.wiki/memory/`. `delete`: the
    # rows go from the record too.
    keep: Literal["memory", "delete"]


@router.post("/api/reset/{cid}")
def reset(cid: str, body: Clear) -> dict:
    known(cid)
    with _lock:
        if cid in _busy:
            raise HTTPException(409, "답변 생성이 끝난 뒤 대화를 초기화해 주세요")
        repo, cfg = current_repo(), dict(config(cid))
        rows = memory.clear(LOGS / f"{cid}.jsonl", lambda r: r.get("repo") == str(repo),
                            {"ts": time.time(), "role": "context", "text": memory.CLEARED, "repo": str(repo)},
                            body.keep == "delete")
        chat = _sessions.pop(session_key(cid), None)
    if chat:
        chat.close()
    return {"ok": True, **keep(repo, cid, rows, cfg, body)}


def keep(repo: Path, focus: str, rows: list[dict], cfg: dict, body: Clear) -> dict:
    """The memory pair, outside every lock: it is a model turn. A failure is
    said, not raised — the conversation is cleared by then either way."""

    if body.keep != "memory":
        return {}
    try:
        kept = memory.keep(repo, focus, rows, cfg.get("model", ""), cfg.get("effort", ""))
    except RuntimeError as exc:
        return {"fault": str(exc)}
    if kept:
        # Searchable now, not at the next question (stage 3 of `docs/plans/jev/`).
        refresh(repo)
    return kept


@router.post("/api/say/{cid}")
def say(cid: str, body: Say) -> StreamingResponse:
    """One question, run to its end on its own thread as a `knowledge.Run`,
    whoever is watching; the response is a tail of the run's events. A screen
    that reloads reattaches after the last `seq` it saw
    (`/api/knowledge/runs/{id}/events`), and a stop reaches the run through
    `/api/knowledge/runs/{id}/cancel`. The settings are read once, here: a
    change reaches the next question, not this one."""

    from .work import tail  # `work` imports this module

    known(cid)
    text = body.text.strip()
    if body.propose and cid != "next":
        raise HTTPException(400, "후보는 다음 작업 초점에서만 낸다")
    if not text and not body.propose:
        raise HTTPException(400, "빈 발화")
    # The settings first: `config` can fail (503 when Codex cannot list its
    # models), and after the hold nothing may fail before the thread owns the
    # release — a focus stayed busy for good that way.
    with _lock:
        cfg = dict(config(cid))
        release = hold(_busy, _lock, cid, "이 초점의 답변을 생성하고 있습니다")
    try:
        run = Run(current_repo(), cid, text or "(후보 요청)", decision.config())
        # The thread reads the project this request was checked against.
        threading.Thread(target=contextvars.copy_context().run, args=(ask, cid, body, text, cfg, run, release),
                         daemon=True).start()
    except BaseException:
        release()
        raise
    return streaming(tail(run, -1))


def ask(cid: str, body: Say, text: str, cfg: dict, run: Run, release) -> None:
    """The question's turn, from the utterance to the record. Every payload
    goes to `run`; the record and the run's summary are written, and the
    focus let go, however it ends."""

    put = run.put

    def keep(*args, **extra) -> None:
        """A row of this run's record — `/api/log` serves it — with the key
        replaced, as in the run's events."""

        remember(*run.redact(args), **run.redact(extra))

    reply: list[str] = []
    failed = ""
    code = ""
    simple = ""
    simple_error = ""
    finished = False
    metadata = {}
    simple_meta = {}
    shown: list[dict] = []
    verification = None
    out = None
    try:
        from . import specs  # `specs` imports this module

        # The row keeps what the CLI was sent, whole, so a resumed CLI
        # saw the same; `said` is what the screen shows instead.
        sent, flags = text, {}
        if body.propose:
            sent = specs.materials(run.repo) + (f"\n\n{text}" if text else "")
            flags = {"said": "(후보 요청)" + (f" {text}" if text else ""), "propose": True}
        results = unseen(cid)
        if results:
            sent = "Since your last turn:\n" + "\n".join(f"- {r['text']}" for r in results) + "\n\n" + sent
            flags.setdefault("said", text)
        # The run's repository, not a read of the selection: this thread writes its record whatever happens after.
        keep(cid, "user", sent, repo=run.repo, run_id=run.id, **flags)
        jev = run.cfg
        dossier = None
        if jev.mode != "off":
            # Record the utterance before any external request. The dossier
            # is context for this turn, not a second user utterance.
            prior = recall(cid)[-7:-1]
            context = "\n".join(f"{r['role']}: {r.get('said', r['text'])}" for r in prior)
            if jev.mode == "active":
                dossier = prepare(text or sent, run.repo, context, cfg=jev, run=run)
                keep(cid, "retrieval", "Jev retrieval decision", repo=run.repo, dossier=dossier, run_id=run.id)
            else:
                shadow(cid, text or sent, run.repo, context, jev)
        # A display-time match against the relevant rules. Not a check
        # that the host actually injected anything.
        hits = hits_for(text) if text else []
        if hits:
            put({"kind": "hits", "text": "", "pages": hits})
        if dossier is None:
            run.step("answer", "baseline")
        events = iter(()) if dossier is not None else session(cid).say(sent, run.cancel)
        if dossier is not None:
            # Active: the answer is drafted, checked, and only then published
            # (stage 7 of `docs/plans/jev/`). Nothing of the draft is sent on.
            spent: dict = {}
            # What a direct run's text may restate: answers that were verified, English as code wrote them.
            # An unverified one restated would come out verified.
            verified = "\n".join(r["text"] for r in prior if r["role"] == "assistant"
                                 and (r.get("verification") or {}).get("verified")
                                 and not r["verification"].get("degraded"))
            flow = grounded(text or sent, run.repo, context, dossier,
                            drafting(cid, sent, spent, run.cancel), jev, said=verified, run=run)
            try:
                while True:
                    try:
                        ev = next(flow)
                    except StopIteration as stop:
                        out = stop.value
                        break
                    put(ev if "kind" in ev else
                        {"kind": "tool", "text": PROGRESS[ev["progress"]], "progress": ev["progress"]})
            except Failed as exc:
                if run.cancel.is_set():
                    raise Stopped() from exc   # the stop killed the draft's turn
                failed, code = str(exc), "host_failed"
                put({"kind": "error", "text": failed, "code": code, **exc.meta})
            else:
                # The drafts are the run's own record, never the conversation's.
                keep(cid, "draft", "Jev answer draft", repo=run.repo, record=out["record"], run_id=run.id)
                if not run.seal():
                    # Stopped while it was checked: nothing of it is published.
                    out = None
                    raise Stopped()
                verification = out["verified"]
                reply = [out["text"]]
                finished = True
                metadata = {k: v for k, v in spent.items() if k != "error"}
                if cid == "next":
                    found = specs.blocks(out["rest"])[1]
                    cited = {c["evidence_id"]: c for c in verification["citations"]}
                    accepted = {e: cited[c] for e, c in out["evidence_ids"].items() if c in cited}
                    source = {"focus": cid, "turn": time.time(), "session": metadata.get("session_id", "")}
                    shown = specs.answered(run.repo, found, source, accepted)
                put({"kind": "done", "text": reply[0], **metadata, "verification": verification})
                if shown:
                    put({"kind": "blocks", "text": "", "blocks": shown})
        for ev in events:
            if run.cancel.is_set():
                break   # what the stopped turn says after the stop is not the answer
            if (shown_line := line(cid, ev)) is not None:
                put(shown_line)
                continue
            if ev.kind == "delta":
                reply.append(ev.text)
            # A quota or API error arrives on `done` with `error=True`. It
            # is an error, not an answer. Held together with the partial
            # answer instead of separately, the reason for the cut-off is
            # gone when the conversation is restored — a retro that hit a
            # session limit survived as the single line `세겠습니다`.
            if ev.kind == "done" and ev.meta.get("error"):
                failed, code = ev.text or "완료된 답변이 없습니다", "host_failed"
                put({"kind": "error", "text": failed, "code": code, **ev.meta})
                break
            if ev.kind == "error":
                failed, code = ev.text, "host_failed"
            if ev.kind == "done" and not run.seal():
                break
            if ev.kind == "done":
                reply = [ev.text or "".join(reply)]
                finished = bool(reply[0].strip())
                metadata = ev.meta
                metadata.pop("error", None)
                if cid == "next":
                    # The blocks leave the answer, so the overlay never
                    # translates JSON; they go to the screen apart.
                    reply[0], found = specs.blocks(reply[0])
                    source = {"focus": cid, "turn": time.time(), "session": metadata.get("session_id", "")}
                    shown = specs.answered(run.repo, found, source)
                    ev.text = reply[0]
            put({"kind": ev.kind, "text": ev.text, **({"code": code} if ev.kind == "error" else {}), **ev.meta})
            if ev.kind == "done" and shown:
                put({"kind": "blocks", "text": "", "blocks": shown})
        if run.cancel.is_set() and not run.sealed:
            getattr(events, "close", lambda: None)()   # the host's turn lets go of its session now
            raise Stopped()
        if not finished and not failed:
            failed, code = "답변 생성이 완료되지 않았습니다", "incomplete"
            put({"kind": "error", "text": failed, "code": code})
        if finished and not failed and reply[0].strip() and not run.cancel.is_set():
            run.step("explain", "writing")
            put({"kind": "simple_start", "text": ""})
            try:
                simple_finished = False
                for ev in explain("".join(reply), cfg["model"], cfg["effort"]):
                    if run.cancel.is_set():
                        raise ValueError("멈췄다")
                    if ev.kind == "delta":
                        simple += ev.text
                        if verification is not None:
                            continue   # a verified answer's explanation is shown once it is checked
                    elif ev.kind == "done":
                        if ev.meta.get("error") or not ev.text.strip():
                            raise ValueError(ev.text or "쉬운 설명이 비어 있습니다")
                        # Explaining may drop a fact of the accepted answer, never add one.
                        new = translate.added(reply[0], ev.text) if verification is not None else []
                        if new:
                            raise ValueError(f"표시 오류 — 답에 없는 숫자나 식별자가 생겼다: {', '.join(new[:5])}")
                        simple = ev.text
                        simple_finished = True
                        simple_meta = ev.meta
                    elif ev.kind == "error":
                        raise ValueError(ev.text)
                    if ev.kind in ("delta", "done"):
                        put({"kind": "simple_" + ev.kind, "text": ev.text, **ev.meta})
                if not simple_finished:
                    raise ValueError("쉬운 설명 생성이 완료되지 않았습니다")
            except Exception as exc:
                simple, simple_error = "", f"{type(exc).__name__}: {exc}"
                put({"kind": "simple_error", "text": simple_error})
    except Stopped:
        # A stop is its own outcome: not an error, not an answer. What had
        # streamed before it stays on record beside the reason.
        failed, code = "멈췄다", "cancelled"
        put({"kind": "cancelled", "text": failed, "code": code})
    except Exception as exc:  # a cut run still owes the screen a reason
        failed, code = f"{type(exc).__name__}: {exc}", "internal"
        put({"kind": "error", "text": failed, "code": code})
    finally:
        try:
            if reply or failed:
                keep(cid, "assistant", "".join(reply), failed, repo=run.repo,
                         simple_text=simple, simple_error=simple_error, simple_meta=simple_meta,
                         provider="codex" if cfg["model"].startswith("codex:") else "claude", run_id=run.id,
                         **({"cancelled": True} if code == "cancelled" else {}),
                         **({"blocks": shown} if shown else {}),
                         **({"verification": verification} if verification else {}), **metadata)
        finally:
            try:
                outcome = ("cancelled" if code == "cancelled" else "failed" if failed else
                           verification["status"] if verification else "answered")
                run.finish(outcome, code or None, out if verification else None, answered="".join(reply))
            finally:
                release()


class Stopped(Exception):
    """The run's stop was heard: nothing more is published."""


# -- Trigger hits -----------------------------------------------------------

def repo_url(repo: Path) -> str:
    """`git@…` and `https://…` both to one web address. Empty with no remote."""

    remote = run(repo, "remote", "get-url", "origin")
    if not remote:
        return ""
    remote = re.sub(r"^git@([^:]+):", r"https://\1/", remote)
    return re.sub(r"\.git$", "", remote)


def hits_for(text: str) -> list[str]:
    """A display-time trigger match.

    Not evidence that the host injected anything, and it does not re-run the
    hook.
    """
    repo = current_repo()
    try:
        return [label(path) for _severity, _body, path in match_pages(text, pages(repo.name, repo))]
    except Exception:
        return []


# -- That was wrong ---------------------------------------------------------

class Mark(BaseModel):
    kind: str            # correction, re-entry, partial completion, reversal
    user_text: str
    assistant_text: str = ""
    session_id: str = ""


KINDS = ("교정", "재입력", "부분수행", "되돌림")


@router.post("/api/mark/{cid}")
def mark(cid: str, body: Mark) -> dict:
    """A person marks it at the moment it was wrong, in the census's own format.

    A census only speaks once dozens of sessions have piled up, and it rides
    on the bias of its marker regexes. A person naming the category at the
    moment it went wrong has neither problem. `raw/corrections.jsonl` carries
    the same columns as the census output — session, order, at, chars, text —
    plus `kind`.
    """

    known(cid)
    if body.kind not in KINDS:
        raise HTTPException(400, f"부류는 {' · '.join(KINDS)} 중 하나")
    CORRECTIONS.parent.mkdir(parents=True, exist_ok=True)
    order = sum(1 for _ in CORRECTIONS.open(encoding="utf-8")) if CORRECTIONS.exists() else 0
    row = {
        "session": body.session_id or f"chat:{cid}",
        "order": order,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "chars": len(body.user_text),
        "text": body.user_text,
        "kind": body.kind,
        "channel": cid,
        "repo": config(cid)["repo"],
        "answer": body.assistant_text[:600],
    }
    with CORRECTIONS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True, "total": order + 1}
