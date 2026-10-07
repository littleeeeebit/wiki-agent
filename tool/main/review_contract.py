"""Compose review lenses and evidence floors; recommendations never authorize work."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import decision
import refactor_profile
from common.budget import Budget

from . import decisions, query, verification

VERSION = 1
CRITERIA = {
    "refactor": "Preserve externally observable behavior, contracts, errors, side effects and concurrency; measure simplification, not new features.",
    "documentation": "Check factual accuracy, source support, runnable examples, links and consistency; do not demand implementation of future plans.",
    "security": "Check authentication, authorization, secret exposure and trust boundaries against explicit threat scenarios.",
    "data": "Check schema compatibility, migration, rollback, idempotency and loss/corruption scenarios.",
    "async": "Check cancellation, duplicate delivery, races, restart and resource lifetime.",
    "performance": "Require representative before/after measurements; correctness tests alone do not establish a speed improvement.",
}
EVIDENCE = {
    "offline": "Current-head focused checks; the full gate is still required before merge.",
    "api": "Registered isolated API flow receipts with actual requests and expected/observed assertions, bound to environment and head.",
    "browser": "Registered browser flow receipts with actual actions, measured build head and expected/observed assertions.",
    "desktop": "Actual native-host interaction evidence; a browser screenshot or mock is not desktop evidence.",
    "differential": "Frozen characterization checks rerun on this head; protected test inputs unchanged from the refactoring baseline.",
}
PROMPT = "Select only additional review obligations supported by the task and registered flows. These are shadow observations: never waive floors, decide readiness, invent commands or grant permissions. For each named facet choose add if needed, otherwise skip. Defer if evidence is insufficient."
POLICY = decision.Policy("review-contract-shadow-1", {"action": {"confidence": 0.6, "margin": 0.2}})
RUBRIC = {name: (query.ROOT / f"tool/prompts/review-{name}.md").read_text(encoding="utf-8").strip()
          for name in ("plan", "code")}


def profile_fields(repo: Path, block: dict) -> dict:
    """Validate the settled profile and additional obligations in one place."""
    from . import specs

    profile = block.get("review_profile") or "code"
    if profile not in specs.PROFILES:
        raise ValueError("`review_profile` 은 plan · code · mixed 중 하나여야 한다")
    root = block.get("artifact_root")
    if root is not None and (not isinstance(root, str) or not specs.inside(repo, root.strip())):
        raise ValueError("`artifact_root` 는 저장소 안의 폴더여야 한다")
    if profile == "plan" and not root:
        raise ValueError("plan 명세는 `artifact_root` 를 적어야 한다")
    root = root.strip().replace("\\", "/").strip("/") if root else None
    result = {"review_profile": profile, "review_profile_version": specs.PROFILE_VERSION, "artifact_root": root}
    if block.get("review") is not None:
        result["review"] = declared(block["review"])
    return result


def effective(spec: dict, paths: list[str] | None) -> str:
    """A plan leaving its prose artifact root widens to mixed, never code-free."""
    from . import specs

    asked = specs.profile_of(spec)
    if asked["review_profile"] != "plan":
        return asked["review_profile"]
    root = (asked["artifact_root"] or "").rstrip("/") + "/"
    explicit = declared(spec.get("review"))
    preservation_needed = "refactor" in explicit["criteria"] or "differential" in explicit["evidence"]
    return "plan" if not preservation_needed and paths and all(p.startswith(root) and prose(p) for p in paths) else "mixed"


def profiled(spec: dict, profile: str, head: str, base: str, base_oid: str) -> list[str]:
    """Retain the existing plan/code rubrics and planning requirement provenance."""
    from . import specs

    asked = specs.profile_of(spec)
    out = ["", "## Review profile", "",
           f"- Profile `{profile}`, version {asked['review_profile_version']}. Reviewed head `{head}`, base `{base}`"
           + (f" at merge base `{base_oid}`." if base_oid else ".")]
    if asked["review_profile"] != profile:
        out.append(f"- The spec asked for `{asked['review_profile']}` with artifact root `{asked['artifact_root']}`; "
                   "the change reaches past it, so the code criteria apply too.")
    planned = spec.get("planning") or {}
    if profile != "code" and planned.get("outline"):
        out += ["", "## Requirements", "", f"- R0: {spec['goal']}"]
        out += [f"- {r['id']}: {r['text']}" for r in planned["outline"]["requirements"]]
        out += ["", "## Source manifest", ""]
        out += [f"- {s['id']}: {s['title']} · {s['url']} · retrieved {s['retrieved']} · {s['locator']}"
                for s in planned.get("source_manifest") or []] or ["(none kept)"]
    elif profile != "code":
        out += ["", "## Requirements", "", f"- R0: {spec['goal']}"]
        out += [f"- R{i}: {d}" for i, d in enumerate(spec["done"], 1)]
        grounds = spec.get("grounds") or {}
        cited = [*grounds.get("pages", []), *grounds.get("files", []),
                 *(e["cite"] for e in grounds.get("evidence", []))]
        out += ["", "## Source manifest", "", *(f"- `{c}`" for c in cited or ["(none cited)"])]
    if profile != "code" and asked["artifact_root"]:
        out.append(f"- The plan's documents: `{asked['artifact_root']}/`")
    for name in ("plan", "code"):
        if profile in (name, "mixed"):
            out += ["", RUBRIC[name]]
    return out


def declared(value) -> dict:
    """Accept closed facets/flow IDs, never model-authored execution commands."""
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {"criteria", "evidence", "flows"}:
        raise ValueError("review 는 criteria · evidence · flows 목록만 받는다")
    out = {}
    for key, allowed in (("criteria", CRITERIA), ("evidence", EVIDENCE), ("flows", None)):
        items = value.get(key, [])
        if not isinstance(items, list) or any(not isinstance(i, str) or not i for i in items):
            raise ValueError(f"review.{key} 는 문자열 목록이어야 한다")
        if allowed is not None and any(i not in allowed for i in items):
            raise ValueError(f"등록되지 않은 review.{key} 항목이다")
        out[key] = sorted(set(items))
    return out


def prose(path: str) -> bool:
    """Executable Markdown retains code criteria, including inside a plan root."""
    return (path.endswith(".md") and path.rsplit("/", 1)[-1] not in ("AGENTS.md", "CLAUDE.md", "SKILL.md")
            and not path.startswith(("tool/", "web/", ".github/", ".wiki/", ".agents/", ".claude/",
                                     ".codex/", ".gemini/", "operator/", "prompts/")))


def preservation(spec: dict) -> tuple[Path | None, str]:
    """The refactoring owner's existing frozen check, not a second evaluator."""
    if not spec.get("refactor"):
        return None, ""
    from . import refactor

    run = refactor.load(spec["repo"], spec["refactor"].get("run", ""))
    if not run:
        return None, "리팩토링 실행 기록이 없다"
    if (run.get("tests") or {}).get("spec") == spec["id"]:
        return None, ""  # Characterization is implementation, not preservation of itself.
    step = next((s for s in run.get("steps", []) if s.get("spec") == spec["id"]), None)
    if step and step.get("kind"):
        return None, ""  # Cleanup/ratchet have their own verified finishing owner.
    if not step:
        return None, "리팩토링 단계가 실행 기록에 없다"
    file = refactor_profile.STORE / run["scope"] / spec["id"] / "frozen.json"
    return file, "" if file.is_file() else "동작 보존 검사의 고정 명세가 없다"


