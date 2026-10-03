"""Even when killed, where it stopped is recorded without the payload, and a
normal run's output survives."""

import os
from pathlib import Path
import subprocess
import sys
import time


# This file does not print anything itself. The encoding is pinned anyway:
# `lint.py`'s check looks at every `tool/*.py` without exception, and that
# check itself records that a narrowed condition is the next incident.
#
# A narrowed condition would have missed it here. The real exposure was not
# on the printing side but on the side reading a child: the four `text=True`
# calls below decode by locale, and reading `codex_pretool.py` — which emits
# Korean — died on cp949. So all four state their encoding.
sys.stdout.reconfigure(encoding="utf-8")

TOOL = Path(__file__).resolve().parent


def test_timeout_evidence_survives_process_kill(tmp_path):
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "PYTHONPATH": str(TOOL)}
    code = (
        "from hook_diagnostics import arm; import time; "
        "arm('probe.py', 0.2); print('ready', flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8",
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        deadline = time.monotonic() + 5
        logs = []
        while time.monotonic() < deadline:
            logs = list(tmp_path.rglob("*.log"))
            if logs and "Timeout" in logs[0].read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        assert logs and "Timeout" in logs[0].read_text(encoding="utf-8")
    finally:
        process.kill()
        process.communicate(timeout=5)
    evidence = logs[0].read_text(encoding="utf-8")
    assert '"hook": "probe.py"' in evidence
    assert '"pid":' in evidence and '"started_utc":' in evidence
    assert 'File "<string>"' in evidence
    assert '"completed_ms"' not in evidence
    logs[0].unlink()

    normal = subprocess.run(
        [sys.executable, "-c", "from hook_diagnostics import arm; "
         "arm('probe.py', 8); print('unchanged')"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=10,
    )
    assert (normal.returncode, normal.stdout, normal.stderr) == (0, "unchanged\n", "")
    assert not list(tmp_path.rglob("*.log"))


def test_installed_pretool_records_blocked_stdin_without_content(tmp_path):
    # The real entry point, with only the watchdog's 8 s delay shortened; the
    # header below still has to name the 8 s the threshold table gives it.
    runner = tmp_path / "run.py"
    runner.write_text(f"""import faulthandler, runpy, sys
real = faulthandler.dump_traceback_later
faulthandler.dump_traceback_later = lambda after, **kw: real(0.2, **kw)
sys.path.insert(0, {str(TOOL)!r})
sys.argv = [{str(TOOL / "codex_pretool.py")!r}]
runpy.run_path(sys.argv[0], run_name="__main__")
""", encoding="utf-8")
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path)}
    process = subprocess.Popen(
        [sys.executable, str(runner)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        process.stdin.write(b'{"prompt":"PRIVATE_SENTINEL"')
        process.stdin.flush()
        deadline = time.monotonic() + 5
        evidence = ""
        while time.monotonic() < deadline:
            logs = list(tmp_path.rglob("*.log"))
            evidence = logs[0].read_text(encoding="utf-8") if logs else ""
            if "Timeout" in evidence:
                break
            time.sleep(0.1)
        assert "Timeout" in evidence and '"hook": "codex_pretool.py"' in evidence
        assert '"stack_after_seconds": 8' in evidence
        assert "PRIVATE_SENTINEL" not in evidence
        assert process.poll() is None
    finally:
        process.kill()
        process.communicate(timeout=5)


def test_slow_completion_retention_and_unwritable_directory(tmp_path):
    directory = tmp_path / "wiki-hook-diagnostics"
    directory.mkdir()
    expired = directory / "wiki-old.log"
    expired.write_text("old", encoding="utf-8")
    os.utime(expired, (0, 0))
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "PYTHONPATH": str(TOOL)}
    slow = subprocess.run(
        [sys.executable, "-c", "from hook_diagnostics import arm; import time; "
         "arm('probe.py', 0.1); time.sleep(0.3); print('unchanged')"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=5,
    )
    assert (slow.returncode, slow.stdout, slow.stderr) == (0, "unchanged\n", "")
    assert not expired.exists()
    logs = list(directory.glob("*.log"))
    assert len(logs) == 1
    assert '"completed_ms"' in logs[0].read_text(encoding="utf-8")
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("keep", encoding="utf-8")
    env["LOCALAPPDATA"] = str(blocked)
    normal = subprocess.run(
        [sys.executable, str(TOOL / "codex_pretool.py")],
        input='{"tool_name":"request_user_input_async"}',
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=5,
    )
    assert normal.returncode == 0 and normal.stderr == ""
    assert '"permissionDecision": "deny"' in normal.stdout
