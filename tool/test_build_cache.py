"""Cargo's incremental cache stays warm, and the least recently used goes once a worktree's caches pass the cap."""

import os
import shutil
import subprocess
import time
from unittest.mock import patch

from common import build_cache

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
    at.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":   # a junction, as a worktree links a shared folder
        subprocess.run(["cmd", "/c", "mklink", "/J", str(at), str(to)], check=True, capture_output=True)
    else:
        at.symlink_to(to)


def test_the_oldest_caches_go_until_the_rest_fit_wherever_cargo_built_and_nothing_past_a_link_is_read(tmp_path):
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    cache(tree, "old", 400)
    cache(tree, "mid", 300, build=NESTED)
    cache(tree, "set", 200, build="build/rust/release")   # `build.target-dir` pointed inside the worktree
    cache(tree, "new", 100, build="target/release")
    cache(outside, "theirs", 900)
    link(tree / "app" / "target", outside / "target")
    assert build_cache.pruned(tree, cap=10_000) == 0   # under the cap: every cache stays warm
    assert build_cache.pruned(tree, cap=250) == 200   # the two oldest, however deep their build folders
    assert caches(tree) == ["new", "set"]
    assert (outside / "target/debug/incremental/theirs/s-1/f").exists()
    assert not list(tree.rglob(build_cache.PIN))   # each pin goes with its lock
    assert build_cache.pruned(tmp_path / "none") == 0   # a worktree that builds nothing with Cargo


def test_a_build_cargo_holds_is_left_whole_and_a_cache_that_will_not_go_leaves_the_next_its_turn(tmp_path):
    tree = tmp_path / "tree"
    cache(tree, "running", 500, build="target/release")
    cache(tree, "stuck", 400)
    cache(tree, "old", 300)
    cache(tree, "new", 100)
    fd = os.open(tree / "target/release/.cargo-lock", os.O_RDWR)   # a build started from a shell, still running
    real = shutil.rmtree
    try:
        build_cache.lock(fd, True)
        with patch.object(build_cache.shutil, "rmtree",
                          lambda path, **k: None if path.name == "stuck" else real(path, **k)):   # a file held open
            assert build_cache.pruned(tree, cap=200) == 100   # only what really went
        assert caches(tree) == ["new", "running", "stuck"]   # the held build untouched, the next one taken instead
    finally:
        build_cache.lock(fd, False)
        os.close(fd)
    assert build_cache.pruned(tree, cap=150) == 200   # its build done: the oldest go first again
    assert caches(tree) == ["new"]


def test_a_trim_that_fails_never_raises_into_the_turns_end(tmp_path):
    cache(tmp_path, "old", 300)
    with patch.object(build_cache, "trimmed", side_effect=PermissionError("denied")):
        assert build_cache.pruned(tmp_path, cap=0) == 0
