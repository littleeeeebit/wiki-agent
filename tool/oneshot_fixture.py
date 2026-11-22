"""The owner's desktop round's fixture (plan 9e5, docs/plans/oneshot/9-kinds.md "The owner's desktop round"): the
server's own window, never the build's, showing the page of English words the server drew for one check. It is
topmost and shown by Windows without activation, never through Tk's `deiconify`, which asks for focus. It paints
its whole client area (`--rect`'s WIDTH by HEIGHT) opaque, so a surface read of that area reads only the round's
own pixels beneath whatever the app draws over it. Along the client area's edges it paints the server's marks
for this check (`--marks`: one `x y width height rrggbb` per line, in client pixels), which the capture helper
finds in the very frame it sampled, or keeps nothing.

Once shown, it gives itself a window region of exactly its client area. To keep the page steady for the reading,
its own window procedure turns requests to move, resize, restack, hide, retitle, restyle, minimize, maximize or
close it into none, and puts back a minimize, move, hide or lost topmost flag that came without such a request.
Its order among other topmost windows is not kept; the marks cover that. Then it writes its frame's handle and
its client area's rectangle on screen, in physical pixels, to `--out`, and runs until the round ends its job.

    python oneshot_fixture.py --title oneshot-fixture --words <file> --marks <file> --rect X Y WIDTH HEIGHT --out <file>
"""

import argparse
import ctypes
import json
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

GWL_EXSTYLE, GWLP_WNDPROC, WS_EX_NOACTIVATE = -20, -4, 0x08000000
WM_SETTEXT, WM_CLOSE, WM_WINDOWPOSCHANGING, WM_STYLECHANGING, WM_SYSCOMMAND = 0x000C, 0x0010, 0x0046, 0x007C, 0x0112
WM_WINDOWPOSCHANGED, SW_SHOWNOACTIVATE, HWND_TOPMOST, WS_EX_TOPMOST = 0x0047, 4, -1, 0x8
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_HIDEWINDOW = 0x1, 0x2, 0x4, 0x10, 0x40, 0x80
SWP_NOSENDCHANGING = 0x400
SC_SIZE, SC_MOVE, SC_MINIMIZE, SC_MAXIMIZE, SC_CLOSE = 0xF000, 0xF010, 0xF020, 0xF030, 0xF060
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("after", wintypes.HWND), ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", ctypes.c_uint)]


class STYLESTRUCT(ctypes.Structure):
    _fields_ = [("old", wintypes.DWORD), ("new", wintypes.DWORD)]


def guarded(user32, frame: int):
    """The frame's window procedure wrapped so that it keeps its place, size, topmost flag, visibility, title and
    styles: each request for a change is turned into none. What comes without a request (a minimize by another
    process's `ShowWindow`, a move, hide or lost topmost flag sent with `SWP_NOSENDCHANGING`) is put back as soon
    as Windows reports it, without activation; until then the helper finds the fixture changed or its marks
    missing. Its order among other topmost windows is not kept. The callback, which must outlive the window."""

    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
    placed = wintypes.RECT()
    user32.GetWindowRect(frame, ctypes.byref(placed))
    before = []

    def undone(hwnd) -> None:
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
            return
        box = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(box))
        moved = (box.left, box.top, box.right, box.bottom) != (placed.left, placed.top, placed.right, placed.bottom)
        if moved or not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST:   # past its own guard
            user32.SetWindowPos(hwnd, HWND_TOPMOST, placed.left, placed.top, placed.right - placed.left,
                                placed.bottom - placed.top, SWP_NOACTIVATE | SWP_NOSENDCHANGING)

    def proc(hwnd, msg, wparam, lparam):
        if msg == WM_WINDOWPOSCHANGING:
            pos = WINDOWPOS.from_address(lparam)
            pos.flags = (pos.flags | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER) & ~SWP_HIDEWINDOW
        elif msg == WM_STYLECHANGING:
            style = STYLESTRUCT.from_address(lparam)
            style.new = style.old
        elif msg in (WM_SETTEXT, WM_CLOSE) or (msg == WM_SYSCOMMAND and wparam & 0xFFF0 in
                                                (SC_SIZE, SC_MOVE, SC_MINIMIZE, SC_MAXIMIZE, SC_CLOSE)):
            return 0
        done = user32.CallWindowProcW(before[0], hwnd, msg, wparam, lparam)
        if msg == WM_WINDOWPOSCHANGED:
            undone(hwnd)
        return done

    callback = WNDPROC(proc)
    before.append(user32.SetWindowLongPtrW(frame, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p)))
    return callback


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--words", type=Path, required=True)
    parser.add_argument("--marks", type=Path, required=True)
    parser.add_argument("--rect", type=int, nargs=4, required=True, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor v2: the rect is physical pixels
    words = args.words.read_text(encoding="utf-8").split()
    x, y, width, height = args.rect
    root = tk.Tk()
    root.withdraw()   # styled before it is ever shown
    root.title(args.title)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(False, False)
    root.configure(bg="white")
    page = tk.Canvas(root, width=width, height=height, bg="white", highlightthickness=0, bd=0)
    page.pack(expand=True, fill="both")
    marks = [line.split() for line in args.marks.read_text(encoding="utf-8").splitlines() if line.strip()]
    for mx, my, mw, mh, color in marks:
        mx, my, mw, mh = int(mx), int(my), int(mw), int(mh)
        page.create_rectangle(mx, my, mx + mw, my + mh, fill=f"#{color}", outline="")
    band = max((int(m[3]) for m in marks if int(m[1]) == 0), default=0)   # the top edge's marks
    page.create_text(24 + band, 24 + band, text=" ".join(words), anchor="nw", width=width - 48 - 2 * band,
                     font=("Segoe UI", 28), fill="black")
    root.update_idletasks()
    frame = user32.GetParent(root.winfo_id())   # Tk's toplevel frame, the window others find
    user32.SetWindowLongW(frame, GWL_EXSTYLE, user32.GetWindowLongW(frame, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
    # Shown topmost by Windows directly, never by Tk's `deiconify` or `-topmost`, which raise it and ask for focus.
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos(frame, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    root.update()
    keep = []   # the guard's callback lives as long as the window

    def named() -> None:
        box, origin, placed = wintypes.RECT(), wintypes.POINT(), wintypes.RECT()   # the client area, all of it
        user32.GetClientRect(frame, ctypes.byref(box))    # painted, never the frame's borders, which DWM draws
        user32.ClientToScreen(frame, ctypes.byref(origin))   # partly invisible
        user32.GetWindowRect(frame, ctypes.byref(placed))
        # The window's region is exactly its client area, in window coordinates: a shape Windows reports as one
        # rectangle with those bounds, which the helper requires at each check.
        left, top = origin.x - placed.left, origin.y - placed.top
        gdi32.CreateRectRgn.restype = ctypes.c_void_p
        user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
        if not user32.SetWindowRgn(frame, gdi32.CreateRectRgn(left, top, left + box.right, top + box.bottom), True):
            raise SystemExit("the fixture could not take its shape")
        keep.append(guarded(user32, frame))   # placed, shaped, styled and titled: from here on it refuses changes
        part = args.out.with_suffix(".part")
        part.write_text(json.dumps({"hwnd": frame, "rect": [origin.x, origin.y, box.right, box.bottom]}),
                        encoding="utf-8")
        part.replace(args.out)

    root.after(200, named)   # drawn first: the round reads nothing before the page is on screen
    root.mainloop()


if __name__ == "__main__":
    main()
