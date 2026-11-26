"""The machine an agent runs on: its size once per boot, and its load now (plan 15a).

The size is the CPU with its cores and threads, the RAM, each GPU with its VRAM and each disk with its type and
bus, read from WMI and `nvidia-smi` once per boot and kept under the temporary folder. A value that cannot be read
is left out, never guessed. The load is CPU percent (two `GetSystemTimes` reads 100 ms apart), RAM
(`GlobalMemoryStatusEx`) and, per NVIDIA adapter, VRAM and the processes the GPU lists. A sample is kept for
`KEEP` seconds across the hook's processes; a part that fails or runs out of time is `None`, shown as `읽지 못함`.

On WDDM `--query-compute-apps` lists graphics users too (`explorer.exe`, `ShellHost.exe`), each with memory
`[N/A]` and some named `[Insufficient Permissions]`: every listed pid is a holder, and no process's share is read.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

KEEP = 2.0   # seconds a sample is reused by the next hook process
STALE = 10.0   # a sample older than this is never shown
SMI_SECONDS = 1.5
UNREAD = "읽지 못함"
UNOWNED = "세션 소유 확인 불가"
VRAM_FLOOR, RAM_FLOOR = 0.20, 0.15   # the free share the queue's head waits for (`gpu_queue`)
STATE = Path(tempfile.gettempdir())


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _boot() -> float:
    """When this boot began, to the second, as the wall clock puts it."""

    if os.name != "nt":
        return time.time() - time.monotonic()
    k = ctypes.WinDLL("kernel32")
    k.GetTickCount64.restype = ctypes.c_uint64
    return time.time() - k.GetTickCount64() / 1000


def _wmi() -> dict:
    """CPU, RAM, video controllers and physical disks from WMI through PowerShell; `{}` when it cannot be read."""

    import shutil

    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell or os.name != "nt":
        return {}
    script = ("$c = Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors; "
              "$m = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory; "
              "$v = Get-CimInstance Win32_VideoController | Select-Object Name; "
              "$d = Get-PhysicalDisk | Select-Object FriendlyName,MediaType,BusType,Size; "
              "@{cpu=@($c); ram=$m; video=@($v); disks=@($d)} | ConvertTo-Json -Compress -Depth 4")
    try:
        done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=15, **_no_window())
        return json.loads(done.stdout) if done.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def _gpus(timeout: float = SMI_SECONDS) -> list[dict] | None:
    """Each NVIDIA adapter's uuid, name, used and total VRAM in MiB; `None` when `nvidia-smi` is missing or fails."""

    rows = _smi(["--query-gpu=uuid,name,memory.used,memory.total", "--format=csv,noheader,nounits"], timeout)
    if rows is None:
        return None
    try:
        return [{"uuid": r[0].strip(), "name": r[1].strip(), "used": float(r[2]), "total": float(r[3])}
                for r in (line.split(",", 3) for line in rows) if len(r) == 4]
    except ValueError:
        return None


def _smi(args: list[str], timeout: float) -> list[str] | None:
    try:
        done = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, **_no_window())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [line for line in done.stdout.splitlines() if line.strip()] if done.returncode == 0 else None


