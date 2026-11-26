"""Cargo's incremental cache, kept warm and bounded.

With it, a rebuild after a fix compiles only what changed in a crate; without it (`CARGO_INCREMENTAL=0`, #366) every
build after a one-line change compiled the whole crate again, once per flag set an agent used. Cargo never prunes it,
and each flag set keeps its own: one worktree's `target` once reached 24 GB. So after each turn, while the turn still
holds its worktree, the least recently used caches go until the rest fit under `CAP`.

Only a build folder whose Cargo lock this takes is touched, so no Cargo build runs in it meanwhile, whoever started
it. On Windows that lock's open file, and a pin file in `incremental`, keep every folder above a cache from being
renamed or replaced while it goes. The cache folder itself is the one name left unpinned, and `shutil.rmtree` refuses
it if it is a link when it starts. Off Windows an open file pins nothing; there `rmtree` works through file
descriptors instead.
"""

import os
import shutil
from contextlib import ExitStack
from pathlib import Path

from common import errorlog, is_junction

CAP = 6 * 2**30   # per worktree: a few flag sets of a large crate, warm
DEPTH = 6   # how deep a build folder is looked for: `apps/desktop/src-tauri/target/<triple>/debug` is five
SKIP = {".git", "node_modules"}   # never a Cargo build, and large
PIN = ".pruning"   # rustc and Cargo read only their own entries in `incremental`


def linked(path) -> bool:
    return os.path.islink(path) or is_junction(path)


def folders(path) -> list[os.DirEntry]:
    """The real folders in `path`, never one through a link or a junction."""

    return [e for e in os.scandir(path) if e.is_dir(follow_symlinks=False) and not is_junction(e.path)]


def builds(tree: Path, depth: int = DEPTH):
    """Every Cargo build folder under `tree` (one profile's: `.cargo-lock` beside `incremental`), whatever its target
    folder is called and however deep, reached through real folders only."""

    try:
        inside = folders(tree)
    except OSError:
        return
    if any(e.name == "incremental" for e in inside) and os.path.isfile(tree / ".cargo-lock") \
            and not linked(tree / ".cargo-lock"):
        yield tree
        return
    for e in inside:
        if depth and e.name not in SKIP:
            yield from builds(Path(e.path), depth - 1)


def lock(fd: int, take: bool) -> None:
    """Cargo's own lock on a build folder: exclusive, and refused at once while a build holds it."""

    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)   # Cargo locks the whole file; any byte of it conflicts
        msvcrt.locking(fd, msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB if take else fcntl.LOCK_UN)


def held(stack: ExitStack, build: Path) -> bool:
    """`build`'s Cargo lock taken until `stack` closes; False while a build holds it."""

    fd = os.open(build / ".cargo-lock", os.O_RDWR)
    stack.callback(os.close, fd)
    try:
        lock(fd, True)
    except OSError:
        return False
    stack.callback(lock, fd, False)
    return True


def pinned(stack: ExitStack, build: Path) -> list[Path]:
    """The caches in `build`'s real `incremental`, kept where it is by an open pin file until `stack` closes."""

    inc = build / "incremental"
    if linked(inc):
        return []
    fd = os.open(inc / PIN, os.O_RDWR | os.O_CREAT)
    stack.callback(unpin, inc / PIN)   # after the close below
    stack.callback(os.close, fd)
    if linked(inc) or os.path.realpath(inc) != os.path.join(os.path.realpath(build), "incremental"):
        return []   # replaced before the pin held it
    return [Path(e.path) for e in folders(inc)]


def unpin(pin: Path) -> None:
    try:
        os.remove(pin)
    except OSError as exc:   # left behind, it is taken again next time; Cargo and rustc never read it
        if not isinstance(exc, FileNotFoundError):
            errorlog.record("build-cache", exc, path=str(pin))


def size(path) -> int:
    """Bytes under `path`, never counted through a link or a junction; 0 once it is gone."""

    try:
        entries = list(os.scandir(path))
    except FileNotFoundError:
        return 0
    return sum(size(e.path) if e.is_dir(follow_symlinks=False) and not is_junction(e.path)
               else e.stat(follow_symlinks=False).st_size if e.is_file(follow_symlinks=False) else 0 for e in entries)


def used(cache: Path) -> float:
    """When rustc last wrote a session into `cache`: it writes a new one each time it compiles that crate."""

    return max((e.stat(follow_symlinks=False).st_mtime for e in os.scandir(cache)),
               default=os.stat(cache, follow_symlinks=False).st_mtime)


def pruned(tree: Path, cap: int = CAP) -> int:
    """The least recently used incremental caches under `tree` removed until the rest fit in `cap` bytes; the bytes
    freed. A build folder a build holds is left whole, and so is a cache that cannot be read, for the next turn's end.
    Never raises: it runs as a turn ends."""

    freed = 0
    try:
        freed = trimmed(tree, cap)
    except Exception as exc:
        errorlog.record("build-cache", exc, path=str(tree))
    return freed


def left(cache: Path, before: int) -> int:
    try:
        return size(cache)
    except OSError:   # unreadable after a partial removal: counted as all still there
        return before


def trimmed(tree: Path, cap: int) -> int:
    with ExitStack() as stack:
        sized = []
        for build in builds(tree):
            try:
                caches = pinned(stack, build) if held(stack, build) else []
            except OSError as exc:
                errorlog.record("build-cache", exc, path=str(build))
                continue
            for cache in caches:
                try:
                    sized.append((used(cache), size(cache), cache))
                except OSError as exc:
                    errorlog.record("build-cache", exc, path=str(cache))
        total, freed = sum(s for _, s, _ in sized), 0
        for _, bytes_, cache in sorted(sized):   # oldest first
            if total <= cap:
                break
            shutil.rmtree(cache, ignore_errors=True)   # a link refused; a file something holds stays
            gone = bytes_ - left(cache, bytes_)   # what was really freed: one that would not go leaves the next its turn
            total, freed = total - gone, freed + gone
        if total > cap:
            errorlog.record("build-cache", OSError(f"{total} bytes left over {cap}"), path=str(tree))
    return freed
