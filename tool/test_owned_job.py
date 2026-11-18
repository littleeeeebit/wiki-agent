"""The owned job: a process created inside it, every member followed to its exit, and a proof that holds
whenever coverage or an exit is not proven (docs/research/oneshot-desktop-session.md)."""

import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows jobs")

if sys.platform == "win32":
    import native_process
    import owned_job

SLEEP = "import time; time.sleep(60)"
TREE = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        " subprocess.run([sys.executable, '-c', 'pass']); time.sleep(60)")


@pytest.fixture
def job(tmp_path):
    made = owned_job.Job()
    yield made
    if not made.ended:
        made.end(5)
    made.close()


def started(job, tmp_path, code, **options):
    with open(tmp_path / "out.log", "wb") as log:
        return job.start([sys.executable, "-c", code], tmp_path, dict(os.environ), None, log, **options)


def settled(job, count):
    deadline = time.monotonic() + 20
    while len(job.members()) < count and time.monotonic() < deadline:
        time.sleep(0.2)
    return job.members()


def test_the_root_is_in_its_job_before_it_runs_and_every_member_is_proven_gone(job, tmp_path):
    early = []
    root = started(job, tmp_path, TREE, before_resume=lambda identity: early.append(job.members()))

    assert [member["pid"] for member in early[0]] == [root["pid"]]
    members = settled(job, 3)
    proof = job.end()

    assert proof["clean"], proof["reasons"]
    assert proof["total"] == proof["joined"] >= 3 and proof["active"] == 0
    assert all(native_process.identity(member["pid"]) is None
               or not native_process.same(member, native_process.identity(member["pid"])) for member in members)


def test_a_member_whose_join_and_exit_were_both_lost_leaves_the_proof_inconclusive(job, tmp_path, monkeypatch):
    root = started(job, tmp_path, TREE)
    real = job._messages
    lost = set()

    def dropping(wait_ms):
        found = real(wait_ms)
        for message, pid in found:
            if message == owned_job.NEW and pid != root["pid"] and not lost:
                lost.add(pid)   # the first child's join, and later its exit, never arrive
        return [(message, pid) for message, pid in found if pid not in lost]

    monkeypatch.setattr(job, "_messages", dropping)
    time.sleep(3)
    proof = job.end()

    assert lost and not proof["clean"]
    assert any("counted" in reason for reason in proof["reasons"])


def test_a_member_whose_exit_is_never_seen_is_not_called_gone(job, tmp_path, monkeypatch):
    started(job, tmp_path, SLEEP)
    settled(job, 1)
    monkeypatch.setattr(job, "_gone", lambda entry: False)

    proof = job.end(1)

    assert not proof["clean"] and any("no exit proven" in reason for reason in proof["reasons"])


def test_a_program_that_cannot_start_is_refused_before_anything_runs(job, tmp_path):
    with open(tmp_path / "out.log", "wb") as log, pytest.raises(owned_job.Refused, match="cannot start"):
        job.start([str(tmp_path / "missing.exe")], tmp_path, dict(os.environ), None, log)

    assert job.members() == []


def test_an_app_that_asks_for_elevation_is_refused_without_a_shell_retry(job, tmp_path, monkeypatch):
    monkeypatch.setattr(job, "_create", lambda *_: owned_job.ELEVATION_REQUIRED)

    with open(tmp_path / "out.log", "wb") as log, pytest.raises(owned_job.Refused, match="elevation"):
        job.start([sys.executable], tmp_path, dict(os.environ), None, log)


def test_the_job_is_named_once_and_records_the_uptime_it_was_made_at():
    first, second = owned_job.Job(), owned_job.Job()
    try:
        assert first.name != second.name and first.name.startswith("Local\\wiki-oneshot-")
        assert 0 < first.uptime <= second.uptime
    finally:
        first.close()
        second.close()
