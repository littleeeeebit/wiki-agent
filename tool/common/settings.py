"""Settings files — `KEY=value` lines, read and never executed.

The one reader `translate` and `decision` share, so a key registered in the
hub's `.env` means the same thing to both. The format is deliberately smaller
than dotenv's:

- A line is `KEY=value`. Whitespace around the key and the value is dropped.
- A line that is blank or starts with `#` is a comment. So is a line without
  `=`. `export KEY=value` is not special: its key is `export KEY`.
- A value wrapped in matching single or double quotes is the text between
  them, verbatim — `#` inside quotes is text. Unquoted, a `#` after
  whitespace starts a comment.
- No escapes, no `${VAR}` substitution, no shell: `$(...)` is text.
- The first entry for a key wins; later duplicates are ignored.
- A present entry wins even when its value is empty. Falling through to the
  environment there would answer a half-filled file with a machine-wide key.

A missing file is no entries. An unreadable one raises, so a caller can tell
"not configured" from "could not read the configuration".

`saved` writes the app's own JSON settings file, whose writers share a lock.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

# The hub checkout this code runs from; its `.env` is the default file.
HUB = Path(__file__).resolve().parents[2]

COMMENT = re.compile(r"\s#")


def entries(path: Path) -> dict[str, str]:
    """Every entry in `path`; `{}` when it does not exist.

    Raises `OSError` or `ValueError` (a decode error) when it exists and
    cannot be read. A byte-order mark is tolerated: Notepad writes one.
    """

    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and key not in found:
            found[key] = unquote(value.strip())
    return found


_saving = threading.Lock()


def saved(path: Path, changes: dict) -> None:
    """`changes` merged into the JSON settings file at `path`, read to replace
    under one lock. The loop's settings and the translation switch share
    `raw/chat/main.json`, and a desktop and a phone can write it at once:
    unlocked, one writer's value was lost or its replace found no temp file."""

    with _saving:
        try:
            old = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(path).with_suffix(".tmp")
        temporary.write_text(json.dumps({**(old if isinstance(old, dict) else {}), **changes}, ensure_ascii=False)
                             + "\n", encoding="utf-8")
        temporary.replace(path)


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return COMMENT.split(value, maxsplit=1)[0].rstrip()


def pick(found: dict[str, str], name: str, default: str | None = None) -> tuple[str | None, str]:
    """`(value, source)`: the file's entry, else the environment, else `default`.

    `source` is `file`, `environment` or `default` — a category that is safe
    to show, never the value.
    """

    if name in found:
        return found[name], "file"
    value = os.environ.get(name)
    if value is not None:
        return value.strip(), "environment"
    return default, "default"
