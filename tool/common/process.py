"""The creation options that keep a background child from opening a window.

A console program started by a process that has no console — the detached
search daemon — gets a console of its own, and Windows shows it: Windows
Terminal opened for every `git` the daemon ran while indexing. Options only;
the caller keeps spawning, pipes, cancellation and reaping.
"""

import subprocess
import sys


def background_options() -> dict:
    """`creationflags=CREATE_NO_WINDOW` on Windows, nothing elsewhere."""

    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
