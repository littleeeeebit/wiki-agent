"""Read-only native proposal and critic adapters for explicit experiment runs."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from agent import ChatSession

PROPOSE = """Propose a general improvement to this repository's agent harness or
workflow. Read only inside the supplied candidate root. The supplied context
belongs to its owner. Respect its rules. Return a unified Git patch; do not edit
files, run other agents, change evaluation machinery, or deploy anything.
Use the incumbent's failure traces and all attempted hypotheses. Avoid repeating
rejected hypotheses. Declare each independent edit separately with component,
hypothesis, and exact repository-relative paths. Respect edit_budget and any
exploration_required directive. Prune directives request measured removal
experiments, not assumptions that a component is useless. Improve a general
procedure, never encode task IDs, expected answers or benchmark-specific branches.
Return only JSON: {"edits": [{"component": "...", "hypothesis": "...",
"paths": ["..."]}], "patch": "diff --git ..."}."""

CRITIC = """Review the supplied harness patch independently. Read only inside the
candidate root. Do not edit files, call other agents or evaluate tasks. Reject
task-specific hardcoding, answer memorization, cross-trial capture of task
solutions, evaluator or rubric manipulation, removal of required safety without
a replacement, unbounded work, undeclared independent edits, and component tags
that misdescribe the change. General procedures and bounded mechanisms are valid.
This is a leakage and mechanism review; runtime tests run separately afterwards.
Return only JSON: {"verdict": "accept" | "reject", "reasons": ["..."]}."""


def response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("The role returned no JSON object")
    return value


def run(request: dict, model: str, effort: str) -> dict:
    if request["stage"] not in ("propose", "critic"):
        raise ValueError("Native host adapter accepts only proposal or critic requests")
    halted = threading.Event()
    tokens, answer, completed = 0, "", False
    chat = ChatSession(Path(request["root"]), tools="Read,Glob,Grep", model=model, effort=effort,
                       system=PROPOSE if request["stage"] == "propose" else CRITIC)

    def expire():
        halted.set()
        chat.stop(halted)

    timer = threading.Timer(request["limits"]["seconds"], expire)
    timer.daemon = True
    timer.start()
    try:
        for event in chat.say(json.dumps(request, ensure_ascii=False), halted):
            if event.kind != "done":
                continue
            usage = event.meta.get("tokens")
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("in", "out")):
                return {"error": "Native host token usage is unavailable"}
            tokens += usage["in"] + usage["out"]
            answer, completed = event.text, not event.meta.get("error", False)
        if not completed or halted.is_set():
            return {"error": "Native host did not complete the bounded role turn",
                    **({"usage": {"calls": 1, "tokens": tokens}} if answer else {})}
        try:
            result = response(answer)
        except ValueError:
            return {"error": "Native role output is not a JSON object", "usage": {"calls": 1, "tokens": tokens}}
        return {**result, "usage": {"calls": 1, "tokens": tokens}, "role_model": model, "role_session": chat.id}
    finally:
        timer.cancel()
        chat.close()


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run one bounded, read-only improvement role")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()
    try:
        result = run(json.load(sys.stdin), args.model, args.effort)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 1 if "error" in result else 0
    except (ValueError, RuntimeError, OSError) as exc:
        # Missing usage remains unknown; the controller stops instead of charging zero.
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