def preservation_command(spec: dict) -> str:
    file, problem = preservation(spec)
    if file is None or problem:
        return ""
    argv = [sys.executable, str(Path(refactor_profile.__file__).resolve()), "preserve", "--spec", str(file),
            "--sha256", verification.sha(file.read_bytes())]
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def signature(spec: dict) -> str:
    return verification.sha({k: spec.get(k) for k in ("rev", "goal", "done", "out", "grounds", "review",
                                                     "review_profile", "artifact_root", "refactor")})


def select(repo: Path, path: Path, spec: dict, profile: str, paths: list[str] | None,
           head: str, base_oid: str) -> dict:
    explicit = declared(spec.get("review"))
    criteria = {"plan", "code"} if profile == "mixed" else {profile}
    criteria.update(explicit["criteria"])
    evidence = {"offline", *explicit["evidence"]}
    problems, flows, manifest_digest, frozen_digest, preservation_inputs = [], [], "", "", None
    if paths and all(prose(p) for p in paths) and profile == "code":
        criteria.add("documentation")  # Conservative legacy code floor remains.
    frozen, problem = preservation(spec)
    if problem:
        problems.append(problem)
    if frozen is not None or "refactor" in criteria or "differential" in evidence:
        criteria.update(("code", "refactor"))
        evidence.add("differential")
        if frozen is None:
            problems.append("동작 보존 검사는 리팩토링 실행 소유자의 고정 명세가 필요하다")
    if frozen is not None and not problem:
        try:
            frozen_digest = verification.sha(frozen.read_bytes())
            tests = json.loads(frozen.read_text(encoding="utf-8"))["tests"]
            preservation_inputs = {"baseline": spec["start"], "tests": tests}
            from . import specs

            protected = specs.sh(["git", "--literal-pathspecs", "diff", "--exit-code", spec["start"], head, "--", *tests], path)
            if not tests or protected.returncode:
                problems.append("고정된 동작 보존 테스트가 바뀌었거나 기준선을 확인하지 못했다")
        except (OSError, ValueError, KeyError, TypeError):
            problems.append("동작 보존 명세를 확인하지 못했다")
    if verification.cloud(spec) or explicit["flows"] or evidence & {"api", "browser"}:
        try:
            contract, manifest_digest = verification.manifest(path)
            catalog = {f.id: f for f in contract.flows}
            unknown = set(explicit["flows"]) - catalog.keys()
            if unknown:
                problems.append("등록되지 않은 검증 흐름: " + ", ".join(sorted(unknown)))
            # Cloud's complete major-flow floor remains unchanged. Explicit local
            # flows only require their registered receipts, never grant execution.
            chosen = list(catalog) if verification.cloud(spec) else list(explicit["flows"])
            if evidence & {"api", "browser"} and not chosen:
                from . import specs

                mapped = paths and all(any(fnmatchcase(p, g) for f in contract.flows for g in f.paths)
                                      and not any(fnmatchcase(p, g) or fnmatchcase(p.rsplit("/", 1)[-1], g)
                                                  for g in specs.SHARED) for p in paths)
                chosen = [f.id for f in contract.flows if any(fnmatchcase(p, g) for p in paths for g in f.paths)] \
                    if mapped else list(catalog)  # Unreadable, shared or unmapped impact widens.
            flows = [catalog[i].model_dump() for i in sorted(set(chosen)) if i in catalog]
            evidence.update(f["kind"] for f in flows if f["kind"] != "command")
            if "browser" in evidence:
                evidence.add("api")  # Existing browser receipts require actual API requests too.
            if verification.cloud(spec) and verification.documents(path, base_oid, head):
                flows = []  # Existing, repository-declared prose exemption.
                evidence = {"offline", *explicit["evidence"]}
        except (OSError, ValueError):
            problems.append("등록된 검증 명세를 준비해야 한다")
    for kind in evidence & {"api", "browser"}:
        if not any(f["kind"] == kind or kind == "api" and f["kind"] == "browser" for f in flows):
            problems.append(f"{kind}: 필요한 동작을 검증하는 등록 흐름이 없다")
    if "desktop" in evidence:
        problems.append("네이티브 호스트 증거 검증 경로가 아직 없다 — 오프라인·브라우저 검사로 대체하지 않는다")
    out = {"version": VERSION, "head": head, "base_oid": base_oid, "spec_signature": signature(spec),
           "profile": profile, "criteria": sorted(criteria), "evidence": sorted(evidence), "flows": flows,
           "manifest_digest": manifest_digest, "frozen_digest": frozen_digest, "preservation": preservation_inputs,
           "problems": problems,
           "rubric_digest": verification.sha(RUBRIC),
           "catalog_digest": verification.sha({"criteria": CRITERIA, "evidence": EVIDENCE})}
    out["digest"] = verification.sha(out)
    return out


