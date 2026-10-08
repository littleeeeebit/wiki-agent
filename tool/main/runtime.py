"""One server owns a hub's persisted workflows, including startup recovery."""

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from common import process
from common.budget import Budget


stopping = threading.Event()
LOCAL_SECONDS, FLOW_SECONDS, CLEANUP_SECONDS = 19 * 60, 180, 30
verification_budget = ContextVar("verification_budget", default=None)


@contextmanager
def verification_scope(halt, seconds=None, budget=None):
    budget = budget or verification_budget.get() or Budget(
        seconds=LOCAL_SECONDS if seconds is None else min(LOCAL_SECONDS, seconds),
        calls=None, candidates=0, cancel=halt)
    token = verification_budget.set(budget)
    try:
        yield budget
    finally:
        verification_budget.reset(token)


def prepare(repo, path, settings):
    from .verification import private_file, sha

    source = Path(settings["env_file"])
    if not source.is_absolute() or not source.is_file():
        raise ValueError("로컬 .env 의 절대 경로를 설정해야 한다")
    content = source.read_bytes()
    content.decode("utf-8")
    if content.startswith(b"\xef\xbb\xbf"):
        raise ValueError("로컬 .env 는 BOM 없는 UTF-8 이어야 한다")
    destination = private_file(path, ".env")
    copied = private_file(path, ".wiki/verification.env.sha256")
    owned = destination.is_file() and copied.is_file() and copied.read_text(encoding="utf-8").strip() == sha(destination.read_bytes())
    changed = False
    if destination.exists() and destination.read_bytes() != source.read_bytes():
        if not owned:
            raise ValueError("검증 폴더에 다른 .env 가 있다 — 사용자가 확인해야 한다")
        shutil.copyfile(source, destination)
        changed = True
    if not destination.exists():
        shutil.copyfile(source, destination)
        changed = True
    if owned or changed:
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_text(sha(destination.read_bytes()) + "\n", encoding="utf-8")
    if os.name != "nt":
        destination.chmod(0o600)


def gate(cmd, cwd, halt, env, timeout, owned_jobs, kill):
    budget = verification_budget.get()
    timeout = min(timeout, budget.left()) if budget else timeout
    if halt.is_set() or timeout <= 0:
        return None, "", "사람이 멈춤" if halt.is_set() else "검증 실행 시간 제한을 넘었다"
    deadline, cut = time.monotonic() + timeout, ""
    scratch = os.environ.get("TEMP") if os.name == "nt" else None
    # A background setup child may inherit stdout; file capture never waits for its EOF.
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace", dir=scratch) as output:
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                                **{**process.background_options(), "creationflags":
                                   process.background_options().get("creationflags", 0) | process.SUSPENDED})
        job = process.contained(proc)
        try:
            if os.name == "nt" and not job:
                proc.kill()
                proc.wait(timeout=1)
                raise OSError("Cannot own the verification process tree")
            process.resumed(proc)
            while True:
                try:
                    proc.wait(timeout=max(0.01, min(0.1, deadline - time.monotonic())))
                    cut = "사람이 멈춤" if halt.is_set() else "검증 실행 시간 제한을 넘었다" \
                        if time.monotonic() > deadline else ""
                    break
                except subprocess.TimeoutExpired:
                    if halt.is_set() or time.monotonic() > deadline:
                        cut = "사람이 멈춤" if halt.is_set() else "검증 실행 시간 제한을 넘었다"
                        process.killed(job) if job else kill(proc)
                        try:
                            proc.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        break
            output.seek(0)
            return None if cut else proc.returncode, output.read(), cut
        finally:
            if job and owned_jobs is not None:
                owned_jobs.append(job)
            else:
                process.terminated(job)


@contextmanager
def server_owner(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    # Keep the inode in place: deleting the file can let another process lock
    # a replacement while the first server still owns the original.
    with (directory / "server.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as exc:
            raise RuntimeError("이 위키의 앱 서버가 이미 실행 중이다. 기존 창에서 계속해라.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
