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
    """`temporary` replaces `file` whole. Its bytes reach the disk first: a power loss after an unflushed rename
    left a spec of NUL bytes, and the run vanished from the list. A Windows reader holding `file` open refuses
    the swap for a moment, so it is tried again."""

    with open(temporary, "rb+") as written:
        os.fsync(written.fileno())
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
    out, last = [], 0
    for start, end, label in _spans(text, hide):
        out += [text[last:start], label]
        last = end
    return "".join(out + [text[last:]])


def overlaps(value: str, labels) -> bool:
    """Whether `value` could lie inside one of `labels`, hold one, or run into one from either side, read as `masked`
    reads (`folded`). Such a value cannot be masked by that label, and masking twice could change what masking once
    showed. No other value can be found in `masked`'s output that it did not leave there, so a second pass is a no-op."""

    v = folded(value)
    return any(v in f or f in v or any(v.startswith(f[i:]) or v.endswith(f[:i]) for i in range(1, len(f)))
               for f in map(folded, labels))


def _spans(text: str, hide: dict[str, str]) -> list[tuple[int, int, str]]:
    """Where `masked` puts each label in `text`, as `(start, end, label)` in order, none overlapping."""
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
    return sorted(chosen)


class Veil:
    """`masked` over a stream: every string in an event, however nested, and each running text (an answer's
    deltas, one command's output) masked as a whole. The tail a value could still be starting in is held until the
    next part of that text, or `rest`, so a value split across parts is never shown in pieces."""

    # The handles at the top of an event or a record row, kept as they are: an approval is answered by its `id` as
    # the screen got it, and a conversation resumes by its `session_id`.
    IDS = frozenset({"id", "request", "item_id", "session_id", "parent_id", "turn"})
    # The tags the screen and the record dispatch on, kept only as a `tool` field: constants of this code, so a
    # provider's own tool name, which is none of them, and any other field that spells one are masked like any text.
    TAGS = frozenset({"AskUserQuestion", "requestUserInput", "commandExecution", "command_execution", "tool_result"})

    def __init__(self, hide: dict[str, str] | None):
        self.hide, self.held = hide or {}, {}
        self.needles = [(v, folded(v)) for v in self.hide if v]
        self.raw, self.flat = (max((len(x[i]) for x in self.needles), default=0) for i in (0, 1))

    def deep(self, value):
        if not self.hide:
            return value
        if isinstance(value, str):
            return masked(value, self.hide)
        if isinstance(value, dict):
            return {k: v if k == "tool" and v in self.TAGS else self.deep(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.deep(v) for v in value]
        return value

    def meta(self, meta: dict) -> dict:
        """An event's meta, every string in it masked but the server's own handles at its top."""
        return {k: v if (k in self.IDS and isinstance(v, str)) or (k == "tool" and v in self.TAGS) else self.deep(v)
                for k, v in meta.items()}

    @staticmethod
    def options(meta: dict) -> list[list]:
        """An asked question's option labels as asked, one list per question."""
        return [[o.get("label") if isinstance(o, dict) else None for o in q.get("options") or []]
                if isinstance(q, dict) else [] for q in (meta.get("input") or {}).get("questions") or []]

    @staticmethod
    def chosen(answers: list[str], picks: list | None, options: list[list]) -> list[str]:
        """Each answer: picked options by their indices, as asked and in the screen's `, ` join; typed text as typed.
        An index the question does not have raises `ValueError`."""
        out = []
        for i, a in enumerate(answers):
            p = (picks or [])[i] if i < len(picks or []) else None
            if p and not all(isinstance(j, int) and 0 <= j < len(row := options[i] if i < len(options) else [])
                             and isinstance(row[j], str) for j in p):
                raise ValueError("고른 선택지가 그 질문에 없다")
            out.append(", ".join(options[i][j] for j in p) if p else a)
        return out

    def stream(self, key, chunk: str, meta: dict) -> str:
        """What of `key`'s text can be shown now, `chunk` added: all but a tail some value starts with."""
        if not self.hide:
            return chunk
        text = self.held.pop(key, ("", meta))[0] + chunk
        # Where a value could still start, read as the complete match reads: at any folded position, even inside
        # one character's expansion (a ligature), mapped back to the character that holds it; or as spelled.
        folds = _folds(text)
        flat, owner = "".join(folds), [i for i, f in enumerate(folds) for _ in f]
        cut = next((owner[j] for j in range(max(0, len(flat) - self.flat), len(flat))
                    if any(n.startswith(flat[j:]) for _, n in self.needles)), len(text))
        cut = next((i for i in range(max(0, len(text) - self.raw), cut)
                    if any(v.startswith(text[i:]) for v, _ in self.needles)), cut)
        for start, end, _ in _spans(text, self.hide):
            if start < cut < end:   # spans never overlap: one at most holds the cut
                cut = start
        if cut < len(text):
            self.held[key] = (text[cut:], meta)
        return masked(text[:cut], self.hide)

    def rest(self) -> list[tuple[str, str, dict]]:
        """Every held tail as `(kind, text, meta)`, masked: the stream ended, so no value completes it."""
        out = [(key[0], masked(text, self.hide), self.meta(meta)) for key, (text, meta) in self.held.items()]
        self.held.clear()
        return out


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
