"""Run on the owned desktop: shoot the viewer and the overlay with PrintWindow, read the overlay's layered alpha,
both DWM frames and their z-order, and compose the overlay over the viewer from those."""
import ctypes, json, sys
from ctypes import wintypes as w
from pathlib import Path
from PIL import Image
u, g, d = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32"), ctypes.WinDLL("dwmapi")
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
out = Path(sys.argv[1])
def find(title):
    return u.FindWindowW(None, title)
def frame(h):
    r = w.RECT(); d.DwmGetWindowAttribute(w.HWND(h), 9, ctypes.byref(r), ctypes.sizeof(r)); return [r.left, r.top, r.right - r.left, r.bottom - r.top]
def outer(h):
    r = w.RECT(); u.GetWindowRect(w.HWND(h), ctypes.byref(r)); return [r.left, r.top, r.right - r.left, r.bottom - r.top]
class BIH(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG), ("planes", w.WORD), ("bits", w.WORD), ("comp", w.DWORD),
                ("image", w.DWORD), ("xp", w.LONG), ("yp", w.LONG), ("used", w.DWORD), ("important", w.DWORD)]
def shoot(h):
    x, y, cx, cy = outer(h)
    screen = u.GetDC(None); dc = g.CreateCompatibleDC(screen); bmp = g.CreateCompatibleBitmap(screen, cx, cy); g.SelectObject(dc, bmp)
    drawn = u.PrintWindow(w.HWND(h), dc, 2)
    info = BIH(ctypes.sizeof(BIH), cx, -cy, 1, 32, 0, 0, 0, 0, 0, 0); buf = ctypes.create_string_buffer(cx * cy * 4)
    g.GetDIBits(dc, bmp, 0, cy, buf, ctypes.byref(info), 0)
    g.DeleteObject(bmp); g.DeleteDC(dc); u.ReleaseDC(None, screen)
    img = Image.frombuffer("RGB", (cx, cy), buf, "raw", "BGRX", 0, 1)
    fx, fy, fw, fh = frame(h)
    return bool(drawn), img.crop((fx - x, fy - y, fx - x + fw, fy - y + fh))
result = {}
viewer, overlay = find("viewer"), find("overlay")
result["hwnds"] = [viewer, overlay]
key, alpha, flags = w.DWORD(), ctypes.c_ubyte(), w.DWORD()
ok = u.GetLayeredWindowAttributes(w.HWND(overlay), ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
result["overlay_layered"] = {"read": bool(ok), "alpha": alpha.value, "flags": flags.value,
                             "exstyle": hex(u.GetWindowLongW(w.HWND(overlay), -20) & 0xFFFFFFFF)}
above, h = False, u.GetWindow(w.HWND(viewer), 3)   # GW_HWNDPREV: windows above the viewer
while h:
    if h == overlay: above = True; break
    h = u.GetWindow(w.HWND(h), 3)
result["overlay_above_viewer"] = above
result["frames"] = {"viewer": frame(viewer), "overlay": frame(overlay)}
vd, vimg = shoot(viewer); od, oimg = shoot(overlay)
result["drawn"] = {"viewer": vd, "overlay": od}
vimg.save(out / "pw-viewer.png"); oimg.save(out / "pw-overlay.png")
composed = vimg.copy()
vx, vy = result["frames"]["viewer"][:2]; ox, oy = result["frames"]["overlay"][:2]
region = (ox - vx, oy - vy, ox - vx + oimg.width, oy - vy + oimg.height)
under = composed.crop(region)
composed.paste(Image.blend(under, oimg, alpha.value / 255 if ok and flags.value & 2 else 1.0), region[:2])
composed.save(out / "composed.png")
result["composed"] = "composed.png, from the two PrintWindow shots and the overlay's alpha"
(out / "layers.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
