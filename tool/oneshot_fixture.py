"""The owner's desktop round's fixture (plan 9e5, docs/plans/oneshot/9-kinds.md "The owner's desktop round"): the
server's own window, never the build's, showing the page of English words the server drew for one check. It is
opaque and topmost over its whole frame, so a surface read of that frame reads only the round's own pixels
beneath whatever the app draws over it, and it never takes activation. Along its client area's edges it paints
the server's marks for this check (`--marks`: one `x y width height rrggbb` per line, in client pixels), which
the capture helper finds in the very frame it sampled, or keeps nothing.

Once shown, it refuses in its own window procedure whatever would make it another source: a move, a resize, a
change of z-order, a hide, a new title or style, a minimize, maximize or close. Then it writes its frame's
handle and rectangle, in physical pixels, to `--out`, and runs until the round ends its job.

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
WM_WINDOWPOSCHANGED, SW_SHOWNOACTIVATE = 0x0047, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_HIDEWINDOW = 0x1, 0x2, 0x4, 0x80
SC_SIZE, SC_MOVE, SC_MINIMIZE, SC_MAXIMIZE, SC_CLOSE = 0xF000, 0xF010, 0xF020, 0xF030, 0xF060
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("after", wintypes.HWND), ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", ctypes.c_uint)]


class STYLESTRUCT(ctypes.Structure):
    _fields_ = [("old", wintypes.DWORD), ("new", wintypes.DWORD)]


def guarded(user32, frame: int):
    """The frame's window procedure wrapped so that it keeps its place, size, z-order, visibility, title and
    styles: each request for a change is turned into none. A minimize, which another process's `ShowWindow` makes
    without asking, is undone at once without activation; until then the helper finds it minimized and its marks
    missing. The callback, which must outlive the window."""

    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    before = []

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
        if msg == WM_WINDOWPOSCHANGED and user32.IsIconic(hwnd):   # its size kept, so no WM_SIZE says so
            user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
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
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor v2: the rect is physical pixels
    words = args.words.read_text(encoding="utf-8").split()
    x, y, width, height = args.rect
    root = tk.Tk()
    root.withdraw()   # styled before it is ever shown
    root.title(args.title)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(False, False)
    root.attributes("-topmost", True)
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
    root.deiconify()
    root.update()
    keep = []   # the guard's callback lives as long as the window

    def named() -> None:
        keep.append(guarded(user32, frame))   # placed, styled and titled: from here on it refuses every change
        box = wintypes.RECT()
        user32.GetWindowRect(frame, ctypes.byref(box))
        part = args.out.with_suffix(".part")
        part.write_text(json.dumps({"hwnd": frame, "rect": [box.left, box.top, box.right - box.left,
                                                            box.bottom - box.top]}), encoding="utf-8")
        part.replace(args.out)

    root.after(200, named)   # drawn first: the round reads nothing before the page is on screen
    root.mainloop()


if __name__ == "__main__":
    main()
