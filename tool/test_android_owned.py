"""The owned emulator: its own ADB server, refusals, and cleanup that touches only what its record names.

A simulated Windows (processes, parents, listeners) and simulated `adb` and `emulator` stand in for the
real ones; the record, the AVD files and the folder cleanup are real. A pre-existing default server on
5037 and the owner's emulator on 5554 live in every world and must survive every test untouched.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import android_owned as owned
import native_process

DEFAULT_SERVER, USER_EMULATOR = 50, 51


class World:
    def __init__(self, tmp: Path):
        sdk = tmp / "sdk"
        self.image = sdk / "system-images/android-34/google_apis/x86_64"
        self.image.mkdir(parents=True)
        (self.image / "system.img").write_bytes(b"image")
        for name in ("platform-tools/adb.exe", "emulator/emulator.exe"):
            (sdk / name).parent.mkdir(parents=True, exist_ok=True)
            (sdk / name).write_bytes(name.encode())
        self.adb, self.emulator = sdk / "platform-tools/adb.exe", sdk / "emulator/emulator.exe"
        self.tools = {"adb": {"path": str(self.adb), "sha256": owned.sha256(self.adb)},
                      "emulator": {"path": str(self.emulator), "sha256": owned.sha256(self.emulator)},
                      "image": {"path": str(self.image), "sha256": owned.image_digest(self.image)}}
        self.procs, self.listen, self.clock, self.next, self.ever = {}, {}, 1000, 200, {}
        self.argvs, self.envs, self.stubborn = [], {}, set()
        self.connecting = None        # runs as a client connects, after the owned checks passed
        self.extra_devices, self.version = [], "37.0.1"
        self.taken_by = None          # a pid that grabs the owned port before the owned server does
        self.server_parent = None     # the parent pid the owned server reports, when not its client
        self.boot = "1"
        self.add(DEFAULT_SERVER, os.path.normcase(r"C:\owner\adb.exe"), 1, [5037])
        self.add(USER_EMULATOR, os.path.normcase(r"C:\owner\qemu.exe"), 1, [5554, 5555])

    def add(self, pid, exe, parent, ports=()):
        self.clock += 1
        self.procs[pid] = self.ever[pid] = {"pid": pid, "created": self.clock, "exe": exe, "parent": parent}
        for port in ports:
            self.listen.setdefault(port, set()).add(pid)
        return pid

    def spawn(self, exe, parent, ports=()):
        self.next += 1
        return self.add(self.next, os.path.normcase(str(exe)), parent, ports)

    def kill(self, pid):
        self.procs.pop(pid, None)
        for holders in self.listen.values():
            holders.discard(pid)

    def survivors(self):
        return DEFAULT_SERVER in self.procs and USER_EMULATOR in self.procs and self.listen[5037] == {DEFAULT_SERVER}

    # -- the patched surface ---------------------------------------------------------------------------

    def Popen(self, argv, env=None, **_):
        world, pid = self, self.spawn(argv[0], 1)
        self.envs[argv[0] if "start-server" not in argv else "start-server"] = env
        if argv[0] == str(self.adb) and "start-server" not in argv:
            return self.command(argv, pid)
        self.argvs.append(argv)

        class Proc:
            returncode = None

            def __init__(self):
                self.pid, self._handle = pid, pid

            def resume(self):
                if "start-server" in argv:
                    port = int(argv[argv.index("-P") + 1])
                    if world.taken_by:
                        world.listen.setdefault(port, set()).add(world.taken_by)
                    else:
                        world.spawn(world.adb, world.server_parent or pid, [port])
                    world.kill(pid)   # the client exits once its server answers
                else:
                    console = int(argv[argv.index("-port") + 1])
                    world.spawn(Path(argv[0]).with_name("qemu.exe"), pid, [console, console + 1])

            def wait(self, _timeout=None):
                return 0

            def poll(self):
                return None if pid in world.procs else 1

            def kill(self):
                world.kill(pid)

        return Proc()

    def command(self, argv, client):
        """An adb client: it connects to whatever holds the port, starting a server there if nothing does."""

        self.argvs.append(argv)
        assert argv[1] == "-P", argv   # every owned call names the owned port
        port, rest = int(argv[2]), argv[3:]
        if self.connecting:
            self.connecting()
        if not self.listen.get(port):
            self.spawn(self.adb, client, [port])
        if rest[0] == "-s":
            rest = rest[2:]
        out = ""
        if rest == ["devices"]:
            qemus = [p for p in self.procs.values() if p["exe"].endswith("qemu.exe") and p["pid"] != USER_EMULATOR]
            serials = [f"emulator-{min(k for k, v in self.listen.items() if q['pid'] in v)}" for q in qemus]
            out = "List of devices attached\n" + "".join(f"{s}\tdevice\n" for s in [*serials, *self.extra_devices])
        elif rest[:2] == ["shell", "getprop"]:
            out = {"sys.boot_completed": self.boot, "ro.kernel.qemu": "1"}[rest[2]]
        elif rest[0] == "forward" and rest[1].startswith("tcp:"):
            self.listen.setdefault(int(rest[1][4:]), set()).update(self.listen[port])   # the server listens
        self.kill(client)
        return SimpleNamespace(pid=client, _handle=client, returncode=0, kill=lambda: None,
                               communicate=lambda timeout=None: (out.encode(), b""))

    def run(self, argv, text=False, **_):
        self.argvs.append(argv)
        assert argv[1:] == ["version"], argv
        out = f"Android Debug Bridge version 1.0.41\nVersion {self.version}-15733141\n"
        return subprocess.CompletedProcess(argv, 0, out if text else out.encode(), "" if text else b"")

    def terminate(self, recorded, wait=10):
        if not native_process.same(recorded, self.identity(recorded["pid"])):
            return True
        if recorded["pid"] in self.stubborn:
            return False
        self.kill(recorded["pid"])
        return True

    def identity(self, pid, handle=None, live=True):
        found = self.procs.get(pid) or (None if live else self.ever.get(pid))
        return dict(found) if found else None


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    fake = SimpleNamespace(Popen=w.Popen, run=w.run, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE,
                           CREATE_NO_WINDOW=0, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(owned, "subprocess", fake)
    monkeypatch.setattr(owned, "process", SimpleNamespace(SUSPENDED=0, background_options=dict, terminated=lambda job: None,
                                                          contained=lambda proc: "job", resumed=lambda proc: proc.resume()))
    monkeypatch.setattr(owned, "listeners", lambda port: set(w.listen.get(port, ())))
    monkeypatch.setattr(owned, "identity", w.identity)
    monkeypatch.setattr(owned, "parents", lambda: {p["pid"]: p["parent"] for p in w.procs.values()})
    monkeypatch.setattr(owned, "terminate", w.terminate)
    monkeypatch.setattr(owned.time, "sleep", lambda _s: None)
    w.folder = tmp_path / "run"
    w.folder.mkdir()
    yield w
    assert w.survivors(), "the owner's default server or emulator was touched"


def booted(world, **options):
    context = owned.boot(world.folder, world.tools, **options)
    return context, context.__enter__()


def test_a_boot_runs_its_own_closed_server_and_emulator_and_cleans_exactly_them(world):
    with owned.boot(world.folder, world.tools, device={"width": 720, "height": 1280, "dpi": 320}, renderer="host") as serial:
        record = owned.load(world.folder)
        port = record["server"]["port"]
        assert serial == "emulator-5586" and record["console"] == 5586 and port != 5037
        assert record["server"]["parent"] == record["client"]["pid"]
        assert all(world.envs["start-server"][k] == v for k, v in owned.ISOLATED.items())
        env = world.envs[str(world.adb.parent.parent / "emulator/emulator.exe")]
        assert env["ANDROID_ADB_SERVER_PORT"] == str(port) and env["ANDROID_AVD_HOME"].startswith(str(world.folder))
        # A missing emulator home made the real emulator retry its lock file forever.
        assert all(Path(env[key]).is_dir() for key in ("ANDROID_AVD_HOME", "ANDROID_EMULATOR_HOME", "ANDROID_USER_HOME"))
        emulator = next(a for a in world.argvs if a[0] == str(world.emulator))
        assert emulator[emulator.index("-gpu") + 1] == "host" and "-wipe-data" in emulator
        config = (world.folder / "android/avd/owned.avd/config.ini").read_text(encoding="utf-8")
        assert "hw.lcd.width=720" in config and "image.sysdir.1=system-images/android-34/google_apis/x86_64/" in config
        local = owned.forward(world.folder, "localabstract:webview_devtools_remote_1")
        assert owned.load(world.folder)["forwards"] == [local]
    record = owned.load(world.folder)
    assert record["state"] == "cleaned" and record["left"] == []
    assert not (world.folder / "android").exists()
    assert record["server"]["pid"] not in world.procs and not world.listen[local]
    assert not any("kill-server" in a or "--remove" in a for a in world.argvs)
    assert not any(p["exe"].endswith(("qemu.exe", "emulator.exe")) and p["pid"] != USER_EMULATOR for p in world.procs.values())
    assert all(a[1:] == ["version"] or a[1:3] == ["-P", str(record["server"]["port"])] for a in world.argvs
               if a[0] == str(world.adb) and "start-server" not in a)


def test_a_console_port_with_any_listener_is_skipped(world):
    world.add(90, os.path.normcase(r"C:\other\thing.exe"), 1, [5587])
    with owned.boot(world.folder, world.tools) as serial:
        assert serial == "emulator-5588"
    assert 90 in world.procs


@pytest.mark.parametrize("case", ["unrelated", "same-binary"])
def test_a_server_the_run_did_not_start_is_neither_adopted_nor_stopped(world, case):
    if case == "unrelated":
        world.taken_by = world.add(91, os.path.normcase(r"C:\other\server.exe"), 1)
    else:
        world.server_parent = world.add(92, os.path.normcase(str(world.adb)), 1)   # another attempt's client
    with pytest.raises(ValueError, match="neither adopted nor stopped"):
        booted(world)
    record = owned.load(world.folder)
    assert record["state"] == "cleaned" and "pid" not in record["server"]
    assert not any(a[-1] == "kill-server" for a in world.argvs)
    if case == "unrelated":
        assert 91 in world.procs
    else:
        assert any(p["exe"] == os.path.normcase(str(world.adb)) and p["parent"] == 92 for p in world.procs.values())


@pytest.mark.parametrize("device", ["R58M123ABC", "emulator-5554", "192.168.0.7:5555"])
def test_an_owned_server_that_sees_any_device_refuses_before_booting(world, device):
    world.extra_devices = [device]
    with pytest.raises(ValueError, match="sees devices it did not start"):
        booted(world)
    assert not any(a[0] == str(world.emulator) for a in world.argvs)
    assert owned.load(world.folder)["state"] == "cleaned"


@pytest.mark.parametrize("change", ["adb", "emulator", "image", "old-adb"])
def test_a_replaced_tool_or_an_old_adb_refuses_before_anything_starts(world, change):
    if change == "old-adb":
        world.version = "36.0.0"
    elif change == "image":
        (world.image / "system.img").write_bytes(b"another image")
    else:
        Path(world.tools[change]["path"]).write_bytes(b"replaced")
    with pytest.raises(ValueError, match="older than|missing or changed"):
        booted(world)
    assert not any(a[0] == str(world.emulator) or "start-server" in a for a in world.argvs)
    assert owned.load(world.folder)["server"] is None


def test_a_killed_collector_is_cleaned_by_the_orchestrator_from_its_record(world):
    _context, _serial = booted(world)   # held: a dropped context would clean up as it is collected
    local = owned.forward(world.folder, "localabstract:webview_devtools_remote_1")
    owned._jobs.clear()   # the collector died: its jobs and memory are gone, the record is not
    sent = len(world.argvs)
    assert owned.cleanup(world.folder) == []
    assert world.argvs[sent:] == [] and not world.listen[local]   # the forward closed with its server
    assert owned.load(world.folder)["state"] == "cleaned" and not (world.folder / "android").exists()
    assert owned.cleanup(world.folder) == []   # idempotent


def test_a_reused_emulator_pid_or_console_is_left_alone(world):
    _context, _serial = booted(world)
    record = owned.load(world.folder)
    for key in ("emulator", "console_process"):
        pid = record[key]["pid"]
        world.kill(pid)
        world.add(pid, os.path.normcase(r"C:\other\reused.exe"), 1, [record["console"]] if key == "console_process" else [])
    with pytest.raises(ValueError, match="emulator is gone"):
        owned.call(world.folder, ["shell", "getprop", "ro.kernel.qemu"])
    owned._jobs.clear()
    assert owned.cleanup(world.folder) == []
    assert record["emulator"]["pid"] in world.procs and record["console_process"]["pid"] in world.procs
    assert not any("--remove" in a for a in world.argvs)


def test_a_server_that_lost_its_listener_gets_no_command_and_is_killed_by_identity(world):
    _context, _serial = booted(world)
    record = owned.load(world.folder)
    owned.forward(world.folder, "localabstract:webview_devtools_remote_1")
    world.listen[record["server"]["port"]].clear()   # the control listener is gone; the server is not
    sent = len(world.argvs)
    owned._jobs.clear()
    assert owned.cleanup(world.folder) == []
    assert not any(a[0] == str(world.adb) for a in world.argvs[sent:])
    assert record["server"]["pid"] not in world.procs


def test_a_server_replaced_between_the_check_and_the_connection_is_neither_trusted_nor_stopped(world):
    _context, _serial = booted(world)
    server = owned.load(world.folder)["server"]
    foreign = []

    def replaced():
        world.connecting = None
        world.kill(server["pid"])
        foreign.append(world.spawn(world.adb, 999, [server["port"]]))   # another run's server takes the port

    world.connecting = replaced
    with pytest.raises(ValueError, match="changed during the call"):
        owned.call(world.folder, ["shell", "getprop", "ro.kernel.qemu"])
    owned._jobs.clear()
    assert owned.cleanup(world.folder) == [] and foreign[0] in world.procs


def test_a_server_a_call_started_after_the_owned_one_vanished_is_stopped(world):
    _context, _serial = booted(world)
    server = owned.load(world.folder)["server"]

    def vanished():
        world.connecting = None
        world.kill(server["pid"])

    world.connecting = vanished
    with pytest.raises(ValueError, match="changed during the call"):
        owned.call(world.folder, ["shell", "getprop", "ro.kernel.qemu"])
    assert not world.listen[server["port"]], "the client's own server must not outlive the call"


def test_a_folder_already_in_the_run_folder_is_never_owned_or_deleted(world):
    existing = world.folder / "android"
    existing.mkdir()
    (existing / "kept.txt").write_text("not this run's", encoding="utf-8")
    with pytest.raises(ValueError, match="not this run's"):
        booted(world)
    assert (existing / "kept.txt").read_text(encoding="utf-8") == "not this run's"
    assert owned.load(world.folder)["folders"] == [] and owned.load(world.folder)["state"] == "cleaned"


def test_a_cleanup_that_cannot_finish_says_what_is_left_and_fails_the_run(world):
    context, _serial = booted(world)
    world.stubborn.add(owned.load(world.folder)["emulator"]["pid"])
    with pytest.raises(ValueError, match="Android cleanup left: emulator"):
        context.__exit__(None, None, None)
    assert owned.load(world.folder)["state"] == "failed"
    world.stubborn.clear()
    assert owned.cleanup(world.folder) == [] and owned.load(world.folder)["state"] == "cleaned"


def test_an_emulator_that_never_boots_is_still_cleaned(world, monkeypatch):
    world.boot = "0"
    monkeypatch.setattr(owned, "BOOT_SECONDS", 0)
    with pytest.raises(ValueError, match="did not boot"):
        booted(world)
    assert owned.load(world.folder)["state"] == "cleaned"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process and TCP tables")
def test_the_real_identity_listener_and_terminate_helpers():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        assert native_process.listeners(server.getsockname()[1]) == {os.getpid()}
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        recorded = native_process.identity(child.pid)
        assert native_process.same(recorded, native_process.identity(child.pid, child._handle))
        assert native_process.terminate({**recorded, "created": recorded["created"] - 1}) and child.poll() is None
        assert native_process.terminate(recorded) and child.wait(5) is not None
    finally:
        child.kill()
