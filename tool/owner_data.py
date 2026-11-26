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
    """Roaming and Local AppData, as the known folders say and as the environment says where it differs. Spellings
    are told apart exactly: in a case-sensitive folder two that differ only in case are two folders, and one
    folder spelled twice is only watched twice."""

    found = [known(ROAMING), known(LOCAL), *(Path(os.environ[v]) for v in ("APPDATA", "LOCALAPPDATA") if os.environ.get(v))]
    return [Path(b) for b in dict.fromkeys(str(base) for base in found if base)]


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


def conf_names(conf: Path | bytes | None) -> list[str]:
    """One `tauri.conf.json`'s `identifier` and `productName`, from the file or the bytes read of it; none when
    it cannot be read."""

    try:
        value = json.loads(conf if isinstance(conf, bytes) else Path(conf).read_bytes())
    except (OSError, ValueError, TypeError):
        return []
    return [str(value[k]) for k in ("identifier", "productName") if isinstance(value, dict) and value.get(k)
            and str(value[k]) not in (".", "..")]


CONF = "tauri.conf.json".encode("utf-16-le")   # as a directory notice spells it


def _conf(relative: Path) -> bool:
    return relative.name.lower() == "tauri.conf.json" and not any(
        part in ("node_modules", "target", ".git") for part in relative.parts)


def _confs(tree: Path):
    for conf in Path(tree).glob("**/tauri.conf.json"):
        if _conf(conf.relative_to(tree)):
            yield conf


def _alias(name: str) -> str:
    """A name as Win32 may store it for another spelling: no trailing dots or spaces, case folded. Matching more
    often only makes a hit, never a clean window."""

    return name.rstrip(". ").casefold()


def _file_id(k, what) -> tuple[int, bytes] | None:
    """The volume and 128-bit file ID of an open handle, or of the folder a path leads to now (opened for it)."""

    handle = what if isinstance(what, int) else k.CreateFileW(str(what), 0x80, 0x7, None, 3, 0x02000000, None)
    if not handle or handle == w.HANDLE(-1).value:
        return None
    try:
        info = _FileId()
        ok = k.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info))   # FileIdInfo
        return (info.volume, bytes(info.id)) if ok else None
    finally:
        if handle is not what:
            k.CloseHandle(handle)


def _state(conf: Path) -> tuple[bytes, int] | None:
    """A config's bytes and its write time, read in that order through one handle, or `None` when it is gone."""

    try:
        with open(conf, "rb") as fh:
            return fh.read(), os.fstat(fh.fileno()).st_mtime_ns
    except OSError:
        return None


def roots(tree: Path) -> list[Path]:
    return [base / name for name in product_names(Path(tree)) for base in bases()]


def _plain(text: str) -> str:
    """One spelling of a path: forward slashes, no doubled ones, Git Bash's `/c/` as `c:/`, lower case."""

    text = re.sub(r"/+", "/", text.replace("\\", "/")).casefold()
    return re.sub(r"(?<![\w:])/([a-z])/", r"\1:/", text)


def named(text: str, found: list[Path]) -> list[Path]:
    """The roots `text` names: by absolute path or through `%APPDATA%`, `$LOCALAPPDATA`, `$env:LOCALAPPDATA`
    or `${env:APPDATA}`, either slash, any case. A variable alone names none."""

    plain = _plain(text)
    out = []
    for root in found:
        # The name, or the name with the trailing dots and spaces Win32 drops, but only where the component ends
        # there (a separator, a quote or the end): `Budget. Notes` is another folder. An unquoted argument ending
        # in such a run before more text is not told from that sibling; the refusal catches mistakes, and the
        # watch sees what any spelling writes.
        tail = re.escape(_alias(root.name)) + r"""(?:[. ]+(?=[/"'`]|$)|(?![\w.-]))"""
        # The parent without its own trailing separator, so a drive or share root is not doubled.
        absolute = re.escape(_plain(str(root.parent)).rstrip("/")) + "/" + tail
        # Either variable, whatever the base is called: a redirected Local folder keeps its refusal, and a
        # Roaming name spelled through the Local variable is the same product's, refused as well.
        spelled = rf"(%(local)?appdata%|\$\{{?(env:)?(local)?appdata\}}?)/{tail}"
        if re.search(absolute, plain) or re.search(spelled, plain):
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

