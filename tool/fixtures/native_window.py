"""Disposable real Win32 window for native collector acceptance."""

import ctypes
import json
import os
import sys
from ctypes import wintypes as w
from pathlib import Path

u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
u.DefWindowProcW.argtypes, u.DefWindowProcW.restype = (w.HWND, w.UINT, w.WPARAM, w.LPARAM), ctypes.c_ssize_t
u.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
u.CreateWindowExW.restype = w.HWND
u.GetDlgItem.argtypes, u.GetDlgItem.restype = (w.HWND, ctypes.c_int), w.HWND
u.SetWindowTextW.argtypes = (w.HWND, w.LPCWSTR)
u.ShowWindow.argtypes = (w.HWND, ctypes.c_int)
u.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
u.DispatchMessageW.argtypes, u.DispatchMessageW.restype = (ctypes.POINTER(w.MSG),), ctypes.c_ssize_t
k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = (w.LPCWSTR,), w.HMODULE


@callback
def window_proc(hwnd, message, wp, lp):
    if message == 0x0007 and "--wrong-focus" in sys.argv:
        other = getattr(window_proc, "other", None)
        if other:
            u.SetActiveWindow(other)
            u.SetFocus(other)
        return 0
    if message == 0x0111 and wp & 0xFFFF == 1:
        u.SetWindowTextW(u.GetDlgItem(hwnd, 2), "Saved")
        with Path(os.environ["WIKI_NATIVE_EVENTS"]).open("a", encoding="utf-8") as file:
            file.write(json.dumps({"pid": os.getpid(), "hwnd": int(hwnd), "control_id": 1,
                                   "event": "WM_COMMAND/save"}) + "\n")
        return 0
    if message == 0x0002:
        u.PostQuitMessage(0)
        return 0
    return u.DefWindowProcW(hwnd, message, wp, lp)


class WindowClass(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("proc", callback), ("class_extra", ctypes.c_int), ("window_extra", ctypes.c_int),
                ("instance", w.HINSTANCE), ("icon", w.HICON), ("cursor", w.HANDLE), ("background", w.HBRUSH),
                ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]


u.RegisterClassW.argtypes = (ctypes.POINTER(WindowClass),)
u.SetActiveWindow.argtypes, u.SetActiveWindow.restype = (w.HWND,), w.HWND
u.SetFocus.argtypes, u.SetFocus.restype = (w.HWND,), w.HWND
instance = k.GetModuleHandleW(None)
wc = WindowClass(proc=window_proc, instance=instance, background=6, name="WikiVerificationFixture")
if not u.RegisterClassW(ctypes.byref(wc)):
    raise ctypes.WinError(ctypes.get_last_error())
hwnd = u.CreateWindowExW(0, wc.name, "Disposable native verification", 0x00CF0000,
                          100, 100, 360, 200, None, None, instance, None)
if not hwnd:
    raise ctypes.WinError(ctypes.get_last_error())
u.CreateWindowExW(0, "BUTTON", "Save", 0x50010000, 20, 20, 100, 35, hwnd, 1, instance, None)
u.CreateWindowExW(0, "STATIC", "Unsaved", 0x50000000, 20, 70, 200, 30, hwnd, 2, instance, None)
if "--wrong-focus" in sys.argv:
    # Prepare the negative target before exposing the main window to the collector.
    other = u.CreateWindowExW(0, "STATIC", "Other owned test window", 0x00CF0000,
                              200, 200, 200, 100, None, None, instance, None)
    if not other:
        raise ctypes.WinError(ctypes.get_last_error())
    window_proc.other = other
    u.ShowWindow(other, 5)
u.ShowWindow(hwnd, 5)
msg = w.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg))
    u.DispatchMessageW(ctypes.byref(msg))
