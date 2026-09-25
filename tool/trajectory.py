"""trajectory — what was carried into which utterance, and whether that turn
was corrected."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from census import DEFAULT_MARKERS, Markers  # noqa: E402

KEEP = 4000         # How much of an utterance to keep. `inject.MAX_RENDERED`:
                    # `trigger_audit replay` and `ab` read the utterance back,
                    # and rows before 2026-09-25 were cut at 500
RESUME_MAX = 120    # The census's own value: `이어서` inside a long
                    # instruction is not a request to resume
TAIL = 8192         # How much of the tail to read to find the last line
FILENAME = "trajectory.jsonl"


def path_for(wiki: Path) -> Path:
    return wiki / FILENAME


def hush(wiki: Path) -> None:
    """`.wiki/` is committed and this file changes every turn.

    What gets committed is the measurement taken from here, not the stream.
    `.wiki/.sync` is already ignored for the same reason; that one is held by
    the repository's own `.gitignore`, while this file appears in every target
    repository and so adds itself.
    """

    ignore = wiki / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if FILENAME in lines:
        return
    # The line ending is not left to the environment. Written with the Windows
    # default, the lines already there flip to CRLF too, and adding one line
    # shows up as a diff of the whole file.
    ignore.write_text("\n".join([*lines, FILENAME]) + "\n", encoding="utf-8", newline="\n")


def last_row(path: Path) -> dict | None:
    """The last line. The whole file is not read — this is a hook on every turn.

    Reading only the tail can cut the first line in half. Scanning backwards
    and taking the first line that parses steps over that fragment on its own.
    """

    if not path.exists():
        return None
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - TAIL))
        chunk = handle.read().decode("utf-8", errors="replace")
    for line in reversed(chunk.splitlines()):
        if not line.strip():
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def verdict(prompt: str, markers: Markers) -> str:
    """What this utterance says about the turn before it.

    Only `resume` looks at length. `이어서` inside a long instruction is not a
    request to resume, it is just an instruction, and census already drew that
    line at 120 characters. The same value is used here.

    A marker, not a verdict. Which of these is a false positive is for a
    person to see — scoring it here would be exactly the automatic judgement
    that makes a census untrustworthy.
    """

    if any(re.search(pattern, prompt) for pattern in markers.correction):
        return "교정"
    if len(prompt) < RESUME_MAX and any(
        re.search(pattern, prompt) for pattern in markers.resume
    ):
        return "재개요구"
    if any(re.search(pattern, prompt) for pattern in markers.partial):
        return "부분수행"
    return "ok"


def record(
    wiki: Path | None,
    prompt: str,
    injected: list[str],
    cost: int,
    session: str,
    **extra: object,
) -> str | None:
    """Record one turn, and score the previous line when it is the same session.

    `extra` lands in the row as given — `sent` and `full` from `inject`. What
    each one means is written where it is made.

    Every failure is swallowed: one record is not worth stopping a session
    over — `craft/hooks-fail-open`. It is not swallowed silently, though. The
    exception's name comes back, and whether it reaches a screen is decided by
    whoever owns the stream. A library writing straight to someone else's
    stderr cannot know who pinned that stream's encoding.
    """

    if wiki is None:
        return None
    try:
        wiki.mkdir(parents=True, exist_ok=True)
        hush(wiki)
        path = path_for(wiki)
        row: dict[str, object] = {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "session": session,
            "utterance": prompt[:KEEP],
            "chars": len(prompt),
            "injected": injected,
            "cost": cost,
            **extra,
        }
        previous = last_row(path)
        if session and previous and previous.get("session") == session:
            row["prev"] = verdict(prompt, Markers.load(DEFAULT_MARKERS))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as error:  # noqa: BLE001
        return type(error).__name__
    return None


def rows(wiki: Path) -> list[dict]:
    """The reading side. Defined in two places, this format drifts in two places."""

    return read(path_for(wiki))


def read(path: Path) -> list[dict]:
    """`rows` for a file named directly — `trigger_audit` takes paths."""

    if not path.exists():
        return []
    found = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found
