"""workspace — worktrees and where their session logs live.

`__all__` is the whole contract; `lint.pipeline_surface` goes red when a module
at the `tool/` root uses anything else. Two kinds of caller: the main, which
makes and clears the worktrees an agent writes in, and the tools that read what
the hosts logged about a checkout.
"""

from .sessions import (
    INJECTED, MAX_HUMAN_CHARS, SESSIONS, checkout, checkouts, folder,
    logs, parse,
)
from .worktrees import create, remove, worktrees

__all__ = (
    # worktrees an agent writes in
    "create", "worktrees", "remove",
    # session logs
    "SESSIONS", "INJECTED", "MAX_HUMAN_CHARS", "parse", "checkout", "checkouts",
    "folder", "logs",
)
