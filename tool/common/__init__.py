"""common — what two pipelines actually share. Nothing here imports a pipeline.

`worktree_home` is where `workspace` makes the worktrees an agent writes in,
and the one place `agent` opens a write session. Held here so the two cannot
drift apart: a worktree created by another tool is not ours.
"""

import os
from pathlib import Path
import re
import stat
import sys
import time


def is_junction(path) -> bool:
    """`os.path.isjunction` arrives only in 3.12; the reparse tag reads the same
    from 3.8 on. Off Windows neither the tag nor its constant exists."""
    mount = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    try:
        return mount is not None and getattr(os.lstat(path), "st_reparse_tag", 0) == mount
    except (OSError, ValueError):
        return False


def layout(cwd: Path) -> tuple | None:
    """`(checkout, git dir, common dir, main clone)` read off git's own files, without running git.

    Every hook asks this once per tool call, and starting `git` on Windows costs more than the
    hook's own work; `guard_grant` reads the same answer for the grant."""
    top = next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), None)
    if top is None:
        return None
    gitdir = top / ".git"
    if gitdir.is_file():
        m = re.match(r"gitdir:\s*(.+)", gitdir.read_text(encoding="utf-8", errors="replace").strip())
        if not m:
            return None
        gitdir = (top / m.group(1).strip()).resolve()
    common = gitdir
    if (gitdir / "commondir").is_file():
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    return top, gitdir, common, common.parent if common.name == ".git" else common


def swap(temporary: Path, file: Path) -> None:
    """`temporary` replaces `file` whole. A Windows reader holding `file`
    open refuses the swap for a moment, so it is tried again."""

    for attempt in range(40):
        try:
            Path(temporary).replace(file)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.025)


def python_environment(env: dict | None = None) -> dict:
    """Child commands use the running interpreter without shell activation."""
    env = dict(os.environ if env is None else env)
    if os.name == "nt":
        env = {key.upper(): value for key, value in env.items()}
    directory = str(Path(sys.executable).parent)
    paths = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join([directory, *(p for p in paths if p
                                  and os.path.normcase(p) != os.path.normcase(directory))])
    return env


def worktree_home(repo: Path) -> Path:
    """`../<repo>-worktrees` beside the original checkout `repo`."""

    repo = Path(repo).resolve()
    return repo.parent / f"{repo.name}-worktrees"
