"""`python tool/eval/arms.py <arm> [--collect] [--gate <rows> --gate-old <result>] [--tokens N]`

Stage 11 of `docs/plans/jev/`: ask Jev the way its documentation says it
works, one arm at a time, and compare old against new on the same labelled
cases.

- `view` (A): each passage in its own request, holding only that passage,
  the query and the requirements; coverage over only its listed passages.
- `direct` (B): coverage quotes the requirement; conflict is two literal
  questions combined in code; source names the concrete condition.
- `order` (C): repair asked in two option orders; an answer that changes
  with the order defers.
- `plain` (D): ask and analysis without stacked negations.

The old side of `view`, `direct` and `order` is the recorded calibration
scores (`eval/jev/calibration.scores.json`); only the new arm is asked.
`plain`'s kinds are labelled only in the v7 intents
(`eval/jev/reliability-v8/intents.json`, their calibration split), never
asked alone before, so both sides are asked there, in the same reduced
route request. Each kind is fitted with `eval.policy.fit` on both sides; the
arm is adopted when its total cost falls and no kind's rises by more than one
case's worst cost. With `--gate`, coverage is also asked on the labelled
coverage gate states (Laya stage 6) against Jev's answers recorded there
(`--gate-old`, a `laya/jev_gate.py` result); there coverage is asked alone, so
`direct`'s gate numbers carry part of `view`'s change too and are a
diagnostic, the calibration split the measure. `--collect` stops before a
request would pass `--tokens` Jev tokens. Records go to
`raw/eval/jev/arms-<arm>-<timestamp>.json`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common.budget import Budget  # noqa: E402
from eval import policy  # noqa: E402
from eval.baseline import RAW  # noqa: E402
from main.knowledge import ANALYSIS, ASK, DESCRIBED, PROMPTS, judge_questions  # noqa: E402

ARMS = ("view", "direct", "order", "plain")
INTENTS = policy.HUB / "eval" / "jev" / "reliability-v8" / "intents.json"

# B. The requirement's own words, not its id (the page: name the relevant parts of state).
COVERAGE = ('Requirement {id} asks: "{text}". Do the passages listed in complete_passages, taken together, state '
            "what it asks for, without assuming missing facts? Only passages listed in complete_passages count.")
# B. The old question's negated exception becomes its own literal question; code combines them.
CONFLICT_QUERY = "Does passage {id} state that something the query says or takes as true is false?"
CONFLICT_OTHER = ("Does passage {id} disagree only with another passage, on a point the query does not take as "
                  "true or false?")
SOURCE = "Does the query ask about something that source '{source}' contains? It contains: {description}"
# D. One positive condition first, then what does not count, each stated once.
PLAIN = {
    "ask": ("Is query part {id} a question or request that the user puts to the assistant? Answer no when the part "
            "is material the user supplied for the assistant to work on: a pasted notice, document or message, "
            "instructions written for someone else, or a question someone else asked that the user quotes. Such a "
            "part is material even when the user then asks about the same subject."),
    "analysis": ("Does the query ask the assistant to work something out itself, such as a comparison, a judgment, "
                 "an assessment, an opinion, or a plan or diagnosis for a particular problem the team faces? "
                 "Answer no when it asks for facts the repository's sources state. Answer no for a calculation, a "
                 "conversion, a count, a sort or a rewording of text the query gives: each has one right result. "
                 "Answer no for what to do, or how, in a routine situation that a standing policy, procedure or "
                 "convention covers: the sources state that answer."),
}
OLD_PLAIN = {"ask": ASK, "analysis": ANALYSIS}
# What a wrong verdict costs where the workflow acts on it (`Flow.route`): a question
# read as material is never answered; analysis skips the claim check only on a sure yes.
COSTS = {"ask": {True: {"no": 10, "uncertain": 0.5, "yes": 0}, False: {"no": 0, "uncertain": 0.5, "yes": 1}},
         "analysis": {True: {"no": 1, "uncertain": 1, "yes": 0}, False: {"no": 0, "uncertain": 0, "yes": 10}}}


def noul(kind: str, candidate, text: str) -> dict:
    return {"decision": kind, "candidate": candidate, "question": decision.noul(text)}


def judged(case: dict) -> tuple[dict, dict] | None:
    """The case's judge request as the workflow sends it, if it has passages."""

    return next(((s, q) for kind, s, q in policy.requests(case) if kind == "judge"), None)


