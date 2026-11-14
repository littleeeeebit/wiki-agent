"""Launch a process tree on an owned Windows desktop and reap it on every exit."""

import ctypes
import os
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

from common import process


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
