"""A desktop look: its record before anything starts, apps in owned jobs on the owner's desktop with disposable
data folders, a cleanup proof on every outcome, and recovery of an earlier look's record
(docs/research/oneshot-desktop-session.md)."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktops")

if sys.platform == "win32":
    import desktop_session
    import native_process

SLEEP = "import time; time.sleep(60)"
LISTEN = "import socket, time; s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); time.sleep(60)"
ENVIRONMENT = ("import json, os; print(json.dumps({key: os.environ[key] for key in "
               "('APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'EXTRA')}), flush=True); import time; time.sleep(60)")


def python(code):
    return [sys.executable, "-c", code]


def waited(check, seconds=20):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.2)
    return check()


def test_a_look_runs_its_apps_on_the_owners_desktop_and_proves_everything_gone(tmp_path):
    # No look opens a desktop of its own (plan 13): nothing it starts is out of the owner's sight. These apps have
    # no window, so a test run puts nothing on the screen.
    assert not hasattr(desktop_session.Look, "open")
    with desktop_session.look(tmp_path, "first") as look:
        root = look.start("app", python(SLEEP), tmp_path)
        listener = look.start("server", python(LISTEN), tmp_path)
        folder = look.reserve("files")
        assert look.record["jobs"]["app"]["root"] == root and "desktop" not in look.record
        assert waited(lambda: any(pid == listener["pid"] for state, _, _, pid in native_process._sockets(3)
                                  if state == 2))

    record = desktop_session.load(tmp_path / "first")
    assert record["state"] == "proven" and record["proof"]["clean"], record["proof"]
    assert record["listeners"] and not list((tmp_path / "first").glob("data-*")) and not folder.exists()
    assert all(desktop_session.exited(member) for entry in record["jobs"].values() for member in entry["members"])


def test_a_command_run_on_the_look_returns_when_its_root_exits_and_what_it_left_ends_with_the_look(tmp_path):
    import threading

    left = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); " \
           "print('drawn', flush=True); sys.exit(3)"
    with desktop_session.look(tmp_path, "ran") as look:
        started = time.monotonic()
        code, output, cut = look.run("preview", python(left), tmp_path, 30, threading.Event())
        assert (code, output.strip(), cut) == (3, "drawn", "") and time.monotonic() - started < 20
        never, _, cut = look.run("slow", python(SLEEP), tmp_path, 1, threading.Event())
        assert never is None and cut
        _, long, _ = look.run("long", python("print('x' * 200000 + 'end', flush=True)"), tmp_path, 30,
                              threading.Event())
        assert long.strip().endswith("end") and len(long) <= desktop_session.LOG_TAIL   # its end, bounded

    record = desktop_session.load(tmp_path / "ran")
    assert record["state"] == "proven" and record["proof"]["clean"], record["proof"]
    assert len(record["jobs"]["preview"]["members"]) >= 2   # the root and what it left running


def test_a_run_counts_an_exit_read_by_its_deadline_and_starts_nothing_once_halted(tmp_path):
    import threading
    from types import SimpleNamespace

    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "preview.log").write_bytes(b"")
    reads = iter([None, 0])

    def read(root):   # the exit is first seen by a read that ends past the deadline
        value = next(reads)
        if value is not None:
            time.sleep(0.05)
        return value

    look = SimpleNamespace(folder=tmp_path, jobs={"preview": SimpleNamespace(code=read, members=lambda: [])},
                           start=lambda *a: {"pid": 1}, observe=lambda: None, shown={})
    code, _, cut = desktop_session.Look.run(look, "preview", ["x"], tmp_path, 0.05, threading.Event())
    assert code is None and cut
    halted = threading.Event()
    halted.set()
    look.start = lambda *a: pytest.fail("nothing starts once halted")
    assert desktop_session.Look.run(look, "preview", ["x"], tmp_path, 30, halted) == (None, "", "멈춤")


@pytest.mark.parametrize("lost", [False, True])
def test_a_halt_while_the_root_is_recorded_ends_it_before_it_runs(tmp_path, monkeypatch, lost):
    if lost:   # the port's exit messages are best effort: the rollback's own confirmed exit must still prove it
        read = desktop_session.owned_job.Job._messages
        monkeypatch.setattr(desktop_session.owned_job.Job, "_messages", lambda self, wait_ms, most=64: [
            (m, pid) for m, pid in read(self, wait_ms, most)
            if m not in (desktop_session.owned_job.EXIT, desktop_session.owned_job.ABNORMAL)])

    class Late:   # not set when the run begins, set by the time the suspended root is recorded
        reads = 0

        def is_set(self):
            self.reads += 1
            return self.reads > 1

    marker = tmp_path / "ran"
    with desktop_session.look(tmp_path, "late") as look:
        found = look.run("preview", python(f"open({str(marker)!r}, 'w').close()"), tmp_path, 30, Late())
        assert found == (None, "", "멈춤") and look.record["jobs"]["preview"]["root"]

    time.sleep(0.5)
    record = desktop_session.load(tmp_path / "late")
    assert not marker.exists() and record["state"] == "proven" and record["proof"]["clean"], record["proof"]


@pytest.mark.parametrize("stop", [False, True])   # `ready` refuses, or a stop comes while it reads
def test_a_start_whose_ready_check_refuses_or_is_stopped_ends_the_root_before_it_runs(tmp_path, stop):
    class Moved(Exception):
        pass

    halt = threading.Event()

    def ready():
        if not stop:
            raise Moved("the build moved")
        halt.set()

    marker = tmp_path / "ran"
    with desktop_session.look(tmp_path, "ready") as look:
        with pytest.raises(desktop_session.Halted if stop else Moved):
            look.start("app", python(f"open({str(marker)!r}, 'w').close()"), tmp_path, halt=halt, ready=ready)
        assert look.record["jobs"]["app"]["root"]   # recorded, suspended, then rolled back

    time.sleep(0.5)
    record = desktop_session.load(tmp_path / "ready")
    assert not marker.exists() and record["state"] == "proven" and record["proof"]["clean"], record["proof"]


def test_the_app_keeps_its_data_inside_the_looks_disposable_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRA", "inherited")   # a setting spelt in another case still replaces it
    with desktop_session.look(tmp_path, "data") as look:
        look.start("app", python(ENVIRONMENT), tmp_path, {"extra": "kept", "temp": str(tmp_path), "TMP": "x"})
        log = tmp_path / "data" / "evidence" / "app.log"
        assert waited(lambda: log.stat().st_size > 0)
        seen = json.loads(log.read_text(encoding="utf-8"))
        data = look.folder / look.record["folders"][0]["name"]

    assert seen == {"APPDATA": str(data / "Roaming"), "LOCALAPPDATA": str(data / "Local"),
                    "TEMP": str(data / "Temp"), "TMP": str(data / "Temp"), "EXTRA": "kept"}


def test_a_setting_the_start_maps_to_none_reaches_the_app_in_no_spelling(tmp_path, monkeypatch):
    monkeypatch.setenv("GONE_KEY", "inherited")   # a secret not given: the server's own variable never stands in
    code = ("import json, os; print(json.dumps([k for k in os.environ if k.upper() == 'GONE_KEY']), flush=True); "
            "import time; time.sleep(60)")
    with desktop_session.look(tmp_path, "dropped") as look:
        look.start("app", python(code), tmp_path, {"gone_key": None})
        log = tmp_path / "dropped" / "evidence" / "app.log"
        assert waited(lambda: log.stat().st_size > 0)
        assert json.loads(log.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("disposable", [False, True])
def test_a_disposable_looks_evidence_goes_with_it_and_another_looks_stays(tmp_path, disposable):
    with desktop_session.look(tmp_path, "kept", disposable=disposable) as look:
        look.start("app", python("print('raw output', flush=True)"), tmp_path)
        (look.folder / "evidence" / "driver1").mkdir()
        (look.folder / "evidence" / "driver1" / "s00-1.png").write_bytes(b"shot")
        log = tmp_path / "kept" / "evidence" / "app.log"
        assert waited(lambda: log.stat().st_size > 0)
    record = desktop_session.load(tmp_path / "kept")
    assert record["state"] == "proven" and record["proof"]["clean"], record["proof"]
    assert (tmp_path / "kept" / "evidence").exists() is not disposable
    assert desktop_session.recover(tmp_path / "kept") == []


def test_the_record_names_each_part_before_it_is_made(tmp_path, monkeypatch):
    seen = {}
    early = tmp_path / "early"
    look = desktop_session.Look(tmp_path, "early")
    mkdir, start = Path.mkdir, desktop_session.owned_job.Job.start

    def making(path, *args, **options):
        if path.parent == early and path.name.startswith("data-"):
            seen["folder"] = desktop_session.load(early)["folders"] == [{"name": path.name, "owned": False}]
        return mkdir(path, *args, **options)

    def starting(job, *args, before_resume=None, **options):
        seen["job"] = desktop_session.load(early)["jobs"]["app"]["name"] == job.name

        def recorded(root):
            before_resume(root)
            seen["root"] = desktop_session.load(early)["jobs"]["app"]["root"] == root
        return start(job, *args, before_resume=recorded, **options)

    monkeypatch.setattr(Path, "mkdir", making)
    monkeypatch.setattr(desktop_session.owned_job.Job, "start", starting)
    try:
        look.start("app", python(SLEEP), tmp_path)
    finally:
        assert look.close()["clean"]

    assert seen == {"folder": True, "job": True, "root": True}


def test_a_live_look_of_this_same_server_is_never_recovered(tmp_path):
    with desktop_session.look(tmp_path, "active") as look:
        root = look.start("app", python(SLEEP), tmp_path)
        before = desktop_session.load(look.folder)

        assert desktop_session.recover(look.folder) == ["active: its server is still running"]
        assert desktop_session.load(look.folder) == before and desktop_session.exited(root) is False


def test_an_owner_that_cannot_be_shown_to_have_exited_holds_its_record(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    record = desktop_session.load(folder)
    record["owner"] = {"pid": 4, "created": 1, "exe": "x"}
    desktop_session.save(folder, record)
    monkeypatch.setattr(desktop_session, "exited", lambda recorded: None)

    assert desktop_session.recover(folder) == ["crashed: its server cannot be shown to have exited"]
    assert desktop_session.load(folder)["state"] == "owning"


def test_a_folder_the_look_did_not_create_is_never_removed(tmp_path, monkeypatch):
    with pytest.raises(desktop_session.Refused, match="not created by the look"):
        with desktop_session.look(tmp_path, "collision") as look:
            existing = look.folder / "data-app-000000000000"
            (existing / "Roaming").mkdir(parents=True)
            (existing / "Roaming" / "marker").write_text("keep", encoding="utf-8")
            monkeypatch.setattr(desktop_session.uuid, "uuid4", lambda: type("Fixed", (), {"hex": "0" * 32})())
            with pytest.raises(FileExistsError):
                look.start("app", python(SLEEP), tmp_path)

    assert (tmp_path / "collision" / "data-app-000000000000" / "Roaming" / "marker").read_text(encoding="utf-8") == "keep"


def test_a_job_that_fails_to_end_does_not_skip_the_rest_of_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace

    look = desktop_session.Look(tmp_path, "failure")
    calls = []

    def failing(_seconds):
        raise OSError("first job failed")

    late = {"pid": 7, "created": 1, "exe": "late.exe"}   # first heard of while its job ended

    def ending(_seconds):
        calls.append("second.end")
        return {"clean": True, "reasons": [], "members": [late]}

    look.jobs = {"first": SimpleNamespace(end=failing, close=lambda: calls.append("first.close"), outstanding=lambda: 0),
                 "second": SimpleNamespace(end=ending, close=lambda: calls.append("second.close"),
                                           outstanding=lambda: 0)}
    look.record["jobs"] = {key: {"members": []} for key in look.jobs}
    monkeypatch.setattr(look, "observe", lambda: None)
    monkeypatch.setattr(native_process, "_sockets", lambda _kind: [])
    monkeypatch.setattr(look, "_exits", lambda recorded: calls.append("exits") or [])

    proof = look.close(0)

    assert calls == ["first.close", "second.end", "second.close", "exits"]
    assert not proof["clean"] and any("first job failed" in reason for reason in proof["reasons"])
    assert desktop_session.load(look.folder)["state"] == "held" and look.close() == proof
    assert desktop_session.load(look.folder)["jobs"]["second"]["members"] == [late]   # recovery names it too


def test_a_member_learnt_while_its_job_ended_is_recorded_even_when_the_close_fails(tmp_path, monkeypatch):
    from types import SimpleNamespace

    look = desktop_session.Look(tmp_path, "late")
    late = {"pid": 7, "created": 1, "exe": "late.exe"}

    def closing():
        raise OSError("close failed")

    look.jobs = {"app": SimpleNamespace(end=lambda _s: {"clean": True, "reasons": [], "members": [late]},
                                        close=closing, outstanding=lambda: 0)}
    look.record["jobs"] = {"app": {"members": []}}
    monkeypatch.setattr(look, "observe", lambda: None)
    monkeypatch.setattr(native_process, "_sockets", lambda _kind: [])

    proof = look.close(0)

    assert not proof["clean"] and desktop_session.load(look.folder)["jobs"]["app"]["members"] == [late]


def test_an_interrupted_cleanup_is_never_proven(tmp_path, monkeypatch):
    look = desktop_session.Look(tmp_path, "interrupted")

    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(look, "observe", interrupted)
    with pytest.raises(KeyboardInterrupt):
        look.close()

    proof = desktop_session.load(look.folder)["proof"]
    assert not proof["clean"] and "cleanup was interrupted" in proof["reasons"]


def test_an_interruption_waits_until_every_handle_is_released(tmp_path, monkeypatch):
    from types import SimpleNamespace

    look = desktop_session.Look(tmp_path, "released")
    look.jobs.update(first=SimpleNamespace(outstanding=lambda: 0), second=SimpleNamespace(outstanding=lambda: 0))
    look.record["jobs"] = {key: {"members": []} for key in look.jobs}
    released = []
    monkeypatch.setattr(look, "observe", lambda: None)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])

    def ending(key, job, seconds):
        if key == "first":
            raise KeyboardInterrupt
        released.append(key)
        return []

    monkeypatch.setattr(look, "_ended", ending)
    monkeypatch.setattr(look, "_exits", lambda recorded: released.append("exits"))
    with pytest.raises(KeyboardInterrupt):
        look.close()

    assert released == ["second", "exits"] and desktop_session.load(look.folder)["state"] == "held"


def test_a_proof_that_could_not_be_saved_is_saved_on_retry_and_only_then_returned(tmp_path, monkeypatch):
    look = desktop_session.Look(tmp_path, "receipt")
    save, failed = desktop_session.save, []

    def flaky(folder, record):
        if record["proof"] is not None and not failed:
            failed.append(True)
            raise PermissionError("receipt busy")
        return save(folder, record)

    monkeypatch.setattr(desktop_session, "save", flaky)
    with pytest.raises(PermissionError, match="receipt busy"):
        look.close()
    assert desktop_session.load(look.folder)["state"] == "owning"

    assert look.close()["clean"]
    assert desktop_session.load(look.folder)["state"] == "proven"


class Handles:
    """A job's kernel calls, answered without a job: `closes(handle)` says whether one closes now."""

    def __init__(self, closes):
        self.closes, self.released = closes, []

    def CloseHandle(self, handle):
        self.released.append(handle)
        return self.closes(handle)


