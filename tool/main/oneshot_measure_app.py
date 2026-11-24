"""What the real measurement runs (oneshot steps 12 and 13): the backend headless (`served`), or, in a `desktop`
run, the program the owner installs (`running`), never a headless server.

`docs/plans/oneshot/13-measure-the-product.md` is the design. `.wiki/measure.json` names the program in `app`: its
`run` block, shaped as the PC app's (`oneshot_desktop.run_of`), and the local `health` address the program's own
process answers once it is ready. The built copy is frozen (`oneshot_desktop.frozen`) and started in a job of its own,
on the run-owned desktop for the server's own measurement (`auto`) and on the owner's desktop for theirs (`owner`).
Its `{data}` is one folder kept for the whole run, so a sign-in or a download the program kept is still there for the
next build. The program is told which measurement it is in by `ONESHOT_MEASURE`, as the script is.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from common import masked, process, shell

from . import oneshot_build, oneshot_desktop, oneshot_needs, oneshot_spec
from .oneshot_kept import tail

MODE = "ONESHOT_MEASURE"   # `auto`: the server's measurement, no person; `owner`: the owner's, on their screen
END_SECONDS = 30
# What a Windows program needs from the server's environment to run at all, and nothing that could be a credential:
# the program gets these, the measurement's inputs and the design's secrets, and its own launch settings.
RUNTIME = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "OS", "USERPROFILE", "HOMEDRIVE",
    "HOMEPATH", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "ALLUSERSPROFILE", "PUBLIC", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL", "PROCESSOR_REVISION", "DRIVERDATA"})


def app_of(value) -> dict:
    """`app.run` checked as the PC app's `run` is, and never setting `ONESHOT_MEASURE`, which the server sets."""

    if not isinstance(value, dict):
        raise ValueError("`app` 는 run 과 health 를 담은 객체다 — 주인이 설치해서 쓰는 PC 프로그램을 띄운다")
    run = oneshot_desktop.run_of(value.get("run"), "app")
    if any(k.upper() == MODE for k in run["env"]):
        raise ValueError(f"`app.run.env` 는 `{MODE}` 를 정하지 않는다 — 서버가 auto 나 owner 로 넘긴다")
    return run


def folder(spec: dict) -> Path:
    """The run's program measurements: the frozen copy, the looks, and `data`, kept from one build to the next."""

    return oneshot_spec.store(spec["repo"]).folder / "measure-app"


def started(spec: dict, plan: dict, tree: Path, env: dict, halt, log: Path, hide: dict, mode: str,
            passed: dict | None = None):
    """What the measurement runs, started for the block, which gets the frozen build or `None`: the PC program in
    a run that builds one (`owner` starting the build the server's own measurement `passed`), else the backend."""

    if "app" in plan:
        return running(spec, plan, tree, env, halt, log, hide, mode, passed)
    return served(oneshot_build.run_as(plan["serve"]["command"], spec["oneshot"], tree), tree, env,
                  plan["serve"]["health"], plan["serve"]["seconds"], halt, log, hide)


# -- Starting the backend, in a run without a PC app ----------------------------------------------------

@contextmanager
def served(command: str, path: Path, env: dict, health: str, seconds: int, halt, log: Path, hide: dict):
    """The backend started in a job of its own and answering `health` with 200, for the block; `ValueError` with
    the end of its log when it exits, or does not answer in `seconds`. Killed with everything it started after."""

    if answers(health):   # whatever answers now is not the backend about to start
        raise ValueError(f"`{health}` 에 이미 다른 프로그램이 답한다 — 그 프로그램을 끄거나 `serve` 의 포트를 바꾼다")
    argv, use_shell = shell.launch("gate", command=command)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w+", encoding="utf-8", errors="replace") as output:
        proc = subprocess.Popen(argv, shell=use_shell, cwd=path, env={**env, "PYTHONUNBUFFERED": "1"},
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=os.name != "nt",
                                **{**process.background_options(), "creationflags":
                                   process.background_options().get("creationflags", 0) | process.SUSPENDED})
        job = process.contained(proc)
        try:
            if os.name == "nt" and not job:
                proc.kill()
                raise ValueError("백엔드 프로세스를 묶어 둘 수 없었다")
            process.resumed(proc)
            deadline = time.monotonic() + seconds
            while True:
                if halt.is_set():
                    raise ValueError("사람이 멈췄다")
                if proc.poll() is not None:
                    raise ValueError(f"`serve.command` 가 떠 있지 않고 끝났다 (종료 코드 {proc.returncode}) — {tail(log, hide)}")
                if answers(health) == 200:
                    if not owned(proc, health):
                        raise ValueError(f"`{health}` 에 답한 것이 띄운 백엔드가 아니다 — 같은 포트를 쓰는 다른 프로그램을 끈다")
                    break
                if time.monotonic() > deadline:
                    raise ValueError(f"백엔드가 {seconds}초 안에 `{health}` 에 200 으로 답하지 않았다 — {tail(log, hide)}")
                time.sleep(1)
            yield
        finally:
            process.terminated(job) if job else proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def answers(health: str) -> int | None:
    """The status `health` itself answers with, or `None` when nothing does. A redirect is its status, never
    followed: whatever answers at the other end is not the backend being checked."""

    try:
        with NO_REDIRECT.open(health, timeout=3) as answer:
            return answer.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


