"""Verified test-only cleanup and monotonic ratchet finishing steps."""

import ast
import json
from pathlib import Path

import debt
import refactor_profile
from wiki import slots_for
from workspace import remove

from . import loop, maintenance, refactor, specs, work

PROMPT = (
    "Audit and clean up test bloat after production refactoring. Touch only the listed test files and fixtures. "
    "Never delete tests, assertions, expected observations, or regression cases; never edit production, runner "
    "configuration, dependencies or quality-check controls. Prefer lossless JSON formatting, remove redundant "
    "formatting/comments, and report unsafe structural changes as deferred. Without mutation evidence, Python "
    "files must retain their EXACT full AST and other non-JSON files their exact bytes. With measured mutation "
    "evidence, helpers can be factored but test IDs/results and every mutant verdict must stay identical. "
    "Do not regenerate approved snapshots or discard attributes. Do not commit, push, delegate or ask questions. "
    "End with a fenced refactor-cleanup JSON block: {\"deferred\": [specific reasons for unperformed changes]}. "
    "A no-change audit is valid; do not manufacture a diff. Both total bytes and lines must not grow."
)


def body(goal: str, lines: list[str]) -> str:
    return "\n".join(["## 변경 요약", "", goal, "", "## 확인", "", *[f"- {line}" for line in lines], ""])


def summary(step: dict) -> str:
    if not step.get("kind"):
        return body(step["goal"], ["Frozen characterization tests pass on every candidate considered.",
                                  "Selected by the largest drop in debt (`tool/refactor_profile.py`)."])
    lines = ["Characterization tests and the repository gate pass."]
    if step["kind"] == "test_cleanup":
        lines.append(f"Tests: {step['before']} -> {step['after']}.")
        lines.append("Test IDs/results and each measured mutant verdict are unchanged."
                     if step.get("quality_command") else "Python ASTs and decoded JSON values are unchanged; other files remain byte-identical.")
        lines.extend(f"Deferred: {reason}" for reason in step.get("deferred", []))
    else:
        lines.append(f"Ratchet {step['action']}; debt check passed. No limits or exclusions were raised.")
        lines.append("The baseline is on this task branch; it is not applied to main before merge.")
    return body(step["goal"], lines)


def unchanged(w, run: dict, index: int, sid: str) -> dict:
    step, path = run["steps"][index], refactor.place(w, sid)
    if refactor.git(path, "branch", "--show-current") != sid \
            or refactor.git(path, "rev-parse", "HEAD") != step["verified_head"] \
            or refactor.git(path, "status", "--porcelain"):
        raise refactor.Stop("cleanup_changed", "변경 없는 단계의 작업 폴더가 바뀌었다 — 소유권을 유지한다")
    work.forget(path)
    notes = []
    if w.hub:
        try:
            notes.append(remove(w.repo, path, keep_branch=True))
        except (ValueError, RuntimeError) as exc:
            raise refactor.Stop("cleanup_checkout", str(exc)) from exc
        if path.exists():
            raise refactor.Stop("cleanup_checkout", "작업 폴더가 남았다 — 정리를 확인한 뒤 재개해라")
    else:
        refactor.switched(w, step["base"], step["verified_head"])
    base_path = refactor.place(w, step["base"]) if w.hub else w.repo
    comparison = base_path if base_path.exists() else w.repo
    prune = loop.local_pruned if w.hub else loop._local_pruned  # The project checkout is already held by the caller.
    notes.append(prune(comparison, sid, step["verified_head"]))
    specs.update(w.repo.name, sid, state="정리됨", worktree=None, cleanup=notes)
    run["steps"][index] = {**run["steps"][index], "state": "done", "spec": None}
    return w.note(steps=run["steps"])


