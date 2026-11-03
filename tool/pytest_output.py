"""Keep pytest progress visible instead of buffering it through output filters."""

import re
import shlex

# A quoted string, by the shell's own escapes: `\"` in Git Bash, `` `" `` in PowerShell, whose
# single quotes double (`''`) rather than escape, and whose here-strings (`@'` ... `'@` on lines
# of their own) hold quotes freely.
_QUOTED = {
    "posix": re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\''),
    "ps": re.compile(r'(?s:@\'\r?\n.*?\n\'@)|(?s:@"\r?\n.*?\n"@)|"(?:[^"`]|`.)*"|\'(?:[^\']|\'\')*\''),
}


def runs_pytest(stage: str) -> bool:
    try:
        words = [word.strip("\"'") for word in shlex.split(stage, posix=False)]
    except ValueError:
        return False
    # shortcut: inspect direct argv, not nested shell scripts or env -S strings; extend for observed commands.
    while words:
        if re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
            words.pop(0)
            continue
        wrapper = words[0].replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")
        if wrapper not in {"env", "command", "exec", "time", "timeout"}:
            break
        words.pop(0)
        while words and words[0].startswith("-"):
            option = words.pop(0)
            if option == "--":
                break
            if wrapper == "command" and set(option[1:]) & {"v", "V"}:
                return False
            if option in {"-u", "--unset", "-C", "--chdir", "-a", "-o", "--output", "-f", "--format",
                          "-k", "--kill-after", "-s", "--signal"}:
                del words[:1]
        if wrapper == "timeout":
            del words[:1]
    if not words:
        return False
    executable = words[0].replace("\\", "/").rsplit("/", 1)[-1]
    if re.fullmatch(r"pytest(?:\.exe)?", executable):
        return True
    if re.fullmatch(r"(?:python[\d.]*|py)(?:\.exe)?", executable):
        args = iter(words[1:])
        for arg in args:
            if arg == "-m":
                return next(args, "") == "pytest"
            if arg in {"-W", "-X"}:
                next(args, None)
            elif not arg.startswith("-") or arg == "-c":
                return False
    return False


def _tee(stage: str) -> bool:
    # `tee` is also PowerShell's alias of `Tee-Object`, so Codex's shell reads it the same.
    return stage.split()[:1] in (["tee"], ["Tee-Object"], ["tee-object"])


def _blank(command: str, dialect: str = "posix") -> str:
    """Quoted operators and data heredoc bodies blanked, same length, so patterns, messages and
    documents cannot introduce pipelines. Heredocs are Bash's: PowerShell reads `<<` as text."""
    from edit_as_diff import blank_data_heredocs

    text = blank_data_heredocs(command) if dialect == "posix" else command
    return _QUOTED[dialect].sub(lambda match: re.sub(r"[|;&\n]", " ", match[0]), text)


def held_back(command: str) -> str:
    """The pipe stage a pytest run's output is fed into, unless it is `tee`.
    `| tail` or `| grep` hold the run's progress until it ends, so a gate hung
    on one test reads exactly like a slow one and nobody can tell which."""

    for chain in re.split(r"&&|\|\||;|\n", _blank(command)):
        stages = [s.strip() for s in chain.split("|")]
        for before, after in zip(stages, stages[1:]):
            if runs_pytest(before) and not _tee(after):
                return after
    return ""


def teed(command: str, log: str, dialect: str = "posix") -> str:
    """`command` with every pytest run that feeds a filter also writing `log` as it goes.

    `pytest -q | tail -5` becomes `pytest -q 2>&1 | tee <log> | tail -5`: the
    filter still gets what it asked for, and the log shows whether a run that
    prints nothing is hung or slow."""

    bare = _blank(command, dialect)
    marks = list(re.finditer(r"&&|\|\||;|\n|\|", bare))
    out, cursor = [], 0
    for i, mark in enumerate(marks):
        if mark[0] != "|":
            continue
        before = bare[marks[i - 1].end() if i else 0:mark.start()]
        after = bare[mark.end():marks[i + 1].start() if i + 1 < len(marks) else len(bare)]
        # A pipe inside `$(...)` (or Git Bash's backticks) belongs to the substitution, not to the run.
        inside = "$(" in before or (dialect == "posix" and "`" in before)
        if runs_pytest(before.strip()) and not _tee(after.strip()) and not inside:
            joint = " |" if before.rstrip().endswith("2>&1") else " 2>&1 |"
            out.append(f"{command[cursor:mark.start()].rstrip()}{joint} tee {log} |")
            cursor = mark.end()
    return "".join(out) + command[cursor:]


def reason(command: str) -> str:
    stage = held_back(command)
    if not stage:
        return ""
    return (
                f"테스트 실행(pytest)의 출력을 `| {stage}` 로 넘기면 끝날 때까지 진행 상황이 보이지 않아, "
                "멈춘 실행과 느린 실행을 가릴 수 없다. 이 명령은 실행하지 않았다. 파이프 없이 다시 실행하라 — "
                "오래 걸리면 run_in_background 로 띄우고 그 출력 파일을 읽는다. "
                "줄을 걸러 보려면 `| tee <로그 파일>` 로 남기고 그 파일을 읽는다.")
