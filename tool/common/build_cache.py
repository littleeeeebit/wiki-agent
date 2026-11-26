"""Cargo's incremental cache, kept warm and bounded.

With it, a rebuild after a fix compiles only what changed in a crate; without it (`CARGO_INCREMENTAL=0`, #366) every
build after a one-line change compiled the whole crate again, once per flag set an agent used. Cargo never prunes it,
and each flag set keeps its own: one worktree's `target` once reached 24 GB. So after each turn, while the turn still
holds its worktree, the least recently used caches go until the rest fit under `CAP`.

Only a build folder whose Cargo lock this takes is touched, so no Cargo build runs in it meanwhile, whoever started
it. A busy one is not even read, since a pin in a running build's session folder would block rustc; the count is then
logged as partial. No cache is measured or deleted through a link or outside the worktree, because on Windows a
folder with a file open in it cannot be renamed or removed (discovery only gathers names, each checked as below):

- The lock's open file pins every folder from the worktree root down to the build folder. Only then is that path
  checked: the same root as when the trim began, and a real folder at every step.
- Each folder below is read, and removed, under its own pin: a file created there, never one already there, and
  deleted by the system when its handle closes, so no pin is ever removed by name. The folder is checked again once
  its pin holds, and a link met anywhere goes as a link. A cache whose name no longer holds the folder measured is
  left.
- Every file is counted once by its identity, a file linked more than once too. Only counted files a removal itself
  freed come off the count: each it removed, through that file's own handle opened as the entry itself and refused
  unless it is a plain file, as its last link, once it is really gone (not held by another handle), in whichever
  cache that was, kept as it goes. A cache moved or held meanwhile stays counted. What is left is an estimate; the next
  turn's end counts again.

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


def pinned(stack: ExitStack, build: Path) -> Path:
    """`build`'s real `incremental`, kept where it is by an open pin file until `stack` closes. A link there raises:
    the caller lets go of the pin at once."""

    inc = build / "incremental"
    if linked(inc):
        raise OSError(f"{inc} is a link")
    stack.callback(os.close, pin(inc))
    if linked(inc) or os.path.realpath(inc) != os.path.join(os.path.realpath(build), "incremental"):
        raise OSError(f"{inc} was replaced")   # before the pin held it: only the pin went through
    return inc


def unlinked(path) -> None:
    """A link or junction removed as itself: what it points at is never touched."""

    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)   # a junction, or a link to a folder


def kernel32():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def marked(k32, handle, delete: bool) -> None:
    """`handle`'s file marked for deletion once no handle holds it, or the mark taken back. The legacy disposition: a
    file marked this way keeps its name until it is really gone, and cannot be renamed meanwhile."""

    import ctypes

    mark = ctypes.c_ubyte(int(delete))   # FILE_DISPOSITION_INFO: DeleteFile
    if not k32.SetFileInformationByHandle(handle, 4, ctypes.byref(mark), 1):   # FileDispositionInfo
        raise ctypes.WinError(ctypes.get_last_error())


def final(k32, handle) -> str:
    """Where `handle`'s file is now."""

    import ctypes

    name = ctypes.create_unicode_buffer(32768)
    if not k32.GetFinalPathNameByHandleW(handle, name, len(name), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return name.value


def gone(path: str) -> bool:
    """Whether nothing is at `path`, known for sure: a lookup that fails for any other reason is not an answer."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def removed(path) -> tuple | None:
    """The plain file at `path` removed through its own handle, so the file removed is the one looked at, whatever its
    name holds meanwhile. The entry is opened as itself, never followed: a link or any other reparse point there is
    refused before anything is marked for deletion, and only then is that same handle marked. If where the file is
    cannot then be read, the mark is taken back and the file stays. Its `(st_dev, st_ino)` only when that was its last
    link and it is known to be gone; held by another handle, or not known, nothing is claimed."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    k32 = kernel32()
    delete, read_attributes, share_all, open_existing = 0x00010000, 0x80, 0x7, 3
    as_itself, folders_too = 0x00200000, 0x02000000   # FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS
    handle = k32.CreateFileW(str(path), delete | read_attributes, share_all, None, open_existing,
                             as_itself | folders_too, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)   # from here closing it closes the handle
    except OSError:
        k32.CloseHandle(handle)   # no descriptor took it: never left open
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"{path} is not a plain file")
        marked(k32, handle, True)
        try:
            where = final(k32, handle)   # marked, it cannot move from here
        except OSError:
            marked(k32, handle, False)   # its going could not be checked: it stays
            raise
    finally:
        os.close(fd)   # the system deletes the file this handle opened, once no handle holds it
    # Its name gone now means its bytes are. Still there, another handle holds it (or a new file took the name), and a
    # lookup that fails tells nothing: then nothing is claimed.
    return (st.st_dev, st.st_ino) if st.st_nlink == 1 and gone(where) else None


