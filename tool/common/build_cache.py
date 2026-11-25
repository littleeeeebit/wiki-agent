"""Cargo's incremental cache, kept warm and bounded.

With it, a rebuild after a fix compiles only what changed in a crate; without it (`CARGO_INCREMENTAL=0`, #366) every
build after a one-line change compiled the whole crate again, once per flag set an agent used. Cargo never prunes it,
and each flag set keeps its own: one worktree's `target` once reached 24 GB. So after each turn, while the turn still
holds its worktree, the least recently used caches go until the rest fit under `CAP`.
"""

import shutil
from pathlib import Path

from common import errorlog

CAP = 6 * 2**30   # per worktree: a few flag sets of a large crate, warm
# Where Cargo puts a worktree's build, the Tauri app's own folder included, by profile and by target triple.
TARGETS = ("target/*/incremental", "target/*/*/incremental", "*/target/*/incremental", "*/target/*/*/incremental")


def size(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file() and not f.is_symlink())


def used(cache: Path) -> float:
    """When rustc last wrote a session into `cache`: it writes a new one each time it compiles that crate."""

    return max((e.stat().st_mtime for e in cache.iterdir()), default=cache.stat().st_mtime)


def pruned(tree: Path, cap: int = CAP) -> int:
    """The least recently used incremental caches under `tree` removed until the rest fit in `cap` bytes; the bytes
    freed. A link or junction is never followed or removed: what it points at is not this worktree's."""

    try:
        home = tree.resolve()   # a link anywhere on the way resolves outside it
        caches = [c for pattern in TARGETS for inc in tree.glob(pattern) for c in inc.iterdir()
                  if c.is_dir() and c.resolve() == home / c.relative_to(tree)]
        sized = sorted(((used(c), size(c), c) for c in caches), reverse=True)   # newest first
    except OSError as exc:   # a cache a build is writing right now: left whole, for the next turn's end
        errorlog.record("build-cache", exc, path=str(tree))
        return 0
    total, freed = sum(s for _, s, _ in sized), 0
    for _, bytes_, cache in reversed(sized):   # oldest first
        if total - freed <= cap:
            break
        shutil.rmtree(cache, ignore_errors=True)   # a file a running build still holds stays, for the next turn
        freed += bytes_   # counted either way: one that would not go never costs a newer cache its place
    return freed

