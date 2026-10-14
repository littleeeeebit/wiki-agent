"""Candidate states for a passage kind (plan stage 2), from the path production runs.

A teacher host turn writes questions over a seeded sample of this repository's documents
(`prompts/generate.md`). Each question runs through `knowledge.prepare` in this repository
with the decider stubbed and the translator sending nothing (`captured`), so its first
grade-evidence request is captured as production would send it, and nothing goes to Jev,
the translator or a host fallback. A passage kind's candidate is one question per row, its
state the passage view (`kinds.view`); near-duplicates of the gate test and across splits
are dropped (`generate`).

Files under `laya/data/<kind>/`:

- `generate.ledger.jsonl` — every teacher turn, `pending` before it is sent and whole once
  it returns: seed, pages, queries, model, cost. A rerun runs the queries already paid for
  before it asks for more, and starts no turn while one is unsettled.
- `generate.runs.jsonl` — every query's run: first its claim (the whole requests it
  captured, for its split), then how many candidates it gave, so a rerun never runs a
  query twice and never splits one request across splits: a run interrupted after its
  claim is finished from the claim, not captured again.
- `<split>.candidates.jsonl` — the candidates, `label.py`'s input.

    python laya/generate.py useful train --target 50 --teacher opus --max-turns 3
    python laya/generate.py useful train --abandon 4     # close an unsettled turn

Nothing is sent without `--teacher`; `--dry` shows the first turn's pages.
"""

import argparse
import contextlib
import hashlib
import json
import random
import socket
import sys
import threading
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


def captured(query: str, project: Path = ROOT) -> list[dict]:
    """The first grade-evidence request (`{state, questions}`) that `query` makes on the
    production path in `project`, with no model and no translator request.

    The translator's one request (`translate._ask`) answers nothing, so English passes as
    production reads it, a cached translation is used, and anything else is left unread; a
    question is not split (`translate.parts`). The run is cancelled once the first
    grade-evidence request is answered, before code's own repairs run."""

    if str(ROOT / "tool") not in sys.path:
        sys.path.append(str(ROOT / "tool"))
    import decision
    from main import knowledge

    seen: list[dict] = []
    stop = threading.Event()

    def evaluate(cfg, state, questions, trace, budget=None, stage=""):
        # Every question uncertain: a route still retrieves, a source is still searched.
        if stage == "judge":
            seen.append({"state": state, "questions": questions})
            stop.set()
        return {n: 0.5 if q["type"] == "noul" else
                {"choice": decision.DEFER if decision.DEFER in q["criteria"] else next(iter(q["criteria"])),
                 "confidence": 0.5, "probabilities": {c: 1 / len(q["criteria"]) for c in q["criteria"]}}
                for n, q in questions.items()}

    def no_spawn():
        raise RuntimeError("laya generator: the search daemon must already be running")

    def daemon_only(req, project_, timeout, *args, **kwargs):
        return real_retrieve(req, project_, timeout, *args, **{**kwargs, "start": False})

    # Retrieval only from a daemon already running: none is started, and no index is built in
    # this process, either of which may download embedding models (`search.daemon`).
    translate, search, real_retrieve = knowledge.translate, sys.modules["search"], knowledge.retrieve_from_daemon
    held = (decision.evaluate, translate._ask, knowledge.falls_back, knowledge.retrieve_from_daemon,
            knowledge.cold, search.spawn)
    (decision.evaluate, translate._ask, knowledge.falls_back, knowledge.retrieve_from_daemon,
     knowledge.cold, search.spawn) = (evaluate, lambda *a, **k: None, lambda cfg: False, daemon_only,
                                      lambda *a, **k: None, no_spawn)
    try:
        with loopback_only():
            cfg = decision.Config("shadow", decision.MODEL, "file", key="laya-generator")
            dossier = knowledge.prepare(query, project, cfg=cfg, cache=None, cancel=stop)
    finally:
        (decision.evaluate, translate._ask, knowledge.falls_back, knowledge.retrieve_from_daemon,
         knowledge.cold, search.spawn) = held
    status, reason = dossier.get("status"), dossier.get("reason")
    if seen or status in ("ready", "partial"):
        return seen[:1]
    if (status in ("exhausted", "cancelled") or reason == "retrieval_unavailable"
            or {"baseline": "retrieval_unavailable"} in dossier.get("limits", [])):
        raise Unavailable(f"{status}: {reason}")
    # The query's own failure, the same on every rerun (a question the stubbed translator
    # leaves unread ends `normalization_failed`): recorded as its run's error.
    raise RuntimeError(f"{status}: {reason}")


class Unavailable(Exception):
    """A query's retrieval did not finish (search unavailable, out of budget, cancelled)
    before any grade-evidence request; it is left unrun rather than recorded as empty."""


LOCAL = ("127.0.0.1", "::1", "localhost")


