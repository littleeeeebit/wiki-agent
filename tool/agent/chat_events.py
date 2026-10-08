"""Screen events and provider message rendering, without process ownership."""

from __future__ import annotations

import json
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
