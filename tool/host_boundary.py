"""PreToolUse: redirect obsolete desktop transport to the native workflow."""

import hook_diagnostics  # noqa: F401
import json
import os
import re
import sys

from common.host import INSTRUCTIONS

# Covers direct/nested calls and the discovery commands seen in the incident.
# This is an accidental-host guard, not a sandbox for arbitrary generated code.
CALL = re.compile(
    r"(?:(?:^|[;&|\n])[ &\"']*orca(?:\.exe|\.cmd|\.ps1)?(?:[\s\"']|$)|"
    r"\borca(?:\.exe|\.cmd|\.ps1)?\s+(?:terminal|worktree|skills|browser|computer|"
    r"message|worker|artifact|repo|--help|--version|--json)\b|"
    r"(?:Get-Command|where(?:\.exe)?|which|command\s+-v)\s+orca\b|"
    r"\b(?:pwsh|powershell)(?:\.exe)?\s+(?:-NoProfile\s+|-NonInteractive\s+|-NoLogo\s+)*"
    r"-(?:Command|c)\s+[ &\"']*orca(?:\.exe|\.cmd|\.ps1)?(?:[\s\"']|$)|"
    r"\bcmd(?:\.exe)?\s+/c\s+[ &\"']*orca(?:\.exe|\.cmd|\.ps1)?(?:[\s\"']|$)|"
    r"ORCA_(?:CLI_COMMAND|TERMINAL_HANDLE|AGENT_HOOK_\w+)|"
    r"[\\/]orca(?:\.exe|\.cmd|\.ps1)[\"'\s]|[\\/]\.orca[\\/]agent-hooks[\\/])", re.I,
)


def verdict(payload: dict) -> dict | None:
    if os.environ.get("WIKI_AGENT_MANAGED") != "1":
        return None
    tool = str(payload.get("tool_name") or "")
    given = payload.get("tool_input") or {}
    if not isinstance(given, dict):
        return None
    command = str(given.get("command") or given.get("cmd") or given.get("code") or "")
    skill = str(given.get("skill") or "")
    blocked = (tool == "Skill" and skill in {"orca-cli", "orchestration", "computer-use"})
    blocked = blocked or (tool in {"Bash", "exec_command", "shell_command", "shell", "exec"}
                          and bool(CALL.search(command))) or tool.lower().startswith("mcp__orca")
    if not blocked:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "wiki-agent 자체 기능을 사용하세요. 리뷰 루프·작업 상태는 앱이 관리합니다. "
                                    "ORCA 조회 실패를 이유로 작업을 취소하지 마세요.",
        "additionalContext": INSTRUCTIONS,
    }}


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