def reviewed(w, sid: str) -> None:
    """A later review revision cannot silently invalidate finishing evidence."""
    run = refactor.load(w.repo.name, w.rid)
    step = next((s for s in run["steps"] if s.get("spec") == sid and s.get("kind")), None)
    if step is None:
        return
    head = refactor.head_of(w.repo, refactor.live(w, sid))
    safe = bool(head) and head == step["verified_head"]
    if head and not safe and maintenance.documents_only(w.repo, step["verified_head"], head):
        names = refactor.git(w.repo, "diff", "--name-only", "-z", step["verified_head"], head).split("\0")
        safe = not any(debt.TEST.search(n.lower()) or n in step.get("quality_files", []) for n in names if n)
    if not safe:
        raise refactor.Stop("finish_review_changed", "리뷰 수정이 테스트·기준선 검증 커밋을 바꿨다 — 해당 작업에서 다시 검증해야 한다")


def schedule(run: dict) -> list[dict]:
    """New runs gain two terminal steps; old completed/continued runs never replay."""
    steps = list(run["steps"])
    if run.get("finish_version") and not any(s.get("kind") == "test_cleanup" for s in steps):
        for kind, tier, goal in (("test_cleanup", "L1", "Clean up tests without weakening their evidence"),
                                 ("ratchet", "L0", "Adopt or tighten the post-cleanup debt baseline")):
            steps.append({"n": len(steps) + 1, "kind": kind, "tier": tier, "goal": goal,
                          "files": run["tests"]["tests"] if kind == "test_cleanup" else [debt.RATCHET],
                          "state": "pending", "spec": None})
    return steps


def record(w, step: dict, **fields) -> dict:
    run = refactor.load(w.repo.name, w.rid)
    run["steps"] = [{**s, **fields} if s["n"] == step["n"] else s for s in run["steps"]]
    return w.note(steps=run["steps"])["steps"][step["n"] - 1]


def inventory(path: Path) -> dict[str, bytes]:
    names = refactor.git(path, "ls-files", "-z").split("\0")
    return {name: (path / name).read_bytes() for name in names if name and (path / name).is_file()
            and path.resolve() in (path / name).resolve().parents
            and not (path / name).is_symlink() and debt.TEST.search(name.lower())}


def size(files: dict[str, bytes]) -> dict:
    return {"bytes": sum(map(len, files.values())),
            "lines": sum(len(value.splitlines()) for value in files.values()), "files": len(files)}


def equivalent(name: str, old: bytes, new: bytes) -> bool:
    """No default serializer is allowed to erase expected observations."""
    if old == new:
        return True
    try:
        if name.endswith(".py"):
            return ast.dump(ast.parse(old, type_comments=True), include_attributes=False) == ast.dump(ast.parse(new, type_comments=True), include_attributes=False)
        if name.endswith(".json"):
            def pairs(items):
                if len(dict(items)) != len(items):
                    raise ValueError("Duplicate JSON keys")
                return dict(sorted(items))
            def number(token):
                if token in ("NaN", "Infinity", "-Infinity"):
                    raise ValueError("Nonfinite JSON number")
                return ("number", token)
            canonical = lambda b: repr(json.loads(b, object_pairs_hook=pairs, parse_int=number, parse_float=number, parse_constant=number))  # noqa: E731
            return canonical(old) == canonical(new)
    except (SyntaxError, ValueError, UnicodeError):
        pass
    return False


def quality(w, command: str, path: Path) -> dict:
    done = refactor_profile.sh([command], path, shell=True, halt=w.halt)
    if done.returncode == refactor_profile.CUT:
        raise refactor.Stop("cancelled")
    try:
        value = json.loads(done.stdout)
        tests, mutants = value["tests"], value["mutants"]
        if value.get("complete") is not True or done.returncode or not isinstance(tests, dict) or not tests \
                or not isinstance(mutants, dict) or not mutants:
            raise ValueError("Incomplete quality receipt")
        if not all(isinstance(k, str) and k for k in [*tests, *mutants]) \
                or not all(v in ("passed", "skipped") for v in tests.values()) or "passed" not in tests.values() \
                or not all(v in ("killed", "survived") for v in mutants.values()) or "killed" not in mutants.values():
            raise ValueError("Invalid test or mutant verdict")
        return {"tests": tests, "mutants": mutants}
    except (ValueError, KeyError, TypeError) as exc:
        raise refactor.Stop("test_quality", "테스트·변이 검사 결과가 없거나 유효하지 않다") from exc


