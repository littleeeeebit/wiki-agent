"""A labeller that improves with use (plan stage 3, guided labelling; owner, 2026-10-14).

A small model labels each state; a stronger model from another family verifies the labels
in batches and rewrites the small model's guide from what it rejected, after RRSI
(`loop.py`): a new guide is adopted only when it earns more kept labels than the current one
on the same fresh states by more than the noise floor (`loop.floor`), and a guide that does
not stays in the history as not adopted so it is not proposed again. No earlier labels are
needed, so it works for a kind that has none (a held-out probe of easy agreed labels could
not show a guide helping: `.wiki/laya-insight-guide-probe.md`).

    round r   the labeller answers `per_round` states with the current guide and, when the
              last round proposed one, with the candidate guide too
              the verifier checks the answers `BATCH` at a time, never told which guide wrote
              one; an answer both guides gave is checked once. Each batch also holds
              `MIRRORS` copies of its items with the answer flipped. A batch that endorses
              both an answer and its flipped copy is checked once more, reshuffled; doing it
              again drops the batch (`verifier_unreliable`)
              a label is kept when the labeller and the verifier give the same answer, both
              at confidence `AGREE` or more, in a batch that rejected its mirrors
              a candidate is adopted when its paired gain in kept labels clears the noise
              floor; the round keeps the adopted guide's labels, and its other answers go to
              the error notebook
              from those rejections and the guide history the verifier writes the next
              round's candidate

Every model turn is kept in `<split>.turns.jsonl` under its prompt, model and payload, so a
rerun replays the same rounds and pays only for turns it has not made; a failed turn is
tried up to `ATTEMPTS` times, is never kept, and is made again on a rerun. `max_usd` caps
the whole file's spending, reruns included. Files, under `laya/data/<kind>/`:

- `<split>.turns.jsonl` — every paid turn.
- `<split>.guide.jsonl` — every candidate tried: the round it was tried in, its rules, the
  kept rates of both guides, gain and floor, and whether it was adopted. The guide before
  the first adoption is empty.
- `<split>.jsonl` — the kept labels in Laya's format, with labeller, verifier and guide.

Rows come from a candidates file (`pool.rows`' shape) or a labelled file; a labelled row's
`expected` is never shown to a model, only scored against, so a pilot on labelled data
measures how often kept labels match the earlier ones.

    python laya/guided.py useful pilot --rows train.candidates.jsonl --gold train.jsonl \\
        --labeller haiku --verifier codex:gpt-6.1-sol --rounds 5 --per-round 148 --max-usd 10
"""

import argparse
import hashlib
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
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
LABELLERS, VERIFIERS = 8, 4   # turns at once: a labeller turn takes ~10 s, a verifier batch ~60 s
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
        self.lock = threading.Lock()   # turns run on worker threads

    def __call__(self, prompt: Path, payload: dict, model: str, effort: str = "") -> dict | None:
        key = hashlib.sha256(json.dumps([prompt.name, model, effort, payload], sort_keys=True,
                                        ensure_ascii=False).encode("utf-8")).hexdigest()
        with self.lock:
            if key in self.kept:
                return self.kept[key]["json"]
        for _ in range(ATTEMPTS):   # a host turn fails now and then with no reason given
            with self.lock:
                # ponytail: turns already in flight may pass the limit by one worker pool's worth
                if self.turns >= self.max_turns or self.usd >= self.max_usd:
                    raise Spent(f"limit reached: {self.turns} turns this run, ${self.usd:.2f} in all")
                self.turns += 1
            got = self.call(prompt, payload, model, effort)
            with self.lock:
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
        with self.lock:
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


def outcomes(turns: Turns, kind: str, batch: list[dict], tried: list[list[str]], labeller: str,
             verifier: str, effort: str, seed) -> list[dict]:
    """For each guide in `tried`, every `(state_hash, question)` of `batch` mapped to
    `(outcome, item, verdict)`: outcome `kept`, `verifier_reject`, `verifier_unreliable`,
    `invalid`, `unsure` or `failed`. An answer both guides gave is verified once."""

    with ThreadPoolExecutor(LABELLERS) as pool:
        said = [list(pool.map(lambda row, g=g: answers(turns, row, g, labeller), batch)) for g in tried]
    staged, entries, index = [], [], {}
    for per in said:
        res = {}
        for row, got in zip(batch, per):
            for n, q in row["questions"].items():
                key = (row["provenance"]["state_hash"], n)
                if got is None:
                    res[key] = ("failed", None, None)
                    continue
                item = {"row": row, "n": n, "q": q, "said": label.kept(got.get(n))}
                v = valid(q, got.get(n))
                if v is None or v[1] < AGREE:
                    res[key] = ("invalid" if v is None else "unsure", item, None)
                    continue
                entry = (*key, json.dumps(v[0]))
                if entry not in index:
                    index[entry] = len(entries)
                    entries.append({**item, "value": v[0]})
                res[key] = ("verify", {**item, "value": v[0]}, index[entry])
        staged.append(res)
    starts = range(0, len(entries), BATCH)
    with ThreadPoolExecutor(VERIFIERS) as pool:
        checked = list(pool.map(lambda s: verify(turns, kind, entries[s:s + BATCH], verifier, effort,
                                                 random.Random(f"{seed}-{s}")), starts))
    verdicts = []
    for s, got in zip(starts, checked):
        size = len(entries[s:s + BATCH])
        verdicts += ["failed"] * size if got is None else ["unreliable"] * size if got == "unreliable" else got
    out = []
    for res in staged:
        done = {}
        for key, (outcome, item, at) in res.items():
            if outcome != "verify":
                done[key] = (outcome, item, None)
            elif verdicts[at] in ("failed", "unreliable"):
                done[key] = ("failed" if verdicts[at] == "failed" else "verifier_unreliable", item, None)
            else:
                done[key] = ("kept" if kept(item, verdicts[at]) else "verifier_reject", item, verdicts[at])
        out.append(done)
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


