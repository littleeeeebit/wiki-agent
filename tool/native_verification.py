"""Owned Windows Win32 control verification; no global keyboard or attachment."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from common import process


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NativeAction(Closed):
    assertion: str = Field(min_length=1)
    action: Literal["click", "read_text"]
    control_id: int = Field(ge=1, le=65535)
    observe_id: int = Field(ge=1, le=65535)
    expected: str = Field(min_length=1)
    event: str = ""


class NativeTarget(Closed):
    application: str = Field(min_length=1)
    window_class: str = Field(min_length=1)
    arguments: list[str] = Field(default_factory=list)
    build_inputs: list[str] = Field(min_length=1)
    actions: list[NativeAction] = Field(min_length=1)


class NativeSettings(Closed):
    version: Literal[1]
    host: Literal["windows-win32"]
    application: str = Field(min_length=1)
    executable: str = Field(min_length=1)
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ownership: Literal["launch-disposable"]
    desktop: Literal["attempt-owned"]
    profile: str = Field(min_length=1)


def digest(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()


def build_identity(root: Path, head: str, inputs: list[str]) -> dict:
    """Measure launch inputs against Git blobs, including binary build artifacts."""
    measured = {}
    for name in inputs:
        file = root / name
        if Path(name).is_absolute() or root.resolve() not in file.resolve().parents or file.is_symlink():
            raise ValueError("Native build inputs must stay inside the checkout")
        done = subprocess.run(["git", "rev-parse", f"{head}:{name}"], cwd=root, capture_output=True,
                              timeout=30, **process.background_options())
        actual = subprocess.run(["git", "hash-object", "--path=" + name, name], cwd=root, capture_output=True,
                                timeout=30, **process.background_options())
        if done.returncode or actual.returncode or done.stdout.strip() != actual.stdout.strip():
            raise ValueError("Native build input differs from the reviewed head: " + name)
        measured[name] = {"sha256": digest(file), "git_blob": done.stdout.decode("ascii").strip()}
    return measured


def preflight(root: Path, head: str, target: dict, settings: dict) -> dict:
    target, settings = NativeTarget.model_validate(target), NativeSettings.model_validate(settings)
    if sys.platform != "win32" or settings.host != "windows-win32":
        raise ValueError("Native collector supports Windows Win32 only")
    executable = Path(settings.executable)
    if settings.application != target.application or not executable.is_absolute() or not executable.is_file() \
            or executable.is_symlink() or digest(executable) != settings.executable_sha256:
        raise ValueError("Native executable/application differs from the approved target")
    if root.resolve() in executable.resolve().parents:
        if executable.resolve().relative_to(root.resolve()).as_posix() not in target.build_inputs:
            raise ValueError("The native executable must be a measured reviewed build input")
    elif executable.name.lower() not in ("python.exe", "pythonw.exe") or not target.arguments \
            or target.arguments[0] not in target.build_inputs or not target.arguments[0].endswith(".py"):
        raise ValueError("External native builds need attestation; only approved Python with a reviewed script is supported")
    return build_identity(root, head, target.build_inputs)


def validate_receipt(data: dict, root: Path, artifact: Path, target: dict, settings: dict, revisions: dict,
                     *, redact_text=None) -> dict:
    """Browser output cannot satisfy native identity, action, event or ownership proof."""
    build = preflight(root, data["head"], target, settings)
    native = data.get("native")
    fields = {"application", "host", "driver", "executable_path_digest", "executable_sha256", "build_head", "build_inputs",
              "pid", "hwnd", "window_class", "actions", "environment", "artifacts"}
    if not isinstance(native, dict) or set(native) != fields:
        raise ValueError("Incomplete native receipt")
    for key, expected in {"application": target["application"], "host": "windows-win32",
                          "driver": "win32-ctypes-v1", "executable_path_digest": hashlib.sha256(settings["executable"].encode()).hexdigest(),
                          "executable_sha256": settings["executable_sha256"], "build_head": data["head"],
                          "build_inputs": build, "window_class": target["window_class"]}.items():
        if native[key] != expected:
            raise ValueError("Native receipt identity changed: " + key)
    if any(type(native[key]) is not int or native[key] <= 0 for key in ("pid", "hwnd")):
        raise ValueError("Observed native process/window identity is missing")
    ownership = json.loads((artifact / "ownership.json").read_text(encoding="utf-8"))
    if ownership.get("state") != "cleaned" or ownership.get("executable") != settings["executable"] \
            or any(ownership.get(key) != native[key] for key in ("pid", "hwnd")):
        raise ValueError("Native process cleanup/ownership is not proven")
    environment = native["environment"]
    if not isinstance(environment, dict) or set(environment) != {"os", "runtime", "dpi", "geometry", "display", "profile", "revisions", "desktop"} \
            or any(not isinstance(environment.get(key), str) or not environment[key] for key in ("os", "runtime")) \
            or environment.get("profile") != settings["profile"] or environment.get("revisions") != revisions \
            or type(environment.get("dpi")) is not int or environment["dpi"] <= 0:
        raise ValueError("Native environment identity is missing or changed")
    if not environment.get("desktop") or environment["desktop"] != ownership.get("desktop"):
        raise ValueError("Native desktop ownership is missing")
    for key, size in (("geometry", 4), ("display", 2)):
        values = environment.get(key)
        if not isinstance(values, list) or len(values) != size or any(type(v) is not int for v in values):
            raise ValueError("Native geometry/display observation is missing")
    actions = native["actions"]
    if not isinstance(actions, list) or len(actions) != len(target["actions"]):
        raise ValueError("Registered native actions were not all observed")
    observations = {row["id"]: row for row in data["observations"]}
    measured = json.loads((artifact / "observations.json").read_text(encoding="utf-8"))
    if len(measured["actions"]) != len(actions):
        raise ValueError("Native observation artifact differs from the receipt")
    for actual, expected, raw in zip(actions, target["actions"], measured["actions"], strict=True):
        if not isinstance(actual, dict) or set(actual) != {*expected, "actual", "pid", "hwnd", "focused", "event_observed"} \
                or any(actual.get(key) != value for key, value in expected.items()) \
                or actual.get("focused") is not True or any(actual.get(key) != native[key] for key in ("pid", "hwnd")):
            raise ValueError("Unregistered native action or changed target/focus")
        observation = observations[expected["assertion"]]
        text = redact_text(raw["actual"]) if redact_text else raw["actual"]
        if actual["actual"] != observation["actual"] or text != actual["actual"] \
                or any(raw.get(key) != value for key, value in expected.items()):
            raise ValueError("Native action differs from its assertion observation")
        event = actual.get("event_observed")
        expected_event = {key: actual[key] for key in ("pid", "hwnd", "control_id", "event")}
        if expected["event"] and event != expected_event and observation["pass"]:
            raise ValueError("Required native event is missing")
        passed = raw["actual"] == expected["expected"] and (not expected["event"] or event == expected_event)
        if observation["pass"] is not passed:
            raise ValueError("Native assertion verdict disagrees with observed result")
    artifacts = native["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Native observation artifacts are missing")
    for row in artifacts:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"} or not isinstance(row["path"], str):
            raise ValueError("Invalid native artifact")
        file = artifact / row["path"]
        if Path(row["path"]).is_absolute() or artifact.resolve() not in file.resolve().parents or file.is_symlink() \
                or digest(file) != row["sha256"]:
            raise ValueError("Native artifact is outside ownership or its content changed")
    return native


@contextmanager
def owned_desktop():
    """An isolated Win32 desktop; never switch the user's input desktop."""
    from ctypes import wintypes as w

    u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = (w.DWORD,), w.HANDLE
    u.CreateDesktopW.argtypes = (w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.DWORD, w.DWORD, w.LPVOID)
    u.CreateDesktopW.restype = w.HANDLE
    u.SetThreadDesktop.argtypes, u.CloseDesktop.argtypes = (w.HANDLE,), (w.HANDLE,)
    original = u.GetThreadDesktop(k.GetCurrentThreadId())
    name = "WikiVerification-" + uuid.uuid4().hex
    desktop = u.CreateDesktopW(name, None, None, 0, 0x01FF, None)
    if not desktop:
        raise ValueError("Cannot create an isolated native test desktop")
    try:
        if not u.SetThreadDesktop(desktop):
            raise ValueError("Cannot bind the collector to its owned test desktop")
        yield name
    finally:
        restored = u.SetThreadDesktop(original)
        closed = u.CloseDesktop(desktop)
        if not restored or not closed:
            raise ValueError("Native test desktop cleanup failed")


