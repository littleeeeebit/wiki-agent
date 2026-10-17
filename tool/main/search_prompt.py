"""Shell-safe search instructions shared by conversation focuses."""

from pathlib import Path
import sys

from . import channels


# How a focus finds evidence before it reads, on both hosts.
#
# No first pass on a cheaper model. In the public copy a Haiku `scout`
# subagent was built and measured on 2026-09-25 over ten real questions: where
# it was called, input tokens rose 2.4x — it loads its own context, and the
# main model re-reads what it cites anyway. Searching itself, the main model's
# median input fell 20% and correct citations rose from 29.5 to 39.5.
SEARCH_NOTE = """## Search command
In Bash: {bash} '<query>' [--k 8]
In PowerShell: & {pwsh} '<query>' [--k 8]
Keep the query in single quotes, so `$`, backticks and `$(...)` stay text; a
quote inside it is written '\\'' in Bash and '' in PowerShell.
It returns matching sections with `path:line` and the pages linked to each.
For the configured Jev retrieval controller, run the same command with
`tool/jev_search.py` in place of `tool/search` and add
--state '<brief current state>'. Its JSON dossier includes evidence, routing,
the requirements it covered and its status. Only `ready` means covered; a
partial, unavailable, cancelled or exhausted status requires further
verification. Jev judgments never override hook rules or authorize actions.
The hub's pages are English and many repository documents are Korean, so
search with terms in both languages."""


def search_note(repo: Path) -> str:
    """The note with this server's Python and this repository in the command.

    Forward slashes: Claude's `Bash` is Git Bash on Windows. Both shells'
    forms are given, not the one for the focus's host: a quoted first word is
    a string, not a command, in Codex's PowerShell and needs `&`, which Bash
    reads as "run in the background" — and `reconfigure` can move a focus
    between Claude and Codex while its system prompt stays (review round 1).

    Every path in single quotes, which neither shell expands: a repository
    at `C:/Repos/R&D` or `C:/Team $Ops` reaches Python as it is (round 2).
    A quote inside a path is escaped each shell's own way. The query's
    placeholder is single-quoted too, and the note says how: a model that
    copies `"<query>"` hands `$HOME` or `$(...)` to the shell (round 3).
    """

    paths = [Path(sys.executable), channels.WIKI / "tool/search", repo.resolve()]

    def command(escape) -> str:
        python, script, project = (f"'{escape(p.as_posix())}'" for p in paths)
        return f"{python} {script} --project {project}"

    return SEARCH_NOTE.format(bash=command(lambda s: s.replace("'", "'\\''")),
                              pwsh=command(lambda s: s.replace("'", "''")))
