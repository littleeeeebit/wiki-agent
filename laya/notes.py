"""The error notebook: every discarded or wrongly answered question, kept and read back.

RRSI keeps one history record per attempt and shows the proposer the whole history, so a
falsified hypothesis is not proposed again. Here the attempts are data: what labelling
dropped, what generation got wrong, and what the trained model missed. The notebook is a
separate wiki under `laya/notes/` (local only, ignored by Git):

- `<kind>/ledger.jsonl` — one line per error (`record`).
- `<kind>/<category>--<slice>.md` — one page per cluster of at least `PAGE_AT` errors, its
  front matter kept by code, its diagnosis written by a model (`write_pages`).
- `index.md` — every page, with its status.

A page's status moves `open` -> `targeted` (a round aimed at it) -> `resolved` (a kept round
brought its errors under the loop's floor), `falsified` (a round aimed at it was not kept),
or `ambiguous` (its labellers agree on under half of it). The generator reads `brief(kind)`
before every round: open pages first, never a falsified or ambiguous slice again.
"""

import json
import re
import time
from collections import defaultdict
from pathlib import Path

NOTES = Path(__file__).resolve().parent / "notes"
PROMPT = Path(__file__).resolve().parent / "prompts" / "note.md"
PAGE_AT = 5
SAMPLES = 20

CATEGORIES = {
    "disagree": "the two labellers gave different answers",
    "unsure": "the same answer, but a labeller's confidence was under 0.7",
    "invalid": "an answer the question does not offer, a missing answer, or a malformed one",
    "teacher_miss": "the labellers agreed on an answer other than the one the generator intended",
    "off_slice": "a generated state lacks the feature its slice was aimed at",
    "leak": "a generated state duplicates a gate or validation state",
    "student_wrong": "the trained model answered a validation question wrongly",
}
STATUSES = ("open", "targeted", "resolved", "falsified", "ambiguous")


def slug(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "base"


def record(kind: str, category: str, *, round: int, slice, state_hash: str, source: str, question: str,
           answers: list | None = None, intended=None, predicted=None, state=None) -> dict:
    """Append one error. `slice` is the `(feature, value)` the round aimed at, or `"base"`."""

    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    row = {"id": f"{kind}-{round}-{category}-{state_hash[:12]}-{question}", "kind": kind, "round": round,
           "category": category, "slice": slice if slice == "base" else list(slice), "state_hash": state_hash,
           "source": source, "question": question, "answers": answers or [], "intended": intended,
           "predicted": predicted, "state": state, "at": time.time()}
    path = NOTES / kind / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def ledger(kind: str) -> list[dict]:
    path = NOTES / kind / "ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cluster_name(category: str, slice) -> str:
    return f"{category}--{'base' if slice == 'base' else slug('-'.join(map(str, slice)))}"


def clusters(kind: str) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for row in ledger(kind):
        out[cluster_name(row["category"], row["slice"])].append(row)
    return dict(out)


def front(path: Path) -> dict:
    """A page's front matter (`key: value` lines between `---`)."""

    if not path.exists():
        return {}
    m = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line) if m else {}


def set_status(kind: str, name: str, status: str, round: int, slice="base") -> None:
    """Move a page's status; a page not written yet is written bare first, for `slice`."""

    if status not in STATUSES:
        raise ValueError(status)
    path = NOTES / kind / f"{name}.md"
    if not path.exists():
        write_page(kind, name, clusters(kind).get(name, []), {}, slice)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^status: .*$", f"status: {status}", text, count=1, flags=re.M)
    text = re.sub(r"^rounds: (.*)$", lambda m: f"rounds: {','.join(sorted(set(filter(None, m.group(1).split(',') + [str(round)]))))}",
                  text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")
    write_index()


def write_page(kind: str, name: str, rows: list[dict], diagnosis: dict, slice="base") -> Path:
    """The page for one cluster: front matter and cases by code, diagnosis by the writer."""

    old = front(NOTES / kind / f"{name}.md")
    first = rows[0] if rows else {"category": name.split("--")[0],
                                  "slice": slice if slice == "base" else list(slice)}
    cases = "\n".join(
        f"- `{r['state_hash'][:12]}` `{r['question']}` (round {r['round']}, {r['source']}): "
        + "; ".join(f"{a.get('labeller')}: {a.get('answer')} @ {a.get('confidence')}" for a in r["answers"])
        + (f"; intended {r['intended']}" if r.get("intended") is not None else "")
        + (f"; predicted {r['predicted']}" if r.get("predicted") is not None else "")
        for r in rows[:3])
    body = (f"---\nkind: {kind}\ncategory: {first['category']}\nslice: {json.dumps(first['slice'])}\n"
            f"count: {len(rows)}\nrounds: {old.get('rounds', '')}\nstatus: {old.get('status', 'open')}\n---\n\n"
            f"# {name}\n\n{CATEGORIES.get(first['category'], '')}.\n\n## Cases\n\n{cases or '- none yet'}\n\n"
            f"## Cause\n\n{diagnosis.get('cause', '').strip() or 'Not diagnosed yet.'}\n\n"
            f"## Next\n\n{diagnosis.get('next', '').strip() or 'Not decided yet.'}\n")
    path = NOTES / kind / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def write_pages(kind: str, writer=None, log=print) -> list[str]:
    """Write or refresh every cluster page that reached `PAGE_AT` errors and grew since it
    was last written. `writer(prompt_path, payload) -> {"cause", "next"}` is one model turn
    (`label.host_turn`-like); without one, pages get cases and no diagnosis."""

    written = []
    for name, rows in clusters(kind).items():
        path = NOTES / kind / f"{name}.md"
        if len(rows) < PAGE_AT or front(path).get("count") == str(len(rows)):
            continue
        diagnosis = {}
        if writer is not None:
            diagnosis = writer(PROMPT, {"kind": kind, "category": rows[0]["category"],
                                        "meaning": CATEGORIES[rows[0]["category"]], "slice": rows[0]["slice"],
                                        "errors": rows[-SAMPLES:],
                                        "page": path.read_text(encoding="utf-8") if path.exists() else None})
        write_page(kind, name, rows, diagnosis)
        written.append(name)
        log(f"note {kind}/{name}: {len(rows)} errors")
    write_index()
    return written


def write_index() -> None:
    lines = ["# Error notebook", "", "| Kind | Page | Category | Count | Status | Rounds |", "| --- | --- | --- | --- | --- | --- |"]
    for path in sorted(NOTES.glob("*/*.md")):
        f = front(path)
        lines.append(f"| {f.get('kind')} | [{path.stem}]({path.parent.name}/{path.name}) | {f.get('category')} | "
                     f"{f.get('count')} | {f.get('status')} | {f.get('rounds')} |")
    NOTES.mkdir(parents=True, exist_ok=True)
    (NOTES / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def brief(kind: str) -> dict:
    """What the generator reads before a round: what to aim at and what never to retry."""

    pages = {p.stem: front(p) | {"text": p.read_text(encoding="utf-8")} for p in (NOTES / kind).glob("*.md")}
    return {"open": [p["text"] for p in pages.values() if p.get("status") in ("open", "targeted")],
            "avoid": [json.loads(p["slice"]) for p in pages.values()
                      if p.get("status") in ("falsified", "ambiguous") and p.get("slice", '"base"') != '"base"']}
