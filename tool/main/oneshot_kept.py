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
    point, and a file with no second hard link. So nothing kept or served is a file the measurement did not write.
    Only a missing entry is "no": any other failed look (`OSError`) is raised, never taken for an absent file."""

    try:
        st = os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
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
    """`folder` and everything in it gone, or why not. A link or junction at `folder` itself is removed, never
    followed; inside, `shutil.rmtree` removes links without following them, and a read-only entry it failed to
    remove is made writable and removed once more. Success is read from the disk: nothing is left at `folder`."""

    def again(func, path, exc):
        if func not in (os.unlink, os.remove, os.rmdir) or os.path.islink(path):
            raise exc   # only a removal is retried, and never through a link
        os.chmod(path, stat.S_IWRITE)
        func(path)

    try:
        if os.path.lexists(folder) and not plain(folder, stat.S_IFDIR) and os.path.isdir(folder):
            try:   # a linked folder: the link goes, its target stays
                os.unlink(folder)
            except OSError:
                os.rmdir(folder)
        elif os.path.lexists(folder):
            shutil.rmtree(folder, onexc=again) if os.path.isdir(folder) else os.unlink(folder)
    except OSError:
        pass   # what is left says it below
    if os.path.lexists(folder):
        return f"실측 원본 폴더 `{folder.name}` 를 지우지 못했다 — 그 폴더를 지우고 [실측 시작]을 누른다"
    return ""


def fresh(out: Path, raw: Path) -> str:
    """Every leftover raw folder beside `out`, and `out`, removed, then `raw` made empty for the script; or why
    not. A folder that cannot be listed says so: a leftover it hides is never taken for none."""

    try:
        olds = [Path(e.path) for e in os.scandir(out.parent) if e.name.endswith(".raw")]
    except FileNotFoundError:
        olds = []   # the first measurement of this run
    except OSError as exc:
        return f"실측 폴더를 읽지 못했다 ({type(exc).__name__}) — 그 폴더를 확인하고 [실측 시작]을 누른다"
    for old in [*olds, out]:   # an earlier run's leftover first
        if why := removed(old):
            return why
    raw.mkdir(parents=True)
    return ""


def staged(p: Path, write) -> None:
    """`p` only once `write` has written all of it beside, under a `.part` name: a failed write leaves no
    partial file there, and no `.part`."""

    part = p.with_name(p.name + ".part")
    try:
        write(part)
        os.replace(part, p)
    finally:
        part.unlink(missing_ok=True)


def written(p: Path, text: str) -> None:
    staged(p, lambda part: part.write_text(text, encoding="utf-8"))


def kept_from(raw: Path, out: Path, hide: dict[str, str]) -> str:
    """What a measurement keeps, copied from the folder the script wrote (`raw`) into a new one (`out`): the
    report, decoded strictly, each field masked, written once (one that is not UTF-8 JSON is not kept); the
    backend's log, masked as text in each spelling JSON escaping gives too; and the clips `clips_of` accepts.
    Nothing else is copied, a linked `raw` is never read through, and `raw` is removed on every exit, a failed
    copy included. Returns why the run cannot keep its result (the copy failed, or `raw` is still there), or
    empty."""

    hide = {**hide, **{form: label for value, label in hide.items()
                       for form in (json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1])}}
    why = ""
    try:
        out.mkdir(parents=True)   # new: the run removed any earlier one, so nothing placed there is kept
        if plain(raw, stat.S_IFDIR):
            if plain(report := raw / "report.json"):
                try:
                    text = json.dumps(cleaned(strict(report.read_text(encoding="utf-8")), hide), ensure_ascii=False,
                                      indent=1)
                except (ValueError, RecursionError):   # not UTF-8 JSON; a failed read (`OSError`) stops the run
                    text = None
                if text is not None:
                    written(out / "report.json", text)
            if plain(log := raw / "serve.log"):
                written(out / "serve.log", masked(log.read_text(encoding="utf-8", errors="replace"), hide))
            if names := clips_of(raw, hide):
                (out / "clips").mkdir()
                for name in names:
                    staged(out / "clips" / name, lambda part, name=name: shutil.copyfile(
                        raw / "clips" / name, part, follow_symlinks=False))
    except Exception as exc:   # a failed disk read or write, or anything unforeseen: `raw` is removed all the same
        why = f"실측 결과를 보관하지 못했다 ({type(exc).__name__}) — 디스크를 확인하고 [실측 시작]을 누른다"
    return removed(raw) or why
