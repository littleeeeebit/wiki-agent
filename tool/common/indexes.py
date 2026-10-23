"""The wiki indexes the start hook rebuilds, and a receipt of what it wrote.

`tool/sync.py` rebuilds `.wiki/corpus.json` and `.wiki/graph.json` at a
session's start whenever documents are newer than the listing. Where a
repository commits them, every document push left the checkout modified, and
every review in ai-coding-wiki began with a repair turn that only committed
them. The receipt, in the checkout's own Git directory, holds the HEAD it was
written at and the hash of the exact bytes the hook wrote — never a file read
back later, which another writer may have changed meanwhile. A modification
matching it at that HEAD is the hook's, not anybody's work; an edit after the
hook, another writer or another commit no longer matches.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
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


def head(repo: Path) -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
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
    where = _git_dir(repo)
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


def rebuilt(repo: Path, name: str) -> bool:
    """`name` is an index the hook wrote at this HEAD and nobody changed since."""
    found = _receipt(_git_dir(repo)) if name in INDEXES else {}
    digest = found.get("files", {}).get(name)
    return bool(digest) and digest == _hash(Path(repo) / name) and found.get("head") == head(repo)


def carried(repo: Path) -> bool:
    """The hook's last output, untouched and still modified, under a receipt
    from an earlier commit. A code-only commit moves HEAD without a newer
    document, so nothing else would make the hook write — and record — again.
    An edit no longer matches its hash and is left alone."""
    found = _receipt(_git_dir(repo))
    files = {name: digest for name, digest in found.get("files", {}).items() if name in INDEXES}
    now = head(repo)
    if not files or not now or found.get("head") == now:
        return False
    if any(digest != _hash(Path(repo) / name) for name, digest in files.items()):
        return False
    return subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *files], cwd=repo).returncode == 1


def hook_row(repo: Path, line: str) -> bool:
    """A `git status --porcelain` row (v1, or v2 with `-z`) that is only the
    hook's rebuilt index, modified in the worktree and not staged."""
    if line[:3] == " M ":
        return rebuilt(repo, line[3:].strip('"'))
    fields = line.split(" ", 8)
    return line.startswith("1 .M ") and len(fields) == 9 and rebuilt(repo, fields[8])
