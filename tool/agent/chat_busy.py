"""A turn refused because the provider is full is sent again, not failed.

rfg_rag #70's review loop stopped on "Selected model is at capacity. Please
try a different model." after one immediate retry. Every model turn goes
through `ChatSession.say`, so the wait lives there once: review, repair,
planning, refactoring and the chat focuses alike. The model is never
switched — a review names its reviewer.
"""

from __future__ import annotations

import itertools
import re
import time

from .chat_events import Event

# A provider that is full or throttling, in the words Codex and Claude use.
BUSY = re.compile(r"at capacity|overloaded|rate.?limit|too many requests|temporarily unavailable"
                  r"|try again later|\b(?:429|503|529)\b", re.I)
WAITS = (30, 60, 120, 240, 480, 600)   # seconds; the last repeats


def refused(event: Event) -> bool:
    """Codex says it with an `error`; Claude with a `done` whose result is an error."""
    failed = event.kind == "error" or (event.kind == "done" and event.meta.get("error"))
    return bool(failed) and bool(BUSY.search(event.text))


def patiently(once, over, mark):
    """The events of `once()` — one turn — sent again while the provider
    refuses it as full before any work, the longest wait repeating, until it
    is taken or `over()` — a stop, the session's end, the server's — says to
    give up. Work already done, or any other error, passes through as it
    came. `mark` stamps each event with the session."""

    for attempt in itertools.count():
        turn, worked, busy = once(), False, None
        try:
            for event in turn:
                mark(event)
                if not worked and refused(event):
                    busy = event
                    break
                worked = worked or event.kind in ("delta", "tool", "approval")
                yield event
        finally:
            turn.close()
        if busy is None:
            return
        delay = WAITS[min(attempt, len(WAITS) - 1)]
        # A `tool` step, not `context`: `context` on record means the CLI's
        # conversation could not be resumed, and this one still can.
        yield mark(Event("tool", f"모델 용량 초과 — {delay}초 뒤 같은 요청을 다시 보낸다: {busy.text}"[:300]))
        ends = time.monotonic() + delay
        # ponytail: polls a quarter second; one Event per stop if that ever costs.
        while not over() and time.monotonic() < ends:
            time.sleep(0.25)
        if over():
            yield mark(Event("error", "멈췄다."))
            return
