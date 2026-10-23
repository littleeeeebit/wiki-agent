"""The wiki indexes the start hook rebuilds, and a receipt of what it wrote.

`tool/sync.py` rebuilds `.wiki/corpus.json` and `.wiki/graph.json` at a
session's start whenever documents are newer than the listing. Where a
repository commits them, every document push left the checkout modified, and
every review in ai-coding-wiki began with a repair turn that only committed
them. The receipt, in the checkout's own Git directory, holds each file's
hash as the hook wrote it: a modification still matching it is the hook's, not
anybody's work. An edit after the hook, or another writer, no longer matches.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

INDEXES = (".wiki/corpus.json", ".wiki/graph.json")
RECEIPT = "wiki-indexes.json"


def _git_dir(repo: Path) -> Path | None:
    """This checkout's own Git directory; a linked worktree's `.git` is a file naming it."""
    dot = Path(repo) / ".git"
    if dot.is_dir():
        return dot
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    found = Path(line.removeprefix("gitdir:").strip())
    return found if found.is_absolute() else (Path(repo) / found).resolve()


def _hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def record(repo: Path) -> None:
    """After the hook wrote the indexes: their hashes as written."""
    where = _git_dir(repo)
    if where is None:
        return
    hashes = {name: digest for name in INDEXES if (digest := _hash(Path(repo) / name))}
    try:
        (where / RECEIPT).write_text(json.dumps(hashes), encoding="utf-8")
    except OSError:
        pass   # no receipt: the rebuilt files count as changes, as before


def rebuilt(repo: Path, name: str) -> bool:
    """`name` is an index the hook wrote and nobody changed since."""
    where = _git_dir(repo) if name in INDEXES else None
    try:
        hashes = json.loads((where / RECEIPT).read_text(encoding="utf-8")) if where else {}
    except (OSError, ValueError):
        return False
    return isinstance(hashes, dict) and bool(hashes.get(name)) and hashes.get(name) == _hash(Path(repo) / name)


def hook_row(repo: Path, line: str) -> bool:
    """A `git status --porcelain` row that is only the hook's rebuilt index, unstaged."""
    return line[:3] == " M " and rebuilt(repo, line[3:].strip('"'))