def checks(w, run: dict, path: Path) -> None:
    for argv, shell in ((run["tests"]["test_argv"], False), ([specs.gate_of(w.repo)], True)):
        done = refactor_profile.sh(argv, path, shell=shell, halt=w.halt)
        if done.returncode == refactor_profile.CUT:
            raise refactor.Stop("cancelled")
        if done.returncode:
            raise refactor.Stop("tests_fail", (done.stdout + done.stderr)[-2000:])


def scope(path: Path, before: dict, protected: dict, command: str) -> set[str]:
    changed = maintenance.changes(path)
    for name in changed:
        target = path / name
        if name not in before or target.is_symlink() or not target.is_file() or name in protected \
                or path.resolve() not in target.resolve().parents:
            raise refactor.Stop("cleanup_scope", f"허용되지 않은 테스트 정리 변경: {name}")
        if (name.endswith(".json") or not command) and not equivalent(name, before[name], target.read_bytes()):
            raise refactor.Stop("cleanup_evidence", f"테스트의 의미가 보존됐다는 근거가 없다: {name}")
    if any(not (path / name).is_file() or (path / name).read_bytes() != value for name, value in protected.items()):
        raise refactor.Stop("cleanup_scope", "품질 검사 도구를 바꿨다")
    return changed


def cleaned(w, run: dict, step: dict, path: Path) -> dict:
    before = inventory(path)
    settings = slots_for(w.repo.name, w.repo)
    command = settings.get("test_quality_cmd", "").strip()
    try:
        controls = json.loads(settings.get("test_quality_files", "[]"))
    except (TypeError, ValueError) as exc:
        raise refactor.Stop("test_quality", "test_quality_files는 JSON 경로 목록이어야 한다") from exc
    if command and (not isinstance(controls, list) or not controls):
        raise refactor.Stop("test_quality", "test_quality_files에 검사 도구·설정 파일을 지정해야 한다")
    protected = {}
    for name in controls if command else []:
        if not isinstance(name, str):
            raise refactor.Stop("test_quality", "검사 도구 경로는 문자열이어야 한다")
        target = (path / name).resolve()
        if path.resolve() not in target.parents or not target.is_file():
            raise refactor.Stop("test_quality", "검사 도구 경로가 저장소 안의 파일이 아니다")
        protected[name] = target.read_bytes()
    checks(w, run, path)
    baseline = quality(w, command, path) if command else None
    if maintenance.changes(path):
        raise refactor.Stop("cleanup_scope", "기준 검사 명령이 저장소를 바꿨다")
    step = record(w, step, before=size(before), quality_command=command,
                  quality_files=sorted(protected), quality_before=baseline)
    final = refactor.turn(w, run, "Allowed test files:\n" + "\n".join(sorted(before.keys() - protected.keys()))
                          + f"\nMutation evidence available: {bool(baseline)}", system=PROMPT, where=path)
    if refactor.git(path, "rev-parse", "HEAD") != step["before_head"] \
            or refactor.git(path, "branch", "--show-current") != step["before_branch"]:
        raise refactor.Stop("cleanup_scope", "정리 모델이 커밋을 만들었다 — 자동으로 채택하지 않는다")
    report = refactor.fenced("refactor-cleanup", final)
    if not isinstance(report, dict) or not isinstance(report.get("deferred"), list) \
            or not all(isinstance(s, str) for s in report["deferred"]):
        raise refactor.Stop("format", "refactor-cleanup에는 deferred 이유 목록이 필요하다")
    scope(path, before, protected, command)
    checks(w, run, path)
    verdict = quality(w, command, path) if command else None
    if verdict != baseline:
        raise refactor.Stop("cleanup_evidence", "테스트 ID·결과 또는 변이 탐지 결과가 바뀌었다")
    changed = scope(path, before, protected, command)
    if refactor.git(path, "rev-parse", "HEAD") != step["before_head"] \
            or refactor.git(path, "branch", "--show-current") != step["before_branch"]:
        raise refactor.Stop("cleanup_scope", "검사 중 커밋이나 브랜치가 바뀌었다")
    after = size(inventory(path))
    if changed and (after["bytes"] > step["before"]["bytes"] or after["lines"] > step["before"]["lines"]
                    or after == step["before"]):
        raise refactor.Stop("cleanup_growth", "테스트 정리는 줄·바이트를 늘리지 않고 실제 크기를 줄여야 한다")
    return {"after": after, "deferred": report["deferred"], "quality_after": verdict}


