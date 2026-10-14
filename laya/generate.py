"""Candidate states for the ten retrieval kinds (plan stage 2), from the path production runs.

A teacher host turn writes questions over a seeded sample of one repository's documents
(`prompts/generate.md`); the repository is drawn from those on this machine that keep a wiki
corpus (`repos`), weighted by the square root of its size, and each question is given its
type and intent before it is written (`sample`), so the set is diverse by construction
(owner, 2026-10-14). Each question runs through `knowledge.prepare` in its repository with
the decider stubbed and the translator sending nothing (`captured`): its route request and
its first grade-evidence request are captured as production would send them, and nothing goes
to Jev, the translator or a host fallback. Each kind's candidate is one question per row,
its state the kind's view (`kinds.view`), at most `CAP` per kind from one question;
near-duplicates of the gate test and across splits are dropped (`generate`).

Files under `laya/data/`:

- `retrieval/generate.ledger.jsonl` — every teacher turn, `pending` before it is sent and
  whole once it returns: seed, repository, pages, queries, model, cost. A rerun runs the
  queries already paid for before it asks for more, and starts no turn while one is unsettled.
- `retrieval/generate.runs.jsonl` — every query's run: first its claim (the whole requests it
  captured, for its split), then how many candidates it gave, so a rerun never runs a
  query twice and never splits one query across splits: a run interrupted after its
  claim is finished from the claim, not captured again.
- `<kind>/<split>.candidates.jsonl` — each kind's candidates, `label.py`'s input.

    python laya/generate.py train --target 50 --teacher sonnet --max-turns 7
    python laya/generate.py train --abandon 4     # close an unsettled turn

Nothing is sent without `--teacher`; `--dry` shows the first turn's repository, pages and slots.
"""

import argparse
import contextlib
import hashlib
import json
import math
import random
import socket
import sys
import threading
from pathlib import Path

import gate
from kinds import NOUL, kind_of, view
from label import host_json
from pool import digest
from store import append, read

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
PROMPT = Path(__file__).resolve().parent / "prompts" / "generate.md"
SPLITS = ("train", "validation", "calibration")
RETRIEVAL = (*NOUL, "repair")
PAGES = 6
PER_TURN = 8
EXCERPT = 3000
CAP = 2   # candidates of one kind from one question, so a kind's set spans many questions
TYPES = ("answered", "partial", "bridging", "unanswered", "false_premise")
INTENTS = ("lookup", "analysis", "progress", "general", "compound", "pasted")


def prompt_version() -> str:
    return hashlib.sha256(PROMPT.read_bytes()).hexdigest()[:12]


def repos(parent: Path = ROOT.parent) -> dict[str, Path]:
    """The repositories beside this one that keep a wiki corpus, by folder name; a linked
    worktree (`.git` a file) is a copy of another and is left out."""

    return {p.name: p for p in sorted(parent.iterdir())
            if (p / ".wiki" / "corpus.json").is_file() and (p / ".git").is_dir()}


def corpus(repo: Path) -> list[dict]:
    """The corpus documents whose path resolves inside `repo`: an entry that points out of
    it (`../x.md`, an absolute path, a link) is never read, so never sent to the teacher."""

    home = repo.resolve()
    docs = json.loads((repo / ".wiki" / "corpus.json").read_text(encoding="utf-8"))["docs"]
    return [d for d in docs if (repo / d["path"]).resolve().is_relative_to(home) and (repo / d["path"]).is_file()]


def sample(seed: int, found: dict[str, Path] | None = None) -> dict:
    """The teacher's turn for `seed`: one repository (weighted by the square root of its
    document count, so the largest does not dominate), `PAGES` of its documents, and a type
    and intent for each of the `PER_TURN` questions, drawn so a turn spans them."""

    found = repos() if found is None else found
    rng = random.Random(seed)
    names = sorted(found)
    docs = {n: corpus(found[n]) for n in names}
    name = rng.choices(names, weights=[math.sqrt(len(docs[n])) for n in names])[0]
    chosen = rng.sample(docs[name], min(PAGES, len(docs[name])))
    types, intents = list(TYPES) * 2, list(INTENTS) * 2
    rng.shuffle(types)
    rng.shuffle(intents)
    pages = [{**{k: d[k] for k in ("path", "title", "heads", "lead")},
              "excerpt": (found[name] / d["path"]).read_text(encoding="utf-8", errors="replace")[:EXCERPT]}
             for d in chosen]
    return {"repo": name, "pages": pages,
            "slots": [{"type": t, "intent": i} for t, i in zip(types[:PER_TURN], intents[:PER_TURN])]}


