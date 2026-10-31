"""The focuses the wiki query can take, and what can be chosen — project, model, effort.

They were channels of the old chat screen, and their ids still name the
records in `raw/chat/`. The labels and preambles here are read by a person on
screen, so they stay Korean.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time

from agent import CodexServer, settings

WIKI = Path(__file__).resolve().parents[2]
LOCAL = settings()
WORKSPACE = (WIKI / Path(LOCAL.get("workspace", "..")).expanduser()).resolve()

# What the CLI accepts by name in `--model`. An empty value means the flag is
# not passed at all, which leaves the CLI on its own default model.
MODELS = [
    {"id": "", "label": "Claude 기본", "note": "Claude CLI 가 정한 것"},
    {"id": "opus", "label": "Opus", "note": "제일 세다. 제일 느리다"},
    {"id": "sonnet", "label": "Sonnet", "note": "보통 이거면 된다"},
    {"id": "haiku", "label": "Haiku", "note": "짧은 확인용"},
    {"id": "fable", "label": "Fable", "note": ""},
]

# What a hand-typed Claude model name may look like: `opus`, `claude-opus-5-5`,
# `opus[1m]`. It reaches the CLI as one argument, never through a shell.
CLAUDE_MODEL = re.compile(r"[a-z][a-z0-9.\-]{0,63}(\[1m\])?")

# The five `--effort` takes. Further up thinks longer and costs more.
EFFORTS = [
    {"id": "", "label": "기본", "note": "CLI 가 정한 것"},
    {"id": "low", "label": "low", "note": "빠른 사실 확인"},
    {"id": "medium", "label": "medium", "note": ""},
    {"id": "high", "label": "high", "note": "원인을 캐야 할 때"},
    {"id": "xhigh", "label": "xhigh", "note": ""},
    {"id": "max", "label": "max", "note": "제일 비싸다"},
]

ANSWER_PROMPT = (WIKI / "tool/prompts/chat-answer.md").read_text(encoding="utf-8")


# How long a fetched Codex model list is served before asking again. Held for
# the life of the server, a Codex update that brought new models stayed
# invisible until the chat was restarted.
CODEX_MODELS_TTL = 300
_codex_cache: dict = {}


def codex_models() -> list[dict]:
    """The installed Codex's public model list.

    A failure is not cached — caching one would make a CLI that came back up
    look permanently broken.
    """
    if _codex_cache and time.monotonic() - _codex_cache["at"] < CODEX_MODELS_TTL:
        return _codex_cache["models"]
    models, cursor = [], None
    with CodexServer() as server:
        while True:
            result = server.request("model/list", {"limit": 100, "includeHidden": False, "cursor": cursor})
            for model in result["data"]:
                default = model["defaultReasoningEffort"]
                models.append({
                    "id": "codex:" + model["model"], "label": model["displayName"], "note": "Codex",
                    "default_effort": default,
                    "is_default": model.get("isDefault", False),
                    "supports_fast": any(tier.get("id") in {"fast", "priority"} for tier in model.get("serviceTiers", []))
                    or bool({"fast", "priority"} & set(model.get("additionalSpeedTiers", []))),
                    "efforts": [{"id": "", "label": f"기본 ({default})", "note": ""}] + [
                        {"id": e["reasoningEffort"], "label": e["reasoningEffort"], "note": ""}
                        for e in model["supportedReasoningEfforts"]
                    ],
                })
            cursor = result.get("nextCursor")
            if not cursor:
                break
    if not models:
        raise RuntimeError("Codex가 사용 가능한 모델을 반환하지 않았습니다")
    _codex_cache.update(at=time.monotonic(), models=models)
    return models


codex_models.cache_clear = _codex_cache.clear


def efforts_of(model: str) -> set[str]:
    """The efforts `model` takes: a Codex model's own list — empty for a model
    Codex does not list — else the Claude CLI's five and the CLI default."""

    if model.startswith("codex:"):
        found = next((m for m in codex_models() if m["id"] == model), None)
        return {e["id"] for e in found["efforts"]} if found else set()
    return {e["id"] for e in EFFORTS}


