"""The machine's GPU queue and the runner a GPU job is wrapped in (plan 15a)."""

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import gpu_queue
import gpu_run
import native_process

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the GPU queue runs on Windows only")
TOOL = Path(__file__).parent
SHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """A queue of the test's own, with mutexes of its own, and a machine with room."""

    monkeypatch.setenv("WIKI_AGENT_GPU_HOME", str(tmp_path / "gpu"))
    free(tmp_path)
    monkeypatch.setenv("WIKI_AGENT_LOAD_FILE", str(tmp_path / "load.json"))
    return tmp_path / "gpu"


def free(tmp_path: Path, used: float = 1000) -> None:
    (tmp_path / "load.json").write_text(json.dumps({"cpu": 1.0, "ram": [4, 32], "holders": [], "gpus": [
        {"uuid": "a", "name": "A", "used": used, "total": 10240}]}), encoding="utf-8")


def sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def until(test, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not test():
        assert time.monotonic() < deadline, "never happened"
        time.sleep(0.05)


class Lines(list):
    def __call__(self, line: str) -> None:
        self.append(line)


def background(label: str, halt=None, lines=None, floor=True, hold: threading.Event | None = None, order=None,
               **more):
    """A holder on its own thread, holding the lease until `hold` is set; the thread and what it raised."""

    raised: list = []

    def run():
        try:
            with gpu_queue.held(label, halt=halt, say=lines if lines is not None else Lines(), floor=floor, **more) as holder:
                if order is not None:
                    order.append(label)
                if hold is not None:
                    hold.wait(60)
                more.get("inside", lambda h: None)(holder)
        except BaseException as error:  # noqa: BLE001 -- the test reads it
            raised.append(error)

    more.pop("inside", None)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, raised


# -- The lease and the order ------------------------------------------------------------------------------------

def test_the_lease_is_exclusive_under_racing_takers(queue):
    inside, most, lock = [0], [0], threading.Lock()

    def job(holder):
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        time.sleep(0.02)
        with lock:
            inside[0] -= 1

    threads = []
    for i in range(8):
        raised: list = []

        def run(raised=raised):
            try:
                with gpu_queue.held(f"t{i}", say=Lines()) as holder:
                    job(holder)
            except BaseException as error:  # noqa: BLE001
                raised.append(error)
        threads.append((threading.Thread(target=run), raised))
    for t, _ in threads:
        t.start()
    for t, raised in threads:
        t.join(60)
        assert not raised and not t.is_alive(), raised
    assert most[0] == 1 and not list((queue / "queue").iterdir()) and not (queue / "holder").exists()


def test_places_go_in_counter_order_and_a_paused_taker_keeps_its_turn(queue):
    order, first = [], threading.Event()
    a, _ = background("a", hold=first, order=order)
    until(lambda: order == ["a"])
    paused, go = threading.Event(), threading.Event()
    real = gpu_queue.Holder._locked

    def slow(self, step):   # the taker of place 2 stops inside the queue mutex, before its file exists
        if self.label == "b" and step.__name__ == "taken":
            def step_after(step=step):
                paused.set()
                go.wait(30)
                return step()
            return real(self, step_after)
        return real(self, step)

    gpu_queue.Holder._locked = slow
    try:
        b, _ = background("b", order=order)
        paused.wait(30)
        c, _ = background("c", order=order)   # its counter waits on the mutex: it can only be 3
        time.sleep(0.3)
        go.set()
        until(lambda: sorted(p.name for p in (queue / "queue").iterdir() if p.name.isdigit()) == ["1", "2", "3"])
    finally:
        gpu_queue.Holder._locked = real
    first.set()
    for t in (a, b, c):
        t.join(30)
    assert order == ["a", "b", "c"]


def test_a_stale_place_is_removed_and_an_unreadable_one_stops_the_queue(queue):
    (queue / "queue").mkdir(parents=True)
    (queue / "next").write_text("3", encoding="utf-8")
    (queue / "queue" / "1").write_text("", encoding="utf-8")   # opens: no one holds it
    (queue / "queue" / "1.who").write_text("{}", encoding="utf-8")
    (queue / "queue" / "2.who").write_text("{}", encoding="utf-8")   # its place is gone
    with gpu_queue.held("x", say=Lines()) as holder:
        assert holder.counter == 4
    assert not list((queue / "queue").iterdir())
    (queue / "queue" / "4").mkdir()   # a place Windows will not open: never decided by guessing
    halt, lines = threading.Event(), Lines()
    t, raised = background("y", halt=halt, lines=lines)
    until(lambda: any("GPU queue stopped" in line for line in lines))
    halt.set()
    t.join(30)
    assert isinstance(raised[0], gpu_queue.Stopped)
    (queue / "queue" / "4").rmdir()
    with gpu_queue.held("z", say=Lines()):
        pass


def test_the_floor_holds_the_head_until_there_is_room(queue, tmp_path):
    free(tmp_path, used=9800)
    lines, order = Lines(), []
    t, raised = background("x", lines=lines, order=order)
    until(lambda: any("waits for free memory" in line for line in lines))
    assert order == []
    free(tmp_path)
    t.join(30)
    assert order == ["x"] and not raised
    free(tmp_path, used=9800)
    with gpu_queue.held("owner", floor=False, say=Lines()):   # the owner's round skips it
        pass


def test_the_servers_launch_waits_in_line_and_stops_there(queue):
    first = threading.Event()
    a, _ = background("agent job", hold=first)
    until(lambda: (queue / "holder").exists())
    counts, halt = [], threading.Event()
    done: list = []

    def launch():
        try:
            with gpu_queue.launched("measure 1", halt, ahead=counts.append):
                done.append("ran")
        except ValueError as error:
            done.append(str(error))

    t = threading.Thread(target=launch)
    t.start()
    until(lambda: 1 in counts)
    halt.set()
    t.join(30)
    assert done == ["사람이 멈췄다"]
    halt.clear()
    t = threading.Thread(target=launch)
    t.start()
    until(lambda: counts.count(1) >= 2)
    first.set()
    t.join(30)
    a.join(30)
    assert done[-1] == "ran" and counts[-1] == 0


# -- The holder note ---------------------------------------------------------------------------------------------

def test_a_note_left_by_an_unproven_end_holds_the_next_holder(queue):
    proc = sleeper()
    try:
        member = native_process.identity(proc.pid)
        with gpu_queue.held("server", say=Lines()) as holder:   # a server holder whose job did not end cleanly
            holder.root(member)
            holder.ended({"clean": False, "total": 1, "joined": 1, "members": [member]})
        assert json.loads((queue / "holder").read_text(encoding="utf-8"))["processes"] == [member]
        lines, order = Lines(), []
        t, _ = background("next", lines=lines, order=order)
        until(lambda: any(f"{proc.pid}" in line for line in lines))
        assert order == []
        proc.kill()
        t.join(30)
        assert order == ["next"] and not (queue / "holder").exists()
    finally:
        proc.kill()


def test_an_inconclusive_end_stops_the_queue_until_the_owner_deletes_the_note(queue):
    with gpu_queue.held("job", say=Lines()) as holder:   # the job counted a process that never reported joining
        holder.root({"pid": 999999, "created": 1, "exe": "x.exe"})
        holder.ended({"clean": False, "total": 3, "joined": 1, "members": []})
    assert json.loads((queue / "holder").read_text(encoding="utf-8"))["inconclusive"] is True
    lines, order = Lines(), []
    t, _ = background("next", lines=lines, order=order)
    until(lambda: any(gpu_queue.INCONCLUSIVE in line for line in lines))
    time.sleep(1)
    assert order == []
    (queue / "holder").unlink()
    t.join(30)
    assert order == ["next"]
    with gpu_queue.held("rolled back", say=Lines()) as holder:   # a root with no identity: inconclusive too
        holder.root({"pid": 999998, "created": 1, "exe": "x.exe"})
        holder.ended({"clean": False, "total": 1, "joined": 1, "members": []}, [])
    assert json.loads((queue / "holder").read_text(encoding="utf-8"))["inconclusive"] is True


CRASH = """
import os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import gpu_queue, native_process
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdout=subprocess.DEVNULL,
                         creationflags=0x00000008)   # DETACHED: it outlives this holder
holder = gpu_queue.Holder("crash", say=lambda line: None)
holder.take(); holder.lead(); holder.take_lease()
holder.root(native_process.identity(child.pid))
print(child.pid, flush=True)
time.sleep(float(sys.argv[2]))
os._exit(1)   # no finish: the lease abandoned, the note left
"""


@pytest.mark.parametrize("waiter", [True, False], ids=["abandoned", "destroyed"])
def test_a_crashed_holders_note_is_acted_on(queue, waiter):
    crash = subprocess.Popen([sys.executable, "-c", CRASH, str(TOOL), "1.5" if waiter else "0"], stdout=subprocess.PIPE,
                             text=True, env=os.environ.copy())
    child = int(crash.stdout.readline())
    try:
        lines, order = Lines(), []
        if not waiter:
            crash.wait(30)   # gone before anyone waits: the mutex no longer exists, only the note
        t, raised = background("next", lines=lines, order=order)
        until(lambda: any(str(child) in line for line in lines))
        assert order == []
        subprocess.run(["taskkill", "/PID", str(child), "/F"], capture_output=True)
        t.join(30)
        assert order == ["next"] and not raised
    finally:
        subprocess.run(["taskkill", "/PID", str(child), "/F"], capture_output=True)
        crash.kill()


# -- Whose queue, and where --------------------------------------------------------------------------------------

def test_one_folder_whatever_temp_says(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_AGENT_GPU_HOME", raising=False)
    first = gpu_queue.home()
    for key in ("TEMP", "TMP", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / key))
    assert gpu_queue.home() == first and first.parts[-2:] == ("wiki-agent-data", "gpu")
    assert gpu_queue.names(first)[0] == "Local\\wiki-agent-gpu"


def test_another_account_runs_nothing_and_creates_nothing(queue, monkeypatch, capsys):
    monkeypatch.setattr(gpu_queue, "owner_sid", lambda folder=gpu_queue.HUB: "S-1-5-21-0-0-0-1")
    with pytest.raises(gpu_queue.Refused, match=gpu_queue.UNJOINABLE):
        gpu_queue.Holder("x")
    assert not queue.exists()
    encoded = base64.b64encode(b"echo ran").decode()
    assert gpu_run.main(["--hold", "--session", "1:1", "--codex", "--ask", encoded]) == 1
    said = capsys.readouterr().out
    assert "nothing ran" in said and "require_escalated" in said and not queue.exists()
    assert gpu_run.main(["--stop", "1"]) == 2 and not queue.exists()


# -- The runner ----------------------------------------------------------------------------------------------------

def runner(queue: Path, command: str, session: int, *flags: str) -> subprocess.Popen:
    """`gpu_run.py --hold` as the hook writes it for PowerShell, run by a PowerShell of its own."""

    encoded = base64.b64encode(command.encode()).decode()
    created = native_process.identity(session)["created"]
    line = (f"& '{sys.executable}' '{TOOL / 'gpu_run.py'}' --hold --shell $PID --session {session}:{created} "
            f"--dialect ps {' '.join(flags)} {encoded}; exit $LASTEXITCODE")
    return subprocess.Popen([SHELL, "-NoLogo", "-NoProfile", "-Command", line], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            env=os.environ.copy())


def marker(tmp_path: Path) -> tuple[Path, str]:
    """A command that writes its pid to a file, then sleeps."""

    pid = tmp_path / "pid.txt"
    return pid, (f"& '{sys.executable}' -c \"import os, time; open(r'{pid}', 'w').write(str(os.getpid())); "
                 "time.sleep(120)\"")


def same_session(monkeypatch, pid: int) -> None:
    """`--stop` run in-process as though from the session `pid` (in the field, the Codex program above it)."""

    identity = native_process.identity(pid)
    monkeypatch.setattr(gpu_queue, "ancestry", lambda pid=None: (identity, []))


def alive(pid: int) -> bool:
    return native_process.identity(pid) is not None


def test_the_runner_passes_the_output_and_the_exit_code_and_leaves_nothing(queue):
    run = runner(queue, f"& '{sys.executable}' -c \"print('hello'); raise SystemExit(3)\"", os.getpid())
    text, _ = run.communicate(timeout=60)
    assert "hello" in text and "[gpu queue] running" in text and run.returncode == 3, text
    assert not (queue / "holder").exists() and not [p for p in (queue / "queue").iterdir()]


@pytest.mark.parametrize("ends", ["shell", "session", "stop"])
def test_the_runner_ends_its_job_when_its_shell_its_session_or_a_stop_ends_it(queue, tmp_path, ends, monkeypatch):
    session = sleeper()
    same_session(monkeypatch, session.pid)
    pid, command = marker(tmp_path)
    run = runner(queue, command, session.pid, "--codex")
    try:
        until(lambda: pid.exists() and pid.read_text())
        job = int(pid.read_text())
        if ends == "shell":
            subprocess.run(["taskkill", "/PID", str(run.pid), "/F"], capture_output=True)
        elif ends == "session":
            session.kill()
        else:
            assert gpu_run.main(["--stop", "1"]) == 0
        until(lambda: not alive(job))
        until(lambda: not (queue / "queue" / "1").exists())
        if ends != "shell":
            text, _ = run.communicate(timeout=30)
            assert f"{gpu_run.AFTER} ({ends})" in text and "--stop 1" in text, text
        assert not (queue / "holder").exists()
    finally:
        session.kill()
        run.kill()


def test_a_stop_while_waiting_for_a_place_runs_nothing(queue, tmp_path, monkeypatch):
    same_session(monkeypatch, os.getpid())
    hold = threading.Event()
    a, _ = background("ahead", hold=hold)
    until(lambda: (queue / "holder").exists())
    pid, command = marker(tmp_path)
    run = runner(queue, command, os.getpid(), "--codex")
    try:
        until(lambda: (queue / "queue" / "2").exists())
        assert gpu_run.main(["--stop", "2"]) == 0
        text, _ = run.communicate(timeout=30)
        assert f"{gpu_run.BEFORE} (stop)" in text and not pid.exists(), text
    finally:
        hold.set()
        a.join(30)
        run.kill()


def test_a_stop_for_another_sessions_place_is_refused(queue, tmp_path, monkeypatch, capsys):
    session = sleeper()
    pid, command = marker(tmp_path)
    run = runner(queue, command, session.pid)
    try:
        until(lambda: pid.exists() and pid.read_text())
        monkeypatch.setattr(gpu_queue, "ancestry", lambda pid=None: (native_process.identity(os.getpid()), []))
        assert gpu_run.main(["--stop", "1"]) == 3 and "another session" in capsys.readouterr().out
        assert alive(int(pid.read_text()))
    finally:
        session.kill()
        run.kill()


def test_a_shell_that_is_not_the_runners_runs_nothing(queue):
    encoded = base64.b64encode(b"echo ran").decode()
    created = native_process.identity(os.getpid())["created"]
    done = subprocess.run([sys.executable, str(TOOL / "gpu_run.py"), "--hold", "--shell", str(os.getpid()),
                           "--session", f"{os.getpid()}:{created}", "--dialect", "ps", encoded],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 1 and "cannot find the shell" in done.stdout and "ran\n" not in done.stdout
