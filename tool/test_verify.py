"""CI and local ownership, bounded dispatch, and reuse without duplicate suites."""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import verify as ci
from main import decisions, specs

HEAD, BASE = "a" * 40, "b" * 40


@pytest.fixture(autouse=True)
def isolated_local_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "local_lock_path", lambda: tmp_path / "wiki-agent-final.lock")


@pytest.fixture
def hosted_repo(tmp_path, monkeypatch):
    (tmp_path / ci.POLICY).write_text(
        '[verification]\nmode = "auto"\nworkflow = "verify.yml"\nround = "focused-check"\nlocal = "complete-check"\n',
        encoding="utf-8")
    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE, ""))
    monkeypatch.setattr(ci, "locked", lambda *_: nullcontext())
    monkeypatch.setattr(ci, "backend", lambda *_: "ci")
    monkeypatch.setattr(ci, "attested", lambda repo, cfg, row, head, base_oid: {
        "head": head, "base_oid": base_oid, "run_id": row["databaseId"], "attempt": row["attempt"],
        "url": row["url"], "workflow": cfg["workflow"], "backend": "ci"})
    return tmp_path


def completed(**changes):
    return {"databaseId": 10, "headSha": HEAD, "displayTitle": f"Final {HEAD} {BASE}",
            "status": "completed", "conclusion": "success", "url": "https://github.com/o/r/actions/runs/10",
            "attempt": 1, **changes}


def test_local_full_suites_are_refused_but_focused_and_collection_remain_available(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    monkeypatch.delenv("WIKI_FINAL_RUN", raising=False)
    root = Path(ci.__file__).resolve().parent.parent
    config = SimpleNamespace(args=[str(root / "tool")],
                             option=SimpleNamespace(collectonly=False, keyword="", markexpr=""),
                             getoption=lambda *_: False)
    for target in (root, root / "tool", root / "laya"):
        config.args = [str(target)]
        assert ci.local_full_run(config)
    config.args = [str(root / "tool/test_ci.py")]
    assert not ci.local_full_run(config)
    config.args = [str(root / "tool")]
    for field in ("collectonly", "keyword", "markexpr"):
        original = getattr(config.option, field)
        setattr(config.option, field, True if field == "collectonly" else "one_case")
        assert not ci.local_full_run(config)
        setattr(config.option, field, original)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert ci.local_full_run(config), "A local self-hosted runner still uses this PC"
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    assert not ci.local_full_run(config)


def test_only_exact_identity_matches_and_the_newest_failure_wins(monkeypatch, tmp_path):
    rows = [completed(), completed(databaseId=11, conclusion="failure"),
            completed(databaseId=12, headSha="c" * 40), completed(databaseId=13, displayTitle="Pending review")]
    monkeypatch.setattr(ci, "run", lambda *_: json.dumps(rows))
    assert ci.latest(tmp_path, "verify.yml", HEAD, BASE)["databaseId"] == 11
    assert ci.latest(tmp_path, "verify.yml", HEAD, "c" * 40) is None


def test_success_is_reused_without_dispatch_or_pytest(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "latest", lambda *_: completed())
    calls = []

    def run(args, repo):
        calls.append(args)
        assert args[0] == "git" and args[-1] == "--git-common-dir"
        return str(repo)

    monkeypatch.setattr(ci, "run", run)
    first = ci.final(hosted_repo, "main")
    assert ci.final(hosted_repo, "main", dispatch=False) == first
    assert first["head"] == HEAD and first["base_oid"] == BASE and first["attempt"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", "neutral"])
def test_failed_ci_is_never_retried_or_replaced_locally(hosted_repo, monkeypatch, conclusion):
    monkeypatch.setattr(ci, "latest", lambda *_: completed(conclusion=conclusion))
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    with pytest.raises(RuntimeError, match="자동 재실행하지"):
        ci.final(hosted_repo, "main")


def test_interrupted_dispatch_does_not_submit_twice(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "latest", lambda *_: None)
    dispatches = []

    def run(args, repo):
        if args[0] == "git":
            if "--git-common-dir" in args:
                return str(repo)
            return "topic" if "symbolic-ref" in args else f"{HEAD}\trefs/heads/topic"
        dispatches.append(args)
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(ci, "run", run)
    with pytest.raises(ci.Pending, match="CI 통신이 중단"):
        ci.final(hosted_repo, "main")
    with pytest.raises(RuntimeError, match="이미 기록"):
        ci.final(hosted_repo, "main")
    assert len(dispatches) == 1
    # Review round 2: a harvested record changed the receipt's name, not the request it would send.
    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE, "a record changed"))
    with pytest.raises(RuntimeError, match="이미 기록"):
        ci.final(hosted_repo, "main")
    assert len(dispatches) == 1