class _FileId(ctypes.Structure):   # FILE_ID_INFO
    _fields_ = [("volume", ctypes.c_ulonglong), ("id", ctypes.c_ubyte * 16)]


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
    k.WaitForSingleObject.argtypes, k.WaitForSingleObject.restype = (w.HANDLE, w.DWORD), w.DWORD
    k.GetFileInformationByHandleEx.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
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

    Every name a product is given during the window counts, a name used only between two reads included: all of
    `tree` is watched, and a `tauri.conf.json` is read again on every change to it, a new one in a new folder
    included. Each name it gives becomes a root at once. One whose folder does not exist is the base watch's: a
    program makes that folder before it writes in it. One whose folder exists had no watch of its own, and nothing
    orders a product's run after this read, so the window is `incomplete`. A change no read saw is `incomplete`
    too: a config read back as it was although it was written (a name given and taken back before the read), one
    made and removed before it, and one changed while the tree's watch overflowed. The scope is the names the
    window shows (the owner's decision, plan 15a): a program built earlier under another name, a name given and
    replaced by another before one read, and a config whose whole life falls in lost notices are beyond it."""

    def __init__(self, found: list[Path], tree: Path | None = None):
        self.lock, self.sync_lock = threading.Lock(), threading.Lock()
        self.state, self.paths, self.why = "clean", [], []
        self.found, self.seen, self.bases, self.tree, self.absent = list(found), {}, set(), tree, []
        self.threads, self.syncs, self.stop, self.k = [], [], None, None
        self.confs, self.lost = {}, False   # each config as last read (its bytes and write time); notices lost
        if os.name != "nt":
            return
        self.k = _k32()
        self.stop = self.k.CreateEventW(None, True, False, None)
        if not self.stop:
            self._fail(f"감시를 시작하지 못했다 (Windows error {ctypes.get_last_error()})")
            return
        for base in map(Path, dict.fromkeys(str(root.parent) for root in found)):   # exact spellings, as `bases`
            if base.is_dir():
                self.bases.add(str(base))
                self._start(base, "base")
            else:
                self._fail(f"`{base}` 가 없어 감시하지 못했다")
        for root in found:
            if root.is_dir():
                self._start(root, "root")
            elif not root.exists():   # the base watch's; one there at a verdict was made in the window
                self._absent(root)
        if tree is not None:
            for conf in _confs(tree):
                self.confs[str(conf)] = _state(conf)
            self._start(Path(tree), "tree")

    def _fail(self, why: str) -> None:
        with self.lock:
            if self.state != "changed":
                self.state = "incomplete"
            if why not in self.why:   # a check repeated at every answer says it once
                self.why.append(why)

    def _changed(self, path: Path | None) -> None:
        with self.lock:
            self.state = "changed"
            if path is not None and len(self.paths) < MOST and path not in self.paths:
                self.paths.append(path)

    def _named(self, base: Path, name: str) -> None:
        """A directory name created, renamed or removed in a base folder: a change when it is a root's."""

        with self.lock:
            self.seen.setdefault(base, set()).add(_alias(name))
            hit = any(r.parent == base and _alias(r.name) == _alias(name) for r in self.found)
        if hit:
            self._changed(None)   # the root itself was created, renamed, removed or replaced

    def _absent(self, root: Path) -> None:
        """A root with no folder yet, left to its base's watch. A name in 8.3 short form (`BUDGET~1`) may stand
        for a folder whose notices carry its long name, so its base cannot see it come and go: `incomplete`."""

        if re.search(r"~\d", root.name):
            self._fail(f"`{root}` 는 짧은 이름 꼴이라, 그 폴더가 생기고 지워지는 것을 볼 수 없다")
        with self.lock:
            self.absent.append(root)

    def _cover(self, now: list[Path]) -> None:
        """Roots not watched so far. One whose folder exists had no watch of its own until now, and nothing orders
        a product's run after this: its inside went unwatched. One whose folder does not is the base watch's."""

        with self.lock:
            watched = {str(r) for r in self.found}   # never `Path` equality, which folds case
            new = list({str(r): r for r in now if str(r) not in watched}.values())
            self.found += new
            seen = {base: set(names) for base, names in self.seen.items()}
        for root in new:
            if str(root.parent) not in self.bases:
                self._fail(f"`{root}` 는 이 구간 중에 생긴 이름이고, 그 상위 폴더는 감시하지 않았다")
            elif _alias(root.name) in seen.get(root.parent, ()):
                self._changed(None)
            elif root.exists():
                self._fail(f"`{root}` 는 이 구간 중에 생긴 이름이라 그 안은 감시하지 못했다")
            else:
                self._absent(root)

    def _reread(self, conf: Path, lost: bool = False) -> None:
        """A `tauri.conf.json` changed (`lost`: maybe, its notices lost): each name it gives now is a root from now
        on, and a change this read cannot show leaves the window `incomplete`."""

        now = _state(conf)
        with self.lock:
            last = self.confs.get(str(conf), "unseen")
            self.confs[str(conf)] = now
        if now == last:   # a second notice of a change already read
            return
        if lost or (now is None and last == "unseen") or (now and last and last != "unseen" and now[0] == last[0]):
            self._fail(f"`{conf}` 가 이 구간 중에 바뀌었고, 그동안 쓰인 제품 이름을 다 알 수 없다")
        self._cover([base / name for name in conf_names(now and now[0]) for base in bases()])   # the bytes kept

    def _now(self, now: list[Path] | None) -> list[Path] | None:
        """The roots as they are now; first, after the tree's notices were lost, every config read again."""

        with self.lock:
            lost, self.lost, known = self.lost, False, list(self.confs)
        for conf in {*map(str, _confs(self.tree)), *known} if lost else ():
            self._reread(Path(conf), lost=True)
        with self.lock:
            absent = list(self.absent)
        if any(root.exists() for root in absent):   # Win32 resolves its spelling: made in the window
            self._changed(None)
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
        recursive = mode in ("root", "tree")
        which = {"root": SUBTREE, "base": 0x2, "tree": 0x1 | 0x8 | 0x10}[mode]
        k = self.k
        # A base is held without delete sharing: Windows then refuses to move or remove it, or its parent, for the
        # window, so the folder at its path stays the one its handle watches. What is inside changes as ever. The
        # cost: a program holding the base itself open with delete access makes the watch fail to start
        # (`incomplete`), and one opening it so during the window gets a sharing error. The hold keeps only the
        # folder the path resolved to, so each answer and the end compare the handle's own folder with the one the
        # path opens then (`answer`): a junction naming another, or a root's folder moved, is never taken for it.
        handle = k.CreateFileW(str(folder), 0x1, 0x3 if mode == "base" else 0x7, None, 3, 0x02000000 | 0x40000000,
                               None)
        if not handle or handle == w.HANDLE(-1).value:
            self._fail(f"`{folder}` 를 감시하려고 열지 못했다 (Windows error {ctypes.get_last_error()})")
            ready.set()
            return
        event = k.CreateEventW(None, True, False, None)
        sync = k.CreateEventW(None, False, False, None)   # `settled` asks; answered once nothing delivered is unread
        acked = threading.Event()

        def answer():   # first, the folder this handle watches is still the one its path leads to: the same file
            if mode != "tree":   # identity, never a spelling, so no case, prefix or alias can equate two folders
                if (held := _file_id(k, handle)) is None or (now := _file_id(k, folder)) is None:
                    self._fail(f"`{folder}` 가 가리키는 폴더를 확인하지 못했다")
                elif held != now:
                    self._fail(f"`{folder}` 가 가리키는 폴더가 이 구간 중에 바뀌어, 그 안은 감시하지 못했다")
            acked.set()
        entry = (sync, acked)
        buffer = ctypes.create_string_buffer((1024 if mode == "tree" else 64) * 1024)   # a build's writes are many
        overlapped = _Overlapped(event=event)
        try:
            if not event or not sync:
                self._fail(f"`{folder}` 를 감시하지 못했다 (Windows error {ctypes.get_last_error()})")
                return
            with self.lock:
                self.syncs.append(entry)
            def batch(n: int) -> None:   # one completed read of `n` bytes
                if n == 0:   # the buffer overflowed: what changed is not known
                    if mode == "tree":   # every config is read again at the next read; the watch goes on
                        with self.lock:
                            self.lost = True
                    else:
                        self._changed(None)
                    return
                if mode == "tree" and CONF not in buffer.raw[:n].lower():   # a build's own writes
                    return
                for name in _names(buffer.raw[:n]):
                    if mode == "root":
                        self._changed(folder / name)
                    elif mode == "base":
                        self._named(folder, name)
                    elif _conf(Path(name)):
                        self._reread(folder / name)

            def cancelled() -> None:   # the pending read ended; one that completed meanwhile is read as any other
                k.CancelIoEx(handle, ctypes.byref(overlapped))
                size = w.DWORD()
                if k.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(size), True):
                    batch(size.value)
                elif (error := ctypes.get_last_error()) != 995:   # ERROR_OPERATION_ABORTED: cancelled with nothing
                    self._fail(f"`{folder}` 의 마지막 변경을 읽지 못했다 (Windows error {error})")

            # A stream of changes keeps the event signalled, so a stop or sync seen between batches (`owed`) is
            # answered once a read issued after it caught up: after its batch (`due`), or when nothing came.
            owed, due = set(), set()
            while True:
                if "stop" in due:
                    return
                if due:
                    answer()
                k.ResetEvent(event)
                if not k.ReadDirectoryChangesW(handle, buffer, len(buffer), recursive, which,
                                               None, ctypes.byref(overlapped), None):
                    self._fail(f"`{folder}` 의 변경을 읽지 못했다 (Windows error {ctypes.get_last_error()})")
                    return
                ready.set()
                if owed and (waited := k.WaitForSingleObject(event, 100)) != 0:
                    if waited != 0x102:   # the wait failed: nothing says what came before them was read
                        self._fail(f"`{folder}` 의 감시가 멈췄다 (wait {waited:#x}, Windows error {ctypes.get_last_error()})")
                        cancelled()
                        return
                    if "stop" in owed:   # caught up: nothing before them is unread
                        cancelled()
                        return
                    answer()
                    owed = set()
                # The lowest signalled handle wins: a delivered change is read before a sync is answered.
                while (found := k.WaitForMultipleObjects(3, (w.HANDLE * 3)(event, stop, sync), False,
                                                         0xFFFFFFFF)) == 2:
                    answer()
                error, size = ctypes.get_last_error(), w.DWORD()
                if found != 0:   # closing, or the wait failed: the read is ended first, and read if it came
                    if found != 1:
                        self._fail(f"`{folder}` 의 감시가 멈췄다 (wait {found:#x}, Windows error {error})")
                    cancelled()
                    return
                if not k.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(size), False):
                    self._fail(f"`{folder}` 의 변경을 읽지 못했다 (Windows error {ctypes.get_last_error()})")
                    return
                due, owed = owed, {name for name, h in (("stop", stop), ("sync", sync))
                                   if k.WaitForSingleObject(h, 0) == 0}
                batch(size.value)
        finally:
            with self.lock:
                if entry in self.syncs:
                    self.syncs.remove(entry)
            answer()   # the end, too: its folder checked while the handle is still open
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
