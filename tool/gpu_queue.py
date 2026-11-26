"""One GPU job at a time on this machine, in the order the jobs asked (plan 15a, "The queue's order and the lease").

Every participant follows the same protocol: the hook's runner (`gpu_run`) for an agent's GPU job, and the
server's own launches on the thread that runs them.

- Whose. The queue belongs to the account owning the hub checkout's folder. A process of any other account, the
  Codex sandbox's among them, runs nothing and creates nothing (`joinable`).
- Where. `wiki-agent-data\\gpu` under the owner's `FOLDERID_LocalAppData`, never `TEMP` or `LOCALAPPDATA`, so a
  shell and the server's gate, which points `TEMP` elsewhere, see one queue.
- The lease, mutex `Local\\wiki-agent-gpu`, is the only permission to run, owned by the thread that runs the job.
- The holder note, `gpu\\holder`, survives a holder that ends without cleaning up: written right after the lease
  is taken, it names the holder and the job's root; an end proven clean clears it, an unproven one leaves the
  processes not proven gone, or `inconclusive`. Every acquisition reads it first and waits for what it names.
- A place gives the order: under mutex `Local\\wiki-agent-gpu-queue` a taker raises the counter in `gpu\\next` and
  creates `gpu\\queue\\<counter>` with no sharing, held open while it waits and runs (and deleted on close). The
  head is the lowest place a live process holds; a place that opens belongs to no one and is removed; any other
  error stops the queue until it can be decided. Nothing is timed out anywhere.
- The head, holding the lease, waits for the free-memory floor (`machine.floor`) unless it is the owner's round.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path

import machine
import native_process

HUB = Path(__file__).resolve().parent.parent
POLL = 0.5   # seconds between looks at the queue and at a note's processes: a pace, never a limit
FLOOR_EVERY = 2.0
INCONCLUSIVE = "GPU 작업의 끝을 증명하지 못했다 — 확인 후 지우기"
UNJOINABLE = "this account cannot join the GPU queue"
SHARING, MISSING, NO_PATH = 32, 2, 3
WAIT_TIMEOUT, WAIT_FAILED = 0x102, 0xFFFFFFFF
INFINITE = 0xFFFFFFFF


class Stopped(Exception):
    """A watched process ended, the stop was set, or the caller's halt: the job runs nothing more."""


class Refused(Exception):
    """This process may not take part: another account, or the queue could not be prepared."""


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes, k.CreateMutexW.restype = (w.LPVOID, w.BOOL, w.LPCWSTR), w.HANDLE
    k.CreateEventW.argtypes, k.CreateEventW.restype = (w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR), w.HANDLE
    k.OpenEventW.argtypes, k.OpenEventW.restype = (w.DWORD, w.BOOL, w.LPCWSTR), w.HANDLE
    k.SetEvent.argtypes = (w.HANDLE,)
    k.ReleaseMutex.argtypes = (w.HANDLE,)
    k.WaitForMultipleObjects.argtypes = (w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD)
    k.WaitForMultipleObjects.restype = w.DWORD
    k.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
    k.CreateFileW.restype = w.HANDLE
    k.CloseHandle.argtypes = (w.HANDLE,)
    k.GetCurrentProcess.restype = w.HANDLE
    k.OpenProcess.argtypes, k.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    return k


K = _k32() if os.name == "nt" else None
BAD = w.HANDLE(-1).value


# -- Whose queue, and where -----------------------------------------------------------------------------------------

def _sid_text(sid) -> str:
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.ConvertSidToStringSidW.argtypes = (w.LPVOID, ctypes.POINTER(w.LPWSTR))
    text = w.LPWSTR()
    if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise OSError(ctypes.get_last_error(), "cannot read a SID")
    try:
        return text.value
    finally:
        ctypes.windll.kernel32.LocalFree(text)


def owner_sid(folder: Path = HUB) -> str:
    """The SID owning `folder` (the hub checkout's), read with `GetNamedSecurityInfo`."""

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = (w.LPCWSTR, ctypes.c_int, w.DWORD, ctypes.POINTER(w.LPVOID),
                                          w.LPVOID, w.LPVOID, w.LPVOID, ctypes.POINTER(w.LPVOID))
    owner, descriptor = w.LPVOID(), w.LPVOID()
    error = adv.GetNamedSecurityInfoW(str(folder), 1, 1, ctypes.byref(owner), None, None, None,
                                      ctypes.byref(descriptor))   # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION
    if error:
        raise OSError(error, f"cannot read the owner of {folder}")
    try:
        return _sid_text(owner)
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)


