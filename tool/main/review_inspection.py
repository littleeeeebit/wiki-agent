"""Bounded Jev routing before collection, and judgment over measured receipts."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path

import decision
from common.budget import Budget, Cancelled, Exhausted

from . import decisions, review_contract, specs, verification

POLICY = decision.Policy("review-inspection-2", {"action": {"confidence": 0.6, "margin": 0.2}})
LIMITS = {"seconds": 15, "calls": 1, "candidates": 0}
PROMPT = {"selection": "Select checks to execute, without judging whether they pass. A required flow or "
          "a flow with changed_paths must run. Skip only a flow unrelated to the change and acceptance.",
          "judgment": "Judge this flow's measured receipt against all its assertions. Covered means every "
          "assertion is observed; insufficient means acceptance is not proved. Never invent observations."}


def diff_context(text: str, limit: int = 10000) -> dict:
    """Expose every changed file rather than spending the entire budget on early docs."""
    parts = text.split("\ndiff --git ")
    parts = [parts[0], *("diff --git " + p for p in parts[1:])]
    if len(text) <= limit:
        return {"text": text, "truncated": False, "files": len(parts) if text else 0}
    allowance = max(1, limit // max(1, len(parts)))
    return {"text": "\n".join(p[:allowance] for p in parts)[:limit], "truncated": len(text) > limit,
            "files": len(parts) if text else 0}


def identity(spec: dict, head: str, base: str, digest: str, paths, flows: list[dict]) -> str:
    return verification.sha({"policy": POLICY.record(), "prompt": PROMPT, "head": head, "base": base,
                             "spec": review_contract.signature(spec), "manifest": digest,
                             "paths": paths, "flows": flows})


def choices(record: dict, binding: str, ids: list[str], options: set[str]) -> dict | None:
    """Replay the typed result; summaries and model confidence alone grant nothing."""
    try:
        req, res = record["request"], record["result"]
        if (record["input_identity"] != binding or req["state_en"]["input_identity"] != binding
                or req["policy_version"] != POLICY.version or res["status"] != "decided"
                or verification.sha({"request": req, "result": res}) != record["frozen_digest"]
                or set(req["questions"]) != set(ids)):
            return None
        decision.checked(req, res)
        out = {}
        for cid in ids:
            if res["verdicts"][cid] != "yes" or decision.verdict(POLICY, "action", res["answers"][cid]) != "yes" \
                    or res["answers"][cid]["choice"] not in options:
                return None
            out[cid] = res["answers"][cid]["choice"]
        return out
    except (KeyError, TypeError, ValueError):
        return None


def selected(spec: dict, binding: str, flows: list[dict]) -> list[str] | None:
    rows = choices((spec.get("review_inspection") or {}).get("selection") or {}, binding,
                   [f["id"] for f in flows], {"run", "skip"})
    if rows is None:
        return None
    required = set(review_contract.declared(spec.get("review"))["flows"])
    record = spec.get("local_verification") or {}
    required.update(r["id"] for r in record.get("flows", []) if not r.get("ok"))
    chosen = [cid for cid, action in rows.items() if action == "run"]
    return chosen if chosen and required <= set(chosen) else None


def ask(repo: Path, state: dict, ids: list[str], stage: str, halt, budget: Budget) -> dict:
    cfg = decision.config(repo)
    out = {"engine": "jev", "stage": stage, "input_identity": state["input_identity"],
           "status": "unavailable", "model": cfg.model}
    try:
        if cfg.mode != "active" or cfg.problem or not cfg.key:
            return {**out, "reason": cfg.problem or ("active_jev_required" if cfg.key else "missing_key")}
        if len(json.dumps(state, ensure_ascii=False)) > 32000:
            raise ValueError("context_too_large")
        budget.check(budget.call_seconds)
        # Translating code would change literals/selectors and spend the preparation
        # budget on source syntax. Mask non-English literals; normalize acceptance.
        normalized_input = {**state, "grounds": [{**g, "text": decisions.HANGUL.sub("…", g["text"])}
            if g["id"] == "diff" else g for g in state["grounds"]]}
        state_en, normalization = decisions.normalized(normalized_input, budget.left() - budget.call_seconds, cancel=halt)
        if state_en is None:
            raise ValueError("normalization_failed")
        if len(json.dumps(state_en, ensure_ascii=False)) > 32000:
            raise ValueError("normalized_context_too_large")
        options = {"run": "required is true, changed_paths is nonempty, or acceptance explicitly requests this flow.",
                   "skip": "required is false, changed_paths is empty, and acceptance does not request this flow."} if stage == "selection" else {
            "covered": "Observed evidence proves every assertion.", "insufficient": "Evidence does not prove acceptance."}
        questions = {}
        for cid in ids:
            flow = next(f for f in state_en["flows"] if f["id"] == cid)
            scope = json.dumps({k: flow[k] for k in ("required", "changed_paths")}) if stage == "selection" else ""
            questions[cid] = {"decision": "action", "candidate": None, "question": decision.choice(
                f"{PROMPT[stage]} Decide only for flow {cid} in state.flows. Its scope facts: {scope}.",
                {**options, decision.DEFER: "Insufficient grounds to decide."})}
        req = decision.request("action", state_en, questions, allowed=list(options), model=cfg.model,
                               prompt_version=POLICY.version + ":" + stage, policy_version=POLICY.version,
                               normalization_version=normalization, budget=budget)
        if len(json.dumps(req, ensure_ascii=False).encode("utf-8")) > 90000:
            raise ValueError("request_too_large")
        trace = []
        res = decision.checked(req, decision.decide(req, decisions.transport(cfg), budget, trace, POLICY))
        budget.check()
        reason = res["reason_code"] or ("uncertain: " + ", ".join(
            key for key, verdict in res["verdicts"].items() if verdict != "yes") if res["status"] == "uncertain" else "")
        return {**out, "status": res["status"], "reason": reason, "request": req, "result": res,
                "frozen_digest": verification.sha({"request": req, "result": res}),
                "budget": budget.record(), "trace": trace, "elapsed_ms": res["elapsed_ms"]}
    except (Cancelled, Exhausted, ValueError) as exc:
        return {**out, "status": "cancelled" if isinstance(exc, Cancelled) else "exhausted"
                if isinstance(exc, Exhausted) else "unavailable", "reason": str(exc), "budget": budget.record()}


def prepare(repo: Path, path: Path, spec: dict, contract: dict, halt) -> tuple[dict, dict]:
    if not contract["flows"] or contract["problems"]:
        return spec, contract
    binding = contract["inspection_identity"]
    saved = (spec.get("review_inspection") or {}).get("selection") or {}
    cfg = decision.config(repo)
    if contract.get("inspection_selected") and saved.get("model") == cfg.model and cfg.mode == "active" and cfg.key and not cfg.problem:
        return spec, contract
    budget = Budget(**LIMITS, cancel=halt)
    diff = specs.sh(["git", "diff", "--no-ext-diff", "--no-textconv", "--unified=3",
                     contract["base_oid"], contract["head"], "--"], path, timeout=min(2, budget.left()))
    if diff.returncode:
        raise ValueError("Jev 검증 선택 대기 — 변경 코드를 읽지 못했다")
    settings = verification.redaction(verification.local(repo), path)
    flows = [{k: f[k] for k in ("id", "title", "kind", "paths", "assertions")} for f in contract["inspection_catalog"]]
    grounds = [{"id": "acceptance", "locator": "Current spec acceptance", "text": json.dumps(
        {k: spec.get(k) for k in ("goal", "done", "out", "review")}, ensure_ascii=False)},
        {"id": "diff", "locator": "Actual reviewed diff; bounded excerpts from every changed file",
         **diff_context(diff.stdout)}]
    required = review_contract.declared(spec.get("review"))
    required["flows"] = sorted(set(required["flows"]) | {r["id"] for r in (
        spec.get("local_verification") or {}).get("flows", []) if not r.get("ok")})
    for flow in flows:
        flow["required"] = flow["id"] in required["flows"]
        patterns = flow.pop("paths")
        flow["changed_paths"] = [p for p in contract["inspection_paths"] or []
                                 if any(fnmatchcase(p, pattern) for pattern in patterns)]
    state = verification.sanitize({"input_identity": binding, "paths": contract["inspection_paths"],
        "flows": flows, "grounds": grounds, "required": required,
        "head": contract["head"], "base": contract["base_oid"]}, settings)
    observation = ask(repo, state, [f["id"] for f in flows], "selection", halt, budget)
    with specs._files:
        fresh = specs.load(spec["repo"], spec["id"])
        if fresh is None or review_contract.signature(fresh) != contract["spec_signature"]:
            raise ValueError("Jev 검증 선택 도중 명세가 바뀌었다")
        spec = specs.update(spec["repo"], spec["id"], review_inspection={
            "head": contract["head"], "selection": observation})
    from . import loop

    paths = loop.changed(path, contract["base_oid"], contract["head"])
    current = review_contract.select(repo, path, spec, review_contract.effective(spec, paths), paths,
                                     contract["head"], contract["base_oid"])
    if not current.get("inspection_selected"):
        raise ValueError("Jev 검증 선택 대기 — " + (observation.get("reason") or "필수·실패 흐름이 빠졌다"))
    return spec, current


def judgment_problem(spec: dict, contract: dict) -> str:
    if not contract.get("inspection_selected"):
        return "Jev 검증 선택을 준비해야 한다"
    binding = verification.sha({"selection": contract["digest"], "evidence": verification.evidence_identity(spec)})
    record = (spec.get("review_inspection") or {}).get("judgment") or {}
    rows = choices(record, binding, [f["id"] for f in contract["flows"]], {"covered", "insufficient"})
    return "" if rows and all(v == "covered" for v in rows.values()) else "Jev 실행 증거 판단 대기"


def assess(repo: Path, path: Path, spec: dict, contract: dict, halt) -> dict:
    if not contract["flows"] or not judgment_problem(spec, contract):
        return spec
    ids = [f["id"] for f in contract["flows"]]
    if problem := verification.proven(repo, path, spec, contract["head"], contract["base_oid"], flow_ids=ids):
        raise ValueError(problem)
    binding = verification.sha({"selection": contract["digest"], "evidence": verification.evidence_identity(spec)})
    rows = {r["id"]: r for r in spec["local_verification"]["flows"]}
    settings = verification.redaction(verification.local(repo), path)
    state = verification.sanitize({"input_identity": binding,
        "flows": [{"id": f["id"], "assertions": f["assertions"], "evidence": verification.clean_evidence(
            rows[f["id"]]["evidence"], settings)} for f in contract["flows"]],
        "grounds": [{"id": f["id"], "locator": "Measured receipt for " + f["id"],
                     "text": "See this flow's measured requests, actions and observations in state.flows."}
                    for f in contract["flows"]]}, settings)
    observation = ask(repo, state, ids, "judgment", halt, Budget(**LIMITS, cancel=halt))
    with specs._files:
        fresh = specs.load(spec["repo"], spec["id"])
        if fresh is None or review_contract.signature(fresh) != contract["spec_signature"] \
                or verification.evidence_identity(fresh) != verification.evidence_identity(spec):
            raise ValueError("Jev 증거 판단 도중 증거·명세가 바뀌었다")
        spec = specs.update(spec["repo"], spec["id"], review_inspection={
            **fresh.get("review_inspection", {}), "judgment": observation})
    if problem := judgment_problem(spec, contract):
        raise ValueError(problem + ": " + observation.get("reason", "insufficient evidence"))
    return spec


def collect(repo: Path, path: Path, spec: dict, head: str, base: str, halt) -> dict:
    contract = current(repo, path, spec, head, base)
    spec, contract = prepare(repo, path, spec, contract, halt)
    if contract["problems"]:
        raise ValueError("; ".join(contract["problems"]))
    ids = [f["id"] for f in contract["flows"]]
    if verification.proven(repo, path, spec, head, base, flow_ids=ids if ids else None):
        spec = verification.pending(repo, spec, head, "Jev 선택 흐름의 로컬 검증을 시작한다", "running")
        spec = verification.execute(repo, spec, path, head, base, halt,
                                    flow_ids=ids if ids else None, enforced_digest=contract["digest"] if ids else "")
    if not halt.is_set() and spec["local_verification"]["state"] in ("runtime_passed", "verified"):
        spec = assess(repo, path, spec, contract, halt)
    return spec


def current(repo: Path, path: Path, spec: dict, head: str, base: str) -> dict:
    from . import loop

    paths = loop.changed(path, base, head)
    return review_contract.select(repo, path, spec, review_contract.effective(spec, paths), paths, head, base)
