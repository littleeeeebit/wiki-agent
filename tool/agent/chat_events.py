"""Screen events and provider message rendering, without process ownership."""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, field


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


OUTPUT_EVERY = 0.5   # seconds one command's output gathers before it goes on as one event
OUTPUT_LIMIT = 4000  # characters of it an event carries, the latest


def merged(events, clock=time.monotonic):
    """A command's streamed output as one event per `OUTPUT_EVERY`, not one per write.

    A progress bar — a model download's — writes several times a second for as
    long as it runs, and each write was an event every watching screen redrew
    its whole turn for; the screen stopped answering, the project picker with
    it. Each command gathers its own, so two running at once still make one
    event each per `OUTPUT_EVERY`. Anything else that comes lets the gathered
    output go first.
    """

    held: dict[str, Event] = {}   # command -> its gathered output, in the order they began
    since = 0.0
    for ev in events:
        item = output_of(ev.kind, ev.meta)
        if item:
            if not held:
                since = clock()
            if item in held:
                held[item].text = (held[item].text + ev.text)[-OUTPUT_LIMIT:]
            else:
                held[item] = ev
            if clock() - since < OUTPUT_EVERY:
                continue
        yield from held.values()
        held.clear()
        if not item:
            yield ev
    yield from held.values()


def output_of(kind: str, meta: dict) -> str | None:
    """The command whose streamed output this event is, if it is one."""
    return meta.get("item_id") if kind == "tool" and meta.get("tool") == "commandExecution" else None


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


DIFF_LINES = 200   # lines of one write's diff an event carries; the changes panel has the rest


def _clipped(lines: list[str]) -> str:
    more = len(lines) - DIFF_LINES
    return "\n".join(lines[:DIFF_LINES] + ([f"… {more}줄 더 — 코드 변경 현황에서 전체를 본다"] if more > 0 else []))


def _snippet(old: str, new: str) -> list[str]:
    """Two snippets as one hunk with no header: its line numbers would count from the snippet, not the file."""

    a, b = old.splitlines(), new.splitlines()
    return list(difflib.unified_diff(a, b, lineterm="", n=max(len(a), len(b))))[3:]


def _tool_meta(block: dict) -> dict:
    """A tool call's name and, for a file edit, the lines it takes out and puts in, as the screen colours them."""

    name = str(block.get("name") or "")
    args = block.get("input") or {}
    if name == "Write" and isinstance(args.get("content"), str):
        lines = ["+" + line for line in args["content"].splitlines()]
    elif name == "Edit" and isinstance(args.get("old_string"), str) and isinstance(args.get("new_string"), str):
        lines = _snippet(args["old_string"], args["new_string"])
    elif name == "MultiEdit" and isinstance(args.get("edits"), list):
        lines = [line for e in args["edits"] if isinstance(e, dict)
                 for line in ["⋯", *_snippet(str(e.get("old_string") or ""), str(e.get("new_string") or ""))]][1:]
    else:
        return {"tool": name}
    return {"tool": name, "diff": _clipped(lines)} if lines else {"tool": name}


def _patch_meta(changes: object) -> dict:
    """Codex's `fileChange`: an added or deleted file comes as its content, an update as a unified diff."""

    lines: list[str] = []
    for c in changes if isinstance(changes, list) else []:
        if not isinstance(c, dict):
            continue
        kind = c.get("kind")
        kind = kind.get("type") if isinstance(kind, dict) else kind
        body = str(c.get("diff") or "").splitlines()
        mark = {"add": "+", "delete": "-"}.get(str(kind))
        if mark is None:   # an update: its own `---`/`+++` header names the path again
            hunk = next((i for i, line in enumerate(body) if line.startswith("@@")), 0)
            body, mark = body[hunk:], ""
        lines += [f"▸ {c.get('path')}", *(mark + line for line in body)]
    return {"diff": _clipped(lines)} if any(not line.startswith("▸ ") for line in lines) else {}
