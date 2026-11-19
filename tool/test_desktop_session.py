"""A desktop look: its record before anything starts, apps in owned jobs on an owned desktop with disposable
data folders, a cleanup proof on every outcome, and recovery of an earlier look's record
(docs/research/oneshot-desktop-session.md)."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktops")

if sys.platform == "win32":
    import desktop_session
    import native_process

SLEEP = "import time; time.sleep(60)"
WINDOW = "import tkinter; root = tkinter.Tk(); root.title('fixture'); root.after(60000, root.destroy); root.mainloop()"
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


def test_a_look_runs_its_app_on_its_desktop_and_proves_everything_gone(tmp_path):
    with desktop_session.look(tmp_path, "first") as look:
        root = look.start("app", python(WINDOW), tmp_path)
        listener = look.start("server", python(LISTEN), tmp_path)
        assert look.record["jobs"]["app"]["root"] == root
        assert waited(lambda: any(pid == listener["pid"] for state, _, _, pid in native_process._sockets(3)
                                  if state == 2))
        windows = []
        assert waited(lambda: look.u.EnumDesktopWindows(look.desktop, desktop_session._Each(
            lambda hwnd, _: windows.append(hwnd) or True), 0) and windows)

    record = desktop_session.load(tmp_path / "first")
    assert record["state"] == "proven" and record["proof"]["clean"], record["proof"]
    assert record["listeners"] and not list((tmp_path / "first").glob("data-*"))
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

    record = desktop_session.load(tmp_path / "ran")
    assert record["state"] == "proven" and record["proof"]["clean"], record["proof"]
    assert len(record["jobs"]["preview"]["members"]) >= 2   # the root and what it left running


def test_the_app_keeps_its_data_inside_the_looks_disposable_folder(tmp_path):
    with desktop_session.look(tmp_path, "data") as look:
        look.start("app", python(ENVIRONMENT), tmp_path, {"EXTRA": "kept", "temp": str(tmp_path), "TMP": "x"})
        log = tmp_path / "data" / "evidence" / "app.log"
        assert waited(lambda: log.stat().st_size > 0)
        seen = json.loads(log.read_text(encoding="utf-8"))
        data = look.folder / look.record["folders"][0]["name"]

    assert seen == {"APPDATA": str(data / "Roaming"), "LOCALAPPDATA": str(data / "Local"),
                    "TEMP": str(data / "Temp"), "TMP": str(data / "Temp"), "EXTRA": "kept"}


def test_the_record_names_each_part_before_it_is_made(tmp_path, monkeypatch):
    seen = {}
    early = tmp_path / "early"
    look = desktop_session.Look(tmp_path, "early")
    create, mkdir, start = look.u.CreateDesktopW, Path.mkdir, desktop_session.owned_job.Job.start

    def creating(name, *args):
        seen["desktop"] = desktop_session.load(early)["desktop"] == name
        return create(name, *args)

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

    monkeypatch.setattr(look.u, "CreateDesktopW", creating)
    monkeypatch.setattr(Path, "mkdir", making)
    monkeypatch.setattr(desktop_session.owned_job.Job, "start", starting)
    try:
        look.open()
        look.start("app", python(SLEEP), tmp_path)
    finally:
        assert look.close()["clean"]

    assert seen == {"desktop": True, "folder": True, "job": True, "root": True}


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

    def ending(_seconds):
        calls.append("second.end")
        return {"clean": True, "reasons": []}

    look.jobs = {"first": SimpleNamespace(end=failing, close=lambda: calls.append("first.close"), outstanding=lambda: 0),
                 "second": SimpleNamespace(end=ending, close=lambda: calls.append("second.close"),
                                           outstanding=lambda: 0)}
    look.record["jobs"] = {key: {"members": []} for key in look.jobs}
    monkeypatch.setattr(look, "observe", lambda: None)
    monkeypatch.setattr(native_process, "_sockets", lambda _kind: [])
    monkeypatch.setattr(look, "_desktop_closed", lambda: calls.append("desktop") or [])

    proof = look.close(0)

    assert calls == ["first.close", "second.end", "second.close", "desktop"]
    assert not proof["clean"] and any("first job failed" in reason for reason in proof["reasons"])
    assert desktop_session.load(look.folder)["state"] == "held" and look.close() == proof


def test_an_interrupted_cleanup_is_never_proven(tmp_path, monkeypatch):
    look = desktop_session.Look(tmp_path, "interrupted")

    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(look, "_desktop_closed", interrupted)
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
    monkeypatch.setattr(look, "_desktop_closed", lambda: released.append("desktop"))
    with pytest.raises(KeyboardInterrupt):
        look.close()

    assert released == ["second", "desktop"] and desktop_session.load(look.folder)["state"] == "held"


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


class Desktops:
    """The desktop calls of user32, answered without a desktop."""

    def __init__(self, closes=True, enumerate=None):
        self.closes, self.enumerate, self.released, self.created = closes, enumerate, [], []

    def EnumDesktopWindows(self, *args):
        if self.enumerate:
            raise self.enumerate
        return 0

    def CloseDesktop(self, handle):
        self.released.append(handle)
        return self.closes

    def OpenDesktopW(self, *args):
        desktop_session.ctypes.set_last_error(desktop_session.NOT_FOUND)
        return None

    def CreateDesktopW(self, *args):
        self.created.append(args)
        return 123


def test_an_interrupted_window_wait_still_releases_the_desktop(tmp_path, monkeypatch):
    look = desktop_session.Look(tmp_path, "desktop-interrupted")
    look.u, look.desktop = Desktops(enumerate=KeyboardInterrupt()), 123
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])

    with pytest.raises(KeyboardInterrupt):
        look.close()

    record = desktop_session.load(look.folder)
    assert look.u.released == [123] and look.desktop is None
    assert record["state"] == "held" and record["owner"] is None


def test_a_desktop_that_will_not_close_keeps_its_server_the_owner(tmp_path, monkeypatch):
    look = desktop_session.Look(tmp_path, "desktop-kept")
    look.u, look.desktop = Desktops(closes=False), 123
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])

    proof = look.close()

    record = desktop_session.load(look.folder)
    assert not proof["clean"] and any("cannot close the desktop" in reason for reason in proof["reasons"])
    assert look.desktop == 123 and record["owner"] is not None
    assert desktop_session.recover(look.folder) == ["desktop-kept: its server is still running"]


def test_a_probe_handle_that_will_not_close_is_held_and_keeps_the_owner(tmp_path, monkeypatch):
    from itertools import count
    from types import SimpleNamespace

    class Probe(Desktops):
        def OpenDesktopW(self, *args):
            self.created.append(args)
            return 124

        def CloseDesktop(self, handle):
            self.released.append(handle)
            return handle == 123

    tick = count()
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    look = desktop_session.Look(tmp_path, "probe-kept")
    look.u, look.desktop = Probe(), 123
    monkeypatch.setattr(desktop_session, "time", SimpleNamespace(monotonic=lambda: next(tick), sleep=lambda _: None))

    assert not look.close()["clean"]
    assert len(look.u.created) == 1 and look.probes == [124]
    assert desktop_session.load(look.folder)["owner"] is not None and not look.closed


def test_a_held_desktop_handle_is_released_on_retry_and_only_then_the_owner_retires(tmp_path, monkeypatch):
    look = desktop_session.Look(tmp_path, "retry-release")
    look.u, look.desktop = Desktops(closes=False), 123
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])

    assert not look.close()["clean"]
    assert desktop_session.load(look.folder)["owner"] is not None

    look.u.closes = True
    look.close()

    assert look.u.released == [123, 123] and look.desktop is None and look.closed
    assert desktop_session.load(look.folder)["owner"] is None


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


def test_an_interrupted_probe_close_is_counted_and_retried(tmp_path, monkeypatch):
    class Probe(Desktops):
        interrupt = True

        def OpenDesktopW(self, *args):
            return 124

        def CloseDesktop(self, handle):
            self.released.append(handle)
            if handle == 124 and self.interrupt:
                raise KeyboardInterrupt
            return True

    look = desktop_session.Look(tmp_path, "probe-interrupted")
    look.u, look.desktop = Probe(), 123
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])

    with pytest.raises(KeyboardInterrupt):
        look.close()
    assert desktop_session.load(look.folder)["owner"] is not None

    look.u.interrupt = False
    look.close()

    assert look.u.released == [123, 124, 124] and desktop_session.load(look.folder)["owner"] is None


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
    class Flaky(Desktops):
        def CloseDesktop(self, handle):
            self.released.append(handle)
            return len(self.released) > 1

    desktop = Flaky()

    def opened(look):
        look.u, look.desktop = desktop, 123

    monkeypatch.setattr(desktop_session.Look, "open", opened)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    monkeypatch.setattr(desktop_session.time, "sleep", lambda _: None)
    with pytest.raises(desktop_session.Refused, match="held"):
        with desktop_session.look(tmp_path, "managed"):
            pass

    record = desktop_session.load(tmp_path / "managed")
    assert desktop.released == [123, 123] and record["state"] == "held" and record["owner"] is None


def test_leaving_a_look_retries_after_an_interrupted_close_and_then_raises_it(tmp_path, monkeypatch):
    class Probe(Desktops):
        def OpenDesktopW(self, *args):
            return 124

        def CloseDesktop(self, handle):
            self.released.append(handle)
            if self.released == [123, 124]:
                raise KeyboardInterrupt
            return True

    desktop = Probe()

    def opened(look):
        look.u, look.desktop = desktop, 123

    monkeypatch.setattr(desktop_session.Look, "open", opened)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    monkeypatch.setattr(desktop_session.time, "sleep", lambda _: None)
    with pytest.raises(KeyboardInterrupt):
        with desktop_session.look(tmp_path, "managed-interruption"):
            pass

    record = desktop_session.load(tmp_path / "managed-interruption")
    assert desktop.released == [123, 124, 124] and record["state"] == "held" and record["owner"] is None


def test_leaving_a_look_retries_when_the_delay_is_interrupted(tmp_path, monkeypatch):
    class Flaky(Desktops):
        def CloseDesktop(self, handle):
            self.released.append(handle)
            return len(self.released) > 1

    desktop, waits = Flaky(), []

    def opened(look):
        look.u, look.desktop = desktop, 123

    def interrupted_once(seconds):
        waits.append(seconds)
        if len(waits) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(desktop_session.Look, "open", opened)
    monkeypatch.setattr(native_process, "_sockets", lambda _: [])
    monkeypatch.setattr(desktop_session.time, "sleep", interrupted_once)
    with pytest.raises(KeyboardInterrupt):
        with desktop_session.look(tmp_path, "managed-delay"):
            pass

    record = desktop_session.load(tmp_path / "managed-delay")
    assert desktop.released == [123, 123] and record["state"] == "held" and record["owner"] is None


def test_leaving_a_look_retries_a_proof_that_could_not_be_saved(tmp_path, monkeypatch):
    save, failures = desktop_session.save, []

    def flaky(folder, record):
        if record["proof"] is not None and len(failures) < 2:
            failures.append(True)
            raise PermissionError("receipt busy")
        return save(folder, record)

    monkeypatch.setattr(desktop_session, "save", flaky)
    monkeypatch.setattr(desktop_session.Look, "open", lambda look: None)
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
    look.u = Desktops()
    with pytest.raises(PermissionError, match="receipt busy"):
        look.close()

    with pytest.raises(desktop_session.Refused, match="cleanup has begun"):
        look.open()
    with pytest.raises(desktop_session.Refused, match="cleanup has begun"):
        look.start("app", python(SLEEP), tmp_path)
    assert look.u.created == []


def test_a_relative_root_still_gives_the_app_absolute_data_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "looks").mkdir()
    (tmp_path / "project").mkdir()
    look = desktop_session.Look(Path("looks"), "relative")
    seen = {}

    def capture(job, argv, cwd, env, desktop, log, before_resume):
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


def test_an_unanswered_logon_read_proves_nothing(tmp_path, monkeypatch):
    folder = crashed(tmp_path, monkeypatch)
    monkeypatch.setattr(desktop_session, "logon_ended", lambda recorded: None)

    assert desktop_session.recover(folder)


SERVER = """
import sys, time
sys.path.insert(0, {tool!r})
import desktop_session
look = desktop_session.Look({root!r}, "dead")
look.open()
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
