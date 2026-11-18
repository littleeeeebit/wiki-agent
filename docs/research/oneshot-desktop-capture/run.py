"""Runs the 9d2b3 measurements on an owned desktop through the look's session: a tkinter viewer, with `overlay` a
tkinter overlay over it, then `wgc.exe` on each window, or with `layers` the PrintWindow and composition probe
`layers.py`. Build `wgc.exe` first: csc.exe -platform:x64 -out:wgc.exe -r:<Windows.winmd> -r:System.Runtime.WindowsRuntime.dll
-r:System.Runtime.dll -r:System.Threading.Tasks.dll -r:System.Runtime.InteropServices.WindowsRuntime.dll wgc.cs
(.NET Framework 4 csc, Windows SDK 10.0.22621 UnionMetadata)."""
import json, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tool"))
import desktop_session, desktop_driver
here = Path(__file__).parent.resolve()
VIEWER = ("import tkinter; r = tkinter.Tk(); r.title('viewer'); r.geometry('400x300+50+50'); "
          "c = tkinter.Canvas(r, width=400, height=300, bg='#2050c0'); c.pack(); "
          "c.create_text(200, 150, text='VIEWER TEXT', fill='white', font=('Arial', 24)); r.after(60000, r.destroy); r.mainloop()")
OVERLAY = ("import tkinter; r = tkinter.Tk(); r.title('overlay'); r.overrideredirect(True); r.geometry('200x80+100+150'); "
           "r.attributes('-topmost', True); r.attributes('-alpha', 0.7); "
           "tkinter.Label(r, text='OVERLAY', bg='yellow', font=('Arial', 20)).pack(fill='both', expand=True); r.after(60000, r.destroy); r.mainloop()")
root = Path(tempfile.mkdtemp(prefix="oneshot-capture-"))
def wait(job, seconds=60):
    end = time.monotonic() + seconds
    while not job.finished() and time.monotonic() < end:
        time.sleep(0.2)
    return job.finished()
with desktop_session.look(root, "wgc") as look:
    out = look.folder / "evidence"
    look.start("viewer", [sys.executable, "-c", VIEWER], here)
    if "overlay" in sys.argv:
        look.start("overlay", [sys.executable, "-c", OVERLAY], here)
    time.sleep(2)
    if "layers" in sys.argv:
        look.start("layers", [sys.executable, str(here / "layers.py"), str(out)], here)
        print("layers finished", wait(look.jobs["layers"]))
        print((out / "layers.json").read_text(encoding="utf-8") if (out / "layers.json").exists() else (out / "layers.log").read_text(errors="replace")[-2000:])
    for title in () if "layers" in sys.argv else ("viewer", "overlay") if "overlay" in sys.argv else ("viewer",):
        look.start(f"probe_{title}", [str(here / "wgc.exe"), str(out), title], here)
        print(title, "finished", wait(look.jobs[f"probe_{title}"]))
        print((out / f"wgc-{title}.txt").read_text(encoding="utf-8") if (out / f"wgc-{title}.txt").exists() else "no json")
        log = out / f"probe_{title}.log"
        print(log.read_bytes().decode("cp949", "replace")[-1500:] if log.exists() else "")
print("proof", json.dumps(desktop_session.load(root / "wgc")["proof"]))
print(root / "wgc" / "evidence")