def size() -> dict:
    """The machine's parts, read once per boot and kept under the temporary folder."""

    kept, boot = STATE / "wiki-agent-machine.json", _boot()
    try:
        saved = json.loads(kept.read_text(encoding="utf-8"))
        if abs(saved["boot"] - boot) < 30:
            return saved["size"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    found, wmi = {}, _wmi()
    cpu = (wmi.get("cpu") or [None])[0]
    if isinstance(cpu, dict) and cpu.get("Name"):
        found["cpu"] = {"name": " ".join(str(cpu["Name"]).split()), "cores": cpu.get("NumberOfCores"),
                        "threads": cpu.get("NumberOfLogicalProcessors")}
    if isinstance(wmi.get("ram"), (int, float)) and wmi["ram"] > 0:
        found["ram_gb"] = round(wmi["ram"] / 2 ** 30)
    gpus = _gpus(5)
    if gpus:
        found["gpus"] = [{"name": g["name"], "vram_gb": round(g["total"] / 1024)} for g in gpus]
    elif wmi.get("video"):
        found["gpus"] = [{"name": v["Name"]} for v in wmi["video"] if isinstance(v, dict) and v.get("Name")]
    found["disks"] = [{"name": d.get("FriendlyName"), "type": d.get("MediaType"), "bus": d.get("BusType"),
                       "gb": round(d["Size"] / 1e9) if isinstance(d.get("Size"), (int, float)) else None}
                      for d in wmi.get("disks") or [] if isinstance(d, dict) and d.get("FriendlyName")]
    if wmi:   # a failed read is tried again by the next session, never kept for the boot
        try:
            part = kept.with_suffix(f".{os.getpid()}.part")
            part.write_text(json.dumps({"boot": boot, "size": found}), encoding="utf-8")
            os.replace(part, kept)
        except OSError:
            pass
    return found


def size_line(found: dict | None = None) -> str:
    """One line for a session's opening context; parts that could not be read are left out."""

    found = size() if found is None else found
    bits = []
    if cpu := found.get("cpu"):
        counts = ", ".join(f"{cpu[k]} {k}" for k in ("cores", "threads") if cpu.get(k))
        bits.append(f"CPU {cpu['name']}" + (f" ({counts})" if counts else ""))
    if found.get("ram_gb"):
        bits.append(f"RAM {found['ram_gb']} GB")
    for gpu in found.get("gpus") or []:
        bits.append(f"GPU {gpu['name']}" + (f" {gpu['vram_gb']} GB VRAM" if gpu.get("vram_gb") else ""))
    disks = []
    for disk in found.get("disks") or []:
        parts = [str(x) for x in (disk.get("type"), disk.get("bus")) if x]
        parts += [f"{disk['gb']} GB"] if disk.get("gb") else []
        disks.append(disk["name"] + (f" ({', '.join(parts)})" if parts else ""))
    if disks:
        bits.append("disks " + "; ".join(disks))
    return ("This machine: " + " · ".join(bits) + ". Every session on it shares these; plan tests and GPU work "
            "for the load each call shows.") if bits else ""


# -- The load now ------------------------------------------------------------------------------------------------

def _cpu() -> float | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)

    def read():
        idle, kernel, user = w.FILETIME(), w.FILETIME(), w.FILETIME()
        if not k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return [t.dwHighDateTime << 32 | t.dwLowDateTime for t in (idle, kernel, user)]

    first = read()
    time.sleep(0.1)
    second = read()
    if not first or not second:
        return None
    idle, busy = second[0] - first[0], (second[1] - first[1]) + (second[2] - first[2])   # kernel includes idle
    return round(100 * (1 - idle / busy), 1) if busy > 0 else None


def _ram() -> tuple[float, float] | None:
    """(used, total) in GiB."""

    if os.name != "nt":
        return None

    class Status(ctypes.Structure):   # MEMORYSTATUSEX
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                    ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                    ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

    status = Status(length=ctypes.sizeof(Status))
    if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)) or not status.total:
        return None
    return (status.total - status.free) / 2 ** 30, status.total / 2 ** 30


def _holders(timeout: float = SMI_SECONDS) -> list[dict] | None:
    """Every process an NVIDIA adapter lists: its adapter, pid and name ("" when Windows withholds it)."""

    rows = _smi(["--query-compute-apps=gpu_uuid,pid,process_name", "--format=csv,noheader"], timeout)
    if rows is None:
        return None
    found = []
    for row in rows:
        parts = row.split(",", 2)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            continue
        name = parts[2].strip() if len(parts) > 2 else ""
        found.append({"gpu": parts[0].strip(), "pid": int(parts[1]),
                      "name": "" if name.startswith("[") else Path(name).name})
    return found


