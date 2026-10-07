"""Pure audit composition and redacted record views; execution remains in review_contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import decision


def compose(contract: dict, observation: dict | None = None) -> dict:
    """Rebuild the audit union from frozen choices, never from an additions list."""
    from . import verification
    from .review_contract import CRITERIA, EVIDENCE, close, coverage, include, replay_shadow, selected_sets

    out = copy.deepcopy(contract)
    if observation is not None:
        out["shadow"] = copy.deepcopy(observation)
    items = {r["id"]: {**r, "membership": ["enforced"],
             "grounds": [g for g in r["grounds"] if g["scope"] == "enforced"]}
             for r in contract["items"] if "enforced" in r["membership"]}
    for row in items.values():
        row["membership"].append("candidate")
    unresolved = [r for r in contract["unresolved"] if r["scope"] == "enforced"]
    dispositions, flows = list(contract.get("enforced_rejections", [])), list(contract["flows"])
    record = out.get("shadow") or {}
    if record.get("request") and record.get("result"):
        try:
            replay = replay_shadow(record)
            req = record["request"]
            if record["input_identity"] != contract["digest"] or record["status"] not in ("decided", "uncertain"):
                raise ValueError(record.get("reason") or "stale_or_unavailable_observation")
            state = req["state_en"]
            offered = {g["id"] for g in state["grounds"]}
            catalog = {f["id"]: f for f in state["registered_flows"]}
            if len(catalog) != len(state["registered_flows"]):
                raise ValueError("duplicate offered flow IDs")
            for flow in catalog.values():
                assertions = [verification.Assertion.model_validate(a).id for a in flow["assertions"]]
                if (flow["kind"] not in ("api", "browser", "command") or "command" in flow
                        or not assertions or len(set(assertions)) != len(assertions)):
                    raise ValueError("invalid offered flow catalog")
            manifest = state["manifest"]
            for rec in replay["recommendations"]:
                cid = rec["candidate_id"]
                axis, name = cid.split(":", 1)
                reason = ""
                if cid not in req["questions"] or axis not in ("criteria", "evidence", "flow") or (
                        axis == "criteria" and name not in CRITERIA or axis == "evidence" and name not in EVIDENCE
                        or axis == "flow" and name not in catalog):
                    reason = "unknown candidate"
                elif rec["choice"] == decision.DEFER or rec["verdict"] == "uncertain":
                    reason = "deferred or uncertain recommendation"
                    unresolved.append({"item_id": cid, "scope": "candidate", "reason": reason})
                elif not rec["basis_refs"] or not set(rec["basis_refs"]) <= offered:
                    reason = "unsupported source references"
                elif axis == "flow" and (manifest["status"] != "validated" or contract["manifest_digest"]
                                         and manifest["digest"] != contract["manifest_digest"]):
                    reason = "manifest changed or unavailable"
                disposition = "unresolved" if reason.startswith("deferred") else "rejected" if reason else (
                    "accepted" if rec["choice"] == "add" and rec["verdict"] == "yes" else "skipped")
                dispositions.append({**rec, "disposition": disposition, "reason": reason})
                if disposition != "accepted":
                    continue
                include(items, cid, "candidate", {"origin": "jev-shadow", "request_id": req["request_id"],
                        "request_version": req["schema_version"],
                        "prompt_version": req["prompt_version"], "policy_version": req["policy_version"],
                        "candidate_id": cid, "basis_refs": rec["basis_refs"], "disposition": "accepted",
                        "context_digest": record["context_digest"]})
                if axis == "flow":
                    flows.append(catalog[name])
                    include(items, cid, "candidate", {"origin": "manifest", "flow_id": name,
                            "input_digest": manifest["digest"], "assertions": [a["id"] for a in catalog[name]["assertions"]],
                            "reason": "validated offered shadow flow"})
        except (ValueError, KeyError, TypeError) as exc:
            dispositions.append({"candidate_id": None, "disposition": "rejected", "reason": str(exc)})
    elif record:
        dispositions.append({"candidate_id": None, "disposition": record.get("status"), "reason": record.get("reason")})
    close(items, "candidate", flows)
    out["candidate"] = selected_sets(items, "candidate")
    selected_flows = [f for f in flows if f["id"] in out["candidate"]["flows"]]
    unresolved += [{**r, "scope": "candidate"} for r in coverage(out["candidate"], selected_flows, out["preservation"])
                   if {**r, "scope": "enforced"} not in unresolved]
    out["items"] = sorted(items.values(), key=lambda r: r["id"])
    for row in out["items"]:
        row["grounds"].sort(key=lambda g: json.dumps(g, sort_keys=True))
    out["unresolved"] = sorted(unresolved, key=lambda r: (r["scope"], r["item_id"] or "", r["reason"]))
    out["dispositions"] = dispositions
    out["candidate_digest"] = verification.sha({"enforced_digest": out["digest"], "candidate": out["candidate"],
            "items": out["items"], "unresolved": out["unresolved"], "dispositions": dispositions, "observation": record})
    return out


def view(repo: Path, spec: dict, allowed: dict | None) -> dict:
    """Expose current and legacy contracts through the existing spec response."""
    from . import verification

    record = spec.get("review_contract") or (allowed or {}).get("review_contract")
    status = "available" if record and record.get("version") == 2 else "unavailable"
    settings = verification.redaction(verification.local(repo), Path(spec.get("worktree") or repo))
    record = verification.sanitize({**record, "provenance_status": status}, settings) if record else None
    return {"review_contract": record, "review_provenance_status": status}
