"""Keep pytest progress visible instead of buffering it through output filters."""

import re

_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')
_TEST_RUN = re.compile(r"-m\s+pytest\b|(?:^|\s)(?:\S*[/\\])?pytest(?:\.exe)?(?=\s|$)")
_INSTALL = re.compile(r"\binstall\b")  # `pip install pytest | tail` installs, it runs nothing


def held_back(command: str) -> str:
    """The pipe stage a pytest run's output is fed into, unless it is `tee`.
    `| tail` or `| grep` hold the run's progress until it ends, so a gate hung
    on one test reads exactly like a slow one and nobody can tell which. Quoted
    text is blanked first, so a `|` in a pattern or a commit message is not a pipe."""

    bare = _QUOTED.sub('""', command)
    for chain in re.split(r"&&|\|\||;|\n", bare):
        stages = [s.strip() for s in chain.split("|")]
        for before, after in zip(stages, stages[1:]):
            if _TEST_RUN.search(before) and not _INSTALL.search(before) and after.split()[:1] != ["tee"]:
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
