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
        with patch.object(build_cache, "wiped", lambda c, i: None if c.name == "stuck" else real(c, i)):   # held open
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

    def swapped(c, ident):   # after it was measured: moved to another name in the same folder, something in its place
        if c.name == "old" and not (inc / "kept").exists():   # once
            os.rename(c, inc / "kept")
            link(c, outside) if junction else c.mkdir()
        return real(c, ident)
    with patch.object(build_cache, "wiped", swapped):
        assert build_cache.pruned(tree, cap=100) == 100   # what was in its name freed nothing; `kept` went where it went
    # a junction goes as itself, never its target; an empty folder in its name is a cache of its own, and goes
    assert caches(tree) == ["new"] and (outside / "f").exists()


def test_a_cache_that_cannot_be_read_again_is_never_counted_as_freed(tmp_path):
    tree = tmp_path / "tree"
    for name, age in (("a", 300), ("b", 200), ("c", 100)):
        cache(tree, name, age)
    real_measured, real_wiped, gone = build_cache.measured, build_cache.wiped, []

    def measured(path):   # once the first went, `b` cannot be read, nor removed
        if gone and Path(path).name == "b":
            raise PermissionError("denied")
        return real_measured(path)

    def wiped(c, ident):
        if c.name != "b":
            gone.append(c.name)
            real_wiped(c, ident)
    with patch.object(build_cache, "measured", measured), patch.object(build_cache, "wiped", wiped):
        assert build_cache.pruned(tree, cap=100) == 200   # `b` still counted at its last size: `c` had to go too
    assert caches(tree) == ["b"]


def test_a_session_moved_out_of_a_cache_is_counted_where_it_went(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "old", 300)
    cache(tree, "new", 100)
    inc, real = tree / "target/debug/incremental", build_cache.wiped

    def moved(c, ident):   # after it was measured: its session moved to a cache of another name, the folder kept
        if c.name == "old":
            (inc / "elsewhere").mkdir()
            os.rename(c / "s-1", inc / "elsewhere" / "s-1")
        return real(c, ident)
    with patch.object(build_cache, "wiped", moved):
        assert build_cache.pruned(tree, cap=100) == 100   # the emptied folder freed nothing; the moved bytes counted
    assert caches(tree) == ["new"]   # where they went, still the oldest, they went next


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
