"""The owner's real data folders, kept away from a build session (plan 15a, "The owner's real data folder").

The roots are, for each of `FOLDERID_RoamingAppData` and `FOLDERID_LocalAppData` and the `APPDATA` and
`LOCALAPPDATA` values where they differ, that folder joined with the repository's name and with the `identifier`
and `productName` of each `tauri.conf.json` in the worktree. A product keeping its data elsewhere is not covered.

- `verdict`, a pretool check on both hosts in a build session (`WIKI_AGENT_ROLE=build`): a shell call whose text
  names a root in any spelling, or an edit inside one, is refused. Naming only the variable is not.
- `Watch`: `ReadDirectoryChangesW` on each root's subtree, and on each base folder for a root itself being created,
  renamed, removed or replaced, a root named only during the window included. Nothing is walked; junctions are
  not followed. It says `clean`, `changed` with up
  to five paths (none for an overflow or the root itself), or `incomplete`.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import threading
from ctypes import wintypes as w
from pathlib import Path

ROAMING, LOCAL = "3EB685DB-65F9-4CF6-A03A-E3EF65729F3D", "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"
MOST = 5
EDITS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELLS = {"Bash", "PowerShell", "Monitor", "exec_command", "shell_command", "shell", "local_shell"}
REFUSED = ("주인의 실제 데이터 폴더를 건드리는 호출을 막았다 — {roots}. 빌드는 그 폴더를 쓰지 않는다. "
           "프로그램을 띄울 때 데이터 폴더는 `{agent}` 를 쓴다 (계약의 `app.run` 이 `{{data}}` 를 넘기는 방식 그대로).")


def known(guid: str) -> Path | None:
    if os.name != "nt":
        return None
    import uuid

    shell = ctypes.WinDLL("shell32")
    shell.SHGetKnownFolderPath.argtypes = (ctypes.c_char_p, w.DWORD, w.HANDLE, ctypes.POINTER(w.LPWSTR))
    raw = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
    path = w.LPWSTR()
    if shell.SHGetKnownFolderPath(ctypes.cast(raw, ctypes.c_char_p), 0, None, ctypes.byref(path)):
        return None
    try:
        return Path(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


def bases() -> list[Path]:
    """Roaming and Local AppData, as the known folders say and as the environment says where it differs."""

    found = [known(ROAMING), known(LOCAL), *(Path(os.environ[v]) for v in ("APPDATA", "LOCALAPPDATA") if os.environ.get(v))]
    out: list[Path] = []
    for base in found:
        if base and all(os.path.normcase(str(base)) != os.path.normcase(str(o)) for o in out):
            out.append(base)
    return out


def product_names(tree: Path) -> list[str]:
    """The repository's own name (its main checkout's folder) and each `tauri.conf.json`'s identifier and name."""

    names = []
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=tree,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        if common:
            names.append(Path(common).parent.name)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for conf in _confs(tree):
        names += conf_names(conf)
    return list(dict.fromkeys(n for n in names if n and n not in (".", "..")))


def conf_names(conf: Path) -> list[str]:
    """One `tauri.conf.json`'s `identifier` and `productName`; none when it cannot be read."""

    try:
        value = json.loads(conf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(value[k]) for k in ("identifier", "productName") if isinstance(value, dict) and value.get(k)
            and str(value[k]) not in (".", "..")]


def _confs(tree: Path):
    for conf in Path(tree).glob("**/tauri.conf.json"):
        if not any(part in ("node_modules", "target", ".git") for part in conf.relative_to(tree).parts):
            yield conf


def roots(tree: Path) -> list[Path]:
    return [base / name for name in product_names(Path(tree)) for base in bases()]


def _plain(text: str) -> str:
    """One spelling of a path: forward slashes, no doubled ones, Git Bash's `/c/` as `c:/`, lower case."""

    text = re.sub(r"/+", "/", text.replace("\\", "/")).lower()
    return re.sub(r"(?<![\w:])/([a-z])/", r"\1:/", text)


def named(text: str, found: list[Path]) -> list[Path]:
    """The roots `text` names: by absolute path or through `%APPDATA%`, `$LOCALAPPDATA`, `$env:LOCALAPPDATA`
    or `${env:APPDATA}`, either slash, any case. A variable alone names none."""

    plain = _plain(text)
    out = []
    for root in found:
        tail = re.escape(root.name.lower())
        end = r"(?![\w.-])"
        absolute = re.escape(_plain(str(root)))
        variable = "local" if root.parent.name.lower() == "local" else ""
        spelled = rf"(%{variable}appdata%|\$\{{?(env:)?{variable}appdata\}}?)/{tail}{end}"
        if re.search(absolute + end, plain) or re.search(spelled, plain):
            out.append(root)
    return out


