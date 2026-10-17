"""Keep pytest progress visible instead of buffering it through output filters."""

import re
import shlex

_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')


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


def held_back(command: str) -> str:
    """The pipe stage a pytest run's output is fed into, unless it is `tee`.
    `| tail` or `| grep` hold the run's progress until it ends, so a gate hung
    on one test reads exactly like a slow one and nobody can tell which. Quoted
    operators are blanked so patterns and messages cannot introduce pipelines."""

    bare = _QUOTED.sub(lambda match: re.sub(r"[|;&\n]", " ", match[0]), command)
    for chain in re.split(r"&&|\|\||;|\n", bare):
        stages = [s.strip() for s in chain.split("|")]
        for before, after in zip(stages, stages[1:]):
            if runs_pytest(before) and after.split()[:1] != ["tee"]:
                return after
    return ""


def reason(command: str) -> str:
    stage = held_back(command)
    if not stage:
        return ""
    return (
                f"테스트 실행(pytest)의 출력을 `| {stage}` 로 넘기면 끝날 때까지 진행 상황이 보이지 않아, "
                "멈춘 실행과 느린 실행을 가릴 수 없다. 이 명령은 실행하지 않았다. 파이프 없이 다시 실행하라 — "
                "오래 걸리면 run_in_background 로 띄우고 그 출력 파일을 읽는다. "
                "줄을 걸러 보려면 `| tee <로그 파일>` 로 남기고 그 파일을 읽는다.")
