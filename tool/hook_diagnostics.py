"""Leave a local stack behind before something outside kills the hook.

Reads nothing from stdin, because the hook it is watching is usually blocked
on exactly that.
"""

import atexit
from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import sys
import time


def arm(hook: str, after: float) -> None:
    """Delete the record of a normal run, keep the slow one and the killed one."""
    stream = None
    armed = False
    try:
        directory = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "wiki-hook-diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        path = directory / f"wiki-{os.getpid()}-{time.time_ns()}.log"
        stream = path.open("x", encoding="utf-8", newline="\n")
        stream.write(json.dumps({
            "hook": Path(hook).name,
            "pid": os.getpid(), "parent_pid": os.getppid(),
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "stack_after_seconds": after,
        }) + "\n")
        stream.flush()
        # The C watchdog writes the stack even while blocked on stdin or the
        # GIL. A `finally` cannot: the kill that this exists to catch comes
        # from outside and never runs one.
        faulthandler.dump_traceback_later(after, file=stream)

        def finish():
            try:
                faulthandler.cancel_dump_traceback_later()
                elapsed = time.monotonic() - started
                stream.write(json.dumps({"completed_ms": round(elapsed * 1000)}) + "\n")
                stream.close()
                if elapsed < after:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

        atexit.register(finish)
        armed = True
        # Never touch a file a run is still holding. The next hook clears what
        # has gone cold instead.
        now = time.time()
        old = sorted(
            (p for p in directory.glob("*.log") if now - p.stat().st_mtime > 60),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for index, previous in enumerate(old):
            if index >= 100 or now - previous.stat().st_mtime > 7 * 86400:
                previous.unlink(missing_ok=True)
    except (OSError, RuntimeError, ValueError):
        # A diagnostic that fails must not change an injection or a block.
        # Watching the work is not worth being able to break it.
        if not armed and stream is not None:
            faulthandler.cancel_dump_traceback_later()
            stream.close()


# Armed by the entry point's own first import, and only then. `apply` and
# pytest import this module too, and arming there would write a diagnostic for
# a run nobody is waiting on.
_thresholds = {
    "codex_pretool.py": 8, "inject.py": 8, "declared_continuation.py": 8,
    "session_state.py": 13, "sync.py": 28, "keepalive.py": 8,
}
if Path(sys.argv[0]).name in _thresholds:
    arm(sys.argv[0], _thresholds[Path(sys.argv[0]).name])
