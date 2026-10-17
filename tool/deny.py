"""Evaluate shared deny rules only for projects attached by the dispatcher."""

# First, so the wait on the other imports and on stdin is already being watched.
import hook_diagnostics  # noqa: F401
from fnmatch import fnmatchcase
import json
import sys

from pytest_output import held_back, reason as output_reason  # noqa: F401 -- public hook helper


def verdict(payload: dict) -> dict | None:
    from apply import declared

    given = payload.get("tool_input") or {}
    tool = payload.get("tool_name")
    command = str(given.get("command") or "").strip()
    denies, _ = declared()
    # Tools and Bash argument patterns share `enforce.deny` declarations.
    # ponytail: only the declared pattern of the direct command is matched. A
    # nested shell can bypass it; a security boundary needs host execution policy.
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
    reason = output_reason(command) if tool == "Bash" else ""
    if reason:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
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
