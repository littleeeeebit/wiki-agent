"""A labeller that improves with use (plan stage 3, guided labelling; owner, 2026-10-14).

A small model labels each state; a stronger model from another family verifies the labels
in batches and rewrites the small model's guide from what it rejected, after RRSI
(`loop.py`): a new guide is adopted only when it beats the current one on a held-out probe
by more than the noise floor (`loop.floor`), a guide that does not stays in the history as
not adopted so it is not proposed again, and the probe never reaches the verifier.

    round r   the labeller answers `per_round` states with the current guide
              the verifier checks them `BATCH` at a time; each batch also holds `MIRRORS`
              copies of its items with the answer flipped. A batch that endorses both an
              answer and its flipped copy is checked once more, reshuffled; doing it again
              drops the batch (`verifier_unreliable`)
              a label is kept when the labeller and the verifier give the same answer, both
              at confidence `AGREE` or more, in a batch that rejected its mirrors; anything
              else goes to the error notebook
              from the round's rejections and the guide history the verifier writes the next
              guide; the labeller answers the probe with both, and the new one is adopted
              when its paired gain clears the noise floor

Every model turn is kept in `<split>.turns.jsonl` under its prompt, model and payload, so a
rerun replays the same rounds and pays only for turns it has not made; a failed turn is
tried up to `ATTEMPTS` times, is never kept, and is made again on a rerun. `max_usd` caps the whole file's spending, reruns
included. Files, under `laya/data/<kind>/`:

- `<split>.turns.jsonl` — every paid turn.
- `<split>.guide.jsonl` — every guide proposed: its round, rules, probe accuracy, gain and
  floor, and whether it was adopted. The guide before the first adoption is empty.
- `<split>.jsonl` — the kept labels in Laya's format, with labeller, verifier and guide.

Rows come from a candidates file (`pool.rows`' shape) or a labelled file; a labelled row's
`expected` is never shown to a model, only scored against, so a pilot on labelled data
measures how often kept labels match the earlier ones.

    python laya/guided.py useful pilot --rows train.jsonl --probe validation.jsonl:60 \\
        --labeller haiku --verifier codex:gpt-6.1-sol --rounds 4 --per-round 150 --max-usd 10
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

import label
import loop
import notes
from store import append, read

AGREE = label.AGREE
ATTEMPTS = 3
BATCH = 20
MIRRORS = 2
MAX_RULES = 12
RULE_CHARS = 300
LESSON_AT = notes.PAGE_AT
SAMPLES = 20
PROMPTS = Path(__file__).resolve().parent / "prompts"
VERIFY, LESSON = PROMPTS / "verify.md", PROMPTS / "lesson.md"


class Spent(Exception):
    """The run reached its turn or spending limit."""


class Turns:
    """Model turns, each paid once: replayed from `path`, or made through `call` within
    the limits. A call returns the turn's JSON, or `None` for a failed turn."""

    def __init__(self, path: Path, call, max_turns: int, max_usd: float, log=print):
        self.path, self.call, self.max_turns, self.max_usd, self.log = path, call, max_turns, max_usd, log
        self.kept = {r["key"]: r for r in read(path)}
        self.turns, self.unknown = 0, 0
        self.usd = sum(r["cost_usd"] or 0 for r in self.kept.values())

    def __call__(self, prompt: Path, payload: dict, model: str, effort: str = "") -> dict | None:
        key = hashlib.sha256(json.dumps([prompt.name, model, effort, payload], sort_keys=True,
                                        ensure_ascii=False).encode("utf-8")).hexdigest()
        if key in self.kept:
            return self.kept[key]["json"]
        for _ in range(ATTEMPTS):   # a host turn fails now and then with no reason given
            if self.turns >= self.max_turns or self.usd >= self.max_usd:
                raise Spent(f"limit reached: {self.turns} turns this run, ${self.usd:.2f} in all")
            got = self.call(prompt, payload, model, effort)
            self.turns += 1
            if got.get("cost_usd") is None:
                self.unknown += 1
            else:
                self.usd += got["cost_usd"]
            if "error" not in got:
                break
            self.log(f"{prompt.name} on {model} failed: {got['error']}")
        else:
            return None
        row = {"key": key, "prompt": prompt.name, "model": got["model"], "cost_usd": got["cost_usd"],
               "json": got["json"]}
        append(self.path, row)
        self.kept[key] = row
        return got["json"]