def ready(repo: Path, path: Path, spec: dict, contract: dict) -> str:
    if contract["problems"]:
        return "; ".join(contract["problems"])
    if not contract["base_oid"]:
        return "리뷰 대상 base 를 확인하지 못했다"
    if contract["flows"]:
        settings = verification.local(repo)
        if not settings:
            return "격리된 검증 환경의 로컬 설정을 준비해야 한다"
        if settings["manifest_digest"] != contract["manifest_digest"]:
            return "등록 검증 명세가 바뀌었다 — 로컬 설정에서 실행 범위를 다시 확인해야 한다"
        return verification.proven(repo, path, spec, contract["head"], contract["base_oid"],
                                   flow_ids=[f["id"] for f in contract["flows"]])
    return ""


def matches(spec: dict, record: dict, contract: dict) -> bool:
    old = record.get("review_contract")
    if old:
        return old.get("digest") == contract["digest"]
    return not spec.get("review") and not spec.get("refactor") and record.get("profile", "code") == contract["profile"]


def render(contract: dict, spec: dict | None = None, path: Path | None = None) -> list[str]:
    out = ["", "## Composed review contract", "",
           f"Contract v{VERSION} · `{contract['digest']}`. Criteria and evidence are separate obligations."]
    out += [f"- {name}: {CRITERIA[name]}" for name in contract["criteria"] if name in CRITERIA]
    out += ["", "### Required evidence", "", *[f"- {name}: {EVIDENCE[name]}" for name in contract["evidence"]]]
    out += [f"- Flow `{f['id']}` ({f['kind']}): {f['title']} — "
            + "; ".join(a["expected"] for a in f["assertions"]) for f in contract["flows"]]
    if contract["preservation"]:
        out += ["", "Frozen preservation inputs:", "```json", json.dumps(contract["preservation"]), "```"]
    if spec is not None and contract["flows"] and not verification.cloud(spec):
        from . import channels

        repo = channels.repo_for(spec["repo"])
        settings = verification.redaction(verification.local(repo), path) if repo is not None else {}
        ids = {f["id"] for f in contract["flows"]}
        rows = [{**{k: r.get(k) for k in ("id", "head", "executed_head", "signature")},
                 "reuse_reason": verification.redact(r.get("reuse_reason", ""), settings),
                 "evidence": verification.clean_evidence(r.get("evidence", {}), settings)}
                for r in (spec.get("local_verification") or {}).get("flows", []) if r.get("id") in ids]
        out += ["", "## Registered runtime evidence", "",
                "Check observed assertions against acceptance and the diff; exit zero alone is not proof. "
                "These receipts do not grant execution tools.", "", "```json",
                json.dumps(rows, ensure_ascii=False, indent=2), "```"]
    return out