def teacher_turn(model: str, seed: int) -> dict:
    drawn = sample(seed)
    got = host_json(PROMPT, {"pages": drawn["pages"], "slots": drawn["slots"]}, model)
    said = got.pop("json").get("queries")
    queries, used = [], set()
    for i, q in enumerate(said if isinstance(said, list) else []):
        if not (isinstance(q, dict) and isinstance(q.get("text"), str) and q["text"].strip()):
            continue
        slot = q["slot"] if isinstance(q.get("slot"), int) and 0 <= q["slot"] < PER_TURN else i
        if slot < PER_TURN and slot not in used:   # the slot it was asked for, never the teacher's own label
            used.add(slot)
            queries.append({"text": q["text"], **drawn["slots"][slot]})
    return {**got, "seed": seed, "repo": drawn["repo"], "pages": [p["path"] for p in drawn["pages"]],
            "queries": queries}


def captured(query: str, project: Path = ROOT) -> list[dict]:
    """The route request and the first grade-evidence request (each `{state, questions}`)
    that `query` makes on the production path in `project`, with no model and no
    translator request.

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
        if stage in ("route", "judge") and not stop.is_set():
            seen.append({"state": state, "questions": questions})
            if stage == "judge":
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
    # A route alone is the whole capture only when retrieval finished with nothing to judge.
    if stop.is_set() or status in ("ready", "partial"):
        return seen
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


def indexed(project: Path, wait: float) -> bool:
    """Whether the daemon has finished `project`'s index, waiting up to `wait` seconds; an
    unfinished index answers searches too, with what it has, so its answer is not enough."""

    if str(ROOT / "tool") not in sys.path:
        sys.path.append(str(ROOT / "tool"))
    import search

    return search.indexed(project, timeout=10.0 + wait, wait=wait, start=False)


WARM = 1800.0   # seconds the daemon may take to index a repository it has not read yet


def candidates(kind: str, requests: list[dict], provenance: dict) -> list[dict]:
    """`kind`'s questions, at most `CAP` (a draw seeded by the run and the kind, kept in
    their order), as rows of the kind's view: questions that share a view (a route's
    sources, a request's coverage) share one row, so no question is dropped as a
    duplicate of its neighbour, and a passage kind's row holds its one question."""

    asked = [(r, name, q) for r, req in enumerate(requests)
             for name, q in req["questions"].items() if kind_of(name) == kind]   # named, kinds left out
    if len(asked) > CAP:
        drawn = set(random.Random(f"{provenance['run']}-{kind}").sample(range(len(asked)), CAP))
        asked = [a for i, a in enumerate(asked) if i in drawn]
    rows: dict[tuple, dict] = {}
    for r, name, q in asked:
        state = view(name, kind, requests[r]["state"])
        h = digest(state)
        row = rows.setdefault((r, h), {"id": f"{provenance['run']}-{r}-{name}", "state": state, "state_hash": h,
                                       "questions": {}, "provenance": provenance})
        row["questions"][name] = {"kind": kind, "question": q}
    return list(rows.values())


def whole(req: dict) -> dict:
    """A captured request as `gate.signatures` reads a row: its whole state and every
    question of a known kind, so each passage view counts and a claim keeps them all."""

    return {"state": req["state"], "questions": {n: {"kind": kind_of(n), "question": q}
                                                 for n, q in req["questions"].items() if kind_of(n)}}


def unsettled(ledger: list[dict]) -> list[int]:
    """Seeds of teacher turns started (`pending`) that never came back or were abandoned:
    a turn that may have been paid for with nothing to show for it."""

    closed = {e["seed"] for e in ledger if "queries" in e or e.get("abandoned")}
    return sorted({e["seed"] for e in ledger if e.get("pending")} - closed)


