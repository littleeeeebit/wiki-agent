"""The runner a GPU job is wrapped in by the pretool hook (`gpu_job`): it waits in the GPU queue, runs the job
once it holds the lease, and leaves nothing behind (plan 15a, "The runner leaves nothing behind").

`--hold` is the wrapper the hook writes; the job's command comes base64-encoded, run by the shell that started
this runner, whose Windows pid the wrapper passes (`/proc/$$/winpid`, `$PID`): an MSYS `exec` leaves no
parent to walk up to. The runner watches that shell, the session's Claude or Codex process (pid and start time
from the hook) and its place's stop event: any of them ending stops the job the normal way, before it starts or
while it runs. The command is created inside a job of the runner's own from
its first instruction (`owned_job`), so closing it ends every process it started.

`--stop <counter>` is Codex's stop: it sets the place's event and waits until the place is no longer live.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import os
import sys
from ctypes import wintypes as w
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gpu_queue  # noqa: E402
import native_process  # noqa: E402

MARK = "gpu_run.py --hold"   # what `background_left` finds in a recorded command
SHELLS = {"posix": {"bash.exe", "sh.exe"}, "ps": {"pwsh.exe", "powershell.exe"}}   # what runs the job's text
BEFORE, AFTER ="stopped before start", "stopped after start"
ESCALATE = ("Run this GPU job outside the sandbox: call it again with `sandbox_permissions: "
            "\"require_escalated\"`, which asks the owner.")


def say(line: str) -> None:
    print(f"[gpu queue] {line}", flush=True)


def stop_command(counter: int) -> str:
    """The exact command that stops place `counter`, from any worktree: the hub's Python and runner by path."""

    return f"& '{sys.executable}' '{HERE / 'gpu_run.py'}' --stop {counter}"


def runnable(shell: str, dialect: str, command: str) -> tuple[str, str]:
    """(the shell that runs the job, the text it runs): the caller's own, except a packaged (Store) PowerShell,
    which Windows will not create inside a job. Then the newest installed one that is not packaged, chosen as the
    shared launch code chooses (`common.shell`): on 5.1 the pipeline chains are lowered, and text only 7 runs is
    refused (`ValueError`) before anything is queued."""

    if dialect != "ps" or "windowsapps" not in {part.lower() for part in Path(shell).parts}:
        return shell, command
    from common import shell as shells

    owned = [s for s in shells.detect() if "windowsapps" not in {part.lower() for part in Path(s.executable).parts}]
    if not owned:
        raise ValueError("the shell is a Store PowerShell, which cannot run inside the job, and no other PowerShell "
                         "is installed")
    chosen = max(owned, key=lambda s: s.major)
    if chosen.form == "ps5":
        command = shells.ps5_chains(command)
        if needs := shells.seven_only(command):
            raise ValueError(f"the shell is a Store PowerShell, which cannot run inside the job, and Windows "
                             f"PowerShell 5.1 cannot run this command ({', '.join(needs)}); install PowerShell 7 "
                             "outside the Store")
    return chosen.executable, command


def shell_argv(shell: str, dialect: str, command: str) -> list[str]:
    if dialect == "ps":   # `-Command` exits 1 for any failure: the last program's own code is kept
        return [shell, "-NoLogo", "-NoProfile", "-Command",
                f"$global:LASTEXITCODE = 0\n{command}\n$ok = $?\nif ($LASTEXITCODE) {{ exit $LASTEXITCODE }}\n"
                "if (-not $ok) { exit 1 }"]
    return [shell, "-c", command]


def opened(identity: dict):
    """A handle that signals when the process `identity` names ends, checked against that identity."""

    handle = gpu_queue.K.OpenProcess(0x00100000 | 0x1000, False, identity["pid"])   # SYNCHRONIZE | QUERY_LIMITED
    if handle and native_process.same(identity, native_process.identity(identity["pid"], handle)):
        return handle
    if handle:
        gpu_queue.K.CloseHandle(handle)
    return None


