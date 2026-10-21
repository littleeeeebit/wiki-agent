"""`python tool/eval/arms.py <arm> [--collect] [--gate <rows> --gate-old <result>] [--tokens N]`

Stage 11 of `docs/plans/jev/`: ask Jev the way its documentation says it
works, one arm at a time, and compare old against new on the same labelled
cases.

- `view` (A): each passage in its own request, holding only that passage,
  the query and the requirements; coverage over only its listed passages.
- `direct` (B): coverage quotes the requirement; conflict is two literal
  questions combined in code; source names the concrete condition.
- `order` (C): repair's options flipped; an answer that changes with the
  order defers.
- `plain` (D): ask and analysis without stacked negations.

The old side of `view`, `direct` and `order` is the recorded calibration
scores (`eval/jev/calibration.scores.json`); only the new arm is asked, in
exactly the requests the recorded side was asked with (`baseline`) but for
the arm's own change, and only the arm's own kinds replace recorded answers
(`merged`). `order` asks the same judge request again with the repair's
options flipped, and combines it with the recorded answer in the old order.
`plain`'s kinds are labelled only in the v7 intents
(`eval/jev/reliability-v8/intents.json`, their calibration split), never
asked alone before, so both sides are asked there, in the same reduced
route request. Each kind is fitted on both sides (`eval.policy.fit`; ask
over each intent's parts together, as `Flow.route` reads them); the arm is
adopted when its total cost falls and no kind's rises by more than one
case's worst cost. With `--gate`, coverage is also asked on the labelled
coverage gate states (Laya stage 6) against Jev's answers recorded there
(`--gate-old`, a `laya/jev_gate.py` result); there coverage is asked alone,
so `direct`'s gate numbers carry part of `view`'s change too and are a
diagnostic, the calibration split the measure.

`--collect` appends each outcome to `raw/eval/jev/arms.journal.jsonl`, one
journal for every arm, as it arrives, failures with what they cost, so a
stop or a failure keeps what was paid for and a rerun asks only the rest.
`--tokens` caps the stage's total, every arm's included: a request is sent
only when its reservation (`bound`) fits in what is left. Jev takes no
output limit, so the reservation's output part is an estimate. A response
past it, or one sent with no usable count, halts every arm until the owner
accepts that halt (`--accept-halts`).
The record goes to `raw/eval/jev/arms-<arm>-<timestamp>.json`; an
incomplete one is never compared.

B was adopted (step 3): production asks its questions, and the recorded
scores are B's. An arm built or compared now is measured against B; the
records of 2026-10-19 were compared against the scores at fc5c8f1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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
# One journal for every arm: the approved allowance is the stage's, not an arm's.
JOURNAL = RAW / "arms.journal.jsonl"

# B, adopted into production's PROMPTS (stage 11 step 3). The requirement's own words, not its id
# (the page: name the relevant parts of state); the old conflict question's negated exception
# became its own literal question, which code combines.
COVERAGE, CONFLICT_QUERY, CONFLICT_OTHER, SOURCE = (PROMPTS[k] for k in ("coverage", "conflictquery",
                                                                          "conflictother", "source"))
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
ANALYSIS_COSTS = policy.ANALYSIS_COSTS
# Ask's cost is a whole intent's (`ask_cost`): a question dropped as material is never answered.
DROPPED, KEPT_MATERIAL = 10, 1
# The worst one case can cost, per kind: how far a kind may rise and the arm still be adopted.
WORST = {**{k: max(v for by in c.values() for v in by.values()) for k, c in policy.COSTS.items()},
         "repair": max(policy.CHOICE_COSTS.values()), "ask": DROPPED, "analysis": 10}
# A request's bound before anything was answered: tokens beyond its bytes (Jev's own
# instructions, the same in every request) and output per question. The recorded side
# spent 126,572 input tokens over 172 requests, so those instructions cost at most 736
# a request; output averaged 116 a request. Raised to the largest seen as answers arrive.
SEED_OVERHEAD, SEED_OUTPUT = 4_000, 200
FLIPPED = "flipped:"


def noul(kind: str, candidate, text: str) -> dict:
    return {"decision": kind, "candidate": candidate, "question": decision.noul(text)}


def baseline(case: dict, names) -> list[tuple[str, dict, dict]]:
    """The case's requests holding only the questions the recorded side was asked (`names`):
    the route gained analysis and progress after the calibration scores were collected."""

    out = [(kind, state, {n: q for n, q in questions.items() if n in names})
           for kind, state, questions in policy.requests(case)]
    return [r for r in out if r[2]]


def judged(case: dict, names) -> tuple[dict, dict] | None:
    return next(((s, q) for kind, s, q in baseline(case, names) if kind == "judge"), None)


def view(case: dict, names) -> list[tuple]:
    """A: the judge request's passage and coverage questions, each reading only what it needs."""

    if not (got := judged(case, names)):
        return []
    state, old = got
    context = {"query": state["query"], "requirements": state["requirements"]}
    out = [("judge", {**context, "passages": [p]}, judge_questions({p["id"]: p["id"]}, {}))
           for p in state["passages"]]
    listed = set(state["complete_passages"])
    coverage = {n: q for n, q in old.items() if q["decision"] == "coverage"}
    if listed and coverage:
        out.append(("judge", {**context, "passages": [p for p in state["passages"] if p["id"] in listed],
                              "complete_passages": state["complete_passages"]}, coverage))
    return out


