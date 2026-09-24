"""chat — find the grounds with Claude or Codex, then explain them plainly.

The explanation comes from a separate call, so that finding the answer and
saying it simply are never the same turn. Everything that reaches the screen
is read by a person and stays Korean.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# On Windows `mimetypes` reads the registry, where `.js` is commonly
# `text/plain`. The browser then refuses `<script type="module">` silently:
# nothing in the console, just an empty screen. It really did go blank that
# way once. The type is looked up per request, so registering it after the
# imports is not too late.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import chat_channels  # noqa: E402
import mirror  # noqa: E402
import translate  # noqa: E402
from agent.chat_session import ChatSession, explain  # noqa: E402
from session_state import active_page, branch_line, decisions, run  # noqa: E402

ROOT = HERE.parent
LOGS = ROOT / "raw" / "chat"
DIST = ROOT / "web" / "dist"
CORRECTIONS = ROOT / "raw" / "corrections.jsonl"
DROPPED = ROOT / "raw" / "retro-dropped.jsonl"
PY = sys.executable

MAX_REPLAY = 200  # How many past turns the screen restores

_sessions: dict[tuple[str, str], ChatSession] = {}
_lock = threading.Lock()
_busy: set[str] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """When the server goes down the children go with it, or `claude.exe` piles up.

    Runs on Ctrl+C and on an ordinary shutdown. A forced kill — Task Manager,
    `kill` — never reaches this, and the children survive it. No language
    catches that one.
    """

    yield
    with _lock:
        alive = list(_sessions.values())
        _sessions.clear()
    for chat in alive:
        chat.close()


app = FastAPI(title="wiki chat", lifespan=lifespan)


# Every channel shares the project selection. The conversation and the model
# settings are per project and per channel.
_config: dict[tuple[str, str], dict] = {}
_project: str | None = None


def project() -> str:
    global _project
    if _project is None:
        path = LOGS / "project.json"
        name = json.loads(path.read_text(encoding="utf-8")) if path.exists() else chat_channels.WIKI.name
        if not isinstance(name, str):
            raise HTTPException(409, "저장된 프로젝트 설정을 읽을 수 없습니다")
        _project = name
    return _project


def session_key(cid: str) -> tuple[str, str]:
    return (str(repo_of(cid)), cid)


def config(cid: str) -> dict:
    key = (project(), cid)
    if key not in _config:
        channel = chat_channels.get(cid)
        model = chat_channels.LOCAL.get("model", channel.model)
        effort = channel.effort
        if model.startswith("codex:"):
            try:
                selected = next(m for m in chat_channels.codex_models() if m["id"] == model)
                if effort not in {e["id"] for e in selected["efforts"]}:
                    effort = selected["default_effort"]
            except Exception as exc:
                raise HTTPException(503, "기본 Codex 모델을 확인할 수 없습니다. 설치 명령을 다시 실행하세요.") from exc
        _config[key] = {"repo": project(),
                        "model": model,
                        "effort": effort}
    return _config[key]


def session(cid: str) -> ChatSession:
    """A channel's live conversation, started if there is none.

    The channel's introduction goes in as a system prompt. Sent as the first
    turn it took two minutes — the model reads the introduction and starts
    going through files — against six seconds for the actual question.

    A dead process is never swapped for a new object. Swapping it throws away
    the `session_id` that object was holding, leaving `--resume` nothing to
    attach to, and then the conversation disappears quietly on every model or
    effort change. That is exactly what happened: the response said
    `kept: True` while the session id had been replaced. Reviving it is
    `ensure`'s job.

    Going to another project and coming back reuses the same object for that
    project and channel.
    """

    with _lock:
        key = session_key(cid)
        chat = _sessions.get(key)
        if chat is None:
            channel = chat_channels.get(cid)
            cfg = config(cid)
            repo = repo_of(cid)
            chat = ChatSession(repo, system=chat_channels.ANSWER_PROMPT + "\n\n" + channel.preamble,
                               model=cfg["model"], effort=cfg["effort"])
            # Restarting the server is not clearing the conversation either.
            # Only the same CLI, after an explicit reset, is resumed.
            for row in reversed(recall(cid, include_context=True)):
                if row.get("role") == "context":
                    break
                if row.get("session_id"):
                    provider = row.get("provider") or ("claude" if str(row.get("model", "")).startswith("claude-") else "codex")
                    if provider == ("codex" if chat.is_codex else "claude"):
                        chat.session_id = row["session_id"]
                    break
            _sessions[key] = chat
        return chat


# -- Records ----------------------------------------------------------------
# No database. On a localhost one person uses, there is nothing a line of
# jsonl cannot do.

def remember(cid: str, role: str, text: str, error: str = "", **extra) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.time(), "role": role, "text": text, "repo": str(repo_of(cid)), **extra}
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
            belongs = not row.get("repo") if legacy else row.get("repo") == str(repo_of(cid))
            if belongs and (include_context or row.get("role") in ("user", "assistant")):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows[-MAX_REPLAY:]


# -- API ------------------------------------------------------------------

class Say(BaseModel):
    text: str


class Config(BaseModel):
    repo: str
    model: str = ""
    effort: str = ""


@app.get("/api/options")
def options() -> dict:
    """Everything the screen can choose between."""

    codex, error = [], ""
    try:
        codex = chat_channels.codex_models()
    except Exception as exc:
        error = f"Codex 모델 목록을 불러오지 못했습니다: {exc}"
    return {"projects": chat_channels.projects(),
            "models": chat_channels.MODELS + codex,
            "efforts": chat_channels.EFFORTS, "codex_error": error}


@app.get("/api/channels")
def channels() -> list[dict]:
    return [
        {"id": c.id, "label": c.label, "blurb": c.blurb,
         "live": session_key(c.id) in _sessions and _sessions[session_key(c.id)].alive,
         "model_name": _sessions[session_key(c.id)].model_name if session_key(c.id) in _sessions else "",
         # Used to turn a SHA in an answer into a commit link. Empty with no
         # remote.
         "remote": repo_url(repo_of(c.id)),
         **config(c.id)}
        for c in chat_channels.CHANNELS
    ]


@app.post("/api/config/{cid}")
def configure(cid: str, body: Config) -> dict:
    """The project applies to every channel; the model and effort apply to the
    channels of the selected project."""
    global _project

    if cid not in chat_channels.BY_ID:
        raise HTTPException(404, "그런 채널이 없다")
    if chat_channels.repo_for(body.repo) is None:
        raise HTTPException(400, f"그런 저장소가 없다: {body.repo}")
    models = chat_channels.MODELS
    if body.model.startswith("codex:"):
        try:
            models = chat_channels.codex_models()
        except Exception as exc:
            raise HTTPException(503, f"Codex 모델 목록 확인 실패: {exc}") from exc
    selected = next((m for m in models if m["id"] == body.model), None)
    if selected is None and not body.model.startswith("codex") and chat_channels.CLAUDE_MODEL.fullmatch(body.model):
        # Typed in by hand. The list only holds aliases, which already follow
        # the latest model; a name that is not on it — a new family, a pinned
        # version — goes to `--model` as it is and the CLI says whether it
        # knows it.
        selected = {"id": body.model}
    if selected is None:
        raise HTTPException(400, "지원하지 않는 모델 선택")
    efforts = {e["id"] for e in selected.get("efforts", chat_channels.EFFORTS)}
    if body.effort not in efforts:
        raise HTTPException(400, "이 모델이 지원하지 않는 effort")

    with _lock:
        switched = body.repo != project()
        if cid in _busy or (switched and _busy):
            raise HTTPException(409, "답변 생성이 끝난 뒤 설정을 바꿔 주세요")
        if switched:
            # Changed only while no turn is being recorded. If the disk write
            # fails, the selection stays where it was too.
            LOGS.mkdir(parents=True, exist_ok=True)
            path = LOGS / "project.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(body.repo, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(path)
            _project = body.repo
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


@app.get("/api/log/{cid}")
def log(cid: str, legacy: bool = False) -> list[dict]:
    if cid not in chat_channels.BY_ID:
        raise HTTPException(404, "그런 채널이 없다")
    return recall(cid, legacy)


@app.post("/api/reset/{cid}")
def reset(cid: str) -> dict:
    if cid not in chat_channels.BY_ID:
        raise HTTPException(404, "그런 채널이 없다")
    with _lock:
        if cid in _busy:
            raise HTTPException(409, "답변 생성이 끝난 뒤 대화를 초기화해 주세요")
        remember(cid, "context", "사용자가 문맥 지우기")
        chat = _sessions.pop(session_key(cid), None)
    if chat:
        chat.close()
    return {"ok": True}


@app.post("/api/say/{cid}")
def say(cid: str, body: Say) -> StreamingResponse:
    if cid not in chat_channels.BY_ID:
        raise HTTPException(404, "그런 채널이 없다")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "빈 발화")
    with _lock:
        if cid in _busy:
            raise HTTPException(409, "이 채널의 답변을 생성하고 있습니다")
        _busy.add(cid)
        cfg = dict(config(cid))

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
            pages = hits_for(cid, text)
            if pages:
                yield sse({"kind": "hits", "text": "", "pages": pages})
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
                with _lock:
                    _busy.discard(cid)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


# -- Trigger hits -----------------------------------------------------------

def repo_url(repo: Path) -> str:
    """`git@…` and `https://…` both to one web address. Empty with no remote."""

    remote = run(repo, "remote", "get-url", "origin")
    if not remote:
        return ""
    remote = re.sub(r"^git@([^:]+):", r"https://\1/", remote)
    return re.sub(r"\.git$", "", remote)


def repo_of(cid: str) -> Path:
    repo = chat_channels.repo_for(project())
    if repo is None:
        raise HTTPException(409, "선택한 프로젝트를 찾을 수 없습니다")
    return repo


def hits_for(cid: str, text: str) -> list[str]:
    """A display-time trigger match.

    Not evidence that the host injected anything, and it does not re-run the
    hook.
    """
    from wiki import label, match_pages, pages
    repo = repo_of(cid)
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


@app.post("/api/mark/{cid}")
def mark(cid: str, body: Mark) -> dict:
    """A person marks it at the moment it was wrong, in the census's own format.

    A census only speaks once dozens of sessions have piled up, and it rides
    on the bias of its marker regexes. A person naming the category at the
    moment it went wrong has neither problem. `raw/corrections.jsonl` carries
    the same columns as the census output — session, order, at, chars, text —
    plus `kind`.
    """

    if cid not in chat_channels.BY_ID:
        raise HTTPException(404, "그런 채널이 없다")
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


# -- Handover ---------------------------------------------------------------

@app.post("/api/handoff/{cid}")
def handoff(cid: str) -> dict:
    """The prompt to hand the next session. A request the census counted 20 times.

    It carries only what a machine can count — the branch, the open plans, the
    recent decisions, the uncommitted changes, this channel's last exchange.
    The "what next" line is added by a person. A machine writing that line
    hands the next session something wrong, confidently.
    """

    if cid not in chat_channels.BY_ID:
        raise HTTPException(404, "그런 채널이 없다")
    repo = repo_of(cid)
    lines = [f"# 인계 — `{repo.name}` · {dt.date.today():%Y-%m-%d}", "",
             "## 지금", "", f"브랜치 {branch_line(repo)}"]

    changed = run(repo, "status", "--short")
    stat = run(repo, "diff", "--shortstat")
    if changed:
        lines += ["", "## 미커밋 변경", "", "```", changed, "```"]
        if stat:
            lines.append(stat)

    body, stale = active_page(repo)
    if body:
        lines += ["", "## 열린 계획 (`.wiki/plan-active.md`)", ""]
        if stale:
            lines.append(f"**낡았을 수 있다** — `{'`, `'.join(stale)}` 가 더 나중에 고쳐졌다.\n")
        lines.append(body)

    recent = decisions(repo)
    if recent:
        lines += ["", "## 최근 결정 — 뒤집기 전에 이유를 보라", ""]
        lines += [f"- {t} — {w}" if w else f"- {t}" for t, w in recent]

    tail = [r for r in recall(cid) if r.get("role") in ("user", "assistant")][-6:]
    if tail:
        lines += ["", f"## 이 채널(#{chat_channels.get(cid).label})에서 방금까지", ""]
        for r in tail:
            who = "나" if r["role"] == "user" else "답"
            text = " ".join(str(r.get("text", "")).split())
            lines.append(f"- **{who}**: {text[:400]}{' …' if len(text) > 400 else ''}")

    lines += ["", "## 다음에 할 것", "", "(여기부터 저기까지 — 사람이 한 줄 적는다)"]
    return {"text": "\n".join(lines)}


# -- Decisions --------------------------------------------------------------

class Decide(BaseModel):
    candidate: str
    target: str          # wiki · claude_md · drop


def oneshot(repo: Path, prompt: str, system: str, tools: str, timeout: int = 600) -> dict:
    """One turn per button press. The only path on which writing is allowed.

    The channel processes have no `Edit` and no `Write` — something opened in
    a browser that edits files is a remote shell. This function runs only when
    a person pressed a button naming one candidate, and it returns what
    changed. The button is the permission.
    """

    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--allowedTools", tools, "--append-system-prompt", system, "--effort", "high"]
    try:
        done = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        out = json.loads(done.stdout or "{}")
        return {"text": str(out.get("result") or ""), "error": bool(out.get("is_error")),
                "cost_usd": out.get("total_cost_usd")}
    except subprocess.TimeoutExpired:
        return {"text": f"{timeout}초 안에 안 끝났다.", "error": True}
    except Exception as exc:
        return {"text": f"{type(exc).__name__}: {exc}", "error": True}


# An instruction that goes to the agent as written. It is an executed string
# rather than a comment, so turning the comments English does not touch it.
# What it says about the result of that run goes back to the web screen, so
# that part is written in Korean.
WIKI_WRITER = (
    "You write one wiki page. Handle only the single candidate you were given. "
    "Follow `SCHEMA.md`'s 'page minimum structure' exactly — front matter with "
    "scope, severity, triggers, slots, sources, links; body with title, rule, "
    "why, and what happens when it is broken. Set severity by the evidence: "
    "`landmine` cannot be used without sources. If the candidate says a page "
    "already exists, **do not create a new one** — open that page and climb the "
    "ladder instead (`enforce.deny`, or stronger triggers). Create or change "
    "exactly one file. When done, run `python tool/lint.py --repo <project "
    "path>` and end with its result and what you wrote where, **in Korean** — "
    "that closing note goes back to the web screen a person reads."
)
CLAUDE_MD_WRITER = (
    "You edit **only** this repository's `CLAUDE.md`. Handle only the single "
    "candidate you were given. Find the right section and add one sentence "
    "under it, in the imperative. If a sentence already says the same thing, "
    "do not add another — point at that one. **Touch no other file for any "
    "reason**; if there is no right place, say so and stop. End with one line "
    "on what you wrote where, **in Korean** — that line goes back to the web "
    "screen a person reads."
)


@app.post("/api/decide/{cid}")
def decide(cid: str, body: Decide) -> dict:
    """What becomes of one retro candidate. The `retrospect` skill's step 5.

    The skill says to ask with options, and a headless run cannot ask. This
    app can.
    """

    if cid not in chat_channels.BY_ID:
        raise HTTPException(404, "그런 채널이 없다")
    candidate = body.candidate.strip()
    if not candidate:
        raise HTTPException(400, "빈 후보")
    repo = repo_of(cid)

    if body.target == "drop":
        DROPPED.parent.mkdir(parents=True, exist_ok=True)
        with DROPPED.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "channel": cid, "repo": repo.name,
                                 "candidate": candidate}, ensure_ascii=False) + "\n")
        return {"text": "버렸다. 다음에 또 나면 그때 다시 본다.", "error": False}

    if body.target == "wiki":
        result = oneshot(chat_channels.WIKI,
                         f"후보: {candidate}\n대상 프로젝트 경로: {repo}",
                         WIKI_WRITER, "Read,Glob,Grep,Write,Edit,Bash")
        result["changed"] = run(chat_channels.WIKI, "status", "--short")
        return result

    if body.target == "claude_md":
        # Not created if it is missing. Told only in prose to "touch nothing
        # else", a repository without a CLAUDE.md had its wiki pages edited
        # instead. That is this wiki's own argument back at it: prose is not
        # followed, and what is followed is what blocks. So it is blocked here
        # first, and the allowed tools are narrowed to that one file.
        if not (repo / "CLAUDE.md").exists():
            return {"text": f"`{repo.name}` 에는 `CLAUDE.md` 가 없다. "
                            "다른 파일을 대신 고치지 않았다.", "error": True}
        result = oneshot(repo, f"후보: {candidate}", CLAUDE_MD_WRITER,
                         "Read,Grep,Edit(CLAUDE.md)")
        result["changed"] = run(repo, "diff", "--stat")
        return result

    raise HTTPException(400, "target 은 wiki · claude_md · drop 중 하나")


# -- Looking inside a file --------------------------------------------------

@app.get("/api/file")
def peek(repo: str, path: str, line: int = 1, around: int = 25) -> dict:
    """Show the place a quoted `path:line` points at. Never outside the repository."""

    base = chat_channels.repo_for(repo)
    if base is None:
        raise HTTPException(400, "그런 저장소가 없다")
    target = (base / path).resolve()
    if base.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "그 파일이 없다")
    if target.stat().st_size > 2_000_000:
        raise HTTPException(413, "너무 크다")
    rows = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, line - around)
    end = min(len(rows), line + around)
    return {"path": path, "start": start, "line": line, "total": len(rows),
            "lines": rows[start - 1:end]}


# -- translation -------------------------------------------------------------
#
# A door onto the phase-one translator so the screens can call it. Not a second
# engine: the same `tool/translate/`, and the same cache, so a sentence the
# hooks already rendered costs the screen nothing.
#
# Failure returns the original. An English screen beats an empty one, and that
# judgement already lives inside the translator.

TRANSLATE_MAX = 40          # sentences per request
TRANSLATE_CHARS = 40_000    # characters per request
TRANSLATE_SECONDS = 60.0    # an overlay renders whole answers; see `mirror.SCREEN_SECONDS`


class Rendering(BaseModel):
    texts: list[str]
    direction: str = translate.EN_KO


@app.post("/api/translate")
def render(body: Rendering) -> dict:
    """Render what a screen is about to show.

    The size limits are here because the monthly limit is shared. A screen
    that accidentally posts a whole document burns what the hooks were going
    to spend — one cache, one bill, one ceiling.
    """

    if body.direction not in (translate.KO_EN, translate.EN_KO):
        raise HTTPException(400, "그런 방향이 없다")
    if len(body.texts) > TRANSLATE_MAX:
        raise HTTPException(413, f"한 번에 {TRANSLATE_MAX} 문장까지다")
    if sum(len(t) for t in body.texts) > TRANSLATE_CHARS:
        raise HTTPException(413, f"한 번에 {TRANSLATE_CHARS} 자까지다")
    return {"texts": translate.translate(
        list(body.texts), body.direction, time.monotonic() + TRANSLATE_SECONDS
    )}


# -- the Korean mirror --------------------------------------------------------
#
# What the person reads while the agent writes English. It is a reading screen,
# but it does write one thing to the server: which repository to watch. So the
# generation was decided before the wiring — `Station.gen` rises on every
# switch, travels out with every payload, and a tab drops anything that does
# not carry the number it is showing. A translation is a round trip, so an
# answer for the old repository arriving after the switch is the normal case,
# not the rare one.

_station = mirror.Station(host="claude", poll=mirror.POLL)


def _pointed() -> tuple[int, object, str, str]:
    """Point somewhere live — if nothing was chosen, or what was chose is gone.

    Two things move the mirror without anyone asking. Nothing has been chosen
    yet, which is the first time the tab is opened. Or the checkout it was
    watching has been deleted: a worktree goes, its log stays behind, and a
    mirror that only asked "is there a log" would sit on a dead file showing
    the last thing a directory that no longer exists ever said.

    `is_dir` is the cheap half and runs every pass. Listing checkouts is the
    expensive half — it walks directories, reads the head of each log and asks
    git which repository each one belongs to — so it runs only when the cheap
    half already said something is wrong. Someone who never opens the mirror
    never pays for it.
    """

    gen, feed, host, project = _station.now()
    if project and Path(project).is_dir():
        return gen, feed, host, project
    found = mirror.checkouts(host)
    if found:
        _station.point(host, Path(found[0]["path"]))
    return _station.now()


@app.get("/api/mirror/repos")
def mirror_repos() -> dict:
    """Every checkout with a session, and which repository each is of.

    Both hosts record the real path, so the list is not a guess — but a
    worktree's directory name is one. Two repositories each had a worktree
    called `pollock`, and the picker offered both under that one word.
    """

    _, _, host, project = _pointed()
    return {"here": {"host": host, "project": project},
            "hosts": {name: mirror.checkouts(name) for name in sorted(mirror.HOSTS)}}


class Point(BaseModel):
    host: str
    project: str


@app.post("/api/mirror/point")
def mirror_point(body: Point) -> dict:
    """Move the mirror to another repository, from this server's own list.

    That the screen named a path is not a reason to open it, and running on
    the same machine does not make it one.
    """

    if body.host not in mirror.HOSTS:
        raise HTTPException(404, "그런 호스트가 없다")
    if body.project not in {row["path"] for row in mirror.checkouts(body.host)}:
        raise HTTPException(404, "그 작업트리의 세션이 없다")
    _station.point(body.host, Path(body.project))
    gen, _, host, project = _station.now()
    return {"gen": gen, "host": host, "project": project}


@app.get("/api/mirror/stream")
def mirror_stream() -> StreamingResponse:
    """What the mirror has rendered, and what arrives next.

    Sent even when empty: it is the heartbeat as well as the data. A tab has
    no other way to tell a quiet mirror from a dead one, and writing to a
    closed socket is how this loop learns to stop.
    """

    def stream():
        cursor = 0
        seen = -1
        while True:
            gen, feed, host, project = _pointed()
            if gen != seen:
                # The repository changed. The cursor belongs to the new feed,
                # and the screen clears itself on seeing the new `gen`.
                cursor, seen = 0, gen
            cursor, parts = feed.since(cursor)
            # `feed` rather than `gen` is what the tab resets on. Both numbers
            # restart with this process; the id does not.
            yield sse({"gen": gen, "feed": feed.id, "host": host,
                       "project": project, "parts": parts})
            time.sleep(mirror.BEAT)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# -- The screen -------------------------------------------------------------

@app.get("/api/graph")
def wiki_graph() -> FileResponse:
    """The policy graph: `graph.json` as `graph.py` produced it.

    For a long time this served a whole `wiki.html` instead. That made two
    layers — Python building a screen and this app wrapping it in an iframe —
    and two copies of the code that draws the same graph. The server hands
    over the data and the screen does the drawing.
    """

    page = chat_channels.WIKI / "graph.json"
    if not page.exists():
        raise HTTPException(404, "python tool/graph.py 를 먼저 돌려라")
    return FileResponse(page, media_type="application/json")


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        # The assets under it are content-hashed and may be cached forever.
        # This file is the only thing that says which hash is current, so a
        # cached copy of it pins the tab to a build that no longer exists on
        # disk — and the person reloads, sees the old screen, and reports the
        # bug that was just fixed. That happened.
        return FileResponse(DIST / "index.html",
                            headers={"Cache-Control": "no-store"})
else:
    @app.get("/")
    def index() -> dict:
        return {"화면이 아직 없다": "npm --prefix web run build 를 먼저 돌려라"}


def taken(host: str, port: int) -> bool:
    """Is somebody already listening on that port?"""

    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def demo() -> None:
    """Does the conversation survive a model or effort change? It once did not.

    No HTTP. The bug was `session()` swapping a dead process for a new object
    and throwing the `session_id` away, and calling these two functions is
    enough to reproduce it.

        python tool/chat.py --check
    """

    cid = "wiki"
    token = "QUOKKA-9"
    reset(cid)
    try:
        first = last = {}
        for ev in session(cid).say(f"이 표를 기억해라: {token}. '알겠다' 한 마디만."):
            if ev.kind == "done":
                first = ev.meta

        answer = configure(cid, Config(repo=config(cid)["repo"], effort="max"))
        assert answer["kept"], answer

        text = ""
        for ev in session(cid).say("아까 기억하라고 한 표를 그대로 적어라. 그것만."):
            if ev.kind == "done":
                text, last = ev.text, ev.meta

        assert first.get("session_id"), first
        assert first["session_id"] == last.get("session_id"), (first, last)
        assert token in text, text
        print(f"ok  effort 를 바꿔도 대화가 남는다 — session {first['session_id']}")
        print(f"    되찾은 표: {text.strip()[:40]}")
    finally:
        reset(cid)


def main() -> int:
    import uvicorn

    # The port notice and the self-check output are both Korean. The encoding
    # is not left to the environment — this repository has been burned by one
    # cp949 character eight times.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    # 8000 is taken on this machine by another project, as its adapter says.
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--workspace", type=Path, help="프로젝트들이 들어 있는 폴더")
    # Bound to 127.0.0.1. This server has no authentication, and opening it to
    # the LAN without any is not offering a chat, it is handing someone a
    # shell.
    ap.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    ap.add_argument("--check", action="store_true", help="자체 점검만 하고 끝낸다")
    # A launcher opens one screen of this app, not the app in general. The
    # value is the fragment that names a tab — `#mirror`, `#map`, or empty for
    # whatever the app opens on.
    ap.add_argument("--open", default="", metavar="#탭", help="뜬 뒤 브라우저로 연다")
    args = ap.parse_args()

    if args.workspace:
        chat_channels.WORKSPACE = args.workspace.expanduser().resolve()
    if not chat_channels.WORKSPACE.is_dir():
        ap.error("프로젝트 폴더가 없습니다. --workspace로 실제 폴더를 지정하세요.")
    # Pointed at the wiki itself, there are no repositories underneath and the
    # list quietly shrinks to the wiki alone.
    if chat_channels.WORKSPACE == chat_channels.WIKI:
        ap.error("프로젝트 폴더가 위키 자신입니다. 프로젝트들이 들어 있는 상위 폴더를 지정하세요: "
                 f"{chat_channels.WIKI.parent}")

    if args.check:
        demo()
        return 0

    # With the port taken, uvicorn prints one English line and exits 1. A
    # double-clicked window closes before that line can be read, so it looks
    # like the thing starts and then just dies. It really did once. Say what
    # is blocked and how to clear it, first.
    url = f"http://{args.host}:{args.port}/{args.open.lstrip('/')}"

    if taken(args.host, args.port):
        # A launcher asked for a screen, and the server that serves it is
        # already up. Opening it is the whole request — telling the person to
        # kill the process they are already using would be absurd.
        if args.open:
            print(f"이미 떠 있다 — {url}")
            webbrowser.open(url)
            return 0
        print(
            f"\n{args.port} 번 포트를 이미 누가 듣고 있다. 그래서 안 뜬다.\n\n"
            f"  누구인지 본다   netstat -ano | findstr :{args.port}\n"
            f"  그것을 내린다   taskkill /pid <위에서 본 PID> /f\n"
            f"  다른 포트로     tool\\chat.cmd --port 9090\n",
            file=sys.stderr,
        )
        return 1

    if args.open:
        # `uvicorn.run` blocks, so the browser has to be opened from a thread
        # that waits for the port to answer. Opening first races the server
        # and lands on a refused connection.
        def wait_then_open() -> None:
            for _ in range(100):
                if taken(args.host, args.port):
                    webbrowser.open(url)
                    return
                time.sleep(0.1)

        threading.Thread(target=wait_then_open, daemon=True).start()

    print(f"{url}  — 끄려면 Ctrl+C")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
