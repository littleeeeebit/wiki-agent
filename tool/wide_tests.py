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

from pytest_output import _QUOTED, _blank, runs_pytest

LIMIT = 8
STAGES = {   # in Bash a lone `&` runs what is before it in the background; `2>&1` and `&>` are redirections
    "posix": re.compile(r"&&|\|\||(?<![<>&])&(?![>&])|[;|\n]"),
    "ps": re.compile(r"&&|\|\||[;|\n]"),   # PowerShell's `&` calls the command after it
}
MOVED = re.compile(r"(cd|sl|Set-Location|pushd|Push-Location)(?:\s+(\S.*))?", re.IGNORECASE)
ENDED = re.compile(r"(?:exit|logout|throw)(?:\s.*)?", re.IGNORECASE)
READ = {   # what `joined` stops at, in each shell: a quoted span, an escaped character, a `#`
    True: re.compile(rf"(?P<quote>{_QUOTED['posix'].pattern})|(?P<escape>\\(?:\r\n|.))|(?P<hash>#)", re.DOTALL),
    False: re.compile(rf"(?P<quote>{_QUOTED['ps'].pattern})|(?P<escape>`(?:\r\n|.))|(?P<hash>#)", re.DOTALL),
}
POPPED = re.compile(r"(?:popd|Pop-Location)(?:\s.*)?", re.IGNORECASE)
# Every option pytest and the plugins installed here read a value from the next word, from pytest's own parser on
# 2026-11-26. An option whose value is optional takes the next word too, unless it reads as another option.
OPTIONAL = {"--debug", "--cache-show"}
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


def argv(text: str, posix: bool) -> list[str]:
    """`text` as the shell hands it on, one word per argument: a quote inside a word (`--junitxml="a b"`) joins
    it. PowerShell has no backslash escape, so a Windows path keeps its separators."""

    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split, lexer.commenters = True, ""
    if not posix:
        lexer.escape = ""
    return list(lexer)


def selected(stage: str, posix: bool = True) -> list[str]:
    """The paths a pytest stage names, options and their values left out."""

    try:
        words = argv(stage, posix)
    except ValueError:
        return []
    start = next((i + 1 for i, w in enumerate(words) if re.search(r"(?i)(^|[\\/])pytest(\.exe)?$", w)), len(words))
    paths, skip, target = [], False, False
    for word in words[start:]:
        if target:
            target = False
        elif re.match(r"(\d*|&)[<>]", word):   # a redirection (`&>` too), which pytest never sees, and its target
            target = bool(re.fullmatch(r"(\d*|&)(>>?|<)", word))
        elif skip and not (skip == "optional" and word.startswith("-")):
            skip = False
        elif word.startswith("-"):
            skip = word in VALUED or ("optional" if word in OPTIONAL else False)
        else:
            paths.append(word)
    return paths


def wide(stage: str, posix: bool = True) -> str:
    """Why the pytest run in `stage` is the wide one, or ""."""

    paths = selected(stage, posix)
    if not paths:
        return "경로 없이 전체"
    files = {p.split("::")[0] for p in paths}
    if broad := [p for p in files if any(c in p for c in "*?[") or not p.endswith(".py")]:
        return f"폴더나 패턴 {', '.join(sorted(broad))}"
    return f"파일 {len(files)}개" if len(files) > LIMIT else ""


def joined(command: str, posix: bool) -> str:
    """`command` read left to right as the shell reads it, quotes, escapes and comments in turn: a comment at the
    start of a word goes, to its line's end, a backslash in it escaping nothing; a continued line is joined (Bash
    drops `\\` and the newline, so a word may go on across it; PowerShell's backtick and newline part words); a
    quoted or escaped `#` stays text."""

    # Whether a `#` starts a word is read off the text joined so far, so a continuation the shell drops is no
    # boundary of its own; `glued` is that text's length where a quote or an escape last ended.
    out, at, glued, size, last = [], 0, -1, 0, ""

    def add(piece: str) -> None:
        nonlocal size, last
        out.append(piece)
        size, last = size + len(piece), piece[-1:] or last

    while m := READ[posix].search(command, at):
        add(command[at:m.start()])
        at = m.end()
        if m["hash"]:
            if (not size or last in " \t\r\n;&|(") and glued != size:
                end = command.find("\n", m.start())
                at = len(command) if end < 0 else end   # the newline stays: it ends the comment's line
            else:
                add("#")
        elif m["escape"] and m[0][1:] in ("\n", "\r\n"):
            add("" if posix else " ")
        else:
            add(m[0] if m["escape"] or not (posix and m[0].startswith('"')) else re.sub(r"\\\r?\n", "", m[0]))
            glued = size
    add(command[at:])
    return "".join(out)


