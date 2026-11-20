"""The deadline answers before the host's limit, once, and exits 0."""

import json
from pathlib import Path
import subprocess
import sys
import time

import apply
import hook_deadline

HERE = Path(__file__).resolve().parent


def run(body: str) -> tuple[subprocess.CompletedProcess, float]:
    script = f"import sys, time; sys.path.insert(0, {str(HERE)!r}); import hook_deadline as d\n{body}"
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30,
                          encoding="utf-8", errors="replace")
    return done, time.monotonic() - started


def test_limits_sit_under_the_host_timeouts():
    hosts = {"inject.py": apply.hook_entry("python", None), "session_state.py": apply.session_entry("python", "")}
    for name, entry in hosts.items():
        assert hook_deadline.LIMITS[name][0] <= entry["hooks"][0]["timeout"] - 3, name


def test_a_stalled_hook_still_answers_with_its_fallback():
    done, took = run("d.arm(0.3, d.output('UserPromptSubmit', 'stalled', '위키: 늦음'))\ntime.sleep(20)")
    assert done.returncode == 0 and took < 10
    assert json.loads(done.stdout) == hook_deadline.output("UserPromptSubmit", "stalled", "위키: 늦음")


def test_the_hook_answer_goes_out_once_and_wins():
    done, _took = run(
        "d.arm(0.5, d.output('UserPromptSubmit', 'quick', 'q'))\n"
        "d.best = d.output('UserPromptSubmit', 'partial', 'p')\n"
        "assert d.send(d.output('UserPromptSubmit', 'full', 'f'))\n"
        "time.sleep(20)"
    )
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] == "full"
