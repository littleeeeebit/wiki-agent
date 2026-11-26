"""Cargo's incremental cache, kept warm and bounded.

With it, a rebuild after a fix compiles only what changed in a crate; without it (`CARGO_INCREMENTAL=0`, #366) every
build after a one-line change compiled the whole crate again, once per flag set an agent used. Cargo never prunes it,
and each flag set keeps its own: one worktree's `target` once reached 24 GB. So after each turn, while the turn still
holds its worktree, the least recently used caches go until the rest fit under `CAP`.

Only a build folder whose Cargo lock this takes is touched, so no Cargo build runs in it meanwhile, whoever started
it. A busy one is not even read, since a pin in a running build's session folder would block rustc; the count is then
logged as partial. Nothing is deleted or read through a link or outside the worktree, because on Windows a folder
with a file open in it cannot be renamed or removed:

- The lock's open file pins every folder from the worktree root down to the build folder. Only then is that path
  checked: the same root as when the trim began, and a real folder at every step.
- Each folder below is read, and removed, under its own pin: a file created there, never one already there, and
  deleted by the system when its handle closes, so no pin is ever removed by name. The folder is checked again once
  its pin holds, and a link met anywhere goes as a link. A cache whose name no longer holds the folder measured is
  credited nothing.

Elsewhere an open file pins nothing, and the oneshot builds on Windows, so nothing is trimmed there.
"""

import os
import stat
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


def builds(tree: Path, unread: list, depth: int = DEPTH):
    """Every Cargo build folder under `tree` (one profile's: `.cargo-lock` beside `incremental`), whatever its target
    folder is called, up to `DEPTH` folders down, reached through real folders only. One inside another is found too.
    A folder that cannot be read goes into `unread`. Names only, nothing pinned: a pin changes its folder's time, and a
    build script may watch a source folder's; each name is checked under its own lock's pin before any use."""

    try:
        inside = folders(tree)
    except OSError as exc:
        unread.append(exc)
        return
    found = any(e.name == "incremental" for e in inside) and os.path.isfile(tree / ".cargo-lock") \
        and not linked(tree / ".cargo-lock")
    if found:
        yield tree
    for e in inside:
        if depth and e.name not in SKIP and not (found and e.name in PAYLOAD):
            yield from builds(Path(e.path), unread, depth - 1)


def real(tree: Path, root: os.stat_result, path: Path) -> bool:
    """`tree` still the root the trim began on, and every folder from it down to `path` a real one, where it puts it."""

    parts = path.relative_to(tree).parts
    if linked(tree) or not os.path.samestat(os.stat(tree), root):
        return False
    if any(linked(tree.joinpath(*parts[:n])) for n in range(1, len(parts) + 1)):
        return False
    return os.path.realpath(path) == os.path.join(os.path.realpath(tree), *parts)


def pin(folder: Path) -> int:
    """A file of our own in `folder`, kept open: the folder stays where it is until it closes, and the system deletes
    the file then. Refused if the name is taken, so a file already there is never ours to remove."""

    return os.open(os.path.join(folder, PIN), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_TEMPORARY)


def lock(fd: int, take: bool) -> None:
    """Cargo's own lock on a build folder: exclusive, and refused at once while a build holds it."""

    import msvcrt
    os.lseek(fd, 0, os.SEEK_SET)   # Cargo locks the whole file; any byte of it conflicts
    msvcrt.locking(fd, msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)


def held(stack: ExitStack, build: Path) -> bool:
    """`build`'s Cargo lock taken until `stack` closes; False while a build holds it."""

    fd = os.open(build / ".cargo-lock", os.O_RDWR)
    stack.callback(os.close, fd)
    entry = os.lstat(build / ".cargo-lock")   # open, the file cannot be renamed or removed: this stays its entry
    if not stat.S_ISREG(entry.st_mode) or not os.path.samestat(os.fstat(fd), entry):
        raise OSError(f"{build / '.cargo-lock'} is not the build folder's own lock file")
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
    stack.callback(os.close, pin(inc))
    if linked(inc) or os.path.realpath(inc) != os.path.join(os.path.realpath(build), "incremental"):
        return []   # replaced before the pin held it: only the pin went through, and goes with its handle
    return [Path(e.path) for e in folders(inc)]


def unlinked(path) -> None:
    """A link or junction removed as itself: what it points at is never touched."""

    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)   # a junction, or a link to a folder


def wiped(path: Path, ident: os.stat_result | None = None) -> bool:
    """`path`, inside a pinned real folder, removed folder by folder, each under its own pin; what will not go (a file
    something holds, a folder whose pin name is taken) stays. False when `path` was no longer that real folder, the
    one `ident` names when given: a link in its place goes as a link, another folder there is left, and a missing name
    goes nowhere. Then nothing of it was freed."""

    if linked(path):
        unlinked(path)
        return False
    try:
        fd = pin(path)   # from here `path` stays the folder it is now
    except FileNotFoundError:
        return False
    try:
        if linked(path):   # replaced just before the pin: only the pin went through, and goes with its handle
            return False
        if ident is not None and not os.path.samestat(os.stat(path), ident):   # another folder took its name
            return False
        for e in os.scandir(path):
            if e.name == PIN:
                continue
            try:
                if linked(e.path):
                    unlinked(e.path)
                elif e.is_dir(follow_symlinks=False):
                    wiped(Path(e.path), None)
                else:
                    os.unlink(e.path)
            except OSError:
                pass
    finally:
        os.close(fd)   # the system deletes the pin
    try:
        os.rmdir(path)   # its parent still pinned: a link put here meanwhile goes as a link
    except OSError:
        pass
    return True


