"""worktrees — where an agent is allowed to write.

Every write happens in a worktree made here, never in the checkout the person
works in. They all sit beside the repository, in `../<repo>-worktrees/<task>`,
so what is the program's and what is the person's is one directory apart.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .sessions import checkout

# A task name is both a branch and a directory. Lowercase ASCII only: a Korean
# directory name in a path has broken `subprocess` decoding on this machine.
TASK = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def home(repo: Path) -> Path:
    repo = Path(repo).resolve()
    return repo.parent / f"{repo.name}-worktrees"


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
    path = home(repo) / task
    done = _git(repo, "worktree", "add", "-b", task, str(path))
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or f"git worktree add 실패: {task}")
    return path


def worktrees(repo: Path) -> list[dict]:
    """The worktrees under `home(repo)`: `path`, `branch`, `dirty`, `gone`.

    `gone` is the upstream branch having been deleted. This repository merges
    by squash, so `git branch --merged` never sees a merge; the remote branch
    being deleted afterwards is the sign one happened.
    """

    repo = _main(repo)
    root = home(repo)
    gone = {line.split(" ", 1)[0] for line in _git(
        repo, "for-each-ref", "--format=%(refname:short) %(upstream:track)", "refs/heads").stdout.splitlines()
        if line.endswith("[gone]")}
    rows, row = [], {}
    for line in [*_git(repo, "worktree", "list", "--porcelain").stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            row = {"path": Path(line[9:]).resolve(), "branch": ""}
        elif line.startswith("branch "):
            row["branch"] = line[7:].removeprefix("refs/heads/")
        elif not line and row:
            if row["path"].parent == root:
                row["dirty"] = bool(_git(row["path"], "status", "--porcelain").stdout.strip())
                row["gone"] = row["branch"] in gone
                rows.append(row)
            row = {}
    return rows


def remove(repo: Path, path: Path) -> str:
    """Remove one worktree made here, then its branch. Returns what happened.

    A dirty worktree is refused: that is work nobody has looked at. The branch
    goes with `-D` when its upstream is gone (merged), otherwise with `-d`,
    which git refuses for unmerged work — then the branch stays.
    """

    repo = _main(repo)
    row = next((r for r in worktrees(repo) if r["path"] == Path(path).resolve()), None)
    if row is None:
        raise ValueError(f"{home(repo)} 아래 작업트리가 아니다: {path}")
    if row["dirty"]:
        raise ValueError(f"커밋하지 않은 변경이 있다: {path}")
    done = _git(repo, "worktree", "remove", str(row["path"]))
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or f"git worktree remove 실패: {path}")
    if not row["branch"]:
        return "작업트리를 지웠다"
    done = _git(repo, "branch", "-D" if row["gone"] else "-d", row["branch"])
    if done.returncode:
        return f"작업트리를 지웠다. 브랜치 {row['branch']} 는 머지되지 않아 남겼다"
    return f"작업트리와 브랜치 {row['branch']} 를 지웠다"
