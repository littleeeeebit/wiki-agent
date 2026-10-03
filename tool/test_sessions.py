"""What has to stay true about `sessions.py`.

Finding a log used to live in `census.py`. These are the cases that made it
a module of its own: several worktrees of one repository, and worktrees that
are gone.

Nothing here touches a real session directory or a network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from workspace import sessions as S  # noqa: E402


def claude_log(folder: Path, name: str, cwd: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps({"type": "user", "cwd": str(cwd)}) + "\n", encoding="utf-8")
    return path


def rollout(day: Path, name: str, cwd: Path) -> Path:
    day.mkdir(parents=True, exist_ok=True)
    path = day / name
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(cwd)}}) + "\n",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------
# Which log belongs to which checkout
# --------------------------------------------------------------------------


def test_codex_sessions_are_not_only_under_the_default_home(tmp_path, monkeypatch):
    """Use only the explicitly selected and default account homes."""

    default = tmp_path / "home" / ".codex"
    account = tmp_path / "roaming" / "orca" / "codex-accounts" / "acc-1" / "home"
    moved = tmp_path / "moved"
    for root in (default, account, moved):
        (root / "sessions").mkdir(parents=True)

    monkeypatch.setattr(S.Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("CODEX_HOME", str(moved))

    found = S.codex_homes()

    assert moved / "sessions" in found          # CODEX_HOME wins, and comes first
    assert default / "sessions" in found        # the default is still read
    assert account / "sessions" not in found   # other applications' accounts are private
    assert found[0] == moved / "sessions"
    assert len(found) == len(set(found))        # a home named twice is read once


def test_a_cell_opened_in_a_subfolder_still_belongs_to_the_repo(tmp_path):
    """Cells get opened in `web/` all the time. `==` reports no session at all."""

    repo = (tmp_path / "repo").resolve()
    (repo / "web" / "src").mkdir(parents=True)

    assert S.under(repo, repo)
    assert S.under(repo / "web" / "src", repo)
    assert not S.under(repo.parent, repo)
    assert not S.under((tmp_path / "other").resolve(), repo)
    assert not S.under(None, repo)


def test_a_clone_inside_a_checkout_is_a_different_checkout(tmp_path):
    """Containment is not identity. A vendored clone is its own repository.

    A cell opened in `outer/vendor/inner` was counted as `outer`'s, so a
    mirror pointed at `outer` showed a conversation about `inner` — the same
    misattribution this whole change exists to stop, one directory deeper.
    """

    outer = (tmp_path / "outer").resolve()
    inner = outer / "vendor" / "inner"
    (inner / "src").mkdir(parents=True)
    (outer / ".git").mkdir()
    (inner / ".git").mkdir()

    assert not S.under(inner, outer)            # the clone itself
    assert not S.under(inner / "src", outer)    # and anything inside it
    assert S.under(inner, inner)
    assert S.under(inner / "src", inner)
    # A worktree's `.git` is a file, not a directory, and is just as much a
    # boundary — asking `is_dir()` here would have walked straight past it.
    parked = outer / "parked"
    parked.mkdir()
    (parked / ".git").write_text("gitdir: ../.git/worktrees/parked\n", encoding="utf-8")
    assert not S.under(parked, outer)


def test_two_worktrees_sharing_a_leaf_name_do_not_share_a_log(tmp_path):
    """`folder`'s fallback used to take the first name that matched the tail.

    Two repositories each had a worktree called `pollock`. Handing the first
    one found to whichever asked is how one repository's session shows up in
    another's mirror, and nothing on the screen says it happened.
    """

    root = tmp_path / "projects"
    claude_log(root / "C--a-pollock", "a.jsonl", tmp_path / "a" / "pollock")
    claude_log(root / "C--b-pollock", "a.jsonl", tmp_path / "b" / "pollock")
    wanted = tmp_path / "somewhere" / "pollock"
    wanted.mkdir(parents=True)

    found = S.folder(wanted, root)

    assert not found.is_dir()               # better nothing than the wrong one
    assert found.name.endswith("-pollock")  # it is still the name it would have


def test_the_tail_fallback_answers_when_the_log_says_it_belongs(tmp_path):
    """The fallback exists for an environment where the flattening differs.

    Two candidates share the tail. The log's own `cwd` is what picks between
    them — being the only match would not have been evidence of anything.
    """

    root = tmp_path / "projects"
    project = tmp_path / "elsewhere" / "demo"
    project.mkdir(parents=True)
    claude_log(root / "flattened-another-way-demo", "a.jsonl", tmp_path / "other" / "demo")
    mine = root / "flattened-some-way-demo"
    claude_log(mine, "a.jsonl", project / "web")  # a cell opened in a subfolder

    assert S.folder(project, root) == mine


def test_a_lone_candidate_that_belongs_to_another_checkout_is_not_taken(tmp_path):
    """Being the only `-demo` directory on the machine is not evidence.

    `C:\\old\\demo` is gone from nobody's disk but the caller is asking about
    `D:\\new\\demo`. Handing the one match over gave the census and the retro
    another repository's conversation to read, and both reported on it as
    though it were this one's.
    """

    root = tmp_path / "projects"
    claude_log(root / "C--old-demo", "a.jsonl", tmp_path / "old" / "demo")
    asked = tmp_path / "new" / "demo"
    asked.mkdir(parents=True)

    found = S.folder(asked, root)

    assert not found.is_dir()   # no log for this checkout, and it says so
    assert found.parent == root


def test_two_checkouts_that_flatten_to_one_directory_are_kept_apart(tmp_path):
    """The flattening is lossy, and the host writes both of them here.

    `C:\\a-b` and `C:\\a\\b` both become `C--a-b`. Reading that directory whole
    hands the census and the retro two repositories' conversations as one
    project's, and a listing that reads only the newest log offers whichever
    of the two spoke last.
    """

    root = tmp_path / "projects"
    hyphen = tmp_path / "x-y"       # one checkout
    nested = tmp_path / "x" / "y"   # and another
    hyphen.mkdir()
    nested.mkdir(parents=True)
    assert S.folder(hyphen, root).name == S.folder(nested, root).name  # one directory

    shared = S.folder(hyphen, root)
    mine = claude_log(shared, "mine.jsonl", hyphen)
    theirs = claude_log(shared, "theirs.jsonl", nested)
    os.utime(mine, (0, 1_000_000))
    os.utime(theirs, (0, 2_000_000))  # the other checkout spoke most recently

    assert S.logs(hyphen, root) == [mine]
    assert S.logs(nested, root) == [theirs]

    with patch.object(S, "SESSIONS", root):
        assert {row["path"] for row in S.checkouts("claude")} == {
            str(hyphen.resolve()), str(nested.resolve()),
        }


def test_a_candidate_proves_itself_with_any_log_not_only_the_newest(tmp_path):
    """A session opened a moment ago has not written its `cwd` record yet.

    Judging the candidate on that one file alone rejected the directory and
    hid every earlier log in it that did belong.
    """

    root = tmp_path / "projects"
    project = tmp_path / "elsewhere" / "demo"
    project.mkdir(parents=True)
    candidate = root / "flattened-another-way-demo"
    old = claude_log(candidate, "old.jsonl", project)
    blank = candidate / "just-opened.jsonl"
    blank.write_text("", encoding="utf-8")   # no records yet
    os.utime(old, (0, 1_000_000))
    os.utime(blank, (0, 2_000_000))

    assert S.folder(project, root) == candidate
    assert S.logs(project, root) == [old]   # the empty one belongs to nobody


def test_a_candidate_with_no_log_at_all_proves_nothing(tmp_path):
    root = tmp_path / "projects"
    (root / "flattened-another-way-demo").mkdir(parents=True)
    project = tmp_path / "elsewhere" / "demo"
    project.mkdir(parents=True)

    assert not S.folder(project, root).is_dir()


# --------------------------------------------------------------------------
# Which repository a checkout is of
# --------------------------------------------------------------------------


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_a_worktree_says_which_repository_it_is_of(tmp_path):
    """The one question the directory name cannot answer.

    `--git-common-dir` is the main clone's `.git` seen from any worktree of
    it, so two checkouts of one repository come back with the same answer
    however their directories are named.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit",
        "--allow-empty", "-m", "one")
    git(repo, "worktree", "add", "-b", "side", str(tmp_path / "barb"))

    top, root, branch = S.checkout(repo)
    assert Path(top) == repo.resolve()
    assert Path(root) == repo.resolve()
    assert branch == "main"

    top, root, branch = S.checkout(tmp_path / "barb")
    assert Path(top) == (tmp_path / "barb").resolve()   # its own root
    assert Path(root) == repo.resolve()                 # but the same repository
    assert branch == "side"

    # A cell opened inside the worktree answers with the worktree, which is
    # what stops `web/` becoming an entry of its own.
    (tmp_path / "barb" / "web").mkdir()
    top, root, _ = S.checkout(tmp_path / "barb" / "web")
    assert Path(top) == (tmp_path / "barb").resolve()
    assert Path(root) == repo.resolve()


