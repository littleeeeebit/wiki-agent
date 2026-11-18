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
    """Wait until `count` members joined; a member that already exited still counts."""

    deadline = time.monotonic() + 20
    while len(job.joined) < count and time.monotonic() < deadline:
        job.members()
        time.sleep(0.2)
    return job.members()


def test_the_root_is_in_its_job_before_it_runs_and_every_member_is_proven_gone(job, tmp_path):
    early = []
    root = started(job, tmp_path, TREE, before_resume=lambda identity: early.append(job.members()))

    assert [member["pid"] for member in early[0]] == [root["pid"]]
    members = settled(job, 3)   # the root, its sleeping child, its child that exits at once
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


def fed(job, monkeypatch, *batches, total=None):
    """The job's port and count replaced: `batches` arrive one per pump, and the count reads `total`."""

    queue = iter(batches)
    monkeypatch.setattr(job, "_messages", lambda wait_ms: next(queue, []))
    monkeypatch.setattr(job.k, "OpenProcess", lambda *_: None)
    if total is not None:
        monkeypatch.setattr(job, "_count", lambda: owned_job._Accounting(total=total))


def test_a_member_never_held_and_never_seen_leaving_is_not_called_gone(job, monkeypatch):
    fed(job, monkeypatch, [(owned_job.NEW, 101)], total=1)

    proof = job.end(0.5)

    assert len(job.joined) == 1 and not proof["clean"]
    assert any("no exit proven for pid 101" in reason for reason in proof["reasons"])


def test_messages_that_arrive_late_are_waited_for_before_the_count_is_judged(job, monkeypatch):
    fed(job, monkeypatch, [(owned_job.NEW, 101), (owned_job.EXIT, 101)], [],
        [(owned_job.NEW, 102), (owned_job.EXIT, 102)], total=2)

    proof = job.end(2)

    assert proof["clean"], proof["reasons"]


def test_an_exit_message_never_stands_in_for_a_held_handle_that_is_not_signalled(job, monkeypatch):
    monkeypatch.setattr(job.k, "WaitForSingleObject", lambda *_: 258)   # WAIT_TIMEOUT

    assert not job._gone({"pid": 101, "handle": 201, "identity": None, "exited": True})
    assert job._gone({"pid": 101, "handle": None, "identity": None, "exited": True})


def test_a_termination_windows_refused_never_gives_a_clean_proof(job, monkeypatch):
    monkeypatch.setattr(job.k, "TerminateJobObject", lambda *_: 0)

    proof = job.end(0.1)

    assert not proof["clean"] and any("cannot terminate" in reason for reason in proof["reasons"])


def test_a_root_whose_identity_cannot_be_read_never_runs(job, tmp_path, monkeypatch):
    monkeypatch.setattr(native_process, "identity", lambda *_: None)
    recorded = []

    with pytest.raises(owned_job.Refused, match="identity"):
        started(job, tmp_path, SLEEP, before_resume=recorded.append)

    assert recorded == []
    assert job.end(5)["clean"]


def test_a_record_that_fails_before_the_root_runs_ends_the_suspended_root(job, tmp_path):
    def failing(identity):
        raise OSError("the record could not be written")

    with pytest.raises(OSError):
        started(job, tmp_path, SLEEP, before_resume=failing)

    assert job.end(5)["clean"]


def test_a_root_whose_rollback_windows_refused_stays_held_until_the_job_ends_it(job, tmp_path, monkeypatch):
    def failing(identity):
        raise OSError("the record could not be written")

    with monkeypatch.context() as patch:
        patch.setattr(job.k, "TerminateProcess", lambda *_: 0)
        with pytest.raises(OSError):
            started(job, tmp_path, SLEEP, before_resume=failing)

    assert len(job.stray) == 1 and job.k.WaitForSingleObject(job.stray[0], 0) != 0   # still suspended, held
    proof = job.end(5)

    assert proof["clean"], proof["reasons"]   # the job's end killed it, and its own handle shows that


def test_a_root_still_alive_after_the_proof_wait_is_named(job, monkeypatch):
    job.stray.append(owned_job.w.HANDLE(1))
    monkeypatch.setattr(job.k, "WaitForSingleObject", lambda *_: 258)   # WAIT_TIMEOUT

    proof = job.end(0.1)

    assert not proof["clean"] and any("refused at start" in reason for reason in proof["reasons"])
    job.stray.clear()


def test_a_flag_that_cannot_be_restored_after_creation_rolls_the_root_back(job, tmp_path, monkeypatch):
    stopped, closed, real = [], [], os.set_handle_inheritable

    def failing(handle, inheritable):
        if not inheritable:
            raise OSError("restore failed")
        real(handle, inheritable)

    with monkeypatch.context() as patch:
        patch.setattr(os, "set_handle_inheritable", failing)
        patch.setattr(job, "_create", lambda *_: (101, 102, 103))
        patch.setattr(job.k, "TerminateProcess", lambda handle, _: stopped.append(handle) or 1)
        patch.setattr(job.k, "WaitForSingleObject", lambda *_: 0)
        patch.setattr(job.k, "CloseHandle", lambda handle: closed.append(handle) or 1)
        with pytest.raises(OSError, match="restore failed"):
            started(job, tmp_path, SLEEP)

    assert stopped == [101] and {101, 102} <= set(closed)


def test_the_root_inherits_only_its_own_input_and_log(job, tmp_path):
    import msvcrt

    with (tmp_path / "unrelated.txt").open("wb") as unrelated:
        handle = msvcrt.get_osfhandle(unrelated.fileno())
        os.set_handle_inheritable(handle, True)
        try:
            code = ("import ctypes; b = ctypes.create_unicode_buffer(4096); "
                    f"ctypes.windll.kernel32.GetFinalPathNameByHandleW(ctypes.c_void_p({handle}), b, len(b), 0); "
                    "print(int(b.value.lower().endswith('unrelated.txt')), flush=True)")
            started(job, tmp_path, code)
        finally:
            os.set_handle_inheritable(handle, False)
        log, deadline = tmp_path / "out.log", time.monotonic() + 20
        while not log.stat().st_size and time.monotonic() < deadline:
            time.sleep(0.05)

    assert log.read_text(encoding="utf-8").strip() == "0"


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
