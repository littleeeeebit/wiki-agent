"""`.wiki/adapter.toml` as its writers make it: `[slots]` written, every other table kept as it was."""

from __future__ import annotations

import json
import tomllib


def adapter_text(slots: dict[str, str], old: str = "") -> str:
    """The adapter with `slots` as its `[slots]`. Given the file it replaces (`old`), only that table's lines
    change: every other table, `[release]` among them, stays as it was written. Refused when it would not."""

    # A JSON string is a TOML basic string, escapes and all.
    rows = [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in slots.items()]
    if not old.strip():
        return "\n".join(['agents = ["claude", "codex"]', "", "[slots]", *rows]) + "\n"
    lines, nl = old.splitlines(), "\r\n" if "\r\n" in old else "\n"
    start = next((i for i, line in enumerate(lines) if line.strip() == "[slots]"), len(lines))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    # Only the table's assignments go. Its comments and blank lines stay, after the new rows, so a comment that
    # heads the next table stays above it; the file keeps its line endings and its last newline, or none.
    kept = [line for line in lines[start + 1:end] if not line.strip() or line.lstrip().startswith("#")]
    head = lines[:start] + ([""] if start == len(lines) and lines and lines[-1].strip() else [])
    gap = [""] if end < len(lines) and not kept else []
    text = nl.join(head + ["[slots]", *rows, *kept, *gap, *lines[end:]]) + (nl if old.endswith("\n") else "")
    before, after = tomllib.loads(old), tomllib.loads(text)
    if after.pop("slots", None) != slots or {k: v for k, v in before.items() if k != "slots"} != after:
        raise ValueError("`.wiki/adapter.toml` 의 `[slots]` 만 바꾸지 못했다 — 다른 표가 함께 바뀐다")
    return text
