"""Candidate states for a passage kind (plan stage 2), from the path production runs.

A teacher host turn writes questions over a seeded sample of the wiki (`prompts/generate.md`).
Each question runs through `knowledge.prepare` with the decider stubbed and the translator
held to English text, so every grade-evidence request is captured as production would send
it, and nothing is sent to Jev or the translator. A passage kind's candidate is one question
per row, its state the passage view (`kinds.view`). A candidate near-duplicate of a gate
state or of a candidate already kept, in any split, is dropped (`gate.Near`).

Files under `laya/data/<kind>/`:

- `generate.ledger.jsonl` — every teacher turn: seed, pages, queries, model, cost. A rerun
  runs the queries already paid for before it asks for more.
- `generate.runs.jsonl` — every query's run and how many candidates it gave, so a rerun
  never runs a query twice.
- `<split>.candidates.jsonl` — the candidates, `label.py`'s input.

    python laya/generate.py useful train --target 50 --teacher opus --max-turns 3

Nothing is sent without `--teacher`; `--dry` shows the first turn's pages.
"""

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

import gate
from kinds import PASSAGE, kind_of, view
from label import host_json
from pool import digest
from store import append, read

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
PROMPT = Path(__file__).resolve().parent / "prompts" / "generate.md"
SPLITS = ("train", "validation", "calibration")
PAGES = 6
PER_TURN = 8
EXCERPT = 3000


def prompt_version() -> str:
    return hashlib.sha256(PROMPT.read_bytes()).hexdigest()[:12]


def sample(seed: int) -> list[dict]:
    """`PAGES` wiki documents, chosen by `seed`, as the teacher reads them."""

    docs = json.loads((ROOT / ".wiki" / "corpus.json").read_text(encoding="utf-8"))["docs"]
    chosen = random.Random(seed).sample(docs, PAGES)
    return [{**{k: d[k] for k in ("path", "title", "heads", "lead")},
             "excerpt": (ROOT / d["path"]).read_text(encoding="utf-8")[:EXCERPT]} for d in chosen]


def teacher_turn(model: str, seed: int) -> dict:
    pages = sample(seed)
    got = host_json(PROMPT, {"pages": pages, "count": PER_TURN}, model)
    said = got.pop("json").get("queries")
    queries = [q for q in said if isinstance(q, dict) and isinstance(q.get("text"), str) and q["text"].strip()] \
        if isinstance(said, list) else []
    return {**got, "seed": seed, "pages": [p["path"] for p in pages], "queries": queries}


def captured(query: str, project: str | None = None) -> list[dict]:
    """The grade-evidence requests (`{state, questions}`) that `query` makes on the
    production path, with no model and no translator called."""

    if str(ROOT / "tool") not in sys.path:
        sys.path.append(str(ROOT / "tool"))
    import decision
    from common.language import language
    from main import knowledge

    seen: list[dict] = []

    def evaluate(cfg, state, questions, trace, budget=None, stage=""):
        # Every question uncertain: a route still retrieves, a source is still searched,
        # nothing is graded away, and a repair defers, so the run ends after its first judge.
        if stage == "judge":
            seen.append({"state": state, "questions": questions})
        return {n: 0.5 if q["type"] == "noul" else
                {"choice": decision.DEFER if decision.DEFER in q["criteria"] else next(iter(q["criteria"])),
                 "confidence": 0.5, "probabilities": {c: 1 / len(q["criteria"]) for c in q["criteria"]}}
                for n, q in questions.items()}

    real = knowledge.translate.english

    def english(texts, deadline, held=None, cancel=None):
        # English passes as production reads it, with no request; other text is left unread.
        kept = [i for i, t in enumerate(texts) if language(t) == "en"]
        out = [{"text": None, "status": "unavailable", "reason": "laya_generator", "request": None,
                "cached": False} for _ in texts]
        for i, o in zip(kept, real([texts[i] for i in kept], deadline) if kept else []):
            out[i] = o
        return out

    held = decision.evaluate, knowledge.translate.english, knowledge.falls_back
    decision.evaluate, knowledge.translate.english, knowledge.falls_back = evaluate, english, lambda cfg: False
    try:
        cfg = decision.Config("shadow", decision.MODEL, "file", key="laya-generator")
        knowledge.prepare(query, project, cfg=cfg, cache=None)
    finally:
        decision.evaluate, knowledge.translate.english, knowledge.falls_back = held
    return seen


