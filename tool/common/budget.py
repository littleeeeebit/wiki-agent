"""One allowance that every step of a run spends from.

A run — one question's retrieval decisions, one connectivity probe — carries a
single `Budget` through every step, so no step's own limit multiplies the
total: a deadline, a count of Jev requests, a token ceiling, and a count of
candidate chunks. Exhausting it, and being cancelled, are outcomes of their
own. Neither is a timeout of one request, and neither is a negative judgment.

The ceilings are design defaults from `docs/plans/jev/1-runtime-baseline.md`,
not measured performance. Changing one changes `POLICY`.
"""

from __future__ import annotations

import threading
import time

POLICY = "jev-budget-1"

# The longest one Jev request may take. A step starts only if a request after
# it could still finish inside the deadline.
CALL_SECONDS = 6.0

# Added retrieval and decision work per question.
QUESTION = {"seconds": 15.0, "calls": 6, "candidates": 40}
# One server-owned choice (stage 8): the action Choice, and the retrieval it may
# pick spending what is left — never a second allowance.
ACTION = {"seconds": 25.0, "calls": 6, "candidates": 40}
# A connectivity smoke check.
PROBE = {"seconds": 15.0, "calls": 2, "candidates": 0}


class Exhausted(Exception):
    """The run's allowance is spent. `str()` names which: deadline, calls, tokens, candidates."""

    category = "budget"


class Cancelled(Exception):
    category = "cancelled"


class Budget:
    def __init__(self, seconds: float, calls: int, candidates: int, tokens: int | None = None,
                 cancel: threading.Event | None = None, call_seconds: float = CALL_SECONDS):
        self.limits = {"seconds": seconds, "calls": calls, "candidates": candidates, "tokens": tokens}
        self.used = {"calls": 0, "candidates": 0, "tokens": 0}
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.cancel = cancel or threading.Event()
        self.call_seconds = call_seconds
        # Time spent in turns with an allowance of their own (the host's fallback): not the run's.
        self.aside_ms = 0.0
        self._lock = threading.Lock()

    def left(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self, need: float = 0.0) -> None:
        """Raise unless the run is live and `need` more seconds still fit."""

        if self.cancel.is_set():
            raise Cancelled("cancelled")
        if time.monotonic() + need > self.deadline:
            raise Exhausted("deadline")

    def call(self) -> float:
        """Take one request from the allowance; the seconds it may use."""

        with self._lock:
            self.check(0.0)
            if self.used["calls"] >= self.limits["calls"]:
                raise Exhausted("calls")
            tokens = self.limits["tokens"]
            if tokens is not None and self.used["tokens"] >= tokens:
                raise Exhausted("tokens")
            self.used["calls"] += 1
        return min(self.call_seconds, self.left())

    def charge(self, usage: object) -> None:
        """Count a response's reported tokens. A missing count is not zero
        spent, but there is nothing honest to add; the trace keeps `usage`."""

        if isinstance(usage, dict):
            spent = sum(v for k, v in usage.items()
                        if k in ("input_tokens", "output_tokens") and type(v) is int and v >= 0)
            with self._lock:
                self.used["tokens"] += spent

    def take(self, n: int) -> int:
        """Up to `n` candidates from what is left; raise when none are."""

        with self._lock:
            n = min(n, self.limits["candidates"] - self.used["candidates"])
            if n <= 0:
                raise Exhausted("candidates")
            self.used["candidates"] += n
        return n

    def aside(self, seconds: float) -> None:
        """`seconds` a turn with its own allowance took: the deadline moves
        past them, and the record keeps them apart from what the run spent."""

        with self._lock:
            self.deadline += seconds
            self.aside_ms += seconds * 1000

    def record(self) -> dict:
        # `elapsed_ms` is the whole wall time; `aside_ms` the part of it the allowance does not cover.
        return {"policy": POLICY, "limits": dict(self.limits), "used": dict(self.used),
                "elapsed_ms": round((time.monotonic() - self.started) * 1000), "aside_ms": round(self.aside_ms)}
