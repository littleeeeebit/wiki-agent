"""One server owns a hub's persisted workflows, including startup recovery."""

from contextlib import contextmanager
from pathlib import Path
import sys


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
