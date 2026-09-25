"""SessionStart, Stop and SessionEnd hook — tell the search daemon what this cell is doing.

The daemon pings a Claude session idle for 55 minutes, at most `keep_alive`
times per person's utterance, so its prompt cache is still there when the
person comes back. It only knows what the cell's hooks tell it: this script
for the three events, `inject.py` for each utterance.

Only where all three hold — anywhere else nothing is sent:

- `--host claude`. Codex's cache lifetime and cost were not measured
- `ORCA_TERMINAL_HANDLE` in the environment: the cell the daemon types into
- `keep_alive = <n>` at the top of the repository's `.wiki/adapter.toml`,
  `n` at least 1. Measured per repository in the public copy, it only pays
  where people return after an hour, so each one opts in

The event comes from the payload's `hook_event_name`, so the three wirings
are one command.
"""

from __future__ import annotations

# First import of the entry point: it keeps the stack from before whatever
# time limit kills this.
import hook_diagnostics  # noqa: F401
import argparse
import json
import os
import sys
import threading
import tomllib
from pathlib import Path

import search
from workspace import INJECTED, checkout

PING = search.PING


def limit(project: str | Path | None) -> int:
    """The repository's `keep_alive`, or 0 — off — for anything else."""

    if not project:
        return 0
    try:
        data = tomllib.loads((Path(project) / ".wiki/adapter.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    value = data.get("keep_alive")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def target(host: str | None, project: str | Path | None) -> tuple[str, int] | None:
    """`(this cell's handle, the cap)` when keep-alive is on here, else `None`."""

    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if host != "claude" or not handle:
        return None
    cap = limit(project)
    return (handle, cap) if cap else None


# How long a notice is retried against a daemon that is there but slow. A
# lost `/busy` leaves the timer armed while the turn runs.
RETRY = 2.0


def on_prompt(prompt: str, host: str | None, project: str | None,
              session: str) -> tuple[bool, threading.Thread | None]:
    """`inject.py`'s half: `(this turn is the daemon's ping, the /busy in flight)`.

    The ping turn carries nothing and is recorded nowhere; it only tells the
    daemon the ping arrived. Anything else clears the timer, and resets the
    count only when a person typed it — the harness's own utterances
    (`workspace.INJECTED`) are not somebody coming back.

    `/busy` goes on a thread, retried for `RETRY` seconds, while the hook
    translates; `inject.py` joins it before it exits. Not a daemon thread:
    when the rest of the hook fails open on an exception and never reaches
    the join, the interpreter still waits for it (review round 1). On every utterance it
    must not start a wait for a daemon that was not running — no timer can
    be armed in one (`spawn_wait=0`).
    """

    cell = target(host, project)
    if cell is None or not session:
        return False, None
    body = {"session": session, "handle": cell[0]}
    if prompt.strip() == PING:
        # The words alone do not make it the ping — a person can type them.
        # The daemon knows whether it sent one; if not, it has taken this as
        # the person's `busy`, and the turn goes on as theirs (review round 2).
        answer = search.notify("/ping-turn", body, spawn_wait=0, retry=RETRY)
        if answer and answer.get("ping"):
            return True, None
        return False, None
    # The ping's own mark does not stop a reset: the real ping never gets
    # here, so a prompt carrying those words is a person quoting them.
    reset = not any(mark in prompt for mark in INJECTED if mark not in PING)
    busy = threading.Thread(target=search.notify, daemon=False, args=(
        "/busy", body | {"reset": reset}), kwargs={"spawn_wait": 0, "retry": RETRY})
    busy.start()
    return False, busy


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="keep-alive 데몬에 이 셀의 상태를 알린다")
    parser.add_argument("--project", default=None, help="대상 저장소. `.wiki/adapter.toml` 을 읽는다")
    parser.add_argument("--checkout", default=None, help="이 셀의 체크아웃. 없으면 --project")
    parser.add_argument("--host", default=None)
    args = parser.parse_args()

    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    session = str(payload.get("session_id") or "")
    event = payload.get("hook_event_name")
    cell = target(args.host, args.project)
    if cell is None or not session:
        return 0
    body = {"session": session, "handle": cell[0]}
    if event == "SessionStart":
        search.notify("/own", body, retry=RETRY)
    elif event == "Stop":
        # The checkout, not the project: Orca names a worktree by its own
        # path, and the project is the main clone. A per-project install
        # passes no `--checkout`; the session's `cwd` answers the same way
        # `hook.py` does.
        top = args.checkout or checkout(Path(payload.get("cwd") or os.getcwd()))[0]
        checkout_ = str(Path(top or args.project).resolve())
        search.notify("/idle", body | {"checkout": checkout_, "limit": cell[1]}, retry=RETRY)
    elif event == "SessionEnd":
        search.notify("/gone", body, retry=RETRY)
    return 0


if __name__ == "__main__":
    # Whatever happens, a hook does not stop the session. `craft/hooks-fail-open`.
    try:
        _code = main()
    except Exception as _error:  # noqa: BLE001
        print(f"hook skipped: {type(_error).__name__}", file=sys.stderr)
        _code = 0
    raise SystemExit(_code)
