"""Approved local evidence; Cloud failures never dispatch local implementation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

import native_verification

from common.settings import unquote
from common import process
from common.budget import Budget, Cancelled, Exhausted
from . import query, runtime, specs
from .runtime import prepare

MANIFEST = "verification.json"
LOCAL = ".wiki/verification.local.json"
CONTEXT = "wiki-agent/local-verification"
router = APIRouter()
_configuration = threading.RLock()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Assertion(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    expected: str = Field(min_length=1)


class Flow(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1)
    kind: Literal["api", "browser", "command", "desktop"]
    command: str = Field(min_length=1)
    paths: list[str] = Field(min_length=1)
    environments: list[str] = Field(default_factory=lambda: ["api", "dataset", "settings"], min_length=1)
    assertions: list[Assertion] = Field(min_length=1)
    native: native_verification.NativeTarget | None = None

    @model_validator(mode="after")
    def native_contract(self):
        if self.kind == "desktop":
            if self.native is None or self.command != "wiki-agent-native":
                raise ValueError("Desktop flows require the registered wiki-agent-native runner and target")
            expected = {a.id: a.expected for a in self.assertions}
            if len(self.native.actions) != len(expected) or {a.assertion: a.expected for a in self.native.actions} != expected:
                raise ValueError("Native actions must cover each registered assertion exactly once")
        elif self.native is not None:
            raise ValueError("Only desktop flows accept a native target")
        return self


class Manifest(Contract):
    version: Literal[1, 2]
    contracts: list[str] = Field(min_length=1)
    flows: list[Flow] = Field(min_length=1)
    prose_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def versioned_native(self):
        if self.version == 1 and any(f.kind == "desktop" for f in self.flows):
            raise ValueError("Desktop flows require verification manifest version 2")
        return self


class LocalSettings(Contract):
    version: Literal[1, 2] = 1
    environment_id: str = Field(min_length=1)
    test_scope: str = Field(min_length=1)
    env_file: str = Field(min_length=1)
    browser_tool: str = Field(min_length=1)
    allowed_origins: list[str] = Field(min_length=1)
    revisions: dict[str, str]
    setup: str = ""
    cleanup: str = ""
    redact_values: list[str] = Field(default_factory=list)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    native: native_verification.NativeSettings | None = None

    @model_validator(mode="after")
    def versioned_native(self):
        if self.native is not None and self.version != 2:
            raise ValueError("Native scope approval requires local settings version 2")
        return self


class Handoff(Contract):
    version: Literal[1]
    implementation: Literal["claude-code-cloud"]
    head: str = Field(pattern=r"^[0-9a-f]{40}$")
    summary: str = Field(min_length=1)
    run: list[str] = Field(min_length=1)
    checks: list[str]
    unverified: list[str]


class PreparationError(ValueError):
    """A script reports missing prerequisites, not broken functionality."""


def cloud(spec: dict) -> bool:
    return spec.get("implementation_environment") == "claude-cloud"


def sha(value: bytes | dict) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()).hexdigest()


def within(root: Path, relative: str) -> Path:
    file = root / relative
    if Path(relative).is_absolute() or root.resolve() not in file.resolve().parents or file.is_symlink():
        raise ValueError(f"저장소 밖의 경로는 받지 않는다: {relative}")
    return file


def manifest(path: Path, *, budget=None) -> tuple[Manifest, str]:
    def tracked(ref):
        if budget is not None:
            budget.check()
        try:
            done = specs.sh(["git", "ls-files", "--error-unmatch", "--", ref], path,
                            timeout=budget.left() if budget is not None else 60)
        except subprocess.TimeoutExpired:
            if budget is not None:
                budget.check()
            raise
        if budget is not None:
            budget.check()
        return done

    file = within(path, MANIFEST)
    if tracked(MANIFEST).returncode:
        raise ValueError("verification.json 을 저장소에 커밋해야 한다")
    data = file.read_bytes()
    parsed = Manifest.model_validate_json(data)
    if any(not p for p in parsed.prose_paths):
        raise ValueError("문서 전용 영향 경로는 비어 있을 수 없다")
    if len({f.id for f in parsed.flows}) != len(parsed.flows):
        raise ValueError("주요 흐름의 id 가 중복되었다")
    for flow in parsed.flows:
        if len({a.id for a in flow.assertions}) != len(flow.assertions) or any(not p for p in flow.paths):
            raise ValueError(f"{flow.id}: 검증 항목 id 또는 영향 경로를 확인한다")
    for ref in parsed.contracts:
        file = within(path, ref)
        if not file.is_file() or tracked(ref).returncode:
            raise ValueError(f"저장소에 커밋된 API·데이터 명세가 필요하다: {ref}")
    return parsed, sha(data)


def local(repo: Path) -> dict:
    try:
        return LocalSettings.model_validate_json(within(repo, LOCAL).read_bytes()).model_dump()
    except (OSError, ValueError):
        return {}


def enabled(repo: Path) -> bool:
    return bool(local(repo)) or (repo / ".wiki/verification.protection.json").is_file()


def handoff(body: str, head: str) -> dict:
    blocks = re.findall(r"^```cloud-handoff[ \t]*\r?\n(.*?)^```[ \t]*$", body, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError("PR 본문에 cloud-handoff 블록이 하나 있어야 한다")
    parsed = Handoff.model_validate_json(blocks[0])
    if parsed.head != head:
        raise ValueError("PR 인계가 현재 커밋을 가리키지 않는다 — 클라우드에서 갱신한다")
    return parsed.model_dump()


def documents(path: Path, base_oid: str, head: str) -> bool:
    if not base_oid:
        return False
    done = specs.sh(["git", "diff", "--name-only", "--no-renames", base_oid, head], path)
    paths = done.stdout.splitlines()
    try:
        contract, _ = manifest(path)
    except (OSError, ValueError):
        return False
    # Only repository-designated prose is exempt; contracts/runtime inputs are not.
    return not done.returncode and bool(paths) and all(
        any(fnmatchcase(p, g) for g in contract.prose_paths)
        and p not in contract.contracts
        and not any(fnmatchcase(p, g) for f in contract.flows for g in f.paths)
        and p.endswith(".md") and not p.startswith(("tool/", "web/", ".github/", ".wiki/", ".agents/",
                                               ".claude/", ".codex/", ".gemini/", "operator/", "prompts/"))
        and p.rsplit("/", 1)[-1] not in ("AGENTS.md", "CLAUDE.md", "SKILL.md") for p in paths)


def hidden_values(settings: dict) -> list[str]:
    values = list(settings.get("redact_values") or [])
    try:
        # Redact duplicates too: project dotenv loaders may choose another value.
        for line in Path(settings["env_file"]).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                value = unquote(line.split("=", 1)[1].strip())
                if value:
                    values += [value, unquote(value)]
    except (OSError, ValueError, KeyError):
        pass
    values += [v for k, v in os.environ.items() if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", k, re.I) and v]
    return sorted({v for v in values if v}, key=len, reverse=True)


def redact(text: str, settings: dict) -> str:
    for value in hidden_values(settings):
        # Logs can contain JSON-escaped values as well as literal values.
        for representation in {value, json.dumps(value, ensure_ascii=False)[1:-1]}:
            text = text.replace(representation, "[redacted]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*|bearer\s+)[^\s,\"']+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:password|token|secret|api[_-]?key)\s*[\"']?\s*[:=]\s*[\"']?)[^\s,\"']+",
                  r"\1[redacted]", text)
    return text


def redaction(settings: dict, path: Path) -> dict:
    """Freeze known values before a command or reviewer can remove their files."""
    return {**settings, "redact_values": [*hidden_values(settings),
            *hidden_values({**settings, "env_file": str(path / ".env")})]}


def sanitize(value, settings: dict):
    """Redact strings without damaging JSON types or escaped content."""
    if isinstance(value, str):
        return redact(value, settings)
    if isinstance(value, list):
        return [sanitize(v, settings) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v, settings) for k, v in value.items()}
    return value


def clean_evidence(data: dict, settings: dict) -> dict:
    """Redact private runtime prose/URLs without corrupting assertion ids or JSON types."""
    if not data:
        return {}
    kept = {"observations": [{**{k: r[k] for k in ("id", "expected", "pass")},
                              "actual": redact(r["actual"], settings)} for r in data.get("observations", [])],
            "requests": [{**{k: r[k] for k in ("method", "status")}, "url": redact(r["url"], settings)}
                         for r in data.get("requests", [])],
            "actions": sanitize(data.get("actions", []), settings)}
    if data.get("native"):
        native = data["native"]
        kept["native"] = {**native, "actions": [{**a, "actual": redact(a["actual"], settings)}
                                               for a in native["actions"]]}
    return kept


def signature(repo: Path, path: Path, flow: Flow, settings: dict) -> str:
    source = Path(settings["env_file"])
    content = source.read_bytes()
    if (path / ".env").read_bytes() != content:
        raise ValueError("실제로 실행할 .env 가 승인된 로컬 원본과 다르다")
    identity = {"flow": flow.model_dump(), "environment_id": settings["environment_id"],
                "test_scope": settings["test_scope"], "browser_tool": settings["browser_tool"],
                "origins": settings["allowed_origins"], "setup": settings["setup"], "cleanup": settings["cleanup"],
                "revisions": {k: settings["revisions"].get(k) for k in flow.environments},
                "env": sha(content), "runtime": specs.digest(repo, path, flow.command)}
    if flow.kind == "desktop":
        if not settings.get("native"):
            raise ValueError("승인된 Windows 네이티브 테스트 범위를 준비해야 한다")
        identity["native"] = settings["native"]
        identity["collector"] = sha(Path(native_verification.__file__).read_bytes())
        identity["executable"] = sha(Path(settings["native"]["executable"]).read_bytes())
    return sha(identity)


def receipt(output: str, flow: Flow, head: str, settings: dict,
            *, path: Path | None = None, artifact: Path | None = None) -> dict:
    blocks = re.findall(r"^```local-evidence[ \t]*\r?\n(.*?)^```[ \t]*$", output, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError("실행 결과에 local-evidence 블록이 하나 필요하다")
    data = json.loads(blocks[0])
    if not isinstance(data, dict) or data.get("head") != head or data.get("flow") != flow.id:
        raise ValueError("검증 증거의 커밋·흐름이 실행 대상과 다르다")
    if data.get("environment_id") != settings["environment_id"] or data.get("test_scope") != settings["test_scope"]:
        raise ValueError("검증 증거의 환경·테스트 범위가 설정과 다르다")
    blocked = data.get("blocked")
    if blocked is not None:
        if not isinstance(blocked, dict) or blocked.get("prerequisite") not in (
                "api", "dataset", "authentication", "browser", "setup", "native") \
                or not isinstance(blocked.get("reason"), str) or not blocked["reason"].strip():
            raise ValueError("검증 준비 대기에는 부족한 전제와 이유가 필요하다")
        raise PreparationError(redact(blocked["prerequisite"] + ": " + blocked["reason"], settings))
    rows = data.get("observations")
    expected = {a.id: a.expected for a in flow.assertions}
    if not isinstance(rows, list) or len(rows) != len(expected) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("모든 검증 항목의 관찰 결과가 필요하다")
    if {r.get("id") for r in rows} != set(expected):
        raise ValueError("검증 항목의 id 가 명세와 다르다")
    for row in rows:
        if row.get("expected") != expected[row["id"]] or type(row.get("pass")) is not bool \
                or not isinstance(row.get("actual"), str) or not row["actual"].strip():
            raise ValueError("기대 결과와 실제 관찰, 판정을 모두 기록한다")
    requests, actions = data.get("requests", []), data.get("actions", [])
    if not isinstance(requests, list) or (flow.kind in ("api", "browser") and not requests):
        raise ValueError("실제 API 요청의 증거가 없다")
    for request in requests:
        if not isinstance(request, dict) or not isinstance(request.get("url"), str):
            raise ValueError("API 요청의 URL 이 없다")
        url = urlsplit(request["url"])
        if url.username or url.password or f"{url.scheme}://{url.netloc}" not in settings["allowed_origins"]:
            raise ValueError("설정된 테스트 API 범위 밖의 요청이다")
        if request.get("method") not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") \
                or type(request.get("status")) is not int or not 100 <= request["status"] <= 599:
            raise ValueError("실제 API 요청의 메서드·응답 코드가 없다")
    if flow.kind == "browser":
        if data.get("build_head") != head or data.get("browser_tool") != settings["browser_tool"]:
            raise ValueError("화면 빌드의 커밋 또는 브라우저 도구가 다르다")
        if not isinstance(actions, list) or not actions or any(
                not isinstance(a, dict) or not all(isinstance(a.get(k), str) and a[k].strip()
                                                 for k in ("action", "expected", "actual")) for a in actions):
            raise ValueError("입력·클릭·이동과 실제 화면 반영의 증거가 없다")
    # Exclude unknown fields that could smuggle credentials into shared evidence.
    kept = {"observations": [{k: r[k] for k in ("id", "expected", "actual", "pass")} for r in rows],
            "requests": [{k: r[k] for k in ("method", "url", "status")} for r in requests],
            "actions": [{k: a[k] for k in ("action", "expected", "actual")} for a in actions]}
    if flow.kind == "desktop":
        if path is None or artifact is None or data.get("version") != 2:
            raise ValueError("Native evidence requires version 2 and owned artifact/build identity")
        kept["native"] = native_verification.validate_receipt(data, path, artifact, flow.native.model_dump(),
                                                             settings.get("native"), settings["revisions"])
    return clean_evidence(kept, settings)


def reusable(record: dict, flow: Flow, head: str, fingerprint: str, path: Path) -> str:
    if not record.get("ok") or record.get("signature") != fingerprint \
            or record.get("evidence_digest") != sha(record.get("evidence", {})):
        return ""
    old = record.get("executed_head")
    if old == head:
        return "same commit and environment"
    if not old or specs.sh(["git", "merge-base", "--is-ancestor", old, head], path).returncode:
        return ""
    delta = specs.sh(["git", "diff", "--name-only", "--no-renames", old, head], path)
    if delta.returncode:
        return ""
    files = delta.stdout.splitlines()
    # Unmapped/shared inputs invalidate all repository-declared flow impacts.
    all_paths = [g for f in manifest(path)[0].flows for g in f.paths]
    if any(any(fnmatchcase(p, g) or fnmatchcase(p.rsplit("/", 1)[-1], g) for g in specs.SHARED)
           or p != ".gitignore" and not any(fnmatchcase(p, g) for g in all_paths) for p in files):
        return ""
    if any(fnmatchcase(p, g) for p in files for g in flow.paths):
        return ""
    return f"{old}..{head}: no changed path reaches this flow; environment unchanged"


def github(repo: Path, endpoint: str, payload: dict | None = None, method: str = "POST"):
    args = ["gh", "api", endpoint]
    if payload is None:
        done = specs.sh(args, repo)
    else:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as file:
            file.write(json.dumps(payload, ensure_ascii=False))
        try:
            done = specs.sh([*args, "--method", method, "--input", file.name], repo)
        finally:
            os.unlink(file.name)
    if done.returncode:
        raise RuntimeError(f"GitHub 검증 상태 연결 실패: {specs.said(done)}")
    return json.loads(done.stdout) if done.stdout.strip() else {}


def protection(repo: Path, base: str) -> dict:
    endpoint = f"repos/{{owner}}/{{repo}}/branches/{quote(base, safe='')}/protection"
    branch = github(repo, endpoint.removesuffix("/protection"))
    if branch.get("protected") is False:
        raise ValueError("대상 브랜치에 보호 규칙이 없다 — 리뷰 탭에서 GitHub 필수 검사 설정 후 로컬 검증을 재개하세요")
    protected = github(repo, endpoint)
    checks = protected.get("required_status_checks") or {}
    contexts = {*checks.get("contexts", []), *(c["context"] for c in checks.get("checks", []))}
    if CONTEXT not in contexts or not checks.get("strict") or not (protected.get("enforce_admins") or {}).get("enabled"):
        raise ValueError("GitHub 에 로컬 검증 필수 검사·최신 base·관리자 적용을 설정해야 한다")
    bypass = (protected.get("required_pull_request_reviews") or {}).get("bypass_pull_request_allowances") or {}
    if any(bypass.get(k) for k in ("users", "teams", "apps")):
        raise ValueError("GitHub 머지 우회 허용이 있다 — 저장소 관리자가 확인해야 한다")
    return protected


def status(repo: Path, head: str, state: str, description: str) -> dict:
    return github(repo, f"repos/{{owner}}/{{repo}}/statuses/{head}",
                  {"state": state, "context": CONTEXT, "description": description[:140]})


def keep(spec: dict, **fields) -> dict:
    with specs._files:
        fresh = specs.load(spec["repo"], spec["id"])
        if fresh is None:
            raise RuntimeError("검증 명세가 없어졌다")
        fresh["local_verification"] = {"version": 1, **fresh.get("local_verification", {}), **fields}
        specs.save(fresh)
        return fresh


def pending(repo: Path, spec: dict, head: str, reason: str, state: str = "waiting_environment") -> dict:
    spec = keep(spec, state=state, head=head, reason=redact(reason, local(repo)), published=None)
    if not cloud(spec) and not enabled(repo):
        return spec
    try:
        result = status(repo, head, "pending", "Local verification required; " + state)
        spec = keep(spec, published={"head": head, "state": "pending", "id": result.get("id")})
    except (RuntimeError, ValueError, OSError, Cancelled, Exhausted, subprocess.SubprocessError):
        pass  # Saved pending locally; publication failure never grants a pass.
    return spec


def return_to_cloud(repo: Path, spec: dict, head: str, reason: str, failures: list[str], kind: str = "failed") -> dict:
    record = (specs.load(spec["repo"], spec["id"]) or spec).get("local_verification") or {}
    settings = local(repo)
    # Redact the loaded managed copy even if its external source disappears.
    copied = {**settings, "env_file": str(Path(spec["worktree"]) / ".env")}
    reason = redact(redact(reason, settings), copied)
    attempts = [*record.get("failure_attempts", []), {
        "head": head, "environment_digest": None,
        "failures": failures, "reason": reason, "ts": time.time(), "confirmed": False,
        "evidence": [clean_evidence(clean_evidence(r.get("evidence", {}), settings), copied)
                     for r in record.get("flows", [])]}]
    spec = keep(spec, failure_attempts=attempts)
    try:
        measured = failure_environment(repo, Path(spec["worktree"]), spec)
        if record.get("execution_environment_digest") not in (None, measured):
            raise ValueError("검사 뒤 로컬 환경이 바뀌었다 — 실패 환경을 확인해야 한다")
        attempts[-1]["environment_digest"] = measured
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        return pending(repo, spec, head, "실패 근거 보존; 로컬 환경 확인 대기: " + str(exc))
    spec = keep(spec, failure_attempts=attempts)
    if not cloud(spec):
        state = "unstable" if kind == "unstable" else "failed"
        seen = {key: list(value) for key, value in record.get("failures", {}).items()}
        for issue in failures:
            if head not in seen.setdefault(issue, []):
                seen[issue].append(head)
        return keep(spec, state=state, outcome=state, reason=reason, failures=seen,
                    needs_research=bool(record.get("needs_research")) or kind == "unstable")
    try:
        current = github(repo, f"repos/{{owner}}/{{repo}}/pulls/{spec['pr']['number']}")
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        return pending(repo, spec, head, "실패 근거 보존; GitHub 확인 대기: " + str(exc))
    if current.get("head", {}).get("sha") != head:
        return pending(repo, spec, current.get("head", {}).get("sha") or head,
                       "검증 중 PR 커밋이 바뀌었다 — 지난 실패는 반복으로 세지 않는다", "waiting_review")
    previous = (spec.get("local_verification") or {}).get("failures", {})
    seen = {k: list(v) for k, v in previous.items()}
    for issue in failures:
        history = seen.setdefault(issue, [])
        if head not in history:
            history.append(head)
    # Two distinct repaired heads count as two cycles; same-head retries do not.
    state = "reanalysis" if any(len(v) >= 3 for v in seen.values()) else "waiting_cloud"
    if kind == "unstable":
        state = "unstable"
    spec = pending(repo, spec, head, reason, state)
    attempts[-1]["confirmed"] = True
    spec = keep(spec, failures=seen, failure_attempts=attempts,
                needs_research=bool(record.get("needs_research")) or state in ("reanalysis", "unstable"))
    record = spec["local_verification"]
    text = ["Cloud implementation: local verification requires changes", "", f"Commit: `{head}`",
            f"State: `{state}`", "", redact(reason, local(repo)), "", "Affected invariants:",
            *(f"- `{issue}`" for issue in failures), "", "Reproduce with the repository's `verification.json` "
            "and the PR handoff. Keep secrets and private data local. Update the handoff to the repaired commit."]
    for row, evidence in zip(record.get("flows", []), attempts[-1]["evidence"]):
        if not row.get("ok"):
            text += ["", f"Flow `{row['id']}`", "```json", json.dumps(evidence, ensure_ascii=False), "```"]
    body = "\n".join(text)
    returned = sha({"head": head, "environment_digest": attempts[-1]["environment_digest"], "body": body})
    if record.get("returned_failure") != returned:
        spec = keep(spec, delivery={"head": head, "body": body, "fingerprint": returned})
        return deliver(repo, spec, head)
    return spec


def deliver(repo: Path, spec: dict, head: str) -> dict:
    delivery = (spec.get("local_verification") or {}).get("delivery")
    if not delivery:
        return spec
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file:
        file.write(delivery["body"])
    try:
        done = specs.sh(["gh", "pr", "comment", str(spec["pr"]["number"]), "--body-file", file.name], repo)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return pending(repo, spec, head, "GitHub 실패 근거 전달 대기: " + str(exc))
    finally:
        os.unlink(file.name)
    if done.returncode:
        return pending(repo, spec, head, "GitHub 실패 근거 전달 대기: " + specs.said(done))
    return keep(spec, returned_failure=delivery["fingerprint"], delivery=None)


def private_file(path: Path, relative: str) -> Path:
    file = within(path, relative)
    ignored = specs.sh(["git", "check-ignore", "--", relative], path)
    tracked = specs.sh(["git", "ls-files", "--error-unmatch", "--", relative], path)
    if ignored.returncode or not tracked.returncode:
        raise ValueError(f"로컬 전용 파일을 Git 에서 제외해야 한다: {relative}")
    return file


def execute(repo: Path, spec: dict, path: Path, head: str, base_oid: str, halt: threading.Event,
            *, flow_ids: list[str] | None = None, enforced_digest: str = "") -> dict:
    with runtime.verification_scope(halt, specs.GATE_SECONDS) as budget:
        try:
            result = _execute(repo, spec, path, head, base_oid, halt, flow_ids=flow_ids, enforced_digest=enforced_digest)
        except Exhausted:
            result = pending(repo, spec, head, "로컬 검증 전체 시간 제한을 넘었다 — 완료된 증거를 보존했다")
        except Cancelled:
            result = pending(repo, spec, head, "로컬 검증이 중단되었다", "interrupted")
        return keep(result, budget=budget.record())


def _execute(repo: Path, spec: dict, path: Path, head: str, base_oid: str, halt: threading.Event,
             *, flow_ids: list[str] | None = None, enforced_digest: str = "") -> dict:
    """Run missing flows once, retaining evidence and impact-based reuse reasons."""
    from . import review_contract
    binding = review_contract.signature(spec)
    fresh = specs.load(spec["repo"], spec["id"])
    if fresh is None or review_contract.signature(fresh) != binding:
        raise ValueError("검증을 시작하기 전에 작업 명세가 바뀌었다")
    problem = checkout_proven(path, head)
    if problem or not base_oid:
        raise ValueError(problem or "검증할 base 를 읽지 못했다")
    if flow_ids is None and documents(path, base_oid, head):
        return keep(spec, state="runtime_passed", head=head, base_oid=base_oid, document_only=True,
                    flows=[], execution_environment_digest=None, spec_signature=binding,
                    reason="문서 변경 — 실행 검증 제외", finished_at=time.time())
    settings = local(repo)
    if not settings:
        raise ValueError("프로젝트별 로컬 검증 설정이 없다")
    contract, digest = manifest(path)
    if digest != settings["manifest_digest"]:
        raise ValueError("검증 명세가 바뀌었다 — 명령과 주요 흐름을 확인하고 로컬 설정에서 승인한다")
    catalog = {f.id for f in contract.flows}
    if flow_ids is not None and (len(flow_ids) != len(set(flow_ids)) or set(flow_ids) - catalog):
        raise ValueError("등록되지 않거나 중복된 검증 흐름이다")
    if cloud(spec) and flow_ids is not None and set(flow_ids) != catalog and not enforced_digest:
        raise ValueError("Cloud 선택 검증은 현재 Jev 계약이 필요하다")
    selected = [f for f in contract.flows if flow_ids is None or f.id in flow_ids]
    if enforced_digest:
        from . import loop

        paths = loop.changed(path, base_oid, head)
        enforced = review_contract.select(repo, path, spec, review_contract.effective(spec, paths), paths, head, base_oid)
        if enforced["enforced_digest"] != enforced_digest or set(flow_ids or []) != set(enforced["enforced"]["flows"]) \
                or enforced["problems"]:
            raise ValueError("실행할 흐름·강제 계약이 현재 명세와 다르다")
    if not selected:
        raise ValueError("실행할 검증 흐름이 없다")
    if any(not settings["revisions"].get(k) for f in selected for k in f.environments):
        raise ValueError("각 흐름이 사용하는 API·데이터·설정 버전을 기록해야 한다")
    for flow in selected:
        if flow.kind == "desktop":
            if not settings.get("native"):
                raise ValueError("승인된 Windows 네이티브 테스트 범위를 준비해야 한다")
            native_verification.preflight(path, head, flow.native.model_dump(), settings["native"])
    prepare(repo, path, settings)
    settings = redaction(settings, path)
    previous_record = spec.get("local_verification") or {}
    previous = [*previous_record.get("excluded_flows", []), *previous_record.get("flows", [])]
    researched = bool((spec.get("local_verification") or {}).get("research_note"))
    old = {f["id"]: runtime.completed_receipt(f) for f in previous}
    reuse_only = previous_record.get("spec_signature") == binding and all(f.kind != "desktop" and reusable(
        old.get(f.id, {}), f, head, signature(repo, path, f, settings), path) for f in selected)
    rows = []
    scratch = Path(os.environ.get("TEMP") or tempfile.gettempdir()) if os.name == "nt" else Path(tempfile.gettempdir())
    artifact = scratch / "wiki-agent-verification" / sha(
        {"repo": str(repo.resolve()), "task": spec["id"]}) / uuid.uuid4().hex
    if artifact.resolve() == path.resolve() or path.resolve() in artifact.resolve().parents:
        raise ValueError("검증 아티팩트는 소스 폴더 밖에 저장해야 한다")
    artifact.mkdir(parents=True, exist_ok=False)
    spec = keep(spec, state="running", head=head, base_oid=base_oid, document_only=False,
                spec_signature=binding, enforced_digest=enforced_digest, owner=spec.get("implementation_environment", "local"),
                artifact_dir=str(artifact), outcome="running", reason="", finished_at=None, research_note="",
                started_at=time.time() - runtime.verification_budget.get().record()["elapsed_ms"] / 1000,
                deadline_at=time.time() + runtime.verification_budget.get().left(),
                excluded_flows=[r for r in previous if r["id"] not in {f.id for f in selected}])
    spec = keep(spec, execution_environment_digest=failure_environment(repo, path, spec))
    env = {**os.environ, "WIKI_VERIFICATION_HEAD": head, "WIKI_VERIFICATION_ENVIRONMENT": settings["environment_id"],
           "WIKI_VERIFICATION_SCOPE": settings["test_scope"], "WIKI_VERIFICATION_BROWSER": settings["browser_tool"],
           "WIKI_VERIFICATION_ARTIFACTS": str(artifact), "TMPDIR": str(scratch)}
    total = runtime.verification_budget.get()
    reserve = min(runtime.CLEANUP_SECONDS, total.left() / 2)
    budget, owned_jobs = Budget(seconds=max(0, total.left() - reserve), calls=None, candidates=0), []
    try:
        if halt.is_set():
            return keep(spec, state="interrupted", outcome="interrupted", reason="로컬 검증이 중단되었다")
        if settings["setup"] and not reuse_only:
            code, out, cut = specs.gate(settings["setup"], path, halt, env=env, owned_jobs=owned_jobs,
                                        timeout=budget.left())
            if halt.is_set():
                return keep(spec, state="interrupted", outcome="interrupted", reason="로컬 검증이 중단되었다")
            if code != 0:
                budget.check()
                raise ValueError("테스트 환경 준비 실패: " + redact(cut or out[-4000:], settings))
        for flow in selected:
            if halt.is_set():
                break
            budget.check()
            fingerprint = signature(repo, path, flow, settings)
            before = old.get(flow.id, {})
            reuse = reusable(before, flow, head, fingerprint, path) if previous_record.get("spec_signature") == binding else ""
            if reuse and flow.kind == "desktop":
                try:
                    native_verification.validate_receipt({"head": before["executed_head"], **before["evidence"]}, path,
                                                         Path(before["artifact_dir"]), flow.native.model_dump(),
                                                         settings["native"], settings["revisions"],
                                                         redact_text=lambda text: redact(text, settings))
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                    reuse = ""
            if reuse:
                rows.append({**before, "head": head, "reuse_reason": reuse})
                keep(spec, flows=rows)
                continue
            attempts = list(before.get("attempts", []))
            if before.get("head"):
                attempts.append({k: v for k, v in before.items() if k != "attempts"})
            row = {"id": flow.id, "title": flow.title, "kind": flow.kind, "head": head, "executed_head": head,
                   "signature": fingerprint, "command": flow.command, "ok": False,
                   "finished_at": None, "started_at": time.time(), "reason": "끝나지 않았다", "evidence": {},
                   "reuse_reason": "", "attempts": attempts, "blocked": False}
            keep(spec, flows=[*rows, row])
            command = flow.command
            flow_artifact = artifact / flow.id
            flow_artifact.mkdir()
            row["artifact_dir"] = str(flow_artifact)
            if flow.kind == "desktop":
                request = {"root": str(path), "artifact_dir": str(flow_artifact), "head": head, "flow": flow.id,
                           "environment_id": settings["environment_id"], "test_scope": settings["test_scope"],
                           "revisions": settings["revisions"], "settings": settings["native"],
                           "target": flow.native.model_dump()}
                request_file = flow_artifact / "request.json"
                request_file.write_text(json.dumps(request), encoding="utf-8")
                env["WIKI_VERIFICATION_NATIVE_REQUEST"] = str(request_file)
                argv = [sys.executable, str(Path(native_verification.__file__).resolve())]
                command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            verdict = specs.judge(path, [command], halt, env=env, timeout=min(runtime.FLOW_SECONDS, budget.left()))
            if verdict.get("code") is None and not halt.is_set():
                row.update(reason="선택 흐름 실행 시간 제한을 넘었다", log=redact(verdict["tail"], settings),
                           finished_at=time.time(), blocked=True, elapsed_seconds=verdict.get("elapsed_seconds"))
                return pending(repo, keep(spec, flows=[*rows, row]), head, row["reason"])
            if halt.is_set():
                row.update(reason="로컬 검증이 중단되었다", log=redact(verdict["tail"], settings))
                return keep(spec, state="interrupted", outcome="interrupted", flows=[*rows, row], reason=row["reason"])
            try:
                evidence = receipt(verdict["tail"], flow, head, settings, path=path, artifact=flow_artifact)
                failed = [f"{flow.id}/{a['id']}" for a in evidence["observations"] if not a["pass"]]
                ok = verdict["ok"] and verdict["head"] == head and not failed
                reason = "" if ok else verdict["reason"] or "기대 결과와 실제 동작이 다르다"
            except PreparationError as exc:
                row.update(reason=str(exc), log=redact(verdict["tail"], settings), finished_at=time.time(), blocked=True)
                rows.append(row)
                spec = keep(spec, flows=rows)
                return pending(repo, spec, head, str(exc))
            except (ValueError, KeyError, TypeError) as exc:
                evidence, failed, ok, reason = {}, [f"{flow.id}/evidence"], False, str(exc)
            reason = redact(reason, settings)
            row.update(observed_ok=ok, blocked=ok, reason=reason, evidence=evidence, failures=failed,
                       evidence_digest=sha(evidence), elapsed_seconds=verdict.get("elapsed_seconds"),
                       code=verdict.get("code"), log=redact(verdict["tail"], settings), finished_at=time.time())
            rows.append(row)
            spec = keep(spec, flows=rows)
            try:
                if fingerprint != signature(repo, path, flow, local(repo)):
                    raise ValueError("검사 중 로컬 환경이 바뀌었다")
                fresh = specs.load(spec["repo"], spec["id"])
                if fresh is None or review_contract.signature(fresh) != binding or checkout_proven(path, head) \
                        or specs.current_merge_base(path, spec["pr"]["base"], head, budget=budget) != base_oid:
                    raise ValueError("검사 중 명세·커밋·base 가 바뀌었다")
                if manifest(path, budget=budget)[1] != digest:
                    raise ValueError("검사 중 검증 명세가 바뀌었다")
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                row.update(blocked=ok, reason=str(exc))
                spec = keep(spec, flows=rows)
                if not ok:
                    return return_to_cloud(repo, spec, head, reason + "\n" + str(exc),
                                           failed or [f"{flow.id}/runtime"])
                return pending(repo, spec, head, str(exc))
            comparable = [r for r in [*before.get("attempts", []), before]
                          if r.get("finished_at") is not None and not r.get("blocked")
                          and r.get("head") == head and r.get("signature") == fingerprint]
            unstable = bool(ok and comparable and not comparable[-1].get("ok") and not researched)
            row.update(ok=ok and not unstable, blocked=False,
                       reason="같은 커밋·환경에서 실패 후 통과 — 원인 확인 필요" if unstable else redact(reason, settings),
                       evidence=evidence, failures=failed, code=verdict.get("code"),
                       log=redact(verdict["tail"], settings), finished_at=time.time())
            spec = keep(spec, flows=rows)
            if unstable:
                return return_to_cloud(repo, spec, head, row["reason"], [f"{flow.id}/intermittent"], "unstable")
            if not ok:
                break
        if halt.is_set():
            return keep(spec, state="interrupted", outcome="interrupted", reason="로컬 검증이 중단되었다", flows=rows)
    except Exhausted:
        return pending(repo, spec, head, "로컬 검증 전체 실행 시간 제한을 넘었다 — 완료된 증거를 보존했다")
    finally:
        try:
            if settings["cleanup"] and not reuse_only:
                code, out, cut = specs.gate(settings["cleanup"], path, threading.Event(), env=env,
                                           timeout=min(runtime.CLEANUP_SECONDS, total.left()))
                if code != 0:
                    keep(spec, state="waiting_environment", reason="테스트 정리 실패: " + redact(cut or out[-4000:], settings))
                    raise ValueError("테스트 데이터·서버 정리를 확인해야 한다")
        finally:
            for job in owned_jobs:
                process.terminated(job)
    failed = [i for r in rows if not r["ok"] for i in r.get("failures") or [f"{r['id']}/runtime"]]
    if failed:
        return return_to_cloud(repo, spec, head, "주요 사용자 흐름 검증 실패", failed)
    if checkout_proven(path, head) or review_contract.signature(specs.load(spec["repo"], spec["id"]) or {}) != binding \
            or specs.current_merge_base(path, spec["pr"]["base"], head) != base_oid or manifest(path)[1] != digest \
            or failure_environment(repo, path, spec) != spec["local_verification"]["execution_environment_digest"]:
        return pending(repo, spec, head, "검증·정리 중 명세·소스·환경이 바뀌었다")
    total.check()
    return keep(spec, state="runtime_passed", outcome="success", flows=rows, repair_heads=[], finished_at=time.time(),
                budget=total.record())


def checkout_proven(path: Path, head: str) -> str:
    """Execution artifacts are outside source; never accept a locally altered implementation."""
    status = specs.sh(["git", "status", "--porcelain"], path)
    current = specs.sh(["git", "rev-parse", "HEAD"], path)
    if current.returncode or current.stdout.strip() != head:
        return "리뷰 실행이 검증 대상 커밋을 바꿨다 — 사용자 확인 필요"
    if status.returncode or status.stdout.strip():
        return "리뷰 실행 뒤 소스 폴더에 변경이 있다 — 검증 파일은 지정된 아티팩트 폴더에 저장해야 한다"
    return ""


def proven(repo: Path, path: Path, spec: dict, head: str, base_oid: str, *, flow_ids: list[str] | None = None) -> str:
    from . import review_contract

    if not cloud(spec) and flow_ids is None:
        return ""
    problem = checkout_proven(path, head)
    if problem:
        return problem
    record = spec.get("local_verification") or {}
    if record.get("needs_research"):
        return "재분석 근거를 확인해야 한다"
    if record.get("state") not in ("runtime_passed", "verified") or record.get("head") != head:
        return "현재 커밋의 로컬 검증이 끝나지 않았다"
    if record.get("base_oid") != base_oid or not base_oid:
        return "로컬 검증 뒤 base 가 바뀌었다"
    if record.get("spec_signature") != review_contract.signature(spec):
        return "로컬 검증 뒤 작업 명세가 바뀌었다"
    if record.get("document_only"):
        return "" if not flow_ids and documents(path, base_oid, head) else "실행 검증을 제외할 수 없는 변경이다"
    if cloud(spec) and flow_ids is None and spec.get("review_inspection"):
        from . import review_inspection
        chosen = review_inspection.current(repo, path, spec, head, base_oid)
        if not chosen.get("inspection_selected") or chosen["problems"]:
            return "현재 Jev 검증 선택을 다시 준비해야 한다"
        flow_ids = [f["id"] for f in chosen["flows"]]
    settings = local(repo)
    try:
        contract, digest = manifest(path)
        if not settings or settings["manifest_digest"] != digest:
            return "로컬 검증 설정·명세가 바뀌었다"
        rows = {r["id"]: r for r in record.get("flows", [])}
        if flow_ids is not None and set(flow_ids) - {f.id for f in contract.flows}:
            return "등록되지 않은 검증 흐름이다"
        for flow in contract.flows:
            if flow_ids is not None and flow.id not in flow_ids:
                continue
            row = rows.get(flow.id, {})
            if row.get("head") != head or not row.get("ok") or row.get("signature") != signature(repo, path, flow, settings):
                return f"{flow.title}: 로컬 증거가 없거나 환경이 바뀌었다"
            if row.get("evidence_digest") != sha(row.get("evidence", {})):
                return f"{flow.title}: 실행 증거가 바뀌었다 — 다시 수집한다"
            if flow.kind == "desktop":
                native_verification.validate_receipt({"head": row.get("executed_head"), **row["evidence"]}, path,
                                                     Path(row["artifact_dir"]), flow.native.model_dump(),
                                                     settings["native"], settings["revisions"],
                                                     redact_text=lambda text: redact(text, settings))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        return "로컬 검증 환경을 확인하지 못했다"
    return ""


def evidence_identity(spec: dict) -> str:
    record = spec.get("local_verification") or {}
    return sha({"head": record.get("head"), "base_oid": record.get("base_oid"),
                "document_only": record.get("document_only"),
                "flows": [{k: r.get(k) for k in ("id", "signature", "executed_head", "evidence")}
                          for r in record.get("flows", [])]})


def failure_environment(repo: Path, path: Path, spec: dict) -> str:
    runtime = specs.digest(repo, path, specs.required(repo, spec))
    if (spec.get("local_verification") or {}).get("document_only"):
        return sha({"runtime": runtime})
    settings = local(repo)
    if not settings:
        raise ValueError("프로젝트별 로컬 검증 설정을 읽지 못했다")
    return sha({"settings": {k: v for k, v in settings.items() if k != "redact_values"},
                "env": sha(Path(settings["env_file"]).read_bytes()) if settings else "",
                "runtime": runtime})


def uninvestigated(repo: Path, path: Path, spec: dict, head: str) -> list[dict]:
    from . import review_inspection

    record = spec.get("local_verification") or {}
    attempts = record.get("failure_attempts", [])
    investigated = {i for note in record.get("research", []) for i in note.get("failure_attempts", [])}
    environment = failure_environment(repo, path, spec)
    unresolved = [row for i, row in enumerate(attempts)
                  if i not in investigated and row["head"] == head and row["environment_digest"] in (None, environment)]
    contract = review_inspection.current(repo, path, spec, head, record["base_oid"]) if record.get("enforced_digest") else {}
    excluded = {r["id"] for r in record.get("excluded_flows", [])} if contract.get("inspection_selected") \
        and contract.get("digest") == record.get("enforced_digest") else set()
    unresolved = [{**r, "failures": [f for f in r["failures"] if f.split("/", 1)[0] not in excluded]} for r in unresolved]
    if remaining := [r for r in unresolved if r["failures"]]:
        return remaining
    # Legacy failures without fingerprints require investigation too.
    if not attempts and any(head in heads for heads in record.get("failures", {}).values()) \
            and not any(note.get("head") == head for note in record.get("research", [])):
        return [{"failures": [issue for issue, heads in record["failures"].items() if head in heads],
                 "reason": "이전 실패의 환경 근거가 없다 — 원인 확인 필요"}]
    return []


def merge_proven(repo: Path, path: Path, spec: dict, head: str, base_oid: str) -> str:
    problem = proven(repo, path, spec, head, base_oid)
    record = spec.get("local_verification") or {}
    allowed = specs.approved(spec) or {}
    published = record.get("published") or {}
    if problem:
        return problem
    try:
        problem = uninvestigated(repo, path, spec, head)
    except (OSError, ValueError, KeyError, TypeError):
        return "이전 실패의 환경 근거를 확인하지 못했다"
    if problem:
        return "같은 커밋·환경에서 실패 후 통과 — 원인 확인 필요"
    if record.get("state") != "verified" or (published.get("head"), published.get("state")) != (head, "success"):
        return "현재 로컬 검증은 리뷰·게시 대기다"
    if allowed.get("local_verification_digest") != evidence_identity(spec):
        return "리뷰가 현재 로컬 실행 증거를 확인하지 않았다"
    return ""


def publish(repo: Path, spec: dict, head: str, base: str) -> dict:
    if not cloud(spec):
        # Ordinary status publication does not add Cloud's protection prerequisite.
        status(repo, head, "success", "Local implementation: existing review and final gate passed")
        return spec
    from . import review_contract

    path = Path(spec["worktree"])
    if problem := review_contract.merge_problem(repo, path, spec, head, specs.current_merge_base(path, base, head)):
        raise ValueError(problem)
    unresolved = uninvestigated(repo, path, spec, head)
    if unresolved:
        reason = "같은 커밋·환경에서 실패 후 통과 — 원인 확인 필요"
        return_to_cloud(repo, spec, head, reason + "\n\n" + "\n\n".join(r["reason"] for r in unresolved),
                        sorted({issue for r in unresolved for issue in r["failures"]}), "unstable")
        raise ValueError(reason)
    protection(repo, base)
    rows = (spec.get("local_verification") or {}).get("flows", [])
    body = "Local verification passed\n\n" + f"Commit: `{head}`\n\n" + "\n".join(
        f"- `{r['id']}`: passed" + (f"; reused: {r['reuse_reason']}" if r.get("reuse_reason") else "") for r in rows)
    if not rows:
        body += "Documentation-only change; runtime checks exempt."
    for row in rows:
        body += "\n\n" + f"Flow `{row['id']}` evidence\n```json\n" + json.dumps(
            clean_evidence(row.get("evidence", {}), local(repo)), ensure_ascii=False) + "\n```"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file:
        file.write(body)
    try:
        done = specs.sh(["gh", "pr", "comment", str(spec["pr"]["number"]), "--body-file", file.name], repo)
    finally:
        os.unlink(file.name)
    if done.returncode:
        raise RuntimeError("로컬 검증 결과를 GitHub 에 남기지 못했다")
    result = status(repo, head, "success", "Independent local review and required verification passed")
    return keep(spec, state="verified", published={"head": head, "state": "success", "id": result.get("id")})


@router.get("/api/verification/config")
def get_config(sid: str | None = None) -> dict:
    repo = query.current_repo()
    spec = specs.load(repo.name, sid) if sid else None
    if sid and spec is None:
        raise HTTPException(404, "그런 명세가 없다")
    path = Path(spec["worktree"]) if spec and spec.get("worktree") else repo
    try:
        parsed, digest = manifest(path)
        return {"repo": repo.name, "settings": local(repo), "manifest": parsed.model_dump(),
                "manifest_digest": digest, "problem": ""}
    except (OSError, ValueError) as exc:
        return {"repo": repo.name, "settings": local(repo), "manifest": None, "manifest_digest": "", "problem": str(exc)}


@router.put("/api/verification/config")
def set_config(body: LocalSettings, sid: str | None = None) -> dict:
    repo = query.current_repo()
    with _configuration:
        spec = specs.load(repo.name, sid) if sid else None
        if sid and spec is None:
            raise HTTPException(404, "그런 명세가 없다")
        path = Path(spec["worktree"]) if spec and spec.get("worktree") else repo
        try:
            parsed, digest = manifest(path)
            if body.manifest_digest != digest:
                raise ValueError("확인한 검증 명세가 바뀌었다 — 다시 읽는다")
            for flow in parsed.flows:
                if flow.kind == "desktop":
                    if not body.native or body.native.application != flow.native.application:
                        raise ValueError("등록된 네이티브 앱·실행 파일·작업 전용 데스크톱 범위를 명시적으로 승인한다")
            if any(not body.revisions.get(k) for f in parsed.flows for k in f.environments):
                raise ValueError("흐름이 사용하는 환경 버전을 모두 지정한다")
            source = Path(body.env_file)
            if not source.is_absolute() or not source.is_file():
                raise ValueError("로컬 .env 의 절대 경로가 필요하다")
            for origin in body.allowed_origins:
                url = urlsplit(origin)
                if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password \
                        or url.path or url.query or url.fragment:
                    raise ValueError("허용 API 는 http(s)://호스트:포트 형식의 origin 으로 지정한다")
            destination = private_file(repo, LOCAL)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = within(repo, LOCAL.removesuffix(".json") + ".tmp")
            saved = body.model_dump()
            if "redact_values" not in body.model_fields_set:
                saved["redact_values"] = local(repo).get("redact_values", [])
            temp.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(destination)
            # Known configuration changes revoke existing passes immediately;
            # the background poll also handles edits made outside the app.
            for current in specs.listing(repo.name):
                record = current.get("local_verification") or {}
                if current["state"] == "머지 가능" and record.get("state") in ("runtime_passed", "verified"):
                    tree = Path(current.get("worktree") or repo)
                    selected = None if cloud(current) else [f["id"] for f in (current.get("review_contract") or {}).get("flows", [])]
                    problem = proven(repo, tree, current, record["head"], record.get("base_oid", ""), flow_ids=selected)
                    if problem:
                        pending(repo, current, record["head"], problem, "waiting_review")
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return get_config(sid)


class ProtectionRequest(Contract):
    base: str = Field(min_length=1)


@router.post("/api/verification/protection")
def configure_protection(body: ProtectionRequest) -> dict:
    repo = query.current_repo()
    endpoint = f"repos/{{owner}}/{{repo}}/branches/{quote(body.base, safe='')}/protection"
    with _configuration:
        # An unreadable rule cannot be replaced; read ownership before creating or extending one.
        try:
            marker = private_file(repo, ".wiki/verification.protection.json")
            branch = github(repo, endpoint.removesuffix("/protection"))
            if not branch.get("protected"):
                github(repo, endpoint, {"required_status_checks": {"strict": True, "checks": [{"context": CONTEXT, "app_id": -1}]},
                                       "enforce_admins": True, "required_pull_request_reviews": None, "restrictions": None}, "PUT")
            protected = github(repo, endpoint)
            checks = protected.get("required_status_checks") or {}
            existing = checks.get("checks") or [{"context": c, "app_id": -1} for c in checks.get("contexts", [])]
            if not any(c["context"] == CONTEXT for c in existing):
                existing.append({"context": CONTEXT, "app_id": -1})
            github(repo, endpoint + "/required_status_checks", {"strict": True, "checks": existing}, "PATCH")
            github(repo, endpoint + "/enforce_admins", {}, "POST")
            protection(repo, body.base)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"base": body.base, "context": CONTEXT}) + "\n", encoding="utf-8")
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "context": CONTEXT}