def valid(question: dict, said) -> tuple[object, float] | None:
    """`(value, confidence)` of an answer in Laya's terms, or `None` when it is malformed."""

    c = said.get("confidence") if isinstance(said, dict) else None
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:
        return None
    value = label.expected(question, said.get("answer"))
    return None if value is None else (value, c)


def spoken(question: dict, value) -> str:
    return ("yes" if value else "no") if question["type"] == "noul" else value


def flipped(question: dict, value, rng: random.Random):
    if question["type"] == "noul":
        return not value
    return rng.choice(sorted(o for o in question["criteria"] if o != value))


def answers(turns: Turns, row: dict, guide: list[str], model: str) -> dict | None:
    """The labeller's answers for one state, `{}` when malformed, `None` when the turn failed."""

    payload = {"state": row["state"], "questions": {n: label.shown(q) for n, q in row["questions"].items()}}
    if guide:
        payload["guide"] = guide
    got = turns(label.PROMPT, payload, model)
    if got is None:
        return None
    said = got.get("answers")
    return said if isinstance(said, dict) else {}


def endorses(verdict, question: dict, value) -> bool:
    return isinstance(verdict, dict) and (verdict.get("verdict") == "accept"
                                          or label.expected(question, verdict.get("answer")) == value)


def verify(turns: Turns, kind: str, chunk: list[dict], model: str, effort: str, rng: random.Random):
    """The verifier's verdict for each item of `chunk` (a list in its order), `"unreliable"`
    when it endorsed both an answer and its flipped copy twice, or `None` when a turn failed."""

    for _ in range(2):
        origins = rng.sample(range(len(chunk)), min(MIRRORS, len(chunk)))
        mirrors = [{**chunk[o], "value": flipped(chunk[o]["q"], chunk[o]["value"], rng)} for o in origins]
        entries = chunk + mirrors
        order = rng.sample(range(len(entries)), len(entries))
        payload = {"kind": kind, "items": [{"id": f"i{k}", "state": entries[i]["row"]["state"],
                                            "question": label.shown(entries[i]["q"]),
                                            "proposed": spoken(entries[i]["q"], entries[i]["value"])}
                                           for k, i in enumerate(order)]}
        got = turns(VERIFY, payload, model, effort)
        if got is None:
            return None
        said = got.get("verdicts") if isinstance(got.get("verdicts"), dict) else {}
        verdicts = {i: said.get(f"i{k}") for k, i in enumerate(order)}
        # The labeller may be wrong, so its flipped copy may be the right one: only endorsing
        # both an answer and its opposite shows the batch was not checked.
        if not any(endorses(verdicts[o], chunk[o]["q"], chunk[o]["value"])
                   and endorses(verdicts[len(chunk) + m], mirrors[m]["q"], mirrors[m]["value"])
                   for m, o in enumerate(origins)):
            return [verdicts[i] for i in range(len(chunk))]
    return "unreliable"


def kept(item: dict, verdict) -> bool:
    v = valid(item["q"], verdict)
    return (isinstance(verdict, dict) and verdict.get("verdict") == "accept" and v is not None
            and v[0] == item["value"] and v[1] >= AGREE)


def wrong(turns: Turns, probe: list[dict], guide: list[str], model: str) -> set | None:
    """The probe's `(state_hash, question)` items the labeller gets wrong with `guide`."""

    out = set()
    for row in probe:
        said = answers(turns, row, guide, model)
        if said is None:
            return None
        for n, q in row["questions"].items():
            v = valid(q, said.get(n))
            if v is None or v[0] != row["expected"][n]:
                out.add((row["provenance"]["state_hash"], n))
    return out