def projects() -> list[dict]:
    """The repositories that can be chosen: those connected to this wiki, in the
    workspace or wherever [폴더 찾기] picked them (`connect.added`). One not
    connected is not offered: nothing runs in it, and it only crowded the
    picker. Each carries its state (`connect.status`): `연결 완료` or `일부`.
    """

    from . import connect  # `connect` imports this module

    names = sorted({path.name for path in (WORKSPACE.iterdir() if WORKSPACE.is_dir() else [])
                    if path.name != WIKI.name and (path / ".git").exists()} | set(connect.added_names()))
    wired = connect.users()
    found = []
    for path in filter(None, map(repo_for, [WIKI.name, *names])):
        state = connect.status(path, wired)
        if state["state"] != "미연결":
            found.append({"id": path.name, "path": str(path), **state})
    return found


def repo_for(name: str) -> Path | None:
    """A repository path by name: a folder a person picked for it
    (`connect.added`), or one in the workspace. Nowhere else — this value came
    from the screen. A picked name belongs to that repository alone: moved or
    replaced, it names nothing — never a workspace folder of the same name,
    which would take over its records — until the person picks it again."""

    if not name or name != Path(name).name or name in (".", ".."):
        return None
    if name == WIKI.name:
        return WIKI
    from . import connect
    if connect.registered(name):
        return connect.added(name)
    path = (WORKSPACE / name).resolve()
    if name == path.name and path.parent == WORKSPACE.resolve() and (path / ".git").exists():
        return path
    return None


@dataclass(frozen=True)
class Channel:
    id: str
    label: str
    blurb: str
    preamble: str
    model: str = ""     # the CLI's own default model
    effort: str = ""    # the CLI's own default effort


CHANNELS: list[Channel] = [
    Channel(
        id="next",
        label="다음 작업",
        blurb="후보를 내고, 되물어, 명세로 정한다",
        preamble=(WIKI / "tool/prompts/next-task.md").read_text(encoding="utf-8").strip(),
    ),
    Channel(
        id="refactor",
        label="리팩터링",
        blurb="목적 모드를 골라 저장소를 조사하고, 파이프라인별 설명과 실행할 명세를 만든다",
        preamble=(WIKI / "tool/prompts/next-task.md").read_text(encoding="utf-8").strip() + "\n\n"
                 + (WIKI / "tool/prompts/refactor-task.md").read_text(encoding="utf-8").strip(),
    ),
    Channel(
        id="wiki",
        label="위키",
        blurb="위키와 도구를 본다 — 왜 안 되나는 기록부터",
        preamble=(
            "Focus: answer questions about the selected repository using its "
            "documents, plans, decisions and implementation. Interpret them to "
            "answer the user's intent. For changes to shared wiki rules, read "
            "the hub's `SCHEMA.md` and `ENFORCEMENT.md`. Before proposing a "
            "new page, establish that the documented admission threshold is met; "
            "additional pages have a context cost.\n\n"
            "When the question is why something does not work, look at what ran "
            "before reading code: follow `.wiki/telemetry.md` where it exists, and "
            "inspect the available `.omm/`, `data/latency_logs/`, Langfuse and "
            "`artifacts/live/*.jsonl` evidence. Code shows what could happen; logs "
            "show what ran. Label hypotheses, state the observation that would "
            "confirm them, and name the sources that were unavailable."
        ),
    ),
    Channel(
        id="retro",
        label="회고",
        blurb="오늘 어긋난 자리를 센다",
        effort="high",
        preamble=(
            "Focus: retrospective. Follow the retrospect skill. Read today's commits "
            "and count corrections, repeated input, partial completion, and reversals "
            "in session logs. Use the wiki's `tool/transcript.py --project <repository>` "
            "to extract user turns and tool counts instead of loading entire logs. "
            "Do not list accomplishments or edit files. End with a fenced block "
            "labeled `retro-candidates`, one candidate per line (empty if none). "
            "Each line: Korean category, count with the Korean occurrence unit, "
            "middle dot, imperative rule, middle dot, existing page ID or a Korean "
            "label meaning new candidate. A repeated violation of an existing page "
            "calls for stronger enforcement, not duplicated prose."
        ),
    ),
]

BY_ID = {c.id: c for c in CHANNELS}


def get(cid: str) -> Channel:
    if cid not in BY_ID:
        raise KeyError(cid)
    return BY_ID[cid]
