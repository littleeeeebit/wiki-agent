"""oneshot step 12: what a measurement keeps, and how it is read.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. A measurement's folder keeps the clips it
accepts, its report and the backend's log, masked and copied out of the folder the script wrote (`kept_from`); the plan
and the report are read strictly (`strict`), so a record built from them always serializes again.
"""

from __future__ import annotations

import decimal
import json
import math
import os
import shutil
import stat
from pathlib import Path

from common import masked

CLIPS = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
         ".flac": "audio/flac", ".mp4": "video/mp4", ".webm": "video/webm"}
MAX_CLIPS, MAX_CLIP = 12, 64 * 1024 * 1024


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


def plain(p: Path, kind: int = stat.S_IFREG) -> bool:
    """`p` is a file (or, with `stat.S_IFDIR`, a folder) of its own: no symbolic link, junction or other reparse
    point, and a file with no second hard link. So nothing kept or served is a file the measurement did not write."""

    try:
        st = os.lstat(p)
    except OSError:
        return False
    return not getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT \
        and stat.S_IFMT(st.st_mode) == kind and (kind == stat.S_IFDIR or st.st_nlink == 1)


def clips_of(out: Path, hide: dict[str, str]) -> list[str]:
    folder = out / "clips"
    if not plain(folder, stat.S_IFDIR):
        return []
    found = sorted(p.name for p in folder.iterdir() if plain(p) and p.suffix.lower() in CLIPS
                   and p.stat().st_size <= MAX_CLIP and masked(p.name, hide) == p.name)
    return found[:MAX_CLIPS]


def removed(folder: Path) -> str:
    """`folder` and everything in it gone, a read-only file made writable first, links removed and never
    followed; or why not."""

    def again(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    try:
        shutil.rmtree(folder, onexc=again)
    except FileNotFoundError:
        pass
    except OSError as exc:
        return f"실측 원본 폴더 `{folder.name}` 를 지우지 못했다 ({type(exc).__name__}) — 그 폴더를 지우고 [실측 시작]을 누른다"
    return ""


def kept_from(raw: Path, out: Path, hide: dict[str, str]) -> str:
    """What a measurement keeps, copied from the folder the script wrote (`raw`) into a new one (`out`): the
    report, decoded strictly, each field masked, written once (one that is not UTF-8 JSON is not kept); the
    backend's log, masked as text in each spelling JSON escaping gives too; and the clips `clips_of` accepts.
    Nothing else is copied, and `raw` is removed. Returns why `raw` could not be removed, or empty."""

    hide = {**hide, **{form: label for value, label in hide.items()
                       for form in (json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1])}}
    out.mkdir(parents=True, exist_ok=True)
    try:
        if plain(report := raw / "report.json"):
            text = json.dumps(cleaned(strict(report.read_text(encoding="utf-8")), hide), ensure_ascii=False, indent=1)
            (out / "report.json").write_text(text, encoding="utf-8")
    except (OSError, ValueError, RecursionError):   # a decoding error is a `ValueError`: `report_of` said so too
        pass
    if plain(log := raw / "serve.log"):
        (out / "serve.log").write_text(masked(log.read_text(encoding="utf-8", errors="replace"), hide), encoding="utf-8")
    if names := clips_of(raw, hide):
        (out / "clips").mkdir(exist_ok=True)
        for name in names:
            shutil.copyfile(raw / "clips" / name, out / "clips" / name, follow_symlinks=False)
    return removed(raw)
