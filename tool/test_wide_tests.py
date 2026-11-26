"""A repair round's pytest run names the touched files, never the whole set (`tool/wide_tests.py`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wide_tests import verdict  # noqa: E402


def ask(command: str, cwd: Path) -> dict | None:
    return verdict({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


def repo(tmp_path: Path, gate: bool = True) -> Path:
    (tmp_path / ".git").mkdir(parents=True)
    (tmp_path / "tool").mkdir()
    if gate:
        (tmp_path / "tool" / "verify.py").write_text("")
    return tmp_path


def test_the_files_a_change_touched_run_and_the_whole_set_is_refused(tmp_path):
    root = repo(tmp_path)
    assert ask("timeout 300 python -m pytest -q -n 4 tool/test_a.py tool/test_b.py::test_x 2>&1 | tail -3", root) is None
    for command in ("python -m pytest -q", "pytest tool -x", "python -m pytest tool/test_oneshot*.py",
                    "python -m pytest -q " + " ".join(f"tool/test_{i}.py" for i in range(9))):
        assert ask(command, root)["hookSpecificOutput"]["permissionDecision"] == "deny", command
    assert ask("python -m pytest -q -k slow -p no:cacheprovider tool/test_a.py", root) is None   # option values


def test_a_run_moved_into_a_gated_repository_is_read_there_and_others_are_left_alone(tmp_path):
    gated, other = repo(tmp_path / "gated"), repo(tmp_path / "other", gate=False)
    assert ask("python -m pytest -q", other) is None   # a product's own gate may run its whole set
    assert ask(f"cd '{gated.as_posix()}' && python -m pytest -q", other)
    assert ask("python tool/verify.py final --base main", gated) is None   # the final gate runs its own


def test_an_operator_inside_quotes_is_text_and_splits_no_command(tmp_path):
    root = repo(tmp_path)
    assert ask('python -m pytest -q -k test --junitxml "review;results.xml"', root)   # still no test path
    assert ask('echo "note; python -m pytest -q; end"', root) is None   # only words echoed
    assert ask("python -m pytest -q 'tool/test_a.py' && echo 'done | ok'", root) is None


def test_every_option_that_takes_a_value_has_it_read_as_the_value(tmp_path):
    root = repo(tmp_path)
    assert ask("python -m pytest --override-ini addopts=-x tool/test_hook.py", root) is None
    assert ask("py -m pytest -k test --junit-xml report.py", root)   # `report.py` is the report, not a test
    assert ask("python -m pytest --import-mode importlib --color yes tool/test_a.py", root) is None


def test_each_run_is_read_where_the_cds_before_it_left_the_shell(tmp_path):
    gated = repo(tmp_path / "top" / "gated")
    (tmp_path / "top" / "loose").mkdir()
    assert ask("cd .. && python -m pytest -q", gated) is None   # the parent has no final gate
    assert ask("cd top && cd gated && python -m pytest -q", tmp_path)   # two steps in
    assert ask("cd ../loose && python -m pytest -q", gated) is None
    assert ask(f"cd {gated.as_posix()} ; python -m pytest -q", tmp_path / "top" / "loose")
    drive = gated.as_posix()   # Git Bash writes `C:/x` as `/c/x`
    assert ask(f"cd /{drive[0].lower()}{drive[2:]} && python -m pytest -q", tmp_path)
    moved = {"tool_name": "PowerShell", "cwd": str(tmp_path / "top"),
             "tool_input": {"command": "Set-Location 'gated'; python -m pytest -q 'a;b'"}}
    assert verdict(moved)   # `'a;b'` is one word to PowerShell too
