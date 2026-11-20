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
    # Every line keeps its own ending; the new ones take the file's first.
    lines = old.splitlines(keepends=True)
    bare = [line.rstrip("\r\n") for line in lines]
    nl = next((line[len(b):] for line, b in zip(lines, bare) if line != b), "\n")
    start = next((i for i, b in enumerate(bare) if b.strip() == "[slots]"), len(lines))
    end = next((i for i in range(start + 1, len(lines)) if bare[i].lstrip().startswith("[")), len(lines))
    # Only the table's assignments go. Its comments and blank lines stay, after the new rows, so a comment that
    # heads the next table stays above it.
    kept = [lines[i] for i in range(start + 1, end) if not bare[i].strip() or bare[i].lstrip().startswith("#")]
    head = lines[:start]
    if start == len(lines) and head:   # a file with no `[slots]` gets one at its end, a blank line before it
        head[-1] += "" if head[-1] != bare[-1] else nl
        head += [nl] if bare[-1].strip() else []
    text = "".join(head + [f"[slots]{nl}", *(row + nl for row in rows), *kept, *lines[end:]])
    if end == len(lines) and not old.endswith("\n") and text.endswith(nl):   # no last newline before, none after
        text = text[:-len(nl)]
    before, after = tomllib.loads(old), tomllib.loads(text)
    if after.pop("slots", None) != slots or {k: v for k, v in before.items() if k != "slots"} != after:
        raise ValueError("`.wiki/adapter.toml` 의 `[slots]` 만 바꾸지 못했다 — 다른 표가 함께 바뀐다")
    return text