def verdict(payload: dict) -> dict | None:
    """In a build session: a refusal for a call that names an owner's data root; else `None`."""

    if os.environ.get("WIKI_AGENT_ROLE") != "build":
        return None
    tool = str(payload.get("tool_name") or "").removeprefix("functions.")
    given = payload.get("tool_input") or {}
    if not isinstance(given, dict):
        return None
    if tool in SHELLS:
        text = given.get("command", given.get("cmd", ""))
        text = " ".join(map(str, text)) if isinstance(text, list) else str(text)
    elif tool in EDITS:
        text = str(given.get("file_path") or given.get("notebook_path") or "")
    elif tool == "apply_patch":
        text = str(given.get("input") or given.get("patch") or given.get("command") or "")
    else:
        return None
    tree = Path(str(payload.get("cwd") or os.getcwd()))
    hit = named(text, roots(tree))
    if not hit:
        return None
    reason = REFUSED.format(roots=", ".join(f"`{r}`" for r in hit), agent=agent_folder(tree) or "measure-app\\agent")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def agent_folder(tree: Path) -> str:
    """The run's `measure-app\\agent`, where a build session's launches keep their data (`guard_grant`)."""

    import guard_grant

    try:
        found = guard_grant.measure_agent(Path(tree))
    except Exception:  # noqa: BLE001 -- only named in the refusal
        return ""
    return str(found) if found else ""


# -- The watch --------------------------------------------------------------------------------------------------------

