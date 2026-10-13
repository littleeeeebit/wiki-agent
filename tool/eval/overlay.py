"""`python tool/eval/overlay.py <run-dir>`

Reliability PR 9's output overlay check (`docs/plans/reliability/9-korean.md`,
v1): arm A's answers of a pairs run (`compare.py --experiment pairs`) through
the Korean display path, a paragraph at a time as the answer screen splits
them. Each paragraph goes to the translator; what it shows is judged without
a back-translation, and `overlay.json` is written beside the rows.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OVERLAY_SECONDS = 60.0


def paragraphs(text: str) -> list[str]:
    """An answer's paragraphs as the answer screen splits them (`web/src/lib/overlay.ts`):
    at blank lines, never inside a ``` or ~~~ fence."""

    out, piece, fence = [], [], ""
    for line in text.split("\n"):
        m = re.match(r"\s*(`{3,}|~{3,})", line)
        mark = m.group(1) if m else ""
        if mark and (not fence or (mark[0] == fence[0] and len(mark) >= len(fence))):
            fence = "" if fence else mark
        if not fence and not mark and not line.strip():
            if piece:
                out.append("\n".join(piece))
            piece = []
        else:
            piece.append(line)
    if piece:
        out.append("\n".join(piece))
    return out


def shape(text: str) -> tuple[tuple, tuple]:
    """The layout a rendering must keep: each table row's cell count, and
    each list item's indent and kind (bullet or number)."""

    lines = text.splitlines()
    cells = tuple(len(re.split(r"(?<!\\)\|", line.strip().strip("|"))) for line in lines
                  if line.lstrip().startswith("|"))
    items = tuple((len(m.group(1)), m.group(2)[0].isdigit()) for line in lines
                  if (m := re.match(r"(\s*)([-*+]|\d+[.)])\s", line)))
    return cells, items


def overlaid(text: str, rendered: list[tuple[str, str]]) -> dict:
    """One answer's overlay judged: each paragraph shown in Korean keeps its
    protected spans (citations, paths, code, names), its numbers and
    identifiers, and its table and list layout; a refused paragraph stays the
    original with its status. No back-translation is read."""

    import translate

    keep = translate.glossary()[0]
    shown = ("translated", "cached", "skipped")
    split = paragraphs(text)
    # A paragraph the rendering never returned is shown as nothing: it is broken, not skipped.
    rendered = list(rendered) + [(None, "missing")] * (len(split) - len(rendered))
    out = []
    for original, (made, status) in zip(split, rendered):
        if status not in shown:
            out.append({"status": status, "kept_original": made == original, "problems": []})
            continue
        problems = []
        if translate.by_kind(made, keep) != translate.by_kind(original, keep):
            problems.append("protected spans changed")
        if translate.facts(original, keep) - translate.facts(made, keep) - set(translate.spelled(made, original)):
            problems.append("a number or identifier lost")
        if shape(made) != shape(original):
            problems.append("table or list layout changed")
        out.append({"status": status, "problems": problems})
    return {"paragraphs": len(out), "refused": sum(p["status"] not in shown for p in out),
            "broken": [n for n, p in enumerate(out) if p["problems"] or (p["status"] not in shown
                                                                         and not p["kept_original"])],
            "detail": out}


def overlay(folder: Path) -> dict:
    """Arm A's first answers of a pairs run, through the display path."""

    import translate

    from eval import compare

    got = {}
    for row in compare.rows(folder, "pairs"):
        text = (row.get("answer") or {}).get("text")
        if row["arm"] == "A" and row["rep"] == 0 and text:
            parts = paragraphs(text)
            got[row["key"]] = overlaid(text, translate.checked(parts, translate.EN_KO,
                                                               time.monotonic() + OVERLAY_SECONDS))
    out = {"answers": len(got), "broken": sorted(k for k, v in got.items() if v["broken"]),
           "refused_paragraphs": sum(v["refused"] for v in got.values()), "rows": got}
    (folder / "overlay.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/overlay.py", description="Reliability PR 9's output overlay")
    parser.add_argument("run_dir", type=Path, help="a pairs run directory")
    got = overlay(parser.parse_args(argv).run_dir)
    print(json.dumps({k: got[k] for k in ("answers", "broken", "refused_paragraphs")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
