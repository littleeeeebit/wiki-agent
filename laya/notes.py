"""The error notebook: every discarded or wrongly answered question, kept and read back.

RRSI keeps one history record per attempt and shows the proposer the whole history, so a
falsified hypothesis is not proposed again. Here the attempts are data: what labelling
dropped, what generation got wrong, and what the trained model missed. The notebook is a
separate wiki under `laya/notes/` (local only, ignored by Git):

- `<kind>/ledger.jsonl` — one line per error (`record`).
- `<kind>/diagnoses.jsonl` — every diagnosis a model wrote for a page, with the error
  count it saw (`write_pages`), journalled before the page shows it.
- `<kind>/<category>--<slice>.md` — one page per cluster of at least `PAGE_AT` errors.
- `index.md` — every page, with its status.

A page's status moves `open` -> `targeted` (a round aimed at it) -> `resolved` (a kept round
brought its errors under the loop's floor), `falsified` (a round aimed at it was not kept),
or `ambiguous` (its labellers agree on under half of it). Pages are views, rebuilt from
three journals: the ledger holds the errors, `diagnoses.jsonl` the paid diagnoses, and
`loop.run` journals every status change in `rounds.jsonl` before making it and replays
them all when it starts, so an interruption loses nothing already paid for. A diagnosis
whose turn ended before it was journalled is paid again; no local file can prevent that.
The generator reads `brief(kind)` before every round: open pages first, never a falsified
or ambiguous slice again.
"""

import json
import re
import time
from collections import defaultdict
from pathlib import Path

import store

NOTES = Path(__file__).resolve().parent / "notes"
PROMPT = Path(__file__).resolve().parent / "prompts" / "note.md"
PAGE_AT = 5
SAMPLES = 20
UNDIAGNOSED = "Not diagnosed yet."

CATEGORIES = {
    "disagree": "the two labellers gave different answers",
    "unsure": "the same answer, but a labeller's confidence was under 0.7",
    "invalid": "an answer the question does not offer, a missing answer, or a malformed one",
    "teacher_miss": "the labellers agreed on an answer other than the one the generator intended",
    "off_slice": "a generated state lacks the feature its slice was aimed at",
    "leak": "a generated state duplicates a gate or validation state",
    "student_wrong": "the trained model answered a validation question wrongly",
    "verifier_reject": "the verifier's answer differed from the labeller's, or it was unsure",
    "verifier_unreliable": "the verifier endorsed both an answer and its planted opposite in the question's batch, twice",
}
STATUSES = ("open", "targeted", "resolved", "falsified", "ambiguous")


