"""agent — run a CLI agent in a checkout and stream its events.

`__all__` is the whole contract; `lint.pipeline_surface` goes red when a module
at the `tool/` root uses anything else. `ChatSession.say` streams `Event`s —
`delta`, `tool`, `approval`, `done`, `error` — each stamped with the session's
own `session_id` and `parent_id`; `ChatSession.answer` answers an approval.
"""

from .chat_local import ROOT, SETTINGS, CodexServer, cli_command, codex_usage, settings
from .chat_session import ChatSession, Event, explain, oneshot

__all__ = (
    # a session and its events
    "ChatSession", "Event", "explain", "oneshot",
    # finding the CLIs, and the chat's local settings
    "CodexServer", "cli_command", "codex_usage", "settings", "ROOT", "SETTINGS",
)
