"""The pretool hook's GPU step: the load line, the two GPU-job kinds, the session's own holders and the wrap
(plan 15a)."""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import claude_pretool
import codex_pretool
import gpu_job
import gpu_queue
import native_process
from common import process

TOOL = Path(__file__).parent


def env(root: Path, kind: str, layout: str = "venv") -> Path:
    """A Python environment's interpreter, its site-packages holding `kind`: `cuda`, `cpu`, `nvidia`, `onnx`."""

    exe = root / ("Scripts/python.exe" if layout == "venv" else "python.exe")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    if layout == "venv":
        (root / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    site = root / "Lib/site-packages"
    {"cuda": site / "torch-2.5.1+cu124.dist-info", "cpu": site / "torch-2.5.1.dist-info", "nvidia": site / "nvidia",
     "onnx": site / "onnxruntime_gpu-1.19.0.dist-info"}[kind].mkdir(parents=True)
    return exe


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An attached checkout outside the temporary roots, and a queue and a load of the test's own."""

    temp = tmp_path / "temp"
    temp.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("WIKI_AGENT_MANAGED", "1")
    monkeypatch.delenv("WIKI_AGENT_JOB", raising=False)
    monkeypatch.delenv("WIKI_AGENT_ROLE", raising=False)
    monkeypatch.setenv("WIKI_AGENT_GPU_HOME", str(tmp_path / "gpu"))
    load(tmp_path, monkeypatch, [])
    monkeypatch.setattr(gpu_queue, "ancestry", lambda pid=None: (native_process.identity(os.getpid()), []))
    top = tmp_path / "work" / "repo"
    (top / ".git").mkdir(parents=True)
    (top / ".git/HEAD").write_text("ref: refs/heads/task\n", encoding="utf-8")
    (top / ".git/config").write_text('[remote "origin"]\n\turl = https://github.com/owner/repo.git\n', encoding="utf-8")
    (top / ".wiki").mkdir()
    (top / ".wiki/adapter.toml").write_text('[slots]\ngate_cmd = "python -m ruff check tool"\n', encoding="utf-8")
    return top


def load(tmp_path: Path, monkeypatch, holders: list[dict] | None) -> None:
    given = tmp_path / "load.json"
    given.write_text(json.dumps({"cpu": 5.0, "ram": [8, 32], "gpus": None if holders is None else
                                 [{"uuid": "a", "name": "A", "used": 1000, "total": 10240}], "holders": holders}),
                     encoding="utf-8")
    monkeypatch.setenv("WIKI_AGENT_LOAD_FILE", str(given))


def call(top: Path, command: str, tool: str = "Bash", host: str = "claude", **more) -> dict | None:
    payload = {"tool_name": tool, "tool_input": {("command" if host == "claude" else "cmd"): command, **more},
               "cwd": str(top)}
    return (claude_pretool if host == "claude" else codex_pretool).verdict(payload)


def out(answer: dict | None) -> dict:
    return (answer or {}).get("hookSpecificOutput") or {}


def test_both_kinds_and_a_cpu_torch(repo, tmp_path, monkeypatch):
    cuda, cpu = env(tmp_path / "cuda", "cuda"), env(tmp_path / "cpu", "cpu")
    conda = env(tmp_path / "conda", "nvidia", layout="conda")   # Anaconda: the interpreter beside `Lib`
    onnx = env(tmp_path / "onnx", "onnx")
    roots: dict = {}
    cwd = str(repo)
    for exe in (cuda, conda, onnx):
        assert gpu_job.kind([str(exe), "train.py"], cwd, roots) == "gpu", exe
        assert gpu_job.kind(["timeout", "60", exe.as_posix(), "-u", "x.py"], cwd, roots) == "gpu"
    assert gpu_job.kind([str(cpu), "train.py"], cwd, roots) == "program"
    monkeypatch.setenv("PATH", f"{cuda.parent}{os.pathsep}{os.environ['PATH']}")   # by `PATH`, as typed
    assert gpu_job.kind(["python", "train.py"], cwd, roots) == "gpu"
    assert gpu_job.kind(["uv", "run", "--with", "torch==2.5.1+cu124", "x.py"], cwd, roots) == "gpu"
    built = Path(tempfile.gettempdir()) / "build" / "app.exe"   # a build of the product, in the temporary folder
    built.parent.mkdir()
    built.write_bytes(b"")
    assert gpu_job.kind([str(built)], cwd, roots) == "gpu"
    where = Path(os.environ["SystemRoot"]) / "System32" / "where.exe"   # any other program
    assert gpu_job.kind([str(where), "x"], cwd, roots) == "program"
    for words in (["git", "status"], ["nvidia-smi"], ["sed", "-n", "1p", "f"], ["Get-ChildItem"], ["cd", "x"]):
        assert gpu_job.kind(words, cwd, roots) in ("inspect", "builtin"), words


def test_a_program_in_a_worktree_is_a_gpu_job(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    top = tmp_path / "real"
    subprocess.run(["git", "init", "-q", str(top)], check=True)
    exe = top / "target" / "release" / "app.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert gpu_job.kind(["./target/release/app.exe"], str(top), {}) == "gpu"
    assert gpu_job.kind([str(env(tmp_path / "cuda", "cuda"))], str(top), {}) == "gpu"
    named = top / "bin" / "cat.exe"   # a program named like an inspection: by its path it is that file
    named.parent.mkdir()
    named.write_bytes(b"")
    assert gpu_job.kind(["./bin/cat.exe"], str(top), {}) == "gpu"
    assert gpu_job.kind([str(named)], str(top), {}) == "gpu"
    assert gpu_job.kind(["cat", "x"], str(top), {}) == "inspect"


@pytest.mark.skipif(os.name != "nt", reason="Windows jobs")
def test_a_new_mark_removes_the_marks_whose_jobs_are_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_AGENT_GPU_HOME", str(tmp_path / "gpu"))
    live = f"live{os.getpid()}x{time.time_ns()}"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=process.SUSPENDED)
    job = process.contained(proc, f"Local\\wiki-agent-session-{live}")
    try:
        closed = gpu_queue.wrapped("closed" + live)   # an owner's session that queued a job and closed
        closed.parent.mkdir(parents=True)
        closed.write_text("", encoding="utf-8")
        gpu_queue.wrapped(live).write_text("", encoding="utf-8")
        monkeypatch.setenv("WIKI_AGENT_JOB", f"Local\\wiki-agent-session-mine{live}")
        gpu_job.mark()
        assert sorted(p.name for p in closed.parent.iterdir()) == sorted([live, "mine" + live])
    finally:
        process.terminated(job)


def test_inspection_gets_nothing_and_a_program_gets_the_load_line(repo):
    assert call(repo, "git status && ls") is None
    line = out(call(repo, "python -m ruff check tool"))["additionalContext"]
    assert line.startswith("Load now: CPU 5%") and "updatedInput" not in out(call(repo, "python -m ruff check tool"))


def test_a_gpu_job_is_wrapped_in_the_background_on_claude(repo, tmp_path, monkeypatch):
    cuda = env(tmp_path / "cuda", "cuda").as_posix()
    monkeypatch.setenv("WIKI_AGENT_JOB", "Local\\wiki-agent-session-" + "ab" * 16)
    answer = out(call(repo, f"{cuda} train.py --epochs 1"))
    given = answer["updatedInput"]
    assert given["run_in_background"] is True and "gpu_run.py --hold" in given["command"]
    assert "--shell \"$(cat /proc/$$/winpid)\"" in given["command"] and "--dialect posix" in given["command"]
    encoded = given["command"].rsplit(" ", 1)[1]
    assert base64.b64decode(encoded).decode() == f"{cuda} train.py --epochs 1"
    assert gpu_queue.wrapped("ab" * 16).exists()   # the server ends this turn's runners at its boundary
    assert "GPU queue" in answer["additionalContext"] and "Load now:" in answer["additionalContext"]
    ps = out(call(repo, f"& '{cuda}' train.py", tool="PowerShell"))["updatedInput"]["command"]
    assert ps.startswith("& '") and "--shell $PID" in ps and "--dialect ps" in ps


def test_an_agents_own_background_call_is_still_its_own(repo, tmp_path):
    cuda = env(tmp_path / "cuda", "cuda").as_posix()
    given = out(call(repo, f"{cuda} serve.py", run_in_background=True))["updatedInput"]
    assert given["run_in_background"] is True and "gpu_run.py --hold" in given["command"]


def test_a_gpu_job_is_judged_by_every_other_check_first(repo, tmp_path):
    cuda = env(tmp_path / "cuda", "cuda").as_posix()
    denied = out(call(repo, f"{cuda} train.py && echo x > ../other/f"))
    assert denied["permissionDecision"] == "deny" and "updatedInput" not in denied and "write(" in denied[
        "permissionDecisionReason"]
    built = Path(tempfile.gettempdir()) / "app.exe"
    built.write_bytes(b"")
    denied = out(call(repo, f"{built.as_posix()} && echo x > ../other/f"))
    assert denied["permissionDecision"] == "deny" and "updatedInput" not in denied


def test_codex_waits_in_the_foreground_and_is_told_how_to_stop(repo, tmp_path):
    cuda = str(env(tmp_path / "cuda", "cuda"))
    payload = {"tool_name": "exec_command", "tool_input": {"cmd": f"& '{cuda}' train.py",
                                                           "sandbox_permissions": "require_escalated"},
               "cwd": str(repo), "permission_mode": "default"}
    answer = out(codex_pretool.verdict(payload))
    assert answer["permissionDecision"] == "allow" and "run_in_background" not in answer["updatedInput"]
    command = answer["updatedInput"]["cmd"]
    assert "--codex" in command and "--escalated" in command and "--ask" in command and "--shell $PID" in command
    assert answer["updatedInput"]["sandbox_permissions"] == "require_escalated"
    never = out(codex_pretool.verdict({**payload, "permission_mode": "bypassPermissions"}))["updatedInput"]["cmd"]
    assert "--ask" not in never   # the hint only where the policy can ask


def test_the_stop_command_is_neither_queued_nor_refused(repo, tmp_path, monkeypatch):
    cuda = env(tmp_path / "cuda", "cuda")
    monkeypatch.setenv("PATH", f"{cuda.parent}{os.pathsep}{os.environ['PATH']}")   # a CUDA Python first on `PATH`
    stop = f"& '{sys.executable}' '{TOOL / 'gpu_run.py'}' --stop 3"
    answer = out(call(repo, stop, tool="exec_command", host="codex"))
    assert "updatedInput" not in answer or "--hold" not in json.dumps(answer["updatedInput"])
    assert answer.get("permissionDecision") != "deny"


def test_no_session_and_an_unwritable_mark_refuse_and_never_run_the_original(repo, tmp_path, monkeypatch):
    cuda = env(tmp_path / "cuda", "cuda").as_posix()
    monkeypatch.setattr(gpu_queue, "ancestry", lambda pid=None: (None, []))
    assert "찾지 못해" in out(call(repo, f"{cuda} x.py"))["permissionDecisionReason"]
    monkeypatch.setattr(gpu_queue, "ancestry", lambda pid=None: (native_process.identity(os.getpid()), []))
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("WIKI_AGENT_GPU_HOME", str(blocker))   # `wrapped\<uuid>` cannot be made under a file
    monkeypatch.setenv("WIKI_AGENT_JOB", "Local\\wiki-agent-session-" + "cd" * 16)
    for host, tool in (("claude", "Bash"), ("codex", "exec_command")):
        answer = out(call(repo, f"{cuda} x.py", tool=tool, host=host))
        assert answer["permissionDecision"] == "deny" and "updatedInput" not in answer, host


def sleeper(*, orphan: bool = False) -> int:
    """A live process below this one, or (`orphan`) one whose parent has already exited: its pid."""

    if not orphan:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        return proc.pid
    middle = subprocess.run([sys.executable, "-c", "import subprocess, sys; print(subprocess.Popen([sys.executable, "
                             "'-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid)"],
                            capture_output=True, text=True, check=True)
    return int(middle.stdout.strip())


def ended(pid: int) -> None:
    import psutil

    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass


def test_a_job_behind_the_sessions_own_holder_is_refused_by_descent(repo, tmp_path, monkeypatch):
    cuda = env(tmp_path / "cuda", "cuda").as_posix()
    mine, orphan = sleeper(), sleeper(orphan=True)
    try:
        load(tmp_path, monkeypatch, [{"gpu": "a", "pid": mine, "name": "python.exe"}])
        answer = out(call(repo, f"{cuda} x.py"))
        assert answer["permissionDecision"] == "deny" and f"pid {mine} python.exe" in answer["permissionDecisionReason"]
        assert "time.sleep(60)" in answer["permissionDecisionReason"]   # its command line, for the agent to act on
        load(tmp_path, monkeypatch, [{"gpu": "a", "pid": orphan, "name": "python.exe"}])
        assert "--hold" in out(call(repo, f"{cuda} x.py"))["updatedInput"]["command"]   # dropped out of the tree
    finally:
        ended(mine)
        ended(orphan)


PROBE = """
import json, os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import gpu_job
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
middle = subprocess.run([sys.executable, "-c", "import subprocess, sys; print(subprocess.Popen([sys.executable, "
                         "'-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid)"],
                            capture_output=True, text=True)
orphan = int(middle.stdout)
time.sleep(0.5)
pids = gpu_job.job_pids()
print(json.dumps({"pids": sorted(pids) if pids is not None else None, "me": os.getpid(), "child": child.pid,
                  "orphan": orphan}))
child.kill()
os.kill(orphan, 9)
"""


def in_job(name: str | None, job_env: str) -> dict:
    """`PROBE` run inside a job of its own, named `name`, told `WIKI_AGENT_JOB=job_env`."""

    proc = subprocess.Popen([sys.executable, "-c", PROBE, str(TOOL)], stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "WIKI_AGENT_JOB": job_env}, creationflags=process.SUSPENDED)
    job = process.contained(proc, name)
    try:
        process.resumed(proc)
        text, _ = proc.communicate(timeout=60)
        return json.loads(text)
    finally:
        process.terminated(job)


@pytest.mark.skipif(os.name != "nt", reason="Windows jobs")
def test_the_apps_named_job_is_the_sessions_own_and_another_job_is_never_read():
    name = f"Local\\wiki-agent-session-test-{os.getpid()}-{time.time_ns()}"
    found = in_job(name, name)
    assert {found["me"], found["child"], found["orphan"]} <= set(found["pids"])   # an orphan stays the session's
    other = in_job(None, name + "-absent")   # a launcher's job it runs in, the named one not there
    assert other["pids"] is None
