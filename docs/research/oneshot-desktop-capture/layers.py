"""Run on the owned desktop: shoot the viewer and the overlay with PrintWindow, read the overlay's layered alpha,
both DWM frames and their z-order, and compose the overlay over the viewer from those.

layers.py <out> <expected desktop> <viewer job> <overlay job>. It refuses to run on any desktop but the one it is
told, and takes only a window of each fixture's own job, so it never reads a window of the owner's. It saves a
reconstruction only when every input it needs was proven: both shots fully read, both frames, the overlay above
the viewer, and a constant alpha with no color key. Otherwise it records why it refused."""
import ctypes, json, sys
from ctypes import wintypes as w
from pathlib import Path
from PIL import Image
u, g, d, k = (ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32"), ctypes.WinDLL("dwmapi"),
              ctypes.WinDLL("kernel32", use_last_error=True))
for f, res in ((u.GetThreadDesktop, w.HANDLE), (u.GetDC, w.HDC), (g.CreateCompatibleDC, w.HDC),
               (g.CreateCompatibleBitmap, w.HBITMAP), (g.SelectObject, w.HGDIOBJ), (u.GetWindow, w.HWND),
               (k.OpenJobObjectW, w.HANDLE), (k.OpenProcess, w.HANDLE)):
    f.restype = res
for f, args in ((g.CreateCompatibleDC, (w.HDC,)), (g.CreateCompatibleBitmap, (w.HDC, ctypes.c_int, ctypes.c_int)),
                (g.SelectObject, (w.HDC, w.HGDIOBJ)), (u.PrintWindow, (w.HWND, w.HDC, w.UINT)),
                (g.GetDIBits, (w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT)),
                (g.DeleteObject, (w.HGDIOBJ,)), (g.DeleteDC, (w.HDC,)), (u.ReleaseDC, (w.HWND, w.HDC)),
                (u.GetWindow, (w.HWND, w.UINT)), (k.IsProcessInJob, (w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL))),
                (k.CloseHandle, (w.HANDLE,))):
    f.argtypes = args   # 64-bit handles: never passed as a C int
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
out, expected, viewer_job, overlay_job = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
result = {"refusals": []}