class _Overlapped(ctypes.Structure):
    _fields_ = [("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t), ("offset", w.DWORD),
                ("offset_high", w.DWORD), ("event", w.HANDLE)]


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
    k.CreateFileW.restype = w.HANDLE
    k.ReadDirectoryChangesW.argtypes = (w.HANDLE, w.LPVOID, w.DWORD, w.BOOL, w.DWORD, w.LPVOID,
                                        ctypes.POINTER(_Overlapped), w.LPVOID)
    k.GetOverlappedResult.argtypes = (w.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(w.DWORD), w.BOOL)
    k.CancelIoEx.argtypes = (w.HANDLE, ctypes.POINTER(_Overlapped))
    k.CreateEventW.argtypes, k.CreateEventW.restype = (w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR), w.HANDLE
    k.SetEvent.argtypes = k.ResetEvent.argtypes = k.CloseHandle.argtypes = (w.HANDLE,)
    k.WaitForMultipleObjects.argtypes = (w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD)
    k.WaitForMultipleObjects.restype = w.DWORD
    return k


SUBTREE = 0x1 | 0x2 | 0x8 | 0x10   # FILE_NOTIFY_CHANGE_FILE_NAME | DIR_NAME | SIZE | LAST_WRITE


class Watch:
    """The roots watched from construction until `close`. The first read of every directory is issued before
    the constructor returns, so nothing written after it goes unseen. Off Windows nothing is watched.

    Each base folder (the roots' parents) is watched for every directory name created, renamed or removed in
    it, so a root that only came to exist during the window (a `tauri.conf.json` identifier the turn added) is
    covered too: `settled` and `close` take the roots as they are now (`now`, else read off `tree`). One of
    them seen at its base is a change; one that already existed, and so had no watch of its own, leaves the
    window `incomplete`.

    Every name a product is given during the window counts, a name used only between two reads included: the
    folder of each `tauri.conf.json` of `tree` is watched, and the file is read again on every change to it.
    Each name it gives becomes a root at once. One whose folder does not exist is the base watch's: a program
    makes that folder before it writes in it. One whose folder exists had no watch of its own, and nothing orders
    a product's run after this read, so the window is `incomplete`. A config watch that overflowed lost names,
    which leaves it `incomplete` too."""

    def __init__(self, found: list[Path], tree: Path | None = None):
        self.lock, self.sync_lock = threading.Lock(), threading.Lock()
        self.state, self.paths, self.why = "clean", [], []
        self.found, self.seen, self.bases, self.tree = list(found), {}, set(), tree
        self.threads, self.syncs, self.stop, self.k = [], [], None, None
        if os.name != "nt":
            return
        self.k = _k32()
        self.stop = self.k.CreateEventW(None, True, False, None)
        if not self.stop:
            self._fail(f"감시를 시작하지 못했다 (Windows error {ctypes.get_last_error()})")
            return
        for base in dict.fromkeys(root.parent for root in found):
            if base.is_dir():
                self.bases.add(base)
                self._start(base, "base")
            else:
                self._fail(f"`{base}` 가 없어 감시하지 못했다")
        for root in found:
            if root.is_dir():
                self._start(root, "root")
        for folder in dict.fromkeys(conf.parent for conf in _confs(tree)) if tree is not None else ():
            self._start(folder, "config")

    def _fail(self, why: str) -> None:
        with self.lock:
            if self.state != "changed":
                self.state = "incomplete"
            self.why.append(why)

    def _changed(self, path: Path | None) -> None:
        with self.lock:
            self.state = "changed"
            if path is not None and len(self.paths) < MOST and path not in self.paths:
                self.paths.append(path)

    def _named(self, base: Path, name: str) -> None:
        """A directory name created, renamed or removed in a base folder: a change when it is a root's."""

        with self.lock:
            self.seen.setdefault(base, set()).add(name.lower())
            hit = any(r.parent == base and r.name.lower() == name.lower() for r in self.found)
        if hit:
            self._changed(None)   # the root itself was created, renamed, removed or replaced

    def _cover(self, now: list[Path]) -> None:
        """Roots not watched so far. One whose folder exists had no watch of its own until now, and nothing orders
        a product's run after this: its inside went unwatched. One whose folder does not is the base watch's."""

        with self.lock:
            new = [r for r in now if r not in self.found]
            self.found += new
            seen = {base: set(names) for base, names in self.seen.items()}
        for root in new:
            if root.parent not in self.bases:
                self._fail(f"`{root}` 는 이 구간 중에 생긴 이름이고, 그 상위 폴더는 감시하지 않았다")
            elif root.name.lower() in seen.get(root.parent, ()):
                self._changed(None)
            elif root.exists():
                self._fail(f"`{root}` 는 이 구간 중에 생긴 이름이라 그 안은 감시하지 못했다")

    def _named_by(self, conf: Path) -> None:
        """A `tauri.conf.json` changed: each name it gives now is a root from now on."""

        self._cover([base / name for name in conf_names(conf) for base in bases()])

    def _now(self, now: list[Path] | None) -> list[Path] | None:
        return now if now is not None or self.tree is None else roots(self.tree)

    def _start(self, folder: Path, mode: str) -> None:
        """A watcher on `folder`: `root` (its subtree), `base` (directory names in it) or `config` (its files).
        Only the constructor starts one, so `settled` and `close` see every watcher there is."""

        ready = threading.Event()
        thread = threading.Thread(target=self._run, args=(folder, mode, ready, self.stop), daemon=True,
                                  name=f"owner-watch-{folder.name}")
        thread.start()
        ready.wait()
        self.threads.append(thread)

    def _run(self, folder: Path, mode: str, ready: threading.Event, stop) -> None:
        recursive = mode == "root"
        which = {"root": SUBTREE, "base": 0x2, "config": 0x1 | 0x8 | 0x10}[mode]
        k = self.k
        handle = k.CreateFileW(str(folder), 0x1, 0x7, None, 3, 0x02000000 | 0x40000000, None)
        if not handle or handle == w.HANDLE(-1).value:
            self._fail(f"`{folder}` 를 감시하려고 열지 못했다 (Windows error {ctypes.get_last_error()})")
            ready.set()
            return
        event = k.CreateEventW(None, True, False, None)
        sync = k.CreateEventW(None, False, False, None)   # `settled` asks; answered once nothing delivered is unread
        acked = threading.Event()
        entry = (sync, acked)
        buffer = ctypes.create_string_buffer(64 * 1024)
        overlapped = _Overlapped(event=event)
        try:
            if not event or not sync:
                self._fail(f"`{folder}` 를 감시하지 못했다 (Windows error {ctypes.get_last_error()})")
                return
            with self.lock:
                self.syncs.append(entry)
            while True:
                k.ResetEvent(event)
                if not k.ReadDirectoryChangesW(handle, buffer, len(buffer), recursive, which,
                                               None, ctypes.byref(overlapped), None):
                    self._fail(f"`{folder}` 의 변경을 읽지 못했다 (Windows error {ctypes.get_last_error()})")
                    return
                ready.set()
                # The lowest signalled handle wins: a delivered change is read before a sync is answered.
                while (found := k.WaitForMultipleObjects(3, (w.HANDLE * 3)(event, stop, sync), False,
                                                         0xFFFFFFFF)) == 2:
                    acked.set()
                error, size = ctypes.get_last_error(), w.DWORD()
                if found != 0:   # closing, or the wait failed: the read is cancelled and waited for first
                    k.CancelIoEx(handle, ctypes.byref(overlapped))
                    k.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(size), True)
                    if found != 1:
                        self._fail(f"`{folder}` 의 감시가 멈췄다 (wait {found:#x}, Windows error {error})")
                    return
                if not k.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(size), False):
                    self._fail(f"`{folder}` 의 변경을 읽지 못했다 (Windows error {ctypes.get_last_error()})")
                    return
                if size.value == 0:   # the buffer overflowed: what changed is not known
                    if mode == "config":   # names given and taken back in it are lost; the watch goes on
                        self._fail(f"`{folder}` 의 변경 기록이 넘쳐, 그동안 쓰인 제품 이름을 다 알 수 없다")
                        self._named_by(folder / "tauri.conf.json")
                    else:
                        self._changed(None)
                    continue
                for name in _names(buffer.raw[:size.value]):
                    if mode == "root":
                        self._changed(folder / name)
                    elif mode == "base":
                        self._named(folder, name)
                    elif name.lower() == "tauri.conf.json":
                        self._named_by(folder / name)
        finally:
            with self.lock:
                if entry in self.syncs:
                    self.syncs.remove(entry)
            acked.set()
            ready.set()
            for h in (event, sync, handle):
                if h:
                    k.CloseHandle(h)

    def result(self) -> dict:
        with self.lock:
            return {"state": self.state, "paths": [str(p) for p in self.paths], "why": list(self.why),
                    "roots": [str(r) for r in self.found]}

    def settled(self, now: list[Path] | None = None) -> dict:
        """What the window holds so far, once every change already delivered has been read; `now`, the roots as
        they are now. A check reads this right before it saves."""

        with self.sync_lock:
            if self.stop is not None:
                failed = []
                with self.lock:   # a worker leaves `syncs` under this lock before it closes its handles
                    pending = list(self.syncs)
                    for sync, acked in pending:
                        acked.clear()
                        if not self.k.SetEvent(sync):
                            failed.append(ctypes.get_last_error())
                for error in failed:
                    self._fail(f"감시에 묻지 못했다 (Windows error {error})")
                if not all(acked.wait(10) for _, acked in pending):
                    self._fail("감시가 제때 답하지 않았다")
        if (now := self._now(now)) is not None:
            self._cover(now)
        return self.result()

    def close(self, now: list[Path] | None = None) -> dict:
        """End every read, each after the changes already delivered, and say what was seen; a second call only
        says it again."""

        with self.sync_lock:
            if self.stop is not None:
                self.k.SetEvent(self.stop)
                for thread in self.threads:
                    thread.join(10)
                    if thread.is_alive():
                        self._fail("감시를 끝내지 못했다")
                if not any(t.is_alive() for t in self.threads):   # one still running may yet wait on it
                    self.k.CloseHandle(self.stop)   # ponytail: else the handle leaks, one per stuck watch
                self.stop = None
        if (now := self._now(now)) is not None:
            self._cover(now)
        return self.result()


def _names(raw: bytes) -> list[str]:
    """The names in a `FILE_NOTIFY_INFORMATION` chain."""

    out, at = [], 0
    while at + 12 <= len(raw):
        following, _, length = (int.from_bytes(raw[at + i:at + i + 4], "little") for i in (0, 4, 8))
        out.append(raw[at + 12:at + 12 + length].decode("utf-16-le", "replace"))
        if not following:
            break
        at += following
    return out


def reason(result: dict) -> str:
    """"" for `clean`; else the refusal's text, with the folders and the paths."""

    if result["state"] == "clean":
        return ""
    folders = ", ".join(f"`{r}`" for r in result["roots"]) or "(없음)"
    if result["state"] == "changed":
        paths = "; ".join(f"`{p}`" for p in result["paths"]) or "(경로를 알 수 없다: 변경이 너무 많거나 폴더 자체가 바뀌었다)"
        return f"이 턴 동안 주인의 실제 데이터 폴더가 바뀌었다 — {folders}: {paths}. 빌드는 그 폴더를 쓰지 않는다"
    return f"이 턴 동안 주인의 실제 데이터 폴더를 다 감시하지 못했다 — {folders}: {'; '.join(result['why'])}"