def rows_of(kind: str, name: str) -> list[dict]:
    """A file under `laya/data/<kind>/` as labelled-shape rows holding only `kind`'s questions."""

    out = []
    for r in read(label.DATA / kind / name):
        if "state_hash" not in r:   # a labelled row; a candidate holds its hash at the top
            out.append(r)
            continue
        qs = {n: q["question"] for n, q in r["questions"].items() if q["kind"] == kind}
        if qs:
            out.append({"state": r["state"], "questions": qs,
                        "provenance": {"state_hash": r["state_hash"], "source": r["id"]}})
    return out


def run(kind: str, split: str, rows: list[dict], probe: list[dict], *, labeller: str, verifier: str,
        rounds: int, per_round: int, max_turns: int, max_usd: float, effort: str = "high",
        call=label.host_json, log=print, seed: int = 0) -> dict:
    """Run guided labelling for `rounds` rounds; returns per-round counts and what it cost."""

    if labeller == verifier:
        raise ValueError("the labeller and the verifier must differ")
    folder = label.DATA / kind
    turns = Turns(folder / f"{split}.turns.jsonl", call, max_turns, max_usd, log)
    guides_path, out_path = folder / f"{split}.guide.jsonl", folder / f"{split}.jsonl"
    guides = read(guides_path)
    written = {r["provenance"]["state_hash"] for r in read(out_path)}
    noted = notes.noted(kind)
    # Every random draw has its own seed, so a replayed round draws the same whatever an
    # earlier round skipped, and its turns are found in the file instead of paid again.
    held = {r["provenance"]["state_hash"] for r in probe}
    pending = [r for r in rows if r["provenance"]["state_hash"] not in held]
    random.Random(f"{seed}-order").shuffle(pending)
    universe = [(r["provenance"]["state_hash"], n) for r in probe for n in r["questions"]]
    guide, version, report = [], 0, []

    def note(category, item, r, verdict=None):
        h = item["row"]["provenance"]["state_hash"]
        if notes.note_id(kind, r, category, h, item["n"]) in noted:
            return
        said = [{"labeller": labeller, **item["said"]}] + ([{"verifier": verifier, **verdict}]
                                                           if isinstance(verdict, dict) else [])
        noted.add(notes.record(kind, category, round=r, slice="base", state_hash=h,
                               source=item["row"]["provenance"]["source"], question=item["n"],
                               answers=said, state=item["row"]["state"])["id"])

    try:
        for r in range(rounds):
            batch = pending[r * per_round:(r + 1) * per_round]
            if not batch:
                break
            stats = {"round": r, "guide": version, "states": len(batch), "questions": 0, "kept": 0,
                     "invalid": 0, "unsure": 0, "rejected": 0, "unreliable": 0, "failed": 0}
            items = []
            for row in batch:
                said = answers(turns, row, guide, labeller)
                if said is None:
                    stats["failed"] += 1
                    continue
                for n, q in row["questions"].items():
                    stats["questions"] += 1
                    item = {"row": row, "n": n, "q": q, "said": label.kept(said.get(n))}
                    v = valid(q, said.get(n))
                    if v is None:
                        stats["invalid"] += 1
                        note("invalid", item, r)
                    elif v[1] < AGREE:
                        stats["unsure"] += 1
                        note("unsure", item, r)
                    else:
                        items.append({**item, "value": v[0]})
            rejections, keep = [], {}
            for start in range(0, len(items), BATCH):
                chunk = items[start:start + BATCH]
                verdicts = verify(turns, kind, chunk, verifier, effort, random.Random(f"{seed}-{r}-{start}"))
                if verdicts is None:
                    stats["failed"] += len(chunk)
                    continue
                for k, item in enumerate(chunk):
                    if verdicts == "unreliable":
                        stats["unreliable"] += 1
                        note("verifier_unreliable", item, r)
                    elif kept(item, verdicts[k]):
                        keep.setdefault(item["row"]["provenance"]["state_hash"], []).append(item)
                    else:
                        stats["rejected"] += 1
                        note("verifier_reject", item, r, verdicts[k])
                        if isinstance(verdicts[k], dict):
                            rejections.append({"state": item["row"]["state"], "question": label.shown(item["q"]),
                                               "labeller": item["said"], "verifier": verdicts[k]})
            gold = [it for its in keep.values() for it in its if "expected" in it["row"]]
            if gold:
                stats["kept_match_earlier"] = round(sum(it["value"] == it["row"]["expected"][it["n"]]
                                                        for it in gold) / len(gold), 3)
            for h, its in keep.items():
                stats["kept"] += len(its)
                if h in written:
                    continue
                row = its[0]["row"]
                append(out_path, {"state": row["state"], "questions": {it["n"]: it["q"] for it in its},
                                  "expected": {it["n"]: it["value"] for it in its},
                                  "provenance": {"state_hash": h, "source": row["provenance"]["source"],
                                                 "labellers": [labeller, verifier], "guide": version}})
                written.add(h)
            # The lesson: the verifier rewrites the guide from this round's rejections.
            done = next((g for g in guides if g["round"] == r), None)
            if done is None and len(rejections) >= LESSON_AT:
                got = turns(LESSON, {"kind": kind, "guide": guide,
                                     "rejections": random.Random(f"{seed}-{r}-lesson").sample(
                                         rejections, min(SAMPLES, len(rejections))),
                                     "history": [{k: g[k] for k in ("rules", "adopted", "gain", "floor")}
                                                 for g in guides if g["round"] < r]}, verifier, effort)
                rules = [x.strip()[:RULE_CHARS] for x in (got or {}).get("rules", [])
                         if isinstance(x, str) and x.strip()][:MAX_RULES]
                if got is not None and rules != guide:
                    old, new = wrong(turns, probe, guide, labeller), wrong(turns, probe, rules, labeller)
                    if old is not None and new is not None:
                        gain, floor = loop.floor(universe, old, new, seed)
                        done = {"round": r, "version": r + 1, "rules": rules, "why": got.get("why"),
                                "adopted": gain > floor, "gain": round(gain, 4), "floor": round(floor, 4),
                                "probe_accuracy": [round(1 - len(old) / len(universe), 3),
                                                   round(1 - len(new) / len(universe), 3)]}
                        append(guides_path, done)
                        guides.append(done)
            if done is not None:
                stats["lesson"] = {k: done[k] for k in ("version", "adopted", "gain", "floor", "probe_accuracy")}
                if done["adopted"]:
                    guide, version = done["rules"], done["version"]
            report.append(stats)
            log(json.dumps(stats))
        stopped = "done"
    except Spent as limit:
        log(str(limit))
        stopped = "limit"
    return {"rounds": report, "stopped": stopped, "turns": turns.turns, "usd_in_all": round(turns.usd, 4),
            "cost_unknown": turns.unknown, "guide": {"version": version, "rules": guide}}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("kind")
    p.add_argument("split")
    p.add_argument("--rows", required=True, help="a file under laya/data/<kind>/")
    p.add_argument("--probe", required=True, help="a labelled file under laya/data/<kind>/, as name[:count]")
    p.add_argument("--labeller", required=True)
    p.add_argument("--verifier", required=True)
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--per-round", type=int, default=150)
    p.add_argument("--max-turns", type=int, default=2000)
    p.add_argument("--max-usd", type=float, required=True)
    p.add_argument("--gold", help="a labelled file whose answers the kept labels are scored against, never shown")
    a = p.parse_args()
    name, _, count = a.probe.partition(":")
    probe = rows_of(a.kind, name)[:int(count) if count else None]
    if not probe or any("expected" not in r for r in probe):
        raise SystemExit("the probe must be labelled rows")
    rows = rows_of(a.kind, a.rows)
    if a.gold:
        gold = {r["provenance"]["state_hash"]: r["expected"] for r in rows_of(a.kind, a.gold)}
        rows = [{**r, "expected": gold[h]} if set(gold.get(h := r["provenance"]["state_hash"], ())) == set(r["questions"])
                else r for r in rows]
    print(json.dumps(run(a.kind, a.split, rows, probe, labeller=a.labeller,
                         verifier=a.verifier, rounds=a.rounds, per_round=a.per_round,
                         max_turns=a.max_turns, max_usd=a.max_usd), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
