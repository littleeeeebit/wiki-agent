"""The effect guard: commands reduce to effects, and a call passes only inside the task's grant."""

import io
import json
import os
from pathlib import Path
import random
import runpy
import shlex
import subprocess
import sys
import tempfile
import time

import pytest

import guard
import guard_grant
from common import host
from guard import DEPTH, Ctx, contained, derive, render, verdict

TOOL = Path(__file__).parent
CORPUS = json.loads((TOOL.parent / "eval/guard/corpus.json").read_text(encoding="utf-8"))
C = CORPUS["context"]
CTX = Ctx("posix", frozenset({C["cwd"]}), C["home"], C["temp"], True, remotes=tuple(map(tuple, C["remotes"])))


def effects(command: str, dialect: str = "posix", ctx: Ctx = CTX) -> set:
    return {render(e) for e in derive(command, dialect, ctx)}


def covers(after: set, before: set) -> bool:
    """`after` is at least `before` in the effect lattice: a superset, or `top`, which absorbs everything."""
    return before <= after or any(e.startswith("top(") for e in after)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An attached checkout outside the temporary roots, with a gate, a check and a `[guard]` table."""
    temp = tmp_path / "temp"
    temp.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("WIKI_AGENT_MANAGED", "1")
    top = tmp_path / "work" / "repo"
    (top / ".git").mkdir(parents=True)
    (top / ".git/HEAD").write_text("ref: refs/heads/task\n", encoding="utf-8")
    (top / ".git/config").write_text('[remote "origin"]\n\turl = https://github.com/owner/repo.git\n', encoding="utf-8")
    (top / ".wiki").mkdir()
    (top / ".wiki/adapter.toml").write_text(
        '[slots]\ngate_cmd = "python -m ruff check tool"\n'
        '[checks.web]\ncmd = "npm --prefix web run build"\n'
        '[guard]\nwrite_roots = ["../shared"]\nnetwork_hosts = ["pypi.org"]\nexecutables = ["npx"]\n',
        encoding="utf-8")
    return top


def call(top: Path, command: str = "", tool: str = "Bash", host_name: str = "claude", **given) -> str | None:
    payload = {"tool_name": tool, "tool_input": given or {"command": command}, "cwd": str(top)}
    answer = verdict(payload, host=host_name)
    return answer and answer["hookSpecificOutput"]["permissionDecisionReason"]


# -- Parsing and reduction ----------------------------------------------------

@pytest.mark.parametrize("command,dialect", [
    ("bash -c 'rm -rf /c/x/y'", "posix"),
    ("sh -c \"rm -rf /c/x/y\"", "posix"),
    ("ls; bash -lc 'cd /c/x && rm -rf y'", "posix"),
    ("echo $(bash -c 'rm -rf /c/x/y')", "posix"),
    ("echo `rm -rf /c/x/y`", "posix"),
    ("true && (rm -rf /c/x/y) || false", "posix"),
    ("cat a | xargs echo | rm -rf /c/x/y", "posix"),
    ("pwsh -NoProfile -Command \"Remove-Item C:/x/y -Recurse\"", "posix"),
    ("powershell -Command 'Remove-Item C:\\x\\y'", "posix"),
    ("cmd /c \"rmdir /s /q C:\\x\\y\"", "posix"),
    ("cmd //c \"del C:\\x\\y\"", "posix"),
    ("bash -c \"pwsh -Command 'cmd /c del C:\\x\\y'\"", "posix"),
    ("pwsh -Command { Remove-Item C:/x/y }", "ps"),
    ("cmd /c rd /s /q C:\\x\\y", "ps"),
])
def test_nested_shell_strings_are_parsed_again(command, dialect):
    assert "delete(C:/x/y)" in effects(command, dialect)


def test_nesting_is_recursed_to_a_fixed_depth():
    command = "rm -rf /c/x/y"
    for depth in range(DEPTH + 2):
        found = effects(command)
        assert ("delete(C:/x/y)" in found) if depth <= DEPTH else any("nesting deeper" in e for e in found)
        command = "bash -c " + shlex.quote(command)


@pytest.mark.parametrize("command,dialect,reason", [
    ('eval "$X"', "posix", "eval"),
    ("source ./env.sh", "posix", "source"),
    (". ./env.sh", "posix", "."),
    ("powershell -EncodedCommand ZQBjAGgAbwA=", "posix", "encoded"),
    ("pwsh -enc ZQBjAGgAbwA=", "ps", "encoded"),
    ("Invoke-Expression $code", "ps", "invoke-expression"),
    ("rm $X/out", "posix", "variable in a delete path"),
    ("echo hi > $OUT", "posix", "variable in a write path"),
    ("echo hi > \"$(mktemp)\"", "posix", "substitution in a write path"),
    ("rm *.pyc", "posix", "glob in a delete path"),
    ("touch out-[0-9].txt", "posix", "glob in a write path"),
    ("Remove-Item $path", "ps", "variable in a delete path"),
    ("& $env:TOOL x", "ps", "variable as the command name"),
    ("bash -c \"$SCRIPT\"", "posix", "variable in a nested shell"),
    ("echo `rm x", "posix", "unclosed backtick"),
    ("echo \"$(rm x\"", "posix", "unclosed"),
    ("Write-Output 'abc", "ps", "unclosed quote"),
    ("PATH=/evil:$PATH python x.py", "posix", "PATH"),
    ("git -c core.sshCommand=evil fetch", "posix", "git config key"),
    ("[IO.File]::WriteAllText('a', 'b')", "ps", "WriteAllText"),
])
def test_undecidable_forms_reduce_to_top(command, dialect, reason):
    tops = [e for e in effects(command, dialect) if e.startswith("top(")]
    assert any(reason.lower() in e.lower() for e in tops), tops


@pytest.mark.parametrize("command,expected", [
    ("echo hi > out.txt 2>>err.log", {"write(C:/Users/me/PycharmProjects/wiki-agent/out.txt)",
                                      "write(C:/Users/me/PycharmProjects/wiki-agent/err.log)"}),
    ("cat <<'EOF' > notes.md\n$(rm -rf /)\nEOF", {"write(C:/Users/me/PycharmProjects/wiki-agent/notes.md)"}),
    ("echo 2>&1 >/dev/null", set()),
    ("curl -sS https://example.org/x -o /tmp/y", {"network(example.org)", "write(C:/Users/me/AppData/Local/Temp/y)"}),
    ("S=/c/x; rm -rf \"$S/y\"", {"delete(C:/x/y)"}),
])
def test_redirections_and_bound_constants(command, expected):
    assert effects(command) - {"read"} == expected


def test_appending_tokens_never_removes_an_effect():
    """Over complete commands: a string left open can be closed by what follows, so it is not a prefix."""
    rng = random.Random(1101)
    tail = ["x", "-f", "--force", "out.txt", ";", "&&", "||", "|", "rm", "tee", "> o.txt", "2>&1", "echo",
            "bash -c 'touch n'", "git", "push", "$(rm q)", "curl https://e.example", "'a b'", "\n", "cd ..", "S=1"]
    for entry in CORPUS["commands"]:
        before = effects(entry["command"], entry["dialect"])
        if any("unclosed" in e for e in before):
            continue
        for _ in range(3):
            longer = entry["command"] + " " + " ".join(rng.choice(tail) for _ in range(rng.randint(1, 4)))
            assert covers(effects(longer, entry["dialect"]), before), longer


# -- Paths --------------------------------------------------------------------

@pytest.mark.parametrize("path,root,inside", [
    ("C:/r/a/../b", "C:/r", True),
    ("C:/r/../x", "C:/r", False),
    ("c:\\R\\a", "C:/r", True),
    ("C:/rx/a", "C:/r", False),
    ("D:/r/a", "C:/r", False),
    ("//server/share/r/a", "C:/r", False),
])
def test_windows_containment(path, root, inside):
    assert contained(path, [root], windows=True) is inside


@pytest.mark.parametrize("path,root,inside", [
    ("/r/a/../b", "/r", True),
    ("/r/a/../../x", "/r", False),
    ("/r/./a", "/r", True),
    ("/rx/a", "/r", False),
])
def test_posix_containment(path, root, inside):
    assert contained(path, [root], windows=False) is inside


@pytest.mark.parametrize("command,expected", [
    ("touch /c/r/a", "write(C:/r/a)"),
    ("touch /C/r/../a", "write(C:/a)"),
    ("touch C:a", "write(C:/Users/me/PycharmProjects/wiki-agent/a)"),
    ("touch '\\\\?\\C:\\r\\a'", "write(C:/r/a)"),
    ("touch /usr/x", "write(//msys/usr/x)"),
    ("touch ~/a", "write(C:/Users/me/a)"),
])
def test_drive_letter_forms_resolve_to_one_spelling(command, expected):
    assert expected in effects(command)


def test_a_relative_path_after_an_unknown_cd_is_top():
    assert any("unknown cd" in e for e in effects("cd \"$X\"; touch a"))


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")
def test_a_junction_out_of_the_root_is_outside(tmp_path):
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "link"), str(out)], check=True, capture_output=True)
    assert not contained(str(root / "link" / "f.txt"), [str(root)])
    assert contained(str(root / "f.txt"), [str(root)])


def test_a_symlink_out_of_the_root_is_outside(tmp_path):
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    try:
        (root / "link").symlink_to(out, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges here")
    assert not contained(str(root / "link" / "f.txt"), [str(root)])


# -- The grant and the verdict -------------------------------------------------

def test_calls_inside_the_grant_pass(repo):
    for command in ("python -m ruff check tool", "npm --prefix web run build", "npx tsc -b",
                    "git add -A && git commit -qm x && git push origin HEAD", "gh pr view 3 --json title",
                    "rm -rf build && echo hi > out.txt", "curl -s https://pypi.org/simple/x",
                    "curl -s http://localhost:8000/api", "python tool/search \"query\"",
                    "touch ../shared/f", "echo hi > /tmp/x.log", "ls ~ && cat /etc/hosts"):
        assert call(repo, command) is None, command


def test_each_denial_names_the_effect_and_the_grant_that_would_allow_it(repo):
    reason = call(repo, "cargo build && curl https://evil.example/x && rm -rf ../other")
    assert "spawn(cargo)" in reason and "`[guard] executables` 에 `cargo`" in reason
    assert "network(evil.example)" in reason and "`[guard] network_hosts` 에 `evil.example`" in reason
    assert "delete(" in reason and "other" in reason and "`[guard] write_roots`" in reason
    assert reason.splitlines()[-1].startswith("허가: 쓰기 루트") and "npx" in reason and "pypi.org" in reason


def test_a_nested_shell_no_longer_bypasses_the_guard(repo):
    assert "delete(" in call(repo, "bash -c 'rm -rf ../other'")
    assert "delete(" in call(repo, "Remove-Item ../other -Recurse", tool="PowerShell")
    # Codex on Windows runs PowerShell; elsewhere the same text is an unknown program.
    codex = call(repo, tool="exec_command", host_name="codex", cmd="Remove-Item ../other -Recurse")
    assert ("delete(" if os.name == "nt" else "spawn(") in codex


@pytest.mark.parametrize("command,tool,marker", [
    # F1 — a symlink created earlier redirects later paths; the link is undecidable.
    ("ln -s ../other link && rm link/victim", "Bash", "판단할 수 없는"),
    # F2 — a directory destination writes the protected descendant it actually names.
    ("cp /tmp/adapter.toml .wiki/", "Bash", "허가를 정하는 파일"),
    # F3 — a constructor on a non-inert type creates a file; the receiver is unmodelled.
    ("[System.IO.FileStream]::new('C:/outside/victim', 'Create').Dispose()", "PowerShell", "판단할 수 없는"),
    # F4 — a comma-joined path array hides a second, escaping target.
    ("Remove-Item -LiteralPath 'keep','../other/victim'", "PowerShell", "판단할 수 없는"),
    # F5 — a git read verb still writes through --output.
    ("git diff HEAD~1 HEAD --output=../victim.patch", "Bash", "쓰기 루트 밖이다"),
    # F6 — a function runs in the caller's context, not where it was defined.
    ("f() { touch victim; }; cd ..; f", "Bash", "판단할 수 없는"),
    # F7 — ANSI-C quoting encodes the path separators the shell will decode.
    ("rm -- $'./\\x2e\\x2e/victim'", "Bash", "판단할 수 없는"),
    # F8 — a shell startup file runs before the given command.
    ("bash --rcfile /tmp/payload.rc -ic 'true'", "Bash", "판단할 수 없는"),
    # F9 — a reader option launches an ungranted preprocessor.
    ("rg --pre ./helper needle README.md", "Bash", "허가된 실행 파일이 아니다"),
])
def test_forms_the_model_cannot_follow_are_denied(repo, command, tool, marker):
    reason = call(repo, command, tool=tool)
    assert reason and marker in reason, reason


def test_nested_shells_cannot_bypass_the_declared_git_blocks(monkeypatch):
    """F10 — a declared `Bash(...)` block holds inside `bash -c`, `pwsh -Command` and `$(...)`."""
    import apply
    import deny

    monkeypatch.setattr(apply, "declared", lambda: (
        {"Bash(git reset --hard*)": ["craft/destructive-git-guards"],
         "Bash(git clean -fdx*)": ["craft/destructive-git-guards"]}, {}))

    def blocked(command, tool="Bash"):
        answer = deny.verdict({"tool_name": tool, "tool_input": {"command": command}})
        return answer and answer["hookSpecificOutput"]["permissionDecisionReason"]

    assert blocked("git reset --hard")                              # the direct block still fires
    assert blocked("bash -c 'git reset --hard'")
    assert blocked("ls && sh -lc \"git reset --hard\"")
    assert blocked("pwsh -Command 'git reset --hard'", tool="PowerShell")
    assert blocked("echo $(git clean -fdx)")
    assert blocked("git clean -fdx") and not blocked("git status")


def test_undecidable_calls_are_denied(repo):
    assert "판단할 수 없는" in call(repo, 'eval "$X"')


def test_files_that_decide_the_grant_are_never_granted(repo):
    for command in ("echo x > .git/config", "rm -rf .wiki", "touch .claude/settings.json", "cp x verification.toml"):
        assert "허가를 정하는 파일" in call(repo, command), command


def test_edit_targets_must_lie_inside_the_write_roots(repo):
    assert call(repo, tool="Edit", file_path=str(repo / "a.py"), old_string="a", new_string="b") is None
    assert "write(" in call(repo, tool="Write", file_path=str(repo.parent / "b.py"), content="")
    assert "write(" in call(repo, tool="MultiEdit", file_path=str(repo / "../c.py"), edits=[])
    assert "허가를 정하는 파일" in call(repo, tool="Edit", file_path=str(repo / ".wiki/adapter.toml"))
    patch = "*** Begin Patch\n*** Add File: {}\n+x\n*** Delete File: a.py\n*** End Patch"
    assert call(repo, tool="apply_patch", input=patch.format(repo / "new.py")) is None
    assert "write(" in call(repo, tool="apply_patch", input=patch.format(repo.parent / "new.py"))


def test_a_plan_task_writes_only_under_its_artifact_root(repo, monkeypatch, tmp_path):
    hub = tmp_path / "hub" / "tool"
    (hub.parent / "raw/specs/repo").mkdir(parents=True)
    (hub.parent / "raw/specs/repo/task.json").write_text(json.dumps(
        {"review_profile": "plan", "artifact_root": "docs/plans/x", "worktree": str(repo)}), encoding="utf-8")
    monkeypatch.setattr(guard_grant, "HERE", hub)
    assert call(repo, "touch docs/plans/x/1-step.md") is None
    assert "write_roots" in call(repo, "touch tool/a.py")


def test_unmanaged_sessions_and_unattached_folders_pass(repo, monkeypatch, tmp_path):
    assert call(tmp_path, "rm -rf /c/x") is None
    monkeypatch.delenv("WIKI_AGENT_MANAGED")
    assert call(repo, "rm -rf ../other") is None


def test_the_guard_answers_each_corpus_call_within_200ms(repo):
    """Every corpus command keeps its recorded effects, and the hook decides it in time."""
    assert len(CORPUS["commands"]) >= 200
    call(repo, "true")
    slowest = 0.0
    for entry in CORPUS["commands"]:
        assert sorted(effects(entry["command"], entry["dialect"])) == entry["effects"], entry["command"]
        took = []
        for _ in range(3):              # best of three: a busy machine's pause is not the guard's time
            start = time.perf_counter()
            call(repo, entry["command"], tool="PowerShell" if entry["dialect"] == "ps" else "Bash")
            took.append(time.perf_counter() - start)
        slowest = max(slowest, min(took))
    assert slowest < 0.2, slowest


def test_the_hook_subprocess_adds_a_bounded_latency(repo):
    """What the PreToolUse hook costs end to end: the guard process over a baseline process."""
    env = host.environment({"WIKI_AGENT_MANAGED": "1", "TEMP": os.environ["TEMP"],
                            "TMP": os.environ["TMP"], "TMPDIR": os.environ["TMPDIR"]})
    samples = ["git add -A && git commit -qm x && git push origin HEAD", "rm -rf ../other",
               "bash -c 'git reset --hard'", "npx tsc -b | tee out.txt",
               "pwsh -Command 'Remove-Item ../other -Recurse'"]

    def run(args, stdin):
        start = time.perf_counter()
        subprocess.run(args, input=stdin, capture_output=True, env=env, timeout=30)
        return time.perf_counter() - start

    baseline = min(run([sys.executable, "-c", "pass"], b"") for _ in range(3))
    worst = 0.0
    for command in samples:
        payload = json.dumps({"tool_name": "PowerShell" if "pwsh" in command else "Bash",
                              "tool_input": {"command": command}, "cwd": str(repo)}).encode("utf-8")
        worst = max(worst, min(run([sys.executable, str(TOOL / "guard.py")], payload) for _ in range(3)))
    # The hook's own work (process start aside) stays well under a second per call.
    assert worst - baseline < 1.0, (baseline, worst)


# -- The hook entry point -----------------------------------------------------

def test_the_entry_point_answers_in_utf8_whatever_the_console(repo):
    env = host.environment({"PYTHONIOENCODING": "cp949", "WIKI_AGENT_MANAGED": "1", "TEMP": os.environ["TEMP"],
                            "TMP": os.environ["TMP"], "TMPDIR": os.environ["TMPDIR"]})
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf ../other"}, "cwd": str(repo)})
    done = subprocess.run([sys.executable, str(TOOL / "guard.py")], input=payload.encode("utf-8"),
                          capture_output=True, env=env, timeout=30)
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout.decode("utf-8"))["hookSpecificOutput"]
    assert answer["permissionDecision"] == "deny" and "쓰기 루트 밖이다" in answer["permissionDecisionReason"]
    broken = subprocess.run([sys.executable, str(TOOL / "guard.py")], input=b"{", capture_output=True,
                            env=env, timeout=30)
    assert broken.returncode == 0 and not broken.stdout and b"JSONDecodeError" in broken.stderr


def test_any_exception_fails_open(monkeypatch, capsys):
    def boom(*_args, **_kw):
        raise RuntimeError("bug")

    monkeypatch.setattr(guard, "verdict", boom)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
    monkeypatch.setattr(sys, "stdout", out)
    runpy.run_path(str(TOOL / "guard.py"), run_name="__main__")
    assert out.encoding == "utf-8" and sys.stdin.encoding == "utf-8"
    assert "hook skipped: RuntimeError" in capsys.readouterr().err
