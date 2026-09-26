"""Jev retrieval, composed: `search`'s controller with `decision`'s transport.

The one place the two meet, so the app's query path and the root CLIs
(`tool/jev_search.py`) run the same flow on the same settings. The settings
are read once per call — a snapshot for that run — and never held, so a key
changed in `.env` reaches a server that is already running on its next turn.
"""

from __future__ import annotations

import functools
import threading
from pathlib import Path

import decision
from common.budget import QUESTION, Budget
from search import prepare as controlled


def disabled(*_args) -> dict:
    raise decision.JevError("disabled")


def prepare(query: str, project: str | Path | None, state: str = "", k: int = 8,
            cfg: decision.Config | None = None, cancel: threading.Event | None = None) -> dict:
    """The dossier for one question, with the settings it ran under (never the key).

    Mode off sends nothing: the dossier is baseline retrieval with the reason
    `disabled`, whoever asked.
    """

    cfg = cfg or decision.config()
    evaluate = functools.partial(decision.evaluate, cfg) if cfg.mode != "off" else disabled
    dossier = controlled(query, project, state, k, evaluate=evaluate, budget=Budget(**QUESTION, cancel=cancel))
    return {**dossier, "jev": cfg.status()}
