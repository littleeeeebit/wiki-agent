"""One desktop look on an owned, invisible Windows desktop: its record written before anything starts, each app
in an owned job with its data in disposable folders, cleanup with its proof on every outcome, and recovery of an
earlier look's record before the next one starts (docs/research/oneshot-desktop-session.md)."""

import ctypes
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path

import native_process
import owned_job
from common import is_junction, read_record, write_record

RECORD = "desktop-session.json"
Refused = owned_job.Refused
NO_SUCH_LOGON_SESSION = 0xC000005F
DESKTOP_ALL, DESKTOP_READ = 0x01FF, 0x0001
NOT_FOUND, INVALID_PARAMETER = 2, 87
FINAL = ("proven", "recovered")
_Each = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


class _Luid(ctypes.Structure):
    _fields_ = [("low", w.DWORD), ("high", w.LONG)]


class _Statistics(ctypes.Structure):   # TOKEN_STATISTICS
    _fields_ = [("token", _Luid), ("logon", _Luid), ("expires", ctypes.c_int64), ("kind", ctypes.c_int),
                ("level", ctypes.c_int), ("dynamic", w.DWORD), ("available", w.DWORD), ("groups", w.DWORD),
                ("privileges", w.DWORD), ("modified", _Luid)]


class _Text(ctypes.Structure):   # LSA_UNICODE_STRING
    _fields_ = [("length", w.USHORT), ("most", w.USHORT), ("buffer", w.LPWSTR)]


class _Logon(ctypes.Structure):   # SECURITY_LOGON_SESSION_DATA, up to LogonTime
    _fields_ = [("size", w.ULONG), ("logon", _Luid), ("user", _Text), ("domain", _Text), ("package", _Text),
                ("kind", w.ULONG), ("session", w.ULONG), ("sid", w.LPVOID), ("time", ctypes.c_int64)]


