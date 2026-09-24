"""Worktrees an agent writes in: made beside the repo, listed, cleared safely."""

import subprocess

import pytest

from workspace import create, remove, worktrees


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "t")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t@example.com")
    path = tmp_path / "demo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "commit", "-q", "--allow-empty", "-m", "start")
    return path


def test_create_puts_the_worktree_beside_the_repo(repo):
    path = create(repo, "fix-login")
    assert path == (repo.parent / "demo-worktrees" / "fix-login").resolve()
    assert git(path, "branch", "--show-current").strip() == "fix-login"
    assert worktrees(repo) == [{"path": path, "branch": "fix-login", "dirty": False, "gone": False}]
    (path / "a.txt").write_text("x")
    assert worktrees(repo)[0]["dirty"]


def test_create_refuses_bad_names_and_worktrees(repo):
    for name in ("Fix", "로그인", "../up", "-x", "a" * 65, ""):
        with pytest.raises(ValueError):
            create(repo, name)
    inner = create(repo, "one")
    with pytest.raises(ValueError):
        create(inner, "two")  # a worktree is not where worktrees are made
    with pytest.raises(RuntimeError):
        create(repo, "one")   # the branch exists already


def test_remove_keeps_unreviewed_work(repo):
    path = create(repo, "task")
    (path / "a.txt").write_text("x")
    with pytest.raises(ValueError):
        remove(repo, path)
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "work")
    # Committed but not merged, and nothing says it was: the branch stays.
    assert "남겼다" in remove(repo, path)
    assert not path.exists()
    assert "task" in git(repo, "branch", "--list", "task")
    with pytest.raises(ValueError):
        remove(repo, repo)    # only what is under the worktrees folder


def test_remove_deletes_a_branch_whose_upstream_is_gone(repo, tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    path = create(repo, "merged")
    git(path, "commit", "-q", "--allow-empty", "-m", "work")
    git(path, "push", "-q", "-u", "origin", "merged")
    git(repo, "push", "-q", "origin", "--delete", "merged")   # what a squash merge leaves
    git(repo, "fetch", "-q", "--prune")
    assert worktrees(repo)[0]["gone"]
    assert "브랜치 merged 를 지웠다" in remove(repo, path)
    assert not git(repo, "branch", "--list", "merged").strip()
