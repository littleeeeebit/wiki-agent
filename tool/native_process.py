"""Launch a process tree on an owned Windows desktop and reap it on every exit; read a process's stable
identity and the listeners of a local port, so cleanup acts only on what it recorded."""

import ctypes
import os
import socket
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

from common import process

QUERY, TERMINATE, SYNCHRONIZE = 0x1000, 0x0001, 0x00100000   # PROCESS_QUERY_LIMITED_INFORMATION, …


def _kernel():
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    k.CloseHandle.argtypes = (w.HANDLE,)
    k.GetProcessTimes.argtypes = (w.HANDLE, *[ctypes.POINTER(w.FILETIME)] * 4)
    k.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
    k.GetExitCodeProcess.argtypes = (w.HANDLE, ctypes.POINTER(w.DWORD))
    k.TerminateProcess.argtypes = (w.HANDLE, w.UINT)
    k.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    k.CreateToolhelp32Snapshot.argtypes, k.CreateToolhelp32Snapshot.restype = (w.DWORD, w.DWORD), w.HANDLE
    return k, w


def parents() -> dict[int, int]:
    """Every live process's parent pid as Windows recorded it at creation. A parent pid may since belong to
    another process: compare creation times before trusting it."""

    k, w = _kernel()

    class Entry(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD), ("heap", ctypes.c_size_t),
                    ("module", w.DWORD), ("threads", w.DWORD), ("parent", w.DWORD), ("priority", w.LONG),
                    ("flags", w.DWORD), ("exe", w.WCHAR * 260)]

    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = (w.HANDLE, ctypes.POINTER(Entry))
    snapshot, found = k.CreateToolhelp32Snapshot(2, 0), {}
    try:
        entry = Entry(size=ctypes.sizeof(Entry))
        more = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            found[int(entry.pid)] = int(entry.parent)
            more = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return found


def _described(k, w, handle, pid: int, tree: dict[int, int] | None = None) -> dict | None:
    times = [w.FILETIME() for _ in range(4)]
    image, size, code = ctypes.create_unicode_buffer(32768), w.DWORD(32768), w.DWORD()
    if not k.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]) \
            or not k.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)) \
            or not k.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
        return None
    created = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
    return {"pid": pid, "created": created, "exe": os.path.normcase(image.value),
            "parent": (parents() if tree is None else tree).get(pid, 0)}


def identity(pid: int, handle=None, tree: dict[int, int] | None = None) -> dict | None:
    """A live process's stable identity: pid, creation time (FILETIME ticks), image path (case-folded) and
    the parent pid recorded at its creation. `None` once it has exited or cannot be read. `handle` reads a
    process this one started, whose handle it holds; `tree`, a `parents()` snapshot already taken, saves a
    caller that walks several processes a snapshot per process."""

    k, w = _kernel()
    if handle is not None:
        return _described(k, w, int(handle), pid, tree)
    opened = k.OpenProcess(QUERY, False, pid)
    if not opened:
        return None
    try:
        return _described(k, w, opened, pid, tree)
    finally:
        k.CloseHandle(opened)


def same(recorded: dict | None, now: dict | None) -> bool:
    """`now` is the process `recorded` names: the same pid, creation time and image. A reused pid is not."""

    return bool(recorded and now) and all(recorded[key] == now[key] for key in ("pid", "created", "exe"))


def terminate(recorded: dict, wait: float = 10) -> bool:
    """Kill the process `recorded` names, checked on the very handle that kills it, so a pid that now
    belongs to another process is never signalled. `True` when it is gone; `False` when it would not die."""

    k, _ = _kernel()
    handle = k.OpenProcess(QUERY | TERMINATE | SYNCHRONIZE, False, recorded["pid"])
    if not handle:
        return identity(recorded["pid"]) is None or not same(recorded, identity(recorded["pid"]))
    try:
        if not same(recorded, identity(recorded["pid"], handle)):
            return True   # exited, or the pid is another process's now: that one is left running
        k.TerminateProcess(handle, 1)
        return k.WaitForSingleObject(handle, int(wait * 1000)) == 0
    finally:
        k.CloseHandle(handle)