def generate(split: str, target: int, teacher: str, max_turns: int, seed: int = 0, kinds=RETRIEVAL,
             capture=captured, turn=teacher_turn, log=print, ready=None, found=None) -> dict:
    """Candidates for `split` of each of `kinds` up to `target`; stops once every kind has
    them, or at `max_turns` teacher turns in this run. What was paid for before (the
    ledger) is run before any new turn.

    A teacher turn is journalled `pending` before it is sent and whole once it returns. A
    turn left pending, by an interruption between the two, may have been paid for: no new
    turn starts until it is settled (`--abandon <seed>`), so nothing is paid twice unseen.

    A captured request is checked whole before it is cut into views: one near-duplicate of
    a gate state (by whole state or any view) gives no candidate, nor one near a request
    whose candidates went to another split. Each kind's row is then checked against every
    candidate of that kind kept, in any split.

    A run's requests are claimed for its split (`claim`) before any candidate is written,
    so an interruption between the two cannot let a resumed run send the same query's
    other candidates to another split: every candidate of one query is in one split. A
    resumed run finishes on what it claimed, never on a fresh capture that may differ; a
    run claimed by another split is left for that split.

    Nothing starts without every gate state's text in the pool, or without the search
    daemon answering: either would make queries that cannot be checked or captured. A
    query whose own retrieval failed (`Unavailable`) is left unrun, with its reason, and no
    new turn starts (`search_unavailable`); the daemon is asked again before each turn."""

    unknown = [k for k in kinds if k not in RETRIEVAL]
    if unknown:
        raise ValueError(f"{', '.join(unknown)}: the generator writes retrieval kinds ({', '.join(RETRIEVAL)}) only")
    kinds = tuple(k for k in RETRIEVAL if k in kinds)   # one order for one set, as the run log records it
    shared = DATA / "retrieval"
    ledger, runs = shared / "generate.ledger.jsonl", shared / "generate.runs.jsonl"
    reserved = gate.reserved()
    gated = [r for r in read(gate.POOL) if r["state_hash"] in reserved]
    missing = reserved - {r["state_hash"] for r in gated}
    if missing:
        raise SystemExit(f"{len(missing)} gate states are not in {gate.POOL.name}; "
                         "run laya/pool.py before generating")
    alive = ready or searching
    if not alive():
        raise SystemExit("the search daemon is not answering; start it before generating")
    found = repos() if found is None else found
    warmed: set[str] = set()   # repositories whose index the daemon has finished in this run
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
    # A finished run is finished for the kinds it wrote; another set would skip its other
    # kinds for good, so the run log keeps the set it was started with.
    other = {tuple(r["kinds"]) for r in read(runs) if "kinds" in r} - {tuple(kinds)}
    if other:
        raise SystemExit(f"{runs} was written for kinds {list(sorted(other)[0])}; generate the same kinds")
    near = {k: gate.Near(row for s in SPLITS for row in read(DATA / k / f"{s}.candidates.jsonl")) for k in kinds}
    # Counted in questions, as labels are: a row holds every question of its kind on its view.
    have = {k: sum(len(r["questions"]) for r in read(DATA / k / f"{split}.candidates.jsonl")) for k in kinds}
    stats = {"turns": 0, "cost_usd": 0.0, "queries": 0, "kept": dict.fromkeys(kinds, 0), "near_gate": 0,
             "other_split": 0, "near_kept": 0}

    def full() -> bool:
        return all(have[k] >= target for k in kinds)

    def run_queries(entry: dict) -> bool:
        """False once a query's retrieval fails (`Unavailable`); that query is left for a rerun."""

        for i, q in enumerate(entry["queries"]):
            run = f"t{entry['seed']}-q{i}"
            if run in done or full() or claimed.get(run, split) != split:
                continue
            provenance = {"generator": "laya/generate.py", "prompt_version": entry["prompt_version"],
                          "teacher": entry["model"], "seed": entry["seed"], "repo": entry["repo"],
                          "pages": entry["pages"], "query": q["text"], "type": q.get("type"),
                          "intent": q.get("intent"), "run": run}
            if run in claimed:   # interrupted after its claim: finish on what it claimed
                kept_requests = [{"state": o["state"], "questions": {n: v["question"] for n, v in o["questions"].items()}}
                                 for o in claims[run]]
            else:
                # A repository the daemon has not indexed answers nothing until it has: its first
                # query waits for the index rather than failing as an empty search.
                if entry["repo"] not in warmed:
                    if not indexed(found[entry["repo"]], WARM):
                        stats["reason"] = f"{run}: {entry['repo']} was not indexed within {WARM:.0f} s"
                        return False
                    warmed.add(entry["repo"])
                try:
                    requests = capture(q["text"], found[entry["repo"]])
                except Unavailable as error:
                    stats["reason"] = f"{run}: {error}"
                    return False
                except Exception as error:  # noqa: BLE001 — one query's failed run is recorded, not retried forever
                    append(runs, {"run": run, "split": split, "candidates": 0, "kinds": list(kinds),
                                  "error": f"{type(error).__name__}: {error}"[:200]})
                    done.add(run)
                    continue
                kept_requests = []
                for req in requests:
                    asked = sum(kind_of(n) in kinds for n in req["questions"])
                    if near_gate.found(whole(req)):
                        stats["near_gate"] += asked
                    elif any(sides[h] != split for h in origins.found(whole(req))):
                        stats["other_split"] += asked
                    else:
                        kept_requests.append(req)
                originals = [whole(req) for req in kept_requests]
                append(runs, {"run": run, "split": split, "claim": originals, "kinds": list(kinds)})
                claimed[run] = split
                for o in originals:
                    origins.add(o)
                    sides.append(split)
            kept = 0
            for kind in kinds:
                for row in candidates(kind, kept_requests, provenance):
                    if near[kind].found(row):
                        stats["near_kept"] += 1
                    elif have[kind] < target:
                        append(DATA / kind / f"{split}.candidates.jsonl", row)
                        near[kind].add(row)
                        have[kind] += len(row["questions"])
                        stats["kept"][kind] += len(row["questions"])
                        kept += len(row["questions"])
            append(runs, {"run": run, "split": split, "candidates": kept, "kinds": list(kinds)})
            done.add(run)
            stats["queries"] += 1
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
    while not full() and stats["turns"] < max_turns:
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
        log(f"turn seed {next_seed} ({entry.get('repo')}): {len(entry['queries'])} queries, "
            f"${entry.get('cost_usd')}" + (f", {entry['error']}" if entry.get("error") else ""))
        if not run_queries(entry):
            return lost()
    return {**stats, "have": have, "stopped": "target" if full() else "turn_limit"}