def user_sid() -> str:
    """This process token's user SID."""

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenProcessToken.argtypes = (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
    adv.GetTokenInformation.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))
    token = w.HANDLE()
    if not adv.OpenProcessToken(K.GetCurrentProcess(), 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
        raise OSError(ctypes.get_last_error(), "cannot open this process's token")
    try:
        buffer, size = ctypes.create_string_buffer(512), w.DWORD()
        if not adv.GetTokenInformation(token, 1, buffer, 512, ctypes.byref(size)):   # TokenUser
            raise OSError(ctypes.get_last_error(), "cannot read this process's user")
        return _sid_text(ctypes.cast(buffer, ctypes.POINTER(w.LPVOID))[0])
    finally:
        K.CloseHandle(token)


def joinable() -> str:
    """"" when this process's account owns the hub checkout; else why it runs nothing. Read before anything of
    the queue is resolved or created."""

    if os.name != "nt":
        return "the GPU queue runs on Windows only"
    try:
        mine, owner = user_sid(), owner_sid()
    except OSError as error:
        return f"{UNJOINABLE}: the hub checkout's owner cannot be read ({error})"
    return "" if mine == owner else f"{UNJOINABLE}: it runs as {mine}, the hub checkout belongs to {owner}"


def _local_appdata() -> Path:
    shell = ctypes.WinDLL("shell32")
    shell.SHGetKnownFolderPath.argtypes = (ctypes.c_char_p, w.DWORD, w.HANDLE, ctypes.POINTER(w.LPWSTR))
    guid = (ctypes.c_byte * 16).from_buffer_copy(
        __import__("uuid").UUID("F1B32785-6FBA-4FCF-9D55-7B8E7F157091").bytes_le)   # FOLDERID_LocalAppData
    path = w.LPWSTR()
    if shell.SHGetKnownFolderPath(ctypes.cast(guid, ctypes.c_char_p), 0, None, ctypes.byref(path)):
        raise Refused("cannot read the owner's LocalAppData folder")
    try:
        return Path(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


def home() -> Path:
    """The queue's folder. `WIKI_AGENT_GPU_HOME` gives a test a queue of its own, with mutexes of its own."""

    if given := os.environ.get("WIKI_AGENT_GPU_HOME"):
        return Path(given)
    return _local_appdata() / "wiki-agent-data" / "gpu"


def wrapped(job_uuid: str) -> Path:
    """`gpu\\wrapped\\<uuid>`: the app's session `job_uuid` queued a GPU job (`gpu_job.mark`)."""

    return home() / "wrapped" / (job_uuid or "none")


def names(folder: Path) -> tuple[str, str, str]:
    """(lease, queue mutex, stop event prefix) for the queue in `folder`."""

    if not os.environ.get("WIKI_AGENT_GPU_HOME"):
        return "Local\\wiki-agent-gpu", "Local\\wiki-agent-gpu-queue", "Local\\wiki-agent-gpu-stop-"
    tag = hashlib.sha256(str(folder).lower().encode()).hexdigest()[:12]
    return f"Local\\wiki-agent-gpu-{tag}", f"Local\\wiki-agent-gpu-queue-{tag}", f"Local\\wiki-agent-gpu-{tag}-stop-"


def stop_event(counter: int, folder: Path | None = None) -> str:
    return names(folder or home())[2] + str(counter)


# -- The session a process belongs to ---------------------------------------------------------------------------------

SESSIONS = {"claude.exe", "codex.exe"}


def ancestry(pid: int | None = None) -> tuple[dict | None, list[dict]]:
    """(the nearest ancestor that is the Claude or Codex program, the processes between it and `pid`, nearest
    first). Each step's parent was created before its child, so a reused parent pid ends the walk."""

    parents, chain = native_process.parents(), []
    here = native_process.identity(pid or os.getpid(), tree=parents)
    while here:
        up = parents.get(here["pid"])
        above = native_process.identity(up, tree=parents) if up else None
        if not above or above["created"] > here["created"]:
            return None, chain
        if Path(above["exe"]).name.lower() in SESSIONS:
            return above, chain
        chain.append(above)
        here = above
        if len(chain) > 32:
            return None, chain
    return None, chain


# -- The note's processes ---------------------------------------------------------------------------------------------

def gone(member: dict) -> bool:
    """`member` (an identity) has exited: no process has its pid, or the one that has is another."""

    now = native_process.identity(int(member["pid"]))
    return not native_process.same(member, now)


def unproven(proof: dict | None, members: list[dict]) -> tuple[list[dict], bool]:
    """After a job's end: (the members not proven to have exited, whether the proof cannot cover every process).
    Inconclusive when the job counted more processes than reported joining, the count was unread, or a joined
    member has no identity (a root rolled back before its identity was taken)."""

    left = [m for m in members if not gone(m)]
    if proof is None:
        return left, False
    total, joined = proof.get("total"), int(proof.get("joined") or 0)
    blind = total is None or total > joined or len(proof.get("members") or []) < joined \
        or any("refused at start" in r for r in proof.get("reasons") or [])
    return left, blind


# -- One participant --------------------------------------------------------------------------------------------------

class Holder:
    """One job's place, lease and note. Every method but `stop_line` runs on one thread: the lease is that
    thread's. `watch` is [(name, handle)] that end the wait when signalled (the stop event, the shell, the session);
    `halt` an Event checked between waits. `say` gets each line for whoever watches; `ahead` the count in front."""

    def __init__(self, label: str, watch=(), halt=None, floor: bool = True, say=print, ahead=None, who: dict | None = None):
        if why := joinable():
            raise Refused(why)
        self.label, self.watch, self.halt, self.use_floor = label, list(watch), halt, floor
        self.say, self.ahead_to, self.who = say, ahead, who or {}
        self.folder = home()
        self.lease_name, self.queue_name, self.stop_prefix = names(self.folder)
        (self.folder / "queue").mkdir(parents=True, exist_ok=True)
        self.counter = self.place = self.lease = None
        self.root_identity, self.proof, self.members = None, None, []
        self.me = native_process.identity(os.getpid())

    # The waits. `_wait` returns the index of the first of `handles` signalled, abandoned counted as signalled,
    # or `None` once `seconds` pass; a watched handle or the halt raises `Stopped` first.
    def _wait(self, handles: list, seconds: float | None) -> int | None:
        if self.halt is not None and self.halt.is_set():
            raise Stopped("halted")
        every = handles + [h for _, h in self.watch]
        ms = INFINITE if seconds is None else int(seconds * 1000)
        if self.halt is not None:   # an Event cannot be waited on with the handles: looked at every `POLL`
            ms = min(ms, int(POLL * 1000))
        if not every:
            time.sleep(ms / 1000)
            found = WAIT_TIMEOUT
        else:
            found = K.WaitForMultipleObjects(len(every), (w.HANDLE * len(every))(*every), False, ms)
        if found == WAIT_FAILED:
            raise Refused(f"cannot wait: Windows error {ctypes.get_last_error()}")
        if found == WAIT_TIMEOUT:
            if self.halt is not None and self.halt.is_set():
                raise Stopped("halted")
            return None
        index = found - 0x80 if found >= 0x80 else found
        if index >= len(handles):
            raise Stopped(self.watch[index - len(handles)][0])
        return index

    def _pause(self, seconds: float = POLL) -> None:
        self._wait([], seconds)

    def _locked(self, step):
        """`step()` under the queue mutex, taken by this thread and released after."""

        mutex = K.CreateMutexW(None, False, self.queue_name)
        if not mutex:
            raise Refused(f"cannot open the queue mutex: Windows error {ctypes.get_last_error()}")
        try:
            while self._wait([mutex], None) is None:
                pass
            try:
                return step()
            finally:
                K.ReleaseMutex(mutex)
        finally:
            K.CloseHandle(mutex)

    def take(self) -> int:
        """A place: the counter raised and the place file created with no sharing, held open; its `.who` after."""

        def taken():
            counter_file = self.folder / "next"
            try:
                counter = int(counter_file.read_text(encoding="utf-8").strip() or 0) + 1
            except FileNotFoundError:
                counter = 1
            except ValueError as error:
                raise Refused(f"`{counter_file}` is unreadable ({error}) — 확인 후 지우기") from None
            part = counter_file.with_suffix(f".{os.getpid()}.part")
            part.write_text(str(counter), encoding="utf-8")
            os.replace(part, counter_file)
            path = self.folder / "queue" / str(counter)
            # GENERIC_READ | DELETE, no sharing, CREATE_NEW, deleted on close: held only while this process is.
            handle = K.CreateFileW(str(path), 0x80000000 | 0x00010000, 0, None, 1, 0x80 | 0x04000000, None)
            if handle == BAD or not handle:
                raise Refused(f"cannot create the place `{path}`: Windows error {ctypes.get_last_error()}")
            return counter, handle

        self.counter, self.place = self._locked(taken)
        who = self.folder / "queue" / f"{self.counter}.who"
        who.write_text(json.dumps({**self.who, "label": self.label, "holder": self.me}), encoding="utf-8")
        return self.counter

    def _scan(self) -> tuple[int, list[str]]:
        """(live places ahead of this one, the places that could not be decided), removing places no one holds."""

        ahead, stuck = 0, []

        def scan():
            nonlocal ahead
            for who in (self.folder / "queue").glob("*.who"):   # a runner killed outright leaves its `.who`
                if not who.with_suffix("").exists():
                    who.unlink(missing_ok=True)
            for path in sorted((p for p in (self.folder / "queue").iterdir() if p.name.isdigit()),
                               key=lambda p: int(p.name)):
                if int(path.name) >= self.counter:
                    break
                state = probe(path)
                if state == "live":
                    ahead += 1
                elif state == "free":
                    path.unlink(missing_ok=True)
                    path.with_name(path.name + ".who").unlink(missing_ok=True)
                elif state == "gone":
                    path.with_name(path.name + ".who").unlink(missing_ok=True)
                else:
                    stuck.append(f"{path} ({state})")

        self._locked(scan)
        return ahead, stuck

    def lead(self) -> None:
        """Wait until this place is the head: no live or undecided place below it."""

        shown = None
        while True:
            ahead, stuck = self._scan()
            if not ahead and not stuck:
                return
            line = (f"GPU queue stopped: place {', '.join(stuck)} cannot be decided — 확인 후 지우기 once its "
                    "process is gone" if stuck else f"GPU 대기 중 — 앞에 {ahead}개 (place {self.counter})")
            if line != shown:
                self.say(line)
                shown = line
            if self.ahead_to:
                self.ahead_to(ahead + len(stuck))
            self._pause()

    def take_lease(self) -> None:
        """The lease, then the note any earlier holder left, then the floor; the note names this holder."""

        mutex = K.CreateMutexW(None, False, self.lease_name)
        if not mutex:
            raise Refused(f"cannot open the GPU lease: Windows error {ctypes.get_last_error()}")
        try:
            while self._wait([mutex], None) is None:
                pass
        except BaseException:
            K.CloseHandle(mutex)
            raise
        self.lease = mutex
        if self.ahead_to:
            self.ahead_to(0)
        self._settle_note()
        if self.use_floor:
            self._floor()
        self._write_note([self.me])

    def _settle_note(self) -> None:
        note, shown = self.folder / "holder", None
        while True:
            try:
                left = json.loads(note.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            except (OSError, ValueError):
                left = {"inconclusive": True, "processes": []}
            if left.get("inconclusive"):
                line = f"{INCONCLUSIVE}: `{note}`"
            else:
                alive = [p for p in left.get("processes") or [] if not gone(p)]
                if not alive:
                    note.unlink(missing_ok=True)
                    return
                line = "GPU lease waits for the last holder's processes to exit: " + ", ".join(
                    f"{Path(p.get('exe') or '?').name} {p['pid']}" for p in alive)
            if line != shown:
                self.say(line)
                shown = line
            self._pause()

    def _floor(self) -> None:
        shown = None
        while True:
            short = machine.floor(machine.sample(fresh=True))
            if not short:
                return
            line = f"GPU job waits for free memory: {short}"
            if (key := re.sub(r"[\d.]+", "#", line)) != shown:   # said again when what is short changes
                self.say(line)
                shown = key
            self._pause(FLOOR_EVERY)

    def _write_note(self, processes: list[dict], inconclusive: bool = False) -> None:
        note = self.folder / "holder"
        part = note.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part")
        part.write_text(json.dumps({"label": self.label, "holder": self.me, "root": self.root_identity,
                                    "processes": [p for p in processes if p], "inconclusive": inconclusive}),
                        encoding="utf-8")
        os.replace(part, note)

    # What the caller reports about its job.
    def root(self, identity: dict) -> None:
        """The job's root, still suspended: the note names it before it runs."""

        self.root_identity = identity
        self._write_note([self.me, identity])

    def ended(self, proof: dict, members: list[dict] | None = None) -> None:
        """The job's end proof (`owned_job.Job.end`), with every member it learned when `proof` lacks them."""

        self.proof, self.members = proof, members if members is not None else list(proof.get("members") or [])

    def finish(self) -> None:
        """Clear the note after a clean end, or leave the processes not proven gone; release the lease; drop the
        place. Every step is tried whatever an earlier one did."""

        try:
            if self.lease is not None:
                if self.root_identity is None or (self.proof or {}).get("clean"):
                    (self.folder / "holder").unlink(missing_ok=True)
                else:
                    members = self.members or [self.root_identity]
                    left, blind = unproven(self.proof, members)
                    self._write_note(left, blind)
                    if left or blind:
                        self.say("GPU job's end not proven" + (" (inconclusive)" if blind else "") + ": "
                                 + ", ".join(f"{Path(p.get('exe') or '?').name} {p['pid']}" for p in left))
                    else:
                        (self.folder / "holder").unlink(missing_ok=True)
        finally:
            try:
                if self.lease is not None:
                    K.ReleaseMutex(self.lease)
                    K.CloseHandle(self.lease)
                    self.lease = None
            finally:
                self.drop()

    def drop(self) -> None:
        if self.place is not None:
            K.CloseHandle(self.place)   # deleted on close
            self.place = None
            try:
                (self.folder / "queue" / f"{self.counter}.who").unlink(missing_ok=True)
            except OSError:
                pass


def probe(path: Path) -> str:
    """`live` (held by a process), `free` (opens: no one holds it), `gone`, or the Windows error number."""

    handle = K.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)   # read, share all, OPEN_EXISTING
    if handle and handle != BAD:
        K.CloseHandle(handle)
        return "free"
    error = ctypes.get_last_error()
    return {SHARING: "live", MISSING: "gone", NO_PATH: "gone"}.get(error, f"Windows error {error}")


@contextmanager
def held(label: str, halt=None, floor: bool = True, say=print, ahead=None, who: dict | None = None):
    """A place, its turn and the lease for the block, on this thread; the holder for the block to report its root
    and its end proof to. Leaves the note and the place settled however the block ends."""

    holder = Holder(label, halt=halt, floor=floor, say=say, ahead=ahead, who=who)
    try:
        holder.take()
        holder.lead()
        holder.take_lease()
        yield holder
    finally:
        holder.finish()


@contextmanager
def launched(label: str, halt, floor: bool = True, ahead=None, say=lambda line: None):
    """`held` for the server's own launches, on the worker thread that runs them: the place and the lease before
    the launch and its deadlines start, kept until the program has ended. A halt while waiting is the owner's
    stop (`ValueError`); a queue the server cannot join is the owner's to see to (`RuntimeError`). Off Windows
    there is no queue."""

    if os.name != "nt":
        yield None
        return
    try:
        with held(label, halt=halt, floor=floor, say=say, ahead=ahead) as holder:
            yield holder
    except Stopped:
        raise ValueError("사람이 멈췄다") from None
    except Refused as error:
        raise RuntimeError(f"GPU 줄에 서지 못했다 — {error}") from None


def stop(counter: int, session: dict | None, say=print) -> int:
    """`gpu_run.py --stop`: set place `counter`'s stop event and wait until the place is no longer live. Refuses a
    place whose `.who` names another session than `session`. 0 once the place is gone."""

    if why := joinable():
        say(f"{why}; a job started with `require_escalated` is stopped by a `--stop` with the same "
            "`require_escalated`")
        return 2
    folder = home()
    place, who = folder / "queue" / str(counter), folder / "queue" / f"{counter}.who"
    try:
        named = json.loads(who.read_text(encoding="utf-8")).get("session")
    except FileNotFoundError:
        named = None
    except (OSError, ValueError):
        named = {}
    if named and not native_process.same(named, session):
        say(f"place {counter} belongs to another session; not stopped")
        return 3
    event = K.OpenEventW(0x0002, False, stop_event(counter, folder))   # EVENT_MODIFY_STATE
    if event:
        K.SetEvent(event)
        K.CloseHandle(event)
    lease, queue, _ = names(folder)
    while True:
        state = probe(place)
        if state == "gone":
            break
        if state == "free":   # no holder: removed under the queue mutex, as any scan would
            mutex = K.CreateMutexW(None, False, queue)
            K.WaitForMultipleObjects(1, (w.HANDLE * 1)(mutex), False, INFINITE)
            try:
                place.unlink(missing_ok=True)
                who.unlink(missing_ok=True)
            finally:
                K.ReleaseMutex(mutex)
                K.CloseHandle(mutex)
            break
        time.sleep(POLL)
    say(f"place {counter} is gone; the runner's own last line in its session says whether its command started")
    return 0