def held_job(look, closes) -> "desktop_session.owned_job.Job":
    """A job of `look` that holds one handle, 456, which closes as `closes` answers; its end is clean."""

    job = object.__new__(desktop_session.owned_job.Job)
    job.k, job.job = Handles(closes), 456
    job.end = lambda _: {"clean": True, "reasons": [], "members": []}
    look.jobs["app"] = job
    look.record["jobs"]["app"] = {"members": []}
    return job


def test_an_interrupted_job_close_keeps_the_owner_while_a_handle_remains(tmp_path, monkeypatch):
    class Kernel:
        def __init__(self):
            self.interrupt = True

        def CloseHandle(self, handle):
            if handle == 123 and self.interrupt:
                raise KeyboardInterrupt
            return True

    look = desktop_session.Look(tmp_path, "job-kept")
    job = object.__new__(desktop_session.owned_job.Job)
    job.k, job.port, job.job = Kernel(), 123, 456
    look.jobs["app"] = job
    look.record["jobs"]["app"] = {"members": []}
    monkeypatch.setattr(job, "end", lambda _: {"reasons": []})
    monkeypatch.setattr(look, "observe", lambda: None)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])

    with pytest.raises(KeyboardInterrupt):
        look.close()
    record = desktop_session.load(look.folder)
    assert record["state"] == "held" and record["owner"] is not None and job.port == 123

    job.k.interrupt = False
    look.close()

    assert job.port is None and job.job is None and desktop_session.load(look.folder)["owner"] is None


