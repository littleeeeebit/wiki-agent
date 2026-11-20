"""The deadline answers before the host's limit, once, and exits 0."""

import json
import os
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


def dispatch(body: str, stdin: str | None, env: dict | None = None) -> tuple[subprocess.CompletedProcess, float]:
    """`hook.py` as the host runs it, its deadline for `inject.py` cut to half a second."""

    script = (
        f"import os, sys, time; from pathlib import Path; sys.path.insert(0, {str(HERE)!r})\n"
        "sys.argv = ['hook.py']\n"
        "import hook, hook_deadline as d\n"
        "d.LIMITS['inject.py'] = (0.5, *d.LIMITS['inject.py'][1:])\n"
        f"{body}\n"
        "hook.main(['claude', 'inject.py'])\n"
    )
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], input=stdin, capture_output=True, timeout=30,
                          encoding="utf-8", errors="replace", env={**os.environ, **(env or {})})
    return done, time.monotonic() - started


def test_a_deadline_with_no_fallback_yet_leaves_the_hook_running():
    """Past the deadline before the project was known, the hook still answers
    if it gets there inside the host's limit — the timer does not cut it."""

    body = ("hook.project_for = lambda cwd: (Path(cwd), Path(cwd))\n"
            "hook.legacy = lambda *a: time.sleep(1.5) or False\n"
            "hook.runpy.run_path = lambda *a, **k: d.send({'answer': 'late'})")
    done, _took = dispatch(body, json.dumps({"cwd": str(HERE)}))
    assert done.returncode == 0 and json.loads(done.stdout) == {"answer": "late"}


def test_the_probe_forwards_and_records_the_deadline_answer(tmp_path):
    body = ("hook.project_for = lambda cwd: (Path(cwd), Path(cwd))\n"
            "hook.legacy = lambda *a: False\n"
            "hook.runpy.run_path = lambda *a, **k: time.sleep(20)")
    done, took = dispatch(body, json.dumps({"cwd": str(tmp_path), "hook_event_name": "UserPromptSubmit"}),
                          {"WIKI_PROBE": "abcdef12", "WIKI_PROBE_ROOT": str(tmp_path)})
    assert done.returncode == 0 and took < 10
    assert json.loads(done.stdout) == hook_deadline.stalled("inject.py")
    row = json.loads((tmp_path / "abcdef12.jsonl").read_text(encoding="utf-8"))
    assert row["injected"] is True


def test_a_torn_row_is_closed_and_skipped(tmp_path):
    import trajectory

    path = trajectory.path_for(tmp_path)
    path.write_text('{"session": "a", "full": [', encoding="utf-8")
    assert trajectory.record(tmp_path, "하나", [], 0, "a") is None
    assert trajectory.record(tmp_path, "둘", [], 0, "b") is None
    assert [r["utterance"] for r in trajectory.session_rows(path, "a")] == ["하나"]
    assert [r["utterance"] for r in trajectory.session_rows(path, "b")] == ["둘"]
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "trajectory.jsonl\n"
    assert not list(tmp_path.glob("*.tmp"))


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
