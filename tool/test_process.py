"""A contained child's whole tree ends with it, on POSIX through its process group."""

import os
import subprocess
import sys
import time

import pytest

from common import process

# The parent starts a grandchild that writes a file a moment later, then exits at once.
ORPHAN = ("import subprocess, sys; subprocess.Popen([sys.executable, '-c', "
          "'import time, sys; time.sleep(1); open(sys.argv[1], \"w\").write(\"late\")', sys.argv[1]])")


@pytest.mark.skipif(os.name == "nt", reason="the Windows job is covered by the agent and runtime tests")
def test_an_orphaned_grandchild_writes_nothing_after_its_group_is_ended(tmp_path):
    late = tmp_path / "late.txt"
    parent = subprocess.Popen([sys.executable, "-c", ORPHAN, str(late)], start_new_session=True)
    job = process.contained(parent)
    assert job == parent.pid
    parent.wait(timeout=10)   # gone already; only its group still reaches the grandchild
    process.terminated(job)
    time.sleep(1.5)
    assert not late.exists()


def test_a_child_that_leads_no_group_is_not_contained_elsewhere():
    child = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=process.SUSPENDED)
    try:
        job = process.contained(child)
        assert (job is not None) if os.name == "nt" else job is None
    finally:
        process.resumed(child)
        child.wait(timeout=10)
        process.terminated(job)


# A parent with no console (as a test worker or the search daemon has none) starts a console child that reports
# whether its console window is visible.
NO_CONSOLE_PARENT = """
import subprocess, sys
sys.path.insert(0, sys.argv[1])
from common import process
process.hide_child_consoles()
probe = "import ctypes; w = ctypes.windll.kernel32.GetConsoleWindow(); print(int(bool(w and ctypes.windll.user32.IsWindowVisible(w))))"
seen = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
open(sys.argv[2], "w").write(seen.stdout.strip() or seen.stderr)
"""


@pytest.mark.skipif(os.name != "nt", reason="consoles open windows only on Windows")
def test_a_console_child_of_a_parent_without_a_console_opens_no_window(tmp_path):
    seen = tmp_path / "seen.txt"
    tool = os.path.dirname(os.path.abspath(__file__))
    subprocess.run([sys.executable, "-c", NO_CONSOLE_PARENT, tool, str(seen)], timeout=90,
                   creationflags=subprocess.DETACHED_PROCESS, check=True)
    assert seen.read_text() == "0"
