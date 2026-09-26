"""Jev retrieval, composed: `search`'s controller with `decision`'s transport
and `translate`'s English normalization.

The one place they meet, so the app's query path and the root CLIs
(`tool/jev_search.py`, `tool/ingest.py`) run the same flow on the same
settings. The settings are read once per call — a snapshot for that run — and
never held, so a key changed in `.env` reaches a server that is already
running on its next turn.
"""

from __future__ import annotations

import functools
import json
import threading
import time
from collections import Counter
from pathlib import Path

import decision
import translate
from common.budget import QUESTION, Budget
from common.language import language
from search import evidence_store, local_index, resolve
from search import prepare as controlled
from session_state import active_page, decisions, plans
from session_state import run as git

# The controller's ceiling on `current_state` (`search.controller.MAX_STATE`).
STATE_CHARS = 4000
# Texts per translation request while ingesting, and the time one may take.
BATCH = 16
BATCH_SECONDS = 60.0


def disabled(*_args) -> dict:
    raise decision.JevError("disabled")


def english(texts: list[str], seconds: float) -> list[dict]:
    """English normalization for the controller, bounded by its seconds."""

    return translate.english(texts, time.monotonic() + seconds)


def summarized(state: str, repo: Path | None) -> tuple[str, dict | None]:
    """`state` as it is when it fits; past `STATE_CHARS`, a structured summary
    of where the work stands, and what the summary left out.

    Jev then judges against the summary, told what is missing, instead of the
    whole feature falling back because the conversation grew long.
    """

    if len(state) <= STATE_CHARS:
        return state, None
    active = active_page(repo)[0] if repo else ""
    open_plans = plans(repo) if repo else []
    revision = git(repo, "rev-parse", "HEAD") if repo else ""
    summary = {
        "summary_of": f"current_state, which exceeded {STATE_CHARS} characters",
        # In backticks: a code span is lifted out before translation, untouched.
        "revision": f"`{revision}`" if revision else None,
        "active_specification": active[:600] or (open_plans[0][0].relative_to(repo).as_posix() if open_plans else None),
        "unresolved_requirements": [f"{path.relative_to(repo).as_posix()}: {row}"
                                    for path, rows in open_plans for row in rows],
        "recent_decisions": [f"{title} — {why}".rstrip(" —") for title, why in (decisions(repo) if repo else [])],
        "recent_state": state[-1500:],
    }
    while len(json.dumps(summary, ensure_ascii=False)) > STATE_CHARS:
        longest = max(("unresolved_requirements", "recent_decisions"), key=lambda k: len(summary[k]))
        if summary[longest]:
            summary[longest].pop()
        else:
            summary["recent_state"] = summary["recent_state"][len(summary["recent_state"]) // 4 + 1:]
    omitted = {"characters": len(state) - len(summary["recent_state"]),
               "reference": "current_state before recent_state: the earlier conversation turns"}
    return json.dumps(summary, ensure_ascii=False), omitted


def cleanup(project: str | Path | None) -> int:
    """Forget the cached English of private memory the index deleted, as its
    journal lists it. Returns how many were pending; what fails stays listed."""

    if not project:
        return 0
    store = evidence_store(project)
    try:
        pending = store.journal("text")
        if pending and translate.forget([text for _id, text in pending]):
            store.cleared("text", [i for i, _text in pending])
        return len(pending)
    finally:
        store.close()


def prepare(query: str, project: str | Path | None, state: str = "", k: int = 8,
            cfg: decision.Config | None = None, cancel: threading.Event | None = None) -> dict:
    """The dossier for one question, with the settings it ran under (never the key).

    Mode off sends nothing — to Jev or to the translator: the dossier is
    baseline retrieval with the reason `disabled`, whoever asked.
    """

    cfg = cfg or decision.config()
    live = cfg.mode != "off"
    evaluate = functools.partial(decision.evaluate, cfg) if live else disabled
    root = Path(project).resolve() if project else None
    brief, omitted = summarized(state, root)
    try:
        dossier = controlled(query, project, brief, k, evaluate=evaluate, budget=Budget(**QUESTION, cancel=cancel),
                             normalize=english if live else None, omitted=omitted)
    finally:
        cleanup(project)
    return {**dossier, "jev": cfg.status(),
            "state": {"characters": len(state), "summarized": omitted is not None, "omitted": omitted}}


def ingest(project: str | Path | None, seconds: float = 600.0, estimate: bool = False) -> dict:
    """Index `project` and normalize its evidence into English ahead of the
    questions, so a turn finds its passages' English cached.

    `estimate` counts what would be sent and sends nothing. English needs no
    request; only Korean text, chunk and heading, is translated, `BATCH` per
    request, until `seconds` run out.
    """

    index = local_index(project)
    try:
        chunks = list(index.chunks)
    finally:
        index.close()
    texts = list(dict.fromkeys([c["text"] for c in chunks] + [c["heading"] for c in chunks]))
    korean = [t for t in texts if language(t) == "ko"]
    # Every chunk's citation, read back from its file: what it quotes must be what is there.
    unresolved = [f"{c['locator']['path']}:{c['line']}" for c in chunks if resolve(c, c["path"]) != c["text"]]
    counts = {"chunks": len(chunks), "texts": len(texts), "korean": len(korean),
              "requests_at_most": -(-len(korean) // BATCH), "unresolved_citations": unresolved}
    if estimate:
        return counts
    end = time.monotonic() + seconds
    statuses: Counter = Counter()
    for start in range(0, len(korean), BATCH):
        deadline = min(end, time.monotonic() + BATCH_SECONDS)
        statuses.update(o["status"] for o in translate.english(korean[start:start + BATCH], deadline))
    return {**counts, "statuses": dict(statuses), "journal_pending": cleanup(project)}
