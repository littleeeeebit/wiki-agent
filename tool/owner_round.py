"""The owner's desktop round's Windows side (plan 9e5, docs/plans/oneshot/9-kinds.md "The owner's desktop round"):
the fixture's words and start, each source bound to the job, process, window handle and class the round created
and proven again before every read, and the capture helper's two reads, each limited where the frame is sampled.

Nothing here reads a window by a bare handle: a window-bound read goes through a Windows Graphics Capture item
made for the handle, the binding is proven again with the item open, and only then is the frame pool started
(`owner_capture.cs`). `PrintWindow` is never called on the owner's desktop.
"""

import ctypes
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

import native_process

FIXTURE = "oneshot-fixture"   # the fixture's title: an app that captures it finds it by this name
FIXTURE_PY = Path(__file__).with_name("oneshot_fixture.py")
SOURCE = Path(__file__).with_name("owner_capture.cs")
FRAMEWORK = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319"
METADATA = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WinMetadata"
REFERENCES = [METADATA / "Windows.Foundation.winmd", METADATA / "Windows.Graphics.winmd",
              METADATA / "Windows.Storage.winmd", FRAMEWORK / "System.Runtime.dll",
              FRAMEWORK / "System.Runtime.InteropServices.WindowsRuntime.dll", FRAMEWORK / "System.Drawing.dll"]
BUILD_SECONDS = 120
ITEM_SECONDS = 15   # the helper's start and its item, before the binding is proven again
READ_SECONDS = 20   # the read once answered: the frame pool's first frame, or the surface, and the file written
FIXTURE_SECONDS = 15   # the fixture's start until it names its window
COUNT = 12   # the fixture's words for one check
# Common English words the server draws from (`secrets`), so neither the builder nor the app knows a round's page.
WORDS = tuple("""
apple river garden window silver basket candle forest pocket mirror ladder winter summer orange yellow purple
bridge castle dragon engine feather guitar hammer island jacket kettle lemon marble needle ocean pencil rabbit
saddle tunnel violin wallet anchor blanket button carpet circle cookie copper cotton desert dinner doctor
falcon farmer flower garlic glove harbor helmet honey hunter jungle kitten lantern meadow monkey napkin orbit
oyster paddle parrot pepper pillow planet puzzle rocket salmon shadow shovel spider spoon statue sugar
teapot ticket tomato tower turtle valley velvet wagon walnut whistle wizard zipper beacon cactus cereal
""".split())


class Unavailable(Exception):
    """A read the round cannot make here: no capture helper, or an item Windows gives empty. The check stays pending."""


def words(count: int = COUNT) -> list[str]:
    """A page of distinct words drawn at random for one check."""

    pool = list(WORDS)
    return [pool.pop(secrets.randbelow(len(pool))) for _ in range(count)]


def contains(text: str | None, page: list[str]) -> bool:
    """Whether `text` holds every word of the fixture's page, each as a whole word, case aside."""

    found = set(re.findall(r"[a-z]+", (text or "").lower()))
    return bool(page) and all(word in found for word in page)


# -- bindings --------------------------------------------------------------------------------------------------

def _user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.IsWindow.argtypes = [w.HWND]
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    return u


def window(hwnd: int) -> dict | None:
    """What a live top-level window is now: its handle, the process that owns it, its class and title."""

    u = _user32()
    if not hwnd or not u.IsWindow(hwnd):
        return None
    pid = w.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name, title, box = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(512), w.RECT()
    u.GetClassNameW(hwnd, name, 256)
    u.GetWindowTextW(hwnd, title, 512)
    u.GetWindowRect(hwnd, ctypes.byref(box))
    return {"hwnd": int(hwnd), "pid": pid.value, "class": name.value, "title": title.value,
            "rect": [box.left, box.top, box.right - box.left, box.bottom - box.top]}


def bind(job: str, hwnd: int, members: list[dict], title: str | None = None) -> dict:
    """The binding of a source the round started: the window `hwnd` of a process of `job`'s `members`, with the
    process's identity and its class; for the fixture also its title and frame, which must not change.
    ValueError when it is none of those."""

    now = window(hwnd)
    member = next((m for m in members if now and m["pid"] == now["pid"]), None)
    if now is None or member is None:
        raise ValueError(f"window {hwnd} is not one of {job}'s")
    if title is not None and now["title"] != title:
        raise ValueError(f"window {hwnd} is not titled {title}")
    return {"job": job, "hwnd": now["hwnd"], "pid": now["pid"], "class": now["class"], "title": title,
            "rect": now["rect"] if title is not None else None, "process": native_process.identity(now["pid"])}


def proven(binding: dict, members: list[dict]) -> str:
    """Why `binding` no longer names the window the round bound, or empty: the handle is gone or names another
    process's or another class's window, its process is not the one recorded (a reused pid), is not in the job,
    or the fixture's title or frame changed. A window replaced, even by its own app, has another handle and is
    refused."""

    now = window(binding["hwnd"])
    if now is None:
        return "the window is gone"
    if now["pid"] != binding["pid"] or now["class"] != binding["class"]:
        return "the handle names another window"
    if not native_process.same(binding["process"], native_process.identity(now["pid"])):
        return "its process is not the one bound"
    if not any(native_process.same(binding["process"], m) for m in members):
        return "its process is not in the round's job"
    if binding.get("title") is not None and now["title"] != binding["title"]:
        return "the fixture's title changed"
    if binding.get("rect") is not None and now["rect"] != binding["rect"]:
        return "the fixture moved"
    return ""