def _sockets(kind: int) -> list[tuple[int, int, int, int]]:
    """Every TCP socket of a table class, IPv4 and IPv6: (state, local port, remote port, owning pid)."""

    _k, w = _kernel()
    ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
    ip.GetExtendedTcpTable.argtypes = (w.LPVOID, ctypes.POINTER(w.DWORD), w.BOOL, w.ULONG, ctypes.c_int, w.ULONG)
    found = []
    # (address family, DWORDs per row, index of the state, local port, remote port and owning pid)
    for family, width, state, here, there, owner in ((socket.AF_INET, 6, 0, 2, 4, 5), (23, 14, 12, 5, 11, 13)):
        size = w.DWORD()
        ip.GetExtendedTcpTable(None, ctypes.byref(size), False, family, kind, 0)
        table = ctypes.create_string_buffer(size.value + 4096)
        size = w.DWORD(len(table))
        if ip.GetExtendedTcpTable(table, ctypes.byref(size), False, family, kind, 0):
            raise ValueError("Cannot read the TCP table")
        values = ctypes.cast(table, ctypes.POINTER(w.DWORD))
        for row in range(values[0]):
            cells = [values[1 + row * width + i] for i in range(width)]
            found.append((int(cells[state]), socket.ntohs(cells[here] & 0xFFFF), socket.ntohs(cells[there] & 0xFFFF),
                          int(cells[owner])))
    return found


def listeners(port: int) -> set[int]:
    """The pids holding a listening TCP socket on `port`, on any local address, IPv4 or IPv6."""

    return {pid for state, here, _, pid in _sockets(3) if state == 2 and here == port}   # OWNER_PID_LISTENER


def peer(port: int, client: int) -> set[int]:
    """The pids holding the accepted end of a loopback connection from local port `client` to `port`: who
    actually answers a socket this process connected."""

    return {pid for state, here, there, pid in _sockets(5) if state != 2 and here == port and there == client}


@contextmanager
def launched(argv, root, env, desktop, log):
    from ctypes import wintypes as w
    import msvcrt

    class Startup(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
                    ("x", w.DWORD), ("y", w.DWORD), ("width", w.DWORD), ("height", w.DWORD),
                    ("chars_x", w.DWORD), ("chars_y", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                    ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", w.LPVOID),
                    ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]

    class Info(ctypes.Structure):
        _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateProcessW.argtypes = (w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD,
                                w.LPVOID, w.LPCWSTR, ctypes.POINTER(Startup), ctypes.POINTER(Info))
    k.CreateProcessW.restype = w.BOOL
    k.CloseHandle.argtypes = (w.HANDLE,)
    k.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    k.GetExitCodeProcess.argtypes = (w.HANDLE, ctypes.POINTER(w.DWORD))
    k.TerminateProcess.argtypes = (w.HANDLE, w.UINT)
    info, job = Info(), None
    with open(os.devnull, "rb") as null:
        handles = [msvcrt.get_osfhandle(file.fileno()) for file in (null, log)]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startup = Startup(size=ctypes.sizeof(Startup), desktop=desktop, flags=0x100,
                          stdin=handles[0], stdout=handles[1], stderr=handles[1])
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        environment = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0")
        try:
            created = k.CreateProcessW(argv[0], command, None, None, True,
                                       subprocess.CREATE_NO_WINDOW | process.SUSPENDED | 0x400,
                                       environment, str(root), ctypes.byref(startup), ctypes.byref(info))
        finally:
            for handle in handles:
                os.set_handle_inheritable(handle, False)
        if not created:
            raise ctypes.WinError(ctypes.get_last_error())
    k.CloseHandle(info.thread)

    def poll():
        if k.WaitForSingleObject(info.process, 0) == 0x102:
            return None
        code = w.DWORD()
        if not k.GetExitCodeProcess(info.process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def wait(timeout=5):
        if k.WaitForSingleObject(info.process, int(timeout * 1000)) != 0:
            raise ValueError("Owned native process did not finish")
        return poll()

    child = SimpleNamespace(pid=info.pid, _handle=info.process, poll=poll, wait=wait,
                            kill=lambda: k.TerminateProcess(info.process, 1))
    try:
        job = process.contained(child)
        if not job:
            raise ValueError("Cannot contain the native process tree")
        process.resumed(child)
        yield child
    finally:
        if job:
            process.terminated(job)
        elif poll() is None:
            child.kill()
        try:
            wait()
        finally:
            k.CloseHandle(info.process)
