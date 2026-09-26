"""EvidenceChunk — one span of an original source, with its English form.

Stage 2 of `docs/plans/jev/`. The original is what a citation resolves to;
the English text is what Jev reads. Neither replaces the other, and English
that failed to come about is `None` with a status saying why, never the
original passed off as English.

IDs are content-derived. A source is its repository and its display path; a
chunk is its source, the source's revision, its line span and `CHUNKER`. The
same sentence in two sources is two pieces of evidence, and an edited source
retires every chunk id it had.

Locators come in three shapes, told apart by their keys: a file's line span,
a URL snapshot's character span, and a PDF's page and block. A location that
is not known is refused, never filled in with line 1.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

SCHEMA_VERSION = 1
# Part of every chunk id: how a source is cut. Changing the cut changes it,
# and the store builds a new generation (`daemon.Store`).
CHUNKER = "chunks/1"

KINDS = ("rule", "document", "decision", "memory", "research", "paper")
VISIBILITY = ("shared", "repository", "private")
COMPLETENESS = ("whole", "partial", "oversized")
LANGUAGES = ("en", "ko", "und")
# `pending`: not normalized yet. The last four carry no English.
STATUSES = ("original_english", "translated", "pending", "unavailable", "uncertain", "retired")
USABLE = ("original_english", "translated")

ID = re.compile(r"\A[0-9a-f]{64}\Z")


def digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def repo_id(root: Path) -> str:
    """A repository is its canonical checkout path, not its name: two clones
    called the same are two repositories."""

    return digest("repo", os.path.normcase(str(Path(root).resolve())))


def source_id(repo: str, display: str) -> str:
    return digest("source", repo, display)


def chunk_id(source: str, revision: str, start: int, end: int) -> str:
    return digest("chunk", source, revision, str(start), str(end), CHUNKER)


def kind_of(display: str, shared: bool) -> str:
    if shared:
        return "rule"
    if display.startswith(".wiki/memory/"):
        return "memory"
    if display.startswith(".wiki/decisions/"):
        return "decision"
    if display.startswith("docs/research/"):
        return "research"
    return "document"


def visibility_of(kind: str) -> str:
    return {"rule": "shared", "memory": "private"}.get(kind, "repository")


def locator_problem(locator: object) -> str | None:
    """Why `locator` is not one of the three shapes, or `None`."""

    if not isinstance(locator, dict):
        return "locator is not an object"

    def whole(*names: str, least: int = 0) -> bool:
        return all(type(locator.get(n)) is int and locator[n] >= least for n in names)

    keys = set(locator)
    if keys == {"path", "start_line", "end_line"}:
        ok = isinstance(locator["path"], str) and locator["path"] and whole("start_line", "end_line", least=1)
        return None if ok and locator["start_line"] <= locator["end_line"] else "file locator needs lines from 1"
    if keys == {"url", "snapshot", "start", "end"}:
        ok = (isinstance(locator["url"], str) and locator["url"].startswith(("https://", "http://"))
              and isinstance(locator["snapshot"], str) and ID.match(locator["snapshot"]) and whole("start", "end"))
        return None if ok and locator["start"] < locator["end"] else "url locator needs a snapshot hash and a span"
    if keys == {"document", "page", "block"}:
        ok = isinstance(locator["document"], str) and locator["document"] and whole("page", least=1) and whole("block")
        return None if ok else "pdf locator needs a document, a page from 1 and a block"
    return f"unknown locator shape {sorted(keys)}"


def problems(chunk: dict) -> list[str]:
    """Everything that makes `chunk` not an EvidenceChunk. Empty means valid."""

    found = []
    if chunk.get("schema_version") != SCHEMA_VERSION:
        found.append("schema_version")
    for name in ("repo_id", "source_id", "revision", "chunk_id"):
        if not isinstance(chunk.get(name), str) or not ID.match(chunk[name]):
            found.append(f"{name} is not a sha256")
    for name, allowed in (("kind", KINDS), ("visibility", VISIBILITY), ("completeness", COMPLETENESS),
                          ("language", LANGUAGES)):
        if chunk.get(name) not in allowed:
            found.append(f"{name} {chunk.get(name)!r}")
    if (problem := locator_problem(chunk.get("locator"))) is not None:
        found.append(problem)
    path = chunk.get("heading_path")
    if not isinstance(path, list) or not all(isinstance(h, str) for h in path):
        found.append("heading_path")
    if not isinstance(chunk.get("original_text"), str) or not chunk["original_text"].strip():
        found.append("original_text is empty")
    translation = chunk.get("translation")
    status = translation.get("status") if isinstance(translation, dict) else None
    if status not in STATUSES:
        found.append(f"translation status {status!r}")
    english = chunk.get("text_en")
    if status in USABLE and not (isinstance(english, str) and english.strip()):
        found.append(f"{status} without English text")
    if status not in USABLE and english is not None:
        # English that did not come about must not look as if it had.
        found.append(f"{status} with English text")
    return found


def contract(hit: dict, outcome: dict | None = None) -> dict:
    """The EvidenceChunk for a search hit, with `outcome` from English
    normalization (`translate.english`'s shape), or `pending` without one.

    Raises `ValueError` when the result is not valid, so an ill-formed chunk
    never travels as evidence.
    """

    status = (outcome or {}).get("status", "pending")
    usable = status in USABLE
    chunk = {
        "schema_version": SCHEMA_VERSION,
        **{name: hit[name] for name in ("repo_id", "source_id", "revision", "chunk_id", "kind",
                                        "locator", "heading_path", "visibility", "completeness")},
        "original_text": hit["text"],
        "text_en": outcome["text"] if usable else None,
        "language": (outcome or {}).get("language") or hit.get("language") or "und",
        "translation": {"status": status, "version": (outcome or {}).get("version") if usable else None,
                        **({"reason": outcome["reason"]} if outcome and outcome.get("reason") else {})},
    }
    wrong = problems(chunk)
    if wrong:
        raise ValueError(f"not an EvidenceChunk: {'; '.join(wrong)}")
    return chunk


def span(text: str, start: int, end: int) -> str:
    """Lines `start` to `end` of `text`, counted from 1 as the locator counts."""

    return "\n".join(text.splitlines()[start - 1:end])


def resolve(chunk: dict, path: Path) -> str | None:
    """The original span a chunk cites, read again from `path` — or `None`
    when the file is gone or no longer the revision the chunk was cut from.
    A stale chunk resolves to nothing rather than to whatever is there now."""

    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != chunk["revision"]:
        return None
    locator = chunk["locator"]
    return span(data.decode("utf-8"), locator["start_line"], locator["end_line"])