def direct(case: dict, names) -> list[tuple]:
    """B: the recorded requests, with coverage, conflict and source asked literally."""

    out = []
    for kind, state, old in baseline(case, names):
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


def combined(new: dict, old: dict) -> dict:
    """B's conflict: the passage contradicts the query, and not only another passage."""

    out = {n: v for n, v in new.items() if not n.startswith(("conflictquery_", "conflictother_"))}
    for name in new:
        if name.startswith("conflictquery_"):
            pid = name.split("_", 1)[1]
            out[f"conflict_{pid}"] = new[name] * (1 - new.get(f"conflictother_{pid}", 0.0))
    return out


def order(case: dict, names) -> list[tuple]:
    """C: the recorded judge request again, its repair's options in the other order (defer
    stays last), answered under `FLIPPED` names; the old order's answer is the recorded one."""

    if not (got := judged(case, names)) or "repair" not in got[1]:
        return []
    state, old = got
    criteria = old["repair"]["question"]["criteria"]
    named = [n for n in criteria if n != decision.DEFER]
    flipped = {n: criteria[n] for n in [*reversed(named), decision.DEFER]}
    repair = {**old["repair"], "question": decision.choice(PROMPTS["repair"], flipped)}
    return [("judge", state, {**old, "repair": repair}, FLIPPED)]


def agreed(new: dict, old: dict) -> dict:
    """C's repair: the mean of both orders over every option either named, deferred when
    their choices differ."""

    a, b = old.get("repair"), new.get(FLIPPED + "repair")
    if a is None or b is None:
        return {}
    probabilities = {n: round((a["probabilities"].get(n, 0) + b["probabilities"].get(n, 0)) / 2, 4)
                     for n in {**a["probabilities"], **b["probabilities"]}}
    same = a["choice"] == b["choice"]
    return {"repair": {"choice": a["choice"] if same else decision.DEFER,
                       "confidence": min(a["confidence"], b["confidence"]) if same else 0.0,
                       "probabilities": probabilities}}


def plain(intent: dict, wording: dict) -> list[tuple[str, dict, dict]]:
    """D: the route request's analysis and ask questions alone, in `wording`."""

    segments = intent["route_segments"]
    parts = {f"r{i}": s["text"] for i, s in enumerate(segments)} if len(segments) > 1 else {}
    state = {"query": intent["variants"]["en"], "current_state": "", **({"query_parts": parts} if parts else {})}
    return [("route", state, {"analysis": noul("analysis", None, wording["analysis"]),
                              **{f"ask_{rid}": noul("ask", rid, wording["ask"].format(id=rid)) for rid in parts}})]


def plain_labelled(intents: list[dict], answers: dict) -> tuple[list[tuple[float, bool]], list[list]]:
    """Analysis's `(score, label)` per intent, and ask's per intent in several parts: its
    parts' `(score, label)` together, as the route reads them."""

    analysis, ask = [], []
    for intent in intents:
        got = answers[intent["id"]]
        analysis.append((got["analysis"], intent["analysis"]))
        segments = intent["route_segments"]
        if len(segments) > 1:
            ask.append([(got[f"ask_r{i}"], s["ask"]) for i, s in enumerate(segments)])
    return analysis, ask