def test_one_dispatch_waits_then_reuses_the_completed_run(hosted_repo, monkeypatch):
    rows = iter([None, completed(status="in_progress", conclusion=""), completed()])
    monkeypatch.setattr(ci, "latest", lambda *_: next(rows))
    halt = SimpleNamespace(is_set=lambda: False, wait=lambda *_: None)
    calls = []

    def run(args, repo):
        calls.append(args)
        if args[0] == "git":
            if "--git-common-dir" in args:
                return str(repo)
            return "topic" if "symbolic-ref" in args else f"{HEAD}\trefs/heads/topic"
        return ""

    monkeypatch.setattr(ci, "run", run)
    assert ci.final(hosted_repo, "main", halt=halt)["run_id"] == 10
    assert len([args for args in calls if args[0] == "gh"]) == 1


def test_changed_identity_and_unfinished_status_cannot_pass(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "latest", lambda *_: completed())
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    identities = iter([(HEAD, BASE, ""), (HEAD, "c" * 40, "")])
    monkeypatch.setattr(ci, "identity", lambda *_: next(identities))
    with pytest.raises(RuntimeError, match="바뀌었습니다"):
        ci.final(hosted_repo, "main")
    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE, ""))
    monkeypatch.setattr(ci, "latest", lambda *_: completed(status="in_progress", conclusion=""))
    with pytest.raises(RuntimeError, match="아직 완료"):
        ci.final(hosted_repo, "main", dispatch=False)


def test_ci_policy_overrides_legacy_full_local_gates_and_optional_checks(hosted_repo, monkeypatch):
    monkeypatch.setattr(specs, "merge_base", lambda *_: BASE)
    monkeypatch.setattr(specs, "registered", lambda *_: pytest.fail("Legacy adapter mapping must not run"))
    spec = {"done": ["python -m pytest tool"], "pr": {"base": "dev"}}
    assert specs.gate_of(hosted_repo) == "focused-check"
    selected = specs.selected(hosted_repo, hosted_repo, "main", "python -m pytest tool")
    assert selected["commands"] == ["focused-check"] and selected["selection"] == "mapped"
    assert "-m pytest tool" not in specs.required(hosted_repo, spec)
    assert "--base dev" in specs.required(hosted_repo, spec)
    monkeypatch.setattr(decisions, "choose", lambda *_a, **_k: pytest.fail("No additive duplicate checks"))
    assert decisions.extra_check(hosted_repo, hosted_repo, None, spec) == (True, "")


def test_malformed_policy_stops_without_using_a_local_fallback(hosted_repo):
    (hosted_repo / ci.POLICY).write_text(
        '[verification]\nmode = "auto"\nworkflow = "../other.yml"\nround = "focused-check"\nlocal = "complete-check"\n',
        encoding="utf-8")
    with pytest.raises(ValueError):
        specs.required(hosted_repo, {"done": ["python -m pytest tool"]})


def test_unchanged_round_is_reused_and_changed_environment_is_not(hosted_repo, monkeypatch):
    chosen = {"base_oid": BASE, "commands": ["focused-check"], "selection": "mapped", "environment_digest": "same"}
    record = {"head": HEAD, **chosen, "ok": True}
    spec = {"validation": {"round": record}, "gate": {"head": HEAD}}
    monkeypatch.setattr(specs, "sh", lambda args, *_: subprocess.CompletedProcess(args, 0, HEAD if "HEAD" in args else "", ""))
    monkeypatch.setattr(specs, "judge", lambda *_: pytest.fail("Reused proof must not execute checks"))
    verdict, reused = specs.rounded(hosted_repo, hosted_repo, spec, "main", None, chosen=chosen)
    assert verdict["ok"] and reused is record
    monkeypatch.setattr(specs, "judge", lambda *_: {"head": HEAD, "ok": True, "ts": 1})
    _, changed = specs.rounded(hosted_repo, hosted_repo, spec, "main", None,
                               chosen={**chosen, "environment_digest": "changed"})
    assert changed["environment_digest"] == "changed" and changed["finished_at"] == 1


