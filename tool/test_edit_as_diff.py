"""Where `edit_as_diff` blocks, and where it does not."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tool.edit_as_diff import verdict

HOOK = Path(__file__).with_name("edit_as_diff.py")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository tracking two files, with an untracked log and three tracked modules beside them."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "existing.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# 제목\n", encoding="utf-8")
    for name in ("a", "b", "c"):
        (tmp_path / "src" / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "run.log").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "README.md", "src"], cwd=tmp_path, check=True)
    return tmp_path


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command",
    [
        'cat > src/existing.py <<PY\nx = 2\nPY',
        'cat >> README.md <<MD\n한 줄 더\nMD',
        'cd /repo && cat > "src/existing.py" <<PY\nPY',
        "python - <<'PY'\nfrom pathlib import Path\nPath('src/existing.py').write_text('x')\nPY",
        "python - <<'PY'\nopen('README.md', 'w').write('x')\nPY",
        "tee src/existing.py <<PY\nPY",
        "echo hi > README.md",
        # The shape that takes the path in a variable and writes through it
        # later. One session did more than ten multi-point replacements this
        # way without the hook firing once, because the two python patterns
        # above only match when `Path(...)` and `.write_` sit together.
        "python - <<'PY'\nimport pathlib\np = pathlib.Path('src/existing.py')\nt = p.read_text()\np.write_text(t.replace('a', 'b'))\nPY",
        # The interpreter called by absolute path. This session actually did
        # write it that way.
        "/c/py/python.exe - <<'PY'\nimport pathlib\np = pathlib.Path('README.md')\np.write_text('x')\nPY",
        # The shape that takes `open` into a variable.
        "python - <<'PY'\nf = open('README.md', 'w')\nf.write('x')\nPY",
        # `Path.open('w')`.
        "python - <<'PY'\nfrom pathlib import Path\np = Path('src/existing.py')\nwith p.open('w') as f:\n    f.write('x')\nPY",
        # The assignment not at the start of a line. A one-line `-c` is the
        # exact shape this rule aims at, and the first version — which looked
        # only at the start of a line — missed it entirely.
        "python -c \"import pathlib; p = pathlib.Path('README.md'); p.write_text('x')\"",
        # The same thing, arriving inside a heredoc.
        "python - <<'PY'\nimport pathlib; p = pathlib.Path('src/existing.py'); p.write_text('x')\nPY",
    ],
)
def test_rewriting_a_file_that_is_already_there_is_refused(
    repo: Path, command: str
) -> None:
    answer = verdict(bash(command), repo)

    assert answer is not None, command
    decision = answer["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Edit" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        # A new file. Nothing to erase, so it cannot become a diff — the
        # page's stated exception.
        "cat > src/brand_new.py <<PY\nPY",
        # Outside the repository. There is nowhere for a diff to live.
        'cat > "$TMPDIR/scratch.py" <<PY\nPY',
        "python - <<'PY'\nPath('/tmp/x.py').write_text('x')\nPY",
        # A read-only command. There is redirection, but not onto a file.
        "grep -rn foo src/ > /dev/null",
        "pytest -q 2>&1 | tail -3",
        "git diff --stat > /dev/null 2>&1",
        # Writes no file at all.
        "ls -la src/",
        "git log --oneline -5",
        # Only reads a repository file; the result goes to the screen. The
        # variable tracking catching this too would be a false positive, and a
        # false positive that stops the work gets the hook turned off.
        "python - <<'PY'\nimport pathlib\np = pathlib.Path('src/existing.py')\nprint(len(p.read_text()))\nPY",
        # Reads a repository file and writes into the scratch directory. What
        # is being written is not in the repository.
        "python - <<'PY'\nimport pathlib\nsrc = pathlib.Path('src/existing.py')\nout = pathlib.Path('/tmp/copy.py')\nout.write_text(src.read_text())\nPY",
        # A file git does not track has no diff to lose: a log, generated output, ignored paths.
        "echo new > run.log",
        "sed -i 's/old/new/' run.log",
        # One substitution over three or more tracked files is the batch Edit cannot do.
        "sed -i 's/x = 1/x = 2/' src/a.py src/b.py src/c.py",
        "sed -i -E 's/x/y/g' src/*.py",
        "rg -l 'x = 1' src | xargs sed -i 's/x = 1/x = 2/'",
        "find src -name '*.py' -exec sed -i 's/x/y/' {} +",
        # Not in place.
        "sed -n '1,5p' src/existing.py",
        "sed 's/x/y/' src/existing.py > /dev/null",
    ],
)
def test_what_it_must_not_block(repo: Path, command: str) -> None:
    assert verdict(bash(command), repo) is None, command


@pytest.mark.parametrize("command", [
    "sed -i 's/x = 1/x = 2/' src/existing.py",
    "sed -i.bak -e 's/x/y/' README.md src/a.py",
    "cd src && sed --in-place 's/x/y/' a.py",
    "sed -Ei 's/x/y/' src/existing.py",
    'F=src/existing.py; sed -i "s/x/y/" "$F"; git checkout -- $F',
    "sed -i \\\n  's/x/y/' \\\n  README.md",
])
def test_sed_in_place_on_one_or_two_tracked_files_is_refused(repo: Path, command: str) -> None:
    answer = verdict(bash(command), repo)
    assert answer and answer["hookSpecificOutput"]["permissionDecision"] == "deny", command


def test_write_onto_an_untracked_file_passes(repo: Path) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(repo / "run.log"), "content": "x"}}
    assert verdict(payload, repo) is None


def test_a_commit_message_that_quotes_a_command_is_not_a_command(repo: Path) -> None:
    """The hook's own false positive, caught the first time it was used.

    A commit message was being passed through a heredoc and that message
    quoted a blocked command; the hook read the string and blocked the commit.
    A review instruction, a PR body or a document quoting a command is normal
    and common — and any text explaining this rule necessarily quotes the
    command it is about.
    """
    command = (
        "git commit -F - <<'MSG'\n"
        "fix: 되쓰기를 막는다\n"
        "\n"
        "cat > docs/README.md 같은 모양이 45회 나왔다.\n"
        "MSG"
    )

    assert verdict(bash(command), repo) is None


def test_a_heredoc_into_an_interpreter_is_still_code(repo: Path) -> None:
    """Dropping a data body must not drop a code body with it."""
    command = (
        "python - <<'PY'\n"
        "from pathlib import Path\n"
        "Path('README.md').write_text('x')\n"
        "PY"
    )

    assert verdict(bash(command), repo) is not None


def test_a_redirection_on_the_command_line_survives_a_data_heredoc(repo: Path) -> None:
    """Even with a data body, the command line's redirection is outside it."""
    command = "cat > README.md <<'MD'\n한 줄\nMD"

    assert verdict(bash(command), repo) is not None