def measured() -> dict:
    """A new sample: the parts are read side by side so the whole takes about the CPU's 100 ms."""

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as pool:
        parts = {name: pool.submit(read) for name, read in
                 (("cpu", _cpu), ("ram", _ram), ("gpus", _gpus), ("holders", _holders))}
        found = {}
        for name, future in parts.items():
            try:
                found[name] = future.result()
            except Exception:  # noqa: BLE001 -- a part that fails is unread, never a guess
                found[name] = None
    if found["gpus"] is None:   # the list means nothing without the adapters it belongs to
        found["holders"] = None
    return {"at": time.time(), **found}


def sample(fresh: bool = False) -> dict:
    """The load now: a sample another hook took in the last `KEEP` seconds, else a new one."""

    if given := os.environ.get("WIKI_AGENT_LOAD_FILE"):   # a test's machine: its load read from a file
        return {**json.loads(Path(given).read_text(encoding="utf-8")), "at": time.time()}
    kept = STATE / "wiki-agent-load.json"
    if not fresh:
        try:
            saved = json.loads(kept.read_text(encoding="utf-8"))
            if 0 <= time.time() - saved["at"] < KEEP:
                return saved
        except (OSError, ValueError, KeyError, TypeError):
            pass
    found = measured()
    try:
        part = kept.with_suffix(f".{os.getpid()}.part")
        part.write_text(json.dumps(found), encoding="utf-8")
        os.replace(part, kept)
    except OSError:
        pass
    return found


def floor(found: dict) -> str:
    """Why the queue's head may not start yet: free VRAM under `VRAM_FLOOR` on an adapter, or free RAM under
    `RAM_FLOOR`; "" when it may. A part that was not read applies no floor."""

    short = []
    for i, gpu in enumerate(found.get("gpus") or []):
        if gpu["total"] and (gpu["total"] - gpu["used"]) / gpu["total"] < VRAM_FLOOR:
            short.append(f"GPU {i} VRAM free {(gpu['total'] - gpu['used']) / 1024:.1f}/{gpu['total'] / 1024:.1f} GB "
                         f"(needs {VRAM_FLOOR:.0%})")
    if ram := found.get("ram"):
        used, total = ram
        if total and (total - used) / total < RAM_FLOOR:
            short.append(f"RAM free {total - used:.1f}/{total:.1f} GB (needs {RAM_FLOOR:.0%})")
    if short:
        names = ", ".join(f"{h['name'] or '?'} {h['pid']}" for h in found.get("holders") or [])
        return "; ".join(short) + (f" — on the GPU: {names}" if names else "")
    return ""


def load_line(found: dict, own: list[dict] | None) -> str:
    """The load for one call's context. `own`: the listed processes that are this session's, `None` when that
    could not be told."""

    if time.time() - found.get("at", 0) > STALE:
        return ""
    bits = [f"CPU {found['cpu']:.0f}%" if found.get("cpu") is not None else f"CPU {UNREAD}"]
    ram = found.get("ram")
    bits.append(f"RAM {ram[0]:.1f}/{ram[1]:.1f} GB used" if ram else f"RAM {UNREAD} (no RAM floor)")
    gpus = found.get("gpus")
    if gpus is None:
        bits.append(f"GPU {UNREAD} (nvidia-smi gave nothing: no VRAM floor, no own-holder check)")
    for i, gpu in enumerate(gpus or []):
        bits.append(f"GPU {i} VRAM {gpu['used'] / 1024:.1f}/{gpu['total'] / 1024:.1f} GB used")
    holders = found.get("holders")
    if holders:
        names = ", ".join(f"{h['name'] or '?'} {h['pid']}" for h in holders)
        bits.append(f"on the GPU ({len(holders)}, graphics users included): {names}")
    if gpus is not None:
        if own is None:
            bits.append(UNOWNED)
        elif own:
            bits.append("this session's own on the GPU: " + ", ".join(f"{h['name'] or '?'} {h['pid']}" for h in own))
    return "Load now: " + " · ".join(bits)
