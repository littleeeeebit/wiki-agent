"""sessions — which session log belongs to which checkout.

Both hosts write one log per cell, and three things here need to find one: the
census counting what went wrong, the transcript feeding a retro, and the mirror
tailing a cell that is running right now. That last one is a live screen, and
it was importing `census` — the report generator — to learn where a log lives.
This module is that knowledge standing on its own, so the screen and the report
share only the thing they actually share.

**A checkout is not a repository.** `git worktree` gives one repository
several, and on this machine most of them are Orca's, under
`~/orca/workspaces/<repo>/<name>`. The leaf name is not an identifier — two
different repositories both had a worktree called `pollock` — so which
repository a checkout belongs to is read out of git, and nothing here infers it
from the shape of a path.

**A log outlives its checkout.** Deleting a worktree does not delete what the
host wrote about it, so a listing built from the log directory alone keeps
offering checkouts that are not on disk any more. Twelve of twenty-nine rows
were that. Anything that is gone is dropped here, once, rather than in each
caller.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# Where Claude Code writes, one directory per checkout.
SESSIONS = Path.home() / ".claude" / "projects"

# How many rollout files back to look before giving up on finding a repo's
# Codex cell. They are one per session and dated, so the answer is always in
# the newest handful unless the cell has been idle for weeks.
CODEX_DEPTH = 60

# Things that arrive in the conversation log in the same place as a person's
# utterance without a person having typed them. The harness injects these, so
# the list has nothing to do with language.
INJECTED = (
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "<system-reminder>",
    "<task-notification>",
    "Caveat: The messages below were generated",
    "[Request interrupted",
    "API Error",
    "Base directory for this skill:",
    "You have access to browser automation tools",
    "Goal check-in:",
    "is still active, and evaluation has been deferred",
    "This session is being continued from a previous",
    "A session-scoped Stop hook is now active",
    # Skill bodies. They arrive as the same record type as a person's
    # utterance and they are long, so left in they take the whole of "the most
    # re-entered instruction". Which is what happened.
    "Approach this as the design lead",
    "Use this skill whenever you are about to create",
    "PONYTAIL MODE ACTIVE",
)

# The list above is always behind — one more skill and one more body leaks
# through. So there is a length filter as well. A person rarely types this
# much at once, and the rare time they do it is usually a pasted log rather
# than an instruction.
MAX_HUMAN_CHARS = 20_000


def parse(line: str) -> dict | None:
    """A JSONL line, or `None` for anything that is not one.

    The last line of a file being written is routinely half a record. It comes
    back `None` here and arrives whole on the next read.
    """

    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except Exception:
        return None
    return record if isinstance(record, dict) else None


def living(path: Path) -> Path | None:
    """The nearest directory at or above `path` that is still on disk.

    A cell records where it was opened, which is routinely a subdirectory. That
    subdirectory can go while the checkout holding it stays, and then the path
    in the log names nothing although the work it describes is still there.
    """

    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return candidate
    return None


def newest(paths) -> Path | None:
    def when(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return -1.0

    return max(paths, key=when, default=None)


# --------------------------------------------------------------------------
# Which repository a checkout belongs to
# --------------------------------------------------------------------------


def git(path: Path, *args: str) -> list[str]:
    """`git -C path ...`, as whatever it managed to print. Empty for a non-repo.

    The exit code is not the test. `rev-parse` asked two questions at once
    answers the one it can and fails on the one it cannot: a clone with no
    commit yet prints its `--git-common-dir` and *then* exits 128 over `HEAD`.
    Demanding a zero threw away the answer that was right there, and the repo
    came out looking like it was not a repo at all.

    The encoding is named. A checkout with a Korean directory name in its path
    decodes as cp949 by default on this machine and raises inside
    `subprocess`'s reader thread, where no caller can catch it.
    """

    try:
        done = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return done.stdout.strip().splitlines()


def checkout(path: Path) -> tuple[str, str, str]:
    """`(this checkout's own root, the repository it is of, its branch)`.

    Two different roots, and the difference is the whole point. `--show-toplevel`
    is *this* checkout — a cell opened in `web/` answers with the worktree
    above it, which is how a subdirectory stops being a separate entry.
    `--git-common-dir` is the main clone's `.git` seen from any worktree of it,
    which is the one thing that answers "the same repository" without a
    registry to consult — and the registries disagree: `git worktree list`
    covers only what this clone knows, Orca's covers the machine.

    The order of the three is not cosmetic. `rev-parse` answers in argument
    order and stops at the first it cannot, so a clone with no commit yet
    prints both paths and only then fails on `HEAD`.

    `("", "", "")` for a directory git does not answer for. A scratchpad folder
    and `C:\\Windows` both turn up in the log directory, and neither is a
    checkout.
    """

    lines = git(path, "rev-parse", "--path-format=absolute",
                "--show-toplevel", "--git-common-dir", "--abbrev-ref", "HEAD")
    if not lines:
        return "", "", ""
    # git answers in its own spelling — forward slashes on Windows. These get
    # compared against paths the hosts wrote into their logs and shown on a
    # screen next to them, so they come back in the one form.
    top = str(Path(lines[0]).resolve())
    repo = ""
    if len(lines) > 1:
        common = Path(lines[1]).resolve()
        repo = str(common.parent if common.name == ".git" else common)
    # `HEAD` is what `--abbrev-ref` says for a detached or still-unborn one.
    # That is not a branch name, and printing it next to a repository reads as
    # though the checkout were on a branch called HEAD.
    branch = lines[2] if len(lines) > 2 and lines[2] != "HEAD" else ""
    return top, repo, branch


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------


def folder(project: Path, root: Path | None = None) -> Path:
    """The log directory for a checkout.

    `root` defaults to `SESSIONS` when it is called, not when it is defined.
    Bound as a default it would be read once at import, and then redirecting
    the constant — which is the only way to run any of this against a
    directory that is not the real one — would silently have no effect.

    Claude Code flattens the checkout path into a directory name:
    `C:\\projects\\demo` becomes `C--projects-demo`, every separator turning
    into `-`, and the drive's `:` taking a place of its own, which is why there
    are two hyphens at the front.

    When that rule does not hold, the directories are searched by the tail of
    their name — and then the candidate has to *prove* it. The leaf name of a
    worktree is not unique: two repositories each had a `pollock`. Worse, one
    matching name is no evidence either — the only `-demo` directory on the
    machine can belong to `C:\\old\\demo` while the caller is asking about
    `D:\\new\\demo`, and handing it over silently gives the census, the retro
    and the Slack brief another repository's conversation to read.

    A candidate proves itself with any log in it that names this checkout, not
    with its newest. The newest file can be a session that has not written its
    first `cwd` record yet, and rejecting the whole directory over that hides
    every earlier log that does belong.

    This answers *which directory*, which is not the same as which logs. The
    flattening is lossy — `C:\\a-b` and `C:\\a\\b` both become `C--a-b` — so one
    directory can hold two checkouts' sessions. `logs` is what settles that.
    """

    root = SESSIONS if root is None else root
    flat = str(project.resolve()).replace(":", "-").replace("\\", "-").replace("/", "-")
    exact = root / flat
    if exact.is_dir() or not root.is_dir():
        return exact
    here = project.resolve()
    tail = f"-{here.name}"
    for candidate in sorted(root.glob("*")):
        if not candidate.is_dir() or not candidate.name.endswith(tail):
            continue
        if any(under(claude_cwd(p), here) for p in candidate.glob("*.jsonl")):
            return candidate
    return exact


def logs(project: Path, root: Path | None = None) -> list[Path]:
    """This checkout's session files, oldest first. Each one proves itself.

    Not everything in the directory. Claude flattens the checkout path into a
    directory name by turning every separator into `-`, which is lossy:
    `C:\\a-b` and `C:\\a\\b` both land in `C--a-b`, and the host writes both
    checkouts' sessions there. Whoever reads that directory whole reads two
    repositories' conversations as one.

    Every record carries the `cwd` the session ran in, so the file settles it
    and the directory name never has to. A file with no `cwd` yet — a session
    opened a moment ago — belongs to nobody until it writes one, and leaving
    it out costs an empty log rather than a wrong attribution.
    """

    directory = folder(project, root)
    if not directory.is_dir():
        return []
    here = project.resolve()
    return sorted(
        (p for p in directory.glob("*.jsonl") if under(claude_cwd(p), here)),
        key=lambda p: p.stat().st_mtime,
    )


def claude_cwd(path: Path) -> Path | None:
    """The checkout a session ran in, read out of the log.

    The directory name is that path with every separator flattened to `-`,
    which is lossy: a repo whose own name contains a hyphen cannot be told
    from a nested one. The records carry the real thing, so read it instead of
    trying to undo the flattening. It is not on the first line — the first few
    records are bookkeeping — so a handful get checked.
    """

    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(8):
                record = parse(fh.readline())
                if record is None:
                    continue
                raw = record.get("cwd")
                if isinstance(raw, str) and raw:
                    return Path(raw).resolve()
    except OSError:
        return None
    return None


def claude_session(project: Path) -> Path | None:
    """The newest log that belongs to this checkout, not the newest in the folder.

    Newest first and stop at the first one that belongs, rather than sorting
    the whole directory by ownership. The mirror asks this once a second for
    as long as it is open, and the newest file is the answer almost every
    time; reading all 81 heads in the largest directory here cost 24ms of
    every second to learn what the first read already said.
    """

    directory = folder(project)
    if not directory.is_dir():
        return None
    here = project.resolve()
    for path in sorted(directory.glob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        if under(claude_cwd(path), here):
            return path
    return None


# --------------------------------------------------------------------------
# Codex
# --------------------------------------------------------------------------


def codex_homes() -> list[Path]:
    """Every session directory this machine's Codex could be writing into.

    The default is `~/.codex`, and `CODEX_HOME` moves it. Orca sets that per
    account, so every Codex cell launched from Orca writes under
    `%APPDATA%/orca/codex-accounts/<id>/home` and none of it appears in the
    default. A mirror that reads only the default sees no session the person
    actually runs, and says "no session" while one is running in front of
    them — which is exactly what it did.
    """

    roots = [Path(os.environ["CODEX_HOME"])] if os.environ.get("CODEX_HOME") else []
    roots.append(Path.home() / ".codex")
    accounts = Path(
        os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    ) / "orca" / "codex-accounts"
    if accounts.is_dir():
        roots += [account / "home" for account in accounts.iterdir() if account.is_dir()]
    found = [root / "sessions" for root in roots]
    return [path for path in dict.fromkeys(found) if path.is_dir()]


def codex_rollouts() -> list[Path]:
    """The newest rollouts across every session directory, merged and sorted."""

    found: list[Path] = []
    for root in codex_homes():
        found += root.rglob("rollout-*.jsonl")
    return sorted(
        found,
        key=lambda p: p.stat().st_mtime if p.exists() else -1.0,
        reverse=True,
    )[:CODEX_DEPTH]


def codex_cwd(path: Path) -> Path | None:
    """The `cwd` out of the first record. Nothing else ties a rollout to a repo."""

    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            record = parse(fh.readline())
    except OSError:
        return None
    payload = (record or {}).get("payload")
    raw = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return Path(raw).resolve()
    except OSError:
        return None


def under(cwd: Path | None, project: Path) -> bool:
    """Is that cell working inside this checkout?

    Not `==`. A cell is routinely opened in a subdirectory — `web/`, a package
    folder — and an exact match silently reports "no session" for a repo whose
    mirror is sitting right there.

    Not containment either. A clone inside a checkout is a different checkout:
    a vendored dependency, a worktree parked in a subdirectory. `C:\\outer` and
    `C:\\outer\\vendor\\inner` are two repositories, and counting the inner
    cell's session as the outer one's shows a mirror pointed at `outer` a
    conversation about `inner`.

    So the walk stops at the first repository boundary. A worktree's `.git` is
    a file rather than a directory, which is why this asks whether the name
    exists at all.

    ponytail: a boundary can only be seen while the directory is there. A
    checkout that has been deleted leaves nothing behind saying it was one,
    and `checkouts` folds such a path onto the living checkout above it.
    Telling those apart needs something the host does not record.
    """

    if cwd is None:
        return False
    if cwd == project:
        return True
    if project not in cwd.parents:
        return False
    step = cwd
    while step != project:
        if (step / ".git").exists():
            return False
        step = step.parent
    return True


def codex_session(project: Path) -> Path | None:
    for path in codex_rollouts():
        if under(codex_cwd(path), project):
            return path
    return None


FINDERS = {"claude": claude_session, "codex": codex_session}


# --------------------------------------------------------------------------
# The listing
# --------------------------------------------------------------------------


def checkouts(host: str) -> list[dict]:
    """Every checkout this host has a session for that is still on disk.

    Newest first, one row per checkout, each carrying the repository it is a
    worktree of. Both hosts stamp the real path into the log, so the paths are
    not guesses; what is and is not still there is `is_dir`, and which
    repository a row belongs to is git's answer, not a parsed path.

    One row per *checkout*, not per directory a cell happened to open in.
    Cells get opened in `web/` all the time, and `codex_session` has always
    counted that as the same checkout — a listing that showed it separately
    offered the same cell twice under two names and disagreed with the finder
    sitting next to it.
    """

    seen: dict[str, float] = {}
    if host == "codex":
        for path in codex_rollouts():
            where = codex_cwd(path)
            if where is None:
                continue
            when = path.stat().st_mtime if path.exists() else 0.0
            seen[str(where)] = max(seen.get(str(where), 0.0), when)
    elif SESSIONS.is_dir():
        for directory in SESSIONS.iterdir():
            if not directory.is_dir():
                continue
            # Every file, not the newest one. A directory can hold two
            # checkouts — the flattening collides — and reading only the
            # newest made whichever ran last the only one offered.
            for log in directory.glob("*.jsonl"):
                where = claude_cwd(log)
                if where is None:
                    continue
                seen[str(where)] = max(seen.get(str(where), 0.0), log.stat().st_mtime)

    rows: dict[str, dict] = {}
    for path, when in sorted(seen.items(), key=lambda row: -row[1]):
        # The cell's directory may be gone while the checkout it sat in is
        # not — `web/` gets deleted, the repository above it does not. Testing
        # the recorded path alone dropped that repository's only session.
        alive = living(Path(path))
        if alive is None:
            continue  # the worktree was deleted; its log was not
        top, repo, branch = checkout(alive)
        if alive != Path(path) and not top:
            continue  # gone, and nothing above it is a checkout either
        # Keyed by the checkout, not by where the cell was opened. Coming in
        # newest first means the first arrival already holds the latest time.
        key = top or path
        if key in rows:
            continue
        rows[key] = {
            "path": key,
            "name": Path(key).name,
            "at": when,
            "repo": repo,
            "repoName": Path(repo).name if repo else "",
            "branch": branch,
        }
    return list(rows.values())
