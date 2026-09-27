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
one leaves the round to code's order. What is written is an error rate on
this split — not a claim that the model is calibrated on this wiki; stage 10
evaluates held-out data.

`--relation` fits stage 7's relation Choice instead, on its own split
(`eval/jev/relation.json`), prompt (`decision.claims`) and artifact
(`eval/jev/relation-policy.json`): one request a case, shaped as the answer
path sends it. Accepting `supports` for a claim the passages do not state —
false acceptance, an unsupported claim published — costs ten times a
supported claim withheld; the two are counted apart. The same request asks
the answers Choice — does a claim give what a part of the question asks —
labelled in `eval/jev/answers.json`; accepting `answers` for a claim that
does not answer it calls an answer complete that is not, and costs the same.
The faithful Choice — does a claim citing nothing, a recommendation or a
direct run's text, state only what its grounds do — has its own cases
(`eval/jev/faithful.json`), one request each; accepting `faithful` for a
claim that adds a fact publishes an unchecked fact, and costs the same.
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


RELATION_DATASET = HUB / "eval" / "jev" / "relation.json"
RELATION_SCORES = HUB / "eval" / "jev" / "relation.scores.json"
RELATION_ARTIFACT = HUB / decision.claims.ARTIFACT
# What a relation verdict costs against a label. Published unsupported is the
# worst; a supported claim withheld costs a weaker answer; a withheld claim
# reported as a conflict instead of as unsupported, or back, costs little.
RELATION_COSTS = {"false_acceptance": 10, "false_rejection": 1, "mislabelled": 0.2, "right": 0}


ANSWERS_DATASET = HUB / "eval" / "jev" / "answers.json"
FAITHFUL_DATASET = HUB / "eval" / "jev" / "faithful.json"


def relation_request(case: dict, labels: dict | None = None) -> tuple[dict, dict]:
    """`(state, questions)` for a case, as `main.knowledge.Grounding.judge`
    sends them: every claim's relation, and the answers Choice for each part
    of the question `labels` (the case's `answers.json` entry) asks about."""

    labels = labels or {"requirements": [], "claims": {}}
    pairs = [(cid, rid) for cid, parts in labels["claims"].items() for rid in parts]
    state = decision.claims.state(case["question"], [{"id": p["id"], "text": p["text"]} for p in case["passages"]],
                                  [{"id": c["id"], "text": c["text"], "cites": c["cites"], "premises": []}
                                   for c in case["claims"]],
                                  labels["requirements"])
    asked = {**decision.claims.questions([c["id"] for c in case["claims"]]), **decision.claims.coverage(pairs)}
    return state, {n: q["question"] for n, q in asked.items()}


def faithful_request(case: dict) -> tuple[dict, dict]:
    """`(state, questions)` for a faithful case, as the answer path sends it:
    the relation of each claim citing a passage, and the faithful Choice of
    each labelled one."""

    state = decision.claims.state(case["question"], case["passages"],
                                  [{k: c[k] for k in ("id", "text", "cites", "premises")} for c in case["claims"]],
                                  conversation=case["conversation"])
    asked = {**decision.claims.questions([c["id"] for c in case["claims"] if c["cites"]]),
             **decision.claims.grounds([c["id"] for c in case["claims"] if "label" in c])}
    return state, {n: q["question"] for n, q in asked.items()}


def faithful_outcome(answer: dict, label: str, confidence: float, margin: float) -> str:
    rule = decision.Policy("fit", {"faithful": {"confidence": confidence, "margin": margin}})
    got = decision.claims.faithful(rule, answer)
    if got == "faithful":
        return "right" if label == "faithful" else "false_acceptance"
    if label == "faithful":
        return "false_rejection"
    # Withheld either way; `adds` read as `contradicts`, or back, changes only what the repair is told.
    return "right" if got in (label, "uncertain") else "mislabelled"


