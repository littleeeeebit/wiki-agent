"""mirror — the Korean half of an English-first session.

The agent writes English; this tails the session log the host is already
writing and hands the same turns back in Korean. Nothing here feeds back into
the session, so a mirror that lags, stalls or dies costs reading comfort and
nothing else.

Two ways to read it. The screen is a tab in the wiki app —
`web/src/components/Mirror.tsx` over `chat.py`'s `/api/mirror/*`, which is
where the repository picker lives. Running this file directly tails one
repository to the terminal instead, which needs nothing built and is the
fastest way to answer "is it seeing anything at all".

Two hosts, one shape. Claude Code writes `~/.claude/projects/<slug>/*.jsonl`,
one record per line; Codex writes `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
and stamps the cell's `cwd` into the first record, which is the only thing
that ties a rollout file to a repository. Both are append-only, so both are
read the same way: seek to where the last read stopped, take whole lines, keep
the partial tail for next time.

*Finding* that file is `sessions.py`'s job, not this one's. This file knows
what a record means and how to keep up with a growing one; which checkout a
log belongs to, and whether that checkout still exists, is a question the
census asks too, and it used to be answered here by importing the census.

What gets translated is the short list: the agent's prose, and the one-line
description it writes for a tool call. Commands, patches, file contents and
tool output are code — translating them would be both expensive and wrong.
Anything the person typed is printed exactly as typed, whatever language it
is in. The input path renders their Korean into English for the agent; the
overlay is where they check what was understood, and a round trip of their own
sentence is what makes that check worthless.

A translation failure prints the English original. An empty mirror is worse
than an English one: the person can read English, they just prefer not to.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from workspace import sessions  # noqa: E402
import translate as T  # noqa: E402
from workspace.sessions import INJECTED, checkouts, parse  # noqa: E402
from transcript import human_text  # noqa: E402

POLL = 1.0

# Markers, not labels. `SELF` is what the person typed and is printed as
# typed — the mark decides that, not the language, because the person writes
# both. `SAID` and `DID` get translated. `CODE` is the call itself — the command, the
# patch — and is the one thing here that must arrive byte for byte, so it never
# goes near the translator. `SEEN` is the mirror talking about itself.
SELF = "▶"
SAID = ""
DID = "·"
CODE = "$"
SEEN = "──"

# How much of one command or patch to carry. A `Write` of a whole file is not
# something anyone reads in a mirror; the first screenful says what changed and
# the rest is already on disk.
CAP = 4000

# The translator has no deadline of its own; each caller brings one. A mirror
# renders whole answers, and a 6.6k-character one measured 7.7s, so this is
# long on purpose — it was 6s once, under the hooks' budget, and every long
# answer came back English as if nothing were wrong (#19).
SCREEN_SECONDS = 60.0


def clock(record: dict) -> str:
    """`HH:MM` in the person's own zone, or `""`.

    Both hosts stamp the record in UTC with a trailing `Z`, which `fromisoformat`
    refuses before 3.11 and which nobody reading a mirror at 23:31 KST wants to
    see as 14:31. The conversion is the point; the string is not.
    """

    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return ""
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return when.astimezone().strftime("%H:%M")


def now() -> str:
    return dt.datetime.now().strftime("%H:%M")


# --------------------------------------------------------------------------
# What each host's records say
# --------------------------------------------------------------------------


def clip(text: str) -> str:
    text = text.rstrip()
    if len(text) <= CAP:
        return text
    return text[:CAP] + f"\n…(+{len(text) - CAP}자)"


def diff(old: str, new: str) -> str:
    """The edit itself, as the two sides. Not a real unified diff on purpose.

    `Edit` hands over the exact strings it matched and wrote, which is already
    the whole change. Running a differ over them would reorder and re-chunk
    what the agent actually sent, and the point of this block is that it is
    what was sent.
    """

    return "\n".join(
        [f"- {line}" for line in old.splitlines()]
        + [f"+ {line}" for line in new.splitlines()]
    )


def claude_payload(name: str, given: dict) -> str:
    """The verbatim half of a tool call: the command, or the change.

    Only the calls whose body is the work. A `Read` or a `Glob` is fully
    described by its one-line description, and printing its arguments under
    that line would be the mirror repeating itself in two languages.
    """

    if name == "Bash":
        return str(given.get("command") or "")
    if name in ("Edit", "NotebookEdit"):
        where = str(given.get("file_path") or given.get("notebook_path") or "")
        body = diff(
            str(given.get("old_string") or given.get("old_source") or ""),
            str(given.get("new_string") or given.get("new_source") or ""),
        )
        return f"{where}\n{body}".strip()
    if name == "Write":
        return f"{given.get('file_path') or ''}\n{given.get('content') or ''}".strip()
    return ""


def claude_parts(record: dict) -> list[tuple[str, str, str]]:
    # A message typed while the agent is mid-turn never becomes a `user`
    # record. It is queued, absorbed into the running turn, and the only place
    # its text survives intact is the `enqueue` — afterwards it lives inside a
    # `<system-reminder>` wrapper in a tool envelope, which is not the person's
    # words any more. Without this the mirror shows one turn out of nine.
    if record.get("type") == "queue-operation":
        if record.get("operation") != "enqueue":
            return []  # `remove` repeats the same text when the turn absorbs it
        body = str(record.get("content") or "").strip()
        if not body or any(mark in body for mark in INJECTED):
            return []
        return [(SELF, body, "")]

    typed = human_text(record)
    if typed is not None:
        return [(SELF, typed, "")]
    if record.get("type") != "assistant":
        return []  # `tool_result` arrives as a `user` record and stops here
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    out: list[tuple[str, str, str]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                out.append((SAID, text, ""))
        elif block.get("type") == "tool_use":
            given = block.get("input")
            if not isinstance(given, dict):
                continue
            name = str(block.get("name") or "")
            note = given.get("description")
            if isinstance(note, str) and note.strip():
                out.append((DID, note.strip(), name))
            body = claude_payload(name, given)
            if body:
                out.append((CODE, clip(body), name))
    return out


def codex_parts(record: dict) -> list[tuple[str, str, str]]:
    """Only `item_completed`, which is Codex's already-filtered view.

    The raw `response_item` records carry the same turns plus the developer
    preamble, the skills catalogue and the environment block — every injection
    this mirror must not print. `item_completed` has none of that, and its
    `McpToolCall.arguments.title` is the same one-line description Claude
    writes on a tool call.
    """

    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "item_completed":
        return []
    item = payload.get("item")
    if not isinstance(item, dict):
        return []
    kind = item.get("type")

    if kind == "McpToolCall":
        given = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        name = str(item.get("tool") or "")
        out: list[tuple[str, str, str]] = []
        note = given.get("title")
        if isinstance(note, str) and note.strip():
            out.append((DID, note.strip(), name))
        body = given.get("code") or given.get("command") or given.get("input")
        if isinstance(body, str) and body.strip():
            out.append((CODE, clip(body), name))
        return out

    if kind == "CommandExecution":
        command = item.get("command")
        line = " ".join(command) if isinstance(command, list) else str(command or "")
        return [(CODE, clip(line), "shell")] if line.strip() else []

    if kind == "FileChange":
        # Codex hands over a unified diff it already built. Carry it across.
        changes = item.get("changes")
        return [
            (CODE, clip(f"{where}\n{body.get('unified_diff') or ''}"), "apply_patch")
            for where, body in (changes or {}).items()
            if isinstance(body, dict) and body.get("unified_diff")
        ]

    if kind not in ("UserMessage", "AgentMessage"):
        return []
    content = item.get("content")
    body = "\n".join(
        str(block.get("text") or "")
        for block in (content if isinstance(content, list) else [])
        if isinstance(block, dict) and str(block.get("type", "")).lower() == "text"
    ).strip()
    if not body:
        return []
    return [(SELF if kind == "UserMessage" else SAID, body, "")]


# --------------------------------------------------------------------------
# Which file to tail
# --------------------------------------------------------------------------
#
# `sessions.py` answers this for both hosts and for the census as well. What
# stays here is only the pairing: a finder, and the parser that reads what it
# finds.


def session_of(host: str):
    """The finder for a host, guarded by the checkout still being there.

    A deleted worktree leaves its log behind — the host wrote it, and nothing
    removes it when the directory goes. Without this the mirror sits on a dead
    file forever, showing the last thing a checkout that no longer exists ever
    said. Returning `None` is what lets the screen move itself somewhere live.
    """

    find = sessions.FINDERS[host]

    def pick(project: Path) -> Path | None:
        return find(project) if project.is_dir() else None

    return pick


HOSTS = {
    "claude": (session_of("claude"), claude_parts),
    "codex": (session_of("codex"), codex_parts),
}


# --------------------------------------------------------------------------
# Tail and print
# --------------------------------------------------------------------------


def translated(records: list[dict], host: str, translator=T.translate):
    """Records in, `(marker, Korean, HH:MM)` out. One request for the batch.

    Which of those are worth a request is `translate.worth_translating`'s
    answer, not one made again here. It skips a line with no English in it, so
    an old Korean log line is not round-tripped into a reworded version of
    itself — and it does *not* skip an English line that carries a protected
    Korean term, which a second "has any Hangul" test here did. The glossary
    holds those terms precisely so they can stand inside English prose.
    """

    _, parts_of = HOSTS[host]
    parts = [
        (mark, text, clock(record), name)
        for record in records
        for mark, text, name in parts_of(record)
    ]
    # `SELF` is what the person typed and `CODE` is a command or a patch.
    # Translating either is the one thing this mirror must never do: the
    # person's own words come back reworded — and they came in here to read
    # what was understood, not a rewording of what they said — while a
    # translated command is a command that no longer runs.
    wanted = [
        i for i, (mark, _, _, _) in enumerate(parts) if mark not in (SELF, CODE)
    ]
    if wanted:
        done = translator(
            [parts[i][1] for i in wanted], T.EN_KO, time.monotonic() + SCREEN_SECONDS
        )
        for i, text in zip(wanted, done):
            parts[i] = (parts[i][0], text, parts[i][2], parts[i][3])
    return parts


def render(records: list[dict], host: str, translator=T.translate) -> list[str]:
    return [
        " ".join(word for word in (at, mark, text) if word)
        for mark, text, at, _ in translated(records, host, translator)
    ]


# How many bytes behind the read position to keep as proof. One poll's worth
# of re-reading, and it happens once per pass.
SEAM = 64

# How far to look for the end of the first record. Both hosts put a session id
# in it, so this is read to identify the file, not to parse it.
FIRST = 8192


def seam(path: Path, offset: int) -> bytes:
    """The bytes just before `offset`, as the file holds them now.

    The bytes at the seam are the ones that were just consumed. A rewrite
    changes them — unless it happens to end the same way, which is why this is
    only half the check.
    """

    start = max(0, offset - SEAM)
    with path.open("rb") as fh:
        fh.seek(start)
        return fh.read(offset - start)


def born(path: Path) -> bytes:
    """The first record, which is where both hosts put the session id.

    Claude opens the file with `sessionId`, Codex with `session_meta` holding
    `session_id`. Neither is ever rewritten while the session appends to it,
    so this answers "is this the same session" rather than sampling bytes and
    hoping they differ — which is what the previous two attempts did, and both
    were shown to coincide.

    ponytail: a rewrite that keeps both the first record and the trailing
    64 bytes is not detected, and neither is one that lands between this
    check and the read below. Answering either needs the whole file hashed
    every poll, which costs more than a mirror is worth: a missed line costs
    reading comfort, and the file this reads is append-only on both hosts.
    """

    with path.open("rb") as fh:
        return fh.read(FIRST).split(b"\n", 1)[0]


def follow(pick, session: Path | None = None, poll: float = POLL, announce=None):
    """Yield one batch of records per pass, `[]` when there is nothing new.

    Yielding on an empty pass is what makes this testable: a caller can take a
    fixed number of passes instead of waiting for an infinite loop to feel
    like stopping.

    Three things go wrong with the file and all three land here. It gets
    truncated — read from the top again. It disappears — find another. A new
    session file appears beside it (`/clear` does this, and so does a second
    cell) — switch, but only while idle, so a switch can never eat a batch
    that was about to be printed. `--session` pins the file and turns all of
    that off except truncation. `announce` is how the caller learns which file
    that ended up being — the terminal prints it, the page draws a divider.
    """

    if announce is None:
        announce = lambda path: print(f"── {path.name}", flush=True)  # noqa: E731
    path, offset, tail, read, first = session, 0, b"", b"", b""
    while True:
        if path is None or not path.exists():
            found = pick()
            if found != path:
                path, offset, tail, read, first = found, 0, b"", b"", b""
                if path is not None:
                    announce(path)
        if path is None:
            yield []
            time.sleep(poll)
            continue

        try:
            size = path.stat().st_size
            # Three ways the file underneath stops being the file already
            # read. It shrinks, which the size catches. It becomes a different
            # session at the same path, which the first record catches. Or it
            # is rewritten with *more* content than was read — the size then
            # says "grown", the read resumes in the middle of content nobody
            # has seen, and everything before that is gone with nothing to say
            # so. The seam catches that: the bytes just consumed are still
            # where they were consumed from, or this is not the same file.
            head = born(path)
            moved = (first and head != first) or (read and seam(path, offset) != read)
        except OSError:
            path = None
            yield []
            continue

        if size < offset or moved:
            offset, tail, read = 0, b"", b""
        first = head
        if size == offset:
            if session is None:
                found = pick()
                if found is not None and found != path:
                    path, offset, tail, read, first = found, 0, b"", b"", b""
                    announce(path)
                    continue
            yield []
            time.sleep(poll)
            continue

        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
        except OSError:
            path = None
            yield []
            continue

        read = (read + chunk)[-SEAM:]

        # Split the bytes, then decode whole lines. A poll lands wherever the
        # writer happened to be, which is routinely inside a Korean character —
        # three bytes, and a read that takes one of them. Decoding the chunk
        # first turns that character into `�` permanently, because by the time
        # the rest arrives the damage is already in the string being carried
        # over. `\n` cannot appear inside a multi-byte sequence, so splitting
        # first is safe and the partial tail stays bytes until it is a line.
        tail += chunk
        *whole, tail = tail.split(b"\n")
        yield [
            r for r in (parse(line.decode("utf-8", errors="replace")) for line in whole)
            if r is not None
        ]


# --------------------------------------------------------------------------
# What the screen reads
#
# One tail, one translation, many tabs. The producer runs once in a thread and
# writes into `Feed`; every tab reading the stream replays from the same list.
# Two tabs must not mean two tails — that is two of every translation, and the
# second one is also a second answer to "which file is current".
#
# The screen itself is `web/src/components/Mirror.tsx`, served by `chat.py`
# with the rest of the wiki app. This file holds no HTTP: a second server on a
# second port was a second stack to keep in step, and the mirror wants the same
# tokens, the same components and the same `/api/translate` as its neighbours.
# --------------------------------------------------------------------------

# How much of the session a tab that opens late gets to read. The whole file
# would be honest and useless: the person opens the mirror to see what is
# happening now.
KEEP = 400

BEAT = 0.4


class Feed:
    """What the mirror has said, with an index so a tab can catch up once.

    The index is absolute and travels with each part. A dropped connection is
    reconnected and the server starts replaying from the top when it is, so
    without the index that reconnect duplicates the whole visible session.

    `id` is what makes the index mean anything to a tab. Both the index and
    the generation number start again from the bottom when this process does,
    so a tab holding `gen 1, index 200` across a server restart meets a new
    feed calling itself the same thing and silently discards its first two
    hundred lines. The number says where in a feed; only this says which feed.
    """

    def __init__(self, keep: int = KEEP) -> None:
        self.items: deque = deque(maxlen=keep)
        self.count = 0
        self.id = uuid.uuid4().hex
        self.lock = threading.Lock()

    def add(self, part: tuple[str, str, str, str]) -> None:
        with self.lock:
            self.items.append(part)
            self.count += 1

    def since(self, cursor: int) -> tuple[int, list[dict]]:
        with self.lock:
            start = self.count - len(self.items)
            begin = max(cursor, start)
            rows = list(self.items)[begin - start:]
            return self.count, [
                {"i": begin + n, "mark": mark, "text": text, "at": at, "name": name}
                for n, (mark, text, at, name) in enumerate(rows)
            ]


def pump(feed: Feed, pick, session: Path | None, poll: float, host: str, mine) -> None:
    """Tail, translate, append — until this feed stops being the current one.

    `mine()` is the ownership question, asked once per pass. A translation is
    a network call, so a switch lands mid-flight more often than not; without
    the check the answer to the old repo arrives in the new repo's feed, and
    it arrives looking exactly like a real line.
    """

    def seen(path: Path) -> None:
        feed.add((SEEN, path.name, now(), ""))

    try:
        for batch in follow(pick, session, poll, seen):
            if not mine():
                return
            parts = translated(batch, host)
            if not mine():
                return  # the switch happened while the batch was being translated
            for part in parts:
                feed.add(part)
    except Exception as error:  # noqa: BLE001 -- a dead thread has to say so
        feed.add((SAID, f"미러가 멈췄다: {type(error).__name__}", now(), ""))


class Station:
    """What the mirror is pointed at, and the right to change it.

    Switching repositories is the one thing the page asks the server to *do*,
    so the generation counter is decided here rather than bolted on later. The
    number goes up on every switch and drives the cursor reset, and each new
    `Feed` carries an id the tab resets on.

    There is no pinned session here on purpose. `--session` is the terminal
    tail's answer to "several cells in one repository"; the screen's answer is
    the picker, and it always follows the newest file. Carrying the flag here
    as well left a guard comparing a value to itself, which a pin would have
    ridden into every repository the person opened.
    """

    def __init__(self, host: str, poll: float) -> None:
        self.lock = threading.Lock()
        self.poll = poll
        self.gen = 0
        self.host = host
        self.project: Path | None = None
        self.feed = Feed()

    def now(self) -> tuple[int, Feed, str, str]:
        with self.lock:
            return self.gen, self.feed, self.host, str(self.project or "")

    def point(self, host: str, project: Path) -> None:
        with self.lock:
            if host == self.host and project == self.project:
                return
            self.gen += 1
            self.host, self.project, self.feed = host, project, Feed()
            gen, feed = self.gen, self.feed

        find, _ = HOSTS[host]
        threading.Thread(
            target=pump,
            args=(feed, lambda: find(project), None, self.poll, host,
                  lambda: self.gen == gen),
            daemon=True,
        ).start()


def main() -> int:
    """The terminal tail. The screen is a tab in the wiki app, not this.

    Kept because it needs nothing built: no npm, no venv, no server. When the
    question is "is the parser seeing anything at all", this answers it in one
    command, and that is the question every time the mirror looks empty.
    """

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="세션 로그를 한국어로 옮겨 찍는다.")
    ap.add_argument("--host", choices=sorted(HOSTS), default="claude")
    ap.add_argument(
        "--project", type=Path, default=Path.cwd(),
        help="비출 저장소. 미러가 어디서 도는지와 무관하다",
    )
    ap.add_argument("--session", type=Path, default=None, help="따라갈 jsonl 경로")
    ap.add_argument("--poll", type=float, default=POLL)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    find, _ = HOSTS[args.host]

    def pick() -> Path | None:
        return find(project)

    if args.session is None and pick() is None:
        print(f"{project} 의 {args.host} 세션 로그를 아직 못 찾았다.", file=sys.stderr)
        others = [r for r in checkouts(args.host) if r["path"] != str(project)]
        if others:
            print("세션이 있는 작업트리는 이것들이다:", file=sys.stderr)
            for row in others[:8]:
                # The leaf name of a worktree says nothing on its own — two
                # repositories each had one called `pollock`. Which repository
                # and which branch is what tells them apart.
                whose = " · ".join(x for x in (row["repoName"], row["branch"]) if x)
                print(f"  --project {row['path']}"
                      + (f"   ({whose})" if whose else ""), file=sys.stderr)
        else:
            print("그 호스트로 한 번 말을 걸면 파일이 생긴다.", file=sys.stderr)
        print("계속 기다린다.", file=sys.stderr, flush=True)

    try:
        for batch in follow(pick, args.session, args.poll):
            for line in render(batch, args.host):
                print(line, end="\n\n", flush=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