# -- the capture helper -----------------------------------------------------------------------------------------

def helper(tools: Path) -> Path:
    """The capture helper built from `owner_capture.cs` into `tools`, once for each source: the .NET Framework's
    csc against the system's WinMetadata, which every Windows 10 and 11 carries. Unavailable when it cannot be
    built: the checks that need a window-bound read stay pending."""

    source = SOURCE.read_bytes()
    exe = tools / f"owner_capture-{hashlib.sha256(source).hexdigest()[:12]}.exe"
    if exe.is_file():
        return exe
    csc = FRAMEWORK / "csc.exe"
    missing = [str(p) for p in (csc, *REFERENCES) if not p.is_file()]
    if missing:
        raise Unavailable("캡처 도우미를 빌드할 도구가 없다 — " + ", ".join(missing))
    tools.mkdir(parents=True, exist_ok=True)
    part = exe.with_suffix(".part.exe")
    try:
        done = subprocess.run([str(csc), "-nologo", "-utf8output", "-platform:x64", f"-out:{part}",
                               *(f"-r:{p}" for p in REFERENCES), str(SOURCE)], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=BUILD_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unavailable(f"캡처 도우미를 빌드하지 못했다 — {exc}") from exc
    if done.returncode != 0 or not part.is_file():
        raise Unavailable("캡처 도우미를 빌드하지 못했다 — " + (done.stdout + done.stderr).strip()[-400:])
    part.replace(exe)
    return exe


def _waited(file: Path, seconds: float, halt) -> dict | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not halt.is_set():
        if file.is_file():
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass   # replaced whole by its writer: read again
        time.sleep(0.05)
    return None


def _ended(session, key: str) -> None:
    session.observe()
    proof = session.jobs[key].end(10)
    if not proof["clean"]:
        raise RuntimeError(f"캡처 도우미를 끝내지 못했다 — {'; '.join(proof['reasons'])}")


def window_read(session, key: str, exe: Path, binding: dict, members, halt) -> dict:
    """A window-bound read of `binding`'s window: the helper makes the item, the binding is proven again with it
    open, and only then may the helper start the frame pool. `{"read": True, "file"}`, or why nothing was read.
    Unavailable when Windows gives an empty item. The helper's job is ended on every outcome."""

    folder = session.reserve(key)
    try:
        session.start(key, [str(exe), "window", str(binding["hwnd"]), str(folder)], folder, halt=halt)
        item = _waited(folder / "item.json", ITEM_SECONDS, halt)
        if item is None:
            return {"read": False, "why": "the helper made no item in time"}
        if item.get("empty"):
            (folder / "stop").write_text("", encoding="utf-8")
            raise Unavailable(f"창 캡처 항목이 비었다 — {item.get('why')}")
        why = proven(binding, members())   # with the item open: a window replaced since never gets its frames
        (folder / ("stop" if why else "go")).write_text("", encoding="utf-8")
        result = _waited(folder / "result.json", READ_SECONDS, halt) or {"read": False, "why": "no result in time"}
        if why:
            return {"read": False, "why": why}
        return {**result, "file": str(folder / "window.png")} if result.get("read") else result
    finally:
        _ended(session, key)


def surface_read(session, key: str, exe: Path, rect: list[int], halt) -> dict:
    """A surface read of exactly `rect`, the fixture's frame, whatever is composed there at the moment of
    sampling: `{"read": True, "file"}`, or why not. The helper's job is ended on every outcome."""

    folder = session.reserve(key)
    try:
        session.start(key, [str(exe), "surface", *(str(v) for v in rect), str(folder)], folder, halt=halt)
        result = _waited(folder / "result.json", READ_SECONDS, halt) or {"read": False, "why": "no result in time"}
        return {**result, "file": str(folder / "surface.png")} if result.get("read") else result
    finally:
        _ended(session, key)


def fixture(session, key: str, page: list[str], rect: list[int], halt) -> tuple[dict, list[int]]:
    """The check's fixture started in a job of its own over `rect`, showing `page`: its binding and its frame in
    physical pixels, as it reports them. RuntimeError, never the build's to fix, when it names no window of its
    job in time; its job ends with the round's."""

    folder = session.reserve(key)
    (folder / "words.txt").write_text(" ".join(page), encoding="utf-8")
    out = folder / "window.json"
    session.start(key, [sys.executable, str(FIXTURE_PY), "--title", FIXTURE, "--words", str(folder / "words.txt"),
                        "--rect", *(str(v) for v in rect), "--out", str(out)], folder, halt=halt)
    named = _waited(out, FIXTURE_SECONDS, halt)
    if named is None:
        raise RuntimeError("고정 창이 제때 뜨지 않았다")
    session.observe()
    try:
        return bind(key, named["hwnd"], session.record["jobs"][key]["members"], FIXTURE), named["rect"]
    except ValueError as exc:
        raise RuntimeError(f"고정 창을 묶지 못했다 — {exc}") from exc