def shadow(spec: dict, paths: list[str] | None, contract: dict, halt) -> dict:
    """One bounded observation, owned by the round; no detached background work."""
    cfg = decision.config()
    if cfg.mode == "off" or halt.is_set():
        return {"mode": "shadow", "status": "not_asked", "reason": "disabled_or_cancelled"}
    if cfg.problem or not cfg.key:
        return {"mode": "shadow", "status": "unavailable", "reason": cfg.problem or "missing_key"}
    budget = Budget(seconds=15, calls=1, candidates=0, cancel=halt)
    state = {"goal": spec.get("goal"), "done": spec.get("done"), "out": spec.get("out"),
             "paths": paths, "contract": contract}
    try:
        from . import channels

        repo = channels.repo_for(spec["repo"])
        if repo is not None:
            settings = verification.redaction(verification.local(repo), Path(spec.get("worktree") or repo))
            state = verification.sanitize(state, settings)
        state_en, normalization = decisions.normalized(state, max(0, budget.left() - budget.call_seconds))
        if state_en is None:
            return {"mode": "shadow", "status": "unavailable", "reason": "normalization_failed"}
        questions = {f"{family}:{name}": {"decision": "action", "candidate": None,
                     "question": decision.choice(f"{PROMPT} Facet: {family}/{name}: {about}",
                     {"add": "An additional obligation is needed.", "skip": "No additional obligation is needed.",
                      decision.DEFER: "Insufficient evidence."})}
                     for family, catalog in (("criteria", CRITERIA), ("evidence", EVIDENCE))
                     for name, about in catalog.items() if name not in contract[family]}
        prompt_version = "review-contract-shadow-1:" + verification.sha({"prompt": PROMPT, "questions": questions})[:16]
        req = decision.request("action", state_en, questions, allowed=["add", "skip"], model=cfg.model,
                               prompt_version=prompt_version, policy_version=POLICY.version,
                               normalization_version=normalization, budget=budget)
        res = decision.checked(req, decision.decide(req, decisions.transport(cfg), budget, [], POLICY))
        additions = sorted(k for k, v in res["answers"].items() if v.get("choice") == "add")
        return {"mode": "shadow", "status": res["status"], "request_id": req["request_id"],
                "reason": res["reason_code"], "model": res["model"], "usage": res["usage"], "elapsed_ms": res["elapsed_ms"],
                "answers": res["answers"], "verdicts": res["verdicts"], "additions": additions,
                "prompt_version": req["prompt_version"], "normalization_version": normalization,
                "policy": POLICY.record(), "budget": budget.record()}
    except Exception as exc:  # noqa: BLE001 — observation failure cannot alter the review.
        return {"mode": "shadow", "status": "unavailable", "reason": type(exc).__name__}


def merge_problem(repo: Path, path: Path, spec: dict, head: str, base_oid: str) -> str:
    from . import loop, specs

    allowed = specs.approved(spec) or {}
    old = allowed.get("review_contract")
    if not old and not spec.get("review") and not spec.get("refactor"):
        return ""  # Existing ordinary approvals remain compatible.
    paths = loop.changed(path, base_oid, head)
    current = select(repo, path, spec, loop.effective(spec, paths), paths, head, base_oid)
    if not old or old.get("digest") != current["digest"]:
        return "작업 관점·검증 의무가 바뀌었다 — 독립 리뷰를 다시 받아야 한다"
    if current["flows"] and allowed.get("local_verification_digest") != verification.evidence_identity(spec):
        return "리뷰 뒤 실행 증거가 바뀌었다 — 독립 리뷰를 다시 받아야 한다"
    return ready(repo, path, spec, current)
