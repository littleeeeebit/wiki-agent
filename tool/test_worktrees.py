"""Worktrees an agent writes in: made beside the repo, listed, cleared safely."""

import subprocess
import sys

import pytest

from workspace import create as make_workspace, remove, worktrees as list_workspaces


def create(repo, task):
    return make_workspace(repo, task, linked=True)


def worktrees(repo):
    return [row for row in list_workspaces(repo) if row["path"] != repo.resolve()]


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
    # nothing on it yet, so nothing would be lost
    assert worktrees(repo) == [{"path": path, "branch": "fix-login", "dirty": False, "merged": True}]
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


def test_a_deleted_remote_branch_is_not_a_merge(repo, tmp_path):
    """The upstream going away proves nothing: this lost a commit once."""

    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    path = create(repo, "unmerged")
    (path / "work.txt").write_text("precious")
    git(path, "add", "work.txt")
    git(path, "commit", "-q", "-m", "work")
    git(path, "push", "-q", "-u", "origin", "unmerged")
    git(repo, "push", "-q", "origin", "--delete", "unmerged")
    git(repo, "fetch", "-q", "--prune")
    assert not worktrees(repo)[0]["merged"]
    assert "남겼다" in remove(repo, path)
    assert "unmerged" in git(repo, "branch", "--list", "unmerged")


def test_remove_deletes_a_squash_merged_branch(repo):
    path = create(repo, "squashed")
    for name in ("a.txt", "b.txt"):
        (path / name).write_text(name)
        git(path, "add", name)
        git(path, "commit", "-q", "-m", name)
    assert not worktrees(repo)[0]["merged"]
    git(repo, "merge", "-q", "--squash", "squashed")   # what a squash merge on GitHub leaves
    git(repo, "commit", "-q", "-m", "squashed (#1)")
    assert worktrees(repo)[0]["merged"]
    assert "브랜치 squashed 를 지웠다" in remove(repo, path)
    assert not git(repo, "branch", "--list", "squashed").strip()


def test_a_squash_that_main_reverted_is_not_merged(repo):
    """The patch is in HEAD's history but not in HEAD: deleting loses it."""

    path = create(repo, "reverted")
    (path / "work.txt").write_text("precious\n")
    git(path, "add", "work.txt")
    git(path, "commit", "-q", "-m", "work")
    git(repo, "merge", "-q", "--squash", "reverted")
    git(repo, "commit", "-q", "-m", "squash")
    git(repo, "revert", "--no-edit", "HEAD")
    assert not worktrees(repo)[0]["merged"]
    assert "남겼다" in remove(repo, path)


def test_the_same_change_elsewhere_in_the_file_is_not_merged(repo):
    """Two identical blocks: the branch changed one, HEAD the other."""

    block = "".join(f"line {i}\n" for i in range(5)) + "old\n" + "".join(f"tail {i}\n" for i in range(5))
    (repo / "f.txt").write_text(block + "---\n" + block)
    git(repo, "add", "f.txt")
    git(repo, "commit", "-q", "-m", "blocks")
    path = create(repo, "first")
    (path / "f.txt").write_text(block.replace("old", "new") + "---\n" + block)
    git(path, "commit", "-q", "-am", "first block")
    (repo / "f.txt").write_text(block + "---\n" + block.replace("old", "new"))
    git(repo, "commit", "-q", "-am", "second block")
    assert not worktrees(repo)[0]["merged"]


def test_a_conflicted_merge_is_not_merged(repo):
    """The branch deletes a file HEAD modified. The conflicted merge keeps
    HEAD's version, so its tree equals HEAD's — only the exit code says no."""

    (repo / "f.txt").write_text("base\n")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-q", "-m", "base")
    path = create(repo, "delete")
    git(path, "rm", "-q", "f.txt")
    git(path, "commit", "-q", "-m", "delete")
    (repo / "f.txt").write_text("changed\n")
    git(repo, "commit", "-q", "-am", "change")
    assert not worktrees(repo)[0]["merged"]


def test_a_merge_driver_that_drops_the_branch_is_not_merged(repo):
    """`merge=ours` makes the merge clean and equal to HEAD while throwing the
    branch's change away. Nothing a merge driver does is proof."""

    (repo / ".gitattributes").write_text("f.txt merge=ours\n")
    (repo / "f.txt").write_text("base\n")
    git(repo, "config", "merge.ours.driver", "true")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    path = create(repo, "task")
    (path / "f.txt").write_text("branch only\n")
    git(path, "commit", "-q", "-am", "branch")
    (repo / "f.txt").write_text("head only\n")
    git(repo, "commit", "-q", "-am", "head")
    assert not worktrees(repo)[0]["merged"]


def test_a_rename_counts_the_old_path_too(repo):
    """The branch renamed a→b. HEAD copied a→b but kept a: the removal of a is
    the branch's alone, and rename detection would hide it."""

    (repo / "a.txt").write_text("same\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "a")
    path = create(repo, "rename")
    git(path, "mv", "a.txt", "b.txt")
    git(path, "commit", "-q", "-m", "rename")
    (repo / "b.txt").write_text("same\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "copy")
    assert not worktrees(repo)[0]["merged"]


def test_a_submodule_pointer_hidden_by_config_is_not_merged(repo):
    """`diff.ignoreSubmodules=all` makes porcelain `git diff` skip gitlinks."""

    path = create(repo, "sub")
    head = git(repo, "rev-parse", "HEAD").strip()
    git(path, "update-index", "--add", "--cacheinfo", f"160000,{head},sub")
    git(path, "commit", "-q", "-m", "submodule pointer")
    git(repo, "config", "diff.ignoreSubmodules", "all")
    assert not worktrees(repo)[0]["merged"]


def test_a_submodule_pointer_hidden_by_gitmodules_is_not_merged(repo):
    """`ignore = all` in `.gitmodules` hides a gitlink from `diff-tree` too."""

    (repo / ".gitmodules").write_text('[submodule "sub"]\n\tpath = sub\n\turl = ./sub\n\tignore = all\n')
    git(repo, "add", ".gitmodules")
    git(repo, "commit", "-q", "-m", "gitmodules")
    path = create(repo, "sub")
    head = git(repo, "rev-parse", "HEAD").strip()
    git(path, "update-index", "--add", "--cacheinfo", f"160000,{head},sub")
    git(path, "commit", "-q", "-m", "submodule pointer")
    assert not worktrees(repo)[0]["merged"]


def test_a_mode_change_alone_is_work(repo):
    (repo / "run.sh").write_text("echo hi\n")
    git(repo, "add", "run.sh")
    git(repo, "commit", "-q", "-m", "script")
    path = create(repo, "exec")
    git(path, "update-index", "--chmod=+x", "run.sh")
    git(path, "commit", "-q", "-m", "executable")
    assert not worktrees(repo)[0]["merged"]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows locks a process's current directory")
def test_a_folder_held_by_a_shell_still_loses_its_branch(repo):
    """The app's terminal sat in the worktree being cleared. Git forgot the
    worktree, failed to delete the folder, and the branch was left with nothing
    on the screen to clear it — the same task name could not be made again."""

    path = create(repo, "held")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=path)
    try:
        said = remove(repo, path)
    finally:
        holder.kill()
        holder.wait()
    assert not worktrees(repo)
    assert not git(repo, "branch", "--list", "held").strip()
    assert "남았다" in said
