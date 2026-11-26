"""A repair round's pytest run names the touched files, never the whole set (`tool/wide_tests.py`)."""

import os
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
    assert ask("python -m pytest tool/test_hook.py > hook-tests.log 2>&1 &", root) is None   # sent to the background
    assert ask("python -m pytest -q &> all.log & wait", root)   # `&>` is a redirection; the run is still wide
    assert ask("cd .. & python -m pytest -q", root)   # a move sent to the background stays in its subshell
    assert ask("python -m pytest tool/test_hook.py &> hook-tests.log & wait", root) is None   # `&>` and its file
    assert ask("python -m pytest tool/test_hook.py &>> hook-tests.log", root) is None
    assert ask("cd .. &\npython -m pytest -k test", root)   # still in the background across the newline
    assert ask("cd .. && python -m pytest -q & python -m pytest -k test; wait", root)   # `&` takes the whole list
    assert ask("cd .. && python -m pytest -q tool/test_a.py & wait", root) is None   # and runs it where it moved


def test_joining_a_long_command_takes_time_in_proportion_to_its_length():
    import time
    from wide_tests import joined
    line = "A configuration example with a 'quoted value'.\n"
    took = []
    for n in (2048, 8192):
        start = time.perf_counter()
        joined(f"cat <<'DOC'\n{line * n}DOC\n", True)
        took.append(time.perf_counter() - start)
    assert took[1] < took[0] * 12, took   # four times the text, well under sixteen times the work


def test_every_option_that_takes_a_value_has_it_read_as_the_value(tmp_path):
    root = repo(tmp_path)
    assert ask("python -m pytest --override-ini addopts=-x tool/test_hook.py", root) is None
    assert ask("py -m pytest -k test --junit-xml report.py", root)   # `report.py` is the report, not a test
    assert ask("python -m pytest --import-mode importlib --color yes tool/test_a.py", root) is None
    assert ask('python -m pytest --junitxml="review results.xml" tool/test_hook.py', root) is None   # one word
    assert ask("python -m pytest -k test --debug debug.py", root)   # an optional value is taken when it stands apart
    assert ask("python -m pytest --debug -q tool/test_a.py 2>&1", root) is None   # but never another option


def test_a_move_the_shell_may_skip_or_undo_leaves_every_folder_it_may_run_in(tmp_path):
    gated = repo(tmp_path / "top" / "gated")
    for command in ("true || cd ..; python -m pytest -k test",   # `cd` runs only if `true` fails
                    "pushd .. >/dev/null && popd >/dev/null && python -m pytest -k test",   # back where it began
                    "cd .. | cat; python -m pytest -q",   # a pipeline's `cd` stays in its subshell
                    "false && cd ..; python -m pytest -q"):
        assert ask(command, gated), command
    assert ask("pushd .. && python -m pytest -q && popd", gated) is None
    assert ask("cd .. || exit 1; python -m pytest -q", gated) is None   # `..` exists, so the move succeeds
    for command in ("cd ../absent; python -m pytest -k test",   # a move that fails stays
                    "cd .. && cd - && python -m pytest -k test",   # `cd -` returns
                    "false &&\n  cd ..; python -m pytest -k test",   # a list broken after `&&` goes on
                    "false && \\\n cd ..; python -m pytest -k test"):   # and so does a continued line
        assert ask(command, gated), command
    assert ask("mkdir -p ../made && cd ../made && python -m pytest -q", gated) is None   # it exists by then
    assert ask("mkdir -p ../new && cd ../new || exit 1; python -m pytest -q", gated) is None   # failing, it ends
    assert ask("cd -P .. && python -m pytest -q", gated) is None   # a switch is not the folder
    for command, expected in (("Set-Location -LiteralPath ..; python -m pytest -q", None),
                              ("Set-Location -Path:..; python -m pytest -q", None),
                              ("Set-Location -PassThru ..; python -m pytest -q", None),
                              ("cd ..; Set-Location -LiteralPath gated; python -m pytest -q", "deny")):
        payload = {"tool_name": "PowerShell", "cwd": str(gated), "tool_input": {"command": command}}
        assert bool(verdict(payload)) == bool(expected), command


def test_a_comment_runs_nothing_and_selects_nothing(tmp_path):
    root = repo(tmp_path)
    assert ask("python -m pytest tool/test_hook.py # selected file", root) is None
    assert ask("echo ready # comment; python -m pytest -k test", root) is None
    assert ask('echo "a #b"; python -m pytest -q', root)   # a `#` inside quotes hides nothing after them
    assert ask("python -m pytest -q \\\n  tool/test_a.py", root) is None   # one command over two lines
    assert ask("py\\\nthon -m py\\\ntest -k test", root)   # Bash joins a word across its continued line
    assert ask("echo build\\ #1; python -m pytest -k test", root)   # an escaped space keeps `#1` in its word
    assert ask("# Windows build output: .\\bin\\\npython -m pytest -k test", root)   # no escape in a comment
    assert ask("echo 'a \\\n'; python -m pytest -k test", root)   # nor inside single quotes
    assert ask('echo a#b \\\n tool/x.py; python -m pytest -q', root)   # a `#` inside a word joins nothing
    assert ask("python -m pytest tool/test_hook.py \\\n# only the hook test", root) is None   # the space stays
    assert ask("python -m pytest -q tool/test_a\\\n#b.py", root) is None   # one word, `tool/test_a#b.py`


def test_a_quoted_folder_before_the_interpreter_is_still_a_pytest_run(tmp_path):
    root = repo(tmp_path)
    assert ask('"/c/Program Files/Python"/python.exe -m pytest -k test', root)
    assert ask("'C:/Py thon'/Scripts/pytest.exe -q tool/test_a.py", root) is None
    for command, wide in (("& 'C:\\Program Files\\Python\\python.EXE' -m pytest -k test", True),
                          ("& 'C:\\Program Files\\Python\\python.exe' -m pytest tool\\test_a.py", False)):
        payload = {"tool_name": "PowerShell", "cwd": str(root), "tool_input": {"command": command}}
        assert bool(verdict(payload)) == wide, command
    assert ask("'C:/Py/Scripts/PYTEST.EXE' -k test", root)   # the same file to Windows, in capitals


def test_each_run_is_read_where_the_cds_before_it_left_the_shell(tmp_path):
    gated = repo(tmp_path / "top" / "gated")
    (tmp_path / "top" / "loose").mkdir()
    assert ask("cd .. && python -m pytest -q", gated) is None   # the parent has no final gate
    assert ask("cd top && cd gated && python -m pytest -q", tmp_path)   # two steps in
    assert ask("cd ../loose && python -m pytest -q", gated) is None
    assert ask(f"cd {gated.as_posix()} ; python -m pytest -q", tmp_path / "top" / "loose")
    if os.name == "nt":   # Git Bash writes `C:/x` as `/c/x`
        drive = gated.as_posix()
        assert ask(f"cd /{drive[0].lower()}{drive[2:]} && python -m pytest -q", tmp_path)
    moved = {"tool_name": "PowerShell", "cwd": str(tmp_path / "top"),
             "tool_input": {"command": "Set-Location 'gated'; python -m pytest -q 'a;b'"}}
    assert verdict(moved)   # `'a;b'` is one word to PowerShell too
