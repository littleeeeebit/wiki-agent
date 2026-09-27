"""`python tool/eval/policy.py [--collect] [--dataset eval/jev/calibration.json] [--scores <file>] [--out <file>]`

Fit the decision workflow's per-decision thresholds (stage 6 of
`docs/plans/jev/`) on the labelled calibration split, and write the policy
artifact `decision.policy` reads (`eval/jev/policy.json`).

`--collect` asks Jev each case as the workflow asks it — a route request,
then one judge request over the case's passages, with the workflow's own
questions (`main.knowledge.route_questions`, `judge_questions`) — and saves
the scores (`eval/jev/calibration.scores.json`). Without it, the saved scores
are fitted again and nothing is sent.

Each Noul kind gets the `no`/`yes` pair, on a 0.05 grid, with the lowest
cost on its labels. The costs put first what the plan puts first: a skipped
retrieval that was needed, a useful passage dropped, a requirement called
covered that is not. A kind with too few labels of either value keeps its
provisional rule, and says so. The repair Choice gets the confidence and
top-two margin, on a 0.1 grid, with the lowest cost on the cases that label
the right repair: an accepted wrong repair spends a round on it, a deferred
one leaves the round to code's order. The relation Choice is stage 7's and
stays provisional. What is written is an error rate on this split — not a
claim that the model is calibrated on this wiki; stage 10 evaluates
held-out data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common.budget import Budget  # noqa: E402
from main.knowledge import (DESCRIBED, PROMPT_VERSION, PROMPTS, REPAIR_ORDER, REPAIRS,  # noqa: E402
                            judge_questions, route_questions)
from search import HUB  # noqa: E402

SCHEMA = "jev-policy/1"
SCORES = "jev-calibration-scores/1"
DATASET = HUB / "eval" / "jev" / "calibration.json"
SCORE_FILE = HUB / "eval" / "jev" / "calibration.scores.json"
ARTIFACT = HUB / "eval" / "jev" / "policy.json"
SOURCES = ["hub", "documents", "memory"]
# English written as English: the split is what Jev reads after normalization.
NORMALIZATION = "original_english"
GRID_NO = [round(0.05 * i, 2) for i in range(1, 9)]       # 0.05 .. 0.40
GRID_YES = [round(0.05 * i, 2) for i in range(12, 20)]    # 0.60 .. 0.95
GRID_CONFIDENCE = [round(0.1 * i, 1) for i in range(3, 10)]  # 0.3 .. 0.9
GRID_MARGIN = [round(0.1 * i, 1) for i in range(0, 6)]      # 0.0 .. 0.5
# Fewest labels of each value a kind needs before its rule is fitted.
LEAST = 3
# Fewest labelled repair cases before the repair Choice is fitted.
LEAST_CHOICE = 6
# A repair accepted from Jev: right costs nothing, wrong spends a round on it.
# Left to code, the round goes to code's first option: nothing if that is
# the right one, else the round it takes to get there.
CHOICE_COSTS = {"accepted_right": 0, "accepted_wrong": 3, "deferred_right": 0, "deferred_wrong": 1}

# What each verdict costs against each label: `{label: {verdict: cost}}`.
COSTS = {
    # A needed retrieval skipped is the worst; an unneeded one costs a search.
    "route": {True: {"no": 10, "uncertain": 0, "yes": 0}, False: {"no": 0, "uncertain": 1, "yes": 1}},
    "source": {True: {"no": 5, "uncertain": 0, "yes": 0}, False: {"no": 0, "uncertain": 0.5, "yes": 1}},
    # A supporting passage dropped is lost evidence; noise kept costs reading.
    "useful": {True: {"no": 10, "uncertain": 0.5, "yes": 0}, False: {"no": 0, "uncertain": 0.5, "yes": 1}},
    # Premature sufficiency ends the search with a requirement unmet.
    "coverage": {True: {"no": 1, "uncertain": 1, "yes": 0}, False: {"no": 0, "uncertain": 0, "yes": 10}},
    "conflict": {True: {"no": 5, "uncertain": 0, "yes": 0}, False: {"no": 0, "uncertain": 0.5, "yes": 1}},
    "redirect": {True: {"no": 5, "uncertain": 0, "yes": 0}, False: {"no": 0, "uncertain": 0.5, "yes": 1}},
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def requests(case: dict) -> list[tuple[str, dict, dict]]:
    """`(kind, state, questions)` for a case, shaped as the workflow sends them."""

    context = {"query": case["query"], "current_state": case["current_state"]}
    out = [("route", {**context, "available_sources": {s: DESCRIBED[s] for s in SOURCES}}, route_questions(SOURCES))]
    passages = case.get("passages") or []
    if passages:
        ids = [f"p{i}" for i in range(len(passages))]
        reqs = [{"id": f"r{i}", "text": r["text"]} for i, r in enumerate(case["requirements"])]
        # A passage read only in part is marked, and never counts toward coverage.
        state = {**context, "requirements": reqs,
                 "passages": [{"id": pid, "heading": p["heading"], "text": p["text"],
                               **({"coverage": p["coverage"]} if p.get("coverage") else {})}
                              for pid, p in zip(ids, passages)],
                 "complete_passages": [pid for pid, p in zip(ids, passages) if not p.get("coverage")]}
        questions = judge_questions({pid: pid for pid in ids}, [r["id"] for r in reqs])
        if case.get("repair"):
            questions["repair"] = {"decision": "repair", "candidate": None,
                                   "question": decision.choice(PROMPTS["repair"], repair_options(case["repair"]))}
        out.append(("judge", state, questions))
    return out


def repair_options(repair: dict) -> dict[str, str]:
    """The repairs a case offers, described as the workflow describes them, and defer."""

    described = {n: REPAIRS[n].format(rest=", ".join(repair.get("rest", []))) for n in repair["options"]}
    return {**described, decision.DEFER: "No step clearly helps; stop here."}


def collect(dataset: dict, cfg: decision.Config) -> dict:
    """Every case asked live; the scores with what they cost."""

    budget = Budget(seconds=900.0, calls=2 * len(dataset["cases"]), candidates=0)
    trace: list[dict] = []
    answers: dict[str, dict] = {}
    started = time.monotonic()
    for case in dataset["cases"]:
        for kind, state, questions in requests(case):
            got = decision.evaluate(cfg, state, {n: q["question"] for n, q in questions.items()}, trace, budget,
                                    f"calibration:{kind}")
            answers.setdefault(case["id"], {}).update(got)
    usage = [t.get("usage") or {} for t in trace]
    return {"schema": SCORES, "model": trace[-1]["model"] if trace else cfg.model, "prompt_version": PROMPT_VERSION,
            "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "answers": answers,
            "costs": {"requests": len(trace), "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                      "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
                      "elapsed_ms": round((time.monotonic() - started) * 1000)}}


def labelled(dataset: dict, answers: dict) -> dict[str, list[tuple[float, bool]]]:
    """`{kind: [(score, label)]}` for every question a case labels."""

    out: dict[str, list] = {kind: [] for kind in COSTS}
    for case in dataset["cases"]:
        got = answers[case["id"]]
        out["route"].append((got["retrieve"], case["retrieve"]))
        for source, label in (case.get("sources") or {}).items():
            out["source"].append((got[f"source_{source}"], label))
        for i, p in enumerate(case.get("passages") or []):
            for kind in ("useful", "conflict", "redirect"):
                out[kind].append((got[f"{kind}_p{i}"], p[kind]))
        for i, r in enumerate(case.get("requirements") or [] if case.get("passages") else []):
            out["coverage"].append((got[f"coverage_r{i}"], r["covered"]))
    return out


def verdict(score: float, no: float, yes: float) -> str:
    return "no" if score <= no else "yes" if score >= yes else "uncertain"


def fit(kind: str, pairs: list[tuple[float, bool]]) -> tuple[dict | None, dict]:
    """The lowest-cost rule for `kind`, the provisional one breaking ties by
    nearness; `None` with too few labels. And the errors it leaves."""

    positives = sum(label for _s, label in pairs)
    report = {"n": len(pairs), "positives": positives}
    if positives < LEAST or len(pairs) - positives < LEAST:
        return None, {**report, "fitted": False, "why": f"fewer than {LEAST} labels of each value"}
    best = None
    for no in GRID_NO:
        for yes in GRID_YES:
            cost = sum(COSTS[kind][label][verdict(s, no, yes)] for s, label in pairs)
            key = (cost, abs(no - 0.2) + abs(yes - 0.8))
            if best is None or key < best[0]:
                best = (key, {"no": no, "yes": yes})
    rule = best[1]
    counts: dict[str, int] = {}
    for s, label in pairs:
        name = f"{'positive' if label else 'negative'}_{verdict(s, rule['no'], rule['yes'])}"
        counts[name] = counts.get(name, 0) + 1
    provisional = sum(COSTS[kind][label][verdict(s, 0.2, 0.8)] for s, label in pairs)
    return rule, {**report, "fitted": True, "cost": best[0][0], "provisional_cost": provisional, "counts": counts}


def chosen(dataset: dict, answers: dict) -> list[tuple[dict, str, str]]:
    """`(answer, right repair, code's first option)` for every case labelling a repair."""

    return [(answers[c["id"]]["repair"], c["repair"]["choice"],
             next(n for n in REPAIR_ORDER if n in c["repair"]["options"]))
            for c in dataset["cases"] if c.get("repair")]


def choice_outcome(answer: dict, right: str, first: str, confidence: float, margin: float) -> str:
    # The workflow's own verdict, so the fit accepts exactly what a run would.
    rule = decision.Policy("fit", {"repair": {"confidence": confidence, "margin": margin}})
    if decision.verdict(rule, "repair", answer) == "yes":
        return "accepted_right" if answer["choice"] == right else "accepted_wrong"
    return "deferred_right" if first == right else "deferred_wrong"


def fit_choice(cases: list[tuple[dict, str, str]]) -> tuple[dict | None, dict]:
    """The lowest-cost confidence and margin for the repair Choice, the
    provisional rule breaking ties by nearness; `None` with too few cases."""

    report = {"n": len(cases)}
    if len(cases) < LEAST_CHOICE:
        return None, {**report, "fitted": False, "why": f"fewer than {LEAST_CHOICE} labelled repairs"}
    best = None
    for confidence in GRID_CONFIDENCE:
        for margin in GRID_MARGIN:
            cost = sum(CHOICE_COSTS[choice_outcome(a, r, f, confidence, margin)] for a, r, f in cases)
            key = (cost, round(abs(confidence - 0.6) + abs(margin - 0.2), 2))
            if best is None or key < best[0]:
                best = (key, {"confidence": confidence, "margin": margin})
    rule = best[1]
    counts: dict[str, int] = {}
    for a, r, f in cases:
        name = choice_outcome(a, r, f, rule["confidence"], rule["margin"])
        counts[name] = counts.get(name, 0) + 1
    provisional = sum(CHOICE_COSTS[choice_outcome(a, r, f, 0.6, 0.2)] for a, r, f in cases)
    return rule, {**report, "fitted": True, "cost": best[0][0], "provisional_cost": provisional, "counts": counts}


def artifact(dataset_path: Path, dataset: dict, scores: dict) -> dict:
    if scores.get("prompt_version") != PROMPT_VERSION:
        raise ValueError("the scores were asked with other prompts; collect again")
    missing = [c["id"] for c in dataset["cases"] if c["id"] not in scores["answers"]]
    if missing:
        raise ValueError(f"no scores for {missing}; collect again")
    rules, errors = {}, {}
    for kind, pairs in labelled(dataset, scores["answers"]).items():
        rule, errors[kind] = fit(kind, pairs)
        if rule:
            rules[kind] = rule
    rule, errors["repair"] = fit_choice(chosen(dataset, scores["answers"]))
    if rule:
        rules["repair"] = rule
    return {"schema": SCHEMA, "model": scores["model"], "prompt_version": PROMPT_VERSION,
            "normalization_version": NORMALIZATION,
            "dataset": {"path": dataset_path.relative_to(HUB).as_posix(), "sha256": sha(dataset_path.read_bytes()),
                        "split": dataset["split"], "cases": len(dataset["cases"])},
            "rules": rules, "errors": errors, "costs": scores["costs"],
            "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "note": "Error rates on the calibration split only; not a calibration claim. The relation Choice "
                    "(stage 7) and kinds without enough labels stay provisional. Stage 10 holds the held-out "
                    "evaluation."}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/policy.py", description="Fit the Jev decision policy")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--scores", type=Path, default=SCORE_FILE)
    parser.add_argument("--out", type=Path, default=ARTIFACT)
    parser.add_argument("--collect", action="store_true", help="ask Jev live and save the scores first")
    args = parser.parse_args(argv)
    dataset_path = args.dataset.resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if args.collect:
        cfg = decision.config()
        if cfg.mode == "off" or not cfg.key:
            parser.error(f"Jev is not configured: {cfg.status()}")
        scores = {**collect(dataset, cfg), "dataset_sha256": sha(dataset_path.read_bytes())}
        args.scores.write_text(json.dumps(scores, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    scores = json.loads(args.scores.read_text(encoding="utf-8"))
    if scores.get("dataset_sha256") != sha(dataset_path.read_bytes()):
        parser.error("the scores are for another version of the dataset; collect again")
    out = artifact(dataset_path, dataset, scores)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("model", "rules", "errors", "costs")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
