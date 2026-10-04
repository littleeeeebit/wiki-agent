"""workspace — worktrees and where their session logs live.

`__all__` is the whole contract; `lint.pipeline_surface` goes red when a module
at the `tool/` root uses anything else. Two kinds of caller: the main, which
makes and clears the worktrees an agent writes in, and the tools that read what
the hosts logged about a checkout.
"""

from .sessions import (
    INJECTED, MAX_HUMAN_CHARS, SESSIONS, checkout, checkouts, codex_homes,
    folder, logs, parse,
)
from .worktrees import TASK, adopt, base_branch, create, folder_for, merged, remove, worktrees

__all__ = (
    # worktrees an agent writes in
    "create", "adopt", "base_branch", "folder_for", "merged", "worktrees", "remove", "TASK",
    # session logs
    "SESSIONS", "INJECTED", "MAX_HUMAN_CHARS", "parse", "checkout", "checkouts",
    "folder", "logs", "codex_homes",
)
