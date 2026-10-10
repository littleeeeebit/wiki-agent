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
