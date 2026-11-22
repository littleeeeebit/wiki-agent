"""Answer the host before its time limit does.

A host that times a hook out kills it and drops whatever it had printed: the
turn gets no injection and nobody is told. The diagnostics of 2026-11-19 show
where those runs stalled — imports, `stat`, reading pages — all before there
was anything to print. So a timer answers first: a few seconds before the
host's limit, the best output assembled so far goes out once and the process
exits 0. The hook keeps `best` current as it gets further.

Armed as early as the process allows: by `hook.py` before it resolves the
project, or, run directly, by the hook's first import — ahead of
`hook_diagnostics`, whose log scan can stall too.
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
# The PreToolUse wrappers: answered a few seconds under their `timeout` (10, `apply.py`) with the deny of a
# session carrying a oneshot's secrets (`secrets_held`), and with nothing in any other, where the host decides.
PRETOOL = {"claude_pretool.py": 7.0, "codex_pretool.py": 7.0}


def pretool() -> dict | None:
    """A PreToolUse wrapper's fallback: the credential-session deny, or `None`."""
    return __import__("secrets_held").held_closed("훅이 시간 안에 검사를 끝내지 못했다")


# Follows the pages the hook found but could not finish: not translated, and
# not checked against what this session already holds. After them, not before,
# so a body over the ceiling still opens with its rule index in the preview.
PARTIAL = (
    "\n\n<!-- wiki:deadline -->\nThe wiki hook ran short of time: the repository pages above are the "
    "untranslated Korean originals and pages already seen this session were not left out.\n"
)

# Held while an answer is written, and by whoever forwards stdout, so a reader
# of the stream never sees half an answer.
lock = threading.Lock()
_sent = False
_armed = False
best: dict | None = None


def output(event: str, context: str, note: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context},
            "systemMessage": note}


def send(answer: dict) -> bool:
    """Write the hook's one answer. False when the deadline answered first."""

    global _sent
    with lock:
        if _sent:
            return False
        json.dump(answer, sys.stdout, ensure_ascii=False)
        sys.stdout.flush()
        # Once per armed process. Unarmed — a test calling `main` again — it
        # writes every time.
        _sent = _armed
        return True


def close() -> None:
    """No answer goes out after this — the stream was forwarded. Under `lock`."""

    global _sent
    _sent = _armed


# Run after the answer and before the hard exit. The dispatcher's probe mode
# holds stdout in a buffer and forwards it from a `finally` that `os._exit`
# skips, so it hangs its forwarding here.
before_exit = None


def _expire() -> None:
    with lock:
        answered, fallback = _sent, best
    if not answered and fallback is None:
        # Nothing chosen yet — the dispatcher has not seen an attached project.
        # The host's own limit decides, as before this existed: exiting now
        # would cut a hook that might still answer inside it.
        return
    try:
        if not answered:
            send(fallback)
        if before_exit is not None:
            before_exit()
    finally:
        # Also after the hook's own answer: it is still recording, and a host
        # that kills it drops the answer it already has. The files a cut can
        # touch are written to survive it: `.wiki/.gitignore` is replaced
        # whole, and a torn trajectory row is closed and skipped (`trajectory`).
        os._exit(0)


def stalled(script: str) -> dict:
    """What `script` sends when it got nowhere."""

    _seconds, event, context, note = LIMITS[script]
    return output(event, context, note)


def arm(seconds: float, fallback: dict | None) -> None:
    """Start the timer. With no `fallback` by expiry, it does nothing."""

    global best, _armed
    best, _armed = fallback, True
    # Pinned before anything is written: a Korean `systemMessage` on a cp949
    # pipe would end the very write this exists for.
    sys.stdout.reconfigure(encoding="utf-8")
    timer = threading.Timer(seconds, _expire)
    timer.daemon = True
    timer.start()


# Run directly by the host. Through `hook.py` the dispatcher arms instead, and
# earlier: resolving the project counts against the host's limit too.
if Path(sys.argv[0]).name in LIMITS:
    try:
        arm(LIMITS[Path(sys.argv[0]).name][0], stalled(Path(sys.argv[0]).name))
    except Exception:  # noqa: BLE001 — a deadline that fails to arm leaves the hook as it was
        pass
elif Path(sys.argv[0]).name in PRETOOL:   # a per-project install runs the wrapper itself
    try:
        arm(PRETOOL[Path(sys.argv[0]).name], pretool())
    except Exception:  # noqa: BLE001
        pass
