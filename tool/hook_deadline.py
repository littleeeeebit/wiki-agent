"""Answer the host before its time limit does.

A host that times a hook out kills it and drops whatever it had printed: the
turn gets no injection and nobody is told. The diagnostics of 2026-11-19 show
where those runs stalled — imports, `stat`, reading pages — all before there
was anything to print. So a timer answers first: a few seconds before the
host's limit, the best output assembled so far goes out once and the process
exits 0. The hook keeps `best` current as it gets further.

Armed by the entry point's own first imports, like `hook_diagnostics`, so the
stalls in the imports after it are covered too.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading

# Seconds after arming at which the timer answers, a few under each hook's
# `timeout` in `apply.py` (15 and 25): interpreter start-up is not counted from
# here. `test_hook_deadline` holds the pair together.
# The third and fourth are what goes out when the hook got nowhere: English
# for the agent, Korean for the person (`operator/english-progress`).
LIMITS = {
    "inject.py": (12.0, "UserPromptSubmit", (
        "<!-- wiki:deadline -->\nThe wiki hook ran out of time before it could read its pages, so "
        "nothing from the wiki was injected for this message. Do not take that as no rule applying: "
        "before anything risky, read the matching pages under the hub's `operator/` and `craft/` yourself."
    ), "위키: 시간 안에 페이지를 못 읽어 이번에는 아무것도 넣지 못했다"),
    "session_state.py": (22.0, "SessionStart", (
        "<!-- wiki:deadline -->\nThe wiki hook ran out of time before it could read where this "
        "repository stands. Before deciding what to change, read `.wiki/plan-active.md` or the open "
        "plans under `docs/plans/`, and the recent records in `.wiki/decisions/`."
    ), "위키: 시간 안에 현재 상태를 못 읽어 넣지 못했다"),
}
# Heads the pages the hook found but could not finish: not translated, and not
# checked against what this session already holds.
PARTIAL = (
    "<!-- wiki:deadline -->\nThe wiki hook ran short of time: the repository pages below are the "
    "untranslated Korean originals and pages already seen this session were not left out.\n\n"
)

_lock = threading.Lock()
_sent = False
_armed = False
best: dict | None = None


def output(event: str, context: str, note: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context},
            "systemMessage": note}


def send(answer: dict) -> bool:
    """Write the hook's one answer. False when the deadline answered first."""

    global _sent
    with _lock:
        if _sent:
            return False
        json.dump(answer, sys.stdout, ensure_ascii=False)
        sys.stdout.flush()
        # Once per armed process. Unarmed — a test calling `main` again — it
        # writes every time.
        _sent = _armed
        return True


def _expire() -> None:
    try:
        if best is not None:
            send(best)
    finally:
        # Also after the hook's own answer: it is still recording, and a host
        # that kills it drops the answer it already has.
        # ponytail: a trajectory row cut mid-write reads back as nothing seen,
        # so the next turn resends pages whole; tolerable, it errs on cost.
        os._exit(0)


def arm(seconds: float, fallback: dict) -> None:
    global best, _armed
    best, _armed = fallback, True
    # Pinned before anything is written: a Korean `systemMessage` on a cp949
    # pipe would end the very write this exists for.
    sys.stdout.reconfigure(encoding="utf-8")
    timer = threading.Timer(seconds, _expire)
    timer.daemon = True
    timer.start()


if Path(sys.argv[0]).name in LIMITS:
    _seconds, _event, _context, _note = LIMITS[Path(sys.argv[0]).name]
    try:
        arm(_seconds, output(_event, _context, _note))
    except Exception:  # noqa: BLE001 — a deadline that fails to arm leaves the hook as it was
        pass
