"""Shell commands the wiki fixes and runs instead of refusing.

A refusal costs the agent a turn and teaches it nothing it could not have been
handed: here the repaired command goes back to the host (`updatedInput`), the
checks judge what will really run, and the agent is told what changed.
"""

import re
import os
import tempfile
import time
from pathlib import Path

from pytest_output import _QUOTED, teed
from common import shell

# The resolver owns the version choice (`operator/run-inside-this-session`).
# PowerShell discovery is shared with capability inspection. This legacy pattern
# remains for POSIX, whose quoting and heredoc rules are separate.
POWERSHELL = re.compile(r"(?i)(^|[;&|(\n]\s*)powershell(?:\.exe)?(?=\s|$)")
NO_PROFILE = re.compile(r"(?i)\s+-(?:NoProfile|nop)\b")


def _pwsh(command: str, dialect: str) -> str:
    from edit_as_diff import blank_data_heredocs

    if dialect == "ps":
        if os.name == "nt" and shell.chosen().major < 7:
            return command
        replacements = []
        for match in shell.invocations(command):
            start, end = match.span("target")
            if re.fullmatch(r"powershell(?:\.exe)?", command[start:end], re.I):
                replacements.append((start, end, "pwsh" if NO_PROFILE.match(command, end) else "pwsh -NoProfile"))
        for start, end, replacement in reversed(replacements):
            command = command[:start] + replacement + command[end:]
        return command
    text = blank_data_heredocs(command) if dialect == "posix" else command  # `<<` is Bash's
    bare = _QUOTED[dialect].sub(lambda m: " " * len(m[0]), text)
    if not POWERSHELL.search(bare):
        return command
    if os.name == "nt" and shell.chosen().major < 7:
        return command
    out, cursor = [], 0
    for match in POWERSHELL.finditer(bare):
        out.append(command[cursor:match.end(1)])
        cursor = match.end()
        out.append("pwsh" if NO_PROFILE.match(command, cursor) else "pwsh -NoProfile")
    return "".join(out) + command[cursor:]


def refusal(payload: dict, host: str = "claude") -> dict | None:
    """A 5.1 session cannot execute a known 7-only construct."""
    import guard

    tool, command, dialect = guard.shell_command(payload, host)
    if os.name != "nt" or tool not in guard.SHELL_TOOLS or not isinstance(command, str):
        return None
    if dialect != "ps" and not POWERSHELL.search(command):
        return None
    try:
        if shell.classic_seven_only(command):
            raise ValueError(f"Windows PowerShell에는 7 전용 명령을 넘길 수 없다. '{shell.INSTALL_CONTROL}'에서 확인하세요.")
        if shell.chosen().major >= 7:
            return None
        shell.resolve("gate", form="ps5", command=command)
        if dialect == "ps":
            from pytest_output import held_back
            if held_back(command, dialect):
                shell.resolve("pytest-tee", form="ps5", log="fixture.log")
    except ValueError as exc:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": str(exc)}}
    return None


# `fd` or `jq` that looks like a command word (`cli_tools`): when this session's PATH misses it,
# its install folder goes on the end of PATH for this command; when it is not there at all, it is
# installed in the background. The command itself is never edited, so the shell, not this
# pattern, decides what is a command: a word it takes wrongly only adds a folder to PATH.
TOOL = re.compile(r"(?:^|[;&|({\n])\s*(?P<name>fd|jq)(?:\.exe)?(?=[\s;&|)}]|$)")


def _tools(command: str, dialect: str) -> tuple[str, list[str]]:
    """The prefix that puts missing tools' folders on PATH, and the notes."""

    import shutil

    import cli_tools
    from edit_as_diff import blank_data_heredocs

    text = blank_data_heredocs(command) if dialect == "posix" else command   # a document naming one is text
    bare = _QUOTED[dialect].sub(lambda m: " " * len(m[0]), text)
    folders, notes = [], []
    for name in dict.fromkeys(m["name"] for m in TOOL.finditer(bare)):
        if shutil.which(name):
            continue
        if path := cli_tools.found(name):
            folders.append(path.rsplit("/", 1)[0])
            notes.append(f"`{name}` is installed but not on this session's PATH; this command ran with "
                         f"{folders[-1]} added to it.")
        elif cli_tools.started(name):
            notes.append(f"`{name}` is not installed; it is being installed in the background (a few "
                         "minutes). Until then use `rg --files` to list files or Python's `json` for JSON.")
    if not folders:
        return "", notes
    if dialect == "ps":
        return "".join(f"$env:PATH += ';{f.replace(chr(39), chr(39) * 2)}'; " for f in folders), notes
    # Git Bash reads `C:/x` in PATH as two entries split at the colon: it wants `/c/x`.
    posix = [re.sub(r"^([A-Za-z]):", lambda m: "/" + m[1].lower(), f).replace("'", "'\\''") for f in folders]
    return "".join(f"PATH=\"$PATH\":'{f}'; " for f in posix), notes


