"""refactor_run — a survey spec runs as an ordinary task, held to its mode.

`docs/plans/refactor/7-purpose-modes.md`, Execution. `specs.start` asks
`startable` before it forks. `specs._check` hands a survey spec's ended turn
here around the ordinary report and cleanup steps: `preserve` freezes its
characterization first, every mode keeps its test contract, and the final
command replaces the narrow round checks, so a failure the survey's baseline
did not have stops the task before any pull request. The conversation's
refinements land on the same card through `refined`.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from pathlib import Path

from fastapi import HTTPException

import debt
import refactor_profile

from . import refactor, refactor_finish, refactor_survey, review_contract, specs
from .query import ROOT, config

RUN = (ROOT / "tool/prompts/refactor-run.md").read_text(encoding="utf-8").strip()
FROZEN = ("The characterization tests are frozen at `{head}`: {tests}. Now make the refactoring this spec "
          "describes, keep those tests passing, run the test cleanup pass within the test contract, and end with "
          "the `test-cleanup` and `done-report` blocks.")
DONE = object()   # `characterized`: nothing to freeze, go on with the ordinary check


def surveyed(spec: dict | None) -> bool:
    return bool(((spec or {}).get("refactor") or {}).get("survey"))


def prompt(spec: dict) -> str:
    """What a survey spec's work session reads after the spec itself; nothing for any other task."""
    if not surveyed(spec):
        return ""
    r = spec["refactor"]
    shown = {k: r.get(k) for k in ("mode", "pipeline", "files", "evidence", "depends", "base")}
    return "\n" + "\n\n".join([refactor_survey.rules(r["mode"]), RUN,
                               "```json\n" + json.dumps({"refactor": {**shown, **r["detail"]}}, ensure_ascii=False,
                                                        indent=2) + "\n```"]) + "\n"


def context(repo: Path, cfg: dict) -> str:
    """What a refactoring conversation turn reads first: the mode, its latest survey and that mode's cards."""

    mode, generation = cfg.get("refactor_mode"), cfg.get("refactor_generation", 0)
    head = f"Selected refactoring mode: `{mode}`."
    if mode not in refactor_survey.MODES:
        return head
    found = [r for r in refactor.listing(repo.name) if r.get("kind") == "survey" and not r.get("recheck")
             and r["mode"] == mode and r.get("generation", 0) == generation]
    if not found:
        return f"{head} No survey of this mode exists yet; a spec needs one."
    run = max(found, key=lambda r: r["created"])
    cards = [{"id": s["id"], "state": s["state"], "rev": s["rev"], **s["refactor"]["detail"],
              "depends": s["refactor"]["depends"], "base": s["refactor"]["base"]}
             for s in specs.listing(repo.name) if surveyed(s) and s["refactor"]["mode"] == mode
             and s["source"].get("refactor_generation", 0) == generation]
    shown = {k: run.get(k) for k in ("id", "state", "phase", "base", "baseline", "map", "pipelines", "survey")}
    shown["baseline"] = {k: (run.get("baseline") or {}).get(k) for k in ("cmd", "code", "failures")}
    return (f"{head}\n\nThe latest survey of this mode:\n```json\n{json.dumps(shown, ensure_ascii=False)}\n```\n\n"
            f"Its spec cards:\n```json\n{json.dumps(cards, ensure_ascii=False)}\n```")


def under(changed: list[str], paths: list[str]) -> list[str]:
    return [c for c in changed if any(c == p or c.startswith(p.rstrip("/") + "/") for p in paths)]


def startable(repo: Path, spec: dict, source: str) -> list[str]:
    """Refuse a start the survey no longer stands for; the files changed since its base."""

    r = spec["refactor"]
    cfg = config("refactor", repo.name)
    if r["mode"] != cfg.get("refactor_mode") \
            or spec["source"].get("refactor_generation", 0) != cfg.get("refactor_generation", 0):
        raise HTTPException(409, "모드가 바뀌었다. 지금 모드로 다시 조사해라")
    survey = refactor.load(repo.name, r["survey"])
    if survey is None or survey["state"] != "done":
        raise HTTPException(409, "이 명세를 낸 조사가 끝나지 않았거나 없어졌다")
    for dep in r.get("depends") or []:
        if (specs.load(repo.name, dep) or {}).get("state") != "머지됨":
            raise HTTPException(409, f"선행 명세 `{dep}` 이 머지된 뒤 시작한다")
    if r["detail"].get("hypothesis") and not r["detail"].get("measure"):
        raise HTTPException(409, "측정 명령이 없는 성능 가설이다 — 측정할 수 있게 다시 확인해라")
    listed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", r["base"],
                       source or "HEAD"], repo)
    if listed.returncode:
        raise HTTPException(409, f"조사 기준 커밋과 비교하지 못했다 — {specs.said(listed)}")
    changed = listed.stdout.splitlines()
    if hit := under(changed, [*r["files"], *r["evidence"]]):
        raise HTTPException(409, f"조사 뒤 대상·근거 파일이 바뀌었다: {', '.join(hit[:10])} — [다시 확인] 으로 "
                                 "이 명세를 지금 코드에서 다시 조사해라")
    return changed