def ask_cost(parts: list[tuple[float, bool]], no: float, yes: float) -> tuple[int, int]:
    """`(questions dropped, material kept)` when `Flow.route` reads these parts' verdicts:
    beside a sure ask an uncertain part is material, with none sure it stays, and should
    every part go, none does."""

    said = [policy.verdict(s, no, yes) for s, _ in parts]
    sure = "yes" in said
    asked = [v == "yes" or (not sure and v != "no") for v in said]
    kept = asked if any(asked) and not all(asked) else [True] * len(parts)
    return (sum(label and not k for (_, label), k in zip(parts, kept)),
            sum(k and not label for (_, label), k in zip(parts, kept)))


def fit_ask(groups: list[list]) -> dict:
    """Ask's lowest-cost rule over whole intents, the provisional one breaking ties."""

    pairs = [p for g in groups for p in g]
    positives = sum(label for _, label in pairs)
    report = {"n": len(pairs), "intents": len(groups), "positives": positives}
    if positives < policy.LEAST or len(pairs) - positives < policy.LEAST:
        return {**report, "fitted": False, "why": f"fewer than {policy.LEAST} labels of each value"}

    def tally(no, yes):
        dropped = kept = 0
        for g in groups:
            d, k = ask_cost(g, no, yes)
            dropped, kept = dropped + d, kept + k
        return dropped * DROPPED + kept * KEPT_MATERIAL, {"dropped": dropped, "kept_material": kept}

    no, yes = min(((n, y) for n in policy.GRID_NO for y in policy.GRID_YES),
                  key=lambda r: (tally(*r)[0], abs(r[0] - 0.2) + abs(r[1] - 0.8)))
    cost, counts = tally(no, yes)
    return {**report, "fitted": True, "rule": {"no": no, "yes": yes}, "cost": cost, "counts": counts}


def fitted(pairs: dict[str, list], costs: dict | None = None) -> dict:
    """Each kind's lowest-cost rule and what it leaves, `costs` added to the fit's own."""

    with patch.dict(policy.COSTS, costs or {}):
        return {kind: policy.fit(kind, got)[1] for kind, got in pairs.items() if got}


def compared(old: dict, new: dict) -> dict:
    """Old against new per kind, and whether the arm is adopted."""

    kinds = {k: {"old": old[k].get("cost"), "new": new.get(k, {}).get("cost"),
                 "old_counts": old[k].get("counts"), "new_counts": new.get(k, {}).get("counts")} for k in old}
    total_old = sum(v["old"] or 0 for v in kinds.values())
    total_new = sum(v["new"] or 0 for v in kinds.values())
    rose = [k for k, v in kinds.items() if v["old"] is not None and v["new"] is not None
            and v["new"] - v["old"] > WORST[k]]
    return {"kinds": kinds, "cost_old": total_old, "cost_new": total_new, "risen": rose,
            "adopted": total_new < total_old and not rose}


def bound(size: int, questions: int, overhead: int, per_question: float) -> int:
    """What one request is reserved before it is sent: a token per byte of its body, Jev's
    own instructions (`overhead`), and twice the largest output per question seen. An
    estimate: Jev takes no output limit, so a response past it stops the run (`asked`)."""

    return size + overhead + int(2 * per_question * questions) + 1


def journalled(journal: Path) -> list[dict]:
    """The journal's entries. A last line cut off mid-write is dropped, and one written whole
    but for its newline gets it, so the next append starts a line of its own."""

    if not journal.exists():
        return []
    text = journal.read_text(encoding="utf-8")
    lines = text.split("\n")
    if lines[-1]:
        try:
            json.loads(lines[-1])
            journal.write_text(text + "\n", encoding="utf-8")
        except json.JSONDecodeError:
            lines[-1] = ""
            journal.write_text("\n".join(lines), encoding="utf-8")
    return [json.loads(line) for line in lines if line]


