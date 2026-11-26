"""Cargo's incremental cache stays warm, and the least recently used goes once a worktree's caches pass the cap."""

import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from common import build_cache

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the trim pins folders the way only Windows keeps them")
NESTED = "apps/desktop/src-tauri/target/x86_64-pc-windows-msvc/debug"   # a Tauri app two folders down, by triple


def cache(root, name, age, size=100, build="target/debug"):
    """A crate's cache in a Cargo build folder: its lock file beside `incremental`, as Cargo leaves it."""

    (root / build).mkdir(parents=True, exist_ok=True)
    (root / build / ".cargo-lock").touch()
    session = root / build / "incremental" / name / "s-1"
    session.mkdir(parents=True)
    (session / "f").write_bytes(b"x" * size)
    os.utime(session / "f", (time.time() - age, time.time() - age))   # when rustc last wrote this session


def caches(tree):
    return sorted(p.name for p in tree.rglob("incremental/*") if p.name != build_cache.PIN and tree in p.resolve().parents)


def link(at, to):
    at.parent.mkdir(parents=True, exist_ok=True)   # a junction, as a worktree links a shared folder
    subprocess.run(["cmd", "/c", "mklink", "/J", str(at), str(to)], check=True, capture_output=True)


def test_the_oldest_caches_go_until_the_rest_fit_wherever_cargo_built_and_nothing_past_a_link_is_read(tmp_path):
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    cache(tree, "old", 400)
    cache(tree, "mid", 300, build=NESTED)
    cache(tree, "set", 200, build="build/rust/release")   # `build.target-dir` pointed inside the worktree
    cache(tree, "inner", 150, build="target/debug/other/debug")   # a target set inside another build folder
    cache(tree, "new", 100, build="target/release")
    cache(outside, "theirs", 900)
    (outside / "keep").mkdir()
    (outside / "keep" / "f").write_bytes(b"x")
    link(tree / "app" / "target", outside / "target")
    link(tree / "target/debug/incremental/old/s-1/shared", outside / "keep")   # a link inside a cache that goes
    assert build_cache.pruned(tree, cap=10_000) == 0   # under the cap: every cache stays warm
    assert build_cache.pruned(tree, cap=10_000) == 0   # and its pins left the order as it was: read again below
    assert build_cache.pruned(tree, cap=250) == 300   # the three oldest, however deep their build folders
    assert caches(tree) == ["inner", "new"]
    assert (outside / "target/debug/incremental/theirs/s-1/f").exists() and (outside / "keep" / "f").exists()
    assert not list(tree.rglob(build_cache.PIN))   # each pin goes with its lock
    assert build_cache.pruned(tmp_path / "none") == 0   # a worktree that builds nothing with Cargo


def test_a_folder_replaced_by_a_link_after_it_was_found_is_never_trimmed(tmp_path):
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    cache(tree, "ours", 300, build="app/target/debug")
    cache(outside, "theirs", 300, build="target/debug")
    found = list(build_cache.builds(tree, []))
    shutil.rmtree(tree / "app")
    link(tree / "app", outside)   # between discovery and the lock: `app/target/debug` now names their build
    with patch.object(build_cache, "builds", lambda t, unread: iter(found)):
        assert build_cache.pruned(tree, cap=0) == 0
    assert (outside / "target/debug/incremental/theirs/s-1/f").exists()


@pytest.mark.parametrize("theirs", [True, False])
def test_a_folder_replaced_before_its_pin_loses_nothing_and_a_pin_never_removes_a_file_it_did_not_create(
        tmp_path, theirs):
    outside, at = tmp_path / "outside", tmp_path / "tree" / "cache"
    outside.mkdir()
    (outside / "f").write_text("x")
    if theirs:   # a file of that name already there: never opened as a pin, never removed
        (outside / build_cache.PIN).write_text("theirs")
    link(at, outside)
    real, seen = build_cache.linked, []

    def linked(path):   # `cache` was a real folder when first looked at, and a junction by its pin
        if Path(path) == at and not seen:
            seen.append(path)
            return False
        return real(path)
    with patch.object(build_cache, "linked", linked):
        try:
            build_cache.wiped(at)
        except FileExistsError:
            assert theirs
    assert (outside / "f").exists()
    assert (outside / build_cache.PIN).read_text() == "theirs" if theirs else not (outside / build_cache.PIN).exists()


