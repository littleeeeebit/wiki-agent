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


def read_record(file: Path) -> dict | None:
    """A JSON record, or `None` when its file does not exist."""
    import json

    try:
        return json.loads(Path(file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_record(file: Path, record: dict) -> None:
    """Write a JSON record through a sibling partial file and a replace, so a crash never leaves half of it."""
    import json

    partial = Path(file).with_suffix(".part")
    partial.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(partial, file)


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


def _folds(text: str) -> list[str]:
    """Each character of `text` folded: case folded away, and the Turkish dotted and dotless i, which
    `text-transform` can draw under a page's `lang`, made a plain `i` (a dot above folds away after an `i` only)."""
    out: list[str] = []
    for c in text:
        f = c.casefold().replace("ı", "i").replace("i̇", "i")
        out.append("" if f == "̇" and out and out[-1].endswith("i") else f)
    return out


def folded(text: str) -> str:
    """`text` with case folded away, as `_folds` does: any casing of a secret is that secret."""
    return "".join(_folds(text))


def masked(text: str, hide: dict[str, str] | None) -> str:
    """`text` with each of `hide`'s values, such as a secret a child's environment held, replaced by its label as
    spelled and in every casing `folded` treats as the same, the longest first; a match inside one character covers
    all of it. Each character is claimed once, so the work grows with the matches, never with their square."""
    if not hide:
        return text
    folds = _folds(text)
    flat, owner = "".join(folds), [i for i, f in enumerate(folds) for _ in f]
    taken, chosen = bytearray(len(text)), []

    def claim(start: int, end: int, label: str) -> None:
        if not any(taken[start:end]):
            taken[start:end] = b"\x01" * (end - start)
            chosen.append((start, end, label))

    for value in sorted(hide, key=lambda v: max(len(v), len(folded(v))), reverse=True):
        at = 0
        while value and (at := text.find(value, at)) >= 0:   # as spelled: a fold can depend on what is around it
            claim(at, at + len(value), hide[value])
            at += 1
        needle, at = folded(value), 0
        while needle and (at := flat.find(needle, at)) >= 0:
            claim(owner[at], owner[at + len(needle) - 1] + 1, hide[value])
            at += 1
    out, last = [], 0
    for start, end, label in sorted(chosen):
        out += [text[last:start], label]
        last = end
    return "".join(out + [text[last:]])


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