class Unfollowed(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # urllib then raises `HTTPError` with the redirect's own status


NO_REDIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), Unfollowed)   # loopback: never a proxy
def owned(proc: subprocess.Popen, health: str) -> bool:
    """Whether every process that accepts a connection to `health` (one connection to each address its host
    resolves to) is `proc` or a process it started: read off the accepted socket itself, whose remote port is
    the connection's own, so whichever listener the system picks is the one judged."""

    try:
        import psutil   # `requirements-chat.txt`; here, so a server without it still starts and refuses only this
    except ImportError:
        raise ValueError("psutil 이 설치되지 않았다 — `pip install -r requirements-chat.txt` 로 다시 설치한다") from None
    try:
        tree = {proc.pid, *(c.pid for c in psutil.Process(proc.pid).children(recursive=True))}
    except psutil.Error:
        return False
    return vouched(tree, health)


def vouched(tree: set[int], health: str, status: bool = False) -> bool:
    """Whether every process that accepts a connection to `health` is one of `tree`: `owned`'s read, and the PC
    program's, whose processes its job names (`running`). With `status`, each of those connections answers 200
    itself: the answer and the process behind it are never read off two connections."""

    import psutil   # `owned` and the program's launch have checked it is installed
    url = urlsplit(health)
    port, path = url.port or 80, (url.path or "/") + (f"?{url.query}" if url.query else "")
    seen = False
    try:
        for family, kind, _, _, address in socket.getaddrinfo(url.hostname, port, type=socket.SOCK_STREAM):
            with socket.socket(family, kind) as s:
                s.settimeout(3)
                try:
                    s.connect(address)
                except OSError:
                    continue   # nothing answers on this address: it cannot answer for the backend either
                if accepted_by(psutil, s.getpeername()[:2], s.getsockname()[:2]) not in tree:
                    return False
                if status:
                    s.sendall(f"GET {path} HTTP/1.1\r\nHost: {url.netloc}\r\nConnection: close\r\n\r\n".encode())
                    line = s.makefile("rb").readline(256).split()
                    if len(line) < 2 or line[1] != b"200":
                        return False
                seen = True
        return seen
    except (psutil.Error, OSError):
        return False


def accepted_by(psutil, server: tuple, client: tuple, seconds: float = 3.0) -> int | None:
    """The process holding the server side of the connection `client` made to `server`, matched on both ends'
    address and port (an IPv4-mapped address read as IPv4), or `None`. A connection still queued for `accept`
    has no owner on Linux yet, so the table is read again until one shows or `seconds` pass."""

    ip = lambda a: a.lower().removeprefix("::ffff:")   # noqa: E731
    deadline = time.monotonic() + seconds
    while True:
        pid = next((c.pid for c in psutil.net_connections("tcp") if c.laddr and c.raddr
                    and (ip(c.laddr.ip), c.laddr.port) == (ip(server[0]), server[1])
                    and (ip(c.raddr.ip), c.raddr.port) == (ip(client[0]), client[1])), None)
        if pid is not None or time.monotonic() > deadline:
            return pid
        time.sleep(0.1)


# -- Starting the PC program, in a run that builds one ----------------------------------------------------

def environment(spec: dict, plan: dict, launch_env: dict, env: dict, mode: str) -> dict:
    """The program's environment over the server's, as `Look.start` reads it: the launch settings, then from the
    measurement's environment only what a program needs to run (`RUNTIME`), the plan's inputs and the design's
    secrets, then the mode, each name once to Windows. Every other name the server holds is dropped (`None`), so
    no credential the measurement does not name reaches the program, nor its log."""

    named = RUNTIME | {x["env"].upper() for x in plan["inputs"]} | {n["env"].upper() for _, n in oneshot_needs.secret_needs(spec)}
    merged: dict = {}
    for part in (launch_env, {k: v for k, v in env.items() if k.upper() in named}, {MODE: mode}):
        for name, value in part.items():
            for old in [k for k in merged if k.upper() == name.upper()]:
                del merged[old]
            merged[name] = value
    kept = {k.upper() for k in merged}
    return {name: None for name in os.environ if name.upper() not in kept} | merged


def changed(build) -> str:
    """Why the frozen copy the server's own measurement passed is no longer there as it passed, or empty."""

    if not isinstance(build, dict) or not build.get("identity"):
        return "자동 실측을 통과한 PC 앱 빌드의 기록이 없다"
    try:
        return oneshot_desktop.altered(build)
    except (OSError, ValueError) as exc:
        return f"자동 실측을 통과한 PC 앱 빌드를 읽지 못했다 ({type(exc).__name__})"