def test_a_build_cargo_holds_is_left_whole_and_unread_and_a_cache_that_will_not_go_leaves_the_next_its_turn(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "running", 500, build="target/release")
    cache(tree, "stuck", 400)
    cache(tree, "old", 300)
    cache(tree, "new", 100)
    fd = os.open(tree / "target/release/.cargo-lock", os.O_RDWR)   # a build started from a shell, still running
    real = build_cache.wiped
    try:
        build_cache.lock(fd, True)
        with patch.object(build_cache, "wiped", lambda c, i, *f: set() if c.name == "stuck" else real(c, i, *f)):   # held open
            assert build_cache.pruned(tree, cap=200) == 100   # only what really went; the busy build not read
        assert caches(tree) == ["new", "running", "stuck"]   # the held build untouched, the next one taken instead
    finally:
        build_cache.lock(fd, False)
        os.close(fd)
    assert build_cache.pruned(tree, cap=150) == 200   # its build done: the oldest go first again
    assert caches(tree) == ["new"]


@pytest.mark.parametrize("junction", [True, False])
def test_a_cache_moved_away_for_another_entry_is_never_counted_as_freed(tmp_path, junction):
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    cache(tree, "old", 300)
    cache(tree, "new", 100)
    outside.mkdir()
    (outside / "f").write_bytes(b"x" * 10)
    inc, real = tree / "target/debug/incremental", build_cache.wiped

    def swapped(c, ident, *f):   # after it was measured: moved to another name in the same folder, something in its place
        if c.name == "old":
            os.rename(c, inc / "kept")
            link(c, outside) if junction else c.mkdir()
        return real(c, ident, *f)
    with patch.object(build_cache, "wiped", swapped):
        assert build_cache.pruned(tree, cap=100) == 100   # what was in its name freed nothing; `new` went instead
    # a junction goes as itself, never its target; another folder in its name is not the one counted, and stays
    assert caches(tree) == (["kept"] if junction else ["kept", "old"]) and (outside / "f").exists()


def test_a_cache_unreadable_is_left_and_the_rest_still_trimmed(tmp_path):
    tree = tmp_path / "tree"
    for name, age in (("a", 300), ("b", 200), ("c", 100)):
        cache(tree, name, age)
    real = build_cache.measured
    with patch.object(build_cache, "measured", lambda p, *a: (_ for _ in ()).throw(PermissionError("denied"))
                      if Path(p).name == "a" else real(p, *a)):
        assert build_cache.pruned(tree, cap=100) == 100   # `a` in no count; of the rest, the oldest went
    assert caches(tree) == ["a", "c"]


def test_a_session_moved_out_of_a_cache_is_never_counted_as_freed(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "old", 300)
    cache(tree, "new", 100)
    inc, real = tree / "target/debug/incremental", build_cache.wiped

    def moved(c, ident, *f):   # after it was measured: its session moved to a cache of another name, the folder kept
        if c.name == "old":
            (inc / "elsewhere").mkdir()
            os.rename(c / "s-1", inc / "elsewhere" / "s-1")
        return real(c, ident, *f)
    with patch.object(build_cache, "wiped", moved):
        assert build_cache.pruned(tree, cap=100) == 100   # the emptied folder freed nothing, so `new` went too
    assert caches(tree) == ["elsewhere"]


def test_a_file_linked_into_the_next_session_is_counted_and_freed_once(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "old", 300)
    cache(tree, "new", 100)
    s1 = tree / "target/debug/incremental/old/s-1"
    (s1.parent / "s-2").mkdir()
    os.link(s1 / "f", s1.parent / "s-2" / "f")   # as rustc carries a session's files into the next
    assert build_cache.pruned(tree, cap=100) == 100   # `old` is 100 bytes on the disk, not 200: `new` stays
    assert caches(tree) == ["new"]


def test_a_file_linked_from_two_caches_is_freed_where_its_last_link_goes(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "a", 300)
    cache(tree, "b", 200)
    cache(tree, "c", 100)
    inc = tree / "target/debug/incremental"
    os.unlink(inc / "b/s-1/f")
    os.link(inc / "a/s-1/f", inc / "b/s-1/f")   # one 100-byte file in both: counted with `a`
    assert build_cache.pruned(tree, cap=100) == 100   # freed when `b` went, though counted with `a`: `c` stays
    assert caches(tree) == ["c"]