def test_a_job_handle_released_only_on_retry_leaves_the_proof_held(tmp_path, monkeypatch):
    class Kernel:
        closes = False

        def CloseHandle(self, handle):
            return self.closes

    look = desktop_session.Look(tmp_path, "job-release-retry")
    job = object.__new__(desktop_session.owned_job.Job)
    job.k, job.job = Kernel(), 456
    look.jobs["app"] = job
    look.record["jobs"]["app"] = {"members": []}
    monkeypatch.setattr(job, "end", lambda _: {"clean": True, "reasons": []})
    monkeypatch.setattr(look, "observe", lambda: None)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])

    assert not look.close()["clean"] and desktop_session.load(look.folder)["state"] == "held"

    job.k.closes = True
    proof = look.close()

    record = desktop_session.load(look.folder)
    assert job.outstanding() == 0 and record["owner"] is None
    assert not proof["clean"] and record["state"] == "held"


def test_leaving_a_look_retries_a_handle_that_did_not_release_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    monkeypatch.setattr(desktop_session.time, "sleep", lambda _: None)
    with pytest.raises(desktop_session.Refused, match="held"):
        with desktop_session.look(tmp_path, "managed") as look:
            job = held_job(look, lambda handle: len(job.k.released) > 1)

    record = desktop_session.load(tmp_path / "managed")
    assert job.k.released == [456, 456] and record["state"] == "held" and record["owner"] is None