def forked(repo: Path, spec: dict) -> tuple[Path, str, str, dict]:
    """`specs.fork` for a survey spec once `startable` allows it. The running
    server's own checkout never switches: a hub refactoring gets a linked worktree."""

    found: list[str] = []
    if refactor.scope_of(repo) != "hub":
        path, before, previous = specs.fork(repo, spec["id"], lambda source: found.extend(startable(repo, spec, source)))
        return path, before, previous, {"workspace_mode": "branch", "refactor_check": {"changed": found}}
    found = startable(repo, spec, "")
    path = specs.create(repo, spec["id"], linked=True)
    return (path, specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip(),
            specs.sh(["git", "branch", "--show-current"], repo).stdout.strip(), {"refactor_check": {"changed": found}})


def held(path: Path, release):
    """A linked worktree's hold in place of the server checkout's: the turn holds its own worktree."""
    release()
    return specs.hold(specs.work._busy, specs._lock, str(path), "그 작업트리를 다른 요청이 쓰고 있다")


# -- Characterization first (`preserve`) -----------------------------------------

def characterized(repo: Path, path: Path, run, spec: dict, final: str):
    """`DONE` once frozen or outside `preserve`; else the next turn, a failure, or None to wait."""

    if not surveyed(spec) or spec["refactor"]["mode"] != "preserve" or spec.get("refactor_frozen"):
        return DONE
    if "```refactor-tests" not in final:
        if any(b["name"] == "done-report" for b in specs.blocks(final)[1]):
            return specs.failed(run, spec, "preserve 는 동작 고정 테스트(`refactor-tests`)가 먼저다 — 완료로 보지 않는다")
        return None
    try:
        given = refactor.fenced("refactor-tests", final)
        tests, argv = given["tests"], given["test_argv"]
        assert isinstance(tests, list) and tests and all(isinstance(t, str) and t for t in tests)
        assert isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)
    except (refactor.Stop, KeyError, TypeError, AssertionError):
        return specs.failed(run, spec, "`refactor-tests` 는 비지 않은 tests 와 test_argv 목록이어야 한다")
    if specs.dirty(path):
        return specs.failed(run, spec, "동작 고정 테스트를 커밋하지 않았다")
    changed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames",
                        spec["start_head"], "HEAD"], path).stdout.splitlines()
    if outside := [c for c in changed if c not in tests or not debt.TEST.search(c.lower())]:
        return specs.failed(run, spec, f"동작 고정 턴이 테스트 밖을 바꿨다: {', '.join(outside[:10])}")
    if missing := [t for t in tests if not (path / t).is_file()]:
        return specs.failed(run, spec, f"고정할 테스트 파일이 없다: {', '.join(missing)}")
    done = refactor_profile.sh(argv, path, halt=run.halt)
    if done.returncode:
        return specs.failed(run, spec, "멈췄다" if done.returncode == refactor_profile.CUT else
                            "동작 고정 테스트가 바꾸기 전 코드에서 실패한다\n" + done.stdout[-1500:])
    head = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    file = refactor_profile.STORE / refactor.scope_of(repo) / spec["id"] / "frozen.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    # `refactor_profile.py preserve` reruns `test_argv` and the ratchet; `head` is where the tests were fixed
    # (a legacy file's `baseline` is a debt count, so the name differs).
    file.write_text(json.dumps({"goal": spec["goal"], "files": spec["refactor"]["files"], "tests": tests,
                                "test_argv": argv, "head": head}, ensure_ascii=False, indent=2),
                    encoding="utf-8", newline="\n")
    spec = specs.update(spec["repo"], spec["id"],
                        refactor_frozen={"tests": tests, "test_argv": argv, "head": head, "file": str(file)})
    specs.note(run, f"동작 고정 테스트 {len(tests)}개를 `{head[:12]}` 에 고정했다")
    return lambda: specs.again(path, run.chat, FROZEN.format(head=head, tests=", ".join(tests)), spec)


# -- Before the pull request ---------------------------------------------------------

