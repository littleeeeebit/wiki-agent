"""agent — run a CLI agent in a checkout and stream its events.

`__all__` is the whole contract; `lint.pipeline_surface` goes red when a module
at the `tool/` root uses anything else. `ChatSession.say` streams `Event`s —
`delta`, `tool`, `approval`, `done`, `error` — each stamped with the session's
own `session_id` and `parent_id`; `ChatSession.answer` answers an approval.
"""

from .chat_local import ROOT, SETTINGS, CodexServer, claude_usage, cli_command, codex_usage, settings
from .chat_session import ChatSession, Event, end_all, explain, oneshot, reopen

__all__ = (
    # a session and its events
    "ChatSession", "Event", "explain", "oneshot",
    # every provider, at server shutdown and start
    "end_all", "reopen",
    # finding the CLIs, and the chat's local settings
    "CodexServer", "cli_command", "codex_usage", "claude_usage", "settings", "ROOT", "SETTINGS",
)
