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


@pytest.fixture
def hosted_repo(tmp_path, monkeypatch):
    (tmp_path / ci.POLICY).write_text(
        '[verification]\nmode = "auto"\nworkflow = "verify.yml"\nround = "focused-check"\nlocal = "complete-check"\n',
        encoding="utf-8")
    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE))
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
    identities = iter([(HEAD, BASE), (HEAD, "c" * 40)])
    monkeypatch.setattr(ci, "identity", lambda *_: next(identities))
    with pytest.raises(RuntimeError, match="바뀌었습니다"):
        ci.final(hosted_repo, "main")
    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE))
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
        return {"ok": True, "tail": "passed", "elapsed_seconds": 1}

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
        return {"ok": True}
    first = ci.final(hosted_repo, "main", judge=judge)
    override = hosted_repo / ".wiki" / ci.POLICY
    override.parent.mkdir()
    override.write_text('[verification]\nmode = "local"\n', encoding="utf-8")
    assert ci.policy(hosted_repo)["mode"] == "local"
    with pytest.raises(RuntimeError, match="결과가 없습니다"):
        ci.final(hosted_repo, "main", dispatch=False, judge=judge)
    second = ci.final(hosted_repo, "main", judge=judge)
    assert first["environment_digest"] != second["environment_digest"]


def test_two_local_final_runs_cannot_overlap(tmp_path):
    with ci.locked(tmp_path / "shared.lock"):
        with pytest.raises(RuntimeError, match="중복 실행하지"):
            with ci.locked(tmp_path / "shared.lock"):
                pytest.fail("A second final run acquired the same host lock")


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
                     judge=lambda *_: {"ok": True})
    receipt = json.loads(next(hosted_repo.glob("wiki-verification-*.json")).read_text(encoding="utf-8"))
    assert proof["backend"] == "local" and receipt["history"][0]["reason"] == "missing browser"
    assert receipt["retry_reason"] == "Installed the required browser"


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
        return {"ok": True}

    proof = ci.final(hosted_repo, "main", local_command="full-check && frozen-check", local_only=True, judge=judge)
    assert proof["backend"] == "local" and calls == ["full-check && frozen-check"]


def test_existing_private_project_without_a_policy_uses_its_adapter(tmp_path, monkeypatch):
    import wiki

    monkeypatch.setattr(ci, "identity", lambda *_: (HEAD, BASE))
    monkeypatch.setattr(ci, "locked", lambda *_: nullcontext())
    monkeypatch.setattr(ci, "run", lambda args, repo: str(repo))
    monkeypatch.setattr(wiki, "slots_for", lambda *_: {"gate_cmd": "legacy-complete-check"})
    commands = []

    def judge(repo, command, *_):
        commands.append(command)
        return {"ok": True}

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
    ci.final(hosted_repo, "main", judge=lambda *_: {"ok": True})
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
