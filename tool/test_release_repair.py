"""Release step 4: a failed attempt becomes an ordinary task in its own worktree at the pinned commit."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from common import worktree_home
from main import release_repair, specs, work

FAILURE = "빌드 실패 — 1 로 끝났다\nerror: cannot find symbol Foo"


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """`budget` with the commit a release pinned on `main`, and the person's checkout elsewhere: on an unrelated
    branch with a commit of its own and an edit not yet committed."""

    monkeypatch.setattr(specs, "SPECS", tmp_path / "specs")
    monkeypatch.setattr(specs, "gate_of", lambda repo, path=None: "python -m pytest -q")
    (repo := tmp_path / "budget").mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("pinned\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "pinned")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-qc", "feature/unrelated")
    (repo / "b.txt").write_text("feature\n", encoding="utf-8")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-qm", "feature")
    (repo / "a.txt").write_text("the person's edit\n", encoding="utf-8")
    monkeypatch.setattr(release_repair.channels, "repo_for", lambda name: repo if name == "budget" else None)
    begun = []

    def begin(path, chat, text, release, run, decide=False):
        begun.append((path, text))
        release()   # the turn would let go of its worktree when it ends
        return run

    monkeypatch.setattr(work, "forget", lambda path: None)
    monkeypatch.setattr(work, "session", lambda path, model, effort, fast=False: SimpleNamespace(id=None, parent_id=None))
    monkeypatch.setattr(work, "begin", begin)
    record = {"id": "20261118-120000-abcdef", "remote": "github.com/o/budget", "tag": "v0.1.1", "version": "0.1.1",
              "sha": sha, "base": "main", "repair": {"needed": FAILURE}}
    return repo, record, begun


def test_a_repair_task_starts_at_the_pinned_commit_in_its_own_worktree(repo):
    repo, record, begun = repo
    sid = release_repair.start(repo, record)
    spec = specs.load("budget", sid)
    tree = worktree_home(repo) / sid

    assert sid == "release-fix-0-1-1" and spec["worktree"] == str(tree) and begun == [(tree, "Start.")]
    assert git(tree, "rev-parse", "HEAD") == record["sha"] and git(tree, "branch", "--show-current") == sid
    assert not (tree / "b.txt").exists() and (tree / "a.txt").read_text(encoding="utf-8") == "pinned\n"
    assert spec["base"] == "main" and spec["start_head"] == record["sha"] and spec["state"] == "작업 중"
    # The person's checkout is where it was, with its edit, and not a task of anyone's.
    assert git(repo, "branch", "--show-current") == "feature/unrelated"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "the person's edit\n"
    assert specs.owner(tree)["id"] == sid and specs.owner(repo) is None
    assert str(tree) not in work._busy

    told = specs.brief(spec)   # what the agent reads
    assert "cannot find symbol Foo" in told and "v0.1.1" in told and "Version files" in told
    assert release_repair.start(repo, record) == "release-fix-0-1-1-2"   # a second failure, a second task


def test_a_token_in_the_failure_never_reaches_the_agent(repo):
    repo, record, _ = repo
    record["repair"]["needed"] = FAILURE + "\nauthorization: token ghp_" + "y" * 36
    told = specs.brief(specs.load("budget", release_repair.start(repo, record)))
    assert "ghp_" not in told and "cannot find symbol Foo" in told


def test_a_turn_that_cannot_start_leaves_the_task_named_in_its_worktree(repo, monkeypatch):
    repo, record, _ = repo

    def refused(*args, **kwargs):
        raise RuntimeError("the agent CLI is missing")

    monkeypatch.setattr(work, "session", refused)
    with pytest.raises(release_repair.Started) as stopped:
        release_repair.start(repo, record)
    assert stopped.value.task == "release-fix-0-1-1" and "CLI is missing" in str(stopped.value)
    tree = worktree_home(repo) / "release-fix-0-1-1"
    assert specs.load("budget", "release-fix-0-1-1")["worktree"] == str(tree) and str(tree) not in work._busy


@pytest.mark.parametrize("known", ["nothing", "another folder"])
def test_a_repository_the_server_does_not_know_gets_no_task(repo, monkeypatch, tmp_path, known):
    # The turn's end finds the task's project by name; unknown, the task would sit in `작업 중` forever.
    repo, record, _ = repo
    monkeypatch.setattr(release_repair.channels, "repo_for", lambda name: None if known == "nothing" else tmp_path)
    with pytest.raises(ValueError, match="아는 프로젝트가 아니다"):
        release_repair.start(repo, record)
    assert not (specs.SPECS / "budget").exists() and not worktree_home(repo).exists()


def test_a_repository_without_a_gate_gets_no_task(repo, monkeypatch):
    repo, record, _ = repo
    monkeypatch.setattr(specs, "gate_of", lambda repo, path=None: "")
    with pytest.raises(ValueError, match="gate_cmd"):
        release_repair.start(repo, record)
    assert not (specs.SPECS / "budget").exists() and not worktree_home(repo).exists()
