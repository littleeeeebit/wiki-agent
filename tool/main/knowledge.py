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


def english(texts: list[str], seconds: float, owners: list[tuple[str, ...]] | None = None,
            project: str | Path | None = None) -> list[dict]:
    """English normalization, bounded by its seconds.

    A text with owners — the private sources it came from — never reaches the
    translator's cache. Its English is read from `project`'s evidence store
    and kept there, beside those sources, so it goes when they do; the
    translator checks what is read as it checks a cache hit (version,
    retirement, the protected spans).
    """

    deadline = time.monotonic() + seconds
    owners = owners or [()] * len(texts)
    out: list[dict] = [{}] * len(texts)
    for group, private in (([i for i, o in enumerate(owners) if not o], False),
                           ([i for i, o in enumerate(owners) if o], True)):
        if not group:
            continue
        sources = {s for i in group for s in owners[i]}
        store = evidence_store(project) if private and project else None
        try:
            held = {}
            for source in sources if store else ():
                held |= store.english(source, [texts[i] for i in group if source in owners[i]])
            made = translate.english([texts[i] for i in group], deadline, held=held if private else None)
            for i, outcome in zip(group, made):
                out[i] = outcome
            for source in sources if store else ():
                store.keep_english(source, [(texts[i], out[i]) for i in group
                                            if source in owners[i] and out[i]["status"] == "translated"])
        finally:
            if store:
                store.close()
    return out


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
    dossier = controlled(query, project, brief, k, evaluate=evaluate, budget=Budget(**QUESTION, cancel=cancel),
                         normalize=functools.partial(english, project=project) if live else None, omitted=omitted)
    return {**dossier, "jev": cfg.status(),
            "state": {"characters": len(state), "summarized": omitted is not None, "omitted": omitted}}


def ingest(project: str | Path | None, seconds: float = 600.0, estimate: bool = False) -> dict:
    """Index `project` and normalize its evidence into English ahead of the
    questions, so a turn finds its passages' English cached — a private
    memory's in the evidence store beside it, the rest in the translator's cache.

    `estimate` counts what would be sent and sends nothing. English needs no
    request; only Korean text, chunk and heading, is translated, `BATCH` per
    request, until `seconds` run out.
    """

    index = local_index(project)
    try:
        chunks = list(index.chunks)
    finally:
        index.close()
    names = translate.glossary()[0]
    # The private sources each text came from; `""` for a shared file, whose
    # text is then the shared file's, cached as any.
    owners: dict[str, set[str]] = {}
    for c in chunks:
        for text in (c["text"], c["heading"]):
            owners.setdefault(text, set()).add(c["source_id"] if c["visibility"] == "private" else "")
    texts = list(owners)
    shared = [t for t in texts if "" in owners[t] and language(t, names) == "ko"]
    private = [t for t in texts if "" not in owners[t] and language(t, names) == "ko"]
    # Every chunk's citation, read back from its file: what it quotes must be what is there.
    unresolved = [f"{c['locator']['path']}:{c['line']}" for c in chunks if resolve(c, c["path"]) != c["text"]]
    counts = {"chunks": len(chunks), "texts": len(texts), "korean": len(shared) + len(private),
              "requests_at_most": -(-len(shared) // BATCH) - (-len(private) // BATCH),
              "unresolved_citations": unresolved}
    if estimate:
        return counts
    end = time.monotonic() + seconds
    statuses: Counter = Counter()
    for group in (shared, private):
        for start in range(0, len(group), BATCH):
            batch = group[start:start + BATCH]
            seconds = max(0.0, min(end - time.monotonic(), BATCH_SECONDS))
            mine = [() if "" in owners[t] else tuple(owners[t]) for t in batch]
            statuses.update(o["status"] for o in english(batch, seconds, mine, project))
    return {**counts, "statuses": dict(statuses)}
