"""A Windows job a look owns from before its first process: the process is created inside it, every member
is followed through the job's completion port, and ending it yields a proof that reads only definite
answers (docs/research/oneshot-desktop-session.md, "The launcher 9d2 needs")."""

import ctypes
import os
import subprocess
import time
import uuid
from ctypes import wintypes as w

import native_process

JOB_LIST, HANDLE_LIST = 0x0002000D, 0x00020002   # PROC_THREAD_ATTRIBUTE_JOB_LIST, _HANDLE_LIST
NEW, EXIT, ABNORMAL = 6, 7, 8   # JOB_OBJECT_MSG_NEW_PROCESS, _EXIT_PROCESS, _ABNORMAL_EXIT_PROCESS
OPEN = 0x1000 | 0x00100000 | 0x0001   # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE | PROCESS_TERMINATE
ELEVATION_REQUIRED = 740


class Refused(ValueError):
    """The process could not be started inside its job; nothing was driven."""


class _Limits(ctypes.Structure):   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("times", ctypes.c_int64 * 2), ("flags", w.DWORD), ("sets", ctypes.c_size_t * 2),
                ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                ("scheduling", w.DWORD), ("io", ctypes.c_uint64 * 6), ("memory", ctypes.c_size_t * 4)]


class _Port(ctypes.Structure):   # JOBOBJECT_ASSOCIATE_COMPLETION_PORT
    _fields_ = [("key", w.LPVOID), ("port", w.HANDLE)]


class _Accounting(ctypes.Structure):   # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
    _fields_ = [("times", ctypes.c_int64 * 4), ("faults", w.DWORD), ("total", w.DWORD), ("active", w.DWORD),
                ("terminated", w.DWORD)]


class _Startup(ctypes.Structure):   # STARTUPINFOEXW
    _fields_ = [("size", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
                ("x", w.DWORD), ("y", w.DWORD), ("width", w.DWORD), ("height", w.DWORD),
                ("chars_x", w.DWORD), ("chars_y", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", w.LPVOID),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE), ("attributes", w.LPVOID)]


class _Info(ctypes.Structure):   # PROCESS_INFORMATION
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


def _kernel():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes, k.CreateJobObjectW.restype = (w.LPVOID, w.LPCWSTR), w.HANDLE
    k.SetInformationJobObject.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
    k.QueryInformationJobObject.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID)
    k.TerminateJobObject.argtypes = (w.HANDLE, w.UINT)
    k.IsProcessInJob.argtypes = (w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL))
    k.CreateIoCompletionPort.argtypes = (w.HANDLE, w.HANDLE, ctypes.c_size_t, w.DWORD)
    k.CreateIoCompletionPort.restype = w.HANDLE
    k.GetQueuedCompletionStatus.argtypes = (w.HANDLE, ctypes.POINTER(w.DWORD), ctypes.POINTER(ctypes.c_size_t),
                                            ctypes.POINTER(ctypes.c_void_p), w.DWORD)
    k.InitializeProcThreadAttributeList.argtypes = (w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t))
    k.UpdateProcThreadAttribute.argtypes = (w.LPVOID, w.DWORD, ctypes.c_size_t, w.LPVOID, ctypes.c_size_t,
                                            w.LPVOID, w.LPVOID)
    k.DeleteProcThreadAttributeList.argtypes = (w.LPVOID,)
    k.CreateProcessW.argtypes = (w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR,
                                 ctypes.POINTER(_Startup), ctypes.POINTER(_Info))
    k.ResumeThread.argtypes, k.ResumeThread.restype = (w.HANDLE,), w.DWORD
    k.TerminateProcess.argtypes = (w.HANDLE, w.UINT)
    k.GetCurrentProcess.restype = w.HANDLE
    k.DuplicateHandle.argtypes = (w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD)
    k.OpenProcess.argtypes, k.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    k.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    k.CloseHandle.argtypes = (w.HANDLE,)
    k.GetTickCount64.restype = ctypes.c_uint64
    return k