def hold(args) -> int:
    command = base64.b64decode(args.command).decode("utf-8")
    if why := gpu_queue.joinable():   # before anything of the queue is resolved or created
        say(f"{why}; nothing ran.")
        if args.ask:
            say(ESCALATE)
        return 1
    pid, created = (int(x) for x in args.session.split(":"))
    session = native_process.identity(pid)
    if not session or session["created"] != created:
        say("the session that started this GPU job is gone; nothing ran.")
        return 1
    shell = native_process.identity(args.shell)
    me = native_process.identity(os.getpid())
    if not shell or not me or shell["created"] > me["created"] or Path(shell["exe"]).name.lower() not in SHELLS[args.dialect]:
        say("cannot find the shell that started this GPU job; nothing ran.")
        return 1
    try:
        exe, text = runnable(shell["exe"], args.dialect, command)
    except ValueError as why:
        say(f"{why}; nothing ran.")
        return 1
    watch = []
    for name, identity in (("session", session), ("shell", shell)):
        handle = opened(identity)
        if handle is None:
            say(f"the {name} that started this GPU job is gone; nothing ran.")
            return 1
        watch.append((name, handle))
    who = {"session": session, "shell": shell, "escalated": args.escalated}
    holder = gpu_queue.Holder("agent job", watch=watch, say=say, who=who)
    stopped, code, job = None, 1, None
    try:
        counter = holder.take()   # its stop event is watched from the moment the place exists
        if args.codex:
            say(f"stop: {stop_command(counter)}" + ("   (run it with `require_escalated` too)" if args.escalated else ""))
        holder.lead()
        holder.take_lease()
        import owned_job

        job = owned_job.Job()

        def last() -> None:   # every watched process alive and no stop set, or the root never runs
            holder._wait([], 0)

        if exe != shell["exe"]:
            say(f"the shell is a Store app, which cannot run inside the job: {Path(exe).name} runs the command")
        try:
            root = job.start(shell_argv(exe, args.dialect, text), os.getcwd(), dict(os.environ),
                             None, sys.stdout, before_resume=holder.root, last=last)
        except gpu_queue.Stopped as stop:
            stopped = (BEFORE, str(stop))
            raise
        except owned_job.Refused as refused:
            say(f"the command could not be created inside its job ({refused}); nothing ran.")
            raise gpu_queue.Stopped("refused") from None
        say(f"running (place {counter}, holds the GPU lease)")
        handle = next(r["handle"] for r in job.roots if r["identity"] and native_process.same(r["identity"], root))
        try:
            holder._wait([handle], None)
        except gpu_queue.Stopped as stop:
            stopped = (AFTER, str(stop))
        proof = job.end(30)
        holder.ended(proof)
        found = w.DWORD()
        if not stopped and ctypes.WinDLL("kernel32").GetExitCodeProcess(w.HANDLE(handle), ctypes.byref(found)):
            code = found.value
    except gpu_queue.Stopped as stop:
        stopped = stopped or (BEFORE, str(stop))
    except gpu_queue.Refused as refused:
        say(f"{refused}; nothing ran.")
    finally:
        try:
            if job is not None:
                if job.roots and holder.proof is None:   # ended by something unforeseen: still proven, never left
                    holder.ended(job.end(30))
                job.close()
        finally:
            holder.finish()
    if stopped:
        say(f"{stopped[0]} ({stopped[1]})")
    return code if not stopped else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--stop", type=int)
    parser.add_argument("--session", default="")
    parser.add_argument("--shell", type=int, default=0)
    parser.add_argument("--dialect", choices=("posix", "ps"), default="posix")
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--escalated", action="store_true")
    parser.add_argument("--ask", action="store_true")
    parser.add_argument("command", nargs="?", default="")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.stop is not None:
        return gpu_queue.stop(args.stop, gpu_queue.ancestry()[0], say)
    if args.hold and args.command:
        return hold(args)
    parser.print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
