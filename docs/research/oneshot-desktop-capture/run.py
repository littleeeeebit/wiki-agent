"""Runs the 9d2b3 measurements on an owned desktop through the look's session: a tkinter viewer and a tkinter
overlay over it, then with `wgc` the capture probe `wgc.exe` on each window, or with `layers` the PrintWindow and
composition probe `layers.py`. Each probe is told the look's desktop and its fixtures' jobs, and refuses anything
else. The outputs and the look's cleanup receipt (`cleanup-<mode>.json`) go to the folder given. Build `wgc.exe`
first: csc.exe -platform:x64 -out:wgc.exe -r:<Windows.winmd> -r:System.Runtime.WindowsRuntime.dll
-r:System.Runtime.dll -r:System.Threading.Tasks.dll -r:System.Runtime.InteropServices.WindowsRuntime.dll wgc.cs
(.NET Framework 4 csc, Windows SDK 10.0.22621 UnionMetadata)."""
import json, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tool"))
import desktop_session
here = Path(__file__).parent.resolve()
WGC = Path(os.environ.get("WGC_EXE", here / "wgc.exe"))   # built outside the repository, named by WGC_EXE
VIEWER = ("import tkinter; r = tkinter.Tk(); r.title('viewer'); r.geometry('400x300+50+50'); "
          "c = tkinter.Canvas(r, width=400, height=300, bg='#2050c0'); c.pack(); "
          "c.create_text(200, 150, text='VIEWER TEXT', fill='white', font=('Arial', 24)); r.after(60000, r.destroy); r.mainloop()")
OVERLAY = ("import tkinter; r = tkinter.Tk(); r.title('overlay'); r.overrideredirect(True); r.geometry('200x80+100+150'); "
           "r.attributes('-topmost', True); r.attributes('-alpha', 0.7); "
           "tkinter.Label(r, text='OVERLAY', bg='yellow', font=('Arial', 20)).pack(fill='both', expand=True); r.after(60000, r.destroy); r.mainloop()")
def outputs(mode):
    return ([f"wgc-{title}.{kind}" for title in ("viewer", "overlay") for kind in ("txt", "png")] if mode == "wgc"
            else ["layers.json", "pw-viewer.png", "pw-overlay.png", "composed.png"]) + [f"cleanup-{mode}.json"]
def reserve(saved, mode):
    """Why `saved` cannot take this mode's run, or None once it is reserved for it. The reservation is a lock file
    created exclusively, so of two runs started together only one gets it; the other refuses. Holding it, the run
    then refuses a folder that already holds any of this mode's outputs, so a run's evidence never sits beside
    another's."""
    try:
        os.close(os.open(saved / f"run-{mode}.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return f"{saved} is reserved by another {mode} run"
    stale = [name for name in outputs(mode) if (saved / name).exists()]
    if stale:
        (saved / f"run-{mode}.lock").unlink()
        return f"{saved} already holds {', '.join(stale)}"
    return None
if sys.argv[1:] == ["--check"]:   # two publishers, modeled with files only
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        assert reserve(folder, "layers") is None
        assert reserve(folder, "layers") == f"{folder} is reserved by another layers run"
        assert reserve(folder, "wgc") is None   # another mode's names never collide
        (folder / "layers.json").write_text("{}")
        (folder / "run-layers.lock").unlink()
        assert reserve(folder, "layers") == f"{folder} already holds layers.json"
        assert not (folder / "run-layers.lock").exists()
    print("reservation check passed")
    sys.exit(0)
mode, saved = sys.argv[1], Path(sys.argv[2])   # run.py wgc|layers <folder for the outputs and the cleanup receipt>
refusal = reserve(saved, mode)
if refusal:
    sys.exit(f"refused before starting anything: {refusal}")
root = Path(tempfile.mkdtemp(prefix="oneshot-capture-"))
def wait(job, seconds=60):
    end = time.monotonic() + seconds
    while not job.finished() and time.monotonic() < end:
        time.sleep(0.2)
    return job.finished()
with desktop_session.look(root, mode) as look:
    out = look.folder / "evidence"
    look.start("viewer", [sys.executable, "-c", VIEWER], here)
    look.start("overlay", [sys.executable, "-c", OVERLAY], here)
    time.sleep(2)
    desktop, jobs = look.record["desktop"], {key: look.jobs[key].name for key in ("viewer", "overlay")}
    if mode == "layers":
        look.start("layers", [sys.executable, str(here / "layers.py"), str(out), desktop, jobs["viewer"], jobs["overlay"]], here)
        print("layers finished", wait(look.jobs["layers"]))
    else:
        for title in ("viewer", "overlay"):
            look.start(f"probe_{title}", [str(WGC), str(out), desktop, jobs[title], title], here)
            print(title, "finished", wait(look.jobs[f"probe_{title}"]))
for name in outputs(mode)[:-1]:
    if (root / mode / "evidence" / name).exists():
        (saved / name).write_bytes((root / mode / "evidence" / name).read_bytes())
record = desktop_session.load(root / mode)
receipt = {"state": record["state"], "proof": record["proof"],
           "jobs": {key: {field: job["proof"][field] for field in ("clean", "joined", "total", "active")}
                    for key, job in record["jobs"].items()}}
(saved / f"cleanup-{mode}.json").write_text(json.dumps(receipt, indent=1), encoding="utf-8")
(saved / f"run-{mode}.lock").unlink()   # the bundle is whole; its outputs now refuse any later run
print(json.dumps(receipt))