def _native():
    k, _ = native_process._kernel()
    u = ctypes.WinDLL("user32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    s = ctypes.WinDLL("secur32")
    k.GetTickCount64.restype = ctypes.c_uint64
    k.GetCurrentProcess.restype = w.HANDLE
    k.ProcessIdToSessionId.argtypes = (w.DWORD, ctypes.POINTER(w.DWORD))
    k.OpenJobObjectW.argtypes, k.OpenJobObjectW.restype = (w.DWORD, w.BOOL, w.LPCWSTR), w.HANDLE
    k.TerminateJobObject.argtypes = (w.HANDLE, w.UINT)
    a.OpenProcessToken.argtypes = (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
    a.GetTokenInformation.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))
    s.LsaGetLogonSessionData.argtypes = (ctypes.POINTER(_Luid), ctypes.POINTER(ctypes.POINTER(_Logon)))
    s.LsaGetLogonSessionData.restype = ctypes.c_ulong
    s.LsaFreeReturnBuffer.argtypes = (w.LPVOID,)
    u.CreateDesktopW.argtypes = (w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.DWORD, w.DWORD, w.LPVOID)
    u.CreateDesktopW.restype = w.HANDLE
    u.OpenDesktopW.argtypes, u.OpenDesktopW.restype = (w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    u.CloseDesktop.argtypes = (w.HANDLE,)
    u.EnumDesktopWindows.argtypes = (w.HANDLE, _Each, w.LPARAM)
    return k, u, a, s


def uptime() -> int:
    """Milliseconds since this boot. A value lower than a recorded one proves a new boot; wall clock never does."""

    return int(_native()[0].GetTickCount64())


def session() -> int:
    k = _native()[0]
    found = w.DWORD()
    if not k.ProcessIdToSessionId(os.getpid(), ctypes.byref(found)):
        raise Refused(f"cannot read this server's session: {ctypes.get_last_error()}")
    return int(found.value)


def _logon_time(luid: int) -> tuple[int, int | None]:
    """(status, logon time) of the logon session `luid` names."""

    _, _, _, s = _native()
    data = ctypes.POINTER(_Logon)()
    status = s.LsaGetLogonSessionData(ctypes.byref(_Luid(luid & 0xFFFFFFFF, luid >> 32)), ctypes.byref(data))
    if status:
        return status, None
    try:
        return 0, int(data.contents.time)
    finally:
        s.LsaFreeReturnBuffer(data)


def logon() -> dict:
    """This server's logon session: its id (LUID) and its logon time."""

    k, _, a, _ = _native()
    token, stats, size = w.HANDLE(), _Statistics(), w.DWORD()
    if not a.OpenProcessToken(k.GetCurrentProcess(), 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
        raise Refused(f"cannot read this server's logon session: {ctypes.get_last_error()}")
    try:
        if not a.GetTokenInformation(token, 10, ctypes.byref(stats), ctypes.sizeof(stats), ctypes.byref(size)):
            raise Refused(f"cannot read this server's logon session: {ctypes.get_last_error()}")
    finally:
        k.CloseHandle(token)
    luid = stats.logon.high << 32 | stats.logon.low
    status, logged_on = _logon_time(luid)
    if status:
        raise Refused(f"cannot read this server's logon session: status {status:#x}")
    return {"luid": luid, "time": logged_on}


def logon_ended(recorded: dict) -> bool | None:
    """The logon session `recorded` names no longer exists: `True`, `False`, or `None` when Windows gives no
    answer. A session with the same id and another logon time is another session."""

    status, logged_on = _logon_time(recorded["luid"])
    if status == NO_SUCH_LOGON_SESSION:
        return True
    if status:
        return None
    return logged_on != recorded["time"]


def exited(recorded: dict) -> bool | None:
    """The process `recorded` names has exited: its handle is signalled, its pid opens nothing, or it opens a
    process created at another time. `None` when Windows gives no answer, such as access denied."""

    k, _ = native_process._kernel()
    handle = k.OpenProcess(native_process.QUERY | native_process.SYNCHRONIZE, False, recorded["pid"])
    if not handle:
        return True if ctypes.get_last_error() == INVALID_PARAMETER else None
    try:
        if k.WaitForSingleObject(handle, 0) == 0:
            return True
        now = native_process.identity(recorded["pid"], handle)
        return None if now is None else not native_process.same(recorded, now)
    finally:
        k.CloseHandle(handle)


def load(folder: Path) -> dict | None:
    return read_record(Path(folder) / RECORD)


def save(folder: Path, record: dict) -> None:
    write_record(Path(folder) / RECORD, record)


def _removed(folder: Path, names: list[str], seconds: float = 30) -> list[str]:
    """Remove each disposable folder the record lists; what is left, by name. A name that is not a plain folder
    directly inside `folder` is left in place and named."""

    deadline, left = time.monotonic() + seconds, []
    for name in names:
        path = folder / name
        if path.is_symlink() or is_junction(path) or path.resolve().parent != folder.resolve():
            left.append(f"folder {name} is not plainly owned; left in place")
            continue
        while path.exists():
            try:
                shutil.rmtree(path)
            except OSError as error:
                if time.monotonic() >= deadline:
                    left.append(f"folder {name} could not be removed: {error}")
                    break
                time.sleep(0.5)
    return left


class Look:
    """One look: an owned desktop, its apps' jobs and their disposable folders, all named in `folder`'s record.
    Use it through `look()`, which runs `close()` on every outcome."""

    def __init__(self, root: Path, name: str):
        self.root, self.folder = Path(root), Path(root) / name
        self.jobs: dict[str, owned_job.Job] = {}
        self.desktop = None
        self.closed = False
        held = recover_all(self.root)
        if held:
            raise Refused("an earlier look is held: " + "; ".join(held))
        self.k, self.u, _, _ = _native()
        self.record = {"state": "owning", "desktop": "wiki-look-" + uuid.uuid4().hex, "session": session(),
                       "logon": logon(), "uptime": uptime(), "owner": native_process.identity(os.getpid()),
                       "folders": [], "jobs": {}, "listeners": [], "proof": None}
        self.folder.mkdir()
        (self.folder / "evidence").mkdir()
        save(self.folder, self.record)

    def open(self) -> None:
        handle = self.u.CreateDesktopW(self.record["desktop"], None, None, 0, DESKTOP_ALL, None)
        if not handle:
            raise Refused(f"cannot create the look's desktop: {ctypes.get_last_error()}")
        self.desktop = handle

    def start(self, key: str, argv: list[str], cwd, extra: dict | None = None) -> dict:
        """Start `argv` in a job of its own on the look's desktop, its data folders inside a disposable folder
        the record names first, its output in `evidence/<key>.log`. The root's identity."""

        if key in self.jobs or not key.isidentifier():
            raise ValueError(f"not a fresh job key: {key!r}")
        data = f"data-{key}"
        self.record["folders"].append(data)
        save(self.folder, self.record)
        folders = {name: self.folder / data / name for name in ("Roaming", "Local", "Temp")}
        for path in folders.values():
            path.mkdir(parents=True)
        job = owned_job.Job()
        self.jobs[key] = job
        self.record["jobs"][key] = {"name": job.name, "uptime": job.uptime, "root": None, "members": [],
                                    "proof": None}
        save(self.folder, self.record)
        env = {**os.environ, "APPDATA": str(folders["Roaming"]), "LOCALAPPDATA": str(folders["Local"]),
               "TEMP": str(folders["Temp"]), "TMP": str(folders["Temp"]), **(extra or {})}

        def recorded(root: dict) -> None:
            self.record["jobs"][key].update(root=root, members=[root])
            save(self.folder, self.record)

        with (self.folder / "evidence" / f"{key}.log").open("ab") as log:
            return job.start(argv, cwd, env, self.record["desktop"], log, before_resume=recorded)

    def observe(self) -> None:
        """Record every member each job has seen, so recovery can still name them after a crash."""

        for key, job in self.jobs.items():
            known = self.record["jobs"][key]["members"]
            for member in job.members():
                if not any(native_process.same(member, seen) for seen in known):
                    known.append(member)
        save(self.folder, self.record)

    def _recorded(self) -> list[dict]:
        return [member for entry in self.record["jobs"].values() for member in entry["members"]]

    def _desktop_closed(self, seconds: float = 10) -> list[str]:
        if not self.desktop:
            return []
        reasons, deadline, found, error = [], time.monotonic() + seconds, None, 0
        while True:
            windows = []
            ctypes.set_last_error(0)   # an empty desktop sets no error; an earlier call's must not stand in
            ok =self.u.EnumDesktopWindows(self.desktop, _Each(lambda hwnd, _: windows.append(hwnd) or True), 0)
            error = 0 if ok else ctypes.get_last_error()
            found = None if error else windows   # an empty desktop answers 0 with no error
            if found == [] or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if found is None:
            reasons.append(f"cannot list the desktop's windows: Windows error {error}")
        elif found:
            reasons.append(f"{len(found)} window(s) remain on the desktop")
        if not self.u.CloseDesktop(self.desktop):
            reasons.append(f"cannot close the desktop: Windows error {ctypes.get_last_error()}")
        self.desktop = None
        again = self.u.OpenDesktopW(self.record["desktop"], 0, False, DESKTOP_READ)
        if again:
            self.u.CloseDesktop(again)
            reasons.append("the desktop still opens by its name")
        elif ctypes.get_last_error() != NOT_FOUND:
            reasons.append(f"cannot tell whether the desktop is gone: Windows error {ctypes.get_last_error()}")
        return reasons

    def close(self, seconds: float = 30) -> dict:
        """End every job, close the desktop, and prove each part gone. The proof, also saved in the record; a
        look whose proof is not clean stays held until recovery proves it."""

        if self.closed:
            return self.record["proof"]
        self.closed = True
        reasons = []
        try:
            self.observe()
        except Exception as error:   # a failed read leaves the proof to the jobs' own count
            reasons.append(f"cannot record the members: {error}")
        recorded = self._recorded()
        try:
            ports = sorted({port for state, port, _, pid in native_process._sockets(3)
                            if state == 2 and any(member["pid"] == pid for member in recorded)})
        except ValueError as error:
            ports = []
            reasons.append(f"cannot read the listeners: {error}")
        self.record["listeners"] = ports
        for key, job in self.jobs.items():
            proof = job.end(seconds)
            job.close()
            self.record["jobs"][key]["proof"] = proof
            reasons += [f"{key}: {reason}" for reason in proof["reasons"]]
        reasons += self._desktop_closed()
        for member in recorded:
            gone = exited(member)
            if gone is not True:
                reasons.append(f"pid {member['pid']} " + ("is still running" if gone is False
                                                          else "cannot be shown to have exited"))
        for port in ports:
            try:
                owners = native_process.listeners(port)
            except ValueError as error:
                reasons.append(f"cannot read the listeners of port {port}: {error}")
                continue
            if any(native_process.same(member, native_process.identity(pid)) for pid in owners for member in recorded):
                reasons.append(f"port {port} is still held by a recorded process")
        reasons += _removed(self.folder, self.record["folders"])
        self.record.update(state="held" if reasons else "proven",
                           proof={"clean": not reasons, "reasons": reasons, "uptime": uptime()})
        save(self.folder, self.record)
        return self.record["proof"]


@contextmanager
def look(root: Path, name: str):
    """A look in `root/name`, its desktop open; cleaned and proven on every exit. A proof that is not clean
    raises, naming what it could not prove."""

    session_ = Look(root, name)
    failure = None
    try:
        session_.open()
        yield session_
    except BaseException as error:
        failure = error
        raise
    finally:
        proof = session_.close()
        if not proof["clean"]:
            message = "the desktop look is held: " + "; ".join(proof["reasons"])
            if failure is None:
                raise Refused(message)
            raise Refused(f"{failure}; {message}") from failure


def recover(folder: Path) -> list[str]:
    """Settle an earlier look's record. `[]` once its proof completed, or once a new boot or the end of its
    logon session proved its processes ended and its disposable folders are gone; otherwise why it is held."""

    folder = Path(folder)
    record = load(folder)
    if record is None or record["state"] in FINAL:
        return []
    owner = record.get("owner")
    if owner and owner["pid"] != os.getpid() and native_process.same(owner, native_process.identity(owner["pid"])):
        return [f"{folder.name}: its server is still running"]
    if uptime() < record["uptime"]:
        proof = "the PC restarted after the record was written"
    elif logon_ended(record["logon"]):
        proof = "the logon session it ran in ended"
    else:
        proof = None
    if proof:
        left = _removed(folder, record["folders"])
        record.update(state="held" if left else "recovered", recovered=None if left else proof, held=left)
        save(folder, record)
        return [f"{folder.name}: {reason}" for reason in left]
    k = _native()[0]
    for entry in record["jobs"].values():
        for member in entry["members"]:
            native_process.terminate(member)
        if record["session"] == session():   # a `Local\` name means nothing in another session
            job = k.OpenJobObjectW(0x0008, False, entry["name"])   # JOB_OBJECT_TERMINATE
            if job:
                k.TerminateJobObject(job, 1)
                k.CloseHandle(job)
    held = ["no proof its processes ended; restart the PC or sign out to release it"]
    record.update(state="held", held=held)
    save(folder, record)
    return [f"{folder.name}: {reason}" for reason in held]


def recover_all(root: Path) -> list[str]:
    """Settle every earlier look's record under `root`; why any is still held."""

    root = Path(root)
    if not root.is_dir():
        return []
    return [reason for child in sorted(root.iterdir()) if (child / RECORD).is_file() for reason in recover(child)]
