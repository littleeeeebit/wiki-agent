"""An Android emulator a run owns (`docs/plans/native-webview/0-overview.md`, "Android collector";
`docs/plans/oneshot/7-android.md`, step 7d): its own ADB server, an AVD under the run's folder, a console
port outside every ADB server's scan, and an ownership record that outlives the process.

Nothing here reaches the owner's default ADB server on port 5037, a default device or a physical device.
Every `adb` call names the owned server's port, and the owned serial once there is one. Each call first
checks that the port's listener is still the recorded server and the console's listener the recorded
emulator. The record is written before the first mutation, and each resource joins it just before it is
created. `cleanup` runs from the collector on every outcome, and again from the orchestrator after a killed
collector. It removes exactly what the record names: a pid or port that now belongs to another process is
left alone.

The command line (`adb`, `forward`, `unforward`, `cleanup`, each taking the run's folder) is how the
WebView driver reaches the device, so there is one implementation of these checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from common import is_junction, process
from native_process import identity, listeners, parents, same, terminate

RECORD = "android-ownership.json"
FIRST_CONSOLE, LAST_CONSOLE = 5586, 5700   # even console ports; each one's adbd port is one above it
MIN_ADB = (37, 0, 1)   # the release verified to honour ADB_EMU, ADB_USB and ADB_MDNS; older ones are unproven
# The owned server's discovery, all off: no emulator scan (and an empty one were ADB_EMU ignored), no USB
# backend, no mDNS. Every owned client carries them, so a server one of them starts is just as closed.
ISOLATED = {"ADB_EMU": "0", "ADB_LOCAL_TRANSPORT_MAX_PORT": "5554", "ADB_USB": "0", "ADB_MDNS": "0"}
DEVICE = {"width": 1080, "height": 2400, "dpi": 420}   # the medium phone when a run names none
AVD, HOME = "owned", "android"
BOOT_SECONDS, CALL_SECONDS = 600, 120
_jobs: dict[str, list] = {}   # the run folder → the job handles holding its client and emulator trees


def sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with open(file, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def image_digest(folder: Path) -> str:
    """A system image folder's content: each file's relative path and SHA-256, sorted."""

    files = sorted(p for p in Path(folder).rglob("*") if p.is_file())
    if not files or any(p.is_symlink() for p in Path(folder).rglob("*")):
        raise ValueError("The system image folder is empty or holds a link")
    rows = [[p.relative_to(folder).as_posix(), sha256(p)] for p in files]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def environment(extra: dict | None = None) -> dict:
    """This process's environment without any inherited `ANDROID_*` or `ADB_*` value, plus `extra`."""

    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ANDROID_", "ADB_"))}
    return {**env, **ISOLATED, **(extra or {})}