def test_write_onto_an_existing_file_is_refused(repo: Path) -> None:
    """The page decides that `Write` is for a new file and nothing else."""
    answer = verdict(
        {"tool_name": "Write", "tool_input": {"file_path": "README.md"}}, repo
    )

    assert answer is not None
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_onto_a_new_file_passes(repo: Path) -> None:
    assert (
        verdict({"tool_name": "Write", "tool_input": {"file_path": "NEW.md"}}, repo)
        is None
    )


def test_the_edit_tool_is_never_looked_at(repo: Path) -> None:
    """Blocking the very tool the refusal recommends is a dead end."""
    assert (
        verdict({"tool_name": "Edit", "tool_input": {"file_path": "README.md"}}, repo)
        is None
    )


def test_a_broken_payload_passes_rather_than_stopping_the_session(repo: Path) -> None:
    """`craft/hooks-fail-open`. A broken hook must not stop the work."""
    for payload in [{}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": {}}]:
        assert verdict(payload, repo) is None


def test_it_runs_as_a_process_and_answers_on_stdout(repo: Path) -> None:
    """The hook is called as a process, so this also checks that a refusal
    containing Korean survives the pipe."""
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cat > README.md <<MD\n한 줄\nMD"},
            "cwd": str(repo),
        }
    )
    done = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        # The encoding is not left to the locale. A child emitting Korean and
        # a parent reading cp949 dies quietly in the reader thread — the third
        # face of `craft/hooks-fail-open`.
        encoding="utf-8",
        errors="replace",
        # A non-zero exit code is part of what this test judges. Whatever
        # happens, the hook has to return 0, and that is asserted below.
        check=False,
    )

    assert done.returncode == 0
    answer = json.loads(done.stdout)
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "README.md" in answer["systemMessage"]
