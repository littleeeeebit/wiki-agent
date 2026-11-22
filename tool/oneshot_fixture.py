"""The owner's desktop round's fixture (plan 9e5, docs/plans/oneshot/9-kinds.md "The owner's desktop round"): the
server's own window, never the build's, showing the page of English words the server drew for one check. It is
opaque and topmost over its whole frame, so a surface read of that frame reads only the round's own pixels
beneath whatever the app draws over it, and it never takes activation. Once shown it writes its frame's handle
and rectangle, in physical pixels, to `--out`, and runs until the round ends its job.

    python oneshot_fixture.py --title oneshot-fixture --words <file> --rect X Y WIDTH HEIGHT --out <file>
"""

import argparse
import ctypes
import json
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--words", type=Path, required=True)
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
    tk.Label(root, text=" ".join(words), font=("Segoe UI", 28), fg="black", bg="white", justify="left",
             wraplength=width - 48).pack(expand=True, fill="both", padx=24, pady=24)
    root.update_idletasks()
    frame = user32.GetParent(root.winfo_id())   # Tk's toplevel frame, the window others find
    user32.SetWindowLongW(frame, GWL_EXSTYLE, user32.GetWindowLongW(frame, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
    root.deiconify()
    root.update()

    def named() -> None:
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
