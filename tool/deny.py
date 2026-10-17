"""PreToolUse: the pages' `enforce.deny`, judged by a script instead of the host.

A per-project install writes these into `permissions.deny`, where they apply
to that checkout. The user-level install cannot: `~/.claude/settings.json`
applies to every directory on the machine, including repositories that never
attached this wiki. Behind `hook.py` the same rules reach only attached
projects. Codex has no deny list at all and chains this from `codex_pretool`.
"""

# First, so the wait on the other imports and on stdin is already being watched.
import hook_diagnostics  # noqa: F401
from fnmatch import fnmatchcase
import json
import re
import sys

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


def verdict(payload: dict) -> dict | None:
    from apply import declared

    given = payload.get("tool_input") or {}
    tool = payload.get("tool_name")
    command = str(given.get("command") or "").strip()
    denies, _ = declared()
    # Blocking a whole tool and blocking a Bash argument pattern read the same
    # `enforce.deny` declaration.
    # ponytail: only the declared pattern of the direct command is matched. A
    # nested shell or a command built at runtime gets past it, and making this
    # a security boundary means enforcing it in the host's execution policy.
    for rule in denies:
        blocked = rule == tool
        if tool == "Bash" and rule.startswith("Bash(") and rule.endswith(")"):
            blocked = fnmatchcase(command, rule[5:-1])
        if blocked:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"공유 위키의 차단 규칙: {rule}",
            }}
    stage = held_back(command) if tool == "Bash" else ""
    if stage:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"테스트 실행(pytest)의 출력을 `| {stage}` 로 넘기면 끝날 때까지 진행 상황이 보이지 않아, "
                "멈춘 실행과 느린 실행을 가릴 수 없다. 이 명령은 실행하지 않았다. 파이프 없이 다시 실행하라 — "
                "오래 걸리면 run_in_background 로 띄우고 그 출력 파일을 읽는다. "
                "줄을 걸러 보려면 `| tee <로그 파일>` 로 남기고 그 파일을 읽는다."),
        }}
    return None


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        payload = json.load(sys.stdin)
        answer = verdict(payload) if isinstance(payload, dict) else None
        if answer:
            json.dump(answer, sys.stdout, ensure_ascii=False)
    except Exception as error:
        print(f"hook skipped: {type(error).__name__}", file=sys.stderr)