def test_a_cache_that_cannot_be_read_whole_hides_none_of_anothers_files(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "a", 300)
    cache(tree, "b", 200)
    inc, real = tree / "target/debug/incremental", build_cache.measured
    os.unlink(inc / "b/s-1/f")
    os.link(inc / "a/s-1/f", inc / "b/s-1/f")   # the same file in both
    (inc / "a/s-2").mkdir()

    def measured(p, *a):   # `a` counts the shared file, then cannot read its second session
        if Path(p).name == "s-2":
            raise PermissionError("denied")
        return real(p, *a)
    with patch.object(build_cache, "measured", measured):
        # `b` still counts it, so it goes; its last link stays in `a`, so nothing on the disk was freed
        assert build_cache.pruned(tree, cap=0) == 0
    assert caches(tree) == ["a"]


def test_a_removal_that_fails_leaves_the_next_its_turn_and_keeps_what_went(tmp_path):
    tree = tmp_path / "tree"
    for name, age in (("a", 300), ("b", 200), ("c", 100)):
        cache(tree, name, age)
    real = build_cache.wiped

    def wiped(c, i, *f):
        if c.name == "b":
            raise PermissionError("denied")
        return real(c, i, *f)
    with patch.object(build_cache, "wiped", wiped):
        assert build_cache.pruned(tree, cap=0) == 200   # `a` and `c` went and count; `b` could not be read to remove
    assert caches(tree) == ["b"]


@pytest.mark.parametrize("held", [False, True])
def test_the_file_removed_is_the_one_looked_at_wherever_it_moved(tmp_path, held):
    at, aside = tmp_path / "f", tmp_path / "aside"
    at.write_bytes(b"x" * 100)
    first, real, close = os.lstat(at), os.fstat, []

    def fstat(fd):   # while its handle is open: the file moved away, another put in its name
        st = real(fd)
        os.rename(at, aside)
        at.write_bytes(b"y" * 100)
        if held:   # and a reader holds it where it went
            close.append(shared(aside))
        return st
    with patch("os.fstat", fstat):
        assert build_cache.removed(at) == (None if held else (first.st_dev, first.st_ino))
    assert at.read_bytes() == b"y" * 100   # its replacement stays
    assert aside.exists() == held   # the one looked at went, or waits for its reader: then nothing was claimed
    for c in close:
        c()
    assert not aside.exists()


@pytest.mark.parametrize("kind", ["junction", "symlink", "folder"])
def test_a_file_removal_never_follows_nor_takes_what_is_not_a_plain_file(tmp_path, kind):
    outside, at = tmp_path / "outside", tmp_path / "entry"
    outside.mkdir()
    (outside / "f").write_bytes(b"x")
    if kind == "junction":
        link(at, outside)
    elif kind == "symlink":
        try:
            os.symlink(outside / "f", at)   # what a file's name could become before its removal opens it
        except OSError as exc:
            pytest.skip(f"symbolic links need a privilege here: {exc}")
    else:
        at.mkdir()
    with pytest.raises(OSError):
        build_cache.removed(at)
    assert os.path.lexists(at) and (outside / "f").read_bytes() == b"x"   # refused before anything was marked


def shared(path):
    """A reader's handle that lets others delete the file under it, as `CreateFileW` with every share flag gives."""

    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    handle = k32.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0, None)   # GENERIC_READ, all shares, existing
    assert handle not in (None, wintypes.HANDLE(-1).value)
    return lambda: k32.CloseHandle(wintypes.HANDLE(handle))


def test_a_file_another_handle_still_holds_is_never_counted_as_freed(tmp_path):
    at = tmp_path / "f"
    at.write_bytes(b"x" * 100)
    close = shared(at)
    try:
        assert build_cache.removed(at) is None   # marked, but its bytes stay while the reader holds it
        assert at.exists()
    finally:
        close()
    assert not at.exists()   # gone once the last handle closed


