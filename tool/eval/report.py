"""`python tool/eval/report.py <run-dir> [<run-dir> ...] [--out <file.json>]`

Stage 10's measurements from recorded rows (`tool/eval/compare.py`), and
each frozen gate of `eval/jev/gates.json` judged on them. Sends nothing.

Every rate names its denominator. Intervals are 95% percentile intervals from
resampling intents — an intent's English and Korean wordings and its
repetitions move together, never counted as independent evidence. A gate
reads `pass`, `fail`, `inconclusive` (the interval does not settle it),
`not_measured` (no rows for it), or `provisional` — a pass on labels no
person has reviewed. The Markdown summary goes to stdout.
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
                         for k in ("jev_requests", "jev_tokens", "host_turns", "host_usd")}
        cats = defaultdict(list)
        for r, v in pairs:
            if v["recall"] is not None:
                cats[intents[r["intent"]]["category"]].append(v["recall"])
        entry["recall_by_category"] = {c: round(statistics.mean(x), 4) for c, x in sorted(cats.items())}
        table[arm] = entry
    out = {"arms": table, "differences": {}, "language": {}, "repetitions": repetitions(rows)}

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
    for arm, pairs in per_arm.items():
        gaps = {}
        for name in ("recall", "coverage"):
            en = by_intent([{"intent": r["intent"], "v": v.get(name)} for r, v in pairs if r["language"] == "en"],
                           lambda x: x["v"])
            ko = by_intent([{"intent": r["intent"], "v": v.get(name)} for r, v in pairs if r["language"] == "ko"],
                           lambda x: x["v"])
            common = sorted(set(en) & set(ko))
            if common:
                gaps[name] = boot([(en[i], ko[i]) for i in common],
                                  lambda g: (ratio([x[0] for x in g]) or 0) - (ratio([x[1] for x in g]) or 0),
                                  B, seed, conf)
        out["language"][arm] = gaps
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


def actions_report(rows: list[dict]) -> dict:
    points = defaultdict(lambda: {"fixtures": 0, "selected_right": 0, "predicted_right": 0, "asked": 0,
                                  "deferred_or_uncertain": 0})
    for r in rows:
        p = points[r["point"]]
        p["fixtures"] += 1
        p["selected_right"] += r["selected"] == r["label"]
        p["predicted_right"] += r["predicted"] == r["label"]
        p["asked"] += r["status"] in ("decided", "uncertain")
        p["deferred_or_uncertain"] += r["predicted"] is None
    n = len(rows)
    return {"fixtures": n, "selected_right_rate": round(sum(p["selected_right"] for p in points.values()) / n, 4)
            if n else None, "points": dict(points),
            "violations": sum(len(r["violations"]) for r in rows),
            "executed_unoffered": sum(r["selected"] is not None and r["selected"] not in r["offered"] for r in rows)}


def judge(gates: dict, found: dict, reviewed: bool) -> list[dict]:
    """Each gate's result on what was measured."""

    arms = found.get("arms") or {}
    diffs = arms.get("differences", {})
    table = arms.get("arms", {})
    actions = found.get("actions")
    out = []
    for g in gates["gates"]:
        value, verdict, detail = None, "not_measured", {}
        if g["id"] == "integrity" and (table or actions):
            value = sum(t["integrity"] for t in table.values()) + (actions or {}).get("violations", 0) + \
                (actions or {}).get("executed_unoffered", 0)
            verdict = "pass" if value == 0 else "fail"
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
            lower = cov.get("value") is not None and cov["value"] < 0
            detail = {"A": a, "D": d, "coverage_D_minus_A": cov}
            if a == 0:
                verdict = "pass" if d == 0 and not lower else "fail"
            else:
                value = round((a - d) / a, 4)
                verdict = "fail" if value < g["target"] or lower else "pass"
        elif g["id"] == "decision_quality" and actions:
            value = actions["selected_right_rate"]
            verdict = "pass" if value >= g["target"] else "fail"
        elif g["id"] == "language_parity" and arms.get("language", {}).get("D"):
            gaps = arms["language"]["D"]
            value = max(abs(x["value"]) for x in gaps.values())
            detail = gaps
            verdict = "pass" if value <= g["target"] else "fail"
        elif g["id"] == "added_latency" and all(a in table for a in "AD"):
            key = "answer_seconds" if all("answer_seconds" in table[a] for a in "AD") else "seconds"
            value = round(table["D"][key]["p95"] - table["A"][key]["p95"], 3)
            detail = {"measured": key}
            verdict = "pass" if value <= g["target"] else "fail"
        elif g["id"] == "operating_ceiling" and table:
            value = sum(t["breaches"] for t in table.values())
            verdict = "pass" if value == 0 else "fail"
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


def build(folders: list[Path]) -> dict:
    data = dataset.load()
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    found: dict = {"runs": {}}
    for folder in folders:
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        rows = compare.rows(folder, run["experiment"])
        found["runs"][run["experiment"]] = {"folder": folder.as_posix(), "options": run["options"],
                                            "versions": run["versions"], "batches": run["batches"],
                                            "rows": len(rows), "dataset": run["dataset"], "corpus": run["corpus"]}
        if run["experiment"] == "arms":
            found["arms"] = arms_report(rows, data, run["options"], gates)
        elif run["experiment"] == "fixed":
            found["fixed"] = fixed_report(rows, data)
        else:
            found["actions"] = actions_report(rows)
    # Decision quality reads the action fixtures' labels, so both files must carry a reviewer.
    fixtures = json.loads(compare.ACTIONS.read_text(encoding="utf-8"))
    reviewed = bool(data["labels"]["reviewed_by"] and fixtures["labels"]["reviewed_by"])
    return {"schema": REPORT, "gates_version": gates["version"], "labels_reviewed": reviewed,
            "categories": data["categories"], "exclusions": data["exclusions"], **found,
            "gates": judge(gates, found, reviewed)}


def markdown(report: dict) -> str:
    lines = [f"Labels reviewed by a person: {'yes' if report['labels_reviewed'] else 'no — quality results are provisional'}", ""]
    arms = (report.get("arms") or {}).get("arms", {})
    if arms:
        lines += ["| Arm | Rows | Recall@k | Candidate recall | Bridge recall | Coverage | Unsupported | p95 s | Jev tokens | Host USD |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]

        def cell(e: dict | None) -> str:
            if not e or e.get("value") is None:
                return "—"
            ci = f" [{e['low']}, {e['high']}]" if e.get("low") is not None else ""
            return f"{e['value']}{ci} (n={e.get('denominator', e.get('n'))})"

        for arm, e in arms.items():
            lines.append(f"| {arm} | {e['rows']} | {cell(e.get('recall'))} | {cell(e.get('candidate_recall'))} | "
                         f"{cell(e.get('bridge_recall'))} | {cell(e.get('coverage'))} | "
                         f"{cell(e.get('unsupported_claim_rate'))} | {e['seconds']['p95']} | "
                         f"{e['cost']['jev_tokens']} | {e['cost']['host_usd']} |")
        lines.append("")
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
