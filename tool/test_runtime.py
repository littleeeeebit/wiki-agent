"""Workflow ownership is exclusive across processes and released after a crash."""

from pathlib import Path
import subprocess
import sys

import pytest

from main.runtime import server_owner


def test_a_killed_server_releases_ownership_without_removing_the_lock_file(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", "-c",
         "from pathlib import Path\nfrom main.runtime import server_owner\nimport sys\n"
         "with server_owner(Path(sys.argv[1])):\n print('owned', flush=True)\n sys.stdin.read()\n",
         str(tmp_path)], cwd=Path(__file__).parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            with server_owner(tmp_path):
                pytest.fail("A second process took the running server's workflows")
    finally:
        child.kill()
        child.communicate(timeout=10)
    assert (tmp_path / "server.lock").is_file()
    with server_owner(tmp_path):
        pass