def _unread_heredoc(command: str) -> bool:
    """A `<<` outside quotes that `HEREDOC` does not read as an opener, or one whose body never
    closes (arithmetic `<<` included)."""
    from edit_as_diff import HEREDOC, blank_data_heredocs, unclosed_heredoc

    if unclosed_heredoc(command):
        return True
    text = blank_data_heredocs(command)  # a `<<` written inside a body it did read is text
    read = {m.start() for m in HEREDOC.finditer(text)}
    bare = _QUOTED["posix"].sub(lambda m: " " * len(m[0]), text)
    return any(m.start() not in read for m in re.finditer(r"(?<!<)<<(?!<)", bare))


def repaired(command: str, dialect: str = "posix") -> tuple[str, list[str]]:
    """The command to run instead, and a line for the agent per repair; `command` itself when none applies.
    `dialect` is the shell's (`guard.shell_command`): its quotes decide what is text."""

    if dialect == "posix" and _unread_heredoc(command):
        return command, []  # a body it cannot place may be data: no repair, the old refusals stand
    path, notes = _tools(command, dialect)
    fixed = _pwsh(command, dialect)
    if fixed != command:
        notes.append("`powershell` (Windows PowerShell 5.1) ran as `pwsh -NoProfile` (PowerShell 7). "
                     "Call `pwsh` directly.")
    if dialect == "ps" and os.name == "nt" and shell.chosen().major < 7:
        compatible = shell.resolve("gate", form="ps5", command=fixed)
        if compatible != fixed:
            notes.append("The command used the compatible PowerShell 5.1 form.")
        fixed = compatible
    log = (Path(tempfile.gettempdir()) / f"pytest-{time.time_ns() // 1_000_000}.log").as_posix()
    log = f'"{log}"' if " " in log else log
    teed_ = teed(fixed, log, dialect)
    if teed_ != fixed:
        notes.append(f"pytest output piped into a filter also goes to {log} as it runs. "
                     "Nothing to do now; if the run seems stuck, that file tells hung from slow.")
    return _prefixed(teed_, path, dialect, notes), notes


# PowerShell wants `using`, `param(...)` with its attributes and named blocks before any statement.
# Placing a prefix after them needs PowerShell's parser (comments, case, nested brackets), so a
# script that has one anywhere, even in a comment or a string, gets no prefix.
FIRST = re.compile(r"(?i)\b(?:using|param|begin|process|end|dynamicparam|clean)\b")


def _prefixed(command: str, prefix: str, dialect: str, notes: list[str]) -> str:
    if not prefix or dialect != "ps" or not FIRST.search(command):
        return prefix + command
    notes[:] = [n.replace("; this command ran with", " at").replace("added to it.", "; call it by that "
                "path: this script's `using`, `param` or named block has to come first, so the hook left "
                "PATH alone.") if "ran with" in n else n for n in notes]
    return command


def repair(payload: dict, host: str = "claude") -> tuple[dict, list[str]]:
    """`payload` with its shell command repaired, and the notes; the same payload when nothing changed,
    with any notes still (a background install's). Codex drops those: it takes context only with `allow`."""

    import guard

    tool, command, dialect = guard.shell_command(payload, host)
    if tool not in guard.SHELL_TOOLS or not isinstance(command, str) or not command:
        return payload, []
    fixed, notes = repaired(command, dialect)
    if fixed == command:
        return payload, notes
    given = dict(payload["tool_input"])
    given["command" if "command" in given else "cmd"] = fixed
    return {**payload, "tool_input": given}, notes
