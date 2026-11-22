"""oneshot step 12: what a measurement keeps, and how it is read.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. A measurement's folder keeps the clips it
accepts, its report and the backend's log, masked (`scrubbed`); the plan and the report are read strictly
(`strict`), so a record built from them always serializes again.
"""

from __future__ import annotations

import decimal
import json
import math
import shutil
from pathlib import Path

from common import masked

CLIPS = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
         ".flac": "audio/flac", ".mp4": "video/mp4", ".webm": "video/webm"}
MAX_CLIPS, MAX_CLIP = 12, 64 * 1024 * 1024
KEPT = ("report.json", "serve.log")   # what a measurement's folder keeps besides its clips, masked


def strict(text: str):
    """JSON whose numbers are numbers: `NaN`, `Infinity` or a float too large for one is `ValueError`, so a plan,
    a report and the screen built from them always serialize again."""

    def number(s: str) -> float:
        if not math.isfinite(v := float(s)):
            raise ValueError("유한한 수가 아닌 값이 있다")   # never the token itself: it may be a secret
        return v
    value = json.loads(text, parse_constant=number, parse_float=number)
    json.dumps(value, ensure_ascii=False).encode("utf-8")   # a lone surrogate escape is `UnicodeEncodeError`
    return value


def same_number(text: str, v: int | float) -> bool:
    """Whether the secret `text` is the number `v`: exactly (an integer past a float's precision included), or,
    for a float `v` the reader already rounded, as that same float. An integer `v` is never rounded."""

    try:
        n = decimal.Decimal(text.strip())
        if not n.is_finite():
            return False
        if n == decimal.Decimal(v):
            return True
        return isinstance(v, float) and math.isfinite(f := float(n)) and f == v
    except (decimal.InvalidOperation, OverflowError, ValueError):
        return False


def label_of(v, hide: dict[str, str]) -> str | None:
    """The mask of a number that is, whole, a secret's value, in any spelling (`1234`, `1234.0`), or `None`."""

    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return next((label for value, label in hide.items() if same_number(value, v)), None)


def cleaned(value, hide: dict[str, str]):
    """`value` decoded from JSON with every string masked and every number that is a secret replaced by its mask.
    A key that shows a secret is masked too, numbered where two would collide."""

    if isinstance(value, str):
        return masked(value, hide)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = masked(k, hide)
            while key in out:
                key += "·"
            out[key] = cleaned(v, hide)
        return out
    if isinstance(value, list):
        return [cleaned(v, hide) for v in value]
    return (label_of(value, hide) or value) if isinstance(value, (int, float)) else value


def clips_of(out: Path, hide: dict[str, str]) -> list[str]:
    folder = out / "clips"
    if not folder.is_dir():
        return []
    found = sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in CLIPS
                   and p.stat().st_size <= MAX_CLIP and masked(p.name, hide) == p.name)
    return found[:MAX_CLIPS]


def scrubbed(out: Path, hide: dict[str, str]) -> None:
    """A measurement's folder as it is kept: the report and the backend's log masked, the clips `clips_of`
    accepts, nothing else. Whatever else the script wrote may hold a value it was given, so it goes. The report
    is decoded, each field masked, and written once; the log is masked as text, in each spelling JSON escaping
    gives too."""

    hide = {**hide, **{form: label for value, label in hide.items()
                       for form in (json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1])}}
    clips = {out / "clips" / name for name in clips_of(out, hide)}
    for p in sorted(out.rglob("*"), reverse=True):   # files before the folders that hold them
        if p in clips or (p == out / "clips" and p.is_dir() and clips):   # a file at that path, or an empty one, goes
            continue
        if p.parent == out and p.name in KEPT and p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            if p.suffix == ".json":   # no escape spells a value differently, and no mask breaks the syntax
                try:
                    p.write_text(json.dumps(cleaned(strict(text), hide), ensure_ascii=False, indent=1), encoding="utf-8")
                except (ValueError, RecursionError):
                    p.unlink()   # `report_of` already said it is not JSON; nothing reads it again
            elif (clean := masked(text, hide)) != text:
                p.write_text(clean, encoding="utf-8")
            continue
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