def wiped(path: Path, ident: os.stat_result | None = None, freed: set | None = None) -> set:
    """`path`, inside a pinned real folder, removed folder by folder, each under its own pin; what will not go (a file
    something holds, a folder whose pin name is taken) stays. Only the real folder `ident` names, when given: a link in
    its place goes as a link, and another folder there is left. Each file it really freed goes into `freed` as it
    goes, so what went is kept even if a folder fails partway; `freed` is returned."""

    freed = set() if freed is None else freed
    if linked(path):
        unlinked(path)
        return freed
    try:
        fd = pin(path)   # from here `path` stays the folder it is now
    except OSError:
        return freed
    try:
        if linked(path):   # replaced just before the pin: only the pin went through, and goes with its handle
            return freed
        if ident is not None and not os.path.samestat(os.stat(path), ident):   # another folder took its name
            return freed
        for e in os.scandir(path):
            if e.name == PIN:
                continue
            try:
                if linked(e.path):
                    unlinked(e.path)
                elif e.is_dir(follow_symlinks=False):
                    wiped(Path(e.path), None, freed)
                elif (file := removed(e.path)) is not None:
                    freed.add(file)
            except OSError:
                pass
    finally:
        os.close(fd)   # the system deletes the pin
    try:
        os.rmdir(path)   # its parent still pinned: a link put here meanwhile goes as a link
    except OSError:
        pass
    return freed


def measured(path, seen: dict | None = None, mine: dict | None = None) -> tuple[int, float, os.stat_result | None]:
    """The bytes under the real folder `path`, when its newest file was written, and which folder it is; nothing once
    it is gone. Each folder is read under its own pin, held while its children are, so every folder read is the one
    checked; a link at `path` is refused, and none inside is followed. Files' times only: a pin changes its folder's
    own time, never a file's, and rustc writes files each time it compiles that crate. Only for a build folder this
    trim holds the lock of: a pin in a running build's session folder would block rustc. Each file it counts goes into
    `mine` by `(st_dev, st_ino)` with its size, and one already in `seen` or `mine` is not counted again: rustc links
    a session's files into the next."""

    seen = {} if seen is None else seen
    mine = {} if mine is None else mine
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
                more, when, _ = measured(e.path, seen, mine)
                total, newest = total + more, max(newest, when)
            elif e.is_file(follow_symlinks=False):
                st = os.lstat(e.path)
                newest, file = max(newest, st.st_mtime), (st.st_dev, st.st_ino)
                if file not in seen and file not in mine:
                    mine[file] = st.st_size
                    total += st.st_size
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


def counted(incs: list[Path]) -> tuple[list, dict, bool]:
    """Every cache in the pinned `incremental` folders, `(newest file, bytes, path, which folder)`; every file counted,
    by `(st_dev, st_ino)`, with its size, each once; and whether one could not be read. A cache that cannot be read
    whole adds none of its files."""

    found, files, short = [], {}, False
    for inc in incs:
        try:
            entries = folders(inc)
        except OSError as exc:
            errorlog.record("build-cache", exc, path=str(inc))
            short = True
            continue
        for e in entries:
            mine = {}
            try:
                bytes_, when, ident = measured(e.path, files, mine)
            except OSError as exc:
                errorlog.record("build-cache", exc, path=e.path)
                short = True
                continue
            if ident is not None:
                found.append((when, bytes_, Path(e.path), ident))
                files.update(mine)
    return found, files, short


def trimmed(tree: Path, cap: int) -> int:
    if linked(tree):   # a worktree is a real folder: a link here is not one to trim through
        return 0
    root, unread = os.stat(tree), []
    with ExitStack() as stack:
        incs, partial = [], False
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
                    incs.append(pinned(one, build))
                    stack.enter_context(one.pop_all())
            except OSError as exc:
                errorlog.record("build-cache", exc, path=str(build))
                partial = True
        # One count, then the oldest go until what is left of it fits. Only counted files a removal itself freed come
        # off, wherever their last link was: a cache that will not go, or moved away meanwhile, stays counted. What is
        # left is an estimate of the count, and the next turn's end counts again.
        sized, files, short = counted(incs)   # what cannot be read is in no count: never credited, never removed
        total, freed = sum(c[1] for c in sized), 0
        for _, _, cache, ident in sorted(sized, key=lambda c: c[0]):   # oldest first
            if total <= cap:
                break
            went = set()
            try:
                wiped(cache, ident, went)
            except OSError as exc:   # it failed partway: what went still counts, and the next gets its turn
                errorlog.record("build-cache", exc, path=str(cache))
            gone = sum(files.pop(f) for f in went if f in files)
            total, freed = total - gone, freed + gone
        partial = partial or short
        for exc in unread:   # a folder discovery could not read: the count may be short of the disk
            errorlog.record("build-cache", exc, path=str(tree))
        partial = partial or bool(unread)
        if total > cap or partial:
            errorlog.record("build-cache", OSError(f"{total} bytes counted over {cap}" + (", some not read" if partial
                                                   else "")), path=str(tree))
    return freed
