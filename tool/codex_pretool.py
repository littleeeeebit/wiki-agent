"""Codex PreToolUse: the shared wiki's command blocks and its own checks, together."""

# First, so the wait on the other imports and on stdin is watched and a credential deny armed.
import hook_deadline
import hook_diagnostics  # noqa: F401
import json
from pathlib import Path
import sys

def verdict(payload: dict) -> dict | None:
    from command_repair import refusal, repair

    # Codex takes `updatedInput` only with `allow`, which there lets the call through without
    # blocking; the approval policy still decides. The checks judge the repaired command.
    try:
        fixed, notes = repair(payload, "codex")
    except Exception:  # noqa: BLE001 -- `craft/hooks-fail-open`
        fixed, notes = payload, []
    answer = refusal(fixed, "codex") or checked(fixed)
    if ((answer or {}).get("hookSpecificOutput") or {}).get("permissionDecision") == "deny":
        return answer
    # Last, on the call every check let through: the load line, and a GPU job refused or queued (`gpu_job`).
    import gpu_job

    gpu = gpu_job.verdict(fixed, "codex")
    if gpu and gpu["hookSpecificOutput"].get("permissionDecision") == "deny":
        return gpu
    queued = gpu and gpu["hookSpecificOutput"].pop("updatedInput", None)
    notes += [gpu["hookSpecificOutput"]["additionalContext"]] if gpu else []
    if fixed is payload and not queued and not notes:
        return answer
    output = (answer := answer or {}).setdefault("hookSpecificOutput", {"hookEventName": "PreToolUse"})
    if fixed is not payload or queued:
        output.update(permissionDecision="allow", updatedInput=queued or fixed["tool_input"])
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

    import owner_data

    denied = host_boundary.verdict(payload) or deny.verdict(payload, "codex") or guard.verdict(payload, host="codex") \
        or owner_data.verdict(payload)
    if denied:
        return denied
    dialect = guard.shell_command(payload, "codex")[2]  # PowerShell on Windows: its strings are its own
    answer = edit_as_diff.verdict(payload, Path(str(payload.get("cwd") or Path.cwd())), dialect)
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
            hook_deadline.send(answer)   # once: the deadline's answer never follows it, nor it the deadline's
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        __import__("secrets_held").closed_out(error)   # a session carrying a oneshot's secrets runs nothing unchecked