@contextlib.contextmanager
def loopback_only():
    """No connection and no name lookup leaves this machine while the block runs, whatever
    path in `knowledge.prepare` would make one (Jev, the translator, arXiv, a URL fetch):
    the stubs above give each its answer, and this is the boundary under all of them."""

    connect, connect_ex, lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def local(address) -> bool:
        return isinstance(address, tuple) and str(address[0]) in LOCAL or isinstance(address, (str, bytes))

    def guarded(real):
        def call(self, address, *args):
            if not local(address):
                raise OSError(f"laya generator: no connection leaves this machine ({address[0]})")
            return real(self, address, *args)
        return call

    def guarded_lookup(host, *args, **kwargs):
        if host is not None and str(host) not in LOCAL:
            raise OSError(f"laya generator: no name lookup leaves this machine ({host})")
        return lookup(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex = guarded(connect), guarded(connect_ex)
    socket.getaddrinfo = guarded_lookup
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, lookup


def searching(project: Path = ROOT) -> bool:
    """Whether the search daemon is running and answers, asked without starting one."""

    if str(ROOT / "tool") not in sys.path:
        sys.path.append(str(ROOT / "tool"))
    import search

    return search.ask("laya generator readiness", project, timeout=10.0, start=False) is not None


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


def whole(req: dict) -> dict:
    """A captured request as `gate.signatures` reads a row: its whole state and every
    passage-kind question, so each of its views counts."""

    return {"state": req["state"], "questions": {n: {"kind": kind_of(n), "question": q}
                                                 for n, q in req["questions"].items() if kind_of(n) in PASSAGE}}


def unsettled(ledger: list[dict]) -> list[int]:
    """Seeds of teacher turns started (`pending`) that never came back or were abandoned:
    a turn that may have been paid for with nothing to show for it."""

    closed = {e["seed"] for e in ledger if "queries" in e or e.get("abandoned")}
    return sorted({e["seed"] for e in ledger if e.get("pending")} - closed)


def generate(kind: str, split: str, target: int, teacher: str, max_turns: int, seed: int = 0,
             capture=captured, turn=teacher_turn, log=print, ready=None) -> dict:
    """Candidates for `split` of `kind` up to `target`; stops at `max_turns` teacher turns
    in this run. What was paid for before (the ledger) is run before any new turn.

    A teacher turn is journalled `pending` before it is sent and whole once it returns. A
    turn left pending, by an interruption between the two, may have been paid for: no new
    turn starts until it is settled (`--abandon <seed>`), so nothing is paid twice unseen.

    A captured request is checked whole before it is cut into views: one near-duplicate of
    a gate state (by whole state or any view) gives no candidate, nor one near a request
    whose candidates went to another split. Each view is then checked against every
    candidate kept, in any split.

    A run's requests are claimed for its split (`claim`) before any candidate is written,
    so an interruption between the two cannot let a resumed run send the same request's
    other views to another split. A resumed run finishes on what it claimed, never on a
    fresh capture that may differ; a run claimed by another split is left for that split.

    Nothing starts without every gate state's text in the pool, or without the search
    daemon answering: either would make queries that cannot be checked or captured. A
    query whose own retrieval failed (`Unavailable`) is left unrun, with its reason, and no
    new turn starts (`search_unavailable`); the daemon is asked again before each turn."""

    if kind not in PASSAGE:
        raise ValueError(f"{kind}: the generator writes passage kinds ({', '.join(PASSAGE)}) only")
    folder = DATA / kind
    ledger, runs = folder / "generate.ledger.jsonl", folder / "generate.runs.jsonl"
    reserved = gate.reserved()
    gated = [r for r in read(gate.POOL) if r["state_hash"] in reserved]
    missing = reserved - {r["state_hash"] for r in gated}
    if missing:
        raise SystemExit(f"{len(missing)} gate states are not in {gate.POOL.name}; "
                         "run laya/pool.py before generating")
    alive = ready or searching
    if not alive():
        raise SystemExit("the search daemon is not answering; start it before generating")
    near_gate = gate.Near(gated)
    origins, sides, claimed, claims, done = gate.Near(), [], {}, {}, set()
    for r in read(runs):
        if "claim" in r:
            claimed[r["run"]], claims[r["run"]] = r["split"], r["claim"]
            for o in r["claim"]:
                origins.add(o)
                sides.append(r["split"])
        else:
            done.add(r["run"])
    unowned = {r["run"] for r in read(runs) if "candidates" in r and "error" not in r} - claimed.keys()
    if unowned:   # written before claims: which split owns those requests cannot be told
        raise SystemExit(f"{runs.name} has runs with no claim ({', '.join(sorted(unowned)[:3])}); "
                         "they predate claims, so remove that kind's generated files before generating")
    near = gate.Near(row for s in SPLITS for row in read(folder / f"{s}.candidates.jsonl"))
    have = len(read(folder / f"{split}.candidates.jsonl"))
    stats = {"turns": 0, "cost_usd": 0.0, "queries": 0, "kept": 0, "near_gate": 0, "other_split": 0,
             "near_kept": 0}

    def run_queries(entry: dict) -> bool:
        """False once a query's retrieval fails (`Unavailable`); that query is left for a rerun."""

        nonlocal have
        for i, q in enumerate(entry["queries"]):
            run = f"t{entry['seed']}-q{i}"
            if run in done or have >= target or claimed.get(run, split) != split:
                continue
            provenance = {"generator": "laya/generate.py", "prompt_version": entry["prompt_version"],
                          "teacher": entry["model"], "seed": entry["seed"], "pages": entry["pages"],
                          "query": q["text"], "type": q.get("type"), "run": run}
            if run in claimed:   # interrupted after its claim: finish on what it claimed
                kept_requests = [{"state": o["state"], "questions": {n: v["question"] for n, v in o["questions"].items()}}
                                 for o in claims[run]]
            else:
                try:
                    requests = capture(q["text"])
                except Unavailable as error:
                    stats["reason"] = f"{run}: {error}"
                    return False
                except Exception as error:  # noqa: BLE001 — one query's failed run is recorded, not retried forever
                    append(runs, {"run": run, "split": split, "candidates": 0,
                                  "error": f"{type(error).__name__}: {error}"[:200]})
                    done.add(run)
                    continue
                asked = sum(kind_of(n) == kind for req in requests for n in req["questions"])
                kept_requests = []
                for req in requests:
                    if near_gate.found(whole(req)):
                        stats["near_gate"] += asked
                    elif any(sides[h] != split for h in origins.found(whole(req))):
                        stats["other_split"] += asked
                    else:
                        kept_requests.append(req)
                originals = [whole(req) for req in kept_requests]
                append(runs, {"run": run, "split": split, "claim": originals})
                claimed[run] = split
                for o in originals:
                    origins.add(o)
                    sides.append(split)
            kept = 0
            for row in candidates(kind, kept_requests, provenance):
                if near.found(row):
                    stats["near_kept"] += 1
                elif have < target:
                    append(folder / f"{split}.candidates.jsonl", row)
                    near.add(row)
                    have, kept = have + 1, kept + 1
            append(runs, {"run": run, "split": split, "candidates": kept})
            done.add(run)
            stats["queries"] += 1
            stats["kept"] += kept
        return True

    def lost() -> dict:
        log(f"search is unavailable ({stats.get('reason', 'the daemon does not answer')}); start it and rerun")
        return {**stats, "have": have, "stopped": "search_unavailable"}

    paid = read(ledger)
    for entry in paid:
        if "queries" in entry and not run_queries(entry):
            return lost()
    open_turns = unsettled(paid)
    if open_turns:
        log(f"teacher turns {open_turns} were sent and never recorded; settle them before a new turn")
        return {**stats, "have": have, "stopped": "unsettled_turn", "unsettled": open_turns}
    seeds = {e["seed"] for e in paid}
    next_seed = seed
    while have < target and stats["turns"] < max_turns:
        if not alive():   # it may have gone since the start: no turn is paid for that cannot run
            return lost()
        while next_seed in seeds:
            next_seed += 1
        append(ledger, {"seed": next_seed, "pending": True, "model": teacher, "prompt_version": prompt_version()})
        seeds.add(next_seed)
        entry = {**turn(teacher, next_seed), "prompt_version": prompt_version()}
        append(ledger, entry)
        stats["turns"] += 1
        stats["cost_usd"] += entry.get("cost_usd") or 0.0
        log(f"turn seed {next_seed}: {len(entry['queries'])} queries, ${entry.get('cost_usd')}"
            + (f", {entry['error']}" if entry.get("error") else ""))
        if not run_queries(entry):
            return lost()
    return {**stats, "have": have, "stopped": "target" if have >= target else "turn_limit"}


def abandon(kind: str, seed: int) -> None:
    """Close an unsettled teacher turn: counted as made, its cost unknown, never retried."""

    ledger = DATA / kind / "generate.ledger.jsonl"
    if seed not in unsettled(read(ledger)):
        raise SystemExit(f"seed {seed} is not an unsettled turn of {kind}")
    append(ledger, {"seed": seed, "abandoned": True, "cost_usd": None})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("kind")
    p.add_argument("split", choices=SPLITS)
    p.add_argument("--target", type=int, default=0)
    p.add_argument("--teacher")
    p.add_argument("--max-turns", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--abandon", type=int, metavar="SEED")
    p.add_argument("--dry", action="store_true")
    a = p.parse_args()
    if a.abandon is not None:
        abandon(a.kind, a.abandon)
        return
    if a.dry or not a.teacher:
        print(json.dumps({"prompt_version": prompt_version(), "pages": [x["path"] for x in sample(a.seed)],
                          "per_turn": PER_TURN}, indent=1))
        return
    print(json.dumps(generate(a.kind, a.split, a.target, a.teacher, a.max_turns, a.seed), indent=1))


if __name__ == "__main__":
    main()
