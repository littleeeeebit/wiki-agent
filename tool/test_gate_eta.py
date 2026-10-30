"""The running gate's estimate: the math, the live read of its output, and the run times kept."""

import json
import sys
import threading
import time

import pytest

from main import gate_eta, runtime, specs


def test_the_estimate_conditions_past_runs_and_trusts_progress_as_it_grows():
    past = [100, 110, 90, 105]
    start = gate_eta.estimate(past, 0, None)
    assert 95 <= start["total"] <= 105 and start["low"] < start["total"] < start["high"]
    late = gate_eta.estimate(past, 104, None)
    assert late["total"] > 104, "a gate still running is longer than the time it has run"
    assert gate_eta.estimate(past, 1000, None) == {"over": True}
    assert gate_eta.estimate([], 60, 0.5)["total"] == pytest.approx(120, abs=1)
    assert gate_eta.estimate(past, 1000, 0.5)["total"] == pytest.approx(2000, rel=0.01), "progress alone, past outrun"
    early = gate_eta.estimate(past, 100, 0.5)["total"]
    assert 100 < early < 130, "at half way the tight past outweighs a 200 s reading of the count"
    near = gate_eta.estimate([100, 300, 50, 200], 200, 0.99)["total"]
    assert near == pytest.approx(202, abs=3), "near the end the progress line outweighs a loose past"
    assert gate_eta.estimate([100, 100], 10, None) is None and gate_eta.estimate([], 3, 0.5) is None
    assert gate_eta.estimate(past, 1000, 0) == {"over": True}, "a 0% reading is no progress (#253 round 1)"


def test_progress_reads_the_last_pytest_percentage():
    assert gate_eta.progress(b"....  [ 40%]\n....   [ 80%]\n..") == 0.8
    assert gate_eta.progress(b"[gw0] [100%] PASSED t.py::a") == 1.0
    assert gate_eta.progress(b"collected 3 items") is None


def test_a_watched_gate_shows_its_output_while_it_runs(tmp_path):
    tails: list[bytes] = []
    script = ("import sys, time\nprint('.... [ 50%]', flush=True)\ntime.sleep(2.5)\n"
              "print('.... [100%]', flush=True)\n")
    (tmp_path / "slow.py").write_text(script, encoding="utf-8")
    token = runtime.gate_output.set(tails.append)
    try:
        code, out, cut = runtime.gate(f'"{sys.executable}" slow.py', tmp_path, threading.Event(), None, 60, None,
                                      lambda proc: proc.kill())
    finally:
        runtime.gate_output.reset(token)
    assert (code, cut) == (0, "") and "[ 50%]" in out and "[100%]" in out
    assert any(gate_eta.progress(t) == 0.5 for t in tails), tails


def test_a_gate_is_cut_when_its_output_stops_not_when_it_has_run_long(tmp_path, monkeypatch):
    """ai-generation's suite ran 15 to 19 minutes and a 19-minute clock cut it as if stuck. Neither script
    flushes: their marks reach the gate only because it runs Python unbuffered."""

    monkeypatch.setattr(runtime, "IDLE_SECONDS", 3)
    (tmp_path / "slow.py").write_text("import time\nfor _ in range(6):\n    print('.', end='')\n    time.sleep(1)\n",
                                      encoding="utf-8")
    (tmp_path / "stuck.py").write_text("import time\nprint('.', end='')\ntime.sleep(60)\n", encoding="utf-8")
    kill = lambda proc: proc.kill()   # noqa: E731
    assert runtime.gate(f'"{sys.executable}" slow.py', tmp_path, threading.Event(), None, 60, None, kill) == (0, "......", "")
    started = time.monotonic()
    code, out, cut = runtime.gate(f'"{sys.executable}" stuck.py', tmp_path, threading.Event(), None, 60, None, kill)
    assert (code, out, cut) == (None, ".", runtime.STUCK) and time.monotonic() - started < 15


def test_a_passing_gate_keeps_its_time_and_the_next_one_starts_from_it(tmp_path, monkeypatch):
    monkeypatch.setattr(specs, "SPECS", tmp_path / "specs")
    for ok in (True, True, False, True):
        with specs.gating("proj", "card", "final", "pytest") as gated:
            gated["ok"] = ok
    kept = specs.gate_times()
    assert len(gate_eta.past(kept, "proj", "pytest")) == 3 and gate_eta.past(kept, "proj", "other") == []
    rows = [{"repo": "proj", "cmd": "slow", "seconds": s} for s in (100, 110, 90)]
    specs.gate_times().write_text("".join(json.dumps(r) + "\n" for r in rows) + "torn {\n", encoding="utf-8")
    with specs.gating("proj", "card", "final", "slow"):
        assert 90 < specs._gating[("proj", "card")]["eta"]["total"] < 110
    assert ("proj", "card") not in specs._gating
    # A chain's first pytest at 100% says nothing of the steps after it (#253 round 1): its history stands.
    rows = [{"repo": "proj", "cmd": "a && b", "seconds": 300} for _ in range(3)]
    specs.gate_times().write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with specs.gating("proj", "card", "final", "a && b"):
        specs._gating[("proj", "card")]["since"] -= 100
        runtime.gate_output.get()(b"first pytest [100%]\nlint running")
        assert specs._gating[("proj", "card")]["eta"]["total"] > 250
