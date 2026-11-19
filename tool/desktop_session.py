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


class Halted(Exception):
    """The server was halted while a root was being started: it was rolled back before it ran."""


NO_SUCH_LOGON_SESSION = 0xC000005F
DESKTOP_ALL, DESKTOP_READ = 0x01FF, 0x0001
NOT_FOUND, INVALID_PARAMETER = 2, 87
FINAL = ("proven", "recovered")
LOG_TAIL = 64 * 1024   # the bytes of a run's log it returns, its end: a writer still running cannot extend the read
RELEASE_RETRIES = 10   # half a second apart: a handle still held after that keeps its server the owner
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


def _plain(parent: Path, path: Path) -> bool:
    """`path` is a plain folder entry directly inside `parent`: no link or junction leads it elsewhere."""

    return not (path.is_symlink() or is_junction(path)) and path.resolve().parent == parent.resolve()


def _removed(folder: Path, entries: list[dict], seconds: float = 30) -> list[str]:
    """Remove each disposable folder the record lists as the look's own; what is left, by name. A folder only
    reserved, never shown to be created by the look, is never removed, and one that exists is named. So is a
    name that is not a plain folder directly inside `folder`."""

    deadline, left = time.monotonic() + seconds, []
    for entry in entries:
        name = entry["name"]
        path = folder / name
        if not entry["owned"]:
            if path.exists():
                left.append(f"folder {name} was reserved but not created by the look; left in place")
            continue
        if not _plain(folder, path):
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
        self.root = Path(root).absolute()   # a child starts elsewhere: its data paths must not be relative
        self.folder = self.root / name
        self.result = None   # the teardown's reasons, kept until its proof is saved
        self.closing = False
        self.probes: list = []   # name-probe handles Windows would not close, still held
        self.jobs: dict[str, owned_job.Job] = {}
        self.desktop = None
        self.closed = False
        held = recover_all(self.root)
        if held:
            raise Refused("an earlier look is held: " + "; ".join(held))
        self.k, self.u, _, _ = _native()
        owner = native_process.identity(os.getpid())
        if owner is None:   # recovery could not tell a live look of this server from a dead one
            raise Refused("cannot record this server's own identity")
        self.record = {"state": "owning", "desktop": "wiki-look-" + uuid.uuid4().hex, "session": session(),
                       "logon": logon(), "uptime": uptime(), "owner": owner, "folders": [], "jobs": {},
                       "listeners": [], "proof": None}
        self.folder.mkdir()
        (self.folder / "evidence").mkdir()
        save(self.folder, self.record)

    def _open_for_more(self) -> None:
        if self.closing:   # nothing may join a look whose cleanup began, saved or not
            raise Refused("the look's cleanup has begun")

    def open(self) -> None:
        self._open_for_more()
        if self.desktop:
            raise Refused("the look's desktop is already open")
        handle = self.u.CreateDesktopW(self.record["desktop"], None, None, 0, DESKTOP_ALL, None)
        if not handle:
            raise Refused(f"cannot create the look's desktop: {ctypes.get_last_error()}")
        self.desktop = handle

    def check_key(self, key: str) -> None:
        """A job key is one identifier no job of this look carries yet, compared without case, since the key
        also names the job's folders and log."""

        if not key.isidentifier() or key.lower() in {known.lower() for known in self.jobs}:
            raise ValueError(f"not a fresh job key: {key!r}")

    def reserve(self, key: str) -> Path:
        """A fresh disposable folder the record names before it exists and cleanup removes, such as a stack's own
        profile folder (Edge's `--user-data-dir`)."""

        self._open_for_more()
        if not key.isidentifier():   # one plain path component: the folder stays a direct child of the look
            raise ValueError(f"not a folder key: {key!r}")
        name = f"data-{key}-{uuid.uuid4().hex[:12]}"
        reserved = {"name": name, "owned": False}
        self.record["folders"].append(reserved)
        save(self.folder, self.record)
        (self.folder / name).mkdir()   # never an existing folder: only one the look created is ever removed
        reserved["owned"] = True
        save(self.folder, self.record)
        return self.folder / name

    def start(self, key: str, argv: list[str], cwd, extra: dict | None = None, halt=None) -> dict:
        """Start `argv` in a job of its own on the look's desktop, its data folders inside a disposable folder
        the record names first, its output in `evidence/<key>.log`. The root's identity. A `halt` set by the
        time the suspended root is recorded ends it before it runs (`Halted`)."""

        self._open_for_more()
        self.check_key(key)
        data = self.reserve(key)
        folders = {name: data / name for name in ("Roaming", "Local", "Temp")}
        for path in folders.values():
            path.mkdir()
        job = owned_job.Job()
        self.jobs[key] = job
        self.record["jobs"][key] = {"name": job.name, "uptime": job.uptime, "root": None, "members": [],
                                    "proof": None}
        save(self.folder, self.record)
        owned = {"APPDATA": str(folders["Roaming"]), "LOCALAPPDATA": str(folders["Local"]),
                 "TEMP": str(folders["Temp"]), "TMP": str(folders["Temp"])}
        # Windows reads names without case: no spelling of an owned name from the server or `extra` survives.
        env = {name: value for name, value in {**os.environ, **(extra or {})}.items()
               if name.upper() not in owned} | owned

        def recorded(root: dict) -> None:
            self.record["jobs"][key].update(root=root, members=[root])
            save(self.folder, self.record)
            if halt is not None and halt.is_set():   # still suspended: the job rolls it back, never run
                raise Halted(f"halted before {argv[0]} ran")

        with (self.folder / "evidence" / f"{key}.log").open("ab") as log:
            return job.start(argv, cwd, env, self.record["desktop"], log, before_resume=recorded)

    def run(self, key: str, argv: list[str], cwd, seconds: float, halt, extra: dict | None = None) \
            -> tuple[int | None, str, str]:
        """`argv` started as `start` starts it and waited for until its root exits: (its exit code, its output,
        why the wait was cut). Nothing starts once `halt` is set. An exit counts only when it was read by the
        deadline, so a root seen finished after it is cut as one still running would be. A build server or a
        window the root left running ends with the look."""

        if halt.is_set():
            return None, "", "멈춤"
        try:
            root = self.start(key, argv, cwd, extra, halt)
        except Halted:
            return None, "", "멈춤"
        job, deadline, cut = self.jobs[key], time.monotonic() + seconds, ""
        while True:
            code = job.code(root)
            if halt.is_set():
                cut = "멈춤"
            elif time.monotonic() > deadline:   # read after the code: an exit it shows happened by now
                cut = f"{int(seconds)}초 안에 끝나지 않았다"
            if cut or code is not None:
                break
            time.sleep(max(0.0, min(0.2, deadline - time.monotonic())))
        self.observe()
        with open(self.folder / "evidence" / f"{key}.log", "rb") as log:   # a member may still be writing it
            log.seek(max(0, log.seek(0, os.SEEK_END) - LOG_TAIL))
            output = log.read(LOG_TAIL).decode("utf-8", "replace")
        return (None if cut else code), output, cut

    def observe(self) -> None:
        """Record every member each job has seen, so recovery can still name them after a crash."""

        for key, job in self.jobs.items():
            self._learn(key, job.members())
        save(self.folder, self.record)

    def _learn(self, key: str, members: list[dict]) -> None:
        known = self.record["jobs"][key]["members"]
        for member in members:
            if not any(native_process.same(member, seen) for seen in known):
                known.append(member)

    def _recorded(self) -> list[dict]:
        return [member for entry in self.record["jobs"].values() for member in entry["members"]]

    def _desktop_closed(self, seconds: float = 10) -> list[str]:
        if not self.desktop:
            return []
        reasons, deadline, found, error = [], time.monotonic() + seconds, None, 0
        try:
            while True:
                windows = []
                ctypes.set_last_error(0)   # an empty desktop sets no error; an earlier call's must not stand in
                ok = self.u.EnumDesktopWindows(self.desktop, _Each(lambda hwnd, _: windows.append(hwnd) or True), 0)
                error = 0 if ok else ctypes.get_last_error()
                found = None if error else windows   # an empty desktop answers 0 with no error
                if found == [] or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
        finally:   # released even when the wait is interrupted; a handle that will not close is kept
            if self.u.CloseDesktop(self.desktop):
                self.desktop = None
            else:
                reasons.append(f"cannot close the desktop: Windows error {ctypes.get_last_error()}")
        if found is None:
            reasons.append(f"cannot list the desktop's windows: Windows error {error}")
        elif found:
            reasons.append(f"{len(found)} window(s) remain on the desktop")
        if self.desktop:
            return reasons
        # A reference Windows has not dropped yet can keep the name a moment: the probe is retried, bounded.
        probe_until = time.monotonic() + 5
        while True:
            again = self.u.OpenDesktopW(self.record["desktop"], 0, False, DESKTOP_READ)
            error = 0 if again else ctypes.get_last_error()
            if again:
                self.probes.append(again)   # counted as held until its close is seen to succeed
                if not self.u.CloseDesktop(again):
                    break   # held, and no further probe opens another
                self.probes.remove(again)
            if error == NOT_FOUND or time.monotonic() >= probe_until:
                break
            time.sleep(0.1)
        if again:
            reasons.append("the desktop still opens by its name")
        elif error != NOT_FOUND:
            reasons.append(f"cannot tell whether the desktop is gone: Windows error {error}")
        return reasons

    def close(self, seconds: float = 30) -> dict:
        """End every job, close the desktop, and prove each part gone. The proof, also saved in the record; a
        look whose proof is not clean stays held until recovery proves it."""

        if self.closed:
            return self.record["proof"]
        self.closing = True
        if self.result is None:
            self.result = self._teardown(seconds)
            interrupted = self.result[1]
        else:   # a retry keeps the teardown it already ran and only releases what is still held
            interrupted = self._release()
        left = self._outstanding()
        stuck = "a handle of the look could not be released at once"
        if left and stuck not in self.result[0]:
            self.result[0].append(stuck)   # kept after a later release: the look could not show it gone then
        reasons = list(self.result[0])
        if left:
            reasons.append(f"{left} handle(s) of the look are still held; close() retries them")
        # Recovery may settle the record without its server's exit only once this server holds nothing of the
        # look; until then its owner stays recorded and the look stays open to a retry.
        record = {**self.record, "state": "held" if reasons else "proven",
                  "owner": self.record["owner"] if left else None,
                  "proof": {"clean": not reasons, "reasons": reasons, "uptime": uptime()}}
        save(self.folder, record)
        self.record, self.closed = record, not left
        if interrupted:
            raise interrupted
        return record["proof"]

    def _outstanding(self) -> int:
        return (bool(self.desktop) + len(self.probes)
                + sum(job.outstanding() for job in self.jobs.values()))

    def _release(self) -> BaseException | None:
        """Close again every handle an earlier close could not; the interruption to raise, if one came."""

        interrupted = None

        def attempt(step) -> None:
            nonlocal interrupted
            try:
                step()
            except BaseException as error:   # what stays held is counted by `_outstanding`
                if not isinstance(error, Exception):
                    interrupted = interrupted or error

        def desktop() -> None:
            if self.u.CloseDesktop(self.desktop):
                self.desktop = None

        def probe(handle) -> None:
            if self.u.CloseDesktop(handle):
                self.probes.remove(handle)

        if self.desktop:
            attempt(desktop)
        for handle in list(self.probes):
            attempt(lambda handle=handle: probe(handle))
        for job in self.jobs.values():
            attempt(job.close)
        return interrupted

    def _teardown(self, seconds: float) -> tuple[list[str], BaseException | None]:
        """Try every part of the cleanup, each whatever an earlier one did. An interruption is held back until
        every handle was released: (reasons, the interruption to raise)."""

        reasons, ports, interrupted = [], [], None

        def attempt(label, step) -> None:
            nonlocal interrupted
            try:
                reasons.extend(step() or [])
            except BaseException as error:
                reasons.append(f"{label}: {error!r}")
                if not isinstance(error, Exception):
                    interrupted = interrupted or error

        attempt("cannot record the members", self.observe)
        recorded = self._recorded()
        attempt("cannot read the listeners", lambda: ports.extend(sorted(
            {port for state, port, _, pid in native_process._sockets(3)
             if state == 2 and any(member["pid"] == pid for member in recorded)})))
        self.record["listeners"] = ports
        for key, job in self.jobs.items():
            attempt(f"{key}: cannot end the job", lambda key=key, job=job: self._ended(key, job, seconds))
        attempt("cannot close the desktop", self._desktop_closed)
        attempt("cannot check the recorded processes", lambda: self._exits(recorded))
        attempt("cannot check the listeners", lambda: self._listening(ports, recorded))
        attempt("cannot remove the folders", lambda: _removed(self.folder, self.record["folders"]))
        if interrupted:
            reasons.append("cleanup was interrupted")
        return reasons, interrupted

    def _ended(self, key: str, job, seconds: float) -> list[str]:
        try:
            proof = job.end(seconds)
            # Members first heard of while the job ended: recorded before any handle closes, so recovery names
            # them however the close goes.
            self._learn(key, proof["members"])
            self.record["jobs"][key]["proof"] = proof
        finally:
            job.close()
        return [f"{key}: {reason}" for reason in proof["reasons"]]

    @staticmethod
    def _exits(recorded: list[dict]) -> list[str]:
        reasons = []
        for member in recorded:
            gone = exited(member)
            if gone is not True:
                reasons.append(f"pid {member['pid']} " + ("is still running" if gone is False
                                                          else "cannot be shown to have exited"))
        return reasons

    @staticmethod
    def _listening(ports: list[int], recorded: list[dict]) -> list[str]:
        return [f"port {port} is still held by a recorded process" for port in ports
                if any(native_process.same(member, native_process.identity(pid))
                       for pid in native_process.listeners(port) for member in recorded)]


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
        # The caller keeps no look to retry a held handle or an unsaved proof with, so leaving retries them here.
        # An exception from a close attempt, an interruption included, is raised after the retries.
        proof, raised = None, None
        for attempt in range(RELEASE_RETRIES + 1):
            try:   # the delay too: an interruption in it is kept, and the close still runs
                if attempt:
                    time.sleep(0.5)
            except BaseException as error:
                raised = raised or error
            try:
                proof = session_.close()
            except BaseException as error:
                raised = raised or error
            if session_.closed:
                break
        if raised is not None:
            raise raised
        if not proof["clean"]:
            message = "the desktop look is held: " + "; ".join(proof["reasons"])
            if failure is None:
                raise Refused(message)
            raise Refused(f"{failure}; {message}") from failure


def recover(folder: Path) -> list[str]:
    """Settle an earlier look's record. `[]` once its proof completed, or once a new boot or the end of its
    logon session proved its processes ended and its disposable folders are gone; otherwise why it is held."""

    folder = Path(folder).absolute()
    if not _plain(folder.parent, folder):   # a link would move every removal to wherever it leads
        return [f"{folder.name}: not a plain folder inside {folder.parent}; left in place"]
    record = load(folder)
    if record is None or record["state"] in FINAL:
        return []
    owner = record.get("owner")   # `None` once the look's own cleanup finished
    if owner is not None:
        gone = exited(owner)
        if gone is not True:   # this server's own live look included: never touched
            return [f"{folder.name}: its server is still running" if gone is False
                    else f"{folder.name}: its server cannot be shown to have exited"]
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