def target(rest: str, posix: bool) -> str:
    """The folder a move names: its first operand, past switches (`cd -P`, `Set-Location -PassThru`), or the value
    of PowerShell's `-Path` or `-LiteralPath`. `-` is `cd -`; none at all is home."""

    words = iter(argv(rest, posix))
    for word in words:
        name, colon, value = word.partition(":")
        if not posix and name.lower() in ("-path", "-literalpath", "-pspath", "-lp"):
            return value if colon else next(words, "~")
        if word == "-" or not word.startswith("-"):
            return word
    return "~"


def stages(command: str, dialect: str) -> list[tuple[str, str]]:
    """`command`, already `joined`, split where the shell runs a new command, each stage with the operator before
    it ("" first). An operator inside quotes or a heredoc is text, and a line broken after `&&`, `||` or `|` goes
    on the same list."""

    blank, cuts, ops = _blank(command, dialect), [0], [""]
    for match in STAGES[dialect].finditer(blank):   # the same length as `command`, so its places are `command`'s
        cuts += [match.start(), match.end()]
        ops.append(match[0])
    cuts.append(len(command))
    split, carried = [], None
    for op, a, b in zip(ops, cuts[::2], cuts[1::2]):
        op = carried if carried and op == "\n" else op
        carried = op if op in ("&&", "||", "|", "&") else None   # a newline after `&` keeps it backgrounded
        if text := command[a:b].strip():
            split.append((op, text))
            carried = None
    return split


def places(command: str, posix: bool, here: Path):
    """Each stage of `command` with every folder it may run in. The shell is followed as a set of states, each a
    folder, its `pushd` stack and the folder `cd -` returns to, split by how the last stage may have ended: `&&`
    runs the next only where it succeeded, `||` only where it failed, anything else everywhere. A move into a
    folder that exists now succeeds; into one that does not it may fail and stay, unless a stage before made it.
    Any other stage may end either way. A move whose target cannot be read ends the walk: where the rest runs is
    unknown, and nothing after it is refused."""

    ok, failed = {(Path(os.path.abspath(here)), (), None)}, set()
    split = stages(command, "posix" if posix else "ps")
    # A Bash list (stages joined by `&&`, `||` or `|`) that `&` ends runs whole in a subshell: its moves hold
    # inside it, and the shell goes on after it from where the list began.
    ends = [i + 1 == len(split) or split[i + 1][0] in (";", "\n", "&") for i in range(len(split))]
    background = [posix and end and i + 1 < len(split) and split[i + 1][0] == "&" for i, end in enumerate(ends)]
    before = None
    for i, (op, stage) in enumerate(split):
        if i == 0 or ends[i - 1]:
            before = ok | failed   # where this list begins
        runs = ok if op == "&&" else failed if op == "||" else ok | failed
        kept_ok, kept_failed = (set(), failed) if op == "&&" else (ok, set()) if op == "||" else (set(), set())
        yield stage, {folder for folder, _, _ in runs}
        # Bash runs each part of a pipeline in a subshell: no move made there outlives it
        after = split[i + 1][0] if i + 1 < len(split) else ""
        piped = posix and "|" in (op, after)
        moved = None if piped else MOVED.fullmatch(stage)
        if moved:
            try:
                to = target(moved[2] or "", posix)
            except ValueError:
                return
            if os.name == "nt":
                to = re.sub(r"^/([A-Za-z])(?=/|$)", r"\1:", to)   # Git Bash's `/c/...`
            pushed, went, stayed = moved[1].lower() in ("pushd", "push-location"), set(), set()
            for state in runs:
                folder, stack, previous = state
                into = (previous or folder) if to == "-" else Path(os.path.abspath(folder / os.path.expanduser(to)))
                went.add((into, (*stack, folder) if pushed else stack, folder))
                if not into.is_dir():
                    stayed.add(state)
        elif not piped and ENDED.fullmatch(stage):   # the shell stops here, on this branch
            went = stayed = set()
        elif not piped and POPPED.fullmatch(stage):
            went = {(stack[-1], stack[:-1], folder) if stack else (folder, stack, previous)
                    for folder, stack, previous in runs}
            stayed = set()
        else:
            went = stayed = runs
        ok, failed = went | kept_ok, stayed | kept_failed
        if background[i]:   # launched, the list leaves the shell where it was, and `&` itself succeeds
            ok, failed = before, set()


def verdict(payload: dict) -> dict | None:
    tool = payload.get("tool_name")
    if tool not in ("Bash", "PowerShell"):
        return None
    posix = tool != "PowerShell"
    command = joined(str((payload.get("tool_input") or {}).get("command") or ""), posix)
    if "pytest" not in command.lower():   # Windows finds `PYTEST.EXE` as well
        return None
    for stage, folders in places(command, posix, Path(str(payload.get("cwd") or Path.cwd()))):
        try:
            words = argv(stage, posix)
        except ValueError:
            continue
        if not posix and words[:1] == ["&"]:   # PowerShell's call operator, before a quoted interpreter's path
            words = words[1:]
        if runs_pytest(stage, words) and any(guarded(f) for f in folders) and (what := wide(stage, posix)):
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
