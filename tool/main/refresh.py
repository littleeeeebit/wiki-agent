"""refresh — a value every request reads but none waits for.

A Codex `app-server` may take minutes to answer (`agent.CodexServer`), and the
project list, the settings hub line and the model pickers waited on it on every
read. `Refresh` keeps the last answer and computes a stale one again on a
thread of its own. A read also says whether that computation runs, so a screen
holding an old answer reads again until it is done.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from agent import pin_servers, server_epoch
from . import runtime


class Refresh:
    """One value, computed off the request path by `compute`.

    A value older than `ttl` is computed again; a failure stands `retry`
    seconds before the next try. `forget` drops the value, and a computation
    begun before it is not kept. `done` runs after every computation."""

    def __init__(self, name: str, compute: Callable[[], object], ttl: float, retry: float,
                 done: Callable[[], None] = lambda: None) -> None:
        self.name, self._compute, self.ttl, self.retry, self._done = name, compute, ttl, retry, done
        self._lock = threading.Lock()
        self._value: tuple[float, object] | None = None   # when, and what the last success gave
        self._failed: tuple[float, str] | None = None     # when, and why the last try failed
        self._revision = 0       # moved by `forget`
        self._running = False

    def read(self) -> tuple[object, str, bool]:
        """`(value, error, refreshing)`: the last value (`None` before the first), the last failure's reason
        while no value is held, and whether a computation runs now. One starts here when the value is missing
        or stale, unless a recent failure stands, one runs already, or the server is going down."""

        with self._lock:
            now = time.monotonic()
            stale = self._value is None or now - self._value[0] >= self.ttl
            waiting = self._failed is not None and now - self._failed[0] < self.retry
            if stale and not waiting and not self._running and not runtime.stopping.is_set():
                self._running = True
                try:
                    threading.Thread(target=self._run, args=(self._revision, server_epoch()), daemon=True,
                                     name=self.name).start()
                except BaseException:   # a thread that never started must not hold the next read off for good
                    self._running = False
                    raise
            value = None if self._value is None else self._value[1]
            error = "" if self._value is not None or self._failed is None else self._failed[1]
            return value, error, self._running

    def forget(self) -> None:
        """What the value was computed from changed: the next read computes it again."""

        with self._lock:
            self._revision += 1
            self._value = self._failed = None

    def _run(self, revision: int, epoch: int) -> None:
        pin_servers(epoch)   # outlives no lifespan: a shutdown meanwhile leaves it no Codex to start
        value, error = None, None
        try:
            value = self._compute()
        except Exception as exc:  # noqa: BLE001 — the reason is the answer until the next try
            error = str(exc) or type(exc).__name__
        finally:
            with self._lock:
                self._running = False
                if revision == self._revision:   # a `forget` meanwhile: this was computed from what changed
                    if error is not None:
                        self._failed = (time.monotonic(), error)
                    elif value is not None:
                        self._value, self._failed = (time.monotonic(), value), None
        self._done()