def view(case: dict) -> list[tuple[str, dict, dict]]:
    """A: the judge request's passage and coverage questions, each reading only what it needs."""

    if not (got := judged(case)):
        return []
    state, old = got
    context = {"query": state["query"], "requirements": state["requirements"]}
    out = [("judge", {**context, "passages": [p]}, judge_questions({p["id"]: p["id"]}, []))
           for p in state["passages"]]
    listed = set(state["complete_passages"])
    coverage = {n: q for n, q in old.items() if q["decision"] == "coverage"}
    if listed and coverage:
        out.append(("judge", {**context, "passages": [p for p in state["passages"] if p["id"] in listed],
                              "complete_passages": state["complete_passages"]}, coverage))
    return out


def direct(case: dict) -> list[tuple[str, dict, dict]]:
    """B: the same requests, with coverage, conflict and source asked literally."""

    out = []
    for kind, state, old in policy.requests(case):
        questions = {}
        for name, q in old.items():
            if q["decision"] == "source":
                questions[name] = noul("source", q["candidate"], SOURCE.format(
                    source=q["candidate"], description=DESCRIBED[q["candidate"]]))
            elif q["decision"] == "coverage":
                text = next(r["text"] for r in state["requirements"] if r["id"] == q["candidate"])
                questions[name] = noul("coverage", q["candidate"], COVERAGE.format(id=q["candidate"], text=text))
            elif q["decision"] == "conflict":
                pid = name.split("_", 1)[1]
                questions[f"conflictquery_{pid}"] = noul("conflict", q["candidate"], CONFLICT_QUERY.format(id=pid))
                questions[f"conflictother_{pid}"] = noul("conflict", q["candidate"], CONFLICT_OTHER.format(id=pid))
            else:
                questions[name] = q
        out.append((kind, state, questions))
    return out


def combined(answers: dict) -> dict:
    """B's conflict: the passage contradicts the query, and not only another passage."""

    out = {n: v for n, v in answers.items() if not n.startswith(("conflictquery_", "conflictother_"))}
    for name in answers:
        if name.startswith("conflictquery_"):
            pid = name.split("_", 1)[1]
            out[f"conflict_{pid}"] = answers[name] * (1 - answers.get(f"conflictother_{pid}", 0.0))
    return out


def order(case: dict) -> list[tuple[str, dict, dict]]:
    """C: only the repair, asked once in each option order; defer stays last."""

    if not (got := judged(case)) or "repair" not in got[1]:
        return []
    state, old = got
    criteria = old["repair"]["question"]["criteria"]
    named = [n for n in criteria if n != decision.DEFER]
    flipped = {n: criteria[n] for n in [*reversed(named), decision.DEFER]}
    return [("judge", state, {"repair": old["repair"]}),
            ("judge", state, {"repairflipped": {**old["repair"], "question": decision.choice(PROMPTS["repair"],
                                                                                              flipped)}})]


def agreed(answers: dict) -> dict:
    """C's repair: the mean of both orders, deferred when their choices differ."""

    a, b = answers.pop("repair", None), answers.pop("repairflipped", None)
    if a is None or b is None:
        return answers
    probabilities = {n: round((a["probabilities"].get(n, 0) + b["probabilities"].get(n, 0)) / 2, 4)
                     for n in a["probabilities"]}
    same = a["choice"] == b["choice"]
    return {**answers, "repair": {"choice": a["choice"] if same else decision.DEFER,
                                  "confidence": min(a["confidence"], b["confidence"]) if same else 0.0,
                                  "probabilities": probabilities}}


def plain(intent: dict, wording: dict) -> list[tuple[str, dict, dict]]:
    """D: the route request's analysis and ask questions alone, in `wording`."""

    segments = intent["route_segments"]
    parts = {f"r{i}": s["text"] for i, s in enumerate(segments)} if len(segments) > 1 else {}
    state = {"query": intent["variants"]["en"], "current_state": "", **({"query_parts": parts} if parts else {})}
    return [("route", state, {"analysis": noul("analysis", None, wording["analysis"]),
                              **{f"ask_{rid}": noul("ask", rid, wording["ask"].format(id=rid)) for rid in parts}})]


def plain_labelled(intents: list[dict], answers: dict) -> dict[str, list[tuple[float, bool]]]:
    out: dict[str, list] = {"ask": [], "analysis": []}
    for intent in intents:
        got = answers[intent["id"]]
        out["analysis"].append((got["analysis"], intent["analysis"]))
        segments = intent["route_segments"]
        for i, s in enumerate(segments if len(segments) > 1 else []):
            out["ask"].append((got[f"ask_r{i}"], s["ask"]))
    return out


