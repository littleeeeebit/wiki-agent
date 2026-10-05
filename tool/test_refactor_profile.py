"""A refactoring step through the real runner, with a scripted proposer."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import improvement
import refactor_profile

BLOCK = "".join(f"    total = total + w * {i}\n" for i in range(1, 6))
CALC = (f"def area(w, h):\n    total = h\n{BLOCK}    return total\n\n\n"
        f"def volume(w, h):\n    total = h\n{BLOCK}    return total * 2\n")
CHECK = "from calc import area, volume\nassert area(2, 3) == 33\nassert volume(2, 3) == 66\n"

# Plays a candidate per runner label, then hands its change back the way the real host does.
PROPOSER = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from improvement_host import captured, git
request = json.load(sys.stdin)
root = Path(request["root"])
label = Path(os.environ["WIKI_IMPROVEMENT_CACHE"]).name
start = git(root, "rev-parse", "HEAD").strip()
calc = root / "src/calc.py"
text = calc.read_text(encoding="utf-8")
if label == "r0-c0-propose":    # changes behaviour
    calc.write_text(text.replace("return total * 2", "return total * 3"), encoding="utf-8")
elif label == "r0-c1-propose":  # edits a frozen test
    (root / "src/check_calc.py").write_text("pass\n", encoding="utf-8")
elif label == "r1-c0-propose":  # removes the duplicate
    head = text.split("\n\n\ndef volume")[0]
    calc.write_text(head + "\n\n\ndef volume(w, h):\n    return area(w, h) * 2\n", encoding="utf-8")
else:                           # changes nothing that counts
    calc.write_text(text + "# note\n", encoding="utf-8")
patch, paths = captured(root, start)
tokens = 20000 if label == "r0-c0-propose" else 10
print(json.dumps({"edits": [{"component": "step", "hypothesis": label, "paths": paths}], "patch": patch,
                  "usage": {"calls": 1, "tokens": tokens}}))
'''


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


def _repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "src/calc.py").write_text(CALC, encoding="utf-8")
    (root / "src/check_calc.py").write_text(CHECK, encoding="utf-8")
    (root / ".gitignore").write_text(".wiki/\n", encoding="utf-8")
    (root / ".wiki").mkdir()
    (root / ".wiki/adapter.toml").write_text(f"[slots]\ngate_cmd = '\"{sys.executable}\" src/check_calc.py'\n",
                                             encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


STEP = {"goal": "Remove the duplicated accumulation", "tier": "L1", "files": ["src/calc.py"],
        "tests": ["src/check_calc.py"], "test_argv": [sys.executable, "src/check_calc.py"]}
LIMITS = {"seconds": 900, "calls": 8, "tokens": 40000}


def test_frozen_tests_reject_and_the_debt_drop_selects(tmp_path):
    repo = _repo(tmp_path / "repo")
    assert refactor_profile.debt_of(repo, ["src/"]) == 12, "six shared lines, twice"
    # L2 owns the directory, so the frozen test beside the code is reachable and must be refused.
    config = refactor_profile.prepare(repo, "project", "dedupe", {**STEP, "tier": "L2"}, {"model": "m"}, LIMITS,
                                      store=tmp_path / "frozen")
    fake = tmp_path / "proposer.py"
    fake.write_text(PROPOSER, encoding="utf-8")
    data = json.loads(config.read_text(encoding="utf-8"))
    data["commands"]["propose"] = [sys.executable, str(fake), str(Path(refactor_profile.__file__).parent)]
    config.write_text(json.dumps(data), encoding="utf-8")

    result = refactor_profile.drive(repo, "project", "dedupe", config, store=tmp_path / "runs")

    assert result["state"] == "adopted" and result["review_required"]
    state = improvement.Experiment(repo, "project", "dedupe", store=tmp_path / "runs").read()
    verdicts = {(c["round"], c["candidate"]): c for c in state["history"]}
    assert not verdicts[0, 0]["evaluation"]["guards"]["preserved"], "a behaviour change fails the frozen test"
    assert "cannot edit" in verdicts[0, 1]["reason"], "a frozen test is not the candidate's to edit"
    assert verdicts[1, 0]["verdict"] == "accepted"
    assert verdicts[1, 1]["verdict"] == "rejected", "no drop in debt is no improvement"
    assert [o["artifact"] for o in state["overruns"]] == ["r0-c0-propose"], "a soft ceiling is recorded, not fatal"
    adopted = subprocess.run(["git", "show", f"{result['branch']}:src/calc.py"], cwd=repo, capture_output=True,
                             text=True, check=True).stdout
    assert "return area(w, h) * 2" in adopted


def test_a_low_tier_step_owns_its_files_and_the_codex_proposer_can_write(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    (repo / "src/other.py").write_text(CALC, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sibling")
    config = refactor_profile.prepare(repo, "project", "own", STEP, {}, LIMITS, store=tmp_path / "frozen")
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["components"] == {"step": ["src/calc.py"]}, "an L1 step may not edit its siblings"
    spec = json.loads((config.parent / "frozen.json").read_text(encoding="utf-8"))
    assert spec["baseline"] == refactor_profile.debt_of(repo, ["src/calc.py"]) \
        < refactor_profile.debt_of(repo, ["src/"]), "nor earn credit from them"

    from agent import chat_session
    seen = []

    def spawn(cmd, **_):
        seen.append(cmd)
        raise OSError("not launched")

    chat = chat_session.ChatSession(repo, write=True, bypass=True, isolated=True, model="codex:m")
    monkeypatch.setattr(chat_session, "cli_command", lambda name: [name])
    monkeypatch.setattr(chat_session.subprocess, "Popen", spawn)
    with pytest.raises(OSError):
        chat._spawn()
    cmd = seen[0]
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access" and "shell_tool" not in cmd


def test_a_command_is_cut_whole_and_preparing_a_step_is_bounded(tmp_path):
    import threading
    import time

    mark = tmp_path / "orphan-lived"
    child = f"import time, pathlib; time.sleep(2); pathlib.Path({str(mark)!r}).write_text('x')"
    parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}]); print('parent done')"
    began = time.monotonic()
    done = refactor_profile.sh([sys.executable, "-c", parent], tmp_path, seconds=30)
    assert done.returncode == 0 and "parent done" in done.stdout and time.monotonic() - began < 2, \
        "a child holding the output does not hold the wait"
    time.sleep(3)
    assert not mark.exists(), "the child that outlived its parent died with the call"

    halt, sleep = threading.Event(), [sys.executable, "-c", "import time; time.sleep(60)"]
    threading.Timer(0.5, halt.set).start()
    began = time.monotonic()
    assert refactor_profile.sh(sleep, tmp_path, halt=halt).returncode == refactor_profile.CUT
    assert refactor_profile.sh(sleep, tmp_path, seconds=0.5).returncode == refactor_profile.CUT
    assert time.monotonic() - began < 10

    repo = _repo(tmp_path / "repo")
    with pytest.raises(improvement.Refused, match="outran"):
        refactor_profile.prepare(repo, "project", "slow", {**STEP, "test_argv": sleep}, {},
                                 {**LIMITS, "seconds": 0.5}, store=tmp_path / "frozen")


def test_a_step_is_frozen_only_when_its_tests_pass_today(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "src/check_calc.py").write_text("assert False\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "break")
    with pytest.raises(improvement.Refused, match="fail on today's code"):
        refactor_profile.prepare(repo, "project", "x", STEP, {}, LIMITS, store=tmp_path / "frozen")
    with pytest.raises(improvement.Refused, match="not committed"):
        refactor_profile.prepare(repo, "project", "y", {**STEP, "tests": ["new_check.py"]}, {}, LIMITS,
                                 store=tmp_path / "frozen")


def test_only_the_refactor_profile_may_soften_a_ceiling(tmp_path):
    with pytest.raises(improvement.Refused, match="Only the refactor profile"):
        improvement.contract({"schema": "wiki-improvement/1", "model": "m", "rounds": 1, "candidates": 1,
                              "trials": 1, "edits_min": 1, "edits_max": 1, "stall_window": 1, "prune_window": 1,
                              "delta": 0, "beta0": 0, "beta1": 0, "w_score": 1, "w_cost": 1, "w_novelty": 0,
                              "limits": LIMITS, "soft_caps": ["propose"],
                              "caps": {k: {"calls": 0, "tokens": 0} for k in ("propose", "critic", "evaluate")}},
                             tmp_path / "x.json")
