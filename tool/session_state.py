"""SessionStart hook — begin a session already knowing where things stand.

Three languages meet here and each has a reason. The Korean this file *reads*
is the plan documents and decision records, which are written in Korean; the
Korean it *hands out* — `active_page` and `decisions` — goes into the `next`
focus's materials (`main/specs.py`), where a person reads it; and the context handed to the agent is English. Only
the last of those is translated, and only at the one point where it crosses over.
"""

from __future__ import annotations

# First import of the entry point: it keeps the stack from before whatever
# time limit kills this.
import hook_diagnostics  # noqa: F401
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import translate  # noqa: E402
from wiki import front_matter  # noqa: E402

MAX_PLANS = 2       # How many plan documents to look at
MAX_ROWS = 8        # How many unfinished rows from one plan
MAX_DECISIONS = 4   # How many recent decisions

# The budget for the whole translation, kept under the hook's own 25 seconds.
# Going over does not cost the translation, it costs the entire injection.
# Whatever is not done by then goes out as the Korean original.
BUDGET = 18.0


def run(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
        return done.stdout.strip() if done.returncode == 0 else ""
    except Exception:
        return ""


def branch_line(repo: Path) -> str:
    """The branch, what is unpushed and what is changed, for the agent.

    No translator. These are fixed strings this file writes itself, so the
    English is simply written out, and a round trip is saved on every session
    start.
    """

    branch = run(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    dirty = run(repo, "status", "--porcelain")
    ahead = run(repo, "rev-list", "--count", "@{u}..HEAD") if branch != "?" else ""
    bits = [f"`{branch}`"]
    if ahead and ahead != "0":
        bits.append(f"{ahead} unpushed")
    if dirty:
        bits.append(f"{len(dirty.splitlines())} changed")
    else:
        bits.append("worktree clean")
    return " · ".join(bits)


def open_steps(path: Path, limit: int = MAX_ROWS) -> list[str]:
    """The rows of a plan's `## 단계` table that are not finished.

    A plan document here gives every step a status cell, and a row that is
    neither done nor cancelled is what is left. No table means an empty list
    and nothing said — a format that could not be read, reported as if it had
    been, is how a wrong thing gets said confidently.
    """

    text = path.read_text(encoding="utf-8")
    block = re.search(r"^##+ 단계\s*$(.*?)(?=^##+ |\Z)", text, re.M | re.S)
    if not block:
        return []
    rows = []
    for line in block.group(1).splitlines():
        if not line.startswith("|") or set(line) <= set("|- :"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("#", ""):
            continue
        state = cells[-1].replace("*", "").strip()
        # The first word, not the whole cell: `완료 — 양 호스트 실측` is done.
        # Not a prefix either: `미완료` is not.
        first = state.split()[0] if state else ""
        if first in ("완료", "취소", "상태") or "~~" in cells[1]:
            continue
        rows.append(f"{cells[0]} {cells[2] if len(cells) > 2 else ''} — {state or '미착수'}")
    return rows[:limit]


def plans(repo: Path, limit: int = MAX_PLANS, rows: int = MAX_ROWS) -> list[tuple[Path, list[str]]]:
    """Plans with steps left. A series may sit in its own folder, and a
    finished series is moved under `done/`, which is not read at all — its
    tables are history, not work.

    Folders go newest first; inside one, the lowest number first, so the
    overview and the next step are read rather than the last two steps."""

    directory = repo / "docs" / "plans"
    if not directory.is_dir():
        return []
    found = []
    live = [p for p in directory.rglob("*.md") if "done" not in p.relative_to(directory).parts[:-1]]
    number = lambda p: int(re.match(r"\d*", p.name).group() or 0)  # noqa: E731
    live.sort(key=lambda p: (number(p), p.name))
    live.sort(key=lambda p: p.parent.relative_to(directory).as_posix(), reverse=True)
    for path in live:
        steps = open_steps(path, rows)
        if steps:
            found.append((path, steps))
        if len(found) >= limit:
            break
    return found


def active_page(repo: Path) -> tuple[str, list[str]]:
    """`.wiki/plan-active.md`, and whether it has gone stale.

    A machine parsing a free-form plan reports things that are not there. In
    one repository the `## 단계` table was finished while the actual remaining
    work sat in another table with no status column at all. So what is open is
    held by a page a person maintains.

    Staleness is the part a machine can answer: if a plan this page points at
    was edited after the page itself, say so.
    """

    path = repo / ".wiki" / "plan-active.md"
    if not path.exists():
        return "", []
    meta, body = front_matter(path.read_text(encoding="utf-8"))
    stale = []
    mine = path.stat().st_mtime
    for target in meta.get("reads") or []:
        doc = repo / str(target)
        if doc.exists() and doc.stat().st_mtime > mine:
            stale.append(str(target))
    return body.strip(), stale


def decisions(repo: Path) -> list[tuple[str, str]]:
    directory = repo / ".wiki" / "decisions"
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.md"), reverse=True)[:MAX_DECISIONS]:
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        title = str(meta.get("title") or "")
        if not title:
            first = next((x for x in body.splitlines() if x.startswith("# ")), "")
            title = first[2:].strip() or path.stem
        # `Why.` since records are written in English; `왜.` in the older ones.
        why = next(
            (x.split(".", 1)[1].strip() for x in body.splitlines() if x.startswith(("왜.", "Why."))),
            "",
        )
        # The first sentence only. The full text is in the file, and four full
        # texts at session start push out the work the session came to do.
        head = re.split(r"(?<=\.)\s", why, maxsplit=1)[0]
        found.append((title, head[:160] + (" …" if len(head) > 160 else "")))
    return found


def doc_catalog(repo: Path) -> str:
    """The repository's document listing, or an empty string.

    The whole listing is about nine thousand characters, so carrying it once
    at session start costs nothing per utterance afterwards. Choosing what to
    open is left to whoever receives it: the side holding the conversation
    beats a cosine score, and it bridges a Korean question and an English
    document without anything in between.
    """

    try:
        import corpus

        index = corpus.load(repo)
        return corpus.catalog(index["docs"]) if index else ""
    except Exception:
        return ""


def titled(listing: str) -> tuple[list[str], list[int]]:
    """`(titles, the line each came from)`. A path is not a title.

    Handing the whole listing to the translator makes filenames and directory
    names translation targets. Wrapping it in a code fence to stop that
    protects the whole thing instead, titles included. So the titles are
    lifted out and sent, and the paths are never touched.
    """

    rows, at = [], []
    for n, line in enumerate(listing.splitlines()):
        if line.startswith("  ") and " — " in line:
            rows.append(line.split(" — ", 1)[1])
            at.append(n)
    return rows, at


def retitled(listing: str, rows: list[str], at: list[int]) -> str:
    lines = listing.splitlines()
    for n, title in zip(at, rows):
        lines[n] = lines[n].split(" — ", 1)[0] + " — " + title
    return "\n".join(lines).replace("(저장소 루트)", "(repo root)")


def report(repo: Path, checkout: Path | None = None) -> str:
    """The session-start context. English, because it is agent input.

    Korean stays the authoritative copy in this repository — commit messages
    and `.wiki/decisions/` are read by people on GitHub. The translation
    happens at this one place, where that text crosses over to the agent, and
    the functions that read it leave Korean alone.

    One batched request. Waiting on each string separately takes 24 seconds
    for four decisions against a 25-second hook budget, and going over loses
    the whole injection rather than the translation. Whatever misses the
    deadline is assembled from the Korean original and sent regardless.
    """

    deadline = time.monotonic() + BUDGET

    body, stale = active_page(repo)
    open_plans = [] if body else plans(repo)
    recent = decisions(repo)
    listing = doc_catalog(repo)
    titles, title_at = titled(listing)

    step_rows = [s for _p, steps in open_plans for s in steps]
    pairs = [t for pair in recent for t in pair]
    chunks = [[body], step_rows, pairs, titles]
    flat = [t for chunk in chunks for t in chunk]
    done = translate.translate(flat, translate.KO_EN, deadline)
    cut, taken = [], 0
    for chunk in chunks:
        cut.append(done[taken:taken + len(chunk)])
        taken += len(chunk)
    (body,), step_rows, pairs, titles = cut
    recent = list(zip(pairs[::2], pairs[1::2]))

    lines = [
        "Where this repository stands right now. The wiki puts this in once, "
        "at session start.",
        "",
        f"## Branch\n\n{branch_line(checkout or repo)}",
    ]
    if body:
        lines.append("\n## In progress\n")
        lines.append(body)
        if stale:
            lines.append(
                f"\n**This list may be stale.** `{'`, `'.join(stale)}` changed "
                "later than it did. Read those documents and bring "
                "`.wiki/plan-active.md` into line before deciding what to change."
            )
    elif open_plans:
        lines.append("\n## Plans still open (read off their step tables)\n")
        taken = 0
        for path, steps in open_plans:
            lines.append(f"`{path.relative_to(repo).as_posix()}`")
            lines += [f"- {s}" for s in step_rows[taken:taken + len(steps)]]
            lines.append("")
            taken += len(steps)
        lines.append(
            "This only reads the status column of the `## 단계` table, so it "
            "carries neither why something was cancelled nor what comes next. "
            "Read the plan document before deciding what to change."
        )
    if listing:
        lines.append("\n## Every document in this repository\n")
        lines.append(
            "Pick from here and open that file. No bodies are loaded."
        )
        lines.append("\n```\n" + retitled(listing, titles, title_at) + "\n```")

    if recent:
        lines.append("\n## Recent decisions — read the reason before reversing one\n")
        for title, why in recent:
            lines.append(f"- {title}" + (f" — {why}" if why else ""))
        lines.append(
            "\nThe full records are in `.wiki/decisions/`. Re-proposing a "
            "direction already weighed and dropped is the most expensive "
            "repetition there is."
        )
    return "\n".join(lines)


def main() -> int:
    # A hook's stdout is a pipe and its default here is cp949. The encoding is
    # not left to the environment.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="세션 시작에 현재 상태를 넣는다")
    parser.add_argument("--project", type=Path, required=True)
    # A worktree of the project. Its branch is the one the session is on;
    # the knowledge still comes from `--project`.
    parser.add_argument("--checkout", type=Path, default=None)
    args = parser.parse_args()

    try:
        sys.stdin.read()  # the input is unused, but the pipe gets drained
    except Exception:
        pass

    repo = args.project.expanduser()
    if not (repo / ".git").exists():
        return 0
    text = report(repo, args.checkout)
    if text.count("\n") < 4:
        return 0  # one branch line on its own is not worth injecting

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": text,
            },
            "systemMessage": "위키: 현재 상태 주입",
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    # Whatever happens, a hook does not stop the session. Leave the name of
    # what went wrong and pass.
    try:
        _code = main()
    except Exception as _error:  # noqa: BLE001
        print(f"hook skipped: {type(_error).__name__}", file=sys.stderr)
        _code = 0
    raise SystemExit(_code)