def test_leaving_a_look_retries_after_an_interrupted_close_and_then_raises_it(tmp_path, monkeypatch):
    def closes(handle):
        if len(job.k.released) == 1:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    monkeypatch.setattr(desktop_session.time, "sleep", lambda _: None)
    with pytest.raises(KeyboardInterrupt):
        with desktop_session.look(tmp_path, "managed-interruption") as look:
            job = held_job(look, closes)

    record = desktop_session.load(tmp_path / "managed-interruption")
    assert job.k.released == [456, 456] and record["state"] == "held" and record["owner"] is None


def test_leaving_a_look_retries_when_the_delay_is_interrupted(tmp_path, monkeypatch):
    waits = []

    def interrupted_once(seconds):
        waits.append(seconds)
        if len(waits) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    monkeypatch.setattr(desktop_session.time, "sleep", interrupted_once)
    with pytest.raises(KeyboardInterrupt):
        with desktop_session.look(tmp_path, "managed-delay") as look:
            job = held_job(look, lambda handle: len(job.k.released) > 1)

    record = desktop_session.load(tmp_path / "managed-delay")
    assert job.k.released == [456, 456] and record["state"] == "held" and record["owner"] is None


def test_leaving_a_look_retries_a_proof_that_could_not_be_saved(tmp_path, monkeypatch):
    save, failures = desktop_session.save, []

    def flaky(folder, record):
        if record["proof"] is not None and len(failures) < 2:
            failures.append(True)
            raise PermissionError("receipt busy")
        return save(folder, record)

    monkeypatch.setattr(desktop_session, "save", flaky)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    monkeypatch.setattr(desktop_session.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError, match="receipt busy"):
        with desktop_session.look(tmp_path, "managed-receipt"):
            pass

    assert desktop_session.load(tmp_path / "managed-receipt")["state"] == "proven"


