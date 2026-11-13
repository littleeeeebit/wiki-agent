"""Installed code is immutable; records belong to the current Windows user."""

import json
import os
from pathlib import Path

RESOURCES = Path(__file__).resolve().parents[2]
PACKAGED = (RESOURCES / "release.json").is_file()
DATA = Path(os.environ.get("WIKI_DATA_HOME") or (
    Path(os.environ["LOCALAPPDATA"]) / "wiki-agent" if PACKAGED else RESOURCES
)).expanduser().resolve()
RAW = DATA / "raw"


def release() -> dict:
    return json.loads((RESOURCES / "release.json").read_text(encoding="utf-8")) if PACKAGED else {}
