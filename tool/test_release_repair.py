"""Release step 4: a failed attempt becomes an ordinary task in its own worktree at the pinned commit."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from common import host, worktree_home
from main import release_repair, specs, work

FAILURE = "빌드 실패 — 1 로 끝났다\nerror: cannot find symbol Foo"
SESSION = work.session   # the fixture replaces it


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """`budget` with the commit a release pinned on `main`, and the person's checkout elsewhere: on an unrelated
    branch with a commit of its own and an edit not yet committed."""

    monkeypatch.setattr(specs, "SPECS", tmp_path / "specs")
    # The pinned worktree's gate, read there: the person's branch may have another, or none.
    monkeypatch.setattr(specs, "gate_of", lambda repo, path=None: "python -m pytest -q" if path and path != repo else "")
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
        begun.append((path, text, str(path) in work._busy))   # the turn starts holding its worktree
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

    assert sid == "release-fix-0-1-1" and spec["worktree"] == str(tree) and begun == [(tree, "Start.", True)]
    assert spec["done"] == ["python -m pytest -q"] and spec["return_branch"] == "main"
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
    assert left_nothing(repo)


def left_nothing(repo, name="release-fix-0-1-1") -> bool:
    """No spec, worktree, branch or hold of a task that was never saved."""
    return (not (specs.SPECS / "budget").exists() and not (worktree_home(repo) / name).exists()
            and not git(repo, "branch", "--list", name) and str(worktree_home(repo) / name) not in work._busy)


@pytest.mark.parametrize("failure", ["no gate at the pinned commit", "the spec cannot be saved"])
def test_a_task_that_cannot_be_saved_leaves_no_worktree_or_branch(repo, monkeypatch, failure):
    repo, record, begun = repo
    if failure == "no gate at the pinned commit":
        monkeypatch.setattr(specs, "gate_of", lambda repo, path=None: "pytest" if path in (None, repo) else "")
    else:
        monkeypatch.setattr(specs, "save", lambda spec: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises((ValueError, OSError), match="gate_cmd|disk full"):
        release_repair.start(repo, record)
    assert left_nothing(repo) and begun == []


def test_a_path_another_request_holds_is_never_made_or_removed(repo):
    repo, record, begun = repo
    planned = str(worktree_home(repo) / "release-fix-0-1-1")
    other = specs.hold(work._busy, specs._lock, planned, "busy")   # e.g. an ordinary work request on that path
    try:
        with pytest.raises(specs.HTTPException):
            release_repair.start(repo, record)
        assert planned in work._busy and begun == []   # still the other request's
        assert not (worktree_home(repo) / "release-fix-0-1-1").exists()
        assert not git(repo, "branch", "--list", "release-fix-0-1-1") and not (specs.SPECS / "budget").exists()
    finally:
        other()


@pytest.mark.parametrize("left", ["the branch is checked out elsewhere", "the ref cannot be deleted"])
def test_what_a_rollback_cannot_remove_is_reported_with_the_failure(repo, monkeypatch, tmp_path, left):
    repo, record, _ = repo
    run = release_repair.subprocess.run

    def unsaved(spec):
        raise OSError("disk full")

    def refusing(argv, **kwargs):
        if left == "the ref cannot be deleted" and "update-ref" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="cannot lock ref")
        if left == "the branch is checked out elsewhere" and "list" in argv:   # another checkout stands on it
            return SimpleNamespace(returncode=0, stdout=f"worktree {tmp_path}/elsewhere\nHEAD {record['sha']}\n"
                                                        "branch refs/heads/release-fix-0-1-1\n", stderr="")
        return run(argv, **kwargs)

    monkeypatch.setattr(specs, "save", unsaved)
    monkeypatch.setattr(release_repair, "subprocess", SimpleNamespace(run=refusing))   # its own calls only
    with pytest.raises(RuntimeError, match="disk full — 정리하지 못한 것: 브랜치 release-fix-0-1-1"):
        release_repair.start(repo, record)
    assert not (worktree_home(repo) / "release-fix-0-1-1").exists()
    assert git(repo, "branch", "--list", "release-fix-0-1-1")   # kept, and said so


def test_a_linked_checkout_chosen_as_the_project_is_refused_before_anything_is_made(repo, monkeypatch, tmp_path):
    repo, record, _ = repo
    linked = tmp_path / "budget-linked"
    git(repo, "worktree", "add", "-q", "--detach", str(linked), record["sha"])
    monkeypatch.setattr(release_repair.channels, "repo_for", lambda name: linked)
    with pytest.raises(ValueError, match="연결된 작업트리"):
        release_repair.start(linked, {**record})
    assert left_nothing(repo)


def test_the_repair_session_never_inherits_the_servers_github_tokens(repo, monkeypatch):
    repo, record, _ = repo
    tree = worktree_home(repo) / release_repair.start(repo, record)
    made = []

    class Chat(SimpleNamespace):
        is_codex = False

        def __init__(self, path, env=None, **kwargs):
            super().__init__(env=env)
            made.append(host.environment(env))

        def reconfigure(self, model, effort):
            pass

    monkeypatch.setattr(work, "ChatSession", Chat)
    monkeypatch.setenv("GH_TOKEN", "server-token")
    work._sessions.pop(str(tree), None)
    try:
        SESSION(tree, "", "", False)   # also how a session is made again when work resumes from the screen
    finally:
        work._sessions.pop(str(tree), None)
    assert made and not any(k in made[0] for k in host.TOKENS)
    assert "server-token" not in made[0].values()