def slug(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "base"


def note_id(kind: str, round: int, category: str, state_hash: str, question: str) -> str:
    """One error's identity: the full state hash, since it decides what is a duplicate."""

    return f"{kind}-{round}-{category}-{state_hash}-{question}"


def record(kind: str, category: str, *, round: int, slice, state_hash: str, source: str, question: str,
           answers: list | None = None, intended=None, predicted=None, state=None) -> dict:
    """Append one error. `slice` is the `(feature, value)` the round aimed at, or `"base"`."""

    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    row = {"id": note_id(kind, round, category, state_hash, question), "kind": kind, "round": round,
           "category": category, "slice": slice if slice == "base" else list(slice), "state_hash": state_hash,
           "source": source, "question": question, "answers": answers or [], "intended": intended,
           "predicted": predicted, "state": state, "at": time.time()}
    store.append(NOTES / kind / "ledger.jsonl", row)
    return row


def ledger(kind: str) -> list[dict]:
    return store.read(NOTES / kind / "ledger.jsonl")


def noted(kind: str) -> set[str]:
    """The identity of every error in the ledger, computed from its fields, so a line
    written under an earlier id format still counts as the same error."""

    return {note_id(kind, r["round"], r["category"], r["state_hash"], r["question"]) for r in ledger(kind)}


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


def joined(old: str, rounds) -> str:
    return ",".join(str(r) for r in sorted({int(r) for r in old.split(",") if r} | {int(r) for r in rounds}))


def set_status(kind: str, name: str, status: str, round: int, slice="base") -> None:
    """Move a page's status; a page not written yet is written bare first, for `slice`."""

    if status not in STATUSES:
        raise ValueError(status)
    path = NOTES / kind / f"{name}.md"
    if not path.exists():
        write_page(kind, name, clusters(kind).get(name, []), slice)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^status: .*$", f"status: {status}", text, count=1, flags=re.M)
    text = re.sub(r"^rounds: (.*)$", lambda m: f"rounds: {joined(m.group(1), [round])}", text, count=1, flags=re.M)
    store.write(path, text)
    write_index()


def diagnoses(kind: str) -> dict[str, dict]:
    """Each page's diagnosis from its journal: the latest cause and next (a later one that
    left a section out keeps the earlier one) and the error count the latest one saw."""

    out: dict[str, dict] = {}
    for d in store.read(NOTES / kind / "diagnoses.jsonl"):
        page = out.setdefault(d["page"], {"cause": "", "next": "", "count": 0})
        page.update({k: d[k] for k in ("cause", "next") if d.get(k)}, count=d["count"])
    return out


def write_page(kind: str, name: str, rows: list[dict], slice="base") -> Path:
    """The page for one cluster, a view: front matter and cases from the ledger, diagnosis
    from `diagnoses.jsonl`; `diagnosed` is the count the latest diagnosis saw, so a page
    that grew past it is diagnosed again."""

    path = NOTES / kind / f"{name}.md"
    old = front(path)
    diagnosis = diagnoses(kind).get(name, {"cause": "", "next": "", "count": 0})
    diagnosed = diagnosis["count"]
    first = rows[0] if rows else {"category": name.split("--")[0],
                                  "slice": slice if slice == "base" else list(slice)}
    cases = "\n".join(
        f"- `{r['state_hash'][:12]}` `{r['question']}` (round {r['round']}, {r['source']}): "
        + "; ".join(f"{a.get('labeller')}: {a.get('answer')} @ {a.get('confidence')}" for a in r["answers"])
        + (f"; intended {r['intended']}" if r.get("intended") is not None else "")
        + (f"; predicted {r['predicted']}" if r.get("predicted") is not None else "")
        for r in rows[:3])
    body = (f"---\nkind: {kind}\ncategory: {first['category']}\nslice: {json.dumps(first['slice'])}\n"
            f"count: {len(rows)}\ndiagnosed: {diagnosed}\n"
            f"rounds: {joined(old.get('rounds', ''), [r['round'] for r in rows])}\n"
            f"status: {old.get('status', 'open')}\n---\n\n"
            f"# {name}\n\n{CATEGORIES.get(first['category'], '')}.\n\n## Cases\n\n{cases or '- none yet'}\n\n"
            f"## Cause\n\n{diagnosis['cause'] or UNDIAGNOSED}\n\n"
            f"## Next\n\n{diagnosis['next'] or 'Not decided yet.'}\n")
    store.write(path, body)
    return path


def write_pages(kind: str, writer=None, log=print, limit: int | None = None) -> list[str]:
    """Write or refresh every cluster page that reached `PAGE_AT` errors and grew since it
    was last written, or whose diagnosis is behind its errors when a writer is given (never
    diagnosed, deferred for want of turns, or a writer's empty answer: each is tried again
    on the next call). `writer(prompt_path, payload) -> {"cause", "next"}` is one model turn
    (`label.writer`), called at most `limit` times; without one, pages get cases and keep
    any diagnosis they had."""

    written, calls, known = [], 0, diagnoses(kind)
    unjournalled = [p.name for p in sorted((NOTES / kind).glob("*.md")) if p.stem not in known
                    and (m := re.search(r"## Cause\n\n(.*?)\n\n## Next", p.read_text(encoding="utf-8"), re.S))
                    and m.group(1).strip() != UNDIAGNOSED]
    if unjournalled:   # written before diagnoses were journalled: never overwrite a paid diagnosis
        raise ValueError(f"{kind}: pages hold diagnoses that diagnoses.jsonl does not: {unjournalled}; "
                         "move them aside before writing pages")
    for name, rows in clusters(kind).items():
        path = NOTES / kind / f"{name}.md"
        if len(rows) < PAGE_AT:
            continue
        seen = known.get(name, {}).get("count", 0)
        page = front(path)
        can = writer is not None and (limit is None or calls < limit) and seen != len(rows)
        if page.get("count") == str(len(rows)) and page.get("diagnosed") == str(seen) and not can:
            continue   # the page already shows its ledger and its journal
        if can:
            calls += 1
            got = writer(PROMPT, {"kind": kind, "category": rows[0]["category"],
                                  "meaning": CATEGORIES[rows[0]["category"]], "slice": rows[0]["slice"],
                                  "errors": rows[-SAMPLES:],
                                  "page": path.read_text(encoding="utf-8") if path.exists() else None})
            if isinstance(got, dict) and str(got.get("cause") or "").strip():   # journalled before the page
                store.append(NOTES / kind / "diagnoses.jsonl",
                             {"page": name, "count": len(rows), "cause": str(got["cause"]).strip(),
                              "next": str(got.get("next") or "").strip(), "at": time.time()})
        write_page(kind, name, rows)
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
    store.write(NOTES / "index.md", "\n".join(lines) + "\n")


def brief(kind: str) -> dict:
    """What the generator reads before a round, as RRSI's proposer reads the whole history:
    every page whatever its status, every cluster's size (under `PAGE_AT` too), and the
    slices never to aim at again. `loop.run` adds the rounds."""

    pages = {p.stem: front(p) | {"text": p.read_text(encoding="utf-8")} for p in sorted((NOTES / kind).glob("*.md"))}
    return {"pages": [{"name": n, "status": p.get("status"), "text": p["text"]} for n, p in pages.items()],
            "clusters": {n: len(rows) for n, rows in clusters(kind).items()},
            "avoid": [json.loads(p["slice"]) for p in pages.values()
                      if p.get("status") in ("falsified", "ambiguous") and p.get("slice", '"base"') != '"base"']}
