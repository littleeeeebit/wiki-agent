"""Shell commands the wiki fixes and runs instead of refusing.

A refusal costs the agent a turn and teaches it nothing it could not have been
handed: here the repaired command goes back to the host (`updatedInput`), the
checks judge what will really run, and the agent is told what changed.
"""

import re
import tempfile
import time
from pathlib import Path

from pytest_output import _blank, teed

# `powershell` is Windows PowerShell 5.1, another product than `pwsh` 7; nothing here needs it
# (`operator/run-inside-this-session`). Only a command word is renamed: the start, or after an
# operator or `$(`, found in the copy with quoted operators blanked, so a message mentioning it
# keeps its text. ponytail: a heredoc line that starts with it is renamed too; quote it if that matters.
POWERSHELL = re.compile(r"(?i)(^|[;&|(\n]\s*)powershell(?:\.exe)?(?=\s|$)")
NO_PROFILE = re.compile(r"(?i)\s+-(?:NoProfile|nop)\b")


def _pwsh(command: str) -> str:
    bare, out, cursor = _blank(command), [], 0
    for match in POWERSHELL.finditer(bare):
        out.append(command[cursor:match.end(1)])
        cursor = match.end()
        out.append("pwsh" if NO_PROFILE.match(command, cursor) else "pwsh -NoProfile")
    return "".join(out) + command[cursor:]


def repaired(command: str) -> tuple[str, list[str]]:
    """The command to run instead, and a line for the agent per repair; `command` itself when none applies."""

    notes = []
    fixed = _pwsh(command)
    if fixed != command:
        notes.append("`powershell` (Windows PowerShell 5.1) ran as `pwsh -NoProfile` (PowerShell 7). "
                     "Call `pwsh` directly.")
    log = (Path(tempfile.gettempdir()) / f"pytest-{time.time_ns() // 1_000_000}.log").as_posix()
    log = f'"{log}"' if " " in log else log
    teed_ = teed(fixed, log)
    if teed_ != fixed:
        notes.append(f"pytest output piped into a filter also goes to {log} as it runs. "
                     "Nothing to do now; if the run seems stuck, that file tells hung from slow.")
    return teed_, notes


def repair(payload: dict) -> tuple[dict, list[str]]:
    """`payload` with its shell command repaired, and the notes; the same payload when nothing changed."""

    import guard

    tool, command, _dialect = guard.shell_command(payload)
    if tool not in guard.SHELL_TOOLS or not isinstance(command, str) or not command:
        return payload, []
    fixed, notes = repaired(command)
    if fixed == command:
        return payload, []
    given = dict(payload["tool_input"])
    given["command" if "command" in given else "cmd"] = fixed
    return {**payload, "tool_input": given}, notes
