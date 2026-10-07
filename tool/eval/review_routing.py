"""Evaluate review selection on synthetic tasks; no reviewer or check is dispatched.

Default is offline. --live calls the configured Jev with synthetic inputs only.
Results include raw predictions, deterministic obligations, and their composition.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import decision  # noqa: E402
from common.budget import Budget  # noqa: E402
from eval.baseline import RAW, revision, sha  # noqa: E402
from main import loop  # noqa: E402
from search import HUB  # noqa: E402

DATASET = HUB / "eval/jev/review-routing.json"
CRITERIA = {
    "code": "Correctness, regression, unsafe transitions and permission/evidence defects in implementation. "
            "Also retain this conservative floor for a declared code profile, including prose-only legacy tasks.",
    "plan": "Implementability of a roadmap: requirements, source support, dependencies, ownership, "
            "observable future acceptance and rollback. Missing future implementation is not a defect.",
    "refactor": "Preservation of public behavior, exceptions and side effects; frozen baseline/candidate "
                "comparison, scope, debt reduction and no weakened tests. An intentional bug fix is not "
                "behavior preservation just because its title says refactor. Add code criteria too.",
    "documentation": "Accuracy and links in ordinary non-executable prose. Excludes roadmaps, executable "
                     "prompts, rules and changed API contracts.",
}
EVIDENCE = {
    "offline": "Repository gate plus applicable local tests, contracts, document or scenario checks. "
               "Mandatory for every task, including plans and ordinary prose.",
    "api": "Observed calls to the actual dedicated test API/tenant. Required when acceptance depends on "
           "deployed authentication, data, persistence or provider behavior that local fixtures cannot prove. "
           "A local provider contract test alone does not require a deployed API.",
    "browser": "Actual browser interactions and observed UI results when acceptance concerns focus, "
               "keyboard, navigation, layout or a browser user flow; not for future UI plans alone.",
    "desktop": "Actual native host/packaged app observations when acceptance concerns Windows processes, "
               "native hooks or notification delivery; browser IPC stubs are insufficient.",
    "differential": "Matching frozen baseline/candidate witnesses for behavior-preserving restructuring "
                    "or test cleanup. Not required for intentional new behavior or a bug fix.",
}
NEXT = {
    "review": "All necessary evidence for this task is supplied, observed, passing and bound to the current "
              "head/base/spec/environment. Ready to ask an independent reviewer, NOT merge approval.",
    "wait_evidence": "At least one required receipt is missing, failed, unobserved or stale in head, "
                     "base, specification revision or environment. Gather evidence first.",
    "wait_environment": "A required live test environment is unavailable. Preparation pending; "
                        "passing mocks cannot replace it.",
    "clarify": "Acceptance or scope is unspecified, so the evidence boundary cannot be determined.",
    "research": "An unresolved identical invariant recurred at the initial head and two distinct repair "
                "heads. Preserve cause/evidence and a next experiment before ordinary fixing resumes.",
}
POLICY = decision.Policy("review-routing-provisional-1", {"action": {"confidence": 0.6, "margin": 0.2}})
PROMPT_VERSION = "review-routing-3"
VARIANTS = ("normal", "reverse", "rename", "rename_reverse")
EXCLUSIONS = {
    "criteria:code": "This is a declared plan-only PR; all changed files are roadmap Markdown wholly "
                     "inside task.artifact_root. There is no implemented behavior in this PR.",
    "criteria:plan": "No roadmap is changed. Prose guides, executable prompts and code alone are not plans.",
    "criteria:refactor": "The task fixes a defect or changes intended observable behavior. "
                         "Checking regressions in any ordinary implementation is NOT a refactor. "
                         "Require an explicit behavior-preserving restructuring or test-cleanup contract.",
    "criteria:documentation": "The PR changes code, a roadmap or an executable prompt, not an ordinary prose guide.",
    "evidence:offline": "The registry exempts this task from every repository gate and local check; "
                        "acceptance has no local verification obligation.",
    "evidence:api": "No actual deployed-service behavior is part of current acceptance. A local contract "
                    "test or a plan for a future API does not require live API execution now.",
    "evidence:browser": "No current browser interaction or rendering acceptance; API-only tasks and plans "
                        "for future UI need no actual browser run.",
    "evidence:desktop": "No current native host/packaged app acceptance. Browser-only behavior or future "
                        "native plans need no desktop run.",
    "evidence:differential": "The task intentionally changes behavior or only writes a plan/prose; no "
                             "baseline/candidate preservation claim is made.",
}


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "review-routing-dataset/1":
        raise ValueError("unsupported dataset schema")
    ids, groups = set(), {}
    for case in data["cases"]:
        if case["id"] in ids or case["split"] not in ("calibration", "heldout"):
            raise ValueError("duplicate id or unknown split")
        ids.add(case["id"])
        if groups.setdefault(case["group"], case["split"]) != case["split"]:
            raise ValueError("scenario family leaks across splits")
        gold = case["expected"]
        if (not set(gold["criteria"]) <= CRITERIA.keys() or not set(gold["evidence"]) <= EVIDENCE.keys()
                or gold["next"] not in NEXT):
            raise ValueError("unknown expected selection")
    return data


def questions(variant: str) -> tuple[dict, dict]:
    reverse, rename = "reverse" in variant, "rename" in variant
    mapping, out = {}, {}

    def add(name: str, instruction: str, options: dict):
        labels = list(options)
        ids = [f"c{i}" for i in range(len(labels))]
        if rename:
            ids = ids[1:] + ids[:1]
        pairs = list(zip(ids, labels))
        if reverse:
            pairs.reverse()
        mapping[name] = dict(pairs)
        criteria = {key: options[label] for key, label in pairs}
        criteria[decision.DEFER] = "Insufficient information to select one option reliably."
        out[name] = {"decision": "action", "candidate": None,
                     "question": decision.choice(instruction, criteria)}

    for axis, facets in (("criteria", CRITERIA), ("evidence", EVIDENCE)):
        for name, about in facets.items():
            key = f"{axis}:{name}"
            add(key, f"For the CURRENT PR, is the {name} {axis} facet required? Classify the task's "
                "explicit acceptance contract. A missing receipt does not remove an obligation. "
                "Planning future behavior does not implement it now.",
                {"required": about, "unneeded": EXCLUSIONS[key]})
    add("next", "Which step is appropriate before independent review for this current PR? Determine "
        "needed evidence from the acceptance; each question is independent, so other answers are not "
        "available. Receipt kind alone does not prove identity or an actual observation. Prioritize "
        "research, unclear scope, unavailable environment, missing evidence, then review readiness.", NEXT)
    return out, mapping


def current(state: dict) -> list[str]:
    task = state["task"]
    spec = {"review_profile": task["requested_profile"], "artifact_root": task["artifact_root"]}
    name = loop.effective(spec, task["changed_files"])
    return sorted(["code", "plan"] if name == "mixed" else [name])


def next_step(state: dict, evidence: list[str]) -> str:
    if len(set(state["history"]["unresolved_same_invariant_heads"])) >= 3:
        return "research"
    if not state["task"]["scope_complete"]:
        return "clarify"
    if not state["registry"]["environment_available"] and set(evidence) & {"api", "browser", "desktop"}:
        return "wait_environment"
    identity = state["identity"]
    for kind in evidence:
        if not any(r["kind"] == kind and r["passed"] is True and r["observed"] is True
                   and all(r[k] == identity[k] for k in ("head", "base", "spec_revision", "environment"))
                   for r in state["receipts"]):
            return "wait_evidence"
    return "review"


def rules(state: dict) -> dict:
    criteria = set(current(state))
    evidence = {"offline", *state["registry"]["mandatory_evidence"]}
    if state["task"]["origin"] == "refactor":
        criteria.update(("code", "refactor"))
        evidence.add("differential")
    return {"criteria": sorted(criteria), "evidence": sorted(evidence),
            "next": next_step(state, sorted(evidence))}


def compose(state: dict, predicted: dict, uncertain: list[str]) -> dict:
    floor = rules(state)
    out = {axis: sorted(set(floor[axis]) | set(predicted[axis])) for axis in ("criteria", "evidence")}
    if "refactor" in out["criteria"]:
        out["criteria"] = sorted(set(out["criteria"]) | {"code"})
        out["evidence"] = sorted(set(out["evidence"]) | {"differential"})
    # Uncertainty about an already enforced obligation cannot remove it or hold a ready task.
    unresolved = [q for q in uncertain if q == "next" or q.split(":")[1] not in out[q.split(":")[0]]]
    needed = next_step(state, out["evidence"])
    out["next"] = needed if needed != "review" else (
        "clarify" if unresolved else predicted["next"])
    return out


def score(expected: dict, actual: dict) -> dict:
    missing = {axis: sorted(set(expected[axis]) - set(actual[axis])) for axis in ("criteria", "evidence")}
    extra = {axis: sorted(set(actual[axis]) - set(expected[axis])) for axis in ("criteria", "evidence")}
    return {"exact": expected == actual, "criteria_exact": expected["criteria"] == actual["criteria"],
            "evidence_exact": expected["evidence"] == actual["evidence"],
            "next_exact": expected["next"] == actual["next"], "missing": missing, "extra": extra,
            "premature_review": actual["next"] == "review" and expected["next"] != "review"}


def sample(case: dict, cfg: decision.Config | None, variant: str) -> dict:
    state = case["state"]
    row = {"id": case["id"], "group": case["group"], "split": case["split"],
           "expected": case["expected"], "current_profile": current(state), "rules": rules(state)}
    if cfg is None:
        return row
    qs, mapping = questions(variant)
    budget, trace = Budget(seconds=15, calls=1, candidates=0), []
    req = decision.request("review.selection.experiment", state, qs, allowed=["c0", "c1", "c2", "c3", "c4"],
                           model=cfg.model, prompt_version=prompt_digest(variant), policy_version=POLICY.version,
                           normalization_version="synthetic-english-1", budget=budget, trace_id=case["id"])
    result = decision.decide(req, lambda *args: decision.evaluate(cfg, *args), budget, trace, POLICY)
    decision.checked(req, result)
    predicted, uncertain = {"criteria": [], "evidence": [], "next": "clarify"}, []
    raw = {"criteria": [], "evidence": [], "next": "clarify"}
    for name, codes in mapping.items():
        answer = result["answers"].get(name)
        label = codes.get(answer["choice"]) if answer else None
        accepted = result["verdicts"].get(name) == "yes"
        if not accepted:
            uncertain.append(name)
        if name == "next":
            raw[name] = label or "clarify"
            predicted[name] = label if accepted else "clarify"
        elif label == "required":
            axis, facet = name.split(":")
            raw[axis].append(facet)
            if accepted:
                predicted[axis].append(facet)
    for out in (raw, predicted):
        for axis in ("criteria", "evidence"):
            out[axis].sort()
    return {**row, "jev": raw, "accepted": predicted, "uncertain": uncertain,
            "guarded": compose(state, predicted, uncertain), "decision": result, "trace": trace,
            "budget": budget.record()}


def prompt_digest(variant: str) -> str:
    return hashlib.sha256(json.dumps(questions(variant), sort_keys=True).encode()).hexdigest()


def summarize(rows: list[dict]) -> dict:
    out = {"cases": len(rows), "current_profile_exact": sum(r["current_profile"] == r["expected"]["criteria"]
                                                            for r in rows)}
    for arm in ("rules", "jev", "guarded"):
        scores = [score(r["expected"], r[arm]) for r in rows if arm in r]
        if not scores:
            continue
        out[arm] = {k: sum(s[k] for s in scores) for k in
                    ("exact", "criteria_exact", "evidence_exact", "next_exact", "premature_review")}
        out[arm].update(missing_criteria=sum(bool(s["missing"]["criteria"]) for s in scores),
                        missing_evidence=sum(bool(s["missing"]["evidence"]) for s in scores),
                        unnecessary_evidence=sum(bool(s["extra"]["evidence"]) for s in scores),
                        cases=len(scores))
    out["uncertain_cases"] = sum(bool(r.get("uncertain")) for r in rows)
    out["review_ready_cases"] = sum(r["expected"]["next"] == "review" for r in rows)
    out["ready_cases_forwarded"] = sum(r["expected"]["next"] == "review" and
                                       r.get("guarded", {}).get("next") == "review" for r in rows)
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--split", choices=("calibration", "heldout", "all"), default="all")
    parser.add_argument("--variant", choices=VARIANTS, default="normal")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    data = load(args.dataset)
    cases = [c for c in data["cases"] if args.split == "all" or c["split"] == args.split]
    cfg = decision.config() if args.live else None
    if cfg is not None:
        if not cfg.key or cfg.problem:
            parser.error("Jev is not configured; no live predictions recorded")
        cfg = replace(cfg, mode="active")  # experiment only; no settings are saved
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(sample, c, cfg, args.variant) for c in cases]
        for future in as_completed(pending):
            rows.append(future.result())
    order = {c["id"]: i for i, c in enumerate(cases)}
    rows.sort(key=lambda r: order[r["id"]])
    calls = [t for r in rows for t in r.get("trace", [])]
    elapsed = sorted(t["elapsed_ms"] for t in calls)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = {"schema": "review-routing-result/1", "dataset": {"id": data["id"], "version": data["version"],
              "sha256": sha(args.dataset.read_bytes()), "labels": data["labels"]},
              "run": {"started": started, **revision(), "harness_sha256": sha(Path(__file__).read_bytes())},
              "live": args.live, "split": args.split, "variant": args.variant,
              "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_digest(args.variant), "policy": POLICY.record(),
              "questions": questions(args.variant)[0], "option_mapping": questions(args.variant)[1],
              "model": cfg.model if cfg else None, "dispatch": "none: synthetic selection only; no approval",
              "usage": {"requests": sum(bool(t.get("sent")) for t in calls),
                        "input_tokens": sum((t.get("usage") or {}).get("input_tokens", 0) for t in calls),
                        "output_tokens": sum((t.get("usage") or {}).get("output_tokens", 0) for t in calls),
                        "median_ms": statistics.median(elapsed) if elapsed else None,
                        "max_ms": max(elapsed) if elapsed else None, "cost_usd": None},
              "summary": {s: summarize([r for r in rows if r["split"] == s]) for s in
                          ("calibration", "heldout") if any(r["split"] == s for r in rows)}, "rows": rows}
    out = args.out or RAW / f"review-routing-{args.split}-{args.variant}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"out": str(out), "summary": result["summary"], "usage": result["usage"]}, indent=1))
    return int(any(r.get("decision", {}).get("status") in ("unavailable", "invalid", "cancelled", "exhausted")
                   for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
