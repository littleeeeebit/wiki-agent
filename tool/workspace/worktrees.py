"""worktrees — where an agent is allowed to write.

Every write happens in a worktree made here, never in the checkout the person
works in. They all sit beside the repository, in `../<repo>-worktrees/<task>`,
so what is the program's and what is the person's is one directory apart.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from common import worktree_home

from .sessions import checkout

# A task name is both a branch and a directory. Lowercase ASCII only: a Korean
# directory name in a path has broken `subprocess` decoding on this machine.
TASK = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def _main(repo: Path) -> Path:
    """The original checkout, or `ValueError`. A worktree made inside a worktree
    would put its folder beside the wrong directory."""

    top, common, _ = checkout(Path(repo))
    if not top or top != common or Path(top) != Path(repo).resolve():
        raise ValueError(f"원본 체크아웃이 아니다: {repo}")
    return Path(top)


def create(repo: Path, task: str) -> Path:
    """A new worktree on a new branch `task`, from the checkout's HEAD."""

    repo = _main(repo)
    if not TASK.fullmatch(task):
        raise ValueError(f"작업 이름은 소문자·숫자·- 만, 64자까지: {task!r}")
    path = worktree_home(repo) / task
    done = _git(repo, "worktree", "add", "-b", task, str(path))
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or f"git worktree add 실패: {task}")
    return path


def _changed(repo: Path, a: str, b: str) -> set[str] | None:
    """Paths whose blob or mode differs between two commits. `None` if git fails.

    Object ids only: no rename detection, no textconv, no attributes, so
    nothing in the repository's configuration can make two versions look alike.
    """

    done = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "-z", "--no-renames",
                           "--no-ext-diff", "--no-textconv", a, b],
                          capture_output=True, timeout=60)
    return None if done.returncode else {p for p in done.stdout.split(b"\0") if p}


def merged(repo: Path, branch: str) -> bool:
    """Would deleting `branch` lose anything the original checkout's HEAD lacks?

    True when every path the branch changed since its merge base holds, in
    HEAD right now, exactly the branch's version — the same blob and mode.
    A squash merge — how this repository merges — passes, as does a merge or
    a branch with no changes of its own.

    A comparison of objects, not a simulation. Four earlier readings each
    deleted a commit that existed nowhere else, because each asked git to
    imagine something and the imagining had its own rules:

    - the upstream being gone: a remote branch can be deleted unmerged
    - `git cherry`: finds the patch in HEAD's history after HEAD reverted it
    - reversing the diff onto HEAD: `git apply` matches the same lines at
      another place in the file
    - merging into HEAD (`merge-tree`): a merge driver such as `merge=ours`
      drops the branch's side cleanly, and a modify/delete conflict keeps
      HEAD's version

    Unsure is `False` — HEAD not yet pulled, or HEAD having changed one of
    those files again since: a kept branch costs a line in a listing; a wrong
    `True` costs work.
    """

    if not branch:
        return False
    base = _git(repo, "merge-base", "HEAD", branch).stdout.strip()
    if not base:
        return False
    ours, differs = _changed(repo, base, branch), _changed(repo, branch, "HEAD")
    return ours is not None and differs is not None and not ours & differs


def worktrees(repo: Path) -> list[dict]:
    """The worktrees under `worktree_home(repo)`: `path`, `branch`, `dirty`, `merged`."""

    repo = _main(repo)
    root = worktree_home(repo)
    rows, row = [], {}
    for line in [*_git(repo, "worktree", "list", "--porcelain").stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            row = {"path": Path(line[9:]).resolve(), "branch": ""}
        elif line.startswith("branch "):
            row["branch"] = line[7:].removeprefix("refs/heads/")
        elif not line and row:
            if row["path"].parent == root:
                row["dirty"] = bool(_git(row["path"], "status", "--porcelain").stdout.strip())
                row["merged"] = merged(repo, row["branch"])
                rows.append(row)
            row = {}
    return rows


def remove(repo: Path, path: Path) -> str:
    """Remove one worktree made here, then its branch. Returns what happened.

    A dirty worktree is refused: that is work nobody has looked at. The branch
    goes only when `merged` says its work is in HEAD — `-D`, since git itself
    does not recognise a squash merge. Otherwise it stays.
    """

    repo = _main(repo)
    row = next((r for r in worktrees(repo) if r["path"] == Path(path).resolve()), None)
    if row is None:
        raise ValueError(f"{worktree_home(repo)} 아래 작업트리가 아니다: {path}")
    if row["dirty"]:
        raise ValueError(f"커밋하지 않은 변경이 있다: {path}")
    done = _git(repo, "worktree", "remove", str(row["path"]))
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or f"git worktree remove 실패: {path}")
    if not row["branch"]:
        return "작업트리를 지웠다"
    if not row["merged"]:
        return f"작업트리를 지웠다. 브랜치 {row['branch']} 는 머지되지 않아 남겼다"
    done = _git(repo, "branch", "-D", row["branch"])
    if done.returncode:
        return f"작업트리를 지웠다. 브랜치 {row['branch']} 는 지우지 못했다: {done.stderr.strip()}"
    return f"작업트리와 브랜치 {row['branch']} 를 지웠다"