def added_only(before: bytes, after: bytes) -> bool:
    """Every old line is still there, in order: the file only gained lines."""
    rest = iter(after.splitlines())
    return all(any(line == other for other in rest) for line in before.splitlines())


def contract(path: Path, spec: dict) -> str:
    """Why the existing tests changed outside the spec's test contract, or empty."""

    tests = spec["refactor"]["detail"].get("tests") or {}
    declared = {t["test"].split("::")[0] for t in [*tests.get("remove", []), *tests.get("contract", [])]}
    start, broken = spec["start_head"], []
    listed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", start, "HEAD"], path)
    for name in listed.stdout.splitlines():
        if not debt.TEST.search(name.lower()) or name in declared:
            continue
        old = subprocess.run(["git", "show", f"{start}:{name}"], cwd=path, capture_output=True)
        if old.returncode:
            continue   # a new test file
        if not (path / name).is_file():
            broken.append(f"`{name}` 삭제")
        elif not (refactor_finish.equivalent(name, old.stdout, new := (path / name).read_bytes())
                  or added_only(old.stdout, new)):
            broken.append(f"`{name}` 의 기존 테스트 변경")
    return "" if not broken else ("테스트 계약 밖의 변경 — " + ", ".join(broken[:10])
                                  + " (명세의 tests.remove·tests.contract 에 없다)")


def verified(repo: Path, path: Path, run, spec: dict) -> str:
    """The test contract, the final command against the survey's baseline and the
    measurement: why the pull request must not open, or empty."""

    if problem := contract(path, spec):
        return problem
    base = specs.local_base(spec, path)
    local_only = bool(review_contract.preservation_command(spec))
    cfg = specs.verify.policy(path, project=repo)
    if cfg and not local_only and specs.verify.backend(path, cfg) == "ci":
        # Hosted CI owns the full suite there; a local full run before the pull request would be a second one.
        verdict, record = specs.rounded(repo, path, spec, base, run.halt, lambda text: specs.note(run, text))
        specs.update(spec["repo"], spec["id"], gate=verdict,
                     refactor_check={**(spec.get("refactor_check") or {}), "final": "ci"})
        specs.validate(spec["repo"], spec["id"], round=record)
        return "" if verdict["ok"] else f"판정 실패 — {verdict['reason']}"
    head = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    cmd, local = specs.required(repo, spec), specs.local_required(repo, spec)
    record = {"head": head, "base_oid": specs.current_merge_base(path, base, head), "command": cmd,
              "environment_digest": specs.digest(repo, path, cmd), "ok": False, "code": None,
              "reason": "끝나지 않았다", "finished_at": None}
    specs.validate(spec["repo"], spec["id"], phase="final_running", final=record)
    seen: dict = {}

    def judging(where, command, env, halt, timeout):
        seen.update(specs.judge(where, [command], halt, lambda text: specs.note(run, text), env=env, timeout=timeout))
        return seen

    proof = None
    try:
        with specs.gating(spec["repo"], spec["id"], "final"):
            proof = specs.verify.final(path, base, project=repo, local_command=local, halt=run.halt,
                                       judge=judging, local_only=local_only)
        reason = ""
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc)
    ok = proof is not None and proof.get("head") == head and proof.get("base_oid") == record["base_oid"]
    tail = seen.get("tail") or reason
    specs.validate(spec["repo"], spec["id"], phase=None, final={
        **record, "ok": ok, "code": 0 if ok else seen.get("code", 1), "reason": "" if ok else reason or "검증 커밋·base가 다르다",
        "finished_at": time.time(), "tail": tail, "pending": False, **({"verification": proof} if ok else {})})
    specs.update(spec["repo"], spec["id"], gate={"ok": ok, "reason": reason, "cmd": local, "tail": tail, "head": head,
                                                 "ts": time.time(), "selection": "final"})
    if run.halt.is_set():
        return "사람이 멈춤 — 최종 명령을 끊었다"
    baseline = refactor.load(spec["repo"], spec["refactor"]["survey"])["baseline"]
    now = [] if ok else refactor_survey.failures({"code": seen.get("code", 1), "tail": tail})
    check = {**(spec.get("refactor_check") or {}), "final": "local", "head": head, "ok": ok,
             "baseline": {"cmd": baseline["cmd"], "code": baseline["code"], "failures": baseline["failures"]}}
    if now is None or not ok and not baseline["parsed"]:
        specs.update(spec["repo"], spec["id"], refactor_check={**check, "unparsed": True})
        return "최종 명령이 실패했고, 실패한 검사를 이름으로 가리지 못해 시작 전부터 있던 실패인지 알 수 없다"
    new, old = sorted(set(now) - set(baseline["failures"])), sorted(set(now) & set(baseline["failures"]))
    check.update(new=new, pre_existing=old)
    specs.update(spec["repo"], spec["id"], refactor_check=check)
    if new:
        return f"새 실패: {', '.join(new[:10])}"
    if measure := spec["refactor"]["detail"].get("measure"):
        try:
            check["measured"] = measured(path, spec, measure, head, run.halt)
        except ValueError as exc:
            return f"측정하지 못했다 — {exc}"
        specs.update(spec["repo"], spec["id"], refactor_check=check)
    return ""