def test_a_clone_with_no_commit_is_still_a_repository(tmp_path):
    """`rev-parse` answers one of the two questions and exits non-zero anyway.

    Demanding exit 0 threw away the answer git had already printed, and a
    freshly initialised repo came out looking like it was not a repo at all.
    """

    repo = tmp_path / "fresh"
    repo.mkdir()
    git(repo, "init", "-b", "main")

    top, root, branch = S.checkout(repo)

    assert Path(top) == repo.resolve()
    assert Path(root) == repo.resolve()
    assert branch == ""  # nothing is checked out yet, and `HEAD` is not a name


def test_a_directory_that_is_not_a_checkout_says_so(tmp_path):
    assert S.checkout(tmp_path) == ("", "", "")


# --------------------------------------------------------------------------
# The listing
# --------------------------------------------------------------------------


def test_every_checkout_with_a_session_is_offered(tmp_path, monkeypatch):
    """The picker's whole list. A checkout appears once, under its latest session."""

    work, other = tmp_path / "work", tmp_path / "other"
    for repo in (work, other):
        repo.mkdir()
        git(repo, "init", "-b", "main")
    (work / "web").mkdir()

    day = tmp_path / "logs" / "2026" / "09" / "22"
    stale = rollout(day, "rollout-a.jsonl", work)
    fresh = rollout(day, "rollout-b.jsonl", work / "web")  # a cell in a subfolder
    rollout(day, "rollout-c.jsonl", other)
    os.utime(stale, (0, 1_000_000))       # the repo's own session is the older one
    os.utime(fresh, (0, 2_000_000))
    monkeypatch.setattr(S, "codex_homes", lambda: [tmp_path / "logs"])

    rows = {row["path"]: row for row in S.checkouts("codex")}

    # `work/web` is not a third checkout. A cell in a subfolder belongs to the
    # repo above it (`under`), and each rollout is placed by its own `cwd`.
    assert set(rows) == {str(work.resolve()), str(other.resolve())}
    assert all(row["name"] for row in rows.values())
    # Merged onto the newest of the two, not whichever the walk reached last.
    # `at` is what sorts the picker and what the screen falls back to, so the
    # repo worked in five minutes ago must not sink below one left last week.
    assert rows[str(work.resolve())]["at"] == 2_000_000


