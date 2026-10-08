"""Bound verification work without weakening current-head evidence or cleanup."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from main import runtime, specs, verification
from common import process
from common.budget import Exhausted
from conftest import pytest_xdist_auto_num_workers
from test_local_verification import cloud_spec, cloud_world, git_world  # noqa: F401
from test_loop import (  # noqa: F401
    commit, looped, no_machine_settings, pr_spec, template,
)


def test_only_whole_suite_requests_default_to_parallel_workers(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    option = SimpleNamespace(keyword="", markexpr="")
    config = SimpleNamespace(args=[str(Path(__file__).parent)], option=option, getoption=lambda *_: False)
    assert pytest_xdist_auto_num_workers(config) == 8
    option.keyword = "one_case"
    assert pytest_xdist_auto_num_workers(config) == 0
    option.keyword, option.markexpr = "", "integration"
    assert pytest_xdist_auto_num_workers(config) == 0
    option.markexpr, config.getoption = "", lambda *_: True
    assert pytest_xdist_auto_num_workers(config) == 0
    config.args, config.getoption = [str(Path(__file__))], lambda *_: False
    assert pytest_xdist_auto_num_workers(config) == 0
    config.args = [str(Path(__file__).parent)]
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    assert pytest_xdist_auto_num_workers(config) == 1


def test_stopped_or_spent_gate_never_starts_a_process(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Spent work must not start"))
    halt = threading.Event()
    assert specs.gate("unused", tmp_path, halt, timeout=0)[0] is None
    halt.set()
    assert specs.gate("unused", tmp_path, halt)[2] == "사람이 멈춤"


def test_local_deadline_is_inherited_by_preparation_and_restored_after_failure(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.append(k["timeout"]) or subprocess.CompletedProcess(a, 0, "", ""))
    halt = threading.Event()
    assert runtime.verification_budget.get() is None
    with runtime.verification_scope(halt, 3) as parent:
        with runtime.verification_scope(halt, 100) as child:
            assert child is parent and parent.limits["seconds"] == 3
            specs.sh(["unused"], tmp_path, 60)
            assert 0 < seen[-1] <= 3
        parent.deadline = time.monotonic() - 1
        with pytest.raises(Exhausted):
            specs.sh(["must-not-start"], tmp_path)
        assert len(seen) == 1
    assert runtime.verification_budget.get() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows owned background setup")
def test_setup_does_not_wait_for_a_child_holding_stdout(tmp_path):
    command = subprocess.list2cmdline([sys.executable, "-c", "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
        "stdout=sys.stdout,stderr=sys.stderr,close_fds=False); print(p.pid,flush=True)"])
    jobs, started = [], time.monotonic()
    try:
        code, out, cut = specs.gate(command, tmp_path, threading.Event(), timeout=3, owned_jobs=jobs)
        assert code == 0 and not cut and out.strip().isdigit() and len(jobs) == 1
        assert time.monotonic() - started < 3
    finally:
        for job in jobs:
            process.terminated(job)


def test_one_slow_flow_becomes_pending_before_it_can_consume_the_attempt(cloud_world, monkeypatch):  # noqa: F811
    world = cloud_world
    runner = world.repo / "verify.py"
    runner.write_text("import time; time.sleep(30)\n" + runner.read_text(encoding="utf-8"), encoding="utf-8")
    commit(world.repo, "slow-flow.txt")
    spec = pr_spec(world, "flow-cap", 1, "change.py")
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    monkeypatch.setattr(runtime, "FLOW_SECONDS", 0.6)
    record = verification.execute(world.repo, spec, path, head, base, threading.Event())["local_verification"]
    assert record["state"] == "waiting_environment" and record["flows"][0]["blocked"]
    assert not record["flows"][0]["ok"] and record["budget"]["limits"]["seconds"] == runtime.LOCAL_SECONDS
    assert record["flows"][0]["elapsed_seconds"] < 10


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata capture")
def test_windows_metadata_capture_keeps_utf8_without_pipe_reader_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("Metadata must not start pipe readers"))
    command = [sys.executable, "-X", "utf8", "-c",
               "import sys; sys.stdout.write('한글\\n' * 65536); sys.stderr.write('오류\\n'); sys.exit(3)"]
    done = specs.sh(command, tmp_path, timeout=10)
    assert done.returncode == 3 and done.stdout == "한글\n" * 65536 and done.stderr == "오류\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows inherited pipe timeout")
def test_metadata_timeout_does_not_wait_for_a_descendant_holding_stdout(tmp_path):
    command = [sys.executable, "-X", "utf8", "-c",
               "import subprocess,sys,time; "
               "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(8)'], "
               "stdout=sys.stdout, stderr=sys.stderr, close_fds=False); "
               "print(child.pid, flush=True); print('partial-stderr', file=sys.stderr, flush=True); time.sleep(8)"]
    started, child_pid = time.monotonic(), None
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            specs.sh(command, tmp_path, timeout=3)
        child_pid = int(caught.value.stdout.strip())
        assert caught.value.stderr == "partial-stderr\n" and time.monotonic() - started < 6
    finally:
        # The known child sleeps eight seconds from its creation; after that,
        # leave an expired PID alone rather than risking a reused identity.
        if child_pid is not None and time.monotonic() - started < 8:
            subprocess.run(["taskkill", "/PID", str(child_pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, **process.background_options())


def test_judgement_commands_share_one_deadline(git_world):  # noqa: F811
    world = git_world
    command = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(0.6); print('complete')"])
    result = specs.judge(world.repo, [command, command], threading.Event(), timeout=0.9)
    assert not result["ok"] and result["code"] is None and "시간 제한" in result["reason"]
    assert result["elapsed_seconds"] < 3


def test_verification_stops_after_first_failure_and_still_cleans_up(cloud_world):  # noqa: F811
    world = cloud_world
    world.contract["flows"].append({**world.contract["flows"][0], "id": "later", "command": "python verify.py later"})
    (world.repo / verification.MANIFEST).write_text(json.dumps(world.contract), encoding="utf-8")
    marker = world.tmp / "cleaned"
    cleanup = subprocess.list2cmdline([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
    settings = {**world.settings, "cleanup": cleanup, "manifest_digest": verification.manifest(world.repo)[1]}
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    commit(world.repo, "two-flows.txt")
    spec = pr_spec(world, "fail-fast", 1, "change.py", implementation_environment="claude-cloud")
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    world.failed.touch()
    result = verification.execute(world.repo, spec, path, head, base, threading.Event())["local_verification"]
    assert result["state"] == "waiting_cloud" and [r["id"] for r in result["flows"]] == ["health"]
    assert result["flows"][0]["elapsed_seconds"] >= 0 and marker.exists()
    assert result["failure_attempts"] and "private-api-key" not in json.dumps(result)


@pytest.mark.parametrize("setup_seconds", [3.5, 12.0])
def test_setup_and_flows_share_budget_and_cleanup_runs_after_timeout(cloud_world, monkeypatch, setup_seconds):  # noqa: F811
    world = cloud_world
    runner = world.repo / "verify.py"
    runner.write_text("import time; time.sleep(3.5)\n" + runner.read_text(encoding="utf-8"), encoding="utf-8")
    marker = world.tmp / "cleaned"
    settings = {**world.settings, "setup": subprocess.list2cmdline([sys.executable, "-c", f"import time; time.sleep({setup_seconds})"]),
                "cleanup": subprocess.list2cmdline([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])}
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    commit(world.repo, "slow-runner.txt")
    spec = pr_spec(world, "budget", 1, "change.py")
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    monkeypatch.setattr(specs, "GATE_SECONDS", 10)
    monkeypatch.setattr(runtime, "CLEANUP_SECONDS", 2)
    record = verification.execute(world.repo, spec, path, head, base, threading.Event())["local_verification"]
    assert record["state"] == "waiting_environment" and "시간 제한" in record["reason"]
    assert not any(r.get("ok") for r in record.get("flows", [])) and marker.exists()


def test_unchanged_cloud_review_reuses_setup_but_environment_change_reruns_it(cloud_world):  # noqa: F811
    world = cloud_world
    marker = world.tmp / "setups"
    setup = subprocess.list2cmdline([sys.executable, "-c", f"from pathlib import Path; p=Path({str(marker)!r}); "
                                    "p.write_text((p.read_text() if p.exists() else '') + 'setup\\n')"])
    settings = {**world.settings, "setup": setup}
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    cloud_spec(world)
    assert looped("cloud")["state"] == "머지 가능"
    assert looped("cloud")["state"] == "머지 가능" and len(marker.read_text().splitlines()) == 1
    settings["revisions"]["api"] = "2"
    (world.repo / verification.LOCAL).write_text(json.dumps(settings), encoding="utf-8")
    assert looped("cloud")["state"] == "머지 가능" and len(marker.read_text().splitlines()) == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows temporary directory policy")
def test_verification_artifacts_and_child_scratch_use_native_temp(cloud_world, monkeypatch):  # noqa: F811
    world = cloud_world
    native, inherited = world.tmp / "native", world.tmp / "application"
    native.mkdir()
    inherited.mkdir()
    runner = world.repo / "verify.py"
    runner.write_text("import os,tempfile\nfrom pathlib import Path\n"
                      "(Path(os.environ['WIKI_VERIFICATION_ARTIFACTS']) / 'scratch-root').write_text("
                      "tempfile.gettempdir(), encoding='utf-8')\n" + runner.read_text(encoding="utf-8"), encoding="utf-8")
    commit(world.repo, "scratch-runner.txt")
    spec = pr_spec(world, "scratch", 1, "change.py")
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    monkeypatch.setenv("TEMP", str(native))
    monkeypatch.setenv("TMPDIR", str(inherited))
    monkeypatch.setattr(verification.tempfile, "tempdir", str(inherited))
    record = verification.execute(world.repo, spec, path, head, base, threading.Event())["local_verification"]
    assert record["state"] == "runtime_passed" and Path(record["artifact_dir"]).is_relative_to(native)
    assert Path((Path(record["artifact_dir"]) / "scratch-root").read_text(encoding="utf-8")) == native


@pytest.mark.skipif(os.name != "nt", reason="Windows temporary directory policy")
def test_test_scratch_uses_native_temp_instead_of_inherited_application_tmpdir(tmp_path):
    script = (Path(__file__).with_name("conftest.py")).resolve()
    native, inherited = tmp_path / "native", tmp_path / "application"
    native.mkdir()
    inherited.mkdir()
    command = [sys.executable, "-c", f"import runpy,tempfile; runpy.run_path({str(script)!r}); print(tempfile.gettempdir())"]
    done = subprocess.run(command, env={**os.environ, "TEMP": str(native), "TMPDIR": str(inherited)},
                          capture_output=True, text=True, encoding="utf-8", check=True, timeout=10)
    assert Path(done.stdout.strip()) == native
