"""One final verification owner: hosted CI when available, otherwise bounded local work."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import tomllib

POLICY = "verification.toml"
# Untracked files the app itself writes into a checkout, never the task's work: the start hook's records
# (`tool/sync.py`) and a cleared conversation's memory pair (`main/memory.py`). Left out, a memory written
# beside a refactor turn read as its uncommitted tests (ai-coding-wiki `preserve-repository-boundaries`).
HARVESTED = (".wiki/decisions/", ".wiki/memory/")
# Only a ceiling: a stuck local run is cut when its output stops growing (`runtime.IDLE_SECONDS`).
FINAL_SECONDS = 2 * 60 * 60


class Pending(RuntimeError):
    """Preparation or interrupted work needs attention, not an implementation repair."""


class Failure(RuntimeError):
    """A completed check failed; preserve it and return it to the implementation owner."""


def policy(repo, *, project=None):
    path = Path(repo) / POLICY
    if not path.is_file():
        return None
    value = tomllib.loads(path.read_text(encoding="utf-8")).get("verification")
    if not isinstance(value, dict) or set(value) - {"mode", "workflow", "round", "local"}:
        raise ValueError("verification.toml의 verification 설정이 올바르지 않습니다")
    value = {"mode": "auto", "workflow": "", **value}
    if not all(isinstance(value.get(k), str) and value[k].strip() for k in ("round", "local")) \
            or not isinstance(value["mode"], str) or value["mode"] not in {"auto", "local"} \
            or not isinstance(value["workflow"], str) \
            or value["workflow"] and not re.fullmatch(r"[\w-]+\.ya?ml", value["workflow"]):
        raise ValueError("verification.toml의 verification 설정이 올바르지 않습니다")
    override = Path(project or repo) / ".wiki" / POLICY
    if override.is_file():
        local = tomllib.loads(override.read_text(encoding="utf-8")).get("verification")
        if not isinstance(local, dict) or set(local) != {"mode"} or not isinstance(local["mode"], str) \
                or local["mode"] not in {"auto", "local"}:
            raise ValueError(".wiki/verification.toml에는 verification.mode만 지정하세요")
        value = {**value, **local}
    return value


def hosted():
    return os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"


def local_full_run(config):
    if hosted() or os.environ.get("WIKI_FINAL_RUN") == "1" or config.option.collectonly \
            or config.option.keyword or config.option.markexpr or config.getoption("lf", False):
        return False
    root = Path(__file__).resolve().parent.parent
    return any(Path(arg.split("::", 1)[0]).resolve() in (root, root / "tool", root / "laya") for arg in config.args)


def command(repo, base, local, *, local_only=False):
    args = [sys.executable, str(Path(__file__).resolve()), "final", "--base", base,
            "--project", str(repo), "--local-command", local]
    if local_only:
        args.append("--local-only")
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    import shlex

    return shlex.join(args)


def environment_digest(repo, path, cmd, locks, adapter=None):
    def sha(file):
        try:
            return hashlib.sha256(file.read_bytes()).hexdigest()
        except OSError:
            return ""

    found = {f.relative_to(path).as_posix(): sha(f) for pattern in (*locks, POLICY, ".github/workflows/*.yml")
             for f in sorted(path.glob(pattern))}
    found["local_verification_policy"] = sha(repo / ".wiki" / POLICY)
    shown = {"cmd": cmd, "python": (sys.version, sys.executable),
             "adapter": sha(adapter or repo / ".wiki/adapter.toml"), "files": found}
    return hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()


def cached_round(spec, chosen, current, clean):
    last = (spec.get("validation") or {}).get("round") or {}
    head = current.stdout.strip()
    if last.get("ok") and (last.get("head"), last.get("base_oid"), last.get("commands")) == (
        head, chosen["base_oid"], chosen["commands"]
    ) and last.get("environment_digest") == chosen.get("environment_digest") \
            and not current.returncode and head and not clean.returncode and not clean.stdout.strip():
        return {**(spec.get("gate") or {}), "head": head, "ok": True, "cmd": " && ".join(chosen["commands"]),
                "reason": "", "selection": chosen["selection"], "tail": "같은 커밋의 검사 결과 재사용"}
    return None


def run(args, repo):
    done = subprocess.run(args, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip() or f"{args[0]} exited {done.returncode}")
    return done.stdout.strip()


@contextmanager
def locked(path):
    """Keep the inode and fail promptly; never queue another heavy local run."""
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as exc:
            raise Pending("이 저장소의 다른 최종 검증이 진행 중입니다. 중복 실행하지 않았습니다") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def identity(repo, base):
    from common import indexes   # here: `conftest` imports this file as `tool.verify`, before `tool` is a path

    head = run(["git", "rev-parse", "HEAD"], repo)
    # v2: `run` strips the leading space of a v1 row. The start hook's rebuilt indexes and harvested
    # records are not changes; untracked files one by one, so a folded folder hides nothing beside them.
    rows = run(["git", "status", "--porcelain=v2", "--untracked-files=all", "-z"], repo).split("\0")
    records = sorted(r[2:] for r in rows if r.startswith("? ") and r[2:].startswith(HARVESTED))
    if any(r and r[2:] not in records and not indexes.hook_row(repo, r) for r in rows):
        raise RuntimeError("커밋하지 않은 변경이 있어 최종 검증 결과를 사용할 수 없습니다")
    # Yet a check may read them (`lint.py` lists untracked files): the evidence is bound to their bytes.
    held = hashlib.sha256(json.dumps({r: hashlib.sha256((Path(repo) / r).read_bytes()).hexdigest()
                                      for r in records}).encode()).hexdigest()
    run(["git", "fetch", "--quiet", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], repo)
    return head, run(["git", "merge-base", "HEAD", f"origin/{base}"], repo), held


def backend(repo, cfg):
    """Discover once before work starts. Private or unconfigured repositories never dispatch CI."""
    if cfg is None or cfg["mode"] == "local" or not cfg.get("workflow"):
        return "local"
    try:
        info = json.loads(run(["gh", "repo", "view", "--json", "visibility,nameWithOwner"], repo))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        return "local"
    if not isinstance(info, dict) or info.get("visibility") != "PUBLIC":
        return "local"
    try:
        workflow = json.loads(run(["gh", "api", f"repos/{info['nameWithOwner']}/actions/workflows/{cfg['workflow']}"], repo))
        if not isinstance(workflow, dict):
            raise Pending("CI 설정 응답의 형식이 올바르지 않습니다")
        return "ci" if workflow.get("state") == "active" else "local"
    except RuntimeError as exc:
        if re.search(r"\b404\b", str(exc)):
            return "local"
        raise Pending(f"공개 저장소의 CI 설정을 확인하지 못했습니다. 로컬 전체 검사는 시작하지 않습니다 — {exc}") from exc
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise Pending("CI 설정 조회가 중단됐습니다. 로컬 전체 검사는 시작하지 않습니다") from exc


def latest(repo, workflow, head, base_oid):
    rows = json.loads(run(["gh", "run", "list", "--workflow", workflow, "--commit", head,
                           "--limit", "100", "--json",
                           "databaseId,headSha,displayTitle,status,conclusion,url,attempt"], repo))
    fields = {"databaseId", "headSha", "displayTitle", "status", "conclusion", "url", "attempt"}
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not fields <= r.keys() for r in rows):
        raise Pending("CI 실행 목록 응답의 형식이 올바르지 않습니다")
    matches = []
    for row in rows:
        title = re.fullmatch(rf"Final {re.escape(head)} ([0-9a-f]{{40}})", row["displayTitle"])
        if row["headSha"] != head or not title:
            continue
        declared = title[1]
        # A first-install label names the base tip; normalize it to the same merge base as dispatch.
        try:
            actual = base_oid if declared == base_oid else run(["git", "merge-base", head, declared], repo)
        except RuntimeError:
            continue  # An invalid request's title is not evidence against other valid runs.
        if actual == base_oid:
            matches.append(row)
    return max(matches, key=lambda r: r["databaseId"], default=None)


def saved(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def attested(repo, cfg, row, head, base_oid):
    view = json.loads(run(["gh", "run", "view", str(row["databaseId"]), "--json",
                           "attempt,headSha,jobs,status,conclusion"], repo))
    if not isinstance(view, dict):
        raise Pending("CI 검사 응답의 형식이 올바르지 않습니다")
    jobs = view.get("jobs") or []
    if (view.get("headSha"), view.get("attempt"), view.get("status"), view.get("conclusion")) != (
        head, row["attempt"], "completed", "success"
    ) or not isinstance(jobs, list) or not jobs or any(not isinstance(j, dict) or j.get("conclusion") != "success"
                                                    or j.get("status") != "completed" for j in jobs):
        raise Pending("CI의 실제 검사 완료 증거가 없습니다. 건너뛴 검사나 변경된 실행은 통과로 처리하지 않습니다")
    return {"head": head, "base_oid": base_oid, "backend": "ci", "run_id": row["databaseId"],
            "attempt": row["attempt"], "url": row["url"], "workflow": cfg["workflow"]}


def ci_result(repo, cfg, head, base_oid, record, receipt, dispatch, deadline, halt):
    try:
        return _ci_result(repo, cfg, head, base_oid, record, receipt, dispatch, deadline, halt)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if isinstance(exc, (Failure, Pending)):
            raise
        raise Pending(f"CI 통신이 중단됐습니다. 로컬로 전환하지 않습니다 — {exc}") from exc


def requested(common, head, base_oid):
    """A CI request for this head and base is on any receipt: the dispatch names neither the local
    command nor the harvested records, so a receipt that differs only in those is the same request.
    One that cannot be read may hold a request too: it stops, named, never read as no request."""
    for path in common.glob(f"wiki-verification-{head}-{base_oid}-*.json"):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("requested"):
                return True
        except (OSError, ValueError, AttributeError) as exc:
            raise Pending(f"같은 커밋·base의 최종 검증 영수증을 읽지 못해 CI 요청 여부를 알 수 없습니다. "
                          f"영수증을 확인하거나 지운 뒤 다시 시도하세요 — {path} — {exc}") from exc
    return False


def _ci_result(repo, cfg, head, base_oid, record, receipt, dispatch, deadline, halt):
    try:
        row = latest(repo, cfg["workflow"], head, base_oid)
    except RuntimeError as exc:
        raise Pending(f"CI 결과 조회 실패 — {exc}") from exc
    if row is None:
        if not dispatch:
            raise Pending("현재 커밋·base의 CI 결과가 없습니다")
        if record.get("requested") or requested(receipt.parent, head, base_oid):
            raise Pending("CI 요청이 이미 기록돼 있습니다. GitHub에서 기존 요청을 확인하세요")
        branch = run(["git", "symbolic-ref", "--short", "HEAD"], repo)
        remote = run(["git", "ls-remote", "origin", f"refs/heads/{branch}"], repo).split()
        if not remote or remote[0] != head:
            raise RuntimeError("현재 커밋을 task 브랜치에 게시한 뒤 CI를 요청하세요")
        # Persist before sending: an ambiguous timeout cannot safely submit again.
        record["requested"] = time.time()
        saved(receipt, record)
        run(["gh", "workflow", "run", cfg["workflow"], "--ref", branch,
             "-f", f"head={head}", "-f", f"base_oid={base_oid}"], repo)
    while time.monotonic() < deadline and not halt.is_set():
        row = latest(repo, cfg["workflow"], head, base_oid)
        if row:
            print(f"CI · {row['status']} · {row.get('conclusion') or '진행 중'} · {row['url']}", flush=True)
            if row["status"] == "completed":
                if row["conclusion"] != "success":
                    error = Failure if row["conclusion"] == "failure" else Pending
                    raise error(f"CI {row['conclusion']} — {row['url']} — 자동 재실행하지 않습니다")
                return attested(repo, cfg, row, head, base_oid)
        if not dispatch:
            raise Pending("CI가 아직 완료되지 않았습니다")
        halt.wait(min(10, max(0, deadline - time.monotonic())))
    raise Pending("CI 대기가 중단됐습니다. 기존 실행은 유지하며 로컬 전체 검사는 시작하지 않습니다")


def local_result(repo, command, record, receipt, dispatch, deadline, halt, judge):
    """Runs under `final`'s lock, one per repository: another repository's final run goes on beside it."""
    if record.get("status") == "passed":
        return record["proof"]
    if record.get("status"):
        error = Failure if record["status"] == "failed" else Pending
        raise error(f"기존 로컬 최종 검사: {record['status']} — {record.get('reason', '')}. 자동 재실행하지 않습니다")
    if not dispatch:
        raise Pending("현재 커밋·조건의 로컬 최종 검사 결과가 없습니다")
    if not command:
        raise ValueError("로컬 최종 검사 명령을 먼저 등록하세요")
    record.update(status="running", started_at=time.time(), command=command)
    saved(receipt, record)
    env = {**os.environ, "WIKI_FINAL_RUN": "1", "PYTEST_XDIST_AUTO_NUM_WORKERS": "2", "PYTHONUTF8": "1"}
    print(f"로컬 최종 검사 · 한 번만 실행 · {command}", flush=True)
    verdict = judge(repo, command, env, halt, max(0, deadline - time.monotonic()))
    if verdict.get("ok") and verdict.get("head") != record["head"]:
        verdict = {**verdict, "ok": False, "code": None, "reason": "검토한 커밋과 실제 검사한 커밋이 다릅니다"}
    record.update(status="passed" if verdict["ok"] else "incomplete" if verdict.get("code", 1) is None else "failed",
                  reason=verdict.get("reason", ""),
                  tail=verdict.get("tail", ""), finished_at=time.time(),
                  elapsed_seconds=verdict.get("elapsed_seconds"))
    if not verdict["ok"]:
        saved(receipt, record)
        error = Pending if record["status"] == "incomplete" else Failure
        raise error(f"로컬 최종 검사 실패 — {record['reason']}\n{record['tail']}")
    proof = {"head": record["head"], "base_oid": record["base_oid"], "backend": "local",
             "environment_digest": record["environment_digest"], "finished_at": record["finished_at"]}
    record["proof"] = proof
    saved(receipt, record)
    return proof


def carried(repo, common, head, base_oid, signature):
    """A local pass of an ancestor that differs from `head` only in the generated wiki indexes:
    merge preparation's commit. ai-generation #60 ran its 30-minute suite again for one line of
    `.wiki/corpus.json`, and the rerun failed twice on load. Same base and signature only."""
    from common import indexes

    for path in common.glob(f"wiki-verification-*-{base_oid}-{signature}.json"):
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            proof = old.get("proof") if isinstance(old, dict) else None
            # The proof stands for its own receipt, as `final` demands of one read at that head.
            if not isinstance(proof, dict) or old.get("status") != "passed" or old.get("head") in (None, head) \
                    or (old.get("base_oid"), old.get("environment_digest"), old.get("backend")) != (base_oid, signature, "local") \
                    or (proof.get("head"), proof.get("base_oid"), proof.get("environment_digest"), proof.get("backend")) \
                    != (old["head"], base_oid, signature, "local"):
                continue
            run(["git", "merge-base", "--is-ancestor", old["head"], head], repo)
            # A submodule a config tells diff to ignore still changes what the tests read.
            names = run(["git", "diff", "--ignore-submodules=none", "--no-renames", "--name-only", "-z", old["head"], head],
                        repo).split("\0")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            continue
        if all(name in indexes.INDEXES for name in names if name):
            return {"head": head, "base_oid": base_oid, "environment_digest": signature, "backend": "local",
                    "status": "passed", "finished_at": old.get("finished_at"),
                    "proof": {**proof, "head": head, "carried_from": old["head"]}}
    return None


def local_judge(repo, command, env, halt, timeout):
    from main import specs

    return specs.judge(repo, [command], halt, env=env, timeout=timeout)


def final(repo, base, *, project=None, local_command="", dispatch=True, seconds=FINAL_SECONDS, halt=None,
          judge=local_judge, retry_reason="", noted=None, local_only=False, again=()):
    project = Path(project or repo)
    cfg = policy(repo, project=project)
    local_command = local_command or (cfg or {}).get("local", "")
    if not local_command and cfg is None:
        import debt
        from wiki import slots_for

        gate = slots_for(project.name, project).get("gate_cmd", "").strip()
        local_command = " && ".join(filter(None, [gate, debt.command(base)])) if gate else ""
    halt = halt or threading.Event()
    deadline = time.monotonic() + seconds
    head, base_oid, records = identity(repo, base)
    common = Path(run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], repo))
    inputs = environment_digest(project, Path(repo), local_command,
                                ("requirements*.txt", "pyproject.toml", "pytest.ini", "web/package-lock.json"))
    signature = hashlib.sha256(json.dumps({"policy": cfg, "command": local_command, "local_only": local_only,
                                          "inputs": inputs, "records": records, "python": [sys.version, sys.executable]}, sort_keys=True).encode()).hexdigest()
    receipt = common / f"wiki-verification-{head}-{base_oid}-{signature}.json"
    with locked(common / "wiki-verification.lock"):
        record = json.loads(receipt.read_text(encoding="utf-8")) if receipt.is_file() else \
            carried(repo, common, head, base_oid, signature) or {
            "head": head, "base_oid": base_oid, "environment_digest": signature,
            "backend": "local" if local_only else backend(repo, cfg)}
        if not isinstance(record, dict) or (record.get("head"), record.get("base_oid"), record.get("environment_digest")) != (
            head, base_oid, signature
        ) or record.get("backend") not in ("ci", "local"):
            raise Pending("저장된 최종 검증 기록의 형식·대상이 맞지 않습니다. 재실행하지 않았습니다")
        # A run cut short — over its time, stopped, or left `running` by a dead
        # server (this lock is held for a live one) — proved nothing either way;
        # a failure may have been fixed outside the code. `again` names the statuses
        # the loop's continue may run once more — a local receipt's; CI reads its run.
        unfinished = dispatch and record["backend"] == "local" and record.get("status") in again
        retry_reason = retry_reason or ("끝나지 못했거나 코드 밖을 고친 로컬 최종 검사를 계속했다" if unfinished else "")
        if retry_reason and receipt.is_file():
            if not dispatch or record["backend"] != "local" or record.get("status") not in {"failed", "running", "incomplete"}:
                raise ValueError("재시도 사유는 실패·중단된 로컬 검사에만 지정하세요. CI는 기존 실행 화면에서 처리하세요")
            # Keep the original attempt; a recorded reason is required for an intentional retry.
            history = [*record.get("history", []), {k: v for k, v in record.items() if k != "history"}]
            record = {"head": head, "base_oid": base_oid, "environment_digest": signature, "backend": "local",
                      "history": history, "retry_reason": retry_reason}
        if halt.is_set() or time.monotonic() >= deadline:
            raise Pending("최종 검사 시작 전에 중단됐습니다")
        if noted:
            noted(record["backend"])
        if dispatch:
            saved(receipt, record)
        if record["backend"] == "ci":
            proof = ci_result(repo, cfg, head, base_oid, record, receipt, dispatch, deadline, halt)
        else:
            proof = local_result(repo, local_command, record, receipt, dispatch, deadline, halt, judge)
        if not isinstance(proof, dict) or (proof.get("head"), proof.get("base_oid"), proof.get("backend")) != (
            head, base_oid, record["backend"]
        ) or record["backend"] == "local" and proof.get("environment_digest") != signature:
            raise Pending("저장된 통과 증거가 현재 커밋·검사 조건과 다릅니다")
        try:
            if identity(repo, base) != (head, base_oid, records):
                raise Pending("검증 대기 중 커밋·base·결정 기록이 바뀌었습니다")
        except (RuntimeError, OSError, subprocess.SubprocessError):
            if dispatch and record["backend"] == "local":
                record.update(status="incomplete", reason="검증 뒤 커밋·base를 확인하지 못했습니다")
                record.pop("proof", None)
                saved(receipt, record)
            raise
        return proof


def verdict(project, repo, base, command, halt, noted=None, local_only=False, retry_reason="", again=()):
    started = time.monotonic()
    try:
        proof = final(repo, base, project=project, local_command=command, halt=halt, noted=noted,
                      local_only=local_only, retry_reason=retry_reason, again=again)
        result = {"ok": True, "head": proof["head"], "code": 0, "reason": "", "verification": proof,
                  "tail": "VERIFY_PROOF " + json.dumps(proof)}
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "head": "", "code": 1, "reason": str(exc), "tail": str(exc),
                  "pending": not isinstance(exc, Failure)}
    return {**result, "elapsed_seconds": round(time.monotonic() - started, 3)}


def confirm(project, repo, base, command, expected, local_only=False):
    proof = final(repo, base, project=project, local_command=command, local_only=local_only, dispatch=False)
    if proof != expected:
        raise Pending("최종 검증 뒤 실행·조건이 바뀌었습니다")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["final", "status"])
    parser.add_argument("--base", default="main")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--local-command", default="")
    parser.add_argument("--retry-reason", default="")
    parser.add_argument("--local-only", action="store_true", help="Keep a frozen local preservation contract on its original owner")
    args = parser.parse_args()
    try:
        proof = final(Path.cwd(), args.base, project=args.project, local_command=args.local_command,
                      dispatch=args.action == "final", retry_reason=args.retry_reason.strip(), local_only=args.local_only)
        print("VERIFY_PROOF " + json.dumps(proof, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"최종 검증 확인 중단 — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
