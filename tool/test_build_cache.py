"""Cargo's incremental cache stays warm, and the least recently used goes once a worktree's caches pass the cap."""

import os
import subprocess
import time

from common import build_cache


def cache(root, name, age, size=100, under="target/debug/incremental"):
    session = root / under / name / "s-1"
    session.mkdir(parents=True)
    (session / "f").write_bytes(b"x" * size)
    os.utime(session, (time.time() - age, time.time() - age))


def test_the_oldest_caches_go_until_the_rest_fit_and_nothing_past_a_link_is_touched(tmp_path):
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    cache(tree, "old", 300)
    cache(tree, "mid", 200, under="src-tauri/target/x86_64-pc-windows-msvc/debug/incremental")
    cache(tree, "new", 100, under="target/release/incremental")
    cache(outside, "theirs", 900)
    link = tree / "app" / "target"
    link.parent.mkdir()
    if os.name == "nt":   # a junction, as a worktree links a shared folder
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside / "target")], check=True, capture_output=True)
    else:
        link.symlink_to(outside / "target")
    assert build_cache.pruned(tree, cap=10_000) == 0   # under the cap: every cache stays warm
    assert build_cache.pruned(tree, cap=250) == 100
    left = sorted(p.parent.name + "/" + p.name for p in tree.rglob("incremental/*") if "outside" not in str(p.resolve()))
    assert left == ["incremental/mid", "incremental/new"]
    assert (outside / "target/debug/incremental/theirs/s-1/f").exists()
    assert build_cache.pruned(tmp_path / "none") == 0   # a worktree that builds nothing with Cargo