class Job:
    """One owned job. `start` creates its process inside it; `members` reads who joined; `end` ends every
    member and proves it. Members are kept as a list, not by pid: a pid an exited member left can join again
    as another process."""

    def __init__(self):
        self.k = _kernel()
        self.uptime = int(self.k.GetTickCount64())
        self.name = "Local\\wiki-oneshot-" + uuid.uuid4().hex
        self.job = self.k.CreateJobObjectW(None, self.name)   # no security attributes: not inheritable
        if not self.job:
            raise Refused(f"cannot create the job: {ctypes.get_last_error()}")
        self.port = self.k.CreateIoCompletionPort(w.HANDLE(-1), None, 0, 1)
        limits = _Limits(flags=0x2000)   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        port = _Port(key=None, port=self.port)
        if not self.port or not self.k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) \
                or not self.k.SetInformationJobObject(self.job, 7, ctypes.byref(port), ctypes.sizeof(port)):
            error = ctypes.get_last_error()
            self.close()
            raise Refused(f"cannot prepare the job: {error}")
        self.joined: list[dict] = []   # {"pid", "handle", "identity", "exited"}
        self.stray: list = []   # creation handles of roots whose rollback could not confirm their exit
        self.ended = False

    def _create(self, argv, cwd, env, desktop, handles):
        """`CreateProcessW` inside the job, suspended, inheriting only `handles` (its stdin and log), never
        another inheritable handle of this server. (`process`, `thread`, `pid`) or the Windows error."""

        size = ctypes.c_size_t()
        self.k.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        jobs = (w.HANDLE * 1)(self.job)
        inherited = (w.HANDLE * len(set(handles)))(*sorted(set(handles)))
        if not self.k.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
            return ctypes.get_last_error()
        try:
            if not self.k.UpdateProcThreadAttribute(attributes, 0, JOB_LIST, jobs, ctypes.sizeof(jobs), None, None) \
                    or not self.k.UpdateProcThreadAttribute(attributes, 0, HANDLE_LIST, inherited,
                                                            ctypes.sizeof(inherited), None, None):
                return ctypes.get_last_error()
            startup = _Startup(size=ctypes.sizeof(_Startup), desktop=desktop, flags=0x100, stdin=handles[0],
                               stdout=handles[1], stderr=handles[1], attributes=ctypes.cast(attributes, w.LPVOID))
            info = _Info()
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            block = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0")
            flags = 0x4 | 0x400 | 0x80000 | subprocess.CREATE_NO_WINDOW   # suspended, unicode env, extended
            if not self.k.CreateProcessW(argv[0], command, None, None, True, flags, block, str(cwd),
                                         ctypes.byref(startup), ctypes.byref(info)):
                return ctypes.get_last_error()
            return info.process, info.thread, info.pid
        finally:
            self.k.DeleteProcThreadAttributeList(attributes)

    def start(self, argv: list[str], cwd, env: dict, desktop: str | None, log, before_resume=None) -> dict:
        """Start `argv` inside the job on `desktop`, its output to `log`. `before_resume` gets the root's
        identity while it is still suspended, so a caller can record it before it runs. The root's identity."""

        import msvcrt

        # The child inherits launcher-owned duplicates; the caller's handles and their flags are never touched.
        here, copies, created = self.k.GetCurrentProcess(), [], None
        try:
            try:
                with open(os.devnull, "rb") as null:
                    for file in (null, log):
                        copy = w.HANDLE()
                        if not self.k.DuplicateHandle(here, msvcrt.get_osfhandle(file.fileno()), here,
                                                      ctypes.byref(copy), 0, True, 2):   # inheritable, same access
                            raise Refused(f"cannot hand {argv[0]} its input and log: {ctypes.get_last_error()}")
                        copies.append(copy.value)
                    created = self._create(argv, cwd, env, desktop, copies)
            finally:
                for copy in copies:
                    self.k.CloseHandle(copy)
        except BaseException:
            if isinstance(created, tuple):   # created, then the cleanup around it failed: owned and rolled back
                self._rollback(created[0], created[1])
            raise
        if not isinstance(created, tuple):
            if created == ELEVATION_REQUIRED:
                raise Refused("the app asks for elevation; it is not retried through the shell")
            raise Refused(f"cannot start {argv[0]} inside its job: Windows error {created}")
        process, thread, pid = created
        try:
            root = native_process.identity(pid, process)
            if root is None:
                raise Refused(f"cannot read the identity of {argv[0]} before it runs")
            if before_resume:
                before_resume(root)
            if self.k.ResumeThread(thread) == 0xFFFFFFFF:
                raise Refused(f"cannot resume {argv[0]}: {ctypes.get_last_error()}")
        except BaseException:
            self._rollback(process, thread)   # never left suspended, never run unrecorded
            raise
        self.k.CloseHandle(thread)
        self.k.CloseHandle(process)
        return root

    def _rollback(self, process, thread) -> None:
        """End a root that must not run, through its creation handle. A root whose exit is confirmed within five
        seconds releases its handles; one that is not keeps its process handle in `stray`, which `end` waits on
        and reports, so the failure is never lost."""

        self.k.TerminateProcess(process, 1)
        self.k.CloseHandle(thread)
        if self.k.WaitForSingleObject(process, 5000) == 0:
            self.k.CloseHandle(process)
        else:
            self.stray.append(process)

    def _messages(self, wait_ms: int, most: int = 64):
        """Up to `most` messages the port holds now, waiting up to `wait_ms` for the first: (message, pid).
        The bound keeps a backlog from holding `end` past its deadline."""

        found = []
        while len(found) < most:
            message, key, pid = w.DWORD(), ctypes.c_size_t(), ctypes.c_void_p()
            if not self.k.GetQueuedCompletionStatus(self.port, ctypes.byref(message), ctypes.byref(key),
                                                    ctypes.byref(pid), wait_ms):
                return found
            found.append((message.value, pid.value or 0))
            wait_ms = 0
        return found

    def _pump(self, wait_ms: int = 0) -> int:
        """Handle one bounded batch of messages; how many there were."""

        found = self._messages(wait_ms)
        for message, pid in found:
            if message == NEW:
                entry = {"pid": pid, "handle": None, "identity": None, "exited": False}
                handle = self.k.OpenProcess(OPEN, False, pid)
                inside = w.BOOL()
                if handle and self.k.IsProcessInJob(handle, self.job, ctypes.byref(inside)) and inside:
                    entry.update(handle=handle, identity=native_process.identity(pid, handle))
                elif handle:
                    self.k.CloseHandle(handle)   # the pid is another process's now: never held
                self.joined.append(entry)
            elif message in (EXIT, ABNORMAL):
                entry = next((e for e in self.joined if e["pid"] == pid and not e["exited"]), None)
                if entry:
                    entry["exited"] = True
        return len(found)

    def members(self) -> list[dict]:
        """The identities of every member that joined and could be held, alive or not: every message ready
        now is handled first, batch after batch."""

        while self._pump() == 64:
            pass
        return [entry["identity"] for entry in self.joined if entry["identity"]]

    def _gone(self, entry: dict) -> bool:
        """A member has exited. A held member only by its signalled handle, which is bound to that process; an
        exit message names a pid, which a later member may carry. A member never held, by its exit message."""

        if entry["handle"]:
            return self.k.WaitForSingleObject(entry["handle"], 0) == 0
        return entry["exited"]

    def _count(self):
        """The job's accounting, or the Windows error that kept it from being read."""

        accounting = _Accounting()
        if self.k.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            return accounting
        return ctypes.get_last_error()

    def end(self, wait: float = 30.0) -> dict:
        """End every member and prove it: termination succeeded, each member that joined exited, and the job's
        cumulative process count equals the members that joined, since Windows does not guarantee the port's
        messages. Messages that arrive late are waited for until `wait` runs out. `ActiveProcesses` is
        reported, never trusted."""

        terminated = self.k.TerminateJobObject(self.job, 1)
        failure = None if terminated else ctypes.get_last_error()
        self.ended = True
        deadline = time.monotonic() + wait
        while True:
            count = self._count()
            if all(self._gone(entry) for entry in self.joined) and not self._strays() \
                    and not isinstance(count, int) and count.total == len(self.joined):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._pump(max(1, min(100, int(remaining * 1000))))
        accounting = self._count()
        read = not isinstance(accounting, int)
        left = [entry["identity"] or {"pid": entry["pid"]} for entry in self.joined if not self._gone(entry)]
        reasons = [f"no exit proven for pid {member['pid']}" for member in left]
        strays = self._strays()
        if strays:
            reasons.append(f"{strays} root(s) refused at start not proven ended")
        if not terminated:
            reasons.append(f"cannot terminate the job: Windows error {failure}")
        if not read:
            reasons.append(f"cannot read the job's count: Windows error {accounting}")
        elif accounting.total != len(self.joined):
            reasons.append(f"the job counted {accounting.total} processes; {len(self.joined)} were followed")
        return {"clean": not reasons, "reasons": reasons, "joined": len(self.joined),
                "total": accounting.total if read else None, "active": accounting.active if read else None,
                "members": [entry["identity"] for entry in self.joined if entry["identity"]]}

    def _strays(self) -> int:
        """How many roots refused at start are still not proven ended."""

        return sum(1 for handle in self.stray if self.k.WaitForSingleObject(handle, 0) != 0)

    def close(self) -> None:
        """Close every handle the job holds. Closing the job's last handle kills what is still in it."""

        for handle in getattr(self, "stray", []):
            self.k.CloseHandle(handle)
        self.stray = []
        for entry in getattr(self, "joined", []):
            if entry["handle"]:
                self.k.CloseHandle(entry["handle"])
                entry["handle"] = None
        for name in ("port", "job"):
            if getattr(self, name, None):
                self.k.CloseHandle(getattr(self, name))
                setattr(self, name, None)
