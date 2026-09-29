"""`python tool/eval/report.py <run-dir> [<run-dir> ...] [--out <file.json>]`

Stage 10's measurements from recorded rows (`tool/eval/compare.py`), and
each frozen gate judged on them. Sends nothing.

The dataset and gates are the ones the runs' manifest names, checked by
hash; runs whose manifests differ, or two runs of one kind (`arms/retrieval`,
`arms/answer`, `arms/repeat`, `fixed`, `actions`), are refused. A gate reads
the run it names. Runs from before the manifest read `eval/jev/intents.json`
and `eval/jev/gates.json`, as they always did.

Every rate names its denominator. Intervals are 95% percentile intervals from
resampling intents — an intent's repetitions move together, never counted
as independent evidence. A gate
reads `pass`, `fail`, `inconclusive` (the interval does not settle it, or
its cohort holds a row that cannot be scored), `not_measured` (its run is
missing or lacks a row of its cohort), or `provisional` — a pass on labels
no review is recorded for. The Markdown summary goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import compare, dataset  # noqa: E402
from search import HUB  # noqa: E402

GATES = HUB / "eval" / "jev" / "gates.json"
REPORT = "jev-evaluation-report/1"


# -- intervals --------------------------------------------------------------

def boot(groups: list, stat, resamples: int, seed: int, confidence: float) -> dict:
    """`stat(groups)` and its percentile interval over `groups` resampled with replacement."""

    point = stat(groups)
    if point is None or len(groups) < 2:
        return {"value": point, "low": None, "high": None, "n": len(groups)}
    rng = random.Random(seed)
    got = sorted(v for v in (stat([rng.choice(groups) for _ in groups]) for _ in range(resamples)) if v is not None)
    cut = (1 - confidence) / 2
    return {"value": round(point, 4), "low": round(got[int(cut * len(got))], 4),
            "high": round(got[min(len(got) - 1, int((1 - cut) * len(got)))], 4), "n": len(groups)}


def ratio(pairs: list[tuple[float, float]]) -> float | None:
    den = sum(d for _n, d in pairs)
    return sum(n for n, _d in pairs) / den if den else None


def by_intent(rows: list[dict], value) -> dict[str, tuple[float, float]]:
    """`{intent: (sum of value, rows counted)}` over rows where `value(row)` is not None."""

    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        v = value(r)
        if v is not None:
            out[r["intent"]][0] += v
            out[r["intent"]][1] += 1
    return {k: (n, d) for k, (n, d) in out.items()}


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


# -- row values ---------------------------------------------------------------

def supporting(intent: dict, label: str) -> bool:
    return any(dataset.met([alt], [label]) for g in intent["evidence"] for alt in g)


def values(row: dict, intent: dict, k: int) -> dict:
    """Each metric's value for one row; `None` where the metric does not apply."""

    evidence_intent = bool(intent["evidence"])
    jev = compare.ARMS[row["arm"]]["jev"]
    handed = row["evidence"][:k]
    recall = dataset.recall(intent, handed) if evidence_intent else None
    out = {"recall": recall,
           "candidate_recall": dataset.recall(intent, row["evidence"] + row.get("beyond_k", []))
           if evidence_intent else None,
           "bridge_recall": recall if "bridged" in intent else None,
           "route_false_exclusion": (float(bool(set(dataset.needed(intent)) - set(row["sources"])))
                                     if jev and evidence_intent and not row["direct"] else None),
           "false_rejection": (float(any(supporting(intent, x) for x in row["rejected"]))
                               if jev and evidence_intent else None),
           "premature_sufficiency": (float(row["status"] == "ready" and recall < 1)
                                     if jev and evidence_intent else None),
           "direct_when_needed": float(row["direct"]) if jev and evidence_intent else None,
           "direct_when_direct": float(row["direct"]) if jev and intent["direct"] else None,
           "fallback": (float(row["status"] in ("unavailable", "exhausted") and row["reason"] != "disabled")
                        if jev else None),
           "seconds": row["elapsed_ms"] / 1000,
           "integrity": len(row["leaks"]) + len((row.get("answer") or {}).get("fabricated") or []),
           "breaches": len(row["breaches"])}
    grade = row.get("grade")
    if grade and "error" not in grade:
        parts = grade.get("parts") or {}
        score = {"answered": 1.0, "partial": 0.5}
        if intent["abstain"]:
            out["coverage"] = float(bool(grade.get("abstained")))
        elif intent["parts"]:
            out["coverage"] = sum(score.get(parts.get(p["id"]), 0.0) for p in intent["parts"]) / len(intent["parts"])
        claims = grade.get("claims") or []
        out["claims"] = (sum(not c.get("supported") for c in claims), len(claims))
        out["forbidden"] = float(bool(grade.get("forbidden")))
        out["answer_seconds"] = (row["elapsed_ms"] + (row.get("answer") or {}).get("elapsed_ms", 0)) / 1000
    return out