def run(request: dict) -> dict:
    preflight(Path(request["root"]), request["head"], request["target"], request["settings"])
    with owned_desktop() as desktop:
        return _run({**request, "desktop": desktop})


def _run(request: dict) -> dict:
    """Launch one approved disposable app in a kill-on-close job and reap it."""
    from ctypes import wintypes as w

    root, artifact = Path(request["root"]), Path(request["artifact_dir"])
    settings, target = request["settings"], request["target"]
    build = preflight(root, request["head"], target, settings)
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    u.EnumWindows.argtypes = (callback_type, w.LPARAM)
    u.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
    u.GetClassNameW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
    u.GetDlgItem.argtypes, u.GetDlgItem.restype = (w.HWND, ctypes.c_int), w.HWND
    u.IsWindow.argtypes, u.IsWindowVisible.argtypes = (w.HWND,), (w.HWND,)
    u.IsWindowEnabled.argtypes = (w.HWND,)
    u.GetWindowRect.argtypes = (w.HWND, ctypes.POINTER(w.RECT))
    u.IsChild.argtypes = (w.HWND, w.HWND)
    u.SendMessageTimeoutW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT,
                                    ctypes.POINTER(ctypes.c_size_t))
    k.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
    k.QueryFullProcessImageNameW.restype = w.BOOL
    k.GetExitCodeProcess.argtypes = (w.HANDLE, ctypes.POINTER(w.DWORD))
    k.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    k.TerminateProcess.argtypes, k.CloseHandle.argtypes = (w.HANDLE, w.UINT), (w.HANDLE,)

    class GUIInfo(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("flags", w.DWORD), ("active", w.HWND), ("focus", w.HWND),
                    ("capture", w.HWND), ("menu", w.HWND), ("move", w.HWND), ("caret", w.HWND), ("rect", w.RECT)]

    u.GetGUIThreadInfo.argtypes = (w.DWORD, ctypes.POINTER(GUIInfo))
    u.AttachThreadInput.argtypes = (w.DWORD, w.DWORD, w.BOOL)
    u.SetActiveWindow.argtypes, u.SetActiveWindow.restype = (w.HWND,), w.HWND
    u.SetFocus.argtypes, u.SetFocus.restype = (w.HWND,), w.HWND

    def pid(hwnd):
        value = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
        return value.value

    def class_name(hwnd):
        value = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, value, len(value))
        return value.value

    def text(hwnd):
        value = ctypes.create_unicode_buffer(8192)
        result = ctypes.c_size_t()
        if not u.SendMessageTimeoutW(hwnd, 0x000D, len(value), ctypes.cast(value, ctypes.c_void_p).value,
                                     2, 2000, ctypes.byref(result)):
            raise ValueError("Native text observation timed out")
        return value.value

    def guard(hwnd, control):
        info = GUIInfo(size=ctypes.sizeof(GUIInfo))
        thread = u.GetWindowThreadProcessId(hwnd, None)
        focused = bool(u.GetGUIThreadInfo(thread, ctypes.byref(info)) and info.active == hwnd
                       and info.focus and pid(info.focus) == proc.pid
                       and (info.focus == hwnd or u.IsChild(hwnd, info.focus)))
        if proc.poll() is not None or not u.IsWindow(hwnd) or pid(hwnd) != proc.pid \
                or class_name(hwnd) != target["window_class"] or not focused \
                or not control or not u.IsWindow(control) or pid(control) != proc.pid \
                or not u.IsWindowVisible(control) or not u.IsWindowEnabled(control):
            raise ValueError("Native target, selector or focus changed; no input sent " + json.dumps({
                "alive": proc.poll() is None, "target_pid": pid(hwnd), "expected_pid": proc.pid,
                "class": class_name(hwnd), "focused": focused,
                "control": bool(control), "visible": bool(u.IsWindowVisible(control)),
                "enabled": bool(u.IsWindowEnabled(control))}))
        image, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        if not k.QueryFullProcessImageNameW(int(proc._handle), 0, image, ctypes.byref(size)) \
                or Path(image.value).resolve() != Path(settings["executable"]).resolve() \
                or digest(Path(image.value)) != settings["executable_sha256"]:
            raise ValueError("Observed native process executable differs from the approved target")

    env = {**os.environ, "WIKI_NATIVE_PROFILE": str(artifact / "profile"),
           "WIKI_NATIVE_EVENTS": str(artifact / "events.jsonl")}
    (artifact / "profile").mkdir()
    with (artifact / "process.log").open("wb") as log:
        # Python's STARTUPINFO does not forward lpDesktop to CreateProcess.
        # Use the native structure so an arbitrary approved Win32 app launches
        # on the attempt desktop, without needing a cooperative app adapter.
        import msvcrt

        class Startup(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
                        ("x", w.DWORD), ("y", w.DWORD), ("width", w.DWORD), ("height", w.DWORD),
                        ("chars_x", w.DWORD), ("chars_y", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                        ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", w.LPVOID),
                        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]

        k.CreateProcessW.argtypes = (w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD,
                                     w.LPVOID, w.LPCWSTR, ctypes.POINTER(Startup), ctypes.POINTER(ProcessInfo))
        k.CreateProcessW.restype = w.BOOL
        with open(os.devnull, "rb") as null:
            handles = [msvcrt.get_osfhandle(file.fileno()) for file in (null, log)]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            startup = Startup(size=ctypes.sizeof(Startup), desktop=request["desktop"], flags=0x100,
                              stdin=handles[0], stdout=handles[1], stderr=handles[1])
            info = ProcessInfo()
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([settings["executable"], *target["arguments"]]))
            environment = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0")
            try:
                created = k.CreateProcessW(settings["executable"], command, None, None, True,
                                            subprocess.CREATE_NO_WINDOW | process.SUSPENDED | 0x400,
                                            environment, str(root), ctypes.byref(startup), ctypes.byref(info))
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
            if not created:
                raise ctypes.WinError(ctypes.get_last_error())
        k.CloseHandle(info.thread)

        def poll():
            if k.WaitForSingleObject(info.process, 0) == 0x102:
                return None
            code = w.DWORD()
            if not k.GetExitCodeProcess(info.process, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value

        def wait(timeout):
            if k.WaitForSingleObject(info.process, int(timeout * 1000)) != 0:
                raise ValueError("Owned native process did not finish cleanup")

        proc = SimpleNamespace(pid=info.pid, _handle=info.process, poll=poll, wait=wait,
                               kill=lambda: k.TerminateProcess(info.process, 1))
        job = process.contained(proc)
        ownership = {"pid": proc.pid, "hwnd": None, "executable": settings["executable"],
                     "profile": str(artifact / "profile"), "desktop": request["desktop"], "state": "running"}
        owned = artifact / "ownership.json"
        try:
            owned.write_text(json.dumps(ownership), encoding="utf-8")
            if not job:
                raise ValueError("Cannot establish owned native process cleanup")
            process.resumed(proc)
            hwnd, deadline = None, time.monotonic() + 10
            while hwnd is None and time.monotonic() < deadline and proc.poll() is None:
                matches = []

                @callback_type
                def visit(window, _):
                    if pid(window) == proc.pid and u.IsWindowVisible(window) \
                            and class_name(window) == target["window_class"]:
                        matches.append(window)
                    return True

                u.EnumWindows(visit, 0)
                if len(matches) > 1:
                    raise ValueError("Ambiguous native target window")
                hwnd = matches[0] if matches else None
                if hwnd is None:
                    time.sleep(0.05)
            if not hwnd:
                raise ValueError("Owned native target window is unavailable")
            ownership["hwnd"] = int(hwnd)
            owned.write_text(json.dumps(ownership), encoding="utf-8")
            # The user's foreground belongs to another desktop. Establish and
            # measure focus only in the input queue of this owned target.
            thread = u.GetWindowThreadProcessId(hwnd, None)
            own_thread = k.GetCurrentThreadId()
            msg = w.MSG()
            u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            if not u.AttachThreadInput(own_thread, thread, True):
                raise ValueError("Cannot focus the owned native input queue")
            try:
                u.SetActiveWindow(hwnd)
                u.SetFocus(hwnd)
            finally:
                u.AttachThreadInput(own_thread, thread, False)
            actions, observations = [], []
            for action in target["actions"]:
                control, observed = u.GetDlgItem(hwnd, action["control_id"]), u.GetDlgItem(hwnd, action["observe_id"])
                guard(hwnd, control)
                guard(hwnd, observed)
                offset = (artifact / "events.jsonl").stat().st_size if (artifact / "events.jsonl").exists() else 0
                if action["action"] == "click":
                    result = ctypes.c_size_t()
                    guard(hwnd, control)
                    if not u.SendMessageTimeoutW(control, 0x00F5, 0, 0, 2, 2000, ctypes.byref(result)):
                        raise ValueError("Native click timed out")
                guard(hwnd, observed)
                actual = text(observed)
                event = None
                if action.get("event"):
                    events = artifact / "events.jsonl"
                    if events.exists():
                        with events.open("rb") as stream:
                            stream.seek(offset)
                            for line in stream:
                                row = json.loads(line)
                                if (row.get("pid"), row.get("hwnd"), row.get("control_id"), row.get("event")) == \
                                        (proc.pid, int(hwnd), action["control_id"], action["event"]):
                                    event = {key: row[key] for key in ("pid", "hwnd", "control_id", "event")}
                actions.append({**action, "actual": actual, "pid": proc.pid, "hwnd": int(hwnd),
                                "focused": True, "event_observed": event})
                observations.append({"id": action["assertion"], "expected": action["expected"],
                                     "actual": actual, "pass": actual == action["expected"]
                                     and (not action.get("event") or event is not None)})
            geometry = w.RECT()
            if not u.GetWindowRect(hwnd, ctypes.byref(geometry)):
                raise ValueError("Cannot measure native window geometry")
            u.GetDpiForWindow.argtypes, u.GetDpiForWindow.restype = (w.HWND,), w.UINT
            dpi = u.GetDpiForWindow(hwnd)
            if not dpi:
                raise ValueError("Cannot measure native display scale")
            build = preflight(root, request["head"], target, settings)
            observed_file = artifact / "observations.json"
            observed_file.write_text(json.dumps({"actions": actions, "observations": observations}), encoding="utf-8")
            receipt = {key: request[key] for key in ("head", "flow", "environment_id", "test_scope")}
            receipt.update(version=2, observations=observations, requests=[], actions=[], native={
                "application": target["application"], "host": "windows-win32", "driver": "win32-ctypes-v1",
                "executable_path_digest": hashlib.sha256(settings["executable"].encode()).hexdigest(),
                "executable_sha256": digest(Path(settings["executable"])),
                "build_head": request["head"], "build_inputs": build, "pid": proc.pid, "hwnd": int(hwnd),
                "window_class": class_name(hwnd), "actions": actions,
                "environment": {"os": platform.platform(), "runtime": platform.python_version(),
                                "dpi": dpi, "geometry": [geometry.left, geometry.top, geometry.right, geometry.bottom],
                                "display": [u.GetSystemMetrics(0), u.GetSystemMetrics(1)],
                                "profile": settings["profile"], "revisions": request["revisions"],
                                "desktop": request["desktop"]},
                "artifacts": [{"path": file.name, "sha256": digest(file)}
                              for file in [observed_file, artifact / "events.jsonl"] if file.exists()]})
            return receipt
        finally:
            if job:
                process.terminated(job)
            elif proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
                ownership["state"] = "cleaned"
                owned.write_text(json.dumps(ownership), encoding="utf-8")
            finally:
                k.CloseHandle(info.process)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    request = json.loads(Path(os.environ["WIKI_VERIFICATION_NATIVE_REQUEST"]).read_text(encoding="utf-8"))
    try:
        result = run(request)
    except (ValueError, OSError, subprocess.SubprocessError, AttributeError) as exc:
        result = {key: request[key] for key in ("head", "flow", "environment_id", "test_scope")}
        result["blocked"] = {"prerequisite": "native", "reason": str(exc)}
    print("```local-evidence\n" + json.dumps(result) + "\n```")
    return 0 if "blocked" not in result and all(row["pass"] for row in result["observations"]) else 1


if __name__ == "__main__":
    sys.exit(main())
