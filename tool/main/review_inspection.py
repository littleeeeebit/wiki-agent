"""Bounded Jev routing before collection, and judgment over measured receipts."""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import decision
from common.budget import Budget, Cancelled, Exhausted

from . import decisions, review_contract, runtime, specs, verification

POLICY = decision.Policy("review-inspection-3", {"action": {"confidence": 0.6, "margin": 0.2}})
LIMITS = {"seconds": 15, "calls": 1, "candidates": 0}
PROMPT = {"selection": "Select the smallest set of checks that proves the current task's acceptance. "
          "A required flow must run. Path overlap is candidate context, NOT proof of behavioral relevance: "
          "shared settings, service, runner and support files must not select the whole catalog. "
          "Include only flows whose concrete assertions test behavior this task changes or explicitly preserves. "
          "Cover required.evidence using the fewest appropriate flows: a browser receipt also covers API. "
          "Existing passed receipts can be reused; run means include in required proof, not repeat execution. "
          "A previous failure remains recorded but does not require rerunning an unrelated check. "
          "Do not substitute a broad whole-repository suite for a missing task-specific check.",
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
        coverage = ["coverage:" + kind for kind in req["state_en"].get("required", {}).get("evidence", [])
                    if kind in {"api", "browser", "desktop"}] if options == {"run", "skip"} else []
        if (record["input_identity"] != binding or req["state_en"]["input_identity"] != binding
                or req["policy_version"] != POLICY.version or res["status"] != "decided"
                or verification.sha({"request": req, "result": res}) != record["frozen_digest"]
                or set(req["questions"]) != set(ids) | set(coverage)):
            return None
        decision.checked(req, res)
        out = {}
        for cid in [*ids, *coverage]:
            offered = options if cid in ids else set(req["questions"][cid]["question"]["criteria"]) - {decision.DEFER}
            if res["verdicts"][cid] != "yes" or decision.verdict(POLICY, "action", res["answers"][cid]) != "yes" \
                    or res["answers"][cid]["choice"] not in offered:
                return None
            out[cid] = res["answers"][cid]["choice"]
        picked = {out[cid] for cid in coverage}
        if picked - set(ids):
            return None
        return {cid: "run" if cid in picked else out[cid] for cid in ids}
    except (KeyError, TypeError, ValueError):
        return None


def selected(spec: dict, binding: str, flows: list[dict]) -> list[str] | None:
    rows = choices((spec.get("review_inspection") or {}).get("selection") or {}, binding,
                   [f["id"] for f in flows], {"run", "skip"})
    if rows is None:
        return None
    required = set(review_contract.declared(spec.get("review"))["flows"])
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
        options = {"run": "Include this flow in the minimum proof of acceptance and required evidence kinds; reuse valid receipts.",
                   "skip": "Outside this task, unnecessarily broad, or another selected flow covers the same evidence kind."} if stage == "selection" else {
            "covered": "Observed evidence proves every assertion.", "insufficient": "Evidence does not prove acceptance."}
        questions = {}
        for cid in ids:
            flow = next(f for f in state_en["flows"] if f["id"] == cid)
            scope = json.dumps({k: flow[k] for k in ("required", "title", "assertions", "previous_outcome")}) if stage == "selection" else ""
            questions[cid] = {"decision": "action", "candidate": None, "question": decision.choice(
                f"{PROMPT[stage]} Decide only for flow {cid} in state.flows. Its scope facts: {scope}.",
                {**options, decision.DEFER: "Insufficient grounds to decide."})}
        if stage == "selection":
            for kind in state_en["required"]["evidence"]:
                if kind not in {"api", "browser", "desktop"}:
                    continue
                offered = {f["id"]: f["title"] for f in state_en["flows"] if f["kind"] == kind
                           or kind == "api" and f["kind"] == "browser"}
                questions["coverage:" + kind] = {"decision": "action", "candidate": None, "question": decision.choice(
                    f"The spec requires {kind} evidence. Choose ONE registered flow closest to the current acceptance, "
                    "preferring existing passing receipts. This covers the declared evidence kind without running "
                    "every flow of that kind. Defer if no offered flow can supply it.",
                    {**offered, decision.DEFER: "No appropriate registered evidence."})}
        req = decision.request("action", state_en, questions, allowed=[*options, *ids], model=cfg.model,
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
    total = runtime.verification_budget.get()
    budget = Budget(**{**LIMITS, "seconds": min(LIMITS["seconds"], total.left()) if total else LIMITS["seconds"]}, cancel=halt)
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
    saved = spec.get("local_verification") or {}
    previous = {r["id"]: r for r in [*saved.get("excluded_flows", []), *saved.get("flows", [])]}
    for flow in flows:
        flow["required"] = flow["id"] in required["flows"]
        before = previous.get(flow["id"], {})
        flow["previous_outcome"] = "passed" if before.get("ok") else "failed_or_incomplete" if before else "not_run"
        patterns = flow.pop("paths")
        flow["changed_paths"] = [p for p in contract["inspection_paths"] or []
                                 if any(fnmatchcase(p, pattern) for pattern in patterns)]
    # Only the prose grounds can carry runtime values. Redacting identities, paths and
    # registered ids would let a short `.env` value such as `1` break the replay binding.
    state = {"input_identity": binding, "paths": contract["inspection_paths"],
             "flows": flows, "grounds": verification.sanitize(grounds, settings), "required": required,
             "head": contract["head"], "base": contract["base_oid"]}
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
        raise ValueError("Jev 검증 선택 대기 — " + (observation.get("reason") or "명시된 필수 흐름 또는 근거 선택이 빠졌다"))
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
    # `clean_evidence` already redacts the measured text; the rest is protocol metadata.
    state = {"input_identity": binding,
        "flows": [{"id": f["id"], "assertions": f["assertions"], "evidence": verification.clean_evidence(
            rows[f["id"]]["evidence"], settings)} for f in contract["flows"]],
        "grounds": [{"id": f["id"], "locator": "Measured receipt for " + f["id"],
                     "text": "See this flow's measured requests, actions and observations in state.flows."}
                    for f in contract["flows"]]}
    # Measured control names and quoted filenames are literals, not prose to translate.
    for flow in state["flows"]:
        for key in ("observations", "actions"):
            for row in flow["evidence"][key]:
                for name in set(re.findall(r"'([^'\n]+\.[A-Za-z0-9]{1,10})'", row["actual"])):
                    row["actual"] = row["actual"].replace(name, f"`{name}`")
                row["actual"] = re.sub(r"([ᄀ-ᇿ㄰-㆏가-힯]+)(?= shown\b)", r"`\1`", row["actual"])
    total = runtime.verification_budget.get()
    observation = ask(repo, state, ids, "judgment", halt, Budget(
        **{**LIMITS, "seconds": min(LIMITS["seconds"], total.left()) if total else LIMITS["seconds"]}, cancel=halt))
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
    with runtime.verification_scope(halt) as budget:
        try:
            spec = _collect(repo, path, spec, head, base, halt)
            budget.check()
            return spec
        except Cancelled:
            # The caller handles a halt; what `execute` recorded (`interrupted`) stands.
            return specs.load(spec["repo"], spec["id"]) or spec
        except Exhausted as exc:
            reason = "로컬 검증 전체 시간 제한을 넘었다 — 완료된 증거를 보존했다"
            verification.pending(repo, spec, head, reason)
            raise ValueError(reason) from exc


def _collect(repo: Path, path: Path, spec: dict, head: str, base: str, halt) -> dict:
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
