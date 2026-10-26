"""gate_tail — what a gate's output tail says failed, read by a survey's baseline and a run's final check.

It names failures across pytest, vitest/jest, go test and cargo test, and the failure-looking lines no
failure name covers.
"""

from __future__ import annotations

import re
from pathlib import Path

# Failure identifiers in the tail of other runners' output: vitest/jest, go test, cargo test. pytest's
# `FAILED`/`ERROR` lines are read whole by `_node`.
FAILED = re.compile(r"^\s*FAIL\s+(\S+)|^--- FAIL: (\S+)|^test (\S+) \.\.\. FAILED", re.M)
# A pytest node id: its path and its parameter id may hold spaces, ` - ` and brackets; its names may not.
# Any collected file (`docs/case1.txt::case1.txt` for a doctest), whose path holds neither `::` nor the
# `: ` of a message (`ImportError: no a.b`); a name holds neither space, `:` nor `[`.
NODE = re.compile(r"[^\s:](?:(?!::|: ).)*?\.\w+(?:::[^\s:\[]+)*(?:\[.*\])?")


def failures(verdict: dict) -> list[str] | None:
    """The failing tests a gate's tail names, or None when it failed without naming any."""
    if verdict.get("code") == 0:
        return []
    tail = verdict.get("tail") or ""
    # Each line stripped, as `unexplained` reads it: an indented `--- FAIL:` (a go subtest) is named too.
    found = {next(g for g in m.groups() if g) for line in tail.splitlines() if (m := FAILED.match(line.strip()))}
    for line in tail.splitlines():
        if (ids := _node(line)) is not None:
            if len(ids) > 1:
                return None   # a node id that could end in two places: two failures could read as one
            found |= ids
    return sorted(found) or None


def _node(line: str) -> set[str] | None:
    """The node ids a `FAILED`/`ERROR` line could name, indented or not: its text before a ` - ` or its
    end that reads as a whole node id. None for any other line — unittest's closing `FAILED (failures=1)`,
    a wrapper's `ERROR conda.cli...` log — which `unexplained` then compares as text."""
    kind, _, body = line.strip().partition(" ")
    if kind not in ("FAILED", "ERROR") or not body or body.startswith("("):
        return None
    ends = [i for i in range(len(body)) if body.startswith(" - ", i)] + [len(body)]
    return {body[:i] for i in ends if NODE.fullmatch(body[:i])} or None


SIGNAL = re.compile(r"\b(error|errors|fatal|fail|failed|failure|failures|exception|traceback|panic)\b", re.I)


def unexplained(tail: str, baseline: str, trees: tuple) -> list[str]:
    """The failure-looking lines of a failing run that no failure name covers and the
    baseline run did not print, compared with both worktree paths blanked and, but on a `FAILED`/`ERROR`
    line that names no node (`docs/case1.txt` is not `docs/case2.txt`), digits too."""
    def plain(line: str) -> str:
        line = line.strip()
        for tree in trees:
            line = line.replace(str(tree), "<tree>").replace(Path(tree).as_posix(), "<tree>")
        return line if line.startswith(("FAILED", "ERROR")) else re.sub(r"\d+", "#", line)
    printed = {plain(line) for line in baseline.splitlines()}
    # pytest's closing count line is judged by its counts alone, before any digit-blind match: passed,
    # skipped and warning counts move with any added test, failed and error counts may not grow.
    summaries = [_counted(line) for line in baseline.splitlines() if SUMMARY.match(line.strip())]
    odd = []
    for line in (line.strip() for line in tail.splitlines()):
        if SUMMARY.match(line):
            if (counts := _counted(line)) and (not summaries or any(n > summaries[-1].get(k, 0)
                                                                    for k, n in counts.items())):
                odd.append(line)
        elif SIGNAL.search(line) and not FAILED.search(line) and _node(line) is None and plain(line) not in printed:
            odd.append(line)
    return odd


# Exactly pytest's count list, its time and its rule (none with `-q`): any other sentence that mentions
# counts stays an ordinary line for the failure-looking comparison.
_COUNT = r"\d+ (?:failed|passed|skipped|deselected|xfailed|xpassed|warnings?|errors?|rerun)"
SUMMARY = re.compile(rf"^(?:=+ )?{_COUNT}(?:, {_COUNT})* in [\d.]+s(?: \(\d+:\d\d:\d\d\))?(?: =+)?$")


def _counted(line: str) -> dict:
    """The failed and error counts of a pytest summary line."""
    return {kind.rstrip("s"): int(n) for n, kind in re.findall(r"(\d+) (failed|errors?)\b", line)}