def test_a_deleted_worktree_leaves_the_listing_when_it_leaves_the_disk(
    tmp_path, monkeypatch
):
    """Deleting a worktree does not delete what the host wrote about it.

    Twelve of twenty-nine rows in the picker were checkouts that no longer
    existed, each still offering to mirror a session that could never say
    another word.
    """

    day = tmp_path / "2026" / "09" / "22"
    here, gone = tmp_path / "here", tmp_path / "gone"
    rollout(day, "rollout-a.jsonl", here)
    rollout(day, "rollout-b.jsonl", gone)
    here.mkdir()  # `gone` is never created — that is the point
    monkeypatch.setattr(S, "codex_homes", lambda: [tmp_path])

    assert {row["path"] for row in S.checkouts("codex")} == {str(here)}


def test_a_deleted_subfolder_keeps_the_checkout_it_sat_in(tmp_path):
    """`web/` goes, the repository above it does not.

    Testing the recorded path alone dropped that repository's only session —
    the row vanished from the picker although the checkout was right there.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit",
        "--allow-empty", "-m", "one")
    (repo / "web").mkdir()

    day = tmp_path / "logs" / "2026" / "09" / "22"
    rollout(day, "rollout-a.jsonl", repo / "web")   # the only session
    shutil.rmtree(repo / "web")

    with patch.object(S, "codex_homes", lambda: [tmp_path / "logs"]):
        rows = S.checkouts("codex")

    assert [row["path"] for row in rows] == [str(repo.resolve())]
    assert rows[0]["branch"] == "main"


def test_a_deleted_checkout_with_no_repository_above_it_is_still_dropped(tmp_path):
    """The climb stops at the first directory that exists, and git decides.

    A scratchpad under `Temp` has a living ancestor too. Keeping a row because
    *something* above the gone directory is on disk would put `Temp` in the
    picker.
    """

    scratch = tmp_path / "scratch" / "run-1"
    (tmp_path / "scratch").mkdir()      # the parent lives, but it is no checkout
    day = tmp_path / "logs" / "2026" / "09" / "22"
    rollout(day, "rollout-a.jsonl", scratch)

    with patch.object(S, "codex_homes", lambda: [tmp_path / "logs"]):
        assert S.checkouts("codex") == []


def test_the_listing_carries_the_repository_each_checkout_is_of(tmp_path, monkeypatch):
    """Several worktrees of one repository, told apart by branch, not by name."""

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit",
        "--allow-empty", "-m", "one")
    git(repo, "worktree", "add", "-b", "side", str(tmp_path / "barb"))

    day = tmp_path / "logs" / "2026" / "09" / "22"
    rollout(day, "rollout-a.jsonl", repo)
    rollout(day, "rollout-b.jsonl", tmp_path / "barb")
    monkeypatch.setattr(S, "codex_homes", lambda: [tmp_path / "logs"])

    rows = {row["path"]: row for row in S.checkouts("codex")}

    assert len(rows) == 2
    assert {row["repo"] for row in rows.values()} == {str(repo.resolve())}
    assert rows[str(repo)]["branch"] == "main"
    assert rows[str(tmp_path / "barb")]["branch"] == "side"
    assert rows[str(tmp_path / "barb")]["repoName"] == "repo"