def fitted(pairs: dict[str, list], costs: dict) -> dict:
    """Each kind's lowest-cost rule and what it leaves, `costs` added to the fit's own."""

    with patch.dict(policy.COSTS, costs):
        return {kind: policy.fit(kind, got)[1] for kind, got in pairs.items() if got}


def compared(old: dict, new: dict, costs: dict) -> dict:
    """Old against new per kind, and whether the arm is adopted."""

    worst = {k: max(v for by in costs[k].values() for v in by.values()) for k in old}
    kinds = {k: {"old": old[k].get("cost"), "new": new.get(k, {}).get("cost"),
                 "old_counts": old[k].get("counts"), "new_counts": new.get(k, {}).get("counts")} for k in old}
    total_old = sum(v["old"] or 0 for v in kinds.values())
    total_new = sum(v["new"] or 0 for v in kinds.values())
    rose = [k for k, v in kinds.items() if v["old"] is not None and v["new"] is not None
            and v["new"] - v["old"] > worst[k]]
    return {"kinds": kinds, "cost_old": total_old, "cost_new": total_new, "risen": rose,
            "adopted": total_new < total_old and not rose}


def asked(cfg: decision.Config, items: list[tuple[str, list]], limit: int, stage: str) -> dict:
    """Every item's requests asked live, stopping before `limit` Jev tokens."""

    budget = Budget(seconds=3600.0, calls=sum(len(r) for _, r in items) + 1, candidates=0)
    trace: list[dict] = []
    answers: dict[str, dict] = {}
    started, spent = time.monotonic(), 0
    for key, reqs in items:
        for kind, state, questions in reqs:
            if spent >= limit:
                raise SystemExit(f"stopped at {spent} Jev tokens, before {key}; raise --tokens to go on")
            got = decision.evaluate(cfg, state, {n: q["question"] for n, q in questions.items()}, trace, budget,
                                    f"{stage}:{kind}")
            answers.setdefault(key, {}).update(got)
            usage = trace[-1].get("usage") or {}
            spent += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    usage = [t.get("usage") or {} for t in trace]
    return {"answers": answers, "model": trace[-1]["model"] if trace else cfg.model,
            "costs": {"requests": len(trace), "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                      "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
                      "elapsed_ms": round((time.monotonic() - started) * 1000)}}


BUILD = {"view": view, "direct": direct, "order": order}
AFTER = {"view": lambda a: a, "direct": combined, "order": agreed}
# The kinds an arm changes: only their new answers replace the recorded ones, so
# a resampled answer to an unchanged question is not credited to the arm.
KINDS = {"view": ("useful", "conflict", "redirect", "coverage"), "direct": ("coverage", "conflict", "source"),
         "order": ("repair",)}


def merged(arm: str, old: dict, new: dict) -> dict:
    """The recorded answers with the arm's own kinds replaced by its new ones."""

    mine = {n: v for n, v in AFTER[arm](dict(new)).items() if n.split("_", 1)[0] in KINDS[arm]}
    return {**old, **mine}


def gate_requests(arm: str, row: dict) -> list[tuple[str, dict, dict]]:
    """Coverage on one gate state, as `view` or `direct` asks it."""

    state, names = row["state"], list(row["expected"])
    rids = [n.split("_", 1)[1] for n in names]
    if arm == "view":
        listed = set(state.get("complete_passages") or [])
        return [("judge", {"query": state["query"], "requirements": state["requirements"],
                           "passages": [p for p in state["passages"] if p["id"] in listed],
                           "complete_passages": state.get("complete_passages") or []},
                 {n: q for n, q in judge_questions({}, rids).items()})]
    text = {r["id"]: r["text"] for r in state["requirements"]}
    return [("judge", state, {f"coverage_{r}": noul("coverage", r, COVERAGE.format(id=r, text=text[r]))
                              for r in rids})]


