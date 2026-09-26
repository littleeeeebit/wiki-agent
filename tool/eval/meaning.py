"""`python tool/eval/meaning.py [<manifest>] --live [--out <file>]`

Stage 2's meaning check for English normalization: each Korean case goes
through `translate.english`, and the English is held against the labels a
person wrote (`eval/jev/meaning.json`). A case passes when English came about
(`translated`) and every label holds. The translating model never grades its
own output.

`--live` is required: the cases are sent to Gemini on `translate`'s key and
monthly limit. The result — each case's English, status and labels — goes to
`raw/eval/jev/`, with the reference beside it for a person to read.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import translate  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "eval" / "jev" / "meaning.json"
RAW = ROOT / "raw" / "eval" / "jev"
SECONDS = 120.0


def check(case: dict, outcome: dict) -> list[str]:
    """What is wrong with one case's English. Empty means it passes."""

    if outcome["status"] != "translated":
        return [f"status {outcome['status']} ({outcome.get('reason')})"]
    text = outcome["text"].lower()
    wrong = [f"none of {group}" for group in case["require"] if not any(p.lower() in text for p in group)]
    return wrong + [f"has {phrase!r}" for phrase in case["forbid"] if phrase.lower() in text]


def run(manifest: Path) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != "jev-meaning-manifest/1":
        raise ValueError(f"{manifest}: not a jev-meaning-manifest/1")
    cases = data["cases"]
    outcomes = translate.english([c["source"] for c in cases], time.monotonic() + SECONDS)
    rows = [{"id": c["id"], "kind": c["kind"], "source": c["source"], "reference": c["reference"],
             "english": o["text"], "status": o["status"], "reason": o["reason"], "cached": o["cached"],
             "problems": check(c, o)} for c, o in zip(cases, outcomes)]
    return {"schema": "jev-meaning-result/1", "manifest": {"id": data["id"], "version": data["version"]},
            "run": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "model": translate.MODEL, "version": next((o["version"] for o in outcomes if o["version"]), None),
            "cases": rows, "summary": {"cases": len(rows), "passed": sum(not r["problems"] for r in rows)}}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/meaning.py", description="Meaning preservation")
    parser.add_argument("manifest", type=Path, nargs="?", default=MANIFEST)
    parser.add_argument("--live", action="store_true", help="send the cases to the translator")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("the cases are sent to Gemini; pass --live to send them")
    result = run(args.manifest)
    out = args.out or RAW / f"meaning-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for row in result["cases"]:
        print(f"{row['id']}: {'pass' if not row['problems'] else '; '.join(row['problems'])}")
    print(f"{out}: {result['summary']['passed']}/{result['summary']['cases']} passed")
    return 0 if result["summary"]["passed"] == result["summary"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
