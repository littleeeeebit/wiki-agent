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


def word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def joins(text: str, k: int, step: int, edge: str) -> bool:
    """Whether `text[k]`, beside a literal's `edge` character (`step` points
    away from it), carries the literal's token on. A word character, `/`,
    `\\`, `-` and `@` always do: a longer name, path, version or address.
    `.` does when a word character follows it (`x.py.bak`, `1.13.0`), not
    when it ends a sentence. `,` and `:` do only between digits (`1,300`,
    `09:30`); a clause's comma, a scheme (`mailto:`) or a label (`Port:`)
    does not."""

    c = text[k]
    far = text[k + step] if 0 <= k + step < len(text) else ""
    if word(c) or c in "/\\-@":
        return True
    if c == ".":
        return bool(far) and word(far)
    return c in ",:" and edge.isdigit() and far.isdigit()


def found(literal: str, text: str) -> str:
    """Whether `literal` is in `text` as a whole token (`present`), only
    inside a longer token a hyphen joins (`joined`), or not at all (`absent`).

    A whole token is never part of a longer number, name, path or version
    (`joins`): `30` is not in `300`, `3.05`, `1,300` or `09:30`; `PR` is not
    in `PRs`; `tool/x.py` is not in `tool/x.py.bak` or `other/tool/x.py`.
    It is in `tool/x.py.`, `(30,`, `Port:7443` and `mailto:a@b.example`. An
    edge that is not a word character (`%`, `)`, `/`) is checked by nothing.
    A hyphen cannot be read: `v1.13.0-beta` is another version, while
    `HMAC-SHA256-based` keeps the algorithm, so `joined` is neither."""

    out, start = "absent", 0
    while (i := text.find(literal, start)) >= 0:
        start, j = i + 1, i + len(literal)
        joints = ([text[i - 1]] if word(literal[0]) and i > 0 and joins(text, i - 1, -1, literal[0]) else []) + \
                 ([text[j]] if word(literal[-1]) and j < len(text) and joins(text, j, 1, literal[-1]) else [])
        if not joints:
            return "present"
        if all(c == "-" for c in joints):
            out = "joined"
    return out


def at(phrase: str, text: str) -> list[int]:
    """Where `phrase` stands in `text`, any case. A phrase matches inside
    words (`reimburs`), but an edge that is a digit is a number's edge and
    takes the whole-token rule: `31 days` is not in `131 days`, `March 1` not
    in `March 12`, `2` not in `12` (reliability PR 9's set review)."""

    low, want, out, start = text.lower(), phrase.lower(), [], 0
    while (i := low.find(want, start)) >= 0:
        start, j = i + 1, i + len(want)
        if not (want[0].isdigit() and i > 0 and joins(low, i - 1, -1, want[0])) and \
                not (want[-1].isdigit() and j < len(low) and joins(low, j, 1, want[-1])):
            out.append(i)
    return out


def present(literal: str, text: str) -> bool:
    return found(literal, text) == "present"


def literal_problem(literal: str, text: str) -> str | None:
    """What is wrong with one literal in `text`: lost, or uncertain — found
    only in a hyphenated word, which is not settled either way."""

    got = found(literal, text)
    return None if got == "present" else f"lost literal {literal!r}" if got == "absent" else \
        f"uncertain literal {literal!r} (only in a hyphenated word)"


def check(case: dict, outcome: dict) -> list[str]:
    """What is wrong with one case's English. Empty means it passes.
    `literals`, where a case has them (reliability PR 9's pairs), must appear
    exactly as written, as whole tokens: a number, a name, a path."""

    if outcome["status"] != "translated":
        return [f"status {outcome['status']} ({outcome.get('reason')})"]
    text = outcome["text"]
    wrong = [f"none of {group}" for group in case["require"] if not any(at(p, text) for p in group)]
    wrong += [f"has {phrase!r}" for phrase in case["forbid"] if at(phrase, text)]
    return wrong + [p for x in case.get("literals", []) if (p := literal_problem(x, outcome["text"]))]


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
