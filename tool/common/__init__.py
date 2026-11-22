"""common — what two pipelines actually share. Nothing here imports a pipeline.

`worktree_home` is where `workspace` makes the worktrees an agent writes in,
and the one place `agent` opens a write session. Held here so the two cannot
drift apart: a worktree created by another tool is not ours.
"""

import json
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
    reads (`folded`). Such a label cannot mask that value, and masking twice could change what masking once showed;
    with labels no value overlaps, a value found in `masked`'s output lies in text it left, so a second pass is a no-op."""

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
    # The tags the screen and the record dispatch on, kept only as the `tool` string at the top of an event's meta or
    # of a saved step: constants of this code. A provider's own tool name, which is none of them, and any field of a
    # tool's arguments, whatever its name, are masked like any text.
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
            # A name is masked too. Masking can make two names one, so a mapping a value's name is in becomes its
            # `[name, value]` pairs: every entry kept, and no text added the mask has not seen.
            if any(isinstance(k, str) and masked(k, self.hide) != k for k in value):
                return [[self.deep(k), self.deep(v)] for k, v in value.items()]
            return {k: self.deep(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.deep(v) for v in value]
        return value

    def meta(self, meta: dict) -> dict:
        """An event's meta, every string in it masked but the server's own handles at its top."""
        if not self.hide:
            return meta
        return {k: v if isinstance(v, str) and (k in self.IDS or (k == "tool" and v in self.TAGS))
                else self.asked(v) if k == "input" and self.question(meta)
                else self.counted(v) if k == "tokens" else self.deep(v)
                for k, v in meta.items()}

    def counted(self, tokens):
        """A turn's token counts by the adapters' names: a number or nothing kept, any other value masked."""
        if not isinstance(tokens, dict):
            return self.deep(tokens)
        return {k: tokens[k] if tokens[k] is None or type(tokens[k]) in (int, float) else self.deep(tokens[k])
                for k in self.TOKENS if k in tokens}

    # The two hosts' question tools: their input is read by the screen in a schema of its own.
    QUESTIONS = frozenset({"AskUserQuestion", "requestUserInput"})

    @classmethod
    def question(cls, meta: dict) -> bool:
        """Whether an event is a question tool's: its `tool` a string, and one of `QUESTIONS`."""
        return isinstance(tool := meta.get("tool"), str) and tool in cls.QUESTIONS

    @staticmethod
    def questions(given, show=None) -> list[dict]:
        """A question tool's questions in their schema's shape and nothing else: each one's `question` and `id`,
        `header` and `multiSelect` when given, and its options' `label`, `description` and `preview`. One entry stays
        for every question and option asked, whatever its shape, so the answers and the option indices line up with
        the provider's; a question and a label are always text. `show` turns each field's value into that text
        (`text` by default); the shape is read off the input as given, so no name a value is in can lose a field."""

        show = show or text
        return [{"question": show(q.get("question")), **{k: show(q[k]) for k in ("id", "header") if isinstance(q.get(k), str)},
                 **({"multiSelect": q["multiSelect"]} if isinstance(q.get("multiSelect"), bool) else {}),
                 "options": [{"label": show(o.get("label")),
                              **{k: show(o[k]) for k in ("description", "preview") if isinstance(o.get(k), str)}}
                             if isinstance(o, dict) else {"label": show(o)}
                             for o in (q["options"] if isinstance(q.get("options"), list) else [])]}
                for q in question_list(given.get("questions") if isinstance(given, dict) else None)]

    def asked(self, given) -> dict:
        """A question tool's input as the screen reads it (`questions`): its fields by their schema's names, each
        value masked before it becomes text, so a value is never spelled otherwise first, as a structured field's
        JSON would spell it. A name the provider added is left out with its value."""
        return {"questions": self.questions(given, lambda value: text(self.deep(value)))}

    # The adapters' token counts: fixed names, numbers or nothing.
    TOKENS = ("in", "out", "cache_read", "cache_write", "reasoning")

    def row(self, fields: dict) -> dict:
        """A work-record row's fields: as `meta`, and each saved step as an event's meta."""
        steps = fields.get("steps")
        return {**self.meta(fields), **({"steps": [self.meta(s) if isinstance(s, dict) else self.deep(s) for s in steps]}
                                        if isinstance(steps, list) else {})}

    @staticmethod
    def options(meta: dict) -> list[list]:
        """An asked question's option labels as asked, one list per question (`questions`)."""
        return [[o.get("label") for o in q["options"]] for q in Veil.questions(meta.get("input"))]

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


def question_list(value, keys=("question",)) -> list[dict]:
    """A question tool's batch as the adapters, their replies and the screen all read it: a list of mappings, each
    with every reply key in `keys` a non-empty string no other entry repeats. Any other batch holds no question and
    can only be declined, so no answer is lost to a key two entries share, and nothing is made text before masking."""

    if not isinstance(value, list) or not all(isinstance(q, dict) for q in value):
        return []
    for k in keys:
        got = [q.get(k) for q in value]
        if not all(isinstance(g, str) and g for g in got) or len(set(got)) != len(got):
            return []
    return value


def text(value) -> str:
    """A field the screen reads as text: a string as given, nothing as empty, any other value as its JSON."""
    return value if isinstance(value, str) else "" if value is None else json.dumps(value, ensure_ascii=False, default=str)


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
