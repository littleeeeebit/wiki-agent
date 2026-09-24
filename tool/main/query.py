"""query — ask the wiki, under one of five focuses, and read the grounds.

Finding the answer and saying it simply are separate calls, so they are never
the same turn. Everything that reaches the screen is read by a person and
stays Korean.
"""

from __future__ import annotations

import datetime as dt
from contextvars import ContextVar
from urllib.parse import quote
import json
import re
import threading
import time
import weakref
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import ChatSession, explain
from session_state import active_page, decisions, run
from wiki import label, match_pages, pages

from . import channels

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
            chat = ChatSession(current_repo(), system=channels.ANSWER_PROMPT + "\n\n" + channels.get(cid).preamble,
                               model=cfg["model"], effort=cfg["effort"])
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

def remember(cid: str, role: str, text: str, error: str = "", **extra) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.time(), "role": role, "text": text, "repo": str(current_repo()), **extra}
    if error:
        row["error"] = error
    with (LOGS / f"{cid}.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def recall(cid: str, legacy: bool = False, include_context: bool = False) -> list[dict]:
    path = LOGS / f"{cid}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            belongs = not row.get("repo") if legacy else row.get("repo") == str(current_repo())
            if belongs and (include_context or row.get("role") in ("user", "assistant")):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows[-MAX_REPLAY:]


def sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def streaming(events) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def hold(busy: dict, lock: threading.Lock, key: str, refused: str):
    """Take `key` for a stream from the moment the request is accepted, or 409.

    Returns the release; call it from the stream's `finally` and pass it to
    `held`. Two ways out, and both are covered. A body that runs releases in
    its `finally`. A body that never starts — the client gone before the first
    byte — runs no `finally`, so the release also rides on the body being
    collected. Taken only inside the body, the key was free between accepting
    and starting, and a project switch or a removal walked through that gap.
    The token makes a second release, or a late one after a new hold, a no-op.
    """

    with lock:
        if key in busy:
            raise HTTPException(409, refused)
        token = busy[key] = object()

    def release() -> None:
        with lock:
            if busy.get(key) is token:
                del busy[key]

    return release


def held(events, release) -> StreamingResponse:
    weakref.finalize(events, release)
    return streaming(events)


# -- API ------------------------------------------------------------------

class Say(BaseModel):
    text: str


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
        # A running agent's worktree, and an approval waiting in it, belong to
        # this project. Switched away, the approval's worktree left the list
        # and the answer to it came back 404.
        if switched and work.busy():
            raise HTTPException(409, "에이전트가 도는 동안은 프로젝트를 바꾸지 않는다")
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


@router.post("/api/reset/{cid}")
def reset(cid: str) -> dict:
    known(cid)
    with _lock:
        if cid in _busy:
            raise HTTPException(409, "답변 생성이 끝난 뒤 대화를 초기화해 주세요")
        remember(cid, "context", "사용자가 문맥 지우기")
        chat = _sessions.pop(session_key(cid), None)
    if chat:
        chat.close()
    return {"ok": True}


@router.post("/api/say/{cid}")
def say(cid: str, body: Say) -> StreamingResponse:
    known(cid)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "빈 발화")
    # The settings first: `config` can fail (503 when Codex cannot list its
    # models), and after the hold nothing may fail before `held` arms the
    # release — a focus stayed busy for good that way.
    with _lock:
        cfg = dict(config(cid))
        release = hold(_busy, _lock, cid, "이 초점의 답변을 생성하고 있습니다")

    def stream():
        answer: list[str] = []
        failed = ""
        simple = ""
        simple_error = ""
        finished = False
        metadata = {}
        simple_meta = {}
        try:
            remember(cid, "user", text)
            # A display-time match against the relevant rules. Not a check
            # that the host actually injected anything.
            hits = hits_for(text)
            if hits:
                yield sse({"kind": "hits", "text": "", "pages": hits})
            for ev in session(cid).say(text):
                if ev.kind == "delta":
                    answer.append(ev.text)
                # A quota or API error arrives on `done` with `error=True`. It
                # is an error, not an answer. Held together with the partial
                # answer instead of separately, the reason for the cut-off is
                # gone when the conversation is restored — a retro that hit a
                # session limit survived as the single line `세겠습니다`.
                if ev.kind == "done" and ev.meta.get("error"):
                    failed = ev.text or "완료된 답변이 없습니다"
                    yield sse({"kind": "error", "text": failed, **ev.meta})
                    break
                if ev.kind == "error":
                    failed = ev.text
                if ev.kind == "done":
                    answer = [ev.text or "".join(answer)]
                    finished = bool(answer[0].strip())
                    metadata = ev.meta
                    metadata.pop("error", None)
                yield sse({"kind": ev.kind, "text": ev.text, **ev.meta})
            if not finished and not failed:
                failed = "답변 생성이 완료되지 않았습니다"
                yield sse({"kind": "error", "text": failed})
            if finished and not failed:
                yield sse({"kind": "simple_start", "text": ""})
                try:
                    simple_finished = False
                    for ev in explain("".join(answer), cfg["model"], cfg["effort"]):
                        if ev.kind == "delta":
                            simple += ev.text
                        elif ev.kind == "done":
                            if ev.meta.get("error") or not ev.text.strip():
                                raise ValueError(ev.text or "쉬운 설명이 비어 있습니다")
                            simple = ev.text
                            simple_finished = True
                            simple_meta = ev.meta
                        elif ev.kind == "error":
                            raise ValueError(ev.text)
                        if ev.kind in ("delta", "done"):
                            yield sse({"kind": "simple_" + ev.kind, "text": ev.text, **ev.meta})
                    if not simple_finished:
                        raise ValueError("쉬운 설명 생성이 완료되지 않았습니다")
                except Exception as exc:
                    simple, simple_error = "", f"{type(exc).__name__}: {exc}"
                    yield sse({"kind": "simple_error", "text": simple_error})
        except Exception as exc:  # a cut stream still owes the screen a reason
            failed = f"{type(exc).__name__}: {exc}"
            yield sse({"kind": "error", "text": failed})
        finally:
            try:
                if answer or failed:
                    remember(cid, "assistant", "".join(answer), failed,
                             simple_text=simple, simple_error=simple_error, simple_meta=simple_meta,
                             provider="codex" if cfg["model"].startswith("codex:") else "claude", **metadata)
            finally:
                release()

    return held(stream(), release)


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