@contextmanager
def running(spec: dict, plan: dict, tree: Path, env: dict, halt, log: Path, hide: dict, mode: str,
            passed: dict | None = None):
    """The frozen program started with the measurement's environment and answering `health` with 200 from a
    socket its job holds, for the block, which gets the frozen build; `ValueError` with the end of its log when it
    exits or does not answer in time. `auto` freezes the built HEAD's copy anew; `owner` starts the copy the
    server's own measurement `passed`, unchanged, never a new one. Ended with everything it started after, its
    log copied to `log` for the kept folder to mask. A desktop that cannot be opened or cleaned is the owner's
    to see to, never the build's (`RuntimeError`)."""

    import desktop_session   # Windows only, as a PC app is

    app = plan["app"]
    if answers(app["health"]):   # whatever answers now is not the program about to start
        raise ValueError(f"`{app['health']}` 에 이미 다른 프로그램이 답한다 — 그 프로그램을 끄거나 `app.health` 의 포트를 바꾼다")
    root = folder(spec)
    if mode == "owner":
        if why := changed(passed):
            raise RuntimeError(f"{why} — [자동 실측 시작]으로 다시 잰다")
        build = passed
    else:
        desktop = {"run": app["run"], "installer": ""}
        if why := oneshot_desktop.built(tree, desktop):
            raise ValueError(why)
        into = root / "app"
        shutil.rmtree(into, ignore_errors=True)
        into.mkdir(parents=True)
        build = oneshot_desktop.frozen(tree, desktop, into)
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    launch, copy = build["launch"], Path(build["folder"])
    argv = [str(copy / launch["program"]), *(a.replace(oneshot_desktop.DATA, str(data)) for a in launch["args"])]
    extra = environment(spec, plan, {k: v.replace(oneshot_desktop.DATA, str(data)) for k, v in launch["env"].items()},
                        env, mode)
    (root / "looks").mkdir(exist_ok=True)
    try:
        with desktop_session.look(root / "looks", f"measure-{mode}-{uuid.uuid4().hex[:12]}", owned=mode == "auto",
                                  disposable=True) as look:
            app_log = look.folder / "evidence" / "app.log"
            try:
                try:
                    look.start("app", argv, copy / launch["cwd"], extra, halt=halt)
                except desktop_session.Refused as exc:   # Windows would not run this program: the build's to fix
                    raise ValueError(f"PC 프로그램을 띄우지 못했다 — {masked(str(exc), hide)}") from None
                job = look.jobs["app"]
                deadline = time.monotonic() + app["seconds"]
                while True:
                    if halt.is_set():
                        raise ValueError("사람이 멈췄다")
                    if job.finished():
                        raise ValueError(f"PC 프로그램이 떠 있지 않고 끝났다 — {tail(app_log, hide)}")
                    if answers(app["health"]) == 200:
                        pids = {m["pid"] for m in job.members()}
                        if not vouched(pids, app["health"]):
                            raise ValueError(f"`{app['health']}` 에 답한 것이 띄운 PC 프로그램이 아니다 — 같은 포트를 쓰는 "
                                             "다른 프로그램을 끈다")
                        if vouched(pids, app["health"], status=True):   # the 200 and its process, one connection
                            break
                    if time.monotonic() > deadline:
                        raise ValueError(f"PC 프로그램이 {app['seconds']}초 안에 `{app['health']}` 에 200 으로 답하지 "
                                         f"않았다 — {tail(app_log, hide)}")
                    time.sleep(1)
                yield build
            finally:
                look.observe()
                if (job := look.jobs.get("app")) is not None and not (end := job.end(END_SECONDS))["clean"]:
                    raise RuntimeError(f"PC 프로그램을 끝내지 못했다 — {'; '.join(end['reasons'])}")
                try:   # masked before it is written: no copy of the program's output holds a secret
                    log.parent.mkdir(parents=True, exist_ok=True)
                    log.write_text(masked(app_log.read_text(encoding="utf-8", errors="replace"), hide), encoding="utf-8")
                except OSError:
                    pass   # no log is kept: the measurement says what it says without it
            if why := changed(build):   # it wrote beside itself: a pass would stand for a copy no longer there
                raise ValueError(f"PC 프로그램이 실행 중에 자기 사본을 바꿨다 — 쓰는 것은 모두 `{{data}}` 아래에 둔다 ({why})")
    except desktop_session.Halted:   # stopped before the program ran: never started
        raise ValueError("사람이 멈췄다") from None
    except desktop_session.Refused as exc:   # the desktop, never the build: opening it or cleaning it up
        raise RuntimeError(f"PC 프로그램의 데스크톱을 열거나 치우지 못했다 — {masked(str(exc), hide)}. 남은 것은 "
                           f"`{root / 'looks'}` 에 있다 — 손으로 지운 뒤 다시 시작한다") from exc
