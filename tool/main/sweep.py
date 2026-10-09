"""sweep — every implementation ends with its test cleanup.

TDD leaves tests behind: intermediate steps a later test supersedes, and tests
of paths the pipeline has since dropped. The work prompt asks for a cleanup
pass and a `test-cleanup` report before `done-report`; `specs._check` asks
once more when a code-changing task leaves it out, then stops the task.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROMPT = ("Before this task is done, run the test cleanup pass your instructions describe: remove tests the "
          "current pipeline made dead or obsolete and fold superseded TDD steps, then rerun the gate, commit, and "
          "end with both the `test-cleanup` block and the `done-report` block.")


REPAIR = ("When the repair changes code, run the test cleanup pass your instructions describe and also end with "
          "the `test-cleanup` block.")
OWED = ("Your commits in that turn change code, and the answer has no valid `test-cleanup` block. Run the test "
        "cleanup pass your instructions describe, rerun the checks, commit and push, then answer again with the "
        "`test-cleanup` block and everything the previous request asked you to end with.")


def valid(value) -> bool:
    """A `test-cleanup` report: the test files audited and each removed test with its reason.
    Auditing nothing needs a reason."""

    return isinstance(value, dict) and isinstance(value.get("audited"), list) \
        and all(isinstance(a, str) and a for a in value["audited"]) \
        and isinstance(value.get("removed"), list) and all(
            isinstance(r, dict) and isinstance(r.get("test"), str) and isinstance(r.get("reason"), str)
            and r["test"] and r["reason"] for r in value["removed"]) \
        and bool(value["audited"] or str(value.get("reason") or "").strip())


def owed(path: Path, since: str) -> bool:
    """Did the commits after `since` change anything but documents? Unknown
    counts as yes. `since` is the task's base for its first report and the
    reviewed head for a review correction."""

    if not since:
        return True
    try:
        done = subprocess.run(["git", "diff", "--name-only", f"{since}...HEAD"], cwd=path, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(done.returncode) or any(not (n.endswith(".md") or n.startswith((".wiki/", "docs/")))
                                        for n in done.stdout.splitlines())


def unreported(path: Path, reviewed: str, report) -> str:
    """Why a review correction is not finished on cleanup, or "". A repair can
    make tests dead too: commits after the reviewed head that change code owe a
    fresh report."""

    return "" if valid(report) or not owed(path, reviewed) else \
        "코드를 고쳤지만 테스트 정리 보고(`test-cleanup`)가 없거나 형식이 틀렸다"


def held(path: Path, text: str, turn, report, stop, keep) -> str | None:
    """A repair turn held to the cleanup every implementation owes: when its
    commits change code, a valid `test-cleanup` block is asked for once more,
    then the loop stops. The valid report replaces the stored one.

    `turn(text)` runs one turn and returns its answer, `None` when stopped;
    `report(answer)` reads the block; `stop(why)` and `keep(fields)` are the loop's."""

    try:
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        before = ""    # unknown: any commit the turn makes is taken to owe the report
    answer = turn(f"{text}\n\n{REPAIR}")
    for last in (False, True):
        missing = answer is not None and unreported(path, before, report(answer))
        if not missing:
            break
        if last:
            stop(missing)
            return None
        answer = turn(OWED)
    if answer is not None and valid(found := report(answer)):
        keep({"test_cleanup": found})
    return answer


def section(report: dict | None) -> list[str]:
    """The PR body's `테스트 정리` section, for the reviewer."""

    if not report:
        return []
    audited = ", ".join(f"`{a}`" for a in report["audited"]) or report.get("reason", "")
    return ["## 테스트 정리", "", f"점검: {audited}", "",
            *([f"- 삭제 `{r['test']}` — {r['reason']}" for r in report["removed"]] or ["- 삭제 없음"]), ""]