def asked(cfg: decision.Config, items: list[tuple[str, list]], limit: int, journal: Path,
          accepted: int = 0) -> dict:
    """Every item's requests, each outcome appended to `journal` as it arrives: an answer, or a
    failure with what it cost (its reported usage, else its reservation once sent). The journal
    is the whole stage's, every arm's, and `limit` its total: a request is sent only when its
    `bound` fits in what is left. A response past its reservation, or one sent with no usable
    count, halts the stage: from then no run sends anything until the owner has seen the
    spending and accepts that halt (`accepted`, the number of halts accepted). Answers already
    in the journal are read back for the same body, model included; failed requests are asked
    again."""

    paid = journalled(journal)
    done = {e["digest"]: e for e in paid if "answers" in e}
    spent = sum(e["input_tokens"] + e["output_tokens"] for e in paid)
    overhead = max([SEED_OVERHEAD] + [e["input_tokens"] - e["bytes"] for e in done.values()])
    per_question = max([SEED_OUTPUT] + [e["output_tokens"] / e["questions"] for e in done.values()])
    budget = Budget(seconds=None, calls=None, candidates=0)
    answers: dict[str, dict] = {}
    halts = sum("halt" in e for e in paid)
    stopped = halts > accepted

    def keep(entry):
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    for key, reqs in items:
        for kind, state, questions, *tag in reqs:
            asking = {n: q["question"] for n, q in questions.items()}
            body = decision.wire(cfg.model, state, asking)
            digest = hashlib.sha256(body).hexdigest()[:24]
            if digest not in done:
                most = bound(len(body), len(questions), overhead, per_question)
                if stopped or spent + most > limit:
                    stopped = True
                    continue
                trace: list[dict] = []
                base = {"digest": digest, "key": key, "kind": kind, "bytes": len(body), "questions": len(questions)}
                try:
                    got = decision.evaluate(cfg, state, asking, trace, budget, f"arms:{kind}")
                except Exception as error:
                    last = trace[-1] if trace else {}
                    usage = last.get("usage")
                    # A count the transport would refuse (`decision.evaluate`) is no count.
                    counted = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                                              for k in ("input_tokens", "output_tokens"))
                    cost = ((usage["input_tokens"], usage["output_tokens"]) if counted else
                            (most, 0) if last.get("sent") else (0, 0))
                    halt = ("sent with no usable count" if last.get("sent") and not counted else
                            f"past its reservation of {most}" if sum(cost) > most else None)
                    keep({**base, "failed": getattr(error, "category", type(error).__name__),
                          "input_tokens": cost[0], "output_tokens": cost[1], **({"halt": halt} if halt else {})})
                    raise
                usage = trace[-1]["usage"]
                over = usage["input_tokens"] + usage["output_tokens"] > most
                entry = {**base, "answers": got, "model": trace[-1].get("model"),
                         "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                         **({"halt": f"past its reservation of {most}"} if over else {})}
                keep(entry)
                done[digest] = entry
                spent += entry["input_tokens"] + entry["output_tokens"]
                overhead = max(overhead, entry["input_tokens"] - entry["bytes"])
                per_question = max(per_question, entry["output_tokens"] / entry["questions"])
                stopped = stopped or over   # nothing more is sent on this estimate, by any run
            prefix = tag[0] if tag else ""
            answers.setdefault(key, {}).update({prefix + n: v for n, v in done[digest]["answers"].items()})
    used = journalled(journal)
    asked_for = {e["digest"] for e in done.values()}
    return {"answers": answers, "complete": not stopped and spent <= limit,
            "model": next((e["model"] for e in reversed(used) if e.get("model")), cfg.model),
            "costs": {"requests": len(used), "answered": len(asked_for),
                      "failed": sum("failed" in e for e in used), "halts": sum("halt" in e for e in used),
                      "input_tokens": sum(e["input_tokens"] for e in used),
                      "output_tokens": sum(e["output_tokens"] for e in used), "limit": limit}}


BUILD = {"view": view, "direct": direct, "order": order}
# view's passage questions are production's, so since B's adoption its conflict comes in two parts too.
AFTER = {"view": combined, "direct": combined, "order": agreed}
# The kinds an arm changes: only their new answers replace the recorded ones, so
# a resampled answer to an unchanged question is not credited to the arm.
KINDS = {"view": ("useful", "conflict", "redirect", "coverage"), "direct": ("coverage", "conflict", "source"),
         "order": ("repair",)}


def merged(arm: str, old: dict, new: dict) -> dict:
    """The recorded answers with the arm's own kinds replaced by its new ones."""

    mine = {n: v for n, v in AFTER[arm](dict(new), old).items() if n.split("_", 1)[0] in KINDS[arm]}
    return {**old, **mine}


def gate_requests(arm: str, row: dict) -> list[tuple]:
    """Coverage on one gate state, as `view` or `direct` asks it."""

    state, names = row["state"], list(row["expected"])
    rids = [n.split("_", 1)[1] for n in names]
    text = {r["id"]: r["text"] for r in state["requirements"]}
    if arm == "view":
        listed = set(state.get("complete_passages") or [])
        return [("judge", {"query": state["query"], "requirements": state["requirements"],
                           "passages": [p for p in state["passages"] if p["id"] in listed],
                           "complete_passages": state.get("complete_passages") or []},
                 judge_questions({}, {r: text[r] for r in rids}))]
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
    p.add_argument("--tokens", type=int, default=900_000,
                   help="the stage's Jev tokens at most, every arm's journalled spending included")
    p.add_argument("--accept-halts", type=int, default=0,
                   help="halts the owner has seen and accepted; past them no run sends anything")
    a = p.parse_args(argv)
    if a.collect == bool(a.answers):
        p.error("give --collect or --answers")
    if a.gate and (a.arm not in ("view", "direct") or not a.gate_old):
        p.error("--gate is for view and direct, with --gate-old")
    source = INTENTS if a.arm == "plain" else policy.DATASET
    dataset = json.loads(policy.DATASET.read_text(encoding="utf-8"))
    old = json.loads(policy.SCORE_FILE.read_text(encoding="utf-8"))["answers"]
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
            items = [(f"{side}:{i['id']}", plain(i, wording))
                     for side, wording in (("old", OLD_PLAIN), ("new", PLAIN)) for i in intents]
        else:
            # One allowance over both sets: case ids and state hashes never collide.
            items = ([(c["id"], BUILD[a.arm](c, old[c["id"]])) for c in dataset["cases"]] +
                     [(r["provenance"]["state_hash"], gate_requests(a.arm, r)) for r in rows])
        RAW.mkdir(parents=True, exist_ok=True)
        got = asked(cfg, items, a.tokens, JOURNAL, a.accept_halts)
        answers = got.pop("answers")
        if a.arm == "plain":
            sides = {s: {"answers": {k.split(":", 1)[1]: v for k, v in answers.items() if k.startswith(s + ":")}}
                     for s in ("old", "new")}
        else:
            hashes = {r["provenance"]["state_hash"] for r in rows}
            sides = {"new": {"answers": {k: v for k, v in answers.items() if k not in hashes}},
                     **({"gate": {"answers": {k: v for k, v in answers.items() if k in hashes}}} if rows else {})}
        record = {"schema": "jev-arm/1", "arm": a.arm, "dataset_sha256": policy.sha(source.read_bytes()),
                  "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **got, **sides}
        out = RAW / f"arms-{a.arm}-{time.strftime('%Y%m%dT%H%M%S')}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("->", out)
        if not record["complete"]:
            print(f"stopped under --tokens {a.tokens}: {record['costs']}. The journal keeps what was paid for and a "
                  "rerun asks only the rest. A halt (a response past its reservation, or one with no usable count) "
                  "stops every arm until the owner, having seen the spending, accepts it with --accept-halts. "
                  "Nothing is compared.")
            return 1
    else:
        record = json.loads(a.answers.read_text(encoding="utf-8"))
        if record.get("arm") != a.arm or record.get("dataset_sha256") != policy.sha(source.read_bytes()):
            p.error("the record is for another arm or another version of its cases; collect again")
        if not record.get("complete"):
            p.error("the record is incomplete; collect again to finish it")
    if a.arm == "plain":
        result = compared(*({**fitted({"analysis": analysis}, {"analysis": ANALYSIS_COSTS}), "ask": fit_ask(ask)}
                            for analysis, ask in (plain_labelled(intents, record[s]["answers"])
                                                  for s in ("old", "new"))))
    else:
        new = {c["id"]: merged(a.arm, old[c["id"]], record["new"]["answers"].get(c["id"], {}))
               for c in dataset["cases"]}
        if a.arm == "order":
            result = compared(*({"repair": policy.fit_choice(policy.chosen(dataset, s))[1]} for s in (old, new)))
        else:
            result = compared(*(fitted(policy.labelled(dataset, s)) for s in (old, new)))
        if rows and "gate" in record:
            result["gate"] = gate_compared(rows, recorded, record["gate"]["answers"])
    print(json.dumps({"arm": a.arm, **result}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
