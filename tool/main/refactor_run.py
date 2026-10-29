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

import ast
import copy
import hashlib
import json
import os
import posixpath
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from fastapi import HTTPException

import debt
import refactor_profile

from . import refactor, refactor_survey, review_contract, specs
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
    """The changed files at or below `paths`, read as Git writes them (a card saved before that rule may hold `./a`)."""
    paths = [posixpath.normpath(p.replace("\\", "/")) for p in paths]
    return [c for c in changed if any(c == p or c.startswith(p.rstrip("/") + "/") for p in paths)]


def startable(repo: Path, spec: dict, source: str) -> list[str]:
    """Refuse a start the survey no longer stands for; the files changed since its base."""

    r = spec["refactor"]
    cfg = config("refactor", repo.name)
    if r["mode"] != cfg.get("refactor_mode") \
            or spec["source"].get("refactor_generation", 0) != cfg.get("refactor_generation", 0):
        raise HTTPException(409, "모드가 바뀌었다. 지금 모드로 다시 조사해라")
    if not r.get("roles"):
        raise HTTPException(409, "조사·구현·리뷰 모델을 고정하기 전의 명세다 — 모델을 골라 새로 조사해라")
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
    if hit := under(changed, [*r["files"], *r["detail"].get("creates", []), *r["evidence"]]):
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

def characterizing(spec: dict, final: str) -> bool:
    """`final` hands in a `refactor-tests` block for a `preserve` card not yet frozen."""
    if not surveyed(spec) or spec["refactor"]["mode"] != "preserve" or spec.get("refactor_frozen"):
        return False
    try:
        return bool(refactor.block("refactor-tests", final))
    except refactor.Stop:
        return False


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
    if outside := [c for c in changed if (c not in tests or not debt.TEST.search(c.lower()))
                   and not dev_only(path, spec["start_head"], c, changed)]:
        return specs.failed(run, spec, f"동작 고정 턴이 테스트 밖을 바꿨다: {', '.join(outside[:10])}")
    if missing := [t for t in tests if not (path / t).is_file()]:
        return specs.failed(run, spec, f"고정할 테스트 파일이 없다: {', '.join(missing)}")
    done = refactor_profile.sh(argv, path, halt=run.halt)
    if done.returncode:
        return specs.failed(run, spec, "멈췄다" if done.returncode == refactor_profile.CUT else
                            "동작 고정 테스트가 바꾸기 전 코드에서 실패한다\n" + done.stdout[-1500:])
    head = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    if problem := regressed(repo, path, run, spec, head):
        return specs.failed(run, spec, problem)
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


def regressed(repo: Path, path: Path, run, spec: dict, head: str) -> str:
    """The survey's baseline gate on the characterization head `head`: why it may not be frozen, or empty.
    A frozen test can no longer change, so a repository rule its new files break has to show before the
    freeze (ai-companion-voice-agent: every integration module declares its settings profile)."""

    seen = specs.judge(path, [baseline_of(spec)["cmd"]], run.halt, lambda text: specs.note(run, text))
    if run.halt.is_set():
        return "멈췄다"
    if specs.dirty(path) or specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() != head \
            or not seen["ok"] and seen.get("code") in (None, 0):
        # The freeze names `head`: a command that did not run, or changed the tree or HEAD, judged something else.
        return f"기준 명령이 동작 고정 커밋을 판정하지 못했다 — {seen.get('reason') or '작업트리나 HEAD 가 바뀌었다'}"
    problem, _ = compared(repo, path, spec, seen["ok"], seen["code"], seen.get("tail") or "", "동작 고정 커밋의 기준 명령")
    return f"{problem} — 고정 전이라 그 테스트를 아직 고칠 수 있다" if problem else ""


def baseline_of(spec: dict) -> dict:
    return refactor.load(spec["repo"], spec["refactor"]["survey"])["baseline"]