def test_a_file_whose_going_cannot_be_checked_stays(tmp_path):
    at = tmp_path / "f"
    at.write_bytes(b"x" * 100)
    with patch.object(build_cache, "final", side_effect=OSError(5, "Access is denied")):
        with pytest.raises(OSError):
            build_cache.removed(at)
    assert at.read_bytes() == b"x" * 100   # the mark taken back: it stays, and is not counted as freed
    os.unlink(at)   # nothing left pending on it


def test_only_a_name_known_to_be_missing_counts_as_gone(tmp_path):
    at = tmp_path / "f"
    assert build_cache.gone(str(at))
    at.write_bytes(b"x")
    assert not build_cache.gone(str(at))
    at.unlink()
    with patch("os.lstat", side_effect=PermissionError(13, "denied")):   # a share that went away, say
        assert not build_cache.gone(str(at))


def test_a_handle_no_descriptor_took_is_closed_at_once(tmp_path):
    at = tmp_path / "f"
    at.write_bytes(b"x")
    with patch("msvcrt.open_osfhandle", side_effect=OSError(24, "Too many open files")):
        with pytest.raises(OSError):
            build_cache.removed(at)
    os.close(os.open(at, os.O_RDONLY))   # refused while a handle asking to delete it were still open
    assert at.read_bytes() == b"x"


def test_a_folder_that_fails_partway_keeps_what_already_went(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "a", 300)
    cache(tree, "b", 100)
    a = tree / "target/debug/incremental/a"
    (a / "s-2").mkdir()
    (a / "s-2" / "g").write_bytes(b"x" * 100)
    os.utime(a / "s-2" / "g", (time.time() - 300, time.time() - 300))
    real_scandir, real_counted, wiping = os.scandir, build_cache.counted, []

    def counted(incs):
        out = real_counted(incs)
        wiping.append(True)
        return out

    def scandir(path):   # removing `a`: its first session goes, then its listing fails
        entries = list(real_scandir(path))
        if not (wiping and Path(path) == a):
            return iter(entries)

        def partway():
            yield next(e for e in entries if e.name == "s-1")
            raise PermissionError("denied")
        return partway()
    with patch("os.scandir", scandir), patch.object(build_cache, "counted", counted):
        assert build_cache.pruned(tree, cap=200) == 100   # the session that went counts: 200 left, so `b` stays
    assert caches(tree) == ["a", "b"] and not (a / "s-1").exists()


def test_a_folder_measured_is_the_one_its_children_are_read_in(tmp_path):
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    cache(tree, "ours", 300)
    (outside / "s-1").mkdir(parents=True)
    (outside / "s-1" / "big").write_bytes(b"x" * 1000)
    at, real = tree / "target/debug/incremental/ours", os.scandir

    def scandir(path):   # the moment its listing is in hand: try to move the folder away for a junction
        entries = list(real(path))
        if Path(path) == at:
            with pytest.raises(PermissionError):   # its pin holds it while its children are read
                os.rename(at, at.with_name("moved"))
        return iter(entries)
    with patch("os.scandir", scandir):
        assert build_cache.measured(at)[0] == 100


def test_a_lock_entry_that_is_not_the_file_opened_is_never_trusted(tmp_path):
    tree, other = tmp_path / "tree", tmp_path / "other"
    cache(tree, "old", 300)
    other.write_text("")
    real = os.lstat   # the entry swapped between the open and its check: a link then, or another file now

    def lstat(path, *a, **k):
        return real(other) if str(path).endswith(".cargo-lock") else real(path, *a, **k)
    with patch("os.lstat", lstat):
        assert build_cache.pruned(tree, cap=0) == 0
    assert caches(tree) == ["old"]


def test_a_link_in_a_caches_place_is_never_read_nor_counted_as_freed(tmp_path):
    outside, at = tmp_path / "outside", tmp_path / "cache"
    outside.mkdir()
    (outside / "big").write_bytes(b"x" * 1000)
    link(at, outside)
    with pytest.raises(OSError):
        build_cache.measured(at)


def test_a_trim_that_fails_never_raises_into_the_turns_end(tmp_path):
    cache(tmp_path, "old", 300)
    with patch.object(build_cache, "trimmed", side_effect=PermissionError("denied")):
        assert build_cache.pruned(tmp_path, cap=0) == 0