def gate_compared(rows: list[dict], old: dict, new: dict) -> dict:
    """Accuracy at 0.5 on the gate questions both sides answered, and per class."""

    pairs = [(row["expected"][n], old[(h, n)], new.get(h, {}).get(n))
             for row in rows for h in [row["provenance"]["state_hash"]] for n in row["expected"] if (h, n) in old]
    pairs = [p for p in pairs if p[2] is not None]

    def rate(side, label=None):
        got = [(e, s[side]) for e, *s in pairs if label is None or e is label]
        return f"{sum((p >= 0.5) == e for e, p in got)}/{len(got)}"

    return {"questions": len(pairs), **{f"{side}_{c}": rate(i, y) for i, side in enumerate(("old", "new"))
                                         for c, y in (("all", None), ("yes", True), ("no", False))}}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python tool/eval/arms.py", description="Stage 11: one Jev question arm")
    p.add_argument("arm", choices=ARMS)
    p.add_argument("--collect", action="store_true", help="ask Jev live (paid; needs the owner's approval)")
    p.add_argument("--answers", type=Path, help="a saved arms record to compare again, nothing sent")
    p.add_argument("--gate", type=Path, help="labelled coverage gate rows (laya/data/coverage/gate.jsonl)")
    p.add_argument("--gate-old", type=Path, help="a laya/jev_gate.py result holding Jev's recorded answers")
    p.add_argument("--tokens", type=int, default=250_000, help="stop before passing this many Jev tokens")
    a = p.parse_args(argv)
    if a.collect == bool(a.answers):
        p.error("give --collect or --answers")
    if a.gate and (a.arm not in ("view", "direct") or not a.gate_old):
        p.error("--gate is for view and direct, with --gate-old")
    source = INTENTS if a.arm == "plain" else policy.DATASET
    dataset = json.loads(policy.DATASET.read_text(encoding="utf-8"))
    intents = [i for i in json.loads(INTENTS.read_text(encoding="utf-8"))["intents"] if i["split"] == "calibration"]
    recorded = {(r["state_hash"], r["question"]): r["jev"]
                for r in json.loads(a.gate_old.read_text(encoding="utf-8"))["records"]
                if r["jev"] is not None} if a.gate else {}
    # Only the gate questions with a recorded answer are compared, so only they are asked.
    rows = [{**r, "expected": {n: e for n, e in r["expected"].items() if (r["provenance"]["state_hash"], n)
                               in recorded}}
            for r in map(json.loads, a.gate.read_text(encoding="utf-8").splitlines() if a.gate else [])]
    rows = [r for r in rows if r["expected"]]
    if a.collect:
        cfg = decision.config()
        if cfg.mode == "off" or not cfg.key:
            p.error(f"Jev is not configured: {cfg.status()}")
        if a.arm == "plain":
            record = {side: asked(cfg, [(i["id"], plain(i, wording)) for i in intents], a.tokens // 2,
                                  f"arms:plain:{side}") for side, wording in (("old", OLD_PLAIN), ("new", PLAIN))}
        else:
            # One allowance over both sets: case ids and state hashes never collide.
            got = asked(cfg, [(c["id"], BUILD[a.arm](c)) for c in dataset["cases"]] +
                        [(r["provenance"]["state_hash"], gate_requests(a.arm, r)) for r in rows], a.tokens,
                        f"arms:{a.arm}")
            hashes = {r["provenance"]["state_hash"] for r in rows}
            record = {"new": {**got, "answers": {k: v for k, v in got["answers"].items() if k not in hashes}},
                      **({"gate": {"answers": {k: v for k, v in got["answers"].items() if k in hashes}}}
                         if rows else {})}
        record = {"schema": "jev-arm/1", "arm": a.arm, "dataset_sha256": policy.sha(source.read_bytes()),
                  "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **record}
        RAW.mkdir(parents=True, exist_ok=True)
        out = RAW / f"arms-{a.arm}-{time.strftime('%Y%m%dT%H%M%S')}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("->", out)
    else:
        record = json.loads(a.answers.read_text(encoding="utf-8"))
        if record.get("arm") != a.arm or record.get("dataset_sha256") != policy.sha(source.read_bytes()):
            p.error("the record is for another arm or another version of its cases; collect again")
    if a.arm == "plain":
        result = compared(*(fitted(plain_labelled(intents, record[s]["answers"]), COSTS) for s in ("old", "new")),
                          COSTS)
    else:
        old = json.loads(policy.SCORE_FILE.read_text(encoding="utf-8"))["answers"]
        new = {c["id"]: merged(a.arm, old[c["id"]], record["new"]["answers"].get(c["id"], {}))
               for c in dataset["cases"]}
        if a.arm == "order":
            sides = [{"repair": policy.fit_choice(policy.chosen(dataset, s))[1]} for s in (old, new)]
            result = compared(*sides, {"repair": {None: policy.CHOICE_COSTS}})
        else:
            result = compared(*(fitted(policy.labelled(dataset, s), {}) for s in (old, new)), policy.COSTS)
        if rows and "gate" in record:
            result["gate"] = gate_compared(rows, recorded, record["gate"]["answers"])
    print(json.dumps({"arm": a.arm, **result}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