def candidate(w, run: dict, step: dict, path: Path) -> dict:
    """Use the ordinary step's publication/review/checkpoint path, not another owner."""
    head = refactor.git(path, "rev-parse", "HEAD")
    branch = refactor.git(path, "branch", "--show-current")
    if step.get("verified_head"):
        if head != step["verified_head"] or branch != step["before_branch"] or refactor.git(path, "status", "--porcelain"):
            raise refactor.Stop("cleanup_changed", "검증한 정리 커밋이 바뀌었다 — 다시 확인해야 한다")
        return {"commit": head, "unchanged": step["unchanged"]}
    if refactor.git(path, "status", "--porcelain") or step.get("before_head") not in (None, head):
        raise refactor.Stop("dirty", "중단된 정리 변경을 먼저 확인해라 — 자동으로 덮어쓰지 않는다")
    record(w, step, before_head=head, before_branch=branch)
    if step["kind"] == "test_cleanup":
        evidence = cleaned(w, run, step, path)
    else:
        checks(w, run, path)
        if maintenance.changes(path):
            raise refactor.Stop("ratchet", "기준 검사 명령이 저장소를 바꿨다")
        file = path / debt.RATCHET
        old = debt.load(file) if file.exists() else None
        problems, _ = debt.check(path)
        if problems:
            raise refactor.Stop("ratchet", "기준을 완화하지 않는다: " + "; ".join(problems))
        if old is None:
            debt.init(path)
        else:
            debt.track_artifacts(path)
            debt.tighten(path)
        problems, _ = debt.check(path)
        if problems:
            raise refactor.Stop("ratchet", "; ".join(problems))
        checks(w, run, path)
        current = debt.load(file)
        evidence = {"action": "adopted" if old is None else "tightened" if old != current else "unchanged",
                    "baseline_before": old, "baseline_after": current, "checked": True}
        if maintenance.changes(path) - {debt.RATCHET}:
            raise refactor.Stop("ratchet", "기준선 외의 변경은 채택하지 않는다")
    if refactor.git(path, "rev-parse", "HEAD") != head or refactor.git(path, "branch", "--show-current") != branch:
        raise refactor.Stop("cleanup_changed", "검사 중 커밋이나 브랜치가 바뀌었다")
    changed = specs.sh(["git", "status", "--porcelain"], path).stdout.strip()
    if step["kind"] == "ratchet":
        changed = evidence["action"] != "unchanged"
    if changed:
        names = list(inventory(path)) if step["kind"] == "test_cleanup" else [debt.RATCHET]
        refactor.git(path, "add", "-f", "--", *names)
        refactor.git(path, "commit", "-m", step["goal"])
    head = refactor.git(path, "rev-parse", "HEAD")
    record(w, step, **evidence, verified_head=head, unchanged=not bool(changed))
    return {"commit": head, "unchanged": not bool(changed)}
