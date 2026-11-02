"""Evaluate shared deny rules only for projects attached by the dispatcher."""

# First, so the wait on the other imports and on stdin is already being watched.
import hook_diagnostics  # noqa: F401
from fnmatch import fnmatchcase
import json
import sys

from pytest_output import held_back, reason as output_reason  # noqa: F401 -- public hook helper


def verdict(payload: dict) -> dict | None:
    import guard
    from apply import declared

    # Read the command the one way the guard does (`command` or `cmd`, string or argv, any tool-name
    # form, Codex's PowerShell); checking another projection is validate-before-canonicalize (CWE-180).
    raw_tool = str(payload.get("tool_name") or "")
    tool, raw, dialect = guard.shell_command(payload)
    command = (" ".join(map(str, raw)) if isinstance(raw, list) else str(raw)).strip()
    denies, _ = declared()
    # Tools and Bash argument patterns share `enforce.deny` declarations. A nested shell
    # (`bash -c '...'`, `$(...)`, `pwsh -Command '...'`) runs commands the direct pattern never
    # sees, so every command the guard's parser finds one level down is held to them too.
    inner = guard.nested_bash_commands(raw, dialect) if tool in guard.SHELL_TOOLS else []
    for rule in denies:
        blocked = rule == raw_tool
        if rule.startswith("Bash(") and rule.endswith(")"):
            pattern = rule[5:-1]
            if tool in guard.SHELL_TOOLS:
                blocked = fnmatchcase(command, pattern)
            blocked = blocked or any(fnmatchcase(c.strip(), pattern) for c in inner)
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