def published(repo: Path, path: Path, run, spec: dict):
    """`specs._check`'s end for a survey spec: `verified`, then the ordinary pull request."""

    if why := verified(repo, path, run, specs.load(spec["repo"], spec["id"]) or spec):
        return specs.failed(run, spec, why)
    specs.note(run, "리팩터링 검증 통과")
    return specs.opened(repo, path, run, specs.load(spec["repo"], spec["id"]) or spec)


def measured(path: Path, spec: dict, measure: dict, head: str, halt) -> dict:
    """The command on the start commit, then on `head`, back to back in this
    worktree; the branch is checked out again whatever happens."""

    branch = specs.sh(["git", "branch", "--show-current"], path).stdout.strip()
    out = {}
    try:
        for label, rev in (("before", spec["start_head"]), ("after", head)):
            switched = specs.sh(["git", "switch", "--detach", rev], path)
            if switched.returncode:
                raise ValueError(specs.said(switched))
            times = []
            for _ in range(measure["repeat"]):
                started = time.perf_counter()
                done = refactor_profile.sh(measure["argv"], path, halt=halt)
                if done.returncode:
                    raise ValueError(f"{label} 측정 명령이 {done.returncode} 로 끝났다\n{done.stdout[-800:]}")
                times.append(round((time.perf_counter() - started) * 1000, 1))
            last = [line for line in done.stdout.splitlines() if line.strip()][-1:]
            out[label] = {"ms": times, "median_ms": statistics.median(times), "last_line": (last or [""])[0][:300]}
    finally:
        specs.sh(["git", "switch", branch] if branch else ["git", "switch", "--detach", head], path)
    return {**out, "metric": measure["metric"], "task": measure["task"], "argv": measure["argv"],
            "repeat": measure["repeat"]}


def section(spec: dict) -> list[str]:
    """The pull request body's `리팩터링 검증` section."""

    if not surveyed(spec):
        return []
    r, c = spec["refactor"], spec.get("refactor_check") or {}
    lines = ["## 리팩터링 검증", "",
             f"- 모드 `{r['mode']}` · 파이프라인 `{r['pipeline']}` · 조사 `{r['survey']}` · 기준 커밋 `{r['base'][:12]}`",
             f"- 조사 뒤 바뀐 파일 {len(c.get('changed') or [])}개 — 대상·근거 파일은 바뀌지 않았다"]
    if frozen := spec.get("refactor_frozen"):
        lines.append(f"- 동작 고정 테스트 `{'`, `'.join(frozen['tests'])}` — 바꾸기 전 코드에서 통과, `{frozen['head'][:12]}` 에 고정")
    tests = r["detail"].get("tests") or {}
    lines.append(f"- 테스트 계약: 기존 테스트 파일은 같거나 추가만 · 선언된 삭제 {len(tests.get('remove') or [])}건 · "
                 f"승인된 계약 갱신 {len(tests.get('contract') or [])}건")
    if c.get("final") == "ci":
        lines.append("- 최종 검증은 CI 가 맡는다 — PR 전 전체 실행과 기준 비교는 하지 않았다")
    elif c.get("final") == "local":
        base = c["baseline"]
        lines.append(f"- 최종 명령 {'통과' if c['ok'] else '실패'} · 기준 커밋 게이트 `{base['cmd']}` 종료 코드 {base['code']}")
        if c.get("pre_existing"):
            lines.append(f"- 시작 전부터 있던 실패 {len(c['pre_existing'])}건: " + ", ".join(f"`{f}`" for f in c["pre_existing"][:10]))
        lines.append(f"- 새 실패 {len(c.get('new') or [])}건")
    if m := c.get("measured"):
        lines.append(f"- 측정 {m['metric']} ({m['task']}) · `{' '.join(m['argv'])}` {m['repeat']}회 · 중앙값 "
                     f"{m['before']['median_ms']} ms → {m['after']['median_ms']} ms")
    return [*lines, ""]