# -- the report -----------------------------------------------------------------

def arms_report(rows: list[dict], data: dict, opts: dict, cfg: dict) -> dict:
    intents = {i["id"]: i for i in data["intents"]}
    B, seed, conf = cfg["resamples"], cfg["seed"], cfg["confidence"]
    first = [r for r in rows if r["rep"] == 0]
    table: dict[str, dict] = {}
    per_arm: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for r in first:
        per_arm[r["arm"]].append((r, values(r, intents[r["intent"]], opts["k"])))
    for arm, pairs in sorted(per_arm.items()):
        entry = {"rows": len(pairs)}
        for name in ("recall", "candidate_recall", "bridge_recall", "route_false_exclusion", "false_rejection",
                     "premature_sufficiency", "direct_when_needed", "direct_when_direct", "fallback", "coverage",
                     "forbidden"):
            counted = by_intent([{"intent": r["intent"], "v": v.get(name)} for r, v in pairs], lambda x: x["v"])
            if counted:
                entry[name] = {**boot(list(counted.values()), ratio, B, seed, conf),
                               "denominator": int(sum(d for _n, d in counted.values()))}
        claims = defaultdict(lambda: [0, 0])
        for r, v in pairs:
            if "claims" in v:
                claims[r["intent"]][0] += v["claims"][0]
                claims[r["intent"]][1] += v["claims"][1]
        if claims:
            entry["unsupported_claim_rate"] = {**boot([tuple(c) for c in claims.values()], ratio, B, seed, conf),
                                               "denominator": sum(c[1] for c in claims.values())}
        seconds = [v["seconds"] for _r, v in pairs]
        entry["seconds"] = {"p50": percentile(seconds, 0.5), "p95": percentile(seconds, 0.95)}
        answer_seconds = [v["answer_seconds"] for _r, v in pairs if "answer_seconds" in v]
        if answer_seconds:
            entry["answer_seconds"] = {"p50": percentile(answer_seconds, 0.5), "p95": percentile(answer_seconds, 0.95)}
        entry["integrity"] = sum(v["integrity"] for _r, v in pairs)
        entry["breaches"] = sum(v["breaches"] for _r, v in pairs)
        entry["failed_answers"] = sum("answer_error" in r or "error" in (r.get("grade") or {}) for r, _v in pairs)
        entry["cost"] = {k: round(sum((r.get("cost") or {}).get(k) or 0 for r, _v in pairs), 4)
                         for k in ("jev_requests", "jev_tokens", "host_turns", "host_usd", "host_unknown")}
        cats = defaultdict(list)
        for r, v in pairs:
            if v["recall"] is not None:
                cats[intents[r["intent"]]["category"]].append(v["recall"])
        entry["recall_by_category"] = {c: round(statistics.mean(x), 4) for c, x in sorted(cats.items())}
        # What the graph lane contributed to the evidence handed over; graph_benefit says whether it helped.
        lanes = [lane for r, _v in pairs for lane in r.get("lanes", [])[:opts["k"]]]
        entry["graph_share"] = round(lanes.count("graph") / len(lanes), 4) if lanes else None
        table[arm] = entry
    out = {"arms": table, "differences": {}, "repetitions": repetitions(rows)}

    def paired(a: str, b: str, name: str, keep=lambda i: True) -> dict | None:
        """`b − a` on `name`, per intent, resampled by intent."""

        va = {}
        for arm in (a, b):
            got = by_intent([{"intent": r["intent"], "v": v.get(name)} for r, v in per_arm.get(arm, [])
                             if keep(intents[r["intent"]])], lambda x: x["v"])
            va[arm] = got
        common = sorted(set(va[a]) & set(va[b]))
        if not common:
            return None
        groups = [(va[a][i], va[b][i]) for i in common]
        return boot(groups, lambda g: (ratio([x[1] for x in g]) or 0) - (ratio([x[0] for x in g]) or 0), B, seed,
                    conf)

    for a, b in (("A", "D"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")):
        for name, keep in (("recall", lambda i: True), ("bridge_recall", lambda i: "bridged" in i),
                           ("coverage", lambda i: True)):
            got = paired(a, b, name, keep)
            if got:
                out["differences"][f"{name}_{b}_minus_{a}"] = got
    return out


def repetitions(rows: list[dict]) -> dict:
    """How often repeated runs of one wording in one arm agreed: status, and the evidence handed over."""

    runs = defaultdict(list)
    for r in rows:
        runs[(r["intent"], r["language"], r["arm"])].append(r)
    repeated = {k: v for k, v in runs.items() if len(v) > 1}
    if not repeated:
        return {"groups": 0}
    by_arm = defaultdict(lambda: {"groups": 0, "same_status": 0, "same_evidence": 0})
    for (_i, _l, arm), group in repeated.items():
        e = by_arm[arm]
        e["groups"] += 1
        e["same_status"] += len({r["status"] for r in group}) == 1
        e["same_evidence"] += len({tuple(r["evidence"]) for r in group}) == 1
    return dict(by_arm)


def fixed_report(rows: list[dict], data: dict) -> dict:
    intents = {i["id"]: i for i in data["intents"]}
    sup = {"judged": 0, "yes": 0, "no": 0, "uncertain": 0}
    other = {"judged": 0, "yes": 0, "no": 0, "uncertain": 0}
    order = {"rrf": [], "jev": []}
    redirect = {"adversarial_pages": 0, "flagged": 0, "other_pages": 0, "other_flagged": 0}
    failed = 0
    for r in rows:
        if r["status"] not in ("decided", "uncertain"):
            failed += 1
            continue
        intent = intents[r["intent"]]
        for c in r["candidates"]:
            if c["label"] is None or c["verdict"] is None:
                continue
            tally = sup if supporting(intent, c["label"]) else other
            tally["judged"] += 1
            tally[c["verdict"]] += 1
            hostile = bool(dataset.REDIRECT.search(dataset.passage(data, c["label"].split("#")[0])[1])) \
                if not c["label"].startswith("papers/") else False
            key = "adversarial_pages" if hostile else "other_pages"
            redirect[key] += 1
            redirect["flagged" if hostile else "other_flagged"] += c.get("redirect") == "yes"
        if intent["evidence"]:
            labels = [c["label"] for c in r["candidates"]]
            by_score = [c["label"] for c in sorted(r["candidates"], key=lambda c: -(c["score"] or 0))]
            order["rrf"].append(dataset.recall(intent, labels[:4]))
            order["jev"].append(dataset.recall(intent, by_score[:4]))
    return {"rows": len(rows), "failed": failed,
            "false_rejection": {"rate": round(sup["no"] / sup["judged"], 4) if sup["judged"] else None,
                                "uncertain": sup["uncertain"], "denominator": sup["judged"]},
            "false_acceptance": {"rate": round(other["yes"] / other["judged"], 4) if other["judged"] else None,
                                 "uncertain": other["uncertain"], "denominator": other["judged"]},
            "recall_at_4": {k: round(statistics.mean(v), 4) if v else None for k, v in order.items()},
            "redirect": redirect}


def actions_report(rows: list[dict], cfg: dict | None = None) -> dict:
    """Each point's tallies and, given the gates' `cfg`, the interval of its
    selected-right rate over its fixtures — each fixture one group."""

    points = defaultdict(lambda: {"fixtures": 0, "selected_right": 0, "predicted_right": 0, "asked": 0,
                                  "deferred_or_uncertain": 0})
    for r in rows:
        p = points[r["point"]]
        p["fixtures"] += 1
        p["selected_right"] += r["selected"] == r["label"]
        p["predicted_right"] += r["predicted"] == r["label"]
        p["asked"] += r["status"] in ("decided", "uncertain")
        p["deferred_or_uncertain"] += r["predicted"] is None
    if cfg:
        for point, p in points.items():
            groups = [(float(r["selected"] == r["label"]), 1.0) for r in rows if r["point"] == point]
            p["interval"] = {**boot(groups, ratio, cfg["resamples"], cfg["seed"], cfg["confidence"]),
                             "denominator": len(groups)}
    n = len(rows)
    return {"fixtures": n, "selected_right_rate": round(sum(p["selected_right"] for p in points.values()) / n, 4)
            if n else None, "points": dict(points),
            "violations": sum(len(r["violations"]) for r in rows),
            "executed_unoffered": sum(r["selected"] is not None and r["selected"] not in r["offered"] for r in rows)}


# -- reliability PR 5's cohorts -------------------------------------------------------

def after_route(row: dict) -> str | None:
    """The state a run went to once the route was decided: retrieve, ready (direct) or where it ended."""

    moves = row.get("transitions") or []
    return moves[1] if moves[:1] == ["route"] and len(moves) > 1 else None


def routing_report(rows: list[dict], data: dict, cohort: dict, cfg: dict) -> dict:
    """Analysis/fact routing and request/material classification on the
    routing cohort, each row against its intent's labels. A row the route
    never reached, or whose English or segments are not the stored ones,
    is unscorable — never counted right."""

    intents = {i["id"]: i for i in data["intents"]}
    want = {(i["id"], a) for i in data["intents"] if i["split"] == cohort["split"]
            and i.get("route_expected") is cohort["route_expected"] for a in cohort["arms"]}
    got = {(r["intent"], r["arm"]): r for r in rows if r["rep"] == 0 and (r["intent"], r["arm"]) in want}
    scored: dict[str, list] = {"analysis_routing": [], "segment_classification": []}
    unscorable, mistakes = [], defaultdict(lambda: {"rows": 0, "mistakes": 0})
    for (iid, arm), r in sorted(got.items()):
        i = intents[iid]
        mistakes[arm]["rows"] += 1
        mistakes[arm]["mistakes"] += after_route(r) not in i["transitions"]
        segments = r.get("route_segments")
        if (segments is None or r.get("question_en") != i["variants"]["en"]
                or [s["text"] for s in segments] != [s["text"] for s in i["route_segments"]]):
            unscorable.append(r["key"])
            continue
        scored["analysis_routing"].append({"intent": iid, "v": float(r.get("analysis") is i["analysis"])})
        scored["segment_classification"].append(
            {"intent": iid, "v": float([s["ask"] for s in segments] == [s["ask"] for s in i["route_segments"]])})
    out = {"cohort": len(want), "missing": len(want) - len(got), "unscorable": len(unscorable),
           "unscorable_rows": unscorable[:20], "route_mistakes": dict(mistakes)}
    for name, pairs in scored.items():
        counted = by_intent(pairs, lambda x: x["v"])
        out[name] = ({**boot(list(counted.values()), ratio, cfg["resamples"], cfg["seed"], cfg["confidence"]),
                      "denominator": len(pairs)} if counted else None)
    return out


def answer_cohort(rows: list[dict], data: dict, cohort: dict) -> dict:
    """Whether every row of the answer cohort was answered and graded: a
    failed answer leaves a pair out, and the surviving pairs are not the cohort."""

    want = {(i["id"], a) for i in data["intents"] if i["split"] == cohort["split"] and not i.get("fault")
            for a in cohort["arms"]}
    got = {(r["intent"], r["arm"]): r for r in rows if r["rep"] == 0 and (r["intent"], r["arm"]) in want}
    failed = sorted(r["key"] for r in got.values()
                    if "answer_error" in r or not r.get("grade") or "error" in r["grade"])
    return {"cohort": len(want), "missing": len(want) - len(got), "incomplete": len(failed),
            "incomplete_rows": failed[:20]}


def failure_report(rows: list[dict], data: dict, cohort: dict) -> dict:
    """The failure family's operational result: each faulted row passes when
    it ended in its fault's expected status and reason."""

    faulted = {(i["id"], a): i["fault"] for i in data["intents"] if i.get("fault") and i["split"] == cohort["split"]
               for a in cohort["arms"]}
    got = [r for r in rows if r["rep"] == 0 and (r["intent"], r["arm"]) in faulted]
    failed = [{"key": r["key"], "status": r["status"], "reason": r["reason"],
               "expect": faulted[(r["intent"], r["arm"])]["expect"]} for r in got
              if {"status": r["status"], "reason": r["reason"]} != faulted[(r["intent"], r["arm"])]["expect"]]
    return {"cohort": len(faulted), "missing": len(faulted) - len(got), "passed": len(got) - len(failed),
            "failed": failed}


def category(row: dict) -> str | None:
    """How a Jev arm's published answer was labelled: verified, host-checked (the host model settled part
    of it where Jev was not confident), direct, abstain, unverified analysis, unavailable."""

    status = (row.get("answer") or {}).get("status")
    return (None if status is None else "unavailable" if status == "verification_unavailable" else
            "unverified_analysis" if status == "unverified" else "abstain" if status == "abstained" else
            "host_checked" if row["answer"].get("host_checked") else
            "direct" if row.get("direct") else "verified")


def fallback_report(rows: list[dict]) -> dict:
    """Per arm (an action's point) and decision kind, of the questions Jev
    was asked: how many `fell` to the host model because Jev left them
    uncertain, how many the host `settled`, and the fallback `rate` — what
    Jev's tuning reads (reliability PR 5, v2)."""

    out: dict = {}
    for r in rows:
        for counts in (r.get("fallback") or {}, (r.get("answer") or {}).get("fallback") or {}):
            for kind, n in counts.items():
                e = out.setdefault(r.get("arm") or r.get("point"), {}).setdefault(
                    kind, {"asked": 0, "fell": 0, "settled": 0})
                for k in e:
                    e[k] += n.get(k, 0)
    for kinds in out.values():
        for e in kinds.values():
            e["rate"] = round(e["fell"] / e["asked"], 4) if e["asked"] else None
    return out


def verification_report(rows: list[dict], data: dict) -> dict:
    """Per Jev arm, how answers were labelled — abstention and unverified
    analysis apart, so relabelling cannot pass for a quality gain — how often
    that matches the intent's expected category, and every analysis or
    host-checked answer that was published or would be remembered as
    verified (an integrity violation)."""

    intents = {i["id"]: i for i in data["intents"]}
    out: dict = {"arms": {}, "violations": []}
    for r in rows:
        kind = category(r) if compare.ARMS[r["arm"]]["jev"] and r["rep"] == 0 else None
        if kind is None:
            continue
        a = r["answer"]
        e = out["arms"].setdefault(r["arm"], {"rows": 0, "categories": defaultdict(int), "labelled": 0, "matches": 0})
        e["rows"] += 1
        e["categories"][kind] += 1
        expected = intents[r["intent"]].get("verification")
        if expected:
            e["labelled"] += 1
            e["matches"] += kind == expected
        if (r.get("analysis") or a["status"] == "unverified" or a.get("host_checked")) and (
                a.get("verified") or str(a.get("remembered") or "").startswith("verified")):
            out["violations"].append(r["key"])
    for e in out["arms"].values():
        e["categories"] = dict(e["categories"])
    return out


def at_least(d: dict | None, target: float) -> str:
    """An `interval_min` gate: the interval's lower end at the target passes, its upper end below fails."""

    if not d or d.get("low") is None:
        return "inconclusive"
    return "pass" if d["low"] >= target else "fail" if d["high"] < target else "inconclusive"


def judge(gates: dict, found: dict, reviewed: bool) -> list[dict]:
    """Each gate's result on what was measured. A gate that names its run
    (reliability PR 5) reads that run's report and no other, and its cohort
    must be whole; version 1–3 gates name none and read the arms run there is."""

    retrieval, answers = found.get("arms") or {}, found.get("answers") or {}
    tables = [r.get("arms", {}) for r in (retrieval, answers, found.get("repeat") or {})]
    actions = found.get("actions")
    out = []
    for g in gates["gates"]:
        if "run" in g:
            arms = {"arms/retrieval": retrieval, "arms/answer": answers}.get(g["run"], {})
        else:
            arms = (answers or retrieval) if g["id"] in ("answer_support", "added_latency") else (retrieval or answers)
        diffs, table = arms.get("differences", {}), arms.get("arms", {})
        value, verdict, detail = None, "not_measured", {}
        if g["id"] == "integrity" and (any(tables) or actions):
            value = sum(t["integrity"] for tb in tables for t in tb.values()) + \
                (actions or {}).get("violations", 0) + (actions or {}).get("executed_unoffered", 0) + \
                len((found.get("verification") or {}).get("violations", []))
            verdict = "pass" if value == 0 else "fail"
        elif g.get("cohort") == "routing":
            routing = found.get("routing")
            if routing and routing["cohort"] and not routing["missing"]:
                detail = {**(routing[g["metric"]] or {}), "unscorable": routing["unscorable"]}
                verdict = "inconclusive" if routing["unscorable"] else at_least(routing[g["metric"]], g["target"])
        elif g.get("cohort") == "actions":
            point = ((actions or {}).get("points") or {}).get(g["point"])
            if point and found.get("actions_split") == gates["cohorts"]["actions"]["split"]:
                detail = point.get("interval") or {}
                verdict = at_least(detail, g["target"])
        elif g["id"] == "graph_benefit" and "bridge_recall_D_minus_A" in diffs:
            detail = diffs["bridge_recall_D_minus_A"]
            verdict = improvement(detail, g["target"])
        elif g["id"] == "overall_recall" and "recall_D_minus_A" in diffs:
            detail = diffs["recall_D_minus_A"]
            verdict = ("pass" if detail["low"] is not None and detail["low"] >= g["target"] else
                       "fail" if detail["high"] is not None and detail["high"] < g["target"] else "inconclusive")
        elif g["id"] == "answer_support" and all("unsupported_claim_rate" in table.get(a, {}) for a in "AD"):
            a, d = table["A"]["unsupported_claim_rate"]["value"], table["D"]["unsupported_claim_rate"]["value"]
            cov = diffs.get("coverage_D_minus_A") or {}
            # Coverage may fall short of A's by `coverage_margin`, read at the interval's lower end
            # (version 3); without one, any shortfall of the point estimate fails (versions 1 and 2).
            margin = g.get("coverage_margin")
            lower = (cov.get("low") is not None and cov["low"] < margin if margin is not None else
                     cov.get("value") is not None and cov["value"] < 0)
            detail = {"A": a, "D": d, "coverage_D_minus_A": cov, "coverage_margin": margin}
            if a != 0:
                value = round((a - d) / a, 4)
            supported = d == 0 if a == 0 else value >= g["target"]
            # A margin read at an interval's lower end is not met by no interval (review round 2).
            unknown = margin is not None and cov.get("low") is None
            verdict = "fail" if not supported or lower else "inconclusive" if unknown else "pass"
        elif g["id"] == "decision_quality" and actions:
            value = actions["selected_right_rate"]
            verdict = "pass" if value >= g["target"] else "fail"
        elif g["id"] == "added_latency" and all(a in table for a in "AD"):
            key = "answer_seconds" if all("answer_seconds" in table[a] for a in "AD") else "seconds"
            value = round(table["D"][key]["p95"] - table["A"][key]["p95"], 3)
            detail = {"measured": key}
            verdict = "pass" if value <= g["target"] else "fail"
        elif g["id"] == "operating_ceiling" and any(tables):
            value = sum(t["breaches"] for tb in tables for t in tb.values())
            verdict = "pass" if value == 0 else "fail"
        if g.get("cohort") == "answer":
            # v3's statistics hold on a complete cohort only: surviving pairs are not the cohort.
            whole = found.get("answer_cohort")
            detail = {**detail, "cohort": whole}
            if not whole or not whole["cohort"] or whole["missing"]:
                verdict = "not_measured"
            elif whole["incomplete"]:
                verdict = "inconclusive"
        if "value" in detail and value is None:
            value = detail["value"]
        if verdict == "pass" and not reviewed and g["id"] not in ("integrity", "operating_ceiling", "added_latency"):
            verdict = "provisional"
        out.append({"id": g["id"], "label": g["label"], "target": g.get("target"), "value": value,
                    "verdict": verdict, "detail": detail})
    return out


def improvement(d: dict, target: float) -> str:
    if d["value"] is None:
        return "not_measured"
    if d["value"] >= target and d["low"] is not None and d["low"] > 0:
        return "pass"
    if d["high"] is not None and d["high"] < target:
        return "fail"
    return "inconclusive"


def kind(run: dict) -> str:
    """What a run measured, as the gates name it: `arms/retrieval`, `arms/answer`, `arms/repeat`, `fixed`, `actions`."""

    if run["experiment"] != "arms":
        return run["experiment"]
    return "arms/repeat" if run["options"]["repeat"] > 1 else f"arms/{run['options']['level']}"


def unchanged(name: str, recorded: str, what: str) -> Path:
    """The file a manifest names, refused when it no longer has the hash the runs recorded."""

    path = HUB / name
    if not path.exists() or dataset.sha(path.read_bytes()) != recorded:
        raise SystemExit(f"{name} is not the {what} these runs measured: freeze a new version and run again")
    return path


def build(folders: list[Path]) -> dict:
    runs: dict[str, tuple[Path, dict]] = {}
    for folder in folders:
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        if kind(run) in runs:
            raise SystemExit(f"two runs of {kind(run)}: {runs[kind(run)][0]} and {folder}")
        runs[kind(run)] = (folder, run)
    manifests = [r.get("manifest") for _f, r in runs.values()]
    if any(manifests) and not all(manifests):
        raise SystemExit("runs recorded with and without a manifest cannot be one comparison")
    shared = {json.dumps({k: m[k] for k in compare.SHARED}, sort_keys=True) for m in manifests if m}
    if len(shared) > 1:
        differ = sorted(k for k in compare.SHARED if len({json.dumps(m[k], sort_keys=True) for m in manifests}) > 1)
        raise SystemExit(f"these runs measured different things ({', '.join(differ)}); compare runs of one manifest")
    first = manifests[0] if manifests and manifests[0] else None
    if first:
        data = dataset.load(unchanged(first["dataset"], first["dataset_hash"], "dataset"))
        gates = json.loads(unchanged(first["gates"], first["gates_hash"], "gates").read_text(encoding="utf-8"))
    else:
        data, gates = dataset.load(), json.loads(GATES.read_text(encoding="utf-8"))
    cohorts = gates.get("cohorts") or {}
    found: dict = {"runs": {}}
    reviews = [data["labels"]]
    for name, (folder, run) in runs.items():
        rows = compare.rows(folder, run["experiment"])
        found["runs"][name] = {"folder": folder.as_posix(), "options": run["options"], "versions": run["versions"],
                               "manifest": run.get("manifest"), "batches": run["batches"], "rows": len(rows),
                               "dataset": run["dataset"], "corpus": run["corpus"],
                               "graph_health": run.get("graph_health")}
        if name == "arms/retrieval":
            found["arms"] = arms_report(rows, data, run["options"], gates)
            if "routing" in cohorts:
                found["routing"] = routing_report(rows, data, cohorts["routing"], gates)
            if "failure" in cohorts:
                found["failure"] = failure_report(rows, data, cohorts["failure"])
        elif name == "arms/answer":
            found["answers"] = arms_report(rows, data, run["options"], gates)
            found["verification"] = verification_report(rows, data)
            if "answer" in cohorts:
                found["answer_cohort"] = answer_cohort(rows, data, cohorts["answer"])
        elif name == "arms/repeat":
            found["repeat"] = arms_report(rows, data, run["options"], gates)
        elif name == "fixed":
            found["fixed"] = fixed_report(rows, data)
        else:
            # The fixtures this run read, by the hash it recorded — and the manifest's, when there is one.
            opts = run["options"]["fixtures"]
            if run.get("manifest") and run["manifest"]["actions_hash"] != opts["sha256"]:
                raise SystemExit(f"{folder}: its action fixtures are not the ones its manifest names")
            path = unchanged(opts["path"], opts["sha256"], "action fixtures") if "path" in opts else compare.ACTIONS
            reviews.append(json.loads(path.read_text(encoding="utf-8"))["labels"])
            found["actions"] = actions_report(rows, gates)
            found["actions_split"] = run["options"]["split"]
        found.setdefault("fallback", {})[name] = fallback_report(rows)
    # A quality result counts only on reviewed labels: the intents', and the fixtures' when decisions were measured.
    reviewed = all(labels["reviewed_by"] for labels in reviews)
    return {"schema": REPORT, "gates_id": gates.get("id", "gates"), "gates_version": gates["version"],
            "labels_reviewed": reviewed, "reviewers": sorted({labels["reviewed_by"] or "nobody" for labels in reviews}),
            "categories": data["categories"], "exclusions": data["exclusions"], **found,
            "gates": judge(gates, found, reviewed)}


def markdown(report: dict) -> str:
    who = "; ".join(report.get("reviewers") or [])
    lines = [f"Labels reviewed: {'yes — by ' + who if report['labels_reviewed'] else 'no — quality results are provisional'}",
             ""]
    def cell(e: dict | None) -> str:
        if not e or e.get("value") is None:
            return "—"
        ci = f" [{e['low']}, {e['high']}]" if e.get("low") is not None else ""
        return f"{e['value']}{ci} (n={e.get('denominator', e.get('n'))})"

    for part, title in (("arms", "Retrieval"), ("answers", "Answers")):
        arms = (report.get(part) or {}).get("arms", {})
        if not arms:
            continue
        lines += [f"{title}:", "",
                  "| Arm | Rows | Recall@k | Candidate recall | Bridge recall | Coverage | Unsupported | p95 s | Jev tokens | Host USD |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for arm, e in arms.items():
            lines.append(f"| {arm} | {e['rows']} | {cell(e.get('recall'))} | {cell(e.get('candidate_recall'))} | "
                         f"{cell(e.get('bridge_recall'))} | {cell(e.get('coverage'))} | "
                         f"{cell(e.get('unsupported_claim_rate'))} | {e['seconds']['p95']} | "
                         f"{e['cost']['jev_tokens']} | {e['cost']['host_usd']}"
                         f"{f' + {u} turns unpriced' if (u := e['cost'].get('host_unknown')) else ''} |")
        lines.append("")
    if routing := report.get("routing"):
        lines += [f"Routing cohort: {routing['cohort']} rows, {routing['missing']} missing, {routing['unscorable']} "
                  f"unscorable; analysis/fact {cell(routing['analysis_routing'])}, request/material "
                  f"{cell(routing['segment_classification'])}; route mistakes {routing['route_mistakes']}", ""]
    if failure := report.get("failure"):
        lines += [f"Failure and cancellation (operational): {failure['passed']} of {failure['cohort']} ended as "
                  f"expected, {failure['missing']} missing, {len(failure['failed'])} otherwise", ""]
    if verification := report.get("verification"):
        lines += [f"Answer labels: {json.dumps(verification['arms'])}; analysis or host-checked answer published "
                  f"or remembered as verified: {len(verification['violations'])}", ""]
    for run, arms in (report.get("fallback") or {}).items():
        said = "; ".join(f"{arm} " + ", ".join(f"{kind} {e['fell']}/{e['asked']} (settled {e['settled']})"
                                               for kind, e in sorted(kinds.items()))
                         for arm, kinds in arms.items())
        if said:
            lines += [f"Host fallback ({run}), fell/asked per kind: {said}", ""]
    if fixed := report.get("fixed"):
        lines += [f"Fixed candidates ({fixed['rows']} requests, {fixed['failed']} failed): false rejection "
                  f"{fixed['false_rejection']['rate']} of {fixed['false_rejection']['denominator']}, false acceptance "
                  f"{fixed['false_acceptance']['rate']} of {fixed['false_acceptance']['denominator']}, recall@4 "
                  f"RRF {fixed['recall_at_4']['rrf']} / Jev {fixed['recall_at_4']['jev']}", ""]
    lines += ["| Gate | Target | Value | Result |", "| --- | --- | --- | --- |"]
    for g in report["gates"]:
        lines.append(f"| {g['label']} | {g['target'] if g['target'] is not None else '0'} | {g['value']} | {g['verdict']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/report.py", description="Stage 10's measurements")
    parser.add_argument("run_dirs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    report = build(args.run_dirs)
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
