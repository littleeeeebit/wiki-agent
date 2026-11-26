"""Cargo's incremental cache, kept warm and bounded.

With it, a rebuild after a fix compiles only what changed in a crate; without it (`CARGO_INCREMENTAL=0`, #366) every
build after a one-line change compiled the whole crate again, once per flag set an agent used. Cargo never prunes it,
and each flag set keeps its own: one worktree's `target` once reached 24 GB. So after each turn, while the turn still
holds its worktree, the least recently used caches go until the rest fit under `CAP`.

Only a build folder whose Cargo lock this takes is touched, so no Cargo build runs in it meanwhile, whoever started
it; a busy one still counts toward the bound. Nothing is deleted through a link or outside the worktree:

- On Windows a folder with a file open in it cannot be renamed or removed. The lock's open file pins every folder
  from the worktree down to the build folder, and only then is that path checked real. Each folder below is then
  removed under its own pin file, opened before it is checked and read. A link met anywhere goes as a link.
- Elsewhere an open file pins nothing, so each folder is opened by descriptor without following links, and
  `shutil.rmtree` removes the cache relative to its parent's descriptor.
"""

import os
import shutil
from contextlib import ExitStack
from pathlib import Path

from common import errorlog, is_junction

CAP = 6 * 2**30   # per worktree: a few flag sets of a large crate, warm
DEPTH = 8   # how deep a build folder is looked for: `apps/desktop/src-tauri/target/<triple>/debug` is five
SKIP = {".git", "node_modules"}   # never a Cargo build, and large
PAYLOAD = {"incremental", "deps", "build", ".fingerprint", "examples"}   # a build folder's own: never another build
PIN = ".pruning"   # rustc and Cargo read only their own entries in `incremental`


def linked(path) -> bool:
    return os.path.islink(path) or is_junction(path)


def folders(path) -> list[os.DirEntry]:
    """The real folders in `path`, never one through a link or a junction."""

    return [e for e in os.scandir(path) if e.is_dir(follow_symlinks=False) and not is_junction(e.path)]


def builds(tree: Path, depth: int = DEPTH):
    """Every Cargo build folder under `tree` (one profile's: `.cargo-lock` beside `incremental`), whatever its target
    folder is called, up to `DEPTH` folders down, reached through real folders only. One inside another is found too."""

    try:
        inside = folders(tree)
    except OSError:
        return
    found = any(e.name == "incremental" for e in inside) and os.path.isfile(tree / ".cargo-lock") \
        and not linked(tree / ".cargo-lock")
    if found:
        yield tree
    for e in inside:
        if depth and e.name not in SKIP and not (found and e.name in PAYLOAD):
            yield from builds(Path(e.path), depth - 1)


def real(tree: Path, path: Path) -> bool:
    """Every folder from `tree` down to `path` a real one, where `tree`'s own real path puts it."""

    parts = path.relative_to(tree).parts
    if any(linked(tree.joinpath(*parts[:n])) for n in range(1, len(parts) + 1)):
        return False
    return os.path.realpath(path) == os.path.join(os.path.realpath(tree), *parts)


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


def unlinked(path) -> None:
    """A link or junction removed as itself: what it points at is never touched."""

    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)   # a junction, or a link to a folder


def wiped(path: Path) -> None:
    """Windows: `path`, inside a pinned real folder, removed folder by folder, each under its own pin; what will not go
    (a file something holds) stays."""

    if linked(path):
        return unlinked(path)
    fd = os.open(path / PIN, os.O_RDWR | os.O_CREAT)   # from here `path` stays the folder it is now
    try:
        if linked(path):   # replaced just before the pin: the pin went through it, and nothing else does
            return
        for e in os.scandir(path):
            if e.name == PIN:
                continue
            try:
                if linked(e.path):
                    unlinked(e.path)
                elif e.is_dir(follow_symlinks=False):
                    wiped(Path(e.path))
                else:
                    os.unlink(e.path)
            except OSError:
                pass
    finally:
        os.close(fd)
        unpin(path / PIN)
    try:
        os.rmdir(path)
    except OSError:
        pass


def removed(tree: Path, cache: Path) -> None:
    """`cache` deleted without following a link on the way to it or inside it, nor reaching outside `tree`."""

    if os.name == "nt":
        return wiped(cache)
    parts = cache.relative_to(tree).parts
    fd = os.open(tree, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            at = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = at
        shutil.rmtree(parts[-1], ignore_errors=True, dir_fd=fd)
    finally:
        os.close(fd)


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
        sized, busy, partial = [], 0, False
        for build in builds(tree):
            try:
                mine = held(stack, build)   # on Windows its open lock file pins the path checked next
                if not real(tree, build):   # a folder on the way replaced since it was found: not this worktree's
                    continue
                caches = pinned(stack, build) if mine else [Path(e.path) for e in folders(build / "incremental")]
            except OSError as exc:
                errorlog.record("build-cache", exc, path=str(build))
                partial = True
                continue
            for cache in caches:
                try:
                    if mine:
                        sized.append((used(cache), size(cache), cache))
                    else:   # a build is using it: left whole, but it fills the same disk
                        busy += size(cache)
                except OSError as exc:
                    errorlog.record("build-cache", exc, path=str(cache))
                    partial = True
        total, freed = busy + sum(s for _, s, _ in sized), 0
        for _, bytes_, cache in sorted(sized):   # oldest first
            if total <= cap:
                break
            try:
                removed(tree, cache)
            except OSError as exc:
                errorlog.record("build-cache", exc, path=str(cache))
            gone = bytes_ - left(cache, bytes_)   # what was really freed: one that would not go leaves the next its turn
            total, freed = total - gone, freed + gone
        if total > cap or partial:
            errorlog.record("build-cache", OSError(f"{total} bytes counted over {cap}" + (", some not read" if partial
                                                   else "")), path=str(tree))
    return freed
