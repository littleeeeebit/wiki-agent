"""Codex PreToolUse: the shared wiki's command blocks and its own checks, together."""

# First, so the wait on the other imports and on stdin is already being watched.
import hook_diagnostics  # noqa: F401
import json
from pathlib import Path
import sys

def verdict(payload: dict) -> dict | None:
    from command_repair import repair

    # Codex takes `updatedInput` only with `allow`, which there lets the call through without
    # blocking; the approval policy still decides. The checks judge the repaired command.
    try:
        fixed, notes = repair(payload)
    except Exception:  # noqa: BLE001 -- `craft/hooks-fail-open`
        fixed, notes = payload, []
    answer = checked(fixed)
    if fixed is payload or ((answer or {}).get("hookSpecificOutput") or {}).get("permissionDecision") == "deny":
        return answer
    answer = answer or {}
    output = answer.setdefault("hookSpecificOutput", {"hookEventName": "PreToolUse"})
    output.update(permissionDecision="allow", updatedInput=fixed["tool_input"])
    output["additionalContext"] = "\n\n".join(filter(None, [output.get("additionalContext"), *notes]))
    return answer


def checked(payload: dict) -> dict | None:
    import host_boundary
    import deny
    import edit_as_diff
    import english_progress
    import guard
    import markdown_emphasis
    import memory_words

    denied = host_boundary.verdict(payload) or deny.verdict(payload) or guard.verdict(payload, host="codex")
    if denied:
        return denied
    answer = edit_as_diff.verdict(payload, Path(str(payload.get("cwd") or Path.cwd())))
    if answer:
        output = answer["hookSpecificOutput"]
        output["permissionDecisionReason"] = output["permissionDecisionReason"].replace(
            "Edit", "apply_patch"
        ).replace("Write", "apply_patch")
        return answer
    # Codex takes one PreToolUse hook, so the checks are chained here. On
    # Claude `apply.py` wires each one separately out of a page's `enforce.pretooluse`.
    # The deny-only memory check goes first: an informational answer ends the chain.
    return memory_words.verdict(payload) or markdown_emphasis.verdict(payload) or english_progress.verdict(payload)


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
