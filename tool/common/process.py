"""The creation options that keep a background child from opening a window,
and the Windows job that ends a child's whole tree with it.

A console program started by a process that has no console — the detached
search daemon — gets a console of its own, and Windows shows it: Windows
Terminal opened for every `git` the daemon ran while indexing. Options and the
job only; the caller keeps spawning, pipes, cancellation and reaping.
"""

import os
import signal
import subprocess
import sys

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _Limits(ctypes.Structure):   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("times", ctypes.c_int64 * 2), ("flags", wintypes.DWORD), ("sets", ctypes.c_size_t * 2),
                    ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD), ("io", ctypes.c_uint64 * 6), ("memory", ctypes.c_size_t * 4)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _nt = ctypes.WinDLL("ntdll")
    _nt.NtResumeProcess.argtypes = (wintypes.HANDLE,)   # resumes every thread; `Popen` keeps no thread handle

# `creationflags` that start a child suspended on Windows, so it is in its job
# (`contained`) before its first instruction; `resumed` lets it run.
SUSPENDED = 0x4 if os.name == "nt" else 0


def background_options() -> dict:
    """`creationflags=CREATE_NO_WINDOW` on Windows, nothing elsewhere."""

    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def resumed(proc: subprocess.Popen) -> None:
    """Let a child started with `SUSPENDED` run; `OSError` when it cannot."""

    if os.name == "nt" and _nt.NtResumeProcess(int(proc._handle)):
        raise OSError(f"could not resume process {proc.pid}")


def contained(proc: subprocess.Popen):
    """A Windows job holding `proc` and all it starts; closing it, or this
    process dying, kills them all. `taskkill /T` cannot find a descendant
    whose parent already exited; the job can. None on failure.
    Start `proc` with `SUSPENDED` and resume it after this: a child it starts
    before the assignment stays outside the job.

    Elsewhere, `proc`'s process group when it leads one (`start_new_session`):
    its children stay in it after `proc` exits, so `killpg` still reaches them.
    ponytail: a child that starts its own session leaves the group; a cgroup
    would hold it, if that shows up."""

    if os.name != "nt":
        try:
            return proc.pid if os.getpgid(proc.pid) == proc.pid else None
        except OSError:
            return None
    job = _k32.CreateJobObjectW(None, None)
    limits = _Limits(flags=0x2000)   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if job and _k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) \
            and _k32.AssignProcessToJobObject(job, int(proc._handle)):
        return job
    if job:
        _k32.CloseHandle(job)
    return None


def killed(job) -> None:
    """Every process still in `job` killed; the job stays open for its owner."""

    if job and os.name == "nt":
        _k32.TerminateJobObject(job, 1)
    elif job:
        try:
            os.killpg(job, signal.SIGKILL)
        except OSError:   # the group is gone already
            pass


def terminated(job) -> None:
    """Every process still in `job`, orphaned or not, killed; the job closed."""

    if job:
        killed(job)
        if os.name == "nt":
            _k32.CloseHandle(job)
