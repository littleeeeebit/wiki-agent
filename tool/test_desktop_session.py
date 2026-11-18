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
    assert record["listeners"] and not (tmp_path / "first" / "data-app").exists()
    assert all(desktop_session.exited(member) for entry in record["jobs"].values() for member in entry["members"])


def test_the_app_keeps_its_data_inside_the_looks_disposable_folder(tmp_path):
    with desktop_session.look(tmp_path, "data") as look:
        look.start("app", python(ENVIRONMENT), tmp_path, {"EXTRA": "kept"})
        log = tmp_path / "data" / "evidence" / "app.log"
        assert waited(lambda: log.stat().st_size > 0)
        seen = json.loads(log.read_text(encoding="utf-8"))

    data = tmp_path / "data" / "data-app"
    assert seen == {"APPDATA": str(data / "Roaming"), "LOCALAPPDATA": str(data / "Local"),
                    "TEMP": str(data / "Temp"), "TMP": str(data / "Temp"), "EXTRA": "kept"}


def test_the_record_names_the_desktop_and_each_job_before_anything_runs(tmp_path):
    seen = {}
    real = desktop_session.owned_job.Job.start

    def spying(job, *args, before_resume=None, **options):
        def recorded(root):
            before_resume(root)
            seen.update(desktop_session.load(tmp_path / "early"))
        return real(job, *args, before_resume=recorded, **options)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(desktop_session.owned_job.Job, "start", spying)
        with desktop_session.look(tmp_path, "early") as look:
            root = look.start("app", python(SLEEP), tmp_path)

    assert seen["desktop"].startswith("wiki-look-") and seen["folders"] == ["data-app"]
    assert seen["jobs"]["app"]["root"] == root and seen["jobs"]["app"]["name"].startswith("Local\\wiki-oneshot-")


def test_a_folder_that_cannot_be_removed_holds_the_look_and_the_next_one(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_session, "_removed", lambda folder, names, seconds=30: [
        f"folder {name} could not be removed: in use" for name in names])
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
    look.record.update(folders=["data-app"], owner=None)
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