def adb_version(adb: Path) -> tuple[int, ...]:
    """`adb version`'s release, read without starting a server."""

    text = subprocess.run([str(adb), "version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=30, env=environment(), **process.background_options()).stdout
    found = re.search(r"^Version (\d+)\.(\d+)\.(\d+)", text, re.M)
    if not found:
        raise ValueError("Cannot read the approved adb's version")
    return tuple(int(n) for n in found.groups())


def checked(tools: dict) -> dict:
    """`tools` measured again: `adb` and `emulator` files and the system image folder by their approved
    SHA-256, and an `adb` new enough to honour the variables that close its discovery."""

    for key in ("adb", "emulator"):
        file = Path(tools[key]["path"])
        if not file.is_absolute() or file.is_symlink() or not file.is_file() or sha256(file) != tools[key]["sha256"]:
            raise ValueError(f"The approved {key} is missing or changed")
    image = Path(tools["image"]["path"])
    if not image.is_absolute() or not image.is_dir() or image_digest(image) != tools["image"]["sha256"]:
        raise ValueError("The approved system image is missing or changed")
    if len(image.parts) < 5 or image.parts[-4] != "system-images":
        raise ValueError("The system image must sit at system-images/<platform>/<tag>/<abi> in its SDK")
    if adb_version(Path(tools["adb"]["path"])) < MIN_ADB:
        raise ValueError(f"The approved adb is older than {'.'.join(map(str, MIN_ADB))}, which closes its discovery")
    return tools


# -- The record ----------------------------------------------------------------------------------------

def load(folder: Path) -> dict | None:
    try:
        return json.loads((Path(folder) / RECORD).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def save(folder: Path, record: dict) -> None:
    file = Path(folder) / RECORD
    partial = file.with_suffix(".part")
    partial.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(partial, file)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def server_ok(record: dict) -> bool:
    """The owned port's only listener is still the recorded server."""

    server = record.get("server") or {}
    if "pid" not in server:
        return False
    return listeners(server["port"]) == {server["pid"]} and same(server, identity(server["pid"]))


def emulator_ok(record: dict) -> bool:
    """The console port's only listener is still the recorded emulator process."""

    console = record.get("console_process")
    return bool(console) and listeners(record["console"]) == {console["pid"]} and same(console, identity(console["pid"]))


def call(folder: Path, args: list[str], *, device: bool = True, timeout: float = CALL_SECONDS,
         binary: bool = False, stream: bool = False):
    """`adb -P <owned port> [-s <owned serial>] args`, only after the listener checks. `stream` hands
    adb this process's own output and waits for it, as `logcat` needs."""

    record = load(folder)
    if not record or not server_ok(record):
        raise ValueError("The owned ADB server is gone or its port belongs to another process")
    if device and not (record.get("serial") and emulator_ok(record)):
        raise ValueError("The owned emulator is gone or its console port belongs to another process")
    argv = [record["tools"]["adb"]["path"], "-P", str(record["server"]["port"]),
            *(["-s", record["serial"]] if device else []), *args]
    if stream:
        sys.stdout.flush()
        return subprocess.call(argv, env=environment(), **process.background_options())
    done = subprocess.run(argv, capture_output=True, timeout=timeout, env=environment(), **process.background_options())
    if done.returncode:
        raise ValueError(f"adb {' '.join(args[:2])} failed: {done.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def devices(folder: Path) -> list[str]:
    lines = call(folder, ["devices"], device=False).splitlines()[1:]
    return [line.split()[0] for line in lines if line.strip()]


# -- Boot ----------------------------------------------------------------------------------------------

def launched(folder: Path, record: dict, key: str, argv: list[str], env: dict, log: Path) -> subprocess.Popen:
    """`argv` started suspended in a kill-on-close job, its identity recorded under `key`, then resumed:
    nothing runs that the record does not already name."""

    with open(log, "ab") as out:
        proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                creationflags=process.SUSPENDED | subprocess.CREATE_NO_WINDOW)
    job = process.contained(proc)
    if job:
        _jobs.setdefault(str(folder), []).append(job)
    record[key] = identity(proc.pid, proc._handle)
    save(folder, record)
    if not job or not record[key]:
        proc.kill()
        raise ValueError(f"Cannot contain or identify the owned {key}")
    process.resumed(proc)
    return proc


def descends(pid: int, ancestor: dict) -> bool:
    """`pid` is `ancestor`, or one of its descendants created after it."""

    tree, cursor, seen = parents(), pid, set()
    while cursor not in seen:
        if cursor == ancestor["pid"]:
            return True
        seen.add(cursor)
        here = identity(cursor)
        parent = tree.get(cursor)
        if not here or parent is None or here["created"] < ancestor["created"]:
            return False
        cursor = parent
    return False


def avd(home: Path, image: Path, device: dict) -> None:
    """An AVD named `AVD` in `home/avd` that boots `image` at `device`'s screen."""

    platform, tag, abi = image.parts[-3:]
    folder = home / "avd" / f"{AVD}.avd"
    folder.mkdir(parents=True)
    (home / "avd" / f"{AVD}.ini").write_text(f"avd.ini.encoding=UTF-8\npath={folder}\ntarget={platform}\n",
                                             encoding="utf-8")
    config = {"avd.ini.encoding": "UTF-8", "AvdId": AVD, "abi.type": abi,
              "hw.cpu.arch": "arm64" if abi.startswith("arm64") else abi, "tag.id": tag, "target": platform,
              "image.sysdir.1": f"system-images/{platform}/{tag}/{abi}/", "hw.lcd.width": device["width"],
              "hw.lcd.height": device["height"], "hw.lcd.density": device["dpi"], "hw.ramSize": 2048,
              "disk.dataPartition.size": "6G", "hw.keyboard": "yes", "hw.gpu.enabled": "yes",
              "PlayStore.enabled": "false"}
    (folder / "config.ini").write_text("".join(f"{k}={v}\n" for k, v in config.items()), encoding="utf-8")


@contextmanager
def boot(folder: Path, tools: dict, device: dict | None = None, renderer: str = "auto"):
    """A booted emulator only this run's ADB server sees, yielding its serial; cleaned on every exit."""

    folder = Path(folder)
    if load(folder) is not None:
        raise ValueError("This folder already holds an Android ownership record")
    record = {"state": "owning", "tools": tools, "server": None, "client": None, "emulator": None,
              "console": None, "console_process": None, "serial": None, "forwards": [], "folders": []}
    save(folder, record)
    failure = None
    try:
        checked(tools)
        _start(folder, record, Path(tools["image"]["path"]), device or DEVICE, renderer)
        yield load(folder)["serial"]
    except BaseException as error:
        failure = error
        raise
    finally:
        left = cleanup(folder)
        if left:
            message = "Android cleanup left: " + ", ".join(left)
            if failure is None:
                raise ValueError(message)
            raise ValueError(f"{failure}; {message}") from failure


def _start(folder: Path, record: dict, image: Path, device: dict, renderer: str) -> None:
    adb, emulator = record["tools"]["adb"]["path"], record["tools"]["emulator"]["path"]
    record["server"] = {"port": free_port()}
    save(folder, record)
    client = launched(folder, record, "client", [adb, "-P", str(record["server"]["port"]), "start-server"],
                      environment(), folder / "adb-server.log")
    if client.wait(60):
        raise ValueError("The owned adb start-server failed")
    held = listeners(record["server"]["port"])
    found = identity(next(iter(held))) if len(held) == 1 else None
    if not found or found["exe"] != os.path.normcase(adb) or sha256(Path(found["exe"])) != record["tools"]["adb"]["sha256"] \
            or found["parent"] != record["client"]["pid"] or found["created"] < record["client"]["created"]:
        raise ValueError("Another process holds the owned ADB port; it is neither adopted nor stopped")
    record["server"] = {"port": record["server"]["port"], **found}
    save(folder, record)
    if devices(folder):
        raise ValueError("The owned ADB server sees devices it did not start")
    console = next((p for p in range(FIRST_CONSOLE, LAST_CONSOLE, 2) if not listeners(p) | listeners(p + 1)), None)
    if console is None:
        raise ValueError("No unused emulator console port")
    record.update(console=console, serial=f"emulator-{console}")
    record["folders"].append(HOME)
    save(folder, record)
    home = folder / HOME
    for made in (home, home / "emulator", home / "user"):   # the emulator cannot create its own home folder
        made.mkdir()
    avd(home, image, device)
    sdk = image.parents[3]
    env = environment({"ANDROID_AVD_HOME": str(home / "avd"), "ANDROID_SDK_ROOT": str(sdk), "ANDROID_HOME": str(sdk),
                       "ANDROID_EMULATOR_HOME": str(home / "emulator"), "ANDROID_USER_HOME": str(home / "user"),
                       "ANDROID_ADB_SERVER_PORT": str(record["server"]["port"])})
    proc = launched(folder, record, "emulator",
                    [emulator, "-avd", AVD, "-port", str(console), "-wipe-data", "-no-snapshot", "-no-window",
                     "-no-audio", "-no-boot-anim", "-no-metrics", "-feature", "-Vulkan",
                     *(["-gpu", renderer] if renderer != "auto" else [])], env, folder / "emulator.log")
    deadline = time.monotonic() + BOOT_SECONDS
    while True:
        if proc.poll() is not None:
            raise ValueError(f"The owned emulator exited with {proc.returncode} before booting")
        if time.monotonic() >= deadline:
            raise ValueError("The owned emulator did not boot in time")
        held = listeners(console)
        if not record["console_process"] and len(held) == 1 and descends(next(iter(held)), record["emulator"]):
            record["console_process"] = identity(next(iter(held)))
            save(folder, record)
        if record["console_process"]:
            try:
                if call(folder, ["shell", "getprop", "sys.boot_completed"], timeout=30).strip() == "1":
                    break
            except (ValueError, subprocess.TimeoutExpired):
                pass
        time.sleep(1)
    if call(folder, ["shell", "getprop", "ro.kernel.qemu"]).strip() != "1":
        raise ValueError("The owned serial is not an emulator")
    if devices(folder) != [record["serial"]]:
        raise ValueError("The owned ADB server sees a device besides its own emulator")


def forward(folder: Path, remote: str) -> int:
    """A local port forwarded to `remote` on the owned emulator, recorded before it exists."""

    record = load(folder)
    local = free_port()
    record["forwards"].append(local)
    save(folder, record)
    call(folder, ["forward", f"tcp:{local}", remote])
    return local


def unforward(folder: Path, local: int) -> None:
    call(folder, ["forward", "--remove", f"tcp:{local}"])
    record = load(folder)
    record["forwards"] = [p for p in record["forwards"] if p != local]
    save(folder, record)


# -- Cleanup -------------------------------------------------------------------------------------------

def owned_folder(folder: Path, name: str) -> Path | None:
    """`folder/name` when it is a plain folder directly inside `folder`, else `None`."""

    path = folder / name
    if path.is_symlink() or is_junction(path) or path.resolve().parent != folder.resolve():
        return None
    return path


def cleanup(folder: Path, seconds: float = 60) -> list[str]:
    """Remove what the record names, checking each identity first; what is left, by name. Idempotent:
    the collector runs it on every outcome and the orchestrator again after a killed collector."""

    folder = Path(folder)
    record = load(folder)
    if record is None:
        return []
    if record["state"] == "cleaned":
        return []
    deadline, left = time.monotonic() + seconds, []
    alive = server_ok(record)
    if alive and record.get("serial") and emulator_ok(record):
        for local in record["forwards"]:
            try:
                call(folder, ["forward", "--remove", f"tcp:{local}"], timeout=10)
            except (ValueError, subprocess.TimeoutExpired):
                pass
    for key in ("console_process", "emulator", "client"):
        if record.get(key) and not terminate(record[key]):
            left.append(key)
    if alive:
        try:
            call(folder, ["kill-server"], device=False, timeout=10)
        except (ValueError, subprocess.TimeoutExpired):
            pass
    server = record.get("server") or {}
    if "pid" in server:
        while alive and same(server, identity(server["pid"])) and time.monotonic() < deadline - 40:
            time.sleep(0.2)   # `kill-server` was sent: give it the first third of the time to exit
        if not terminate(server):
            left.append("server")
        for local in [server["port"], *record["forwards"]]:
            if any(same(server, identity(pid)) for pid in listeners(local)):
                left.append(f"listener {local}")
    for job in _jobs.pop(str(folder), []):
        process.terminated(job)
    for name in record["folders"]:
        path = owned_folder(folder, name)
        if path is None:
            left.append(f"folder {name} (not plainly owned; left in place)")
            continue
        while path.exists():
            try:
                shutil.rmtree(path)
            except OSError:
                if time.monotonic() >= deadline:
                    left.append(f"folder {name}")
                    break
                time.sleep(0.5)
    record.update(state="failed" if left else "cleaned", left=left)
    save(folder, record)
    return left


def main(argv: list[str]) -> int:
    """`adb <folder> args…`, `forward <folder> <remote>`, `unforward <folder> <local>`, `cleanup <folder>`."""

    command, folder, rest = argv[0], Path(argv[1]), argv[2:]
    if command == "adb":
        if rest[:1] == ["logcat"]:
            return call(folder, rest, stream=True)
        sys.stdout.buffer.write(call(folder, rest, binary=True))
    elif command == "forward":
        print(forward(folder, rest[0]))
    elif command == "unforward":
        unforward(folder, int(rest[0]))
    elif command == "cleanup":
        left = cleanup(folder)
        print(json.dumps({"left": left}))
        return 1 if left else 0
    else:
        raise ValueError("Expected adb, forward, unforward or cleanup")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