@pytest.mark.parametrize("visibility,active,expected", [("PRIVATE", True, "local"), ("PUBLIC", False, "local"),
                                                       ("PUBLIC", True, "ci")])
def test_backend_discovery_respects_private_repositories_and_missing_ci(tmp_path, monkeypatch, visibility, active, expected):
    calls = []

    def run(args, repo):
        calls.append(args)
        if args[1:3] == ["repo", "view"]:
            return json.dumps({"visibility": visibility, "nameWithOwner": "o/r"})
        if not active:
            raise RuntimeError("HTTP 404")
        return '{"state": "active"}'

    monkeypatch.setattr(ci, "run", run)
    assert ci.backend(tmp_path, {"mode": "auto", "workflow": "verify.yml"}) == expected
    assert len(calls) == (1 if visibility == "PRIVATE" else 2)
    calls.clear()
    assert ci.backend(tmp_path, None) == "local" and not calls
    assert ci.backend(tmp_path, {"mode": "local"}) == "local" and not calls


def test_private_local_verification_runs_once_and_reuses_across_sessions(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    calls = []

    def judge(repo, command, env, halt, timeout):
        calls.append(command)
        assert env["WIKI_FINAL_RUN"] == "1" and env["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "2"
        assert 0 < timeout <= 19 * 60
        return {"ok": True, "head": HEAD, "tail": "passed", "elapsed_seconds": 1}

    proof = ci.final(hosted_repo, "main", judge=judge)
    assert proof["backend"] == "local"
    assert ci.final(hosted_repo, "main", judge=judge) == proof
    assert ci.final(hosted_repo, "main", dispatch=False, judge=judge) == proof
    assert calls == ["complete-check"]


@pytest.mark.parametrize("status", ["failed", "running"])
def test_local_failure_or_interruption_never_retries_automatically(hosted_repo, monkeypatch, status):
    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    calls = []

    def judge(*_):
        calls.append(1)
        if status == "running":
            raise OSError("interrupted")
        return {"ok": False, "reason": "regression", "tail": "original failure"}

    with pytest.raises((RuntimeError, OSError)):
        ci.final(hosted_repo, "main", judge=judge)
    with pytest.raises(RuntimeError, match="자동 재실행하지"):
        ci.final(hosted_repo, "main", judge=judge)
    assert len(calls) == 1


def test_an_existing_ci_owner_never_switches_to_local_on_transport_failure(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    monkeypatch.setattr(ci, "latest", lambda *_: (_ for _ in ()).throw(RuntimeError("network unavailable")))
    with pytest.raises(RuntimeError, match="network unavailable"):
        ci.final(hosted_repo, "main")
    monkeypatch.setattr(ci, "backend", lambda *_: pytest.fail("The owner was already recorded"))
    with pytest.raises(RuntimeError, match="network unavailable"):
        ci.final(hosted_repo, "main", judge=lambda *_: pytest.fail("No local fallback"))


def test_local_override_and_policy_changes_invalidate_old_success(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    def judge(*_):
        return {"ok": True, "head": HEAD}
    first = ci.final(hosted_repo, "main", judge=judge)
    override = hosted_repo / ".wiki" / ci.POLICY
    override.parent.mkdir()
    override.write_text('[verification]\nmode = "local"\n', encoding="utf-8")
    assert ci.policy(hosted_repo)["mode"] == "local"
    with pytest.raises(RuntimeError, match="결과가 없습니다"):
        ci.final(hosted_repo, "main", dispatch=False, judge=judge)
    second = ci.final(hosted_repo, "main", judge=judge)
    assert first["environment_digest"] != second["environment_digest"]


def test_investigated_local_failure_can_resume_with_a_new_environment(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    with pytest.raises(ci.Failure):
        ci.final(hosted_repo, "main", judge=lambda *_: {"ok": False, "reason": "Old command failed"})
    policy = hosted_repo / ci.POLICY
    policy.write_text(policy.read_text(encoding="utf-8").replace("complete-check", "repaired-check"), encoding="utf-8")
    calls = []

    def judge(repo, command, *_):
        calls.append(command)
        return {"ok": True, "head": HEAD}

    proof = ci.final(hosted_repo, "main", retry_reason="Corrected the command after investigation", judge=judge)
    assert ci.final(hosted_repo, "main", judge=judge) == proof and calls == ["repaired-check"]
    records = [json.loads(p.read_text(encoding="utf-8")) for p in hosted_repo.glob("wiki-verification-*.json")]
    assert sorted(r["status"] for r in records) == ["failed", "passed"]


def test_two_local_final_runs_cannot_overlap(tmp_path):
    with ci.locked(tmp_path / "shared.lock"):
        with pytest.raises(RuntimeError, match="중복 실행하지"):
            with ci.locked(tmp_path / "shared.lock"):
                pytest.fail("A second final run acquired the same host lock")


def test_local_final_can_run_integration_fixtures_while_holding_its_host_lock(tmp_path):
    scratch = tmp_path / "outer-owner"
    scratch.mkdir()
    script = '''
import subprocess, sys
sys.path.insert(0, "tool")
import verify
with verify.locked(verify.local_lock_path()):
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-n", "0", "--basetemp", sys.argv[1],
        "tool/test_loop.py::test_a_targeted_pass_cannot_merge_and_the_loop_runs_only_the_final_gate"], timeout=75)
raise SystemExit(done.returncode)
'''
    env = {**os.environ, "TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch),
           "WIKI_FINAL_RUN": "1", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-c", script, str(scratch / "tests")], cwd=Path(ci.__file__).resolve().parent.parent,
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_direct_whole_suite_stops_before_collecting_or_running_tests():
    env = {k: v for k, v in os.environ.items() if k not in {"WIKI_FINAL_RUN", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT"}}
    env["PYTHONUTF8"] = "1"
    result = subprocess.run([sys.executable, "-m", "pytest", "tool", "-n", "0"],
                            cwd=Path(ci.__file__).resolve().parent.parent, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    assert result.returncode == 4 and "전체 검사는 최종 검증 담당" in result.stderr
    assert "collected" not in result.stdout


def test_explicit_local_retry_keeps_the_original_failure(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    with pytest.raises(ci.Failure):
        ci.final(hosted_repo, "main", judge=lambda *_: {"ok": False, "reason": "missing browser"})
    proof = ci.final(hosted_repo, "main", retry_reason="Installed the required browser",
                     judge=lambda *_: {"ok": True, "head": HEAD})
    receipt = json.loads(next(hosted_repo.glob("wiki-verification-*.json")).read_text(encoding="utf-8"))
    assert proof["backend"] == "local" and receipt["history"][0]["reason"] == "missing browser"
    assert receipt["retry_reason"] == "Installed the required browser"


@pytest.mark.parametrize("cut", ["incomplete", "running", "failed"])
def test_only_a_persons_continue_runs_an_unfinished_local_final_again(hosted_repo, monkeypatch, cut):
    """Over its time, or left `running` by a dead server, a run proved nothing;
    a failure may have been fixed outside the code (rfg_rag #73). `again`, the
    statuses the loop's continue may run once more, runs only those (#185 round 2)."""

    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))

    def first(*_):
        if cut == "running":
            raise OSError("the server died mid-run")
        return {"ok": False, "code": None if cut == "incomplete" else 1, "reason": "cut"}

    with pytest.raises(ci.Failure if cut == "failed" else (ci.Pending, OSError)):
        ci.final(hosted_repo, "main", judge=first)
    never = lambda *_: pytest.fail("ran again without a person")  # noqa: E731
    with pytest.raises(ci.Failure if cut == "failed" else ci.Pending, match="자동 재실행하지"):
        ci.final(hosted_repo, "main", judge=never, again=tuple({"incomplete", "running", "failed"} - {cut}))
    proof = ci.final(hosted_repo, "main", again=(cut,), judge=lambda *_: {"ok": True, "head": HEAD})
    receipt = json.loads(next(hosted_repo.glob("wiki-verification-*.json")).read_text(encoding="utf-8"))
    assert proof["backend"] == "local" and receipt["history"][0]["status"] == cut and receipt["retry_reason"]


def test_a_continue_after_a_failed_ci_reads_its_rerun_instead_of_refusing(hosted_repo, monkeypatch):
    """#185 round 1: `again` is a local receipt's; a CI one reads the run's current result."""

    run = {"conclusion": "failure"}
    monkeypatch.setattr(ci, "latest", lambda *_: completed(**run))
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    with pytest.raises(ci.Failure, match="자동 재실행하지"):
        ci.final(hosted_repo, "main")
    run["conclusion"] = "success"   # rerun on GitHub
    proof = ci.final(hosted_repo, "main", again=("failed",), judge=lambda *_: pytest.fail("CI never runs locally"))
    assert proof["backend"] == "ci" and proof["head"] == HEAD


def test_transport_failure_while_polling_stays_pending(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    rows = iter([completed(status="in_progress", conclusion=""), RuntimeError("network unavailable")])

    def latest(*_):
        row = next(rows)
        if isinstance(row, Exception):
            raise row
        return row

    monkeypatch.setattr(ci, "latest", latest)
    with pytest.raises(ci.Pending, match="로컬로 전환하지"):
        ci.final(hosted_repo, "main", judge=lambda *_: pytest.fail("No local fallback"))


def test_workflow_runs_only_requested_candidates_and_refuses_private_jobs():
    import yaml

    root = Path(ci.__file__).resolve().parent.parent
    workflow = yaml.load((root / ".github/workflows/verify.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch", "pull_request"}
    assert "!github.event.repository.private" in workflow["jobs"]["identity"]["if"]
    assert workflow["concurrency"]["cancel-in-progress"] == "true"
    for name in ("python", "laya", "web"):
        assert workflow["jobs"][name]["needs"] == "identity"
        assert "self-hosted" not in workflow["jobs"][name]["runs-on"]
    steps = "\n".join(step.get("run", "") for step in workflow["jobs"]["web"]["steps"])
    assert "npm --prefix web run build" in steps and "task_browser.py" in steps


def test_frozen_local_contract_never_dispatches_ci(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "backend", lambda *_: pytest.fail("Frozen local contract cannot select CI"))
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    calls = []

    def judge(repo, command, *_):
        calls.append(command)
        return {"ok": True, "head": HEAD}

    proof = ci.final(hosted_repo, "main", local_command="full-check && frozen-check", local_only=True, judge=judge)
    assert proof["backend"] == "local" and calls == ["full-check && frozen-check"]


def test_existing_private_project_without_a_policy_uses_its_adapter(tmp_path, monkeypatch):
    import wiki

    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE, ""))
    monkeypatch.setattr(ci, "locked", lambda *_: nullcontext())
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    monkeypatch.setattr(wiki, "slots_for", lambda *_: {"gate_cmd": "legacy-complete-check"})
    commands = []

    def judge(repo, command, *_):
        commands.append(command)
        return {"ok": True, "head": HEAD}

    proof = ci.final(tmp_path, "main", judge=judge)
    assert proof["backend"] == "local" and commands[0].startswith("legacy-complete-check && ")


@pytest.mark.parametrize("jobs,attempt,valid", [([], 1, False),
    ([{"status": "completed", "conclusion": "skipped"}], 1, False),
    ([{"status": "completed", "conclusion": "success"}], 2, False),
    ([{"status": "completed", "conclusion": "success"}], 1, True)])
def test_ci_success_requires_actual_successful_jobs_on_the_same_attempt(tmp_path, monkeypatch, jobs, attempt, valid):
    result = {"headSha": HEAD, "status": "completed", "conclusion": "success", "attempt": attempt, "jobs": jobs}
    monkeypatch.setattr(ci, "run", lambda *_: json.dumps(result))
    if valid:
        assert ci.attested(tmp_path, {"workflow": "verify.yml"}, completed(), HEAD, BASE)["attempt"] == 1
    else:
        with pytest.raises(ci.Pending, match="실제 검사 완료"):
            ci.attested(tmp_path, {"workflow": "verify.yml"}, completed(), HEAD, BASE)


def test_a_local_policy_does_not_require_any_ci_fields(tmp_path, monkeypatch):
    (tmp_path / ci.POLICY).write_text('[verification]\nround = "focused-check"\nlocal = "complete-check"\n', encoding="utf-8")
    cfg = ci.policy(tmp_path)
    monkeypatch.setattr(ci, "run", lambda *_: pytest.fail("No CI configuration must not query GitHub"))
    assert ci.backend(tmp_path, cfg) == "local"


def test_corrupt_cached_proof_cannot_pass_or_trigger_another_run(hosted_repo, monkeypatch):
    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    ci.final(hosted_repo, "main", judge=lambda *_: {"ok": True, "head": HEAD})
    file = next(hosted_repo.glob("wiki-verification-*.json"))
    record = json.loads(file.read_text(encoding="utf-8"))
    record["proof"]["head"] = "c" * 40
    file.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ci.Pending, match="통과 증거"):
        ci.final(hosted_repo, "main", judge=lambda *_: pytest.fail("A corrupt cache must not rerun"))


def test_a_known_public_repository_with_uncertain_ci_does_not_start_local_work(tmp_path, monkeypatch):
    def run(args, repo):
        if args[1:3] == ["repo", "view"]:
            return '{"visibility": "PUBLIC", "nameWithOwner": "o/r"}'
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(ci, "run", run)
    with pytest.raises(ci.Pending, match="로컬 전체 검사는 시작하지"):
        ci.backend(tmp_path, {"mode": "auto", "workflow": "verify.yml"})


def test_no_ci_optional_selection_cannot_repeat_an_already_passed_round_check(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "registered", lambda *_: {
        "repeat": {"cmd": "focused-check", "about": "Already passed"},
        "new": {"cmd": "another-check", "about": "Additional evidence"}})

    def choose(point, offered, *_a, **_k):
        assert [c["id"] for c in offered()] == ["none", "check:new"]
        return SimpleNamespace(candidate={"id": "none"}, operation="summarize_result", record=None, done=lambda *_a, **_k: None)

    monkeypatch.setattr(decisions, "choose", choose)
    monkeypatch.setattr(decisions, "hosted", lambda *_: None)
    spec = {"repo": "fixture", "id": "one-owner", "done": ["complete-check"], "gate": {"head": HEAD},
            "validation": {"round": {"head": HEAD, "commands": ["focused-check"]}}}
    run = SimpleNamespace(turn=1, halt=None, chat=SimpleNamespace(model=None))
    assert decisions.extra_check(tmp_path, tmp_path, run, spec) == (True, "")


def test_worktree_policy_and_project_local_override_have_separate_owners(tmp_path, monkeypatch):
    project, worktree = tmp_path / "project", tmp_path / "worktree"
    for root, mode, command in ((project, "auto", "old"), (worktree, "local", "reviewed")):
        root.mkdir()
        (root / ci.POLICY).write_text(
            f'[verification]\nmode = "{mode}"\nround = "{command}-round"\nlocal = "{command}-full"\n', encoding="utf-8")
    override = project / ".wiki" / ci.POLICY
    override.parent.mkdir()
    override.write_text('[verification]\nmode = "auto"\n', encoding="utf-8")
    (worktree / ".wiki").mkdir()
    (worktree / ".wiki" / ci.POLICY).write_text('[verification]\nmode = "local"\n', encoding="utf-8")
    cfg = ci.policy(worktree, project=project)
    assert cfg["mode"] == "auto" and cfg["local"] == "reviewed-full"
    monkeypatch.setattr(specs, "merge_base", lambda *_: BASE)
    spec = {"worktree": str(worktree), "done": ["legacy-full"]}
    assert specs.local_required(project, spec) == "reviewed-full"
    assert "reviewed-full" in specs.required(project, spec)
    assert specs.for_round(project, worktree, spec, "main")["commands"] == ["reviewed-round"]
    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE, ""))
    monkeypatch.setattr(ci, "run", lambda *_: str(worktree))
    commands = []

    def judge(repo, command, *_):
        commands.append(command)
        return {"ok": True, "head": HEAD}

    assert ci.final(worktree, "main", project=project, judge=judge)["backend"] == "local"
    assert commands == ["reviewed-full"]
    (worktree / ci.POLICY).unlink()
    assert ci.policy(worktree, project=project) is None, "A removed policy must not come back from the project"


@pytest.mark.parametrize("move_after_judge", [False, True])
def test_a_head_race_never_leaves_a_reusable_local_pass(hosted_repo, monkeypatch, move_after_judge):
    monkeypatch.setattr(ci, "backend", lambda *_: "local")
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    if move_after_judge:
        identities = iter([(HEAD, BASE, ""), ("c" * 40, BASE, "")])
        monkeypatch.setattr(ci, "identity", lambda *_: next(identities))
    with pytest.raises(ci.Pending):
        ci.final(hosted_repo, "main", judge=lambda *_: {"ok": True, "head": HEAD if move_after_judge else "c" * 40})
    receipt = json.loads(next(hosted_repo.glob("wiki-verification-*.json")).read_text(encoding="utf-8"))
    assert receipt["status"] == "incomplete" and "proof" not in receipt
    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE, ""))
    with pytest.raises(ci.Pending, match="자동 재실행하지"):
        ci.final(hosted_repo, "main", judge=lambda *_: pytest.fail("The untested head cannot reuse or rerun"))


@pytest.fixture
def diverged_repo(tmp_path):
    def git(*args):
        return ci.run(["git", *args], tmp_path)

    git("init", "-q", "-b", "main")
    git("config", "user.name", "fixture")
    git("config", "user.email", "fixture@example.com")
    git("commit", "-qm", "base", "--allow-empty")
    base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "task")
    git("commit", "-qm", "candidate", "--allow-empty")
    head = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    git("commit", "-qm", "advanced base", "--allow-empty")
    tip = git("rev-parse", "HEAD")
    git("checkout", "-q", "task")
    return tmp_path, head, base, tip


def test_real_base_fetch_failure_is_preparation_without_starting_a_check(diverged_repo, monkeypatch):
    repo, _, _, _ = diverged_repo
    ci.run(["git", "remote", "add", "origin", str(repo / "missing-origin.git")], repo)
    monkeypatch.setattr(ci, "local_result", lambda *_: pytest.fail("No test starts when preparation fails"))
    result = ci.verdict(repo, repo, "main", "complete-check", None)
    assert not result["ok"] and result["pending"] and "missing-origin" in result["reason"]


def test_label_bootstrap_normalizes_an_advanced_base_tip_to_the_reviewed_merge_base(diverged_repo, monkeypatch):
    import yaml

    repo, head, base, tip = diverged_repo
    root = Path(ci.__file__).resolve().parent.parent
    workflow = yaml.load((root / ".github/workflows/verify.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    script = workflow["jobs"]["identity"]["steps"][1]["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    env = {**os.environ, "EXPECTED_HEAD": head, "EXPECTED_BASE": tip, "EVENT_NAME": "pull_request", "DISPATCH_HEAD": head}
    result = subprocess.run([sys.executable, "-c", script], cwd=repo, env=env, capture_output=True, text=True, check=True)
    assert f"merge base: {base}" in result.stdout and base != tip
    real = ci.run
    row = completed(headSha=head, displayTitle=f"Final {head} {tip}")
    monkeypatch.setattr(ci, "run", lambda args, path: json.dumps([row]) if args[0] == "gh" else real(args, path))
    assert ci.latest(repo, "verify.yml", head, base) == row


@pytest.mark.parametrize("bad_first", [True, False])
def test_a_malformed_prior_ci_run_cannot_hide_valid_evidence(diverged_repo, monkeypatch, bad_first):
    repo, head, base, _ = diverged_repo
    valid = completed(headSha=head, displayTitle=f"Final {head} {base}")
    invalid = completed(databaseId=11, headSha=head, displayTitle=f"Final {head} {'0' * 40}", conclusion="failure")
    rows = [invalid, valid] if bad_first else [valid, invalid]
    real = ci.run
    monkeypatch.setattr(ci, "run", lambda args, path: json.dumps(rows) if args[0] == "gh" else real(args, path))
    assert ci.latest(repo, "verify.yml", head, base) == valid


@pytest.mark.parametrize("event,action,label,shares_final", [
    ("workflow_dispatch", "", "", True), ("pull_request", "synchronize", "", True),
    ("pull_request", "labeled", "ci-final", True), ("pull_request", "labeled", "bug", False),
    ("pull_request", "opened", "", False), ("pull_request", "reopened", "", False)])
def test_only_requests_and_new_commits_can_cancel_a_final_run(event, action, label, shares_final):
    import yaml

    root = Path(ci.__file__).resolve().parent.parent
    workflow = yaml.load((root / ".github/workflows/verify.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    expression = workflow["concurrency"]["group"].split("${{", 1)[1].rsplit("}}", 1)[0]
    context = SimpleNamespace(event_name=event, head_ref="task" if event == "pull_request" else "", ref_name="task",
        run_id=42, event=SimpleNamespace(action=action, label=SimpleNamespace(name=label)))
    group = eval(expression.strip().replace("&&", " and ").replace("||", " or "),
                 {"__builtins__": {}, "github": context, "format": lambda text, value: text.format(value)})
    assert (group == "task") == shares_final
