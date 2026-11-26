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
    os.utime(session, (time.time() - age, time.time() - age))


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
    os.utime(tree / "target/debug/incremental/old/s-1", (time.time() - 400, time.time() - 400))   # dated as before
    assert build_cache.pruned(tree, cap=10_000) == 0   # under the cap: every cache stays warm
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


def test_a_build_cargo_holds_is_left_whole_but_counted_and_a_cache_that_will_not_go_leaves_the_next_its_turn(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "running", 500, build="target/release")
    cache(tree, "stuck", 400)
    cache(tree, "old", 300)
    cache(tree, "new", 100)
    fd = os.open(tree / "target/release/.cargo-lock", os.O_RDWR)   # a build started from a shell, still running
    real = build_cache.wiped
    try:
        build_cache.lock(fd, True)
        with patch.object(build_cache, "wiped", lambda c: None if c.name == "stuck" else real(c)):   # a file held open
            assert build_cache.pruned(tree, cap=200) == 200   # only what really went, the busy build counted
        assert caches(tree) == ["running", "stuck"]   # the held build untouched, the next ones taken instead
    finally:
        build_cache.lock(fd, False)
        os.close(fd)
    assert build_cache.pruned(tree, cap=150) == 100   # its build done: the oldest goes first again
    assert caches(tree) == ["stuck"]


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
        build_cache.size(at)
    assert build_cache.left(at, 100) == 100   # the folder moved away and a link took its name: nothing freed


def test_a_trim_that_fails_never_raises_into_the_turns_end(tmp_path):
    cache(tmp_path, "old", 300)
    with patch.object(build_cache, "trimmed", side_effect=PermissionError("denied")):
        assert build_cache.pruned(tmp_path, cap=0) == 0