def compared(repo: Path, path: Path, spec: dict, ok: bool, code: int, tail: str, what: str) -> tuple[str, dict]:
    """One gate run against the survey's baseline: why it fails something the baseline did not, or empty, and
    the comparison to keep. The freeze and the final command judge the same output alike; `what` names the
    run (a noun ending in `명령`)."""

    baseline = baseline_of(spec)
    check = {"baseline": {"cmd": baseline["cmd"], "code": baseline["code"], "failures": baseline["failures"]}}
    now = [] if ok else refactor_survey.failures({"code": code, "tail": tail})
    if now is None or not ok and not baseline["parsed"]:
        return f"{what}이 실패했고, 실패한 검사를 이름으로 가리지 못해 시작 전부터 있던 실패인지 알 수 없다", \
            {**check, "unparsed": True}
    new, old = sorted(set(now) - set(baseline["failures"])), sorted(set(now) & set(baseline["failures"]))
    check.update(new=new, pre_existing=old, code=code)
    if new:
        return f"새 실패: {', '.join(new[:10])}", check
    if not ok and code != baseline["code"]:
        # Named old failures excuse only the same failing run: another exit code means something else failed too.
        return f"{what}이 {code} 로 끝났다 — 기준 게이트는 {baseline['code']} 였다", check
    trees = (path, refactor_survey.place(repo, spec["refactor"]["survey"]))
    if not ok and (odd := refactor_survey.unexplained(tail, baseline.get("tail") or "", trees)):
        # A name extracted is not a run explained: a wrapper's own error beside an old failing test is new.
        return f"{what}의 실패 줄이 기준 게이트 출력에 없다: " + " · ".join(odd[:5]), {**check, "unexplained": odd[:10]}
    return "", check


# -- A frozen characterization a person approves again (`preserve`) -----------------------
# A frozen test can no longer change, and `preserve` declares no test contract: a characterization that
# broke a repository rule had no way out (ai-companion-voice-agent preserve-repository-boundaries). The
# person reads the frozen tests' diff and approves it; the frozen tests as they now are must still pass on
# the characterization commit's code (`code`, never moved), and the freeze moves to the current head.

def frozen_change(spec: dict) -> dict:
    """The frozen tests' diff since their freeze head: `head`, `from`, `files`, `diff` and the `digest` an
    approval names."""

    frozen = spec.get("refactor_frozen")
    if not surveyed(spec) or spec["refactor"]["mode"] != "preserve" or not frozen:
        raise HTTPException(404, "고정한 동작 고정 테스트가 없는 명세다")
    path = Path(spec["worktree"])
    if left := spec.get("refreezing"):   # an earlier run that failed to restore, or a server that died in it
        restore(spec, path, left)
    head = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    diff = specs.sh(["git", "--literal-pathspecs", "-c", "core.quotepath=off", "diff", frozen["head"], head, "--",
                     *frozen["tests"]], path)
    names = specs.sh(["git", "--literal-pathspecs", "-c", "core.quotepath=off", "diff", "--name-only", frozen["head"],
                      head, "--", *frozen["tests"]], path)
    if diff.returncode or names.returncode:
        raise HTTPException(409, f"고정 기준과 비교하지 못했다 — {specs.said(diff if diff.returncode else names)}")
    return {"head": head, "from": frozen["head"], "files": names.stdout.splitlines(), "diff": diff.stdout,
            "digest": hashlib.sha256(diff.stdout.encode("utf-8")).hexdigest()}


