"""Claude PreToolUse: every wiki check in one process, answering as the separate hooks did together.

Claude used to start one interpreter per check, seven per tool call, and the
starts were most of the time. Here they run in turn in one. The answer is
what Claude made of the separate answers: any `deny` denies, and the
`systemMessage` and `additionalContext` of every check are all kept. Which
denial's reason Claude showed depended on which process finished first; here
it is the first in `MEMBERS`, the order `codex_pretool` already uses.

`--without a.py,b.py` leaves checks out: `deny.py` where `permissions.deny`
does its job (a per-project install), and whatever a checkout's own old
install still runs (`hook.legacy`).
"""

# First, so the wait on the other imports and on stdin is already being watched.
import hook_diagnostics  # noqa: F401
import json
from pathlib import Path
import sys

MEMBERS = ("host_boundary.py", "deny.py", "guard.py", "edit_as_diff.py", "memory_words.py",
           "markdown_emphasis.py", "english_progress.py")


def check(name: str, payload: dict) -> dict | None:
    import importlib

    module = importlib.import_module(name.removesuffix(".py"))
    if name == "edit_as_diff.py":
        return module.verdict(payload, Path(str(payload.get("cwd") or Path.cwd())))
    return module.verdict(payload)


def verdict(payload: dict, without: frozenset = frozenset()) -> dict | None:
    answers = []
    for name in MEMBERS:
        if name in without:
            continue
        try:
            answer = check(name, payload)
        except Exception:  # noqa: BLE001 -- each check passed on its own failure, `craft/hooks-fail-open`
            answer = None
        if answer:
            answers.append(answer)
    if not answers:
        return None
    denied = [a for a in answers if (a.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"]
    out = json.loads(json.dumps(denied[0] if denied else {}))
    messages = [a["systemMessage"] for a in answers if a.get("systemMessage")]
    if messages:
        out["systemMessage"] = "\n".join(messages)
    contexts = [c for a in answers if (c := (a.get("hookSpecificOutput") or {}).get("additionalContext"))]
    if contexts:
        out.setdefault("hookSpecificOutput", {"hookEventName": "PreToolUse"})["additionalContext"] = "\n\n".join(contexts)
    return out


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        args = sys.argv[1:]
        without = frozenset(n for i, a in enumerate(args[:-1]) if a == "--without" for n in args[i + 1].split(","))
        payload = json.load(sys.stdin)
        answer = verdict(payload, without) if isinstance(payload, dict) else None
        if answer:
            json.dump(answer, sys.stdout, ensure_ascii=False)
    except Exception as error:
        print(f"hook skipped: {type(error).__name__}", file=sys.stderr)