def answers_outcome(answer: dict, label: str, confidence: float, margin: float) -> str:
    rule = decision.Policy("fit", {"answers": {"confidence": confidence, "margin": margin}})
    got = decision.claims.answered(rule, answer)
    if got == "answers":
        return "right" if label == "answers" else "false_acceptance"
    if label == "answers":
        return "false_rejection"
    # Not answered either way; `partly` read as nothing, or back, only moves partial and abstained.
    return "right" if got == label or (got == "uncertain" and label == "no") else "mislabelled"


def relation_outcome(answer: dict, label: str, confidence: float, margin: float) -> str:
    rule = decision.Policy("fit", {"relation": {"confidence": confidence, "margin": margin}})
    got = decision.claims.outcome(rule, answer)
    said = {"supported": "supports", "contradicted": "contradicts", "unsupported": "insufficient"}.get(got)
    if said == "supports":
        return "right" if label == "supports" else "false_acceptance"
    if label == "supports":
        return "false_rejection"
    return "right" if said in (None, label) else "mislabelled"


def fit_relation(pairs: list[tuple[dict, str]], outcome=relation_outcome, accepted: str = "supports"
                 ) -> tuple[dict | None, dict]:
    """The lowest-cost confidence and margin for the relation Choice — or,
    with `answers_outcome` and `answers`, the answers Choice — the
    provisional rule breaking ties by nearness; `None` with too few labels."""

    supported = sum(label == accepted for _a, label in pairs)
    report = {"n": len(pairs), accepted: supported}
    if supported < LEAST or len(pairs) - supported < LEAST:
        return None, {**report, "fitted": False, "why": f"fewer than {LEAST} labels of each side"}
    best = None
    # Tighten only: a few dozen synthetic pairs, and a looser rule trades a measured false rejection for a
    # false acceptance nothing here measures (answers at 0.3 kept out a `no` chosen at 0.33 by its margin's
    # last digit). Stage 10's held-out set may loosen it.
    for confidence in (c for c in GRID_CONFIDENCE if c >= 0.6):
        for margin in (m for m in GRID_MARGIN if m >= 0.2):
            cost = sum(RELATION_COSTS[outcome(a, label, confidence, margin)] for a, label in pairs)
            key = (cost, round(abs(confidence - 0.6) + abs(margin - 0.2), 2))
            if best is None or key < best[0]:
                best = (key, {"confidence": confidence, "margin": margin})
    rule = best[1]

    def tally(confidence: float, margin: float) -> dict:
        counts = dict.fromkeys(RELATION_COSTS, 0)
        for a, label in pairs:
            counts[outcome(a, label, confidence, margin)] += 1
        return counts

    provisional = tally(0.6, 0.2)
    return rule, {**report, "fitted": True, "cost": best[0][0], "counts": tally(**rule),
                  "provisional_cost": sum(RELATION_COSTS[k] * n for k, n in provisional.items()),
                  "provisional_counts": provisional}


