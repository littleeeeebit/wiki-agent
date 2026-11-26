"""PreToolUse, last: the machine's load on each program start, and a GPU job queued rather than run beside
another (plan 15a). Called by `claude_pretool` and `codex_pretool` only for a call every other check let through,
on the repaired command.

A shell call whose program words are all inspection gets nothing. Any other gets the load line. A GPU job is
refused while one of the session's own processes is on the GPU's list (a queue behind its own idle server would
never move); otherwise it is wrapped in `tool/gpu_run.py`, which waits for its place and the lease. On Claude the
call also goes to the background, so no foreground timeout counts the wait.

A GPU job is exactly one of two kinds of program word: a Python interpreter whose environment holds a CUDA build
(`cuda_python`), or any other `.exe` resolving inside the session's repository, its worktrees or the temporary
folder (a build of the product and its copies). A GPU job started any other way is not seen; the free-memory floor
still guards it. Once a call is judged a GPU job nothing lets the original through: a failure refuses it.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import gpu_queue
import machine
import native_process

HERE = Path(__file__).resolve().parent
TOOLS = {"Bash", "PowerShell", "exec_command", "shell_command", "shell", "local_shell"}
INSPECT = {"git", "grep", "rg", "ls", "cat", "head", "tail", "find", "wc", "nvidia-smi", "tasklist", "netstat"}
SHELLS = {"bash", "sh", "pwsh", "powershell", "cmd"}   # their own text is read as its programs
NOT_PROGRAMS = {"cd", "echo", "printf", "export", "set", "unset", "true", "false", "test", "[", "source", ".",
                "exit", "return", "local", "read", "shift", "wait", "pwd", "type", "command", "eval", "alias"}
WRAPPERS = {"timeout", "env", "nohup", "nice", "time", "stdbuf"}
PYTHON = re.compile(r"(?i)^(python(3(\.\d+)?)?|pythonw|py)(\.exe)?$")
ASKS = {"default", "acceptEdits", "plan"}   # Codex permission modes whose approval policy can ask the owner
QUEUED = {
    "claude": ("This command is a GPU job, so the hook queued it in the machine's GPU queue (`tool/gpu_run.py`) "
               "and moved it to the background: one GPU job runs at a time. Its output names its place; a notice "
               "comes when it ends. Wait for it (Yield) or read its output; stop it with TaskStop if it is no "
               "longer needed."),
    "codex": ("This command is a GPU job, so the hook queued it in the machine's GPU queue (`tool/gpu_run.py`): "
              "one GPU job runs at a time. The call returns a session id while it waits; read it with "
              "`write_stdin`. Its first output line is where the exact stop command is."),
}


def name(word: str) -> str:
    return Path(word.replace("\\", "/")).name.lower().removesuffix(".exe")


def native(word: str, cwd: str) -> Path:
    """A program word as a Windows path: Git Bash's `/c/x` is `C:/x`; a relative word is under `cwd`."""

    text = word.replace("\\", "/")
    if m := re.match(r"^/([a-zA-Z])(/|$)", text):
        text = f"{m.group(1).upper()}:/{text[3:]}"
    path = Path(text)
    return path if path.is_absolute() else Path(cwd) / path


def resolved(word: str, cwd: str) -> Path | None:
    """Where a program word runs from: its own path, or what `PATH` finds."""

    if re.search(r"[\\/]", word):
        path = native(word, cwd)
        for candidate in (path, path.with_name(path.name + ".exe")):
            if candidate.is_file():
                return candidate.resolve()
        return None
    found = shutil.which(word)
    return Path(found).resolve() if found else None


def unwrapped(words: list[str]) -> list[str]:
    """The program a `timeout 5 x`, `env A=1 x` or `nohup x` runs, which guard does not model."""

    while words and name(words[0]) in WRAPPERS:
        kind, words = name(words[0]), words[1:]
        while words and (words[0].startswith("-") or (kind == "env" and "=" in words[0])):
            words = words[1:]
        if kind == "timeout" and words and re.fullmatch(r"[\d.]+[smhd]?", words[0]):
            words = words[1:]
    return words


def cuda_prefix(prefix: Path) -> bool:
    """An environment holds a CUDA build: a `torch` whose dist-info version carries `+cu`, an `nvidia` package
    folder, or `onnxruntime-gpu`."""

    for site in [prefix / "Lib" / "site-packages", *prefix.glob("lib/python*/site-packages")]:
        if not site.is_dir():
            continue
        if any("+cu" in d.name for d in site.glob("torch-*.dist-info")) or (site / "nvidia").is_dir() \
                or any(site.glob("onnxruntime_gpu-*.dist-info")):
            return True
    return False