# -- From an answer to a task -----------------------------------------------

class Draft(BaseModel):
    question: str = ""
    answer: str = ""
    hits: list[str] = []
    target: str = ""     # "" · wiki · claude_md — a retro candidate's destination


# A citation as an answer writes it and `Answer.tsx` makes clickable. A range
# comes with an en dash as often as a hyphen; a draft once lost its only
# citation, `craft/hooks-fail-open.md:11–14`, to that.
CITE = re.compile(r"`([\w./-]+\.(?:py|md|ts|tsx|js|json|toml|ya?ml|cmd|txt|css|html|jsonl)(?::\d+(?:[-–]\d+)?)?)`")

# Instructions that go to the agent as written, so they are English. What the
# agent says back about them reaches the screen, so that part is Korean. They
# used to drive a one-shot `claude -p` that wrote straight into the checkout
# without asking; now they head a draft that runs in a worktree, where every
# write is approved.
WRITERS = {
    "wiki": (
        "You write one wiki page, in this worktree of the wiki. Handle only the "
        "single candidate below. Follow `SCHEMA.md`'s 'page minimum structure' "
        "exactly — front matter with scope, severity, triggers, slots, sources, "
        "links; body with title, rule, why, and what happens when it is broken. "
        "Set severity by the evidence: `landmine` cannot be used without sources. "
        "If the candidate says a page already exists, **do not create a new one** — "
        "open that page and climb the ladder instead (`enforce.deny`, or stronger "
        "triggers). Create or change exactly one file. When done, run "
        "`python tool/lint.py --check` and end with its result and what you wrote "
        "where, **in Korean**."
    ),
    "claude_md": (
        "You edit **only** this repository's `CLAUDE.md`, in this worktree. Handle "
        "only the single candidate below. Find the right section and add one "
        "sentence under it, in the imperative. If a sentence already says the "
        "same thing, do not add another — point at that one. **Touch no other "
        "file for any reason**; if there is no `CLAUDE.md` or no right place, say "
        "so and stop. End with one line on what you wrote where, **in Korean**."
    ),
}


@router.post("/api/draft")
def draft(body: Draft) -> dict:
    """The instruction draft that carries an answer over to a worktree's agent.

    It holds only what a machine can gather — the question, the answer, the
    places it cited, the rules it matched, the open plan and the recent
    decisions. The "what to do" line is left to a person: a machine writing
    it hands the agent something wrong, confidently.
    """

    if body.target and body.target not in WRITERS:
        raise HTTPException(400, "target 은 wiki · claude_md 중 하나")
    repo = current_repo()
    lines = [f"# 작업 — `{repo.name}` · {dt.date.today():%Y-%m-%d}", ""]
    if body.target:
        lines += [WRITERS[body.target], "", "## 후보", "", body.question.strip()]
    elif body.question.strip():
        lines += ["## 물은 것", "", body.question.strip()]
    if body.answer.strip():
        lines += ["", "## 위키의 답", "", body.answer.strip()]
    cites = list(dict.fromkeys(CITE.findall(body.answer)))
    if cites:
        lines += ["", "## 근거", ""] + [f"- `{c}`" for c in cites]
    if body.hits:
        lines += ["", "## 걸린 규칙", ""] + [f"- {h}" for h in body.hits]

    plan, stale = active_page(repo)
    if plan:
        lines += ["", "## 열린 계획 (`.wiki/plan-active.md`)", ""]
        if stale:
            lines.append(f"**낡았을 수 있다** — `{'`, `'.join(stale)}` 가 더 나중에 고쳐졌다.\n")
        lines.append(plan)
    recent = decisions(repo)
    if recent:
        lines += ["", "## 최근 결정 — 뒤집기 전에 이유를 보라", ""]
        lines += [f"- {t} — {w}" if w else f"- {t}" for t, w in recent]

    lines += ["", "## 할 일", "", "(사람이 한 줄 적는다)"]
    return {"text": "\n".join(lines)}
