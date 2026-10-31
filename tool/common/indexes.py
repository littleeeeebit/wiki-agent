"""The wiki indexes the start hook rebuilds, and a receipt of what it wrote.

`tool/sync.py` rebuilds `.wiki/corpus.json` and `.wiki/graph.json` at a
session's start whenever documents are newer than the listing. Where a
repository commits them, every document push left the checkout modified, and
every review in ai-coding-wiki began with a repair turn that only committed
them. The receipt, in the checkout's own Git directory, holds the HEAD it was
written at and the hash of the exact bytes the hook wrote — never a file read
back later, which another writer may have changed meanwhile. A modification
matching it at that HEAD, or at a later one no document changed in, is the
hook's, not anybody's work; an edit after the hook, another writer or a
document commit no longer matches. Nothing here writes an index.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

INDEXES = (".wiki/corpus.json", ".wiki/graph.json")
RECEIPT = "wiki-indexes.json"


def git_dir(repo: Path) -> Path | None:
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


def head(repo: Path) -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.stdout.strip() if not done.returncode else ""


def _receipt(where: Path | None) -> dict:
    try:
        found = json.loads((where / RECEIPT).read_text(encoding="utf-8")) if where else {}
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) and isinstance(found.get("files"), dict) else {}


def record(repo: Path, written: dict[str, str], at: str) -> None:
    """After the hook wrote `written` (name -> the text it passed to
    `write_text`) at HEAD `at`, read before writing: the hashes of those bytes,
    with the platform's line ends as text mode wrote them."""
    where = git_dir(repo)
    if where is None or not at:
        return
    kept = _receipt(where)
    files = dict(kept["files"]) if kept.get("head") == at else {}
    files.update({name: hashlib.sha256(text.replace("\n", os.linesep).encode("utf-8")).hexdigest()
                  for name, text in written.items() if name in INDEXES})
    try:
        (where / RECEIPT).write_text(json.dumps({"head": at, "files": files}), encoding="utf-8")
    except OSError:
        pass   # no receipt: the rebuilt files count as changes, as before


def _same_sources(repo: Path, at: str, now: str) -> bool:
    """No commit between `at` and `now` touched a document — what both indexes
    are built from — an ignore rule, which picks the graph's decisions, or an
    index. A code-only review repair moves HEAD and nothing the hook read, so
    its output stays its own; a document commit between makes an old output a
    restore somebody chose. Case is ignored: Windows collects `GUIDE.MD` as a
    document. A tree with a submodule or a symbolic link is never compared:
    either can carry documents in from a path no diff here names."""
    if _links(repo, at) or _links(repo, now):
        return False
    done = subprocess.run(["git", "diff", "--name-only", "-z", "--no-renames", at, now],
                          cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return not done.returncode and not any(
        path in INDEXES or path.lower().endswith((".md", ".gitignore")) for path in done.stdout.split("\0"))


def _links(repo: Path, commit: str) -> bool:
    """`commit`'s tree holds a submodule or a symbolic link, or cannot be read."""
    done = subprocess.run(["git", "ls-tree", "-r", "-z", commit],
                          cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return bool(done.returncode) or any(entry.startswith(("120000 ", "160000 ")) for entry in done.stdout.split("\0"))


def rebuilt(repo: Path, name: str) -> bool:
    """`name` is an index the hook wrote, nobody changed since, at this HEAD or
    at one whose documents are this HEAD's."""
    found = _receipt(git_dir(repo)) if name in INDEXES else {}
    digest = found.get("files", {}).get(name)
    if not digest or digest != _hash(Path(repo) / name):
        return False
    at, now = found.get("head") or "", head(repo)
    return bool(at and now) and (at == now or _same_sources(repo, at, now))


def hook_row(repo: Path, line: str) -> bool:
    """A `git status --porcelain` row (v1, or v2 with `-z`) that is only the
    hook's rebuilt index, modified in the worktree and not staged."""
    if line[:3] == " M ":
        return rebuilt(repo, line[3:].strip('"'))
    fields = line.split(" ", 8)
    return line.startswith("1 .M ") and len(fields) == 9 and rebuilt(repo, fields[8])
