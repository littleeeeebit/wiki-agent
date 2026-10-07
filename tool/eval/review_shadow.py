"""Replay frozen production shadow requests; live evaluation is explicit and sends no labels."""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common.budget import Budget  # noqa: E402
from main import decisions, review_contract as contract, verification  # noqa: E402

AXES = ("criteria", "evidence", "flows")


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "review-shadow-evaluation/1":
        raise ValueError("unsupported production evaluation schema")
    ids, families = set(), {}
    for case in data["cases"]:
        if case["id"] in ids or case["split"] not in ("calibration", "heldout"):
            raise ValueError("duplicate case or unknown split")
        ids.add(case["id"])
        if families.setdefault(case["family"], case["split"]) != case["split"]:
            raise ValueError("scenario family leaks across splits")
        contract.replay_shadow(case["record"])
        labels = case.get("expected")
        if labels is not None and (set(labels) != {*AXES, "unresolved", "prohibited", "acceptable_extras"}
                                   or any(not isinstance(v, list) or any(not isinstance(x, str) for x in v)
                                          for v in labels.values())):
            raise ValueError("labels need facet/flow sets, unresolved inputs and prohibited/acceptable extras")
    return data


def sample(case: dict, cfg=None) -> dict:
    record = case["record"]
    replay = contract.replay_shadow(record)
    req = record["request"]
    result, trace = record["result"], []
    if cfg is not None:
        qs, version = contract.shadow_questions(req["state_en"])
        if qs != req["questions"] or version != req["prompt_version"] or req["policy_version"] != contract.POLICY.version:
            raise ValueError("frozen request differs from the current production prompt/catalog/policy")
        budget = Budget(**contract.SHADOW_LIMITS)
        current = decision.request(req["decision_kind"], copy.deepcopy(req["state_en"]), qs,
            allowed=req["allowed_candidate_ids"], model=cfg.model, prompt_version=version,
            policy_version=contract.POLICY.version, normalization_version=req["normalization_version"], budget=budget)
        result = decision.checked(current, decision.decide(current, decisions.transport(cfg), budget, trace, contract.POLICY))
        replay["recommendations"] = contract.shadow_recommendations(current, result)
    baseline = req["state_en"]["baseline"]
    predicted = {axis: set(baseline.get(axis, [])) for axis in AXES}
    predicted["flows"] = set(req["state_en"].get("selected_flows", []))
    unresolved, unsupported = [], []
    for r in replay["recommendations"]:
        if r["choice"] == decision.DEFER or r["verdict"] == "uncertain":
            unresolved.append(r["candidate_id"])
        if r["ground_status"] == "unsupported":
            unsupported.append(r["candidate_id"])
        if r["choice"] == "add" and r["verdict"] == "yes":
            axis, item = r["candidate_id"].split(":", 1)
            predicted["flows" if axis == "flow" else axis].add(item)
    row = {"id": case["id"], "family": case["family"], "split": case["split"],
           "input_identity": record["input_identity"], "context_digest": record["context_digest"],
           "prompt_version": req["prompt_version"], "policy_version": req["policy_version"],
           "predicted": {k: sorted(v) for k, v in predicted.items()}, "unresolved": sorted(unresolved),
           "unsupported": sorted(unsupported), "decision": result, "trace": trace,
           "baseline_disagreement": any(predicted[a] != set(baseline.get(a, []) if a != "flows" else
               req["state_en"].get("selected_flows", [])) for a in AXES)}
    if expected := case.get("expected"):
        missing = {a: sorted(set(expected[a]) - predicted[a]) for a in AXES}
        extra = {a: sorted(predicted[a] - set(expected[a]) - {
            c.split(":", 1)[1] for c in expected["acceptable_extras"] if c.startswith(("flow" if a == "flows" else a) + ":")})
            for a in AXES}
        named = {("flow" if a == "flows" else a) + ":" + v for a in AXES for v in predicted[a]}
        row["score"] = {"missing": missing, "extra": extra, "exact_sets": all(
            predicted[a] == set(expected[a]) for a in AXES), "prohibited": sorted(named & set(expected["prohibited"])),
            "unresolved_exact": sorted(unresolved) == sorted(expected["unresolved"])}
    return row


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path)
    source.add_argument("--spec", type=Path, help="Replay recorded spec attempts without labels")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    path = args.dataset or args.spec
    if args.out.resolve() == path.resolve():
        parser.error("output must not replace frozen inputs")
    digest = verification.sha(path.read_bytes())
    if args.dataset:
        data = load(path)
    else:
        spec = json.loads(path.read_text(encoding="utf-8"))
        data = {"labels": {"reviewed_by": None, "status": "unlabelled actual task observations"}, "cases": [
            {"id": r["attempt_id"], "family": spec["id"], "split": "calibration", "record": r}
            for r in spec.get("review_shadow_attempts", []) if r.get("request") and r.get("result")]}
    cfg = decision.config() if args.live else None
    if cfg is not None and (cfg.problem or not cfg.key):
        parser.error("Jev is not configured")
    rows = [sample(c, cfg) for c in data["cases"]]
    calls = [t for r in rows for t in r["trace"]]
    elapsed = [r["decision"]["elapsed_ms"] for r in rows if args.live]
    output = {"schema": "review-shadow-results/1", "dataset_sha256": digest, "labels": data.get("labels"),
              "live": args.live, "dispatch": "none: audit-only production prompt evaluation", "rows": rows,
              "usage": {"requests": sum(bool(t.get("sent")) for t in calls),
                        "input_tokens": sum((t.get("usage") or {}).get("input_tokens", 0) for t in calls),
                        "output_tokens": sum((t.get("usage") or {}).get("output_tokens", 0) for t in calls),
                        "median_ms": statistics.median(elapsed) if elapsed else None,
                        "max_ms": max(elapsed) if elapsed else None, "cost_usd": None}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"out": str(args.out), "cases": len(rows), "usage": output["usage"]}))
    return int(any(r["decision"]["status"] in ("invalid", "unavailable", "cancelled", "exhausted") for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
