"""The deny a session carrying a oneshot's secrets gets for any call a hook could not check.

Standard library only, so a guard that cannot load, or a check that fails, still denies: the dispatcher
(`hook.py`), both PreToolUse wrappers and the guard itself fall back to it.
"""

from __future__ import annotations

import json
import os
import sys

# Set in a session that carries a oneshot's secrets (`main.work.session`): it never pushes itself; the server
# pushes once its secret check passes (`main.prerequisite_review.push`). A hyphen: never a need's name.
SECRETS_HELD = "WIKI-AGENT-SECRETS-HELD"


def held_closed(why: str) -> dict | None:
    """The deny for a call no check answered, in a session carrying a oneshot's secrets; `None` in any other."""
    if not os.environ.get(SECRETS_HELD):
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": f"{why} — 비밀 값을 가진 세션이라 확인하지 못한 호출은 "
                                                               "막는다. 커밋만 하고, 푸시는 서버가 한다"}}


def closed_out(error: Exception) -> None:
    """A hook process's own failure: its deny written out, in such a session only."""
    if closed := held_closed(f"훅이 실패했다 ({type(error).__name__})"):
        sys.stdout.reconfigure(encoding="utf-8")   # the failure may come before the caller's own
        try:   # through the deadline's once-only writer, standard library only too
            deadline = __import__("hook_deadline")
        except Exception:  # noqa: BLE001 -- it could not load: written directly, as nothing else writes
            json.dump(closed, sys.stdout, ensure_ascii=False)
            return
        deadline.send(closed)   # nothing when an answer already began: a broken stream gets no second one