def candidates(kind: str, requests: list[dict], provenance: dict) -> list[dict]:
    """One row per `kind` question, its state the passage view."""

    out = []
    for r, req in enumerate(requests):
        for name, q in req["questions"].items():   # the transport's questions: named, kinds left out
            if kind_of(name) != kind:
                continue
            state = view(name, kind, req["state"])
            out.append({"id": f"{provenance['run']}-{r}-{name}", "state": state, "state_hash": digest(state),
                        "questions": {name: {"kind": kind, "question": q}},
                        "provenance": provenance})
    return out


def generate(kind: str, split: str, target: int, teacher: str, max_turns: int, seed: int = 0,
             capture=captured, turn=teacher_turn, log=print) -> dict:
    """Candidates for `split` of `kind` up to `target`; stops at `max_turns` teacher turns
    in this run. What was paid for before (the ledger) is run before any new turn."""

    if kind not in PASSAGE:
        raise ValueError(f"{kind}: the generator writes passage kinds ({', '.join(PASSAGE)}) only")
    folder = DATA / kind
    ledger, runs = folder / "generate.ledger.jsonl", folder / "generate.runs.jsonl"
    near = gate.Near([r for r in read(gate.POOL) if r["state_hash"] in gate.reserved()])
    gated = near.rows
    for s in SPLITS:
        for row in read(folder / f"{s}.candidates.jsonl"):
            near.add(row)
    have = len(read(folder / f"{split}.candidates.jsonl"))
    done = {r["run"] for r in read(runs)}
    stats = {"turns": 0, "cost_usd": 0.0, "queries": 0, "kept": 0, "near_gate": 0, "near_kept": 0}

    def run_queries(entry: dict) -> None:
        nonlocal have
        for i, q in enumerate(entry["queries"]):
            run = f"t{entry['seed']}-q{i}"
            if run in done or have >= target:
                continue
            provenance = {"generator": "laya/generate.py", "prompt_version": entry["prompt_version"],
                          "teacher": entry["model"], "seed": entry["seed"], "pages": entry["pages"],
                          "query": q["text"], "type": q.get("type"), "run": run}
            kept = 0
            try:
                requests = capture(q["text"])
            except Exception as error:  # noqa: BLE001 — one query's failed run is recorded, not retried forever
                append(runs, {"run": run, "candidates": 0, "error": f"{type(error).__name__}: {error}"[:200]})
                done.add(run)
                continue
            for row in candidates(kind, requests, provenance):
                hits = near.found(row)
                if hits:
                    stats["near_gate" if min(hits) < gated else "near_kept"] += 1
                    continue
                if have < target:
                    append(folder / f"{split}.candidates.jsonl", row)
                    near.add(row)
                    have, kept = have + 1, kept + 1
            append(runs, {"run": run, "candidates": kept})
            done.add(run)
            stats["queries"] += 1
            stats["kept"] += kept

    paid = read(ledger)
    for entry in paid:
        run_queries(entry)
    seeds = {e["seed"] for e in paid}
    next_seed = seed
    while have < target and stats["turns"] < max_turns:
        while next_seed in seeds:
            next_seed += 1
        entry = {**turn(teacher, next_seed), "prompt_version": prompt_version()}
        append(ledger, entry)
        seeds.add(next_seed)
        stats["turns"] += 1
        stats["cost_usd"] += entry.get("cost_usd") or 0.0
        log(f"turn seed {next_seed}: {len(entry['queries'])} queries, ${entry.get('cost_usd')}"
            + (f", {entry['error']}" if entry.get("error") else ""))
        run_queries(entry)
    return {**stats, "have": have, "stopped": "target" if have >= target else "turn_limit"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("kind")
    p.add_argument("split", choices=SPLITS)
    p.add_argument("--target", type=int, required=True)
    p.add_argument("--teacher")
    p.add_argument("--max-turns", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--dry", action="store_true")
    a = p.parse_args()
    if a.dry or not a.teacher:
        print(json.dumps({"prompt_version": prompt_version(), "pages": [x["path"] for x in sample(a.seed)],
                          "per_turn": PER_TURN}, indent=1))
        return
    print(json.dumps(generate(a.kind, a.split, a.target, a.teacher, a.max_turns, a.seed), indent=1))


if __name__ == "__main__":
    main()