def finish():
    (out / "layers.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    sys.exit(0 if not result["refusals"] else 3)


def desktop():
    name, needed = ctypes.create_unicode_buffer(256), w.DWORD()
    ok = u.GetUserObjectInformationW(u.GetThreadDesktop(k.GetCurrentThreadId()), 2, name, 512, ctypes.byref(needed))
    return name.value if ok else None


def find(title, job_name):
    """The one visible top-level window on this desktop titled `title` whose process is in the fixture's job."""

    job = k.OpenJobObjectW(0x4, False, job_name)   # JOB_OBJECT_QUERY
    if not job:
        return None
    found = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def visit(h, _):
        text = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(h, text, 512)
        if u.IsWindowVisible(h) and text.value == title:
            pid = w.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            process, inside = k.OpenProcess(0x1000, False, pid.value), w.BOOL()
            if process:
                k.IsProcessInJob(process, job, ctypes.byref(inside))
                k.CloseHandle(process)
            if inside:
                found.append(h)
        return True

    try:
        u.EnumWindows(visit, 0)
    finally:
        k.CloseHandle(job)
    return found[0] if len(found) == 1 else None


def frame(h):
    r = w.RECT()
    if d.DwmGetWindowAttribute(w.HWND(h), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        return None
    return [r.left, r.top, r.right - r.left, r.bottom - r.top]


def outer(h):
    r = w.RECT()
    return [r.left, r.top, r.right - r.left, r.bottom - r.top] if u.GetWindowRect(w.HWND(h), ctypes.byref(r)) else None


class BIH(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG), ("planes", w.WORD), ("bits", w.WORD), ("comp", w.DWORD),
                ("image", w.DWORD), ("xp", w.LONG), ("yp", w.LONG), ("used", w.DWORD), ("important", w.DWORD)]


def shoot(h):
    """The window drawn by PrintWindow and read back in full, cut to its frame; None and why when any step fails.
    The bitmap is deselected before it is read, and every GDI object is released whatever happens."""

    box, fr = outer(h), frame(h)
    if not box or not fr or box[2] <= 0 or box[3] <= 0:
        return None, "its rectangle or frame could not be read"
    x, y, cx, cy = box
    screen = u.GetDC(None)   # only a format reference for the memory bitmap; nothing is read from it
    dc = g.CreateCompatibleDC(screen)
    bmp = g.CreateCompatibleBitmap(screen, cx, cy)
    previous = g.SelectObject(dc, bmp)
    try:
        if not u.PrintWindow(w.HWND(h), dc, 2):   # PW_RENDERFULLCONTENT
            return None, "PrintWindow drew nothing"
        g.SelectObject(dc, previous)
        previous = None
        info, buf = BIH(ctypes.sizeof(BIH), cx, -cy, 1, 32, 0, 0, 0, 0, 0, 0), ctypes.create_string_buffer(cx * cy * 4)
        lines = g.GetDIBits(dc, bmp, 0, cy, buf, ctypes.byref(info), 0)
        if lines != cy:
            return None, f"GetDIBits read {lines} of {cy} lines"
    finally:
        if previous:
            g.SelectObject(dc, previous)
        g.DeleteObject(bmp)
        g.DeleteDC(dc)
        u.ReleaseDC(None, screen)
    img = Image.frombuffer("RGB", (cx, cy), buf, "raw", "BGRX", 0, 1)
    fx, fy, fw, fh = fr
    return img.crop((fx - x, fy - y, fx - x + fw, fy - y + fh)), None


result["desktop"] = desktop()
if result["desktop"] != expected or (result["desktop"] or "").lower() == "default":
    result["refusals"].append("not on the look's owned desktop")
    finish()
viewer, overlay = find("viewer", viewer_job), find("overlay", overlay_job)
result["hwnds"] = [viewer, overlay]
if not viewer or not overlay:
    result["refusals"].append("the viewer or the overlay is not one window of its fixture's job here")
    finish()
key, alpha, flags = w.DWORD(), ctypes.c_ubyte(), w.DWORD()
ok = u.GetLayeredWindowAttributes(w.HWND(overlay), ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
result["overlay_layered"] = {"read": bool(ok), "alpha": alpha.value, "flags": flags.value,
                             "exstyle": hex(u.GetWindowLongW(w.HWND(overlay), -20) & 0xFFFFFFFF)}
above, h = False, u.GetWindow(w.HWND(viewer), 3)   # GW_HWNDPREV: windows above the viewer
while h:
    if h == overlay:
        above = True
        break
    h = u.GetWindow(w.HWND(h), 3)
result["overlay_above_viewer"] = above
result["frames"] = {"viewer": frame(viewer), "overlay": frame(overlay)}
(vimg, verr), (oimg, oerr) = shoot(viewer), shoot(overlay)
result["drawn"] = {"viewer": verr or True, "overlay": oerr or True}
if vimg:
    vimg.save(out / "pw-viewer.png")
if oimg:
    oimg.save(out / "pw-overlay.png")
if not (vimg and oimg):
    result["refusals"].append("a shot was not fully read")
if not (result["frames"]["viewer"] and result["frames"]["overlay"]):
    result["refusals"].append("a frame could not be read")
if not above:
    result["refusals"].append("the overlay is not above the viewer")
if not ok or flags.value != 2:   # LWA_ALPHA alone: a color key (LWA_COLORKEY) would hide pixels a blend shows
    result["refusals"].append("the overlay has no constant alpha alone to compose with")
if result["refusals"]:
    finish()
vx, vy = result["frames"]["viewer"][:2]
ox, oy = result["frames"]["overlay"][:2]
region = (ox - vx, oy - vy, ox - vx + oimg.width, oy - vy + oimg.height)
composed = vimg.copy()
composed.paste(Image.blend(composed.crop(region), oimg, alpha.value / 255), region[:2])
composed.save(out / "composed.png")
result["composed"] = "composed.png, from the two PrintWindow shots and the overlay's constant alpha"
finish()