def run(kind: str, split: str, rows: list[dict], *, labeller: str, verifier: str,
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
    pending = list(rows)
    random.Random(f"{seed}-order").shuffle(pending)
    guide, version, report = [], 0, []
    candidate = None   # `(rules, why)` the last round proposed, tried in this one

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
            tried = [guide] + ([candidate[0]] if candidate else [])
            results = outcomes(turns, kind, batch, tried, labeller, verifier, effort, f"{seed}-{r}")
            if candidate:
                # Paired on the states both guides answered and the verifier settled.
                settled = sorted(k for k in results[0] if all(res[k][0] not in ("failed", "verifier_unreliable")
                                                              for res in results))
                old, new = ({k for k in settled if res[k][0] != "kept"} for res in results)
                gain, floor = loop.floor(settled, old, new, seed) if settled else (0.0, 0.0)
                trial = next((g for g in guides if g["round"] == r), None)
                if trial is None:
                    trial = {"round": r, "version": r, "rules": candidate[0], "why": candidate[1],
                             "adopted": gain > floor, "gain": round(gain, 4), "floor": round(floor, 4),
                             "kept_rate": [round(1 - len(old) / max(len(settled), 1), 3),
                                           round(1 - len(new) / max(len(settled), 1), 3)]}
                    append(guides_path, trial)
                    guides.append(trial)
                if trial["adopted"]:
                    guide, version = trial["rules"], trial["version"]
            chosen = results[1] if candidate and trial["adopted"] else results[0]
            stats = {"round": r, "guide": version, "states": len(batch), "questions": len(chosen), "kept": 0,
                     "invalid": 0, "unsure": 0, "rejected": 0, "unreliable": 0, "failed": 0}
            if candidate:
                stats["trial"] = {k: trial[k] for k in ("version", "adopted", "gain", "floor", "kept_rate")}
            rejections, keep = [], {}
            for (h, _), (outcome, item, verdict) in chosen.items():
                if outcome == "kept":
                    keep.setdefault(h, []).append(item)
                    continue
                stats[{"verifier_reject": "rejected", "verifier_unreliable": "unreliable"}.get(outcome, outcome)] += 1
                if outcome != "failed":   # a failed turn is made again on a rerun
                    note(outcome, item, r, verdict)
                if outcome == "verifier_reject" and isinstance(verdict, dict):
                    rejections.append({"state": item["row"]["state"], "question": label.shown(item["q"]),
                                       "labeller": item["said"], "verifier": verdict})
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
            # The lesson: the verifier writes the next round's candidate from these rejections.
            candidate = None
            if len(rejections) >= LESSON_AT and pending[(r + 1) * per_round:] and r + 1 < rounds:
                got = turns(LESSON, {"kind": kind, "guide": guide,
                                     "rejections": random.Random(f"{seed}-{r}-lesson").sample(
                                         rejections, min(SAMPLES, len(rejections))),
                                     "history": [{k: g[k] for k in ("rules", "adopted", "gain", "floor")}
                                                 for g in guides if g["round"] <= r]}, verifier, effort)
                rules = [x.strip()[:RULE_CHARS] for x in (got or {}).get("rules", [])
                         if isinstance(x, str) and x.strip()][:MAX_RULES]
                if got is not None and rules and rules != guide:
                    candidate = (rules, got.get("why"))
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
    p.add_argument("--labeller", required=True)
    p.add_argument("--verifier", required=True)
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--per-round", type=int, default=150)
    p.add_argument("--max-turns", type=int, default=2000)
    p.add_argument("--max-usd", type=float, required=True)
    p.add_argument("--gold", help="a labelled file whose answers the kept labels are scored against, never shown")
    a = p.parse_args()
    rows = rows_of(a.kind, a.rows)
    if a.gold:
        gold = {r["provenance"]["state_hash"]: r["expected"] for r in rows_of(a.kind, a.gold)}
        rows = [{**r, "expected": gold[h]} if set(gold.get(h := r["provenance"]["state_hash"], ())) == set(r["questions"])
                else r for r in rows]
    print(json.dumps(run(a.kind, a.split, rows, labeller=a.labeller,
                         verifier=a.verifier, rounds=a.rounds, per_round=a.per_round,
                         max_turns=a.max_turns, max_usd=a.max_usd), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