def relation_artifact(dataset_path: Path, dataset: dict, scores: dict) -> dict:
    if scores.get("prompt_version") != decision.claims.VERSION:
        raise ValueError("the scores were asked with another relation prompt; collect again")
    if scores.get("answers_sha256") != sha(ANSWERS_DATASET.read_bytes()):
        raise ValueError("the scores are for another version of the answers labels; collect again")
    if scores.get("faithful_sha256") != sha(FAITHFUL_DATASET.read_bytes()):
        raise ValueError("the scores are for another version of the faithful cases; collect again")
    labels = json.loads(ANSWERS_DATASET.read_text(encoding="utf-8"))["cases"]
    pairs = [(scores["answers"][case["id"]][f"relation_{c['id']}"], c["label"])
             for case in dataset["cases"] for c in case["claims"]]
    covered = [(scores["answers"][cid][f"answers_{c}_{r}"], label)
               for cid, entry in labels.items() for c, parts in entry["claims"].items() for r, label in parts.items()]
    grounded = [(scores["answers"][case["id"]][f"faithful_{c['id']}"], c["label"])
                for case in json.loads(FAITHFUL_DATASET.read_text(encoding="utf-8"))["cases"]
                for c in case["claims"] if "label" in c]
    rules, errors = {}, {}
    for kind, got in (("relation", fit_relation(pairs)),
                      ("answers", fit_relation(covered, answers_outcome, "answers")),
                      ("faithful", fit_relation(grounded, faithful_outcome, "faithful"))):
        rule, errors[kind] = got
        if rule:
            rules[kind] = rule
    return {"schema": SCHEMA, "model": scores["model"], "prompt_version": decision.claims.VERSION,
            "normalization_version": NORMALIZATION,
            "dataset": {"path": dataset_path.relative_to(HUB).as_posix(), "sha256": sha(dataset_path.read_bytes()),
                        "split": dataset["split"], "cases": len(dataset["cases"]), "claims": len(pairs),
                        "answers": {"path": ANSWERS_DATASET.relative_to(HUB).as_posix(),
                                    "sha256": sha(ANSWERS_DATASET.read_bytes()), "pairs": len(covered)},
                        "faithful": {"path": FAITHFUL_DATASET.relative_to(HUB).as_posix(),
                                     "sha256": sha(FAITHFUL_DATASET.read_bytes()), "claims": len(grounded)}},
            "rules": rules, "errors": errors, "costs": scores["costs"],
            "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "note": "Error rates on the relation calibration split only; not a calibration claim. False acceptance "
                    "(an unsupported claim accepted as supported) and false rejection are counted apart. Stage 10 "
                    "holds the held-out evaluation."}


def collect_relation(dataset: dict, cfg: decision.Config) -> dict:
    faithful = json.loads(FAITHFUL_DATASET.read_text(encoding="utf-8"))["cases"]
    budget = Budget(seconds=900.0, calls=len(dataset["cases"]) + len(faithful), candidates=0)
    trace: list[dict] = []
    answers: dict[str, dict] = {}
    labels = json.loads(ANSWERS_DATASET.read_text(encoding="utf-8"))["cases"]
    started = time.monotonic()
    asked = [(case["id"], relation_request(case, labels.get(case["id"]))) for case in dataset["cases"]]
    for case_id, (state, questions) in asked + [(case["id"], faithful_request(case)) for case in faithful]:
        answers[case_id] = decision.evaluate(cfg, state, questions, trace, budget, "calibration:relation")
    usage = [t.get("usage") or {} for t in trace]
    return {"schema": SCORES, "model": trace[-1]["model"] if trace else cfg.model,
            "prompt_version": decision.claims.VERSION, "answers_sha256": sha(ANSWERS_DATASET.read_bytes()),
            "faithful_sha256": sha(FAITHFUL_DATASET.read_bytes()),
            "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "answers": answers,
            "costs": {"requests": len(trace), "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                      "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
                      "elapsed_ms": round((time.monotonic() - started) * 1000)}}


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
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--scores", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--collect", action="store_true", help="ask Jev live and save the scores first")
    parser.add_argument("--relation", action="store_true", help="fit stage 7's relation Choice instead")
    args = parser.parse_args(argv)
    defaults = (RELATION_DATASET, RELATION_SCORES, RELATION_ARTIFACT) if args.relation else (DATASET, SCORE_FILE,
                                                                                            ARTIFACT)
    dataset_path, score_path, out_path = (given or default for given, default in
                                          zip((args.dataset, args.scores, args.out), defaults))
    dataset_path = dataset_path.resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if args.collect:
        cfg = decision.config()
        if cfg.mode == "off" or not cfg.key:
            parser.error(f"Jev is not configured: {cfg.status()}")
        scores = {**(collect_relation if args.relation else collect)(dataset, cfg),
                  "dataset_sha256": sha(dataset_path.read_bytes())}
        score_path.write_text(json.dumps(scores, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    scores = json.loads(score_path.read_text(encoding="utf-8"))
    if scores.get("dataset_sha256") != sha(dataset_path.read_bytes()):
        parser.error("the scores are for another version of the dataset; collect again")
    out = (relation_artifact if args.relation else artifact)(dataset_path, dataset, scores)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("model", "rules", "errors", "costs")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