def test_nothing_joins_a_look_whose_proof_is_still_unsaved(tmp_path, monkeypatch):
    look = desktop_session.Look(tmp_path, "pending-proof")
    save = desktop_session.save

    def failing(folder, record):
        if record["proof"] is not None:
            raise PermissionError("receipt busy")
        return save(folder, record)

    monkeypatch.setattr(desktop_session, "save", failing)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    with pytest.raises(PermissionError, match="receipt busy"):
        look.close()

    with pytest.raises(desktop_session.Refused, match="cleanup has begun"):
        look.reserve("files")
    with pytest.raises(desktop_session.Refused, match="cleanup has begun"):
        look.start("app", python(SLEEP), tmp_path)
    assert not look.jobs


def test_a_relative_root_still_gives_the_app_absolute_data_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "looks").mkdir()
    (tmp_path / "project").mkdir()
    look = desktop_session.Look(Path("looks"), "relative")
    seen = {}

    def capture(job, argv, cwd, env, desktop, log, before_resume, last=None, gate=None):
        seen.update(env)
        return {}

    monkeypatch.setattr(desktop_session.owned_job.Job, "start", capture)
    try:
        look.start("app", python(SLEEP), tmp_path / "project")
        profile = (tmp_path / "looks" / "relative" / look.record["folders"][0]["name"])
        assert Path(seen["APPDATA"]) == profile / "Roaming" and Path(seen["LOCALAPPDATA"]) == profile / "Local"
        assert Path(seen["TEMP"]) == Path(seen["TMP"]) == profile / "Temp"
    finally:
        look.close()


