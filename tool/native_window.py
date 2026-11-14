"""Observe an owned process image and its windows on the collector's desktop."""

import ctypes
import hashlib
import json
import sys
import socket
from ctypes import wintypes as w
from pathlib import Path


def listener_owner(port, ancestor):
    """A CDP listener must belong to this launched app's descendants."""
    ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
    ip.GetExtendedTcpTable.argtypes = (w.LPVOID, ctypes.POINTER(w.DWORD), w.BOOL, w.ULONG, ctypes.c_int, w.ULONG)
    size = w.DWORD()
    ip.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 5, 0)
    table = ctypes.create_string_buffer(size.value)
    if ip.GetExtendedTcpTable(table, ctypes.byref(size), False, 2, 5, 0):
        raise ValueError("Cannot observe the native debugging listener")
    values = ctypes.cast(table, ctypes.POINTER(w.DWORD))
    owners = [int(values[1 + i * 6 + 5]) for i in range(values[0])
              if values[1 + i * 6] == 2 and socket.ntohs(values[1 + i * 6 + 2] & 0xffff) == port
              and values[1 + i * 6 + 1] == int.from_bytes(socket.inet_aton("127.0.0.1"), "little")]
    if len(owners) != 1:
        raise ValueError("Native debugging listener is unavailable or ambiguous")

    class Entry(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD), ("heap", ctypes.c_size_t),
                    ("module", w.DWORD), ("threads", w.DWORD), ("parent", w.DWORD), ("priority", w.LONG),
                    ("flags", w.DWORD), ("exe", w.WCHAR * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes, k.CreateToolhelp32Snapshot.restype = (w.DWORD, w.DWORD), w.HANDLE
    k.Process32FirstW.argtypes, k.Process32NextW.argtypes = (w.HANDLE, ctypes.POINTER(Entry)), (w.HANDLE, ctypes.POINTER(Entry))
    k.CloseHandle.argtypes = (w.HANDLE,)
    snapshot, parents = k.CreateToolhelp32Snapshot(2, 0), {}
    try:
        entry = Entry(size=ctypes.sizeof(Entry))
        available = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            parents[entry.pid] = entry.parent
            available = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    cursor, seen = owners[0], set()
    while cursor != ancestor:
        if cursor in seen or cursor not in parents:
            raise ValueError("Debugging listener belongs to another process tree")
        seen.add(cursor)
        cursor = parents[cursor]
    return owners[0]


def observe(pid, executable, expected_hash, desktop, port):
    u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    k.CloseHandle.argtypes = (w.HANDLE,)
    k.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = (w.DWORD,), w.HANDLE
    u.GetUserObjectInformationW.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))
    name, needed = ctypes.create_unicode_buffer(256), w.DWORD()
    if not u.GetUserObjectInformationW(u.GetThreadDesktop(k.GetCurrentThreadId()), 2, name, ctypes.sizeof(name), ctypes.byref(needed)) \
            or name.value != desktop:
        raise ValueError("Native observation left the attempt-owned desktop")
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ValueError("Owned native process is no longer running")
    try:
        image, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        if not k.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)) \
                or Path(image.value).resolve() != Path(executable).resolve() \
                or hashlib.sha256(Path(image.value).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Observed native process image differs from approval")
    finally:
        k.CloseHandle(handle)
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    u.EnumWindows.argtypes = (callback, w.LPARAM)
    u.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
    u.IsWindowVisible.argtypes = (w.HWND,)
    u.GetWindowRect.argtypes = (w.HWND, ctypes.POINTER(w.RECT))
    u.GetDpiForWindow.argtypes, u.GetDpiForWindow.restype = (w.HWND,), w.UINT
    windows = []

    @callback
    def visit(hwnd, _):
        owner, rect = w.DWORD(), w.RECT()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd) and u.GetWindowRect(hwnd, ctypes.byref(rect)) \
                and rect.right - rect.left >= 200 and rect.bottom - rect.top >= 100:
            windows.append({"hwnd": int(hwnd), "geometry": [rect.left, rect.top, rect.right, rect.bottom],
                            "dpi": u.GetDpiForWindow(hwnd)})
        return True

    u.EnumWindows(visit, 0)
    if not windows:
        raise ValueError("Owned native application has no visible window")
    windows.sort(key=lambda row: (row["geometry"][2] - row["geometry"][0]) * (row["geometry"][3] - row["geometry"][1]), reverse=True)
    return {"pid": pid, "windows": windows, "listener_pid": listener_owner(int(port), pid)}


if __name__ == "__main__":
    if sys.argv[1] == "listener":
        print(json.dumps({"pid": listener_owner(int(sys.argv[2]), int(sys.argv[3]))}))
    else:
        print(json.dumps(observe(int(sys.argv[1]), *sys.argv[2:])))