def refreeze(repo: Path, spec: dict, head: str, digest: str) -> dict:
    """The person's approval of `frozen_change` at `head` with `digest`: the card frozen at `head`, or 409.
    The pull request gets the record as a comment, beside the worker's summary."""

    if spec["state"] != "멈춤":
        raise HTTPException(409, "멈춘 카드만 다시 고정한다 — 턴이나 라운드가 도는 중에는 받지 않는다")
    change, frozen, path = frozen_change(spec), spec["refactor_frozen"], Path(spec["worktree"])
    if (change["head"], change["digest"]) != (head, digest):
        raise HTTPException(409, "본 뒤에 고정 테스트나 커밋이 바뀌었다 — 다시 열어 확인해라")
    if not change["files"]:
        raise HTTPException(409, "고정한 뒤 바뀐 고정 테스트가 없다")
    if specs.dirty(path):
        raise HTTPException(409, "작업트리에 커밋하지 않은 변경이 있다")
    if gone := [t for t in frozen["tests"] if not (path / t).is_file()]:
        raise HTTPException(409, f"고정 테스트를 지웠다 — 다시 고정은 고친 파일만 받는다: {', '.join(gone[:10])}")
    code = frozen.get("code") or frozen["head"]   # the characterization commit, kept across approvals
    done = on_old_code(spec, path, frozen["tests"], frozen["test_argv"], code, head)
    if done.returncode:
        raise HTTPException(409, "고정 테스트가 고정 전 코드에서 실패한다\n" + done.stdout[-1500:])
    if specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() != head or specs.dirty(path):
        raise HTTPException(409, "도는 사이에 작업트리나 커밋이 바뀌었다 — 다시 열어 확인해라")
    record = {"from": frozen["head"], "to": head, "code": code, "files": change["files"], "digest": digest,
              "ts": time.time()}
    refrozen = [*frozen.get("refrozen", []), record]
    file = Path(frozen["file"])
    written = {**json.loads(file.read_text(encoding="utf-8")), "head": head, "code": code, "refrozen": refrozen}
    file.write_text(json.dumps(written, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    spec = specs.update(spec["repo"], spec["id"],
                        refactor_frozen={**frozen, "head": head, "code": code, "refrozen": refrozen})
    if number := (spec.get("pr") or {}).get("number"):
        text = (f"사람이 바뀐 동작 고정 테스트를 승인해 `{record['from'][:12]}` → `{head[:12]}` 로 다시 고정했다. "
                f"고정 테스트 전체가 바꾸기 전 코드 `{code[:12]}` 에서 통과했다.\n\n"
                + "\n".join(f"- `{f}`" for f in change["files"]))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
            fh.write(text)
        try:
            said = specs.sh(["gh", "pr", "comment", str(number), "--body-file", fh.name], repo, 60)
        finally:
            os.unlink(fh.name)
        if said.returncode:
            raise HTTPException(502, f"다시 고정했지만 PR 에 기록을 남기지 못했다 — {specs.said(said)}\n\n{text}")
    return spec


# The ignored paths a build writes: they may hold the refactored code compiled (round 2: a package's `main`
# in `dist`), so the old-code run refuses while any is there instead of moving or deleting them (round 3:
# both lost files). Data, assets and tool settings are not here; tests read them (ai-companion-voice-agent's
# `voice_profiles/`, `data/`, `.codex/`).
# ponytail: a name list; a build that writes elsewhere still runs as built — name it here when one does.
BUILT = {"dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache"}


def built(path: Path) -> list[str]:
    """The ignored paths in or named as a `BUILT` directory outside `node_modules`: a collapsed directory, or
    the files beside a tracked `.gitkeep`, which git lists one by one (round 3).
    ponytail: a wholly ignored directory collapses unread (`.venv/` holds a `build` package), so a build
    written inside one, `artifacts/web/dist`, is not seen."""

    listed = specs.sh(["git", "-c", "core.quotepath=off", "ls-files", "--others", "--ignored", "--exclude-standard",
                       "--directory"], path).stdout.splitlines()
    return [p for p in listed if "node_modules/" not in p and set(p.rstrip("/").split("/")) & BUILT]


def on_old_code(spec: dict, path: Path, tests: list[str], argv: list[str], code: str, head: str):
    """`argv` in the card's own worktree switched to `code` with every frozen test laid over it as `head`
    commits it: the paths, environment and dependencies of the freeze's own run, so no import reaches the
    refactored code (a linked worktree reached it through `PYTHONPATH` and workspace links, #247 round 1),
    and no build output of it either: `built` paths refuse the run. `refreezing` on the card names the
    branch from before the switch until `restore` returns to it."""

    branch = specs.sh(["git", "branch", "--show-current"], path).stdout.strip()
    if not branch:
        raise HTTPException(409, "작업트리가 브랜치에 있지 않다")
    if left := built(path):
        raise HTTPException(409, "바꾼 코드의 빌드 결과물일 수 있는 무시된 파일이 있어 고정 전 코드에서 돈다고 할 수 "
                                 "없다 — 지우거나 작업트리 밖으로 옮긴 뒤 다시 해라: "
                                 + ", ".join(f"`{p}`" for p in left[:10]))
    texts = {t: subprocess.run(["git", "show", f"{head}:{t}"], cwd=path, capture_output=True, check=True).stdout
             for t in tests}
    record = {"branch": branch, "head": head, "code": code,
              "tests": {t: hashlib.sha256(text).hexdigest() for t, text in texts.items()}}
    specs.update(spec["repo"], spec["id"], refreezing=record)
    try:
        switched = specs.sh(["git", "checkout", "-q", "--detach", code], path, 120)
        if switched.returncode:
            raise HTTPException(409, f"고정 전 코드로 바꾸지 못했다 — {specs.said(switched)}")
        for t, text in texts.items():
            (path / t).write_bytes(text)
        return refactor_profile.sh(argv, path)
    finally:
        restore(spec, path, record)


def restore(spec: dict, path: Path, record: dict) -> None:
    """Put back what `on_old_code` switched, then clear `refreezing`; 500 naming what is left.
    Nothing is forced: the laid-over tests go back only while they are exactly what was written, the branch
    comes back only by an unforced checkout, and an edit made meanwhile stays (round 2: `-f` erased one)."""

    problems = []
    current = specs.sh(["git", "branch", "--show-current"], path).stdout.strip()
    if not current:   # still on `code`
        for t, digest in record["tests"].items():
            if (path / t).is_file() and hashlib.sha256((path / t).read_bytes()).hexdigest() == digest:
                specs.sh(["git", "checkout", "-q", record["code"], "--", t], path, 60)
        back = specs.sh(["git", "checkout", "-q", record["branch"]], path, 120)
        if back.returncode:
            problems.append(f"`{record['branch']}` 로 돌아가지 못했다: {specs.said(back)}")
        elif specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() != record["head"]:
            problems.append(f"`{record['branch']}` 가 승인한 `{record['head'][:12]}` 가 아니다")
    elif current != record["branch"]:
        problems.append(f"작업트리가 `{record['branch']}` 가 아니라 `{current}` 에 있다")
    if problems:
        raise HTTPException(500, "다시 고정이 작업트리를 다 되돌리지 못했다 — " + " · ".join(problems)
                            + " — 고친 뒤 [고정 테스트 변경 보기] 를 다시 누르면 확인하고 이어 간다")
    specs.update(spec["repo"], spec["id"], refreezing=None)


# The lock files `npm` rewrites beside a `package.json`; no other format is read, so none is accepted.
LOCKS = ("package-lock.json", "npm-shrinkwrap.json")


def dev_only(path: Path, base: str, name: str, changed: list[str]) -> bool:
    """A test-only dependency the characterization added: an existing `package.json` changed in its
    `devDependencies` alone, or the npm lock beside one whose every changed package is marked `dev`.
    Production dependencies, scripts, settings, a new or deleted manifest, and anything unread stay out."""
    folder, _, leaf = name.rpartition("/")
    if leaf != "package.json" and leaf not in LOCKS:
        return False
    old, new = (_json_at(path, rev, name) for rev in (base, "HEAD"))
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if leaf == "package.json":
        return _same(old, new, "devDependencies")
    manifest = posixpath.join(folder, "package.json")
    olds, news = old.get("packages"), new.get("packages")
    if manifest not in changed or not dev_only(path, base, manifest, changed) or not _same(old, new, "packages") \
            or not isinstance(olds, dict) or not isinstance(news, dict):
        return False
    for key in olds.keys() | news.keys():
        before, after = olds.get(key), news.get(key)
        if _same(before, after):
            continue
        if key == "":   # the root package: the manifest's own dependencies
            if not (isinstance(before, dict) and isinstance(after, dict) and _same(before, after, "devDependencies")):
                return False
        elif not all(isinstance(e, dict) and e.get("dev") is True for e in (before, after) if e is not None):
            return False
    return True


def _json_at(path: Path, rev: str, name: str):
    """`name`'s JSON at `rev`, or None when Git cannot show it or it is not JSON."""
    shown = specs.sh(["git", "show", f"{rev}:{name}"], path)
    try:
        return json.loads(shown.stdout) if shown.returncode == 0 else None
    except ValueError:
        return None


def _same(old, new, *but: str) -> bool:
    """The same JSON, `but` keys aside, with its types kept: Python's `1 == True` is not JSON's."""
    def text(value):
        value = {k: v for k, v in value.items() if k not in but} if isinstance(value, dict) else value
        return json.dumps(value, sort_keys=True)
    return text(old) == text(new)


# -- Before the pull request ---------------------------------------------------------

def equivalent(name: str, old: bytes, new: bytes) -> bool:
    """The same test file in another form: a Python file's AST, a JSON file's
    values, or the same bytes. No serializer may erase an expected observation."""
    if old == new:
        return True
    try:
        if name.endswith(".py"):
            return ast.dump(ast.parse(old, type_comments=True), include_attributes=False) == \
                ast.dump(ast.parse(new, type_comments=True), include_attributes=False)
        if name.endswith(".json"):
            def pairs(items):
                if len(dict(items)) != len(items):
                    raise ValueError("Duplicate JSON keys")
                return dict(sorted(items))

            def number(token):
                if token in ("NaN", "Infinity", "-Infinity"):
                    raise ValueError("Nonfinite JSON number")
                return ("number", token)

            def canonical(b):
                return repr(json.loads(b, object_pairs_hook=pairs, parse_int=number, parse_float=number,
                                       parse_constant=number))
            return canonical(old) == canonical(new)
    except (SyntaxError, ValueError, UnicodeError):
        pass
    return False


def added_only(before: bytes, after: bytes) -> bool:
    """Every old line is still there, in order: the file only gained lines."""
    rest = iter(after.splitlines())
    return all(any(line == other for other in rest) for line in before.splitlines())


def _named(stmt) -> str | None:
    return stmt.name if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None


def _head(cls: ast.ClassDef) -> str:
    bare = copy.copy(cls)
    bare.body = []
    return ast.dump(bare)


def _binds(body: list) -> Counter:
    """How often a body binds or deletes each name in its own scope, through every
    `if`/`try`/`for`/`with`/`match` in it: definitions, assignments, imports
    (`*` included), `del`, loop, `with`, `except` and `match` targets, a walrus
    even inside a comprehension, and a nested function's `global`. A
    comprehension's loop variables and a nested scope's locals are not the body's."""

    found = Counter()

    def visit(node, inside: bool = False) -> None:   # inside: within a comprehension's own scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if not isinstance(node, ast.Lambda):
                found[node.name] += 1
                found.update(n for g in ast.walk(node) if isinstance(g, ast.Global) for n in g.names)
            # The header — decorators, bases, defaults, annotations — runs in this scope; only the body is
            # the nested scope's (an argument's own name is a string, not a binding here).
            for field, value in ast.iter_fields(node):
                if field != "body":
                    for child in value if isinstance(value, list) else [value]:
                        if isinstance(child, ast.AST):
                            visit(child, inside)
            return
        if isinstance(node, ast.NamedExpr):
            found[node.target.id] += 1
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and not inside:
            found[node.id] += 1
        elif isinstance(node, ast.alias):
            found[(node.asname or node.name).split(".")[0]] += 1
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            found[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            found[node.rest] += 1
        inside = inside or isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    for stmt in body:
        visit(stmt)
    return found


def _kept(old: list, new: list, allowed: set, at: tuple = ()) -> bool:
    """Every old statement of a body is still there, unchanged and in order, and
    every name it bound is bound as often — a later `def`, assignment or `del`
    would replace a test — except the definitions `allowed` names. A class
    keeps its header and recurses."""

    before, after = _binds(old), _binds(new)
    # A new `pytestmark` skips every test beside it, a new `import *` may replace any of them.
    if any(after[name] != before[name] for name in {*before, "pytestmark", "*"} if (*at, name) not in allowed):
        return False
    rest = iter(new)
    for s in old:
        if (name := _named(s)) and (*at, name) in allowed:
            continue
        for t in rest:
            if isinstance(s, ast.ClassDef) and isinstance(t, ast.ClassDef) and s.name == t.name:
                if _head(s) != _head(t) or not _kept(s.body, t.body, allowed, (*at, s.name)):
                    return False
                break
            if ast.dump(s) == ast.dump(t):
                break
        else:
            return False
    return True


def _defines(body: list, parts: tuple) -> bool:
    found = next((s for s in body if _named(s) == parts[0]), None)
    return found is not None and (len(parts) == 1 or isinstance(found, ast.ClassDef) and _defines(found.body, parts[1:]))


def _declared(spec: dict) -> tuple[set, dict]:
    """The files a spec's test contract frees whole, and the tests it frees by name, per file."""

    tests = spec["refactor"]["detail"].get("tests") or {}
    declared = [t["test"] for t in [*tests.get("remove", []), *tests.get("contract", [])]]
    whole, named = {d for d in declared if "::" not in d}, {}
    for d in declared:
        if "::" in d:
            file, *parts = d.split("::")
            named.setdefault(file, set()).add(tuple(parts))
    return whole, named


# What a refused deletion tells the worker: it cannot declare the file, the person can.
WHOLE_FILE = " — 파일 삭제는 파일 단위로 선언한다. 사람이 [테스트 파일 삭제 승인] 으로 선언할 수 있다"


def contract(path: Path, spec: dict) -> str:
    """Why the existing tests changed outside the spec's test contract, or empty.
    A declared `path` frees the whole file; a declared `path::name` (or
    `path::Class::name`) frees only that test, and it must exist at the start.
    A deleted file needs the file declared, even when every test in it is named:
    which tests pytest collects from a file is not settled by its syntax (#252:
    inheritance, imported TestCases, plugins and configuration hooks each hid one).
    A survey names tests, so the person declares the file (`approve_removal`).
    Python is compared as syntax, so a skip mark or a shadowing redefinition is a change."""

    whole, named = _declared(spec)
    start, broken = spec["start_head"], []
    listed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", start, "HEAD"], path)
    for name in listed.stdout.splitlines():
        if not debt.TEST.search(name.lower()) or name in whole:
            continue
        old = subprocess.run(["git", "show", f"{start}:{name}"], cwd=path, capture_output=True)
        if old.returncode:
            continue   # a new test file
        allowed = named.get(name, set())
        if not (path / name).is_file():
            broken.append(f"`{name}` 삭제{WHOLE_FILE}")
            continue
        new = (path / name).read_bytes()
        if not name.endswith(".py"):
            if allowed or not (equivalent(name, old.stdout, new) or added_only(old.stdout, new)):
                broken.append(f"`{name}` 의 기존 테스트 변경")
            continue
        try:
            before, after = ast.parse(old.stdout, type_comments=True).body, ast.parse(new, type_comments=True).body
        except (SyntaxError, ValueError):
            broken.append(f"`{name}` 을 읽지 못했다")
            continue
        if missing := sorted("::".join(p) for p in allowed if not _defines(before, p)):
            broken.append(f"`{name}` 에 선언한 테스트가 시작 커밋에 없다: {', '.join(missing)}")
        elif not _kept(before, after, allowed):
            broken.append(f"`{name}` 의 기존 테스트 변경")
    return "" if not broken else ("테스트 계약 밖의 변경 — " + ", ".join(broken[:10])
                                  + " (명세의 tests.remove·tests.contract 에 없다)")


def removals(spec: dict) -> dict:
    """`[테스트 파일 삭제 승인]`: the existing test files the card deleted that its contract does not free
    whole, each with the tests named for it and its text at the start; `head` and the `digest` an approval names."""

    if not surveyed(spec) or not spec.get("worktree"):
        raise HTTPException(404, "리팩터링 조사 명세의 작업트리가 없다")
    path, start = Path(spec["worktree"]), spec["start_head"]
    whole, named = _declared(spec)
    head = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    listed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", "--diff-filter=D",
                       start, head], path)
    if not head or listed.returncode:
        raise HTTPException(409, f"시작 커밋과 비교하지 못했다 — {specs.said(listed)}")
    files = []
    for name in listed.stdout.splitlines():
        if debt.TEST.search(name.lower()) and name not in whole:
            text = subprocess.run(["git", "show", f"{start}:{name}"], cwd=path, capture_output=True).stdout
            files.append({"file": name, "named": sorted("::".join(p) for p in named.get(name, ())),
                          "text": text.decode("utf-8", "replace")})
    digest = hashlib.sha256(json.dumps([head, files], ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"head": head, "files": files, "digest": digest}


def approve_removal(spec: dict, head: str, digest: str) -> dict:
    """The person's approval of `removals` at `head` with `digest`: each file declared removed whole in the
    card's test contract, and the card's fault the contract's answer now. 409 when anything moved since."""

    seen = removals(spec)
    if (seen["head"], seen["digest"]) != (head, digest):
        raise HTTPException(409, "본 뒤에 커밋이나 삭제한 파일이 바뀌었다 — 다시 열어 확인해라")
    if not seen["files"]:
        raise HTTPException(409, "승인할 테스트 파일 삭제가 없다")
    tests = spec["refactor"]["detail"].get("tests") or {}
    approved = {"head": head, "ts": time.time()}
    remove = [*tests.get("remove", []), *({"test": f["file"], "reason": "사람이 파일 통째 삭제를 승인했다",
                                          "approved": approved} for f in seen["files"])]
    detail = {**spec["refactor"]["detail"], "tests": {**tests, "remove": remove}}
    spec = {**spec, "refactor": {**spec["refactor"], "detail": detail}}
    fault = spec.get("fault")
    if fault and fault.startswith("테스트 계약 밖의 변경"):
        fault = contract(Path(spec["worktree"]), spec) or None
    return specs.update(spec["repo"], spec["id"], refactor=spec["refactor"], fault=fault)


def refused(path: Path, spec: dict, chosen: dict, head: str) -> tuple[dict, dict] | None:
    """`specs.rounded`'s failed verdict and round record for a survey spec
    outside its test contract: the contract holds on every head a round checks."""

    if not surveyed(spec) or not (broken := contract(path, spec)):
        return None
    verdict = {"ok": False, "reason": broken, "cmd": "테스트 계약", "commands": chosen["commands"], "code": None,
               "tail": broken, "head": head, "ts": time.time(), "selection": chosen["selection"]}
    return verdict, {"head": head, **chosen, "ok": False, "finished_at": verdict["ts"]}


def verified(repo: Path, path: Path, run, spec: dict) -> str:
    """The test contract, the final command against the survey's baseline and the
    measurement: why the pull request must not open, or empty."""

    if problem := contract(path, spec):
        return problem
    # A clone without `origin/HEAD` (one `gh repo create --push` made) asks GitHub, as the pull request will.
    if not (base := specs.local_base(spec, path) or specs.base_of(spec, path).stdout.strip()):
        return "기본 브랜치를 모른다 — 최종 명령을 돌리지 않았다"
    local_only = bool(review_contract.preservation_command(spec))
    # Only the start's record carries over; a verdict of an earlier final command is not this one's.
    started = {"changed": (spec.get("refactor_check") or {}).get("changed", [])}
    cfg = specs.verify.policy(path, project=repo)
    if cfg and not local_only and specs.verify.backend(path, cfg) == "ci":
        # Hosted CI owns the full suite there; a local full run before the pull request would be a second one.
        verdict, record = specs.rounded(repo, path, spec, base, run.halt, lambda text: specs.note(run, text))
        specs.update(spec["repo"], spec["id"], gate=verdict,
                     refactor_check={**started, "final": "ci"})
        specs.validate(spec["repo"], spec["id"], round=record)
        if not verdict["ok"]:
            return f"판정 실패 — {verdict['reason']}"
        return measuring(path, spec, verdict["head"], run.halt)   # whoever owns the suite, the spec's measurement is ours
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
    if not ok and seen.get("code") is None:
        # Nothing ran — no baseline comparison can say anything about it.
        return f"최종 명령을 돌리지 못했다 — {reason or seen.get('reason') or '검증 커밋·base가 다르다'}"
    if not ok and seen["code"] == 0:
        # 0 with a failed verdict: the command never judged this head (it changed the tree, or the head or
        # base moved). A baseline comparison would read that as having no new failure.
        return f"최종 검증이 무효다 — {reason or seen.get('reason') or '검증 커밋·base가 다르다'}"
    after = specs.status(path)
    if not ok and (after.returncode or after.stdout.strip()
                   or specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() != head):
        # Whatever its exit code, a command that changed the tree or the head judged something else.
        return "최종 검증이 무효다 — 최종 명령이 작업트리나 HEAD 를 바꿨다"
    problem, check = compared(repo, path, spec, ok, seen.get("code", 0 if ok else 1), tail, "최종 명령")
    specs.update(spec["repo"], spec["id"], refactor_check={**started, "final": "local", "head": head, "ok": ok, **check})
    return problem or measuring(path, spec, head, run.halt)


def measuring(path: Path, spec: dict, head: str, halt) -> str:
    """A `rework` spec's measurement, kept on the card: why it could not run, or empty."""

    if not (measure := spec["refactor"]["detail"].get("measure")):
        return ""
    try:
        found = measured(path, spec, measure, head, halt)
    except ValueError as exc:
        return f"측정하지 못했다 — {exc}"
    current = specs.load(spec["repo"], spec["id"]) or spec
    specs.update(spec["repo"], spec["id"], refactor_check={**(current.get("refactor_check") or {}), "measured": found})
    return ""


def published(repo: Path, path: Path, run, spec: dict):
    """`specs._check`'s end for a survey spec: `verified`, then the ordinary pull request."""

    if why := verified(repo, path, run, specs.load(spec["repo"], spec["id"]) or spec):
        return specs.failed(run, spec, why)
    specs.note(run, "리팩터링 검증 통과")
    return specs.pr_turn(repo, path, run, specs.load(spec["repo"], spec["id"]) or spec)


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
        for r_ in frozen.get("refrozen", []):
            lines.append(f"  - 사람이 승인해 다시 고정 `{r_['from'][:12]}` → `{r_['to'][:12]}`: 바뀐 고정 테스트 "
                         f"`{'`, `'.join(r_['files'])}` — 바꾸기 전 코드에서 통과")
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