def abandon(seed: int) -> None:
    """Close an unsettled teacher turn: counted as made, its cost unknown, never retried."""

    ledger = DATA / "retrieval" / "generate.ledger.jsonl"
    if seed not in unsettled(read(ledger)):
        raise SystemExit(f"seed {seed} is not an unsettled turn")
    append(ledger, {"seed": seed, "abandoned": True, "cost_usd": None})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("split", choices=SPLITS)
    p.add_argument("--target", type=int, default=0, help="candidates per kind")
    p.add_argument("--kinds", nargs="+", default=list(RETRIEVAL), choices=RETRIEVAL)
    p.add_argument("--teacher")
    p.add_argument("--max-turns", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--abandon", type=int, metavar="SEED")
    p.add_argument("--dry", action="store_true")
    a = p.parse_args()
    if a.abandon is not None:
        abandon(a.abandon)
        return
    if a.dry or not a.teacher:
        drawn = sample(a.seed)
        print(json.dumps({"prompt_version": prompt_version(), "repo": drawn["repo"],
                          "pages": [x["path"] for x in drawn["pages"]], "slots": drawn["slots"]}, indent=1))
        return
    print(json.dumps(generate(a.split, a.target, a.teacher, a.max_turns, a.seed, a.kinds), indent=1))


if __name__ == "__main__":
    main()
