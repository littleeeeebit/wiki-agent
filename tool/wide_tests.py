"""Claude PreToolUse: a repair round runs the test files the change touches, never the whole set.

`craft/verify-narrow-then-wide` says the full set runs once, after `머지 허용`,
through the repository's final gate. Twice the agent ran it anyway during
repairs: on 2026-11-22 a round fix ran fifteen-odd test files, all of
`test_oneshot*` and more, where three answered the change. So in a repository
whose final run goes through `tool/verify.py final`, a direct pytest run that
selects a whole folder, a glob, or more than `LIMIT` files is refused. The
final gate starts its own pytest, which no tool call carries, so it is never
refused here. Only argv the shell would run directly is read (`pytest_output.runs_pytest`).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from pytest_output import _blank, runs_pytest

LIMIT = 8
STAGES = re.compile(r"&&|\|\||[;|\n]")
MOVED = re.compile(r"(?:cd|pushd|Set-Location|sl)\s+(\S.*)", re.IGNORECASE)
# Every option pytest and the plugins installed here read a value from the next word, from pytest's own parser on
# 2026-11-26; an option whose value is optional (`--debug`, `--cache-show`) takes none that stands apart.
VALUED = set("""-W -c -k -m -n -o -p -r --assert --basetemp --capture --code-highlight --color --confcutdir
    --config-file --deselect --dist --doctest-glob --doctest-report --durations --durations-min --ignore
    --ignore-glob --import-mode --junit-prefix --junit-xml --junitprefix --junitxml --last-failed-no-failures --lfnf
    --log-auto-indent --log-cli-date-format --log-cli-format --log-cli-level --log-date-format --log-disable
    --log-file --log-file-date-format --log-file-format --log-file-level --log-file-mode --log-format --log-level
    --max-worker-restart --maxfail --maxprocesses --maxschedchunk --numprocesses --override-ini --pastebin --pdbcls
    --px --pythonwarnings --rootdir --rsyncdir --rsyncignore --show-capture --tb --testrunuid --timeout --tx
    --verbosity""".split())
REASON = """테스트 전체(또는 넓은 묶음)를 지금 돌리려 한다: {what}.
수선 회차에는 이번 변경이 건드린 테스트 파일만 돌린다. 전체는 `머지 허용` 뒤 한 번, `python tool/verify.py final --base main` 으로 돌린다 (`craft/verify-narrow-then-wide`).

바뀐 동작을 묻는 파일만 이름으로 적어 다시 돌린다 ({limit}개 이하)."""


def guarded(folder: Path) -> bool:
    """`folder` sits in a repository whose final run is `tool/verify.py final`."""

    for root in (folder, *folder.parents):
        if (root / ".git").exists():
            return (root / "tool" / "verify.py").is_file()
    return False


def selected(stage: str) -> list[str]:
    """The paths a pytest stage names, options and their values left out."""

    try:
        words = [w.strip("\"'") for w in shlex.split(stage, posix=False)]
    except ValueError:
        return []
    start = next((i + 1 for i, w in enumerate(words) if w == "pytest" or re.search(r"pytest(\.exe)?$", w)), len(words))
    paths, skip = [], False
    for word in words[start:]:
        if skip:
            skip = False
        elif re.match(r"\d*[<>]", word):   # a redirection, and its target when it stands apart
            skip = bool(re.fullmatch(r"\d*(>>?|<)", word))
        elif word.startswith("-"):
            skip = word in VALUED
        else:
            paths.append(word)
    return paths


def wide(stage: str) -> str:
    """Why the pytest run in `stage` is the wide one, or ""."""

    paths = selected(stage)
    if not paths:
        return "경로 없이 전체"
    files = {p.split("::")[0] for p in paths}
    if broad := [p for p in files if any(c in p for c in "*?[") or not p.endswith(".py")]:
        return f"폴더나 패턴 {', '.join(sorted(broad))}"
    return f"파일 {len(files)}개" if len(files) > LIMIT else ""


def stages(command: str, dialect: str) -> list[str]:
    """`command` split where the shell runs a new command: an operator inside quotes or a heredoc is text."""

    blank, cuts = _blank(command, dialect), [0]
    for match in STAGES.finditer(blank):   # the same length as `command`, so its places are `command`'s
        cuts += [match.start(), match.end()]
    cuts.append(len(command))
    return [command[a:b].strip() for a, b in zip(cuts[::2], cuts[1::2])]


def verdict(payload: dict) -> dict | None:
    tool = payload.get("tool_name")
    if tool not in ("Bash", "PowerShell"):
        return None
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if "pytest" not in command:
        return None
    here = Path(str(payload.get("cwd") or Path.cwd()))
    for stage in stages(command, "ps" if tool == "PowerShell" else "posix"):
        if moved := MOVED.fullmatch(stage):   # each stage runs where the `cd`s before it left the shell
            try:
                to = shlex.split(moved[1], posix=tool != "PowerShell")[0].strip("\"'")
            except (ValueError, IndexError):
                return None   # a `cd` read no further: where the rest runs is unknown, so nothing is refused
            to = re.sub(r"^/([A-Za-z])(?=/|$)", r"\1:", to)   # Git Bash's `/c/...`
            here = Path(os.path.abspath(here / os.path.expanduser(to)))
            continue
        if runs_pytest(stage) and guarded(Path(os.path.abspath(here))) and (what := wide(stage)):
            output = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                      "permissionDecisionReason": REASON.format(what=what, limit=LIMIT)}
            return {"hookSpecificOutput": output, "systemMessage": f"넓은 테스트 실행 거부: {what}"}
    return None


if __name__ == "__main__":
    try:   # run alone; `claude_pretool` calls `verdict` itself
        sys.stdout.reconfigure(encoding="utf-8")
        if (answer := verdict(json.loads(sys.stdin.buffer.read().decode("utf-8")) or {})):
            sys.stdout.write(json.dumps(answer, ensure_ascii=False))
    except Exception:  # noqa: BLE001 -- `craft/hooks-fail-open`
        pass