def test_direct_recovery_holds_a_linked_look(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    before, attempted = desktop_session.load(folder), []
    monkeypatch.setattr(desktop_session, "is_junction", lambda path: path == folder)
    monkeypatch.setattr(desktop_session, "uptime", lambda: 1)
    monkeypatch.setattr(desktop_session, "_removed", lambda path, entries: attempted.append(path) or [])

    assert desktop_session.recover(folder)
    assert attempted == [] and desktop_session.load(folder) == before


def test_a_linked_look_folder_is_never_recovered(tmp_path):
    target, root = tmp_path / "elsewhere", tmp_path / "looks"
    target.mkdir()
    root.mkdir()
    (target / desktop_session.RECORD).write_text("{}", encoding="utf-8")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "linked"), str(target)], capture_output=True)
    assert made.returncode == 0, made.stderr

    assert desktop_session.recover_all(root) == [f"linked: not a plain folder inside {root}; left in place"]


def test_a_record_that_cannot_be_read_is_never_taken_for_none(tmp_path, monkeypatch):
    root = tmp_path / "looks"
    (root / "held").mkdir(parents=True)
    (root / "held" / desktop_session.RECORD).write_text("{}", encoding="utf-8")
    real = os.stat

    def denied(path, *a, **k):   # the record is there; Windows refuses to describe it
        if Path(path).name == desktop_session.RECORD:
            raise PermissionError(13, "denied", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(desktop_session.os, "stat", denied)
    with pytest.raises(PermissionError):
        desktop_session.recover_all(root)
    assert desktop_session.recover_all(tmp_path / "missing") == []


def test_a_folder_that_cannot_be_removed_is_named_by_its_own_name_never_a_file_inside_it(tmp_path, monkeypatch):
    # The look saves the reason in its kept record: a file the app named after a secret never reaches it.
    (tmp_path / "data1").mkdir()
    (tmp_path / "data1" / "token-s3cr3t.txt").write_text("x", encoding="utf-8")

    def refused(path, *a, **k):
        error = PermissionError(13, "Access is denied", str(Path(path) / "token-s3cr3t.txt"))
        error.winerror = 5
        raise error

    monkeypatch.setattr(desktop_session.shutil, "rmtree", refused)
    (left,) = desktop_session._removed(tmp_path, [{"name": "data1", "owned": True}], seconds=0)
    assert left == "folder data1 could not be removed: PermissionError 5", left


def test_a_folder_that_cannot_be_removed_holds_the_look_and_the_next_one(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_session, "_removed", lambda folder, entries, seconds=30: [
        f"folder {entry['name']} could not be removed: in use" for entry in entries])
    with pytest.raises(desktop_session.Refused, match="could not be removed"):
        with desktop_session.look(tmp_path, "stuck") as look:
            look.start("app", python(SLEEP), tmp_path)

    assert desktop_session.load(tmp_path / "stuck")["state"] == "held"
    with pytest.raises(desktop_session.Refused, match="earlier look is held"):
        desktop_session.Look(tmp_path, "next")
    assert not (tmp_path / "next").exists()


def crashed(tmp_path, monkeypatch, name="crashed"):
    """A record a look left behind when its server died: its profile folder still there."""

    look = desktop_session.Look(tmp_path, name)
    (look.folder / "data-app" / "Local").mkdir(parents=True)
    look.record.update(folders=[{"name": "data-app", "owned": True}], owner=None)
    desktop_session.save(look.folder, look.record)
    return look.folder


def test_a_new_boot_proves_the_processes_ended_and_recovery_removes_the_profile(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    monkeypatch.setattr(desktop_session, "uptime", lambda: 1)   # lower than recorded: a new boot

    def busy(*_args, **_options):
        raise OSError("busy")

    real, clock = shutil.rmtree, iter(range(0, 10_000, 100))
    monkeypatch.setattr(desktop_session.shutil, "rmtree", busy)
    monkeypatch.setattr(desktop_session.time, "monotonic", lambda: next(clock))   # the removal deadline passes
    assert desktop_session.recover(folder)   # the profile is still there: held
    assert desktop_session.load(folder)["state"] == "held" and (folder / "data-app").exists()

    monkeypatch.setattr(desktop_session.shutil, "rmtree", real)
    assert desktop_session.recover(folder) == []
    record = desktop_session.load(folder)
    assert record["state"] == "recovered" and not (folder / "data-app").exists()
    assert record["recovered"] == "the PC restarted after the record was written"


def test_the_end_of_the_logon_session_proves_the_processes_ended(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    monkeypatch.setattr(desktop_session, "logon_ended", lambda recorded: True)

    assert desktop_session.recover(folder) == []
    assert desktop_session.load(folder)["recovered"] == "the logon session it ran in ended"


def test_the_same_boot_and_logon_prove_nothing_whatever_the_clock_says(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    record = desktop_session.load(folder)
    record["written"] = "2001-01-01T00:00:00Z"   # a wall clock set back or forward is never read
    desktop_session.save(folder, record)

    held = desktop_session.recover(folder)

    assert held and "restart the PC or sign out" in held[0]
    assert desktop_session.load(folder)["state"] == "held" and (folder / "data-app").exists()


def test_exits_the_cleanup_could_not_prove_in_time_release_the_look_once_proven(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)   # 11-24: 8 of 305 exits missed by the cleanup, all gone minutes later
    record = desktop_session.load(folder)
    late = {"pid": 4508, "created": 1}
    record["jobs"]["app"] = {"name": "Local\\none", "uptime": 1, "root": None, "members": [late], "proof": {
        "clean": False, "reasons": ["no exit proven for pid 4508"], "joined": 305, "total": 305, "active": 0}}
    record["proof"] = {"clean": False, "uptime": 1, "reasons": [   # as `close` saved it
        "app: no exit proven for pid 4508", "pid 4508 is still running",
        "folder data-app could not be removed: PermissionError 32"]}
    desktop_session.save(folder, record)
    monkeypatch.setattr(desktop_session.native_process, "terminate", lambda member: True)
    gone = {}
    monkeypatch.setattr(desktop_session, "exited", lambda member: gone.get(member["pid"]))

    assert desktop_session.recover(folder)   # Windows gives no answer: still held
    gone[4508] = True
    assert desktop_session.recover(folder) == []
    assert desktop_session.load(folder)["recovered"] == "every process its jobs followed has exited since"
    assert not (folder / "data-app").exists()


def late_record(tmp_path, monkeypatch, members, reasons):
    """A crashed look whose job counted every process but proved one exit too late, `close`'s own proof saved."""

    folder = crashed(tmp_path, monkeypatch)
    record = desktop_session.load(folder)
    record["jobs"]["app"] = {"name": "Local\\none", "uptime": 1, "root": None, "members": members, "proof": {
        "clean": False, "reasons": ["no exit proven for pid 9"], "joined": 3, "total": 3, "active": 0}}
    record["proof"] = {"clean": False, "uptime": 1, "reasons": ["app: no exit proven for pid 9", *reasons]}
    desktop_session.save(folder, record)
    monkeypatch.setattr(desktop_session.native_process, "terminate", lambda member: True)
    monkeypatch.setattr(desktop_session, "exited", lambda member: True)
    return folder


def test_an_exit_whose_process_was_never_recorded_is_proven_only_by_an_empty_pid(tmp_path, monkeypatch):
    folder = late_record(tmp_path, monkeypatch, [], [])   # before #440: gone before its join was read
    free = {}
    monkeypatch.setattr(desktop_session, "vacant", lambda pid: free.get(pid, False))

    assert "restart the PC or sign out" in desktop_session.recover(folder)[0]   # the pid is taken, or no answer
    free[9] = True
    assert desktop_session.recover(folder) == []


def test_another_jobs_process_under_the_same_pid_proves_nothing_for_this_one(tmp_path, monkeypatch):
    folder = late_record(tmp_path, monkeypatch, [], [])   # `app` never recorded its pid 9
    record = desktop_session.load(folder)
    record["jobs"]["helper"] = {"name": "Local\\other", "uptime": 1, "root": None,
                                "members": [{"pid": 9, "created": 1}],   # an older process the pid once named
                                "proof": {"clean": True, "reasons": [], "joined": 1, "total": 1, "active": 0}}
    desktop_session.save(folder, record)
    monkeypatch.setattr(desktop_session, "vacant", lambda pid: False)   # something runs under pid 9 now

    assert "restart the PC or sign out" in desktop_session.recover(folder)[0]


def test_a_cleanup_failure_other_than_a_late_exit_keeps_the_look_held(tmp_path, monkeypatch):
    folder = late_record(tmp_path, monkeypatch, [{"pid": 9, "created": 1}],
                         ["cannot read the listeners: OSError(22, 'table')"])

    assert "restart the PC or sign out" in desktop_session.recover(folder)[0]


def test_a_cleanup_that_never_saved_its_proof_proves_nothing(tmp_path, monkeypatch):
    folder = late_record(tmp_path, monkeypatch, [{"pid": 9, "created": 1}], [])
    record = desktop_session.load(folder)
    record["proof"] = None   # the server died between its jobs and the end of `close`
    desktop_session.save(folder, record)

    assert "restart the PC or sign out" in desktop_session.recover(folder)[0]


def test_a_job_count_that_did_not_match_proves_nothing_however_its_members_ended(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    record = desktop_session.load(folder)
    record["jobs"]["app"] = {"name": "Local\\none", "uptime": 1, "root": None, "members": [{"pid": 7, "created": 1}],
                             "proof": {"clean": False, "joined": 4, "total": 5, "active": 0, "reasons": [
                                 "no exit proven for pid 7", "the job counted 5 processes; 4 were followed"]}}
    desktop_session.save(folder, record)
    monkeypatch.setattr(desktop_session.native_process, "terminate", lambda member: True)
    monkeypatch.setattr(desktop_session, "exited", lambda member: True)

    assert "restart the PC or sign out" in desktop_session.recover(folder)[0]


def test_an_unanswered_logon_read_proves_nothing(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    monkeypatch.setattr(desktop_session, "logon_ended", lambda recorded: None)

    assert desktop_session.recover(folder)


SERVER = """
import sys, time
sys.path.insert(0, {tool!r})
import desktop_session
look = desktop_session.Look({root!r}, "dead")
look.start("app", [sys.executable, "-c", "import time; time.sleep(120)"], {root!r})
print("started", flush=True)
time.sleep(120)
"""


def test_a_crashed_server_leaves_a_held_record_and_its_app_ended(tmp_path):
    tool = str(Path(desktop_session.__file__).parent)
    server = subprocess.Popen([sys.executable, "-c", SERVER.format(tool=tool, root=str(tmp_path))],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert server.stdout.readline().strip() == "started"
        root = desktop_session.load(tmp_path / "dead")["jobs"]["app"]["root"]

        assert desktop_session.recover(tmp_path / "dead") == ["dead: its server is still running"]
        assert desktop_session.exited(root) is False   # a live look is never touched
    finally:
        server.kill()
        server.wait(10)

    held = desktop_session.recover(tmp_path / "dead")

    assert held == ["dead: no proof its processes ended; restart the PC or sign out to release it"]
    assert waited(lambda: desktop_session.exited(root) is True)


def test_this_servers_logon_session_is_live_and_a_vanished_one_has_ended():
    current = desktop_session.logon()

    assert current["luid"] and current["time"] and desktop_session.logon_ended(current) is False
    assert desktop_session.logon_ended({**current, "time": current["time"] + 1}) is True
    assert desktop_session.logon_ended({"luid": 0x7F7FFFFFF0, "time": 1}) is True


def test_a_process_reads_as_exited_only_with_an_answer(tmp_path):
    child = subprocess.Popen(python("pass"))
    recorded = native_process.identity(child.pid)
    child.wait(10)

    assert desktop_session.exited(native_process.identity(os.getpid())) is False
    assert recorded is None or desktop_session.exited(recorded) is True
    assert desktop_session.exited({**native_process.identity(os.getpid()), "created": 1}) is True


def test_a_job_key_is_used_once(tmp_path):
    with desktop_session.look(tmp_path, "keys") as look:
        look.start("app", python(SLEEP), tmp_path)
        with pytest.raises(ValueError, match="fresh job key"):
            look.start("app", python(SLEEP), tmp_path)


def test_an_output_given_to_the_start_takes_the_apps_output_and_no_log_is_written(tmp_path):
    read, write = os.pipe()
    with os.fdopen(read, "rb") as reader, desktop_session.look(tmp_path, "piped") as look:
        with os.fdopen(write, "wb") as writer:
            look.start("app", python("print('only to the pipe', flush=True)"), tmp_path, output=writer)
        got = reader.read()   # every writing end closes once the app ends
        assert b"only to the pipe" in got
        assert not (look.folder / "evidence" / "app.log").exists()