def prefix_of(exe: Path) -> Path:
    """The environment an interpreter runs: a venv's folder above `Scripts`, else the interpreter's own."""

    folder = exe.parent
    if folder.name.lower() in ("scripts", "bin") and (folder.parent / "pyvenv.cfg").is_file():
        return folder.parent
    return folder


def cuda_python(words: list[str], cwd: str) -> bool | None:
    """`True`/`False` when `words` runs a Python interpreter (or `uv run`), by whether its environment holds a
    CUDA build; `None` when it is not an interpreter at all."""

    first = words[0]
    if name(first) == "uv" and "run" in words[1:3]:
        values = [v for i, v in enumerate(words) if i and words[i - 1] == "--with"] \
            + [v.split("=", 1)[1] for v in words if v.startswith("--with=")]
        if any("+cu" in v or "onnxruntime-gpu" in v or v.startswith("nvidia-") for v in values):
            return True
        named = next((words[i + 1] for i, v in enumerate(words[:-1]) if v in ("--project", "--directory")), None)
        folder = native(named, cwd) if named else Path(cwd)
        for up in [folder, *folder.parents]:
            if (up / "pyproject.toml").is_file():
                return cuda_prefix(up / ".venv")
        return False
    if not PYTHON.match(Path(first.replace("\\", "/")).name):
        return None
    exe = resolved(first, cwd)
    if exe is None:
        return False
    if name(first) == "py":   # the launcher picks an install: it says which
        version = [w for w in words[1:2] if re.match(r"^-(\d|V:)", w)]
        try:
            done = subprocess.run([str(exe), *version, "-c", "import sys; print(sys.prefix)"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return done.returncode == 0 and cuda_prefix(Path(done.stdout.strip()))
        except (OSError, subprocess.TimeoutExpired):
            return False
    return cuda_prefix(prefix_of(exe))


def places(cwd: str) -> list[Path]:
    """The session's repository, its worktrees and the temporary folders: where a built program lives."""

    found = [Path(tempfile.gettempdir())]
    if local := os.environ.get("LOCALAPPDATA"):
        found.append(Path(local) / "Temp")
    try:   # read off the repository's files, as `git worktree list` does, without starting git on every call
        found += checkouts(Path(cwd).resolve())
    except (OSError, ValueError):
        pass
    return [p.resolve() for p in found]


def checkouts(here: Path) -> list[Path]:
    """The checkout `here` is in, its main checkout and every worktree its repository records."""

    for top in (here, *here.parents):
        dot = top / ".git"
        if dot.is_dir():
            common = dot
            break
        if dot.is_file():
            gitdir = Path(dot.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip())
            gitdir = gitdir if gitdir.is_absolute() else top / gitdir
            named = gitdir / "commondir"
            common = (gitdir / named.read_text(encoding="utf-8").strip()).resolve() if named.is_file() else gitdir
            break
    else:
        return []
    found = [top, *([common.parent] if common.name == ".git" else [])]
    for record in (common / "worktrees").glob("*/gitdir"):
        found.append(Path(record.read_text(encoding="utf-8").strip()).parent)
    return found


def inside(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def stop_call(words: list[str], cwd: str) -> bool:
    """`gpu_run.py --stop <n>` by the hub's own Python and runner: never a GPU job, never refused behind its job."""

    if len(words) < 3 or words[2] != "--stop":
        return False
    exe, runner = resolved(words[0], cwd), native(words[1], cwd)
    return exe is not None and exe == Path(sys.executable).resolve() and runner.resolve() == HERE / "gpu_run.py"


def kind(words: list[str], cwd: str, roots: dict) -> str:
    """`inspect`, `builtin`, `program` or `gpu` for one program run."""

    words = unwrapped(words)
    if not words:
        return "builtin"
    first = name(words[0])
    if re.search(r"[\\/]", words[0]):
        pass   # a program named by its path is that file, whatever its name: resolved below, never exempted by name
    elif first in INSPECT or first == "sed" and "-n" in words[1:]:
        return "inspect"
    elif first in NOT_PROGRAMS or first in SHELLS:
        return "builtin"
    elif "." not in words[0] and re.match(r"^[a-z]+-[a-z]", words[0], re.I):
        return "builtin"   # a PowerShell cmdlet, which starts no program
    if stop_call(words, cwd):
        return "program"
    interpreter = cuda_python(words, cwd)
    if interpreter is not None:
        return "gpu" if interpreter else "program"
    exe = resolved(words[0], cwd)
    if exe is not None and exe.suffix.lower() == ".exe":
        if cwd not in roots:   # read once per call and directory
            roots[cwd] = places(cwd)
        if inside(exe, roots[cwd]):
            return "gpu"
    return "program"


def programs(payload: dict, host: str) -> tuple[list[tuple[list[str], str]], str]:
    """Each program the call runs with the directory it runs in, read as `guard` reads them; and the dialect."""

    import guard

    tool, command, dialect = guard.shell_command(payload, host)
    if tool not in TOOLS:
        return [], dialect
    given = payload.get("tool_input") or {}
    cwd = guard.run_dir(Path(str(payload.get("cwd") or os.getcwd())), given if isinstance(given, dict) else {})
    found: list = []
    ctx = guard.Ctx(dialect, frozenset({cwd}), guard.posix(Path.home()), Path(tempfile.gettempdir()).as_posix(),
                    os.name == "nt", programs=found)
    guard.derive(command, dialect, ctx)
    return [(words, (dirs or [cwd or os.getcwd()])[0]) for words, dirs in found if words], dialect


# -- Whose processes ---------------------------------------------------------------------------------------------

def job_pids() -> set[int] | None:
    """The app's named session job's processes, when this hook runs inside it (`WIKI_AGENT_JOB`); `None` when
    there is no such job or this process is not its member."""

    job_name = os.environ.get("WIKI_AGENT_JOB")
    if not job_name or os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenJobObjectW.argtypes, k.OpenJobObjectW.restype = (w.DWORD, w.BOOL, w.LPCWSTR), w.HANDLE
    k.IsProcessInJob.argtypes = (w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL))
    k.QueryInformationJobObject.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID)
    k.GetCurrentProcess.restype = w.HANDLE
    job = k.OpenJobObjectW(0x0004, False, job_name)   # JOB_OBJECT_QUERY
    if not job:
        return None
    try:
        member = w.BOOL()
        if not k.IsProcessInJob(k.GetCurrentProcess(), job, ctypes.byref(member)) or not member:
            return None
        count = 4096
        buffer = (ctypes.c_size_t * (count + 1))()   # two DWORDs share the first slot, then the pids
        if not k.QueryInformationJobObject(job, 3, buffer, ctypes.sizeof(buffer), None):   # BasicProcessIdList
            return None
        listed = ctypes.cast(buffer, ctypes.POINTER(w.DWORD))[1]
        return {int(buffer[1 + i]) for i in range(min(listed, count))}
    finally:
        k.CloseHandle(job)


def descends(pid: int, root: dict, parents: dict[int, int]) -> bool:
    """`pid` is below `root` by recorded parents (one snapshot, `native_process.parents`), each created after
    its parent."""

    here, at = pid, born(pid)
    for _ in range(32):
        if at is None:
            return False
        up = parents.get(here)
        above = born(up) if up else None
        if above is None or above > at:
            return False
        if up == root["pid"] and above == root["created"]:
            return True
        here, at = up, above
    return False


def born(pid: int) -> int | None:
    """A live process's creation time in FILETIME ticks (`native_process.identity`'s `created`), without the
    process snapshot `identity` takes."""

    import ctypes
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    k.GetProcessTimes.argtypes = (w.HANDLE, *[ctypes.POINTER(w.FILETIME)] * 4)
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [w.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
    finally:
        k.CloseHandle(handle)


def own(holders: list[dict] | None, session: dict | None) -> list[dict] | None:
    """The listed GPU processes that are this session's: its named job's in the app, else its Claude or Codex
    program's descendants; `None` when neither can tell."""

    if holders is None:
        return []
    pids = job_pids()
    if pids is not None:
        return [h for h in holders if h["pid"] in pids]
    if session is None:
        return None
    parents = native_process.parents()
    return [h for h in holders if descends(h["pid"], session, parents)]


def command_line(pid: int) -> str:
    try:
        import psutil

        return " ".join(psutil.Process(pid).cmdline()) or "?"
    except Exception:  # noqa: BLE001 -- only shown
        return "?"


# -- The answer -------------------------------------------------------------------------------------------------

def quoted(text: str, dialect: str) -> str:
    return "'" + text.replace("'", "''" if dialect == "ps" else "'\\''") + "'"


def wrapped(command: str, dialect: str, session: dict, host: str, given: dict, payload: dict) -> str:
    """The command as the runner runs it, with the shell's own Windows pid and the session's identity."""

    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    python, runner = Path(sys.executable).as_posix(), (HERE / "gpu_run.py").as_posix()
    if " " in runner:   # the marker `background_left` reads then needs the result's background start instead
        runner = quoted(runner, dialect)
    flags = [f"--session {session['pid']}:{session['created']}", f"--dialect {dialect}"]
    if host == "codex":
        flags.append("--codex")
        if given.get("sandbox_permissions") == "require_escalated":
            flags.append("--escalated")
        if payload.get("permission_mode") in ASKS:
            flags.append("--ask")
    if dialect == "ps":
        return f"& {quoted(python, 'ps')} {runner} --hold --shell $PID {' '.join(flags)} {encoded}"
    return f"{quoted(python, 'posix')} {runner} --hold --shell \"$(cat /proc/$$/winpid)\" {' '.join(flags)} {encoded}"


def mark() -> None:
    """`gpu\\wrapped\\<uuid>` for the app's session, written before the wrapped call can start: the server ends a
    build turn's runners at its boundary by it. No variable, no mark. A mark whose named job no longer exists
    (its CLI closed or crashed, an owner's session included) has no reader: each new mark removes those."""

    job_name = os.environ.get("WIKI_AGENT_JOB")
    if not job_name:
        return
    prefix, own = job_name.rsplit("-", 1)
    found = gpu_queue.wrapped(own)
    found.parent.mkdir(parents=True, exist_ok=True)
    found.write_text("", encoding="utf-8")
    import ctypes
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenJobObjectW.argtypes, k.OpenJobObjectW.restype = (w.DWORD, w.BOOL, w.LPCWSTR), w.HANDLE
    for other in found.parent.iterdir():
        if other.name == own:
            continue
        if job := k.OpenJobObjectW(0x0004, False, f"{prefix}-{other.name}"):   # JOB_OBJECT_QUERY
            k.CloseHandle(job)
        elif ctypes.get_last_error() == 2:   # ERROR_FILE_NOT_FOUND: no such job any more
            other.unlink(missing_ok=True)


def refused(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def context(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def verdict(payload: dict, host: str = "claude") -> dict | None:
    """`None`; the load line as context; a refusal; or the load line with `updatedInput`, the wrapped call."""

    try:
        found, dialect = programs(payload, host)
    except Exception:  # noqa: BLE001 -- not yet a GPU judgement: the hooks' usual open failure
        return None
    if not found:
        return None
    roots: dict = {}
    try:
        kinds = [kind(words, cwd, roots) for words, cwd in found]
    except Exception:  # noqa: BLE001
        return None
    if all(k in ("inspect", "builtin") for k in kinds):
        return None
    try:
        load = machine.sample()
        session = gpu_queue.ancestry()[0]
        mine = own(load.get("holders"), session)
        line = machine.load_line(load, mine)
    except Exception:  # noqa: BLE001 -- the line is help, not a judgement
        load, session, mine, line = {}, None, None, ""
    if "gpu" not in kinds:
        return context(line) if line else None
    try:   # judged a GPU job: every failure from here refuses, never lets the original through
        if mine:
            listed = "; ".join(f"pid {h['pid']} {h['name'] or '?'}: {command_line(h['pid'])}" for h in mine)
            return refused(
                f"GPU 작업을 막았다 — 이 세션이 띄운 프로세스가 이미 GPU 를 잡고 있다: {listed}. 그것을 멈추거나 "
                "끝나기를 기다리거나, 서버와 그것을 쓰는 작업을 한 명령으로 함께 띄워 같이 끝나게 한다. "
                "자기 서버 뒤에 줄을 서면 줄이 움직이지 않으므로 이 경우는 줄 세우지 않는다.")
        if session is None:
            return refused("GPU 작업을 막았다 — 이 호출을 띄운 Claude 나 Codex 프로세스를 찾지 못해 GPU 줄에 세울 수 없다.")
        given = dict(payload.get("tool_input") or {})
        key = "command" if "command" in given else "cmd"
        command = given.get(key)
        if isinstance(command, list):
            command = subprocess.list2cmdline([str(a) for a in command])
        given[key] = wrapped(str(command), dialect, session, host, given, payload)
        if host == "claude":
            given["run_in_background"] = True
        mark()
        answer = context("\n\n".join(filter(None, [line, QUEUED[host]])))
        answer["hookSpecificOutput"]["updatedInput"] = given
        return answer
    except Exception as error:  # noqa: BLE001
        return refused(f"GPU 작업을 막았다 — 줄에 세우지 못했다 ({type(error).__name__}: {error}). 원래 명령은 실행하지 않는다.")
