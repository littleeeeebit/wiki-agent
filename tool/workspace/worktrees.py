"""Task branches in the selected checkout, plus explicitly isolated review trees.

Implementation tasks reuse the repository directory. Linked worktrees remain
available for independent reviewers and for tasks created by older versions.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from common import worktree_home

from .sessions import checkout

# A task name is both a branch and a directory. Lowercase ASCII only: a Korean
# directory name in a path has broken `subprocess` decoding on this machine.
TASK = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def base_branch(repo: Path, base: str) -> str:
    """A checkout-local base ref when the real base is open in a sibling."""
    key = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"wiki-base/{key}/{base}"


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


def create(repo: Path, task: str, *, linked: bool = False, base: str = "") -> Path:
    """Create a task branch in the selected checkout; linked checkouts are explicit."""

    top, _, _ = checkout(Path(repo))
    if not top or Path(top) != Path(repo).resolve():
        raise ValueError(f"저장소 루트가 아니다: {repo}")
    repo = Path(top)
    if not TASK.fullmatch(task):
        raise ValueError(f"작업 이름은 소문자·숫자·- 만, 64자까지: {task!r}")
    if not linked:
        clean = _git(repo, "status", "--porcelain")
        if clean.returncode or clean.stdout.strip():
            raise ValueError("브랜치를 만들기 전에 현재 변경을 커밋하거나 보관해라. 변경은 그대로 남겼다")
        done = _git(repo, "switch", "-c", task, *([base] if base else []))
        if done.returncode:
            raise RuntimeError(done.stderr.strip() or f"git switch 실패: {task}")
        return repo
    repo = _main(repo)
    path = worktree_home(repo) / task
    done = _git(repo, "worktree", "add", "-b", task, str(path))
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or f"git worktree add 실패: {task}")
    return path


def folder_for(branch: str) -> str:
    """A branch name as a task name: lowercase, anything else `-`. Empty when
    nothing usable is left."""

    name = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", branch.lower())).strip("-")[:64].rstrip("-")
    return name if TASK.fullmatch(name) else ""


def adopt(repo: Path, branch: str, oid: str, detached: bool = False) -> Path:
    """A worktree on a pull request's existing branch, standing on `oid`.

    `create` starts a new branch from HEAD, and a review cell there would read
    code other than the pull request's. Here the branch is fetched and checked
    out as it is; the folder takes the branch's name in task shape.

    A local branch of that name is common — a pull request opened by hand
    leaves one behind. At `oid` it is used; anywhere else it is refused, not
    moved: it may hold commits that exist nowhere else. A branch made here
    that does not land on `oid` is taken away again, worktree and all.
    A detached external review uses `oid` without touching a local branch.
    """

    repo = _main(repo)
    task = folder_for(branch)
    if not task or not re.fullmatch(r"[0-9a-f]{40}", oid):
        raise ValueError(f"받을 수 없는 브랜치다: {branch!r}")
    fetched = _git(repo, "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    if fetched.returncode:
        raise RuntimeError(fetched.stderr.strip() or f"git fetch 실패: {branch}")
    path = worktree_home(repo) / task
    local = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").stdout.strip()
    if local and local != oid and not detached:
        raise RuntimeError(f"로컬 `{branch}` 가 PR 머리와 다르다 — 로컬 {local[:12]}, PR {oid[:12]}")
    args = ["worktree", "add", "--detach", str(path), oid] if detached else \
        ["worktree", "add", str(path), branch] if local else \
        ["worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}"]
    done = _git(repo, *args)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or f"git worktree add 실패: {branch}")
    head = _git(path, "rev-parse", "HEAD").stdout.strip()
    if head != oid:
        if detached or not local:
            _git(repo, "worktree", "remove", "--force", str(path))
            if not detached:
                _git(repo, "branch", "-D", branch)
        raise RuntimeError(f"받은 작업트리가 PR 머리에 서지 않았다 — {head[:12]}, PR {oid[:12]}")
    return path


def _tree(repo: Path, rev: str) -> dict[bytes, bytes] | None:
    """Every entry of a commit's tree: path -> mode and object id. `None` if git fails.

    Read, not diffed. Every way git has of diffing reads some setting that
    can hide a path — `diff.renames`, `diff.ignoreSubmodules`,
    `submodule.<name>.ignore`, `ignore = all` in `.gitmodules` — and each of
    those hid a change the branch alone held. `ls-tree` lists every entry,
    gitlinks included, and no setting filters it.
    """

    done = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", rev],
                          capture_output=True, timeout=60)
    if done.returncode:
        return None
    entries = {}
    for line in done.stdout.split(b"\0"):
        if line:
            meta, path = line.split(b"\t", 1)
            mode, _, oid = meta.split(b" ")
            entries[path] = mode + b" " + oid
    return entries


def merged(repo: Path, branch: str) -> bool:
    """Would deleting `branch` lose anything the original checkout's HEAD lacks?

    True when every path the branch changed since its merge base holds, in
    HEAD right now, exactly the branch's version — the same mode and object,
    or absent in both. A squash merge — how this repository merges — passes,
    as does a merge or a branch with no changes of its own.

    Three trees read and compared here, nothing asked of git beyond listing
    them. Every earlier reading deleted a commit that existed nowhere else,
    because each let git decide what counts as a change:

    - the upstream being gone: a remote branch can be deleted unmerged
    - `git cherry`: finds the patch in HEAD's history after HEAD reverted it
    - reversing the diff onto HEAD: `git apply` matches the same lines at
      another place in the file
    - merging into HEAD (`merge-tree`): a merge driver such as `merge=ours`
      drops the branch's side cleanly, and a modify/delete conflict keeps
      HEAD's version
    - diffing (`git diff`, then `diff-tree`): settings hide submodule
      pointers, and rename detection folds away the removal of the old path

    Unsure is `False` — HEAD not yet pulled, or HEAD having changed one of
    those files again since: a kept branch costs a line in a listing; a wrong
    `True` costs work.
    """

    if not branch:
        return False
    base = _git(repo, "merge-base", "HEAD", branch).stdout.strip()
    if not base:
        return False
    old, ours, head = _tree(repo, base), _tree(repo, branch), _tree(repo, "HEAD")
    if old is None or ours is None or head is None:
        return False
    return all(head.get(path) == ours.get(path)
               for path in old.keys() | ours.keys() if old.get(path) != ours.get(path))


def worktrees(repo: Path) -> list[dict]:
    """The selected checkout and legacy task trees: path, branch, dirty, merged."""

    top, _, _ = checkout(Path(repo))
    if not top or Path(top) != Path(repo).resolve():
        raise ValueError(f"저장소 루트가 아니다: {repo}")
    repo = Path(top)
    root = worktree_home(repo)
    rows, row = [], {}
    for line in [*_git(repo, "worktree", "list", "--porcelain").stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            row = {"path": Path(line[9:]).resolve(), "branch": ""}
        elif line.startswith("branch "):
            row["branch"] = line[7:].removeprefix("refs/heads/")
        elif not line and row:
            if row["path"] == repo or row["path"].parent == root:
                row["dirty"] = bool(_git(row["path"], "status", "--porcelain").stdout.strip())
                row["merged"] = row["path"] != repo and merged(repo, row["branch"])
                rows.append(row)
            row = {}
    return rows


def remove(repo: Path, path: Path, force: bool = False) -> str:
    """Remove one worktree made here, then its branch. Returns what happened.

    A dirty worktree is refused: that is work nobody has looked at — unless
    `force`, a person's explicit delete, which drops it. The branch goes only
    when `merged` says its work is in HEAD — `-D`, since git itself does not
    recognise a squash merge. Otherwise it stays, `force` or not.
    """

    if Path(path).resolve() == Path(repo).resolve():
        raise ValueError("원본 저장소는 삭제하지 않는다. 작업 브랜치는 Git에서 관리해라")
    repo = _main(repo)
    row = next((r for r in worktrees(repo) if r["path"] == Path(path).resolve()), None)
    if row is None:
        raise ValueError(f"{worktree_home(repo)} 아래 작업트리가 아니다: {path}")
    if row["dirty"] and not force:
        raise ValueError(f"커밋하지 않은 변경이 있다: {path}")
    done = _git(repo, "worktree", "remove", *(["--force"] if force else []), str(row["path"]))
    left = ""
    if done.returncode:
        # On Windows a process whose current directory is in there — a shell,
        # an editor — keeps the folder. Git has already forgotten the worktree
        # by then, so stopping here strands the branch where nothing lists it.
        if any(r["path"] == row["path"] for r in worktrees(repo)):
            raise RuntimeError(done.stderr.strip() or f"git worktree remove 실패: {path}")
        left = f" 폴더는 다른 프로세스가 쥐고 있어 남았다: {row['path']}"
    if not row["branch"]:
        return "작업트리를 지웠다" + left
    if not row["merged"]:
        return f"작업트리를 지웠다. 브랜치 {row['branch']} 는 머지되지 않아 남겼다" + left
    done = _git(repo, "branch", "-D", row["branch"])
    if done.returncode:
        return f"작업트리를 지웠다. 브랜치 {row['branch']} 는 지우지 못했다: {done.stderr.strip()}" + left
    return f"작업트리와 브랜치 {row['branch']} 를 지웠다" + left