def measured(path) -> tuple[int, float, os.stat_result | None]:
    """The bytes under the real folder `path`, when its newest file was written, and which folder it is; nothing once
    it is gone. Each folder is read under its own pin, held while its children are, so every folder read is the one
    checked; a link at `path` is refused, and none inside is followed. Files' times only: a pin changes its folder's
    own time, never a file's, and rustc writes files each time it compiles that crate. Only for a build folder this
    trim holds the lock of: a pin in a running build's session folder would block rustc."""

    try:
        fd = pin(path)
    except FileNotFoundError:
        return 0, 0.0, None
    try:
        if linked(path):
            raise OSError(f"{path} is a link")
        total, newest = 0, 0.0
        for e in os.scandir(path):
            if e.name == PIN:
                continue
            if e.is_dir(follow_symlinks=False) and not is_junction(e.path):
                more, when, _ = measured(e.path)
                total, newest = total + more, max(newest, when)
            elif e.is_file(follow_symlinks=False):
                st = e.stat(follow_symlinks=False)
                total, newest = total + st.st_size, max(newest, st.st_mtime)
        return total, newest, os.stat(path)
    finally:
        os.close(fd)


def pruned(tree: Path, cap: int = CAP) -> int:
    """The least recently used incremental caches under `tree` removed until the rest fit in `cap` bytes; the bytes
    freed. A build folder a build holds is left whole, and so is a cache that cannot be read, for the next turn's end.
    Never raises: it runs as a turn ends. Off Windows it trims nothing (the module's note)."""

    freed = 0
    if os.name != "nt":
        return freed
    try:
        freed = trimmed(tree, cap)
    except Exception as exc:
        errorlog.record("build-cache", exc, path=str(tree))
    return freed


def left(cache: Path, before: int, ident: os.stat_result) -> int:
    """What is left of `cache`, the folder `ident` names, once its removal ran: between none and all of it. Gone, it
    left nothing; unreadable, a link or another folder at its name, it counts as all still there."""

    try:
        rest, _, now = measured(cache)
    except OSError:
        return before
    if now is not None and not os.path.samestat(now, ident):
        return before
    return min(max(rest, 0), before)


def trimmed(tree: Path, cap: int) -> int:
    if linked(tree):   # a worktree is a real folder: a link here is not one to trim through
        return 0
    root, unread = os.stat(tree), []
    with ExitStack() as stack:
        sized, partial = [], False
        for build in builds(tree, unread):   # names only: each is checked under its own lock's pin before any use
            if not real(tree, root, build):
                continue
            try:
                with ExitStack() as one:   # its lock and pins, kept for the whole trim only once it proves this tree's
                    mine = held(one, build)   # its open lock file pins the path checked next
                    if not real(tree, root, build):   # a folder on the way replaced since: let go of its lock at once
                        continue
                    if not mine:   # a build is using it: left whole, and not read, so its share of the disk is unknown
                        raise OSError(f"{build} is building: its caches are not counted")
                    caches = pinned(one, build)
                    stack.enter_context(one.pop_all())
            except OSError as exc:
                errorlog.record("build-cache", exc, path=str(build))
                partial = True
                continue
            for cache in caches:
                try:
                    bytes_, when, ident = measured(cache)
                    if ident is not None:
                        sized.append((when, bytes_, cache, ident))
                except OSError as exc:
                    errorlog.record("build-cache", exc, path=str(cache))
                    partial = True
        total, freed = sum(c[1] for c in sized), 0
        for _, bytes_, cache, ident in sorted(sized, key=lambda c: c[0]):   # oldest first
            if total <= cap:
                break
            try:
                real_one = wiped(cache, ident)
            except OSError as exc:
                errorlog.record("build-cache", exc, path=str(cache))
                real_one = True   # what of it went, if any, is measured below
            if not real_one:   # moved away, another folder, a link or nothing in its place: none of it freed
                partial = True
                continue
            gone = bytes_ - left(cache, bytes_, ident)   # what really went: one that would not go leaves the next its turn
            total, freed = total - gone, freed + gone
        for exc in unread:   # a folder discovery could not read: the count may be short of the disk
            errorlog.record("build-cache", exc, path=str(tree))
        partial = partial or bool(unread)
        if total > cap or partial:
            errorlog.record("build-cache", OSError(f"{total} bytes counted over {cap}" + (", some not read" if partial
                                                   else "")), path=str(tree))
    return freed
