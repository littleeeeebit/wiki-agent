"""The effect guard: commands reduce to effects, and a call passes only inside the task's grant."""

import io
import json
import os
from dataclasses import replace
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
# The corpus runs in its checkout: the remotes are that checkout's.
CTX = Ctx("posix", frozenset({C["cwd"]}), C["home"], C["temp"], True, remotes=tuple(map(tuple, C["remotes"])),
          repo=C["cwd"])


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
        '[guard]\nwrite_roots = ["../shared"]\nnetwork_hosts = ["pypi.org"]\n',
        encoding="utf-8")
    return top


def call(top: Path, command: str = "", tool: str = "Bash", host_name: str = "claude", **given) -> str | None:
    payload = {"tool_name": tool, "tool_input": given or {"command": command}, "cwd": str(top)}
    answer = verdict(payload, host=host_name)
    return answer and answer["hookSpecificOutput"]["permissionDecisionReason"]


# -- Parsing and reduction ----------------------------------------------------

@pytest.mark.parametrize("command,dialect", [
    ("bash -c 'echo x > /c/x/y'", "posix"),
    ("sh -c \"echo x > /c/x/y\"", "posix"),
    ("echo $(bash -c 'echo x > /c/x/y')", "posix"),
    ("echo `echo x > /c/x/y`", "posix"),
    ("true && (echo x > /c/x/y) || false", "posix"),
    ("cat a | grep b | echo x > /c/x/y", "posix"),
    ("pwsh -NoProfile -Command \"'x' > C:/x/y\"", "posix"),
    ("powershell -Command 'Write-Output x > C:\\x\\y'", "posix"),
    ("cmd /c \"echo x > C:\\x\\y\"", "posix"),
    ("cmd //c \"echo x > C:\\x\\y\"", "posix"),
    ("bash -c \"pwsh -Command 'cmd /c echo x > C:\\x\\y'\"", "posix"),
    ("pwsh -Command { 'x' > C:/x/y }", "ps"),
    ("cmd /c echo x > C:\\x\\y", "ps"),
])
def test_nested_shell_strings_are_parsed_again(command, dialect):
    assert "write(C:/x/y)" in effects(command, dialect)


def test_nesting_is_recursed_to_a_fixed_depth():
    command = "echo x > /c/x/y"
    for depth in range(DEPTH + 2):
        found = effects(command)
        assert ("write(C:/x/y)" in found) if depth <= DEPTH else any("nesting deeper" in e for e in found)
        command = "bash -c " + shlex.quote(command)


@pytest.mark.parametrize("command,dialect,reason", [
    ('eval "$X"', "posix", "eval"),
    ("source ./env.sh", "posix", "source"),
    (". ./env.sh", "posix", "."),
    ("powershell -EncodedCommand ZQBjAGgAbwA=", "posix", "encoded"),
    ("pwsh -enc ZQBjAGgAbwA=", "ps", "encoded"),
    ("Invoke-Expression $code", "ps", "invoke-expression"),
    ("echo hi > $X/out", "posix", "variable in a write path"),
    ("echo hi > $OUT", "posix", "variable in a write path"),
    ("echo hi > \"$(mktemp)\"", "posix", "substitution in a write path"),
    ("echo hi > *.pyc", "posix", "glob in a write path"),
    ("echo hi > out-[0-9].txt", "posix", "glob in a write path"),
    ("'x' > $path", "ps", "variable in a write path"),
    ("& $env:TOOL x", "ps", "variable as the command name"),
    ("bash -c \"$SCRIPT\"", "posix", "variable in a nested shell"),
    ("echo `rm x", "posix", "unclosed backtick"),
    ("echo \"$(rm x\"", "posix", "unclosed"),
    ("Write-Output 'abc", "ps", "unclosed quote"),
    ("PATH=/opt/tools:$PATH python x.py", "posix", "PATH"),
    ("git -c core.sshCommand=helper status", "posix", "-c"),
    ("[IO.File]::WriteAllText('a', 'b')", "ps", "WriteAllText"),
])
def test_undecidable_forms_reduce_to_top(command, dialect, reason):
    tops = [e for e in effects(command, dialect) if e.startswith("top(")]
    assert any(reason.lower() in e.lower() for e in tops), tops


@pytest.mark.parametrize("command,expected", [
    ("echo hi > out.txt 2>>err.log", {"write(C:/Users/me/PycharmProjects/wiki-agent/out.txt)",
                                      "write(C:/Users/me/PycharmProjects/wiki-agent/err.log)"}),
    ("cat <<'EOF' > notes.md\n$(rm -r out)\nEOF", {"write(C:/Users/me/PycharmProjects/wiki-agent/notes.md)"}),
    ("echo 2>&1 >/dev/null", set()),
    ("echo x > /tmp/y", {"write(C:/Users/me/AppData/Local/Temp/y)"}),
    ("S=/c/x; echo x > \"$S/y\"", {"write(C:/x/y)"}),
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
    ("echo x > /c/r/a", "write(C:/r/a)"),
    ("echo x > /C/r/../a", "write(C:/a)"),
    ("echo x > C:a", "write(C:/Users/me/PycharmProjects/wiki-agent/a)"),
    ("echo x > '\\\\?\\C:\\r\\a'", "write(C:/r/a)"),
    ("echo x > /usr/x", "write(//msys/usr/x)"),
    ("echo x > ~/a", "write(C:/Users/me/a)"),
])
def test_drive_letter_forms_resolve_to_one_spelling(command, expected):
    assert expected in effects(command)


def test_a_relative_path_after_an_unknown_cd_is_top():
    assert any("unknown cd" in e for e in effects("cd \"$X\"; echo x > a"))


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
                    "git add -u && git commit -qm x && git -c http.followRedirects=false push --no-recurse-submodules "
                    "origin HEAD", "git status --short && git diff --stat",
                    "echo hi > out.txt", "python tool/search \"query\"", "echo x > ../shared/f",
                    "echo hi > /tmp/x.log", "ls ~ && cat /etc/hosts", "find . -name '*.py'"):
        assert call(repo, command) is None, command
    for command in ("Get-ChildItem; Get-Content README.md", "'x' > out.txt", "'a,b'.Split(',')"):
        assert call(repo, command, tool="PowerShell") is None, command


def test_each_denial_names_the_effect_and_the_grant_that_would_allow_it(repo):
    reason = call(repo, "GH_HOST=unlisted.example cargo build && echo x > ../other/f")
    assert "spawn(" not in reason and "실행 파일" not in reason
    assert "network(unlisted.example)" in reason and "`[guard] network_hosts` 에 `unlisted.example`" in reason
    assert "write(" in reason and "other" in reason and "`[guard] write_roots`" in reason
    assert reason.splitlines()[-1].startswith("허가: 쓰기 루트") and "pypi.org" in reason


def test_a_nested_shell_keeps_the_effects_it_wraps(repo):
    assert "write(" in call(repo, "bash -c 'echo x > ../other/f'")
    assert "write(" in call(repo, "'x' > ../other/f", tool="PowerShell")
    # Codex runs PowerShell on Windows and a POSIX shell elsewhere; either reads this redirection the same.
    assert "write(" in call(repo, tool="exec_command", host_name="codex", cmd="'x' > ../other/f")


def test_files_that_decide_the_grant_are_never_granted(repo):
    for command in ("echo x > .git/config", "echo x > .wiki/adapter.toml", "echo x > .claude/settings.json",
                    "echo x > verification.toml", "echo x > .wiki", "echo x > ."):
        assert "허가를 정하는 파일" in (call(repo, command) or ""), command


def test_every_ancestor_of_a_protected_path_is_judged_protected_for_every_path_kind(repo):
    g = guard_grant.grant(repo, {})
    for p in g.protected:
        a = Path(p).parent
        while contained(str(a), g.roots) and a != a.parent:
            for kind in ("write", "delete"):
                assert "허가를 정하는 파일" in (guard_grant.outside((kind, str(a)), g) or ""), (kind, a)
            a = a.parent


def test_ordinary_writes_beside_protected_files_stay_allowed(repo):
    for command in ("printf x > safe/a", "echo x > .wiki-notes.md", "git add safe/a && git commit -qm x"):
        assert call(repo, command) is None, command


# -- The closed table (spec rev 5) ------------------------------------------------
# Only a program the table lists, with the flags its row lists, yields effects other than `top`; a program
# with no row is a `spawn`, which passes whatever language the project uses. Every other form outside the
# table is denied by design. The rules below are walked over the tables themselves, so a row added later is
# held to them without a new test.

def test_every_row_refuses_a_flag_it_does_not_list():
    from guard_models import KEYWORDS, TABLE
    from guard_windows import PS_ROWS

    for name, r in TABLE.items():
        # A shell keyword (`time`) is read before any program: what follows it is the command.
        if not r.free and name not in KEYWORDS["posix"]:
            assert any(e.startswith("top(") for e in effects(f"{name} --zz-unmodelled x")), name
    for name in PS_ROWS:
        assert any(e.startswith("top(") for e in effects(f"{name} -ZzUnmodelled x", "ps")), name


def test_cmd_rows_only_read_or_move_the_directory():
    from guard_windows import CMD_ROWS

    for name in CMD_ROWS - {"cd", "chdir"}:
        assert effects(f"cmd /c {name} /s x") == {"read"}, name


def test_a_program_with_no_row_is_only_a_spawn(repo):
    assert effects("zz-unlisted-tool --anything ../outside/target.txt") == {"read", "spawn(zz-unlisted-tool)"}
    # A cmdlet runs inside the runspace, not as a program: outside the table it is `top`.
    assert any(e.startswith("top(") for e in effects("Zz-Unlisted ../outside/target.txt", "ps"))


# P0: the gate's language once decided which programs ran. A Python gate granted `python` and refused
# npm, a project CLI and a `.cmd` browser launcher, so a web project could not be verified at all.
@pytest.mark.parametrize("command,tool", [
    ("npm run typecheck && npx vitest run", "Bash"), ("omm validate", "Bash"), ("cargo test", "Bash"),
    ("./scripts/run_user.cmd", "Bash"), ("scripts\\run_user.cmd", "PowerShell"), ("& .\\scripts\\x.ps1", "PowerShell"),
    ("bash scripts/check.sh", "Bash"), ("pwsh -File scripts/check.ps1", "Bash"), ("cmd /c call run_user.cmd", "Bash"),
    ("cmd /c start npm run dev", "Bash"), ("command -v npm && export NODE_ENV=test && npm test", "Bash"),
    ("command -pv npm; export \"NODE_ENV=test\" CI; command -- npm test", "Bash"),
    ("../outside/python x.py", "Bash"),
    # A program with no row runs its own code, as `python -c` always did: what it does is not judged.
    ("rm -rf ../outside", "Bash"), ("curl -o ../outside/x http://localhost:8000/x", "Bash"), ("gh pr view 3", "Bash"),
])
def test_any_program_of_any_toolchain_runs(repo, command, tool):
    assert call(repo, command, tool=tool) is None, command


# Builtins that set what later statements of this shell run are not programs.
@pytest.mark.parametrize("command", [
    "export GIT_DIR=../o/.git", "export 'GIT_DIR=../o/.git'", "export \"PATH=/o\"", "export -f f", "export \"$N=1\"",
    "trap 'x' EXIT", "exec > ../o/f", "declare -x PATH=/o", "builtin cd ..", "compgen -W '$((PATH=1))'; git status",
    "command git push --force origin x", "command -- git push --force origin x", "command -p -- git push --force o x",
    "command -x git", 'cmd /c "call cd .. & echo x > target.txt"', 'cmd /c "call set GIT_DIR=..\\o\\.git & git status"',
])
def test_a_shell_builtin_outside_the_table_stays_denied(repo, command):
    assert call(repo, command), command


# -- PowerShell and cmd rows that write (follow-up b) ---------------------------------

HERE_ = C["cwd"]


@pytest.mark.parametrize("command,dialect,expected", [
    ("Set-Content out.txt hello", "ps", {"write(out.txt)"}),
    ("'x' | Out-File -FilePath a/b.txt -Encoding utf8", "ps", {"write(a/b.txt)"}),
    ("'x' | Tee-Object b.txt -Append", "ps", {"write(b.txt)"}),
    ("Add-Content -Value x -LiteralPath log.txt", "ps", {"write(log.txt)"}),
    ("New-Item -ItemType Directory -Path sub/x", "ps", {"write(sub/x)"}),
    ("New-Item -Path sub -Name f.txt -Value hi -Force", "ps", {"write(sub/f.txt)"}),
    ("Remove-Item old -Recurse -Force", "ps", {"delete(old)"}),
    ("Copy-Item a.txt b", "ps", {"write(b)", "write(b/a.txt)"}),
    ("Move-Item -Path a.txt -Destination b", "ps", {"delete(a.txt)", "write(b)", "write(b/a.txt)"}),
    ("Rename-Item sub/a.txt b.txt", "ps", {"delete(sub/a.txt)", "write(sub/b.txt)"}),
    ("del /q a.txt", "cmd", {"delete(a.txt)"}),
    ("rd /s /q build", "cmd", {"delete(build)"}),
    ("md a\\b c", "cmd", {"write(a/b)", "write(c)"}),
    ("move a.txt b", "cmd", {"delete(a.txt)", "write(b)", "write(b/a.txt)"}),
    ("ren a.txt b.txt", "cmd", {"delete(a.txt)", "write(b.txt)"}),
])
def test_windows_write_rows_name_every_path_they_touch(command, dialect, expected):
    def full(e):
        kind, _, rest = e.partition("(")
        return f"{kind}({HERE_}/{rest}"
    assert effects(command, dialect) == {"read"} | {full(e) for e in expected}


@pytest.mark.parametrize("command,dialect", [
    ("Remove-Item *.txt", "ps"), ("Remove-Item a,b", "ps"), ("Remove-Item 'a[1].txt'", "ps"),
    ("Set-Content Env:PATH x", "ps"), ("Set-Content Function:prompt x", "ps"), ("Set-Content a.txt:s x", "ps"),
    ("Remove-Item FileSystem::C:/x", "ps"), ("Set-Content {$_.Name} x", "ps"), ("Set-Content -Path a -Path b x", "ps"),
    ("Get-ChildItem | Remove-Item", "ps"), ("Get-ChildItem | Copy-Item -Path a.txt", "ps"),
    ("'x' | New-Item -Path f.txt", "ps"), ("New-Item -ItemType HardLink -Path l -Value x", "ps"),
    ("New-Item -ItemType $t -Path l", "ps"), ("Copy-Item a.txt", "ps"), ("Copy-Item -Recurse a b", "ps"),
    ("Rename-Item a.txt sub/b.txt", "ps"), ("Move-Item . b", "ps"), ("Remove-Item -Stream s a.txt", "ps"),
    ("Set-Content a.txt x y", "ps"),
    ("del /s a.txt", "cmd"), ("del *.txt", "cmd"), ("copy a.txt b", "cmd"), ("copy /l a b", "cmd"),
    ("Copy-Item -LiteralPath GIT~1 out", "ps"), ("Move-Item VERIFI~1.TOM out", "ps"), ("move a,b c", "cmd"), ("ren a.txt sub\\b.txt", "cmd"), ("md", "cmd"),
])
def test_windows_write_rows_refuse_what_they_cannot_read(command, dialect):
    assert any(e.startswith("top(") for e in effects(command, dialect)), command


def test_windows_writes_inside_the_grant_pass(repo):
    (repo / "old").mkdir()
    (repo / "a.txt").write_text("x", encoding="utf-8")
    for command in ("Set-Content out.txt hello", "New-Item -ItemType Directory build", "Remove-Item old -Recurse",
                    "Copy-Item a.txt b.txt", "Move-Item a.txt c.txt", "'x' | Out-File ../shared/f.txt"):
        assert call(repo, command, tool="PowerShell") is None, command
    assert call(repo, 'cmd /c "md build2 && move a.txt build2"') is None


def test_a_delete_reaches_every_protected_name_below_it(repo):
    # Another worktree's copy below a write root, or a `.wiki` folder a move would put an `adapter.toml` in.
    for name in (".claude/settings.json", "verification.toml", ".wiki/adapter.toml", "nested/.git/HEAD"):
        (repo / "wt" / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / "wt" / name).write_text("x", encoding="utf-8")
        assert "허가를 정하는 파일" in (call(repo, "Remove-Item wt -Recurse", tool="PowerShell") or ""), name
        assert "허가를 정하는 파일" in (call(repo, 'cmd /c "rd /s /q wt"') or ""), name
        assert "허가를 정하는 파일" in (call(repo, "Move-Item wt moved", tool="PowerShell") or ""), name
        (repo / "wt" / name).unlink()
    (repo / "cfg").mkdir()
    for command in ("Move-Item cfg sub/.wiki", "Rename-Item cfg .wiki", "New-Item -ItemType Directory x/.wiki",
                    "Copy-Item cfg/.claude x"):
        assert "허가를 정하는 파일" in (call(repo, command, tool="PowerShell") or ""), command


@pytest.mark.skipif(os.name != "nt", reason="Windows' spellings")
def test_a_trailing_dot_or_space_still_names_a_protected_folder(repo):
    for command in ("New-Item -ItemType Directory x/.git.", "New-Item -ItemType Directory 'x/.git '",
                    "New-Item -ItemType Directory 'x/.claude.'", "Rename-Item a.txt .wiki."):
        assert "허가를 정하는 파일" in (call(repo, command, tool="PowerShell") or ""), command
    assert "허가를 정하는 파일" in (call(repo, 'cmd /c "md x\\.git."') or "")


@pytest.mark.skipif(os.name != "nt", reason="Windows' spellings")
def test_an_extended_unc_path_is_a_share_not_a_relative_path(repo):
    for command in ("Set-Content '\\\\?\\UNC\\server\\share\\t.txt' x", "Remove-Item '//?/UNC/server/share/t.txt'"):
        reason = call(repo, command, tool="PowerShell") or ""
        assert "server/share/t.txt" in reason and "쓰기 루트 밖" in reason, command
    # Any other device form names no folder of the checkout.
    reason = call(repo, "Set-Content -LiteralPath '\\\\?\\GLOBALROOT\\Device\\X\\t.txt' x", tool="PowerShell") or ""
    assert "쓰기 루트 밖" in reason


def test_an_alias_that_may_name_a_native_program_is_not_its_cmdlet():
    # `sc` is Set-Content in Windows PowerShell but sc.exe in PowerShell 7.
    assert any(e.startswith("top(") for e in effects("sc stop placeholder", "ps"))
    # Off Windows, PowerShell 7 has no `rm`, `cp`, `mv` or `tee` alias: the name runs that program.
    posix_host = replace(CTX, d="ps", windows=False, cwds=frozenset({"/w"}))
    for name in ("rm", "cp", "mv", "tee", "mkdir"):
        assert f"spawn({name})" in effects(f"{name} a b", "ps", posix_host), name
    # `md` aliases the native `mkdir` there: a program under another name.
    assert any(e.startswith("top(") for e in effects("md a", "ps", posix_host))
    assert "delete(/w/a)" in effects("Remove-Item a", "ps", posix_host)
    assert "write(C:/Users/me/PycharmProjects/wiki-agent/a)" in effects("md a", "ps")
    # PowerShell takes a backslash for a separator there too.
    assert "write(/w/../outside/t.txt)" in effects("Set-Content '..\\outside\\t.txt' x", "ps", posix_host)
    assert "write(/w/../outside/t.txt)" in effects("'x' > '..\\outside\\t.txt'", "ps", posix_host)
    # A native program's operand keeps its backslashes: there they are filename characters.
    assert "write(/outside/..\\w\\t.txt)" in effects("sort -o '/outside/..\\w\\t.txt' a", "ps", posix_host)
    assert "write(/outside/..\\w\\t.txt)" in {
        render(e) for e in guard.call_effects("Write", {"file_path": "/outside/..\\w\\t.txt"}, posix_host, "claude", {})}


@pytest.mark.parametrize("dash", ["-", "–", "—", "―"])
def test_every_dash_powershell_reads_names_a_parameter(repo, dash):
    for command in (f"'x' | Set-Content {dash}Path ../outside/t.txt", f"'x' | Out-File {dash}FilePath ../outside/t.txt",
                    f"Remove-Item {dash}LiteralPath ../outside/t.txt"):
        assert "outside/t.txt" in (call(repo, command, tool="PowerShell") or ""), command
    assert any(e.startswith("top(") for e in effects(f"Set-Content {dash}Zz x", "ps"))


def test_a_delete_or_move_of_a_link_is_denied(repo, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    try:
        (repo / "link").symlink_to(out, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("symlinks need privileges here")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(repo / "link"), str(out)], check=True, capture_output=True)
    (repo / "dir").mkdir()
    os.replace(repo / "link", repo / "dir" / "link")
    for command in ("Rename-Item dir/link b", "Move-Item dir/link b", "Remove-Item dir -Recurse"):
        assert "허가를 정하는 파일" in (call(repo, command, tool="PowerShell") or ""), command


# Forms earlier rounds found acting outside the grant, each on a neutral target. The core table had no row
# for any of them; rows added since still deny each one, and bring their own effect tests above. Programs
# with no row (rm, cp, tar, curl, gh, awk, sed, xargs, …) left this list: they run as spawns.
OUTSIDE_TABLE_FORMS = {
    # File writers, copies and moves.
    "copy-item": ("Copy-Item src/adapter.toml -Destination .wiki", "PowerShell"),
    "move-item": ("Move-Item src/adapter.toml .wiki", "PowerShell"),
    "set-content": ("Set-Content ../outside/target.txt x", "PowerShell"),
    "remove-item": ("Remove-Item -LiteralPath:'../outside/target.txt'", "PowerShell"),
    "cmd-copy": ('cmd /c "copy src\\adapter.toml .wiki"', "Bash"),
    "cmd-del": ("cmd /c del /s target.txt", "Bash"),
    # Links and trees.
    "ln": ("ln -s ../outside link", "Bash"),
    "new-item-symlink": ("New-Item -ItemType SymbolicLink -Path link -Value ../outside", "PowerShell"),
    "new-item-junction": ("ni -ItemType Junction link -Value ../outside", "PowerShell"),
    "mklink": ("cmd /c mklink /J link ..\\outside", "Bash"),
    "rename-item": ("Rename-Item src/adapter.toml .wiki", "PowerShell"),
    # Archives.
    "unzip": ("unzip bundle.zip -d safe", "Bash"),
    "expand-archive": ("Expand-Archive bundle.zip -DestinationPath safe", "PowerShell"),
    # Network clients with a refusing row or a cmdlet.
    "wget": ("wget http://localhost:8000/verification.toml", "Bash"),
    "web-request": ("Invoke-WebRequest http://localhost:8000/x -OutFile .wiki", "PowerShell"),
    # Code that runs code in this shell.
    "patch": ("patch -p1 < change.diff", "Bash"),
    "find-delete": ("find . -name x -delete", "Bash"),
    "find-exec": ("find . -execdir rm target.txt ';'", "Bash"),
    "rg-preprocessor": ("rg --pre ./helper needle README.md", "Bash"),
    "bash-startup-file": ("bash --rcfile startup.rc -ic 'true'", "Bash"),
    "arithmetic": ("(( n = $(rm ../outside/target.txt) ))", "Bash"),
    "let": ("let x=1", "Bash"),
    "trap": ("trap 'rm ../outside/target.txt' EXIT", "Bash"),
    "function": ("f() { echo x > target.txt; }; f", "Bash"),
    "for-loop": ("for i in 1 2; do echo x > $i; done", "Bash"),
    "hash-path": ("hash -p ./helper cat; cat", "Bash"),
    "export": ("export GIT_DIR=../outside/.git", "Bash"),
    "printf-name": ("printf -v PATH /outside/tools", "Bash"),
    "read-name": ("read HTTPS_PROXY < startup.rc", "Bash"),
    # PowerShell members, constructors, pipelines and computed commands.
    "constructor": ("[System.IO.FileStream]::new('C:/outside/target.txt', 'Create').Dispose()", "PowerShell"),
    "instance-method": ("(Get-Item README.md).CopyTo('../outside/target.md')", "PowerShell"),
    "foreach-member": ("gi ../outside/target.txt | % Delete", "PowerShell"),
    "foreach-script": ("Get-ChildItem | ForEach-Object { $_.Delete() }", "PowerShell"),
    "member-assignment": ("(Get-Item ../outside/target.txt).IsReadOnly = $true", "PowerShell"),
    "call-operator-expression": ("& ('Remove-' + 'Item') ../outside/target.txt", "PowerShell"),
    "nested-shell-expression": ("pwsh -Command ('Remove-' + 'Item ../outside/target.txt')", "PowerShell"),
    "start-process": ("Start-Process ../outside/python", "PowerShell"),
    # git verbs and options outside the core rows.
    "git-diff-output": ("git diff HEAD~1 HEAD --output=../other.patch", "Bash"),
    "git-grep-pager": ("git grep --open-files-in-pager=./helper pattern", "Bash"),
    "git-restore": ("git restore --source=HEAD~1 -- .wiki/adapter.toml", "Bash"),
    "git-checkout": ("git checkout HEAD~1 -- verification.toml", "Bash"),
    "git-mv": ("git mv a/link b; printf x > b/f", "Bash"),
    "git-config": ("git config remote.origin.url https://unlisted.example/o/r.git", "Bash"),
    "git-remote": ("git remote add other https://unlisted.example/o/r.git", "Bash"),
    "git-merge-strategy": ("git merge -s helper topic", "Bash"),
    "git-pull-url": ("git pull https://unlisted.example/o/r.git main", "Bash"),
    "git-rebase-exec": ("git rebase -x 'rm ../outside/target.txt' main", "Bash"),
    "git-rebase-interactive": ("git rebase -i main", "Bash"),
    "git-reset": ("git reset --hard HEAD~1", "Bash"),
    "git-stash-all": ("git stash push -a", "Bash"),
    "git-fetch-url": ("git fetch https://unlisted.example/o/r.git", "Bash"),
    "git-fetch-upload-pack": ("git fetch --upload-pack=./helper origin", "Bash"),
    "git-clone": ("git clone https://github.com/owner/repo out", "Bash"),
    "git-dash-c-outside": ("git -C ../outside status", "Bash"),
    "git-dash-c-variable": ("git -C $W status", "Bash"),
    "git-checkout-outside": ("git checkout HEAD~1 -- ../outside/target.txt", "Bash"),
    "git-checkout-pattern": ("git checkout HEAD~1 -- '*.toml'", "Bash"),
    "git-checkout-tree": ("git checkout HEAD~1 -- .", "Bash"),
    "git-worktree-add-outside": ("git worktree add ../outside/wt", "Bash"),
    "git-worktree-remove-outside": ("git worktree remove --force ../outside/wt", "Bash"),
    "git-worktree-move": ("git worktree move wt ../outside/wt", "Bash"),
    "git-remote-show": ("git remote show origin", "Bash"),
    "git-config-pager": ("git -c core.pager=./helper log", "Bash"),
    "git-branch-format": ("git branch '--format=%(signature)'", "Bash"),
    # A variable naming another repository or host reaches it, whatever program it is set for.
    "gh-repo-env": ("GH_REPO=other/repo gh pr merge --squash", "Bash"),
    "gh-host-env": ("GH_HOST=unlisted.example gh pr view", "Bash"),
    "curl-proxy-env": ("HTTPS_PROXY=unlisted.example:8080 curl https://github.com/", "Bash"),
    "git-config-flag": ("git -c core.hooksPath=../outside commit -m x", "Bash"),
    "git-dir": ("git --git-dir=../other/.git status", "Bash"),
    "git-dir-variable": ("GIT_DIR=../other/.git git status", "Bash"),
    "git-push-refspec": ("git push origin HEAD:main", "Bash"),
    "git-push-force": ("git push --force origin topic", "Bash"),
    "git-push-unlisted-remote": ("git push https://unlisted.example/o/r.git topic", "Bash"),
    "git-commit-amend": ("git commit --amend -m x", "Bash"),
}


@pytest.mark.parametrize("form", OUTSIDE_TABLE_FORMS)
def test_each_form_outside_the_table_is_denied(repo, form):
    command, tool = OUTSIDE_TABLE_FORMS[form]
    assert call(repo, command, tool=tool), form


# Monotonicity: wrapping a command keeps every effect it had, so no wrapper turns a denial into a pass.
WRAPPERS = {
    "bash-c": lambda c: "bash -c " + shlex.quote(c),
    "sh-lc": lambda c: "ls && sh -lc " + shlex.quote(c),
    "substitution": lambda c: f"echo $({c})",
}


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("form", [f for f, (_, tool) in OUTSIDE_TABLE_FORMS.items() if tool == "Bash"])
def test_no_wrapper_turns_an_outside_table_form_into_a_pass(repo, form, wrapper):
    assert call(repo, WRAPPERS[wrapper](OUTSIDE_TABLE_FORMS[form][0])), form


# A member name the lexer cannot read as an identifier (an escape, a quote, a variable) is dynamic.
@pytest.mark.parametrize("member", ["De`lete", "'Delete'", '"Delete"', "$m", "De``lete"])
def test_a_member_name_that_is_not_an_identifier_is_denied(repo, member):
    reason = call(repo, f"(Get-Item ../outside/target.txt).{member}()", tool="PowerShell")
    assert reason and "판단할 수 없는" in reason, member


# A PowerShell member name does not fix its effect (`String.Replace` reads, `FileInfo.Replace` writes):
# every syntax that invokes a member on an unknown receiver is undecidable, whichever way it was written.
MEMBER_FORMS = {
    "instance-method": "(Get-Item README.md).Replace('../outside/target.md', $null)",
    "foreach-object-membername": "Get-Item ../outside/target.txt | ForEach-Object -MemberName Delete",
    "percent-positional": "gi ../outside/target.txt | % Delete",
    "foreach-abbrev": "gi ../outside/target.txt | foreach -m Delete",
    "wildcard-member": "gi ../outside/target.txt | % -MemberName Del*",
    "member-with-arguments": "gi README.md | ForEach-Object -MemberName CopyTo -ArgumentList '../outside/t.md'",
    "intrinsic-foreach-method": "(Get-Item ../outside/target.txt).ForEach('Delete')",
    "invoke-member": "(Get-Item ../outside/target.txt).Delete.Invoke()",
    "psobject-invoke": "(Get-Item ../outside/target.txt).PSObject.Methods['Delete'].Invoke()",
    "dynamic-member": "(Get-Item ../outside/target.txt).$m()",
    "quoted-member": "(Get-Item ../outside/target.txt).'Delete'()",
    "member-assignment": "(Get-Item ../outside/target.txt).IsReadOnly = $true",
    "setter-method": "(Get-Item ../outside/target.txt).set_IsReadOnly($true)",
}


@pytest.mark.parametrize("form", MEMBER_FORMS)
def test_a_member_on_an_unknown_receiver_is_denied(repo, form):
    assert "판단할 수 없는" in call(repo, MEMBER_FORMS[form], tool="PowerShell"), form


def test_read_only_members_and_safe_receivers_stay_allowed(repo):
    for ok in ("'a,b'.Split(',')", "'x'.Replace('x','y')", "(Get-Date).ToString()"):
        assert call(repo, ok, tool="PowerShell") is None, ok


# -- Declared blocks ------------------------------------------------------------

def test_declared_blocks_hold_across_every_payload_shape(monkeypatch):
    """F9 — the declared `Bash(...)` block fires whatever field, tool name or argv shape carries it."""
    import apply
    import deny

    monkeypatch.setattr(apply, "declared", lambda: (
        {"Bash(git reset --hard*)": ["craft/destructive-git-guards"],
         "Bash(git clean -fdx*)": ["craft/destructive-git-guards"]}, {}))

    def blocked(payload):
        answer = deny.verdict(payload)
        return answer and answer["hookSpecificOutput"]["permissionDecisionReason"]

    def shape(tool, field, value):
        return {"tool_name": tool, "tool_input": {field: value}}

    bad = [shape("Bash", "command", "git reset --hard"),
           shape("exec_command", "cmd", "bash -c 'git reset --hard'"),
           shape("functions.exec_command", "cmd", "git reset --hard"),
           shape("shell_command", "command", "echo ready; git reset --hard"),
           shape("shell", "command", ["bash", "-lc", "git reset --hard"]),
           shape("local_shell", "command", ["git", "reset", "--hard"])]
    for payload in bad:
        reason = blocked(payload)
        assert reason and "공유 위키의 차단 규칙" in reason, payload
    for payload in bad:
        payload["tool_input"] = {k: (["git", "status"] if isinstance(v, list) else v.replace(
            "git reset --hard", "git status").replace("git clean -fdx", "git status"))
            for k, v in payload["tool_input"].items()}
        assert not blocked(payload), payload


def test_declared_git_blocks_hold_inside_nested_shells(monkeypatch):
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
    # A blocked command after a harmless one in the same nested script is still a separate command.
    assert blocked("bash -c 'echo ready; git reset --hard'")
    assert blocked("bash -c 'git clean -fdx' | tee out.txt")
    assert blocked("git clean -fdx") and not blocked("git status")
    assert not blocked("bash -c 'echo ready; git status'")


def test_undecidable_calls_are_denied(repo):
    assert "판단할 수 없는" in call(repo, 'eval "$X"')


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
    assert call(repo, "echo x > docs/plans/x/1-step.md") is None
    assert "write_roots" in call(repo, "echo x > tool/a.py")


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
    """What the PreToolUse hook costs end to end on each host: Claude's guard process and Codex's whole
    chain (`codex_pretool.py`), each over a baseline process."""
    env = host.environment({"WIKI_AGENT_MANAGED": "1", "TEMP": os.environ["TEMP"],
                            "TMP": os.environ["TMP"], "TMPDIR": os.environ["TMPDIR"]})
    samples = ["git add -A && git commit -qm x && git push origin HEAD", "echo x > ../other/f",
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
        for script in ("guard.py", "codex_pretool.py"):
            worst = max(worst, min(run([sys.executable, str(TOOL / script)], payload) for _ in range(3)))
    # The hook's own work (process start aside) stays well under a second per call.
    assert worst - baseline < 1.0, (baseline, worst)


# -- The hook entry point -----------------------------------------------------

def test_the_entry_point_answers_in_utf8_whatever_the_console(repo):
    env = host.environment({"PYTHONIOENCODING": "cp949", "WIKI_AGENT_MANAGED": "1", "TEMP": os.environ["TEMP"],
                            "TMP": os.environ["TMP"], "TMPDIR": os.environ["TMPDIR"]})
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo x > ../other/f"}, "cwd": str(repo)})
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


# A command named by a path is that file, not the program its basename suggests: it never borrows a row,
# suffix included.
def test_a_path_named_executable_is_judged_by_its_path():
    for command in ("./git push origin HEAD", "./cat x", "./helper.cmd --version", "./helper.exe"):
        name = command.split()[0][2:]
        assert effects(command) == {"read", f"spawn(c:/users/me/pycharmprojects/wiki-agent/{name})"}, command


# A variable that redirects a client to another host reaches that host, whatever program it is set for.
@pytest.mark.parametrize("command", [
    "GH_HOST=unlisted.example python -m ruff check tool",
    "HTTPS_PROXY=http://unlisted.example:8080 python -m ruff check tool",
])
def test_a_variable_that_redirects_the_network_is_denied(repo, command):
    assert "network(unlisted.example)" in (call(repo, command) or ""), command
    # `GH_REPO` also picks the repository gh writes to, on any host: never decidable here.
    assert "GH_REPO" in (call(repo, "GH_REPO=github.com/o/r python -m ruff check tool") or "")


# A value computed at run time and then run (a command name, a nested shell's script) is undecidable; only
# a literal script block is reduced as code.
COMPUTED_FORMS = {
    "call-operator-expression": "& ('Remove-' + 'Item') ../outside/target.txt",
    "call-operator-command-lookup": "& (Get-Command Remove-Item) ../outside/target.txt",
    "nested-shell-expression": "pwsh -Command ('Remove-' + 'Item ../outside/target.txt')",
    "cmd-expression": "cmd /c ('del ..\\outside\\target.txt')",
    "bash-expression": "bash -c ('rm ../outside/target.txt')",
}


@pytest.mark.parametrize("form", COMPUTED_FORMS)
def test_a_computed_command_is_denied(repo, form):
    assert "판단할 수 없는" in (call(repo, COMPUTED_FORMS[form], tool="PowerShell") or ""), form


def test_a_literal_script_block_is_reduced_as_code(repo):
    assert call(repo, "& { Get-ChildItem }", tool="PowerShell") is None
    assert call(repo, "pwsh -Command { Get-ChildItem }", tool="PowerShell") is None
    assert "쓰기 루트 밖이다" in call(repo, "& { 'x' > ../outside/target.txt }", tool="PowerShell")


# Constructs the parser reads but cannot evaluate: each reduces to `top`, never to a dropped effect.
TOP_FORMS = {
    "mklink": ("cmd /c mklink /J link ..\\outside", "Bash"),
    "expand-archive": ("Expand-Archive bundle.zip -DestinationPath safe", "PowerShell"),
    # Arithmetic evaluation.
    "let": ("let x=1", "Bash"),
    "dollar-arithmetic": ("echo $((n + 1))", "Bash"),
    "dollar-bracket": ("echo $[n + 1]", "Bash"),
    "arithmetic-command": ("(( n > 1 ))", "Bash"),
    "arithmetic-test": ("[[ $n -eq 1 ]]", "Bash"),
    "array-subscript": ("echo ${a[$i]}", "Bash"),
    "substring-offset": ("echo ${s:$i}", "Bash"),
    "indirect-expansion": ("echo ${!name}", "Bash"),
    "subscript-assignment": ("a[$i]=1", "Bash"),
    "web-request": ("Invoke-WebRequest http://localhost:8000/x -OutFile safe/x", "PowerShell"),
    "wget": ("wget http://localhost:8000/verification.toml", "Bash"),
    "patch": ("patch -p1 < change.diff", "Bash"),
    "robocopy": ("cmd /c robocopy src .wiki adapter.toml", "Bash"),
    "xcopy": ("cmd /c xcopy src .wiki", "Bash"),
    "del-subdirectories": ("cmd /c del /s target.txt", "Bash"),
    "cmd-for-command": ("cmd /c for /f %i in ('dir') do echo %i", "Bash"),
}


@pytest.mark.parametrize("form", TOP_FORMS)
def test_each_unevaluated_construct_reduces_to_top(repo, form):
    command, tool = TOP_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and "판단할 수 없는" in reason, (form, reason)


# Only the first statement may change the directory; anywhere else (a later statement, a group, a body, a
# pipeline, a nested shell or a substitution) the places later paths resolve from are not fixed.
CD_FORMS = {
    "later-statement": ("echo x > a; cd ..; echo x > target.txt", "Bash"),
    "for": ("cd sub && for i in 1 2; do cd ..; done; printf x > target.txt", "Bash"),
    "while": ("while true; do cd ..; done; printf x > target.txt", "Bash"),
    "if": ("if true; then cd sub; fi; printf x > target.txt", "Bash"),
    "brace-group": ("{ cd ..; }; printf x > target.txt", "Bash"),
    "function": ("f() { cd ..; }; printf x > target.txt", "Bash"),
    "pipeline": ("cd .. | cat; echo x > target.txt", "Bash"),
    "substitution": ("echo $(cd ..); echo x > target.txt", "Bash"),
    "nested-shell": ("bash -c 'cd .. && echo x > target.txt'", "Bash"),
    "ps-two-moves": ("Set-Location d01; Set-Location ..; 'x' > target.txt", "PowerShell"),
    "ps-subexpression": ("Write-Output $(Set-Location ../outside); 'x' > target.txt", "PowerShell"),
    "ps-argument-group": ("Write-Output (Set-Location ../outside); 'x' > target.txt", "PowerShell"),
    "ps-pipeline-stage": ("Set-Location ../outside | Out-Null; 'x' > target.txt", "PowerShell"),
    "ps-piped-location": ("Get-Item ../outside | Set-Location; 'x' > target.txt", "PowerShell"),
    "ps-statement-block": ("if ($true) { Set-Location ../outside }; 'x' > target.txt", "PowerShell"),
    "ps-nested-block": ("& { & { Set-Location ../outside } }; 'x' > target.txt", "PowerShell"),
    "ps-bare-set-location": ("Set-Location; 'x' > target.txt", "PowerShell"),
    "ps-foreach": ("foreach ($i in 1..2) { Set-Location .. }; 'x' > target.txt", "PowerShell"),
    "ps-while": ("while ($true) { cd .. }; 'x' > target.txt", "PowerShell"),
    "ps-function": ("function f { Set-Location .. }; f; 'x' > target.txt", "PowerShell"),
    "ps-nested-shell": ("pwsh -c 'Set-Location ..'; 'x' > target.txt", "PowerShell"),
    "cmd-for": ("cmd /c for %i in (1 2) do cd ..", "Bash"),
}


@pytest.mark.parametrize("form", CD_FORMS)
def test_a_directory_change_after_the_first_statement_is_denied(repo, form):
    command, tool = CD_FORMS[form]
    assert call(repo, command, tool=tool), form


def test_a_directory_change_in_the_first_statement_stays_tracked(repo):
    assert call(repo, "cd sub && printf x > target.txt") is None
    assert call(repo, "Set-Location sub; 'x' > target.txt", tool="PowerShell") is None
    assert "쓰기 루트 밖이다" in call(repo, "cd .. && printf x > target.txt")
    assert "쓰기 루트 밖이다" in call(repo, "Set-Location ../outside; 'x' > target.txt", tool="PowerShell")
    # A subexpression that never moves the location leaves later paths where they were.
    assert call(repo, '"$(Get-Date)"; \'x\' > target.txt', tool="PowerShell") is None


# Every pass over a command is linear in its length: names a script binds are layered, never copied per
# command, and a long literal is scanned in place. (dialect, the command of size n, the small n.)
SCALING_FORMS = {
    "locations": ("ps", lambda n: "; ".join(f"Set-Location d{k}" if k % 2 else "Get-ChildItem" for k in range(n)),
                  200),
    "writes": ("posix", lambda n: "; ".join(f"printf x > f{k:05}" for k in range(n)), 200),
    "assignments": ("posix", lambda n: "; ".join(f"A{k}=v{k}" for k in range(n)), 200),
    "assignments-then-groups": ("posix", lambda n: "; ".join(
        f"A{k}=v{k}" if k % 2 else f"( echo $A{k - 1} )" for k in range(n)), 200),
    "literal-in-substitution": ("posix", lambda n: "echo $(echo " + "x" * n + ")", 20000),
}


@pytest.mark.parametrize("form", SCALING_FORMS)
def test_every_pass_is_linear_in_the_command(form):
    dialect, build, n = SCALING_FORMS[form]

    def best(size: int) -> float:
        command = build(size)
        took = []
        for _ in range(3):
            start = time.perf_counter()
            derive(command, dialect, CTX)
            took.append(time.perf_counter() - start)
        return min(took)

    small, large = best(n), best(8 * n)
    # Eight times the input: linear work is about eight times the time, quadratic or worse is far more.
    assert large < small * 24, (form, small, large)


# A shell tool that names its own working directory runs there: the paths it writes resolve from it,
# while the grant stays the session's.
WORKDIR_FORMS = {
    "absolute-outside": lambda repo: {"workdir": str(repo.parent / "outside")},
    "relative-outside": lambda repo: {"workdir": "../outside"},
    "cwd-key": lambda repo: {"cwd": str(repo.parent / "outside")},
    "unreadable": lambda repo: {"workdir": ["../outside"]},
}


@pytest.mark.parametrize("form", WORKDIR_FORMS)
def test_a_shell_call_is_judged_where_it_runs(repo, form):
    given = {"cmd": "echo x > target.txt", **WORKDIR_FORMS[form](repo)}
    assert call(repo, tool="exec_command", host_name="codex", **given), form


def test_a_shell_call_inside_the_checkout_stays_allowed(repo):
    assert call(repo, tool="exec_command", host_name="codex", cmd="echo x > target.txt", workdir="safe") is None


# A builtin that assigns a name is judged as that assignment: a name that changes what runs, where files
# land or which host is reached is `top` however it is assigned.
NAME_FORMS = {
    "printf-path": "printf -v PATH /outside/tools",
    "printf-glued": "printf -vPATH /outside/tools",
    "read-path": "read PATH < startup.rc",
    "mapfile-command": "mapfile GIT_SSH_COMMAND < startup.rc",
    "unset-home": "unset HOME",
    "proxy": "read HTTPS_PROXY < startup.rc",
    "git-dir": "GIT_DIR=../outside/.git",
}


@pytest.mark.parametrize("form", NAME_FORMS)
def test_a_name_assigned_by_a_builtin_is_judged_like_an_assignment(repo, form):
    assert "판단할 수 없는" in (call(repo, NAME_FORMS[form]) or ""), form


# -- git ------------------------------------------------------------------------
# A push reaches the configured remote's host, read from the repository git runs in; another repository's
# remotes (a nested one, one outside the checkout) were never read, so a push from it is undecidable.

def test_the_checkouts_own_remotes_stay_allowed(repo):
    (repo / "tool").mkdir()
    push = "git -c http.followRedirects=false push --no-recurse-submodules"
    for ok in (f"{push} origin HEAD", push, f"{push} -u origin topic", f"cd tool && {push} origin HEAD"):
        assert call(repo, ok) is None, ok


def test_a_push_from_another_repository_is_denied(repo):
    (repo / "sub/.git").mkdir(parents=True)
    (repo.parent / "other").mkdir()
    for bad in ("cd sub && git push origin HEAD", "cd ../other && git push origin HEAD"):
        assert "판단할 수 없는" in (call(repo, bad) or ""), bad


# -- Rev 5 review round 1 -------------------------------------------------------
# Each admitted row and identity check is held to what the host actually runs and where git actually pushes.
POSIX = Ctx("posix", frozenset({"/work/repo"}), "/home/sample", "/tmp", False, remotes=(("origin", "github.com"),),
            repo="/work/repo")


def test_a_script_block_built_at_run_time_is_never_inert():
    wrapped = ("Select-Object -InputObject sample -Property @{Name='n'; "
               "Expression=[System.Management.Automation.ScriptBlock]::Create('Zz-Unlisted sample')}")
    assert any(e.startswith("top(") for e in effects(wrapped, "ps"))
    assert any(e.startswith("top(") for e in effects("[scriptblock]::Create('x').Invoke()", "ps"))


def test_a_posix_program_name_is_looked_up_exactly():
    assert effects("CAT note.txt", ctx=POSIX) == {"read", "spawn(CAT)"}
    assert effects("cat.exe note.txt", ctx=POSIX) == {"read", "spawn(cat.exe)"}
    assert effects("cat note.txt", ctx=POSIX) == {"read"}
    # Windows finds the same program whatever the case, and `.exe` is how it spells it.
    assert effects("CAT.EXE note.txt") == {"read"}


def test_a_repository_differing_only_in_case_is_another_on_posix():
    other = replace(POSIX, cwds=frozenset({"/work/Repo"}))
    assert any("repository other than the checkout" in e for e in effects("git push origin HEAD", ctx=other))
    assert "network(github.com)" in effects("git push origin HEAD", ctx=POSIX)


def home_at(monkeypatch, home: Path):
    """Every place git may take its home from points at `home`."""
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    for name in ("HOMEDRIVE", "HOMEPATH", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("config,expected", [
    ('[remote "origin"]\n url = https://read.example/o/r.git\n pushurl = https://write.example/o/r.git\n',
     {"network(read.example)", "network(write.example)"}),
    ('[remote "origin"]\n url = https://read.example/o/r.git\n url = git@write.example:o/r.git\n',
     {"network(read.example)", "network(write.example)"}),
    ('[remote "origin"]\n url = ../other.git\n', "local path"),
    ('[remote "origin"]\n url = file:///c/other.git\n', "local path"),
    ('[remote "origin"]\n url = https://read.example/o/r.git\n[url "https://write.example/"]\n'
     ' insteadOf = https://read.example/\n', "rewritten"),
    # An included file may also make the checkout a partial clone, which stops git earlier.
    ('[include]\n path = extra.config\n[remote "origin"]\n url = https://read.example/o/r.git\n', "partial clone"),
    # Syntax git decodes and this reader does not: each leaves every destination unread.
    ('[remote "origin"]\n url = https://read.example/o/r.git\n'
     '[remote "origin"] pushurl = https://write.example/o/r.git\n', "does not decode"),
    ('[remote "origin"]\n url = https://read.example/o/r.git\n pushurl = https://write.\\\nexample/o/r.git\n',
     "does not decode"),
    # With no remote read at all, the name is not a configured remote either way.
    ('[remote "origin"]\n url = "https://read.example/o/r.git"\n', "git push to"),
    ('[remote.origin]\n url = https://read.example/o/r.git\n', "git push to"),
])
def test_a_push_reaches_every_destination_git_would_use(tmp_path, monkeypatch, config, expected):
    home_at(monkeypatch, tmp_path / "home")
    (tmp_path / "config").write_text(config, encoding="utf-8")
    found = effects("git push origin HEAD", ctx=replace(POSIX, remotes=guard_grant.remote_hosts(tmp_path)))
    if isinstance(expected, set):
        assert {e for e in found if e.startswith("network(")} == expected
    else:
        assert any(expected in e for e in found if e.startswith("top(")), found


def test_a_branch_upstream_given_as_a_url_is_a_destination(tmp_path, monkeypatch):
    home_at(monkeypatch, tmp_path / "home")
    (tmp_path / "config").write_text('[remote "origin"]\n url = https://read.example/o/r.git\n'
                                     '[branch "main"]\n remote = https://write.example/o/r.git\n', encoding="utf-8")
    found = effects("git push", ctx=replace(POSIX, remotes=guard_grant.remote_hosts(tmp_path)))
    assert {"network(read.example)", "network(write.example)"} <= found


@pytest.mark.parametrize("command", ["git log --show-signature", 'git log --format="%G?"', "git show --pretty=%GG",
                                     "git log --format tformat:%GK", "git log --pretty=custom-alias"])
def test_a_log_option_that_runs_a_verifier_is_not_in_the_row(command):
    assert any(e.startswith("top(") for e in effects(command)), command


def test_built_in_and_literal_formats_stay_reads():
    for command in ("git log --oneline", "git log --pretty=oneline", "git log --format=%h%x09%s",
                    "git show --format=format:%an -s"):
        assert effects(command) == {"read"}, command


@pytest.mark.parametrize("command,dialect,ctx,target", [
    ("echo sample > NUL", "posix", POSIX, "write(/work/repo/NUL)"),
    ("echo sample > /DEV/NULL", "posix", POSIX, "write(/DEV/NULL)"),
    ("Write-Output sample > '$null'", "ps", CTX, "write(C:/Users/me/PycharmProjects/wiki-agent/$null)"),
])
def test_a_device_name_is_a_device_only_by_the_hosts_rule(command, dialect, ctx, target):
    assert target in effects(command, dialect, ctx)


def test_a_using_declaration_that_loads_code_is_top():
    for command in ("using module ./sample.psm1", "using module ../outside/sample.psm1; Get-ChildItem",
                    "using assembly ./sample.dll"):
        assert any(e.startswith("top(") for e in effects(command, "ps")), command
    assert effects("using namespace System.Text; Get-ChildItem", "ps") == {"read"}


def test_unix_device_spellings_are_files_in_native_windows_shells():
    for command in ("Write-Output sample > /dev/null", "Write-Output sample > /dev/fd/target.txt"):
        assert any(e.startswith("write(C:/dev/") for e in effects(command, "ps")), command


@pytest.mark.parametrize("config", ['[url "https://write.example/"] insteadOf = https://read.example/\n',
                                    '[url "https://write.example/"]\n\tpushInsteadOf = https://read.example/\n',
                                    '[Include]\n path = more.config\n',
                                    '[remote "origin"]\n\tpushurl = https://write.example/o/r.git\n',
                                    '[branch "main"]\n\tpushRemote = https://write.example/o/r.git\n'])
def test_a_rewrite_in_any_config_git_reads_leaves_pushes_unread(tmp_path, monkeypatch, config):
    (tmp_path / "home").mkdir()
    (tmp_path / "home/.gitconfig").write_text(config, encoding="utf-8")
    home_at(monkeypatch, tmp_path / "home")
    (tmp_path / "config").write_text('[remote "origin"]\n url = https://read.example/o/r.git\n', encoding="utf-8")
    found = effects("git push origin HEAD", ctx=replace(POSIX, remotes=guard_grant.remote_hosts(tmp_path)))
    # An include may also make the checkout a partial clone, which stops git earlier.
    assert any("rewritten" in e or "partial clone" in e for e in found), found


def test_git_runs_only_in_the_checkout_whose_configuration_is_protected(repo):
    (repo / "sub/.git").mkdir(parents=True)
    for command in ("cd sub && git diff -- sample.dat", "cd sub && git status", "cd sub && git commit -m x"):
        assert "판단할 수 없는" in (call(repo, command) or ""), command
    # Nothing may lay a repository down for git to trust, by a redirection or an edit tool.
    for command in ("echo x > sub2/.git", "echo x > sub/.git/config", "echo x > sub/.GIT/config"):
        assert "허가를 정하는 파일" in (call(repo, command) or ""), command
    assert "허가를 정하는 파일" in call(repo, tool="Write", file_path=str(repo / "sub/.git/config"), content="")
    assert call(repo, "git diff -- a.txt && echo x > .gitignore-notes") is None


def test_a_link_into_another_repository_is_that_repository(repo):
    other = repo.parent.parent / "other"
    (other / ".git").mkdir(parents=True)
    (other / "sub").mkdir()
    link = repo / "link"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(other / "sub")], check=True, capture_output=True)
    else:
        link.symlink_to(other / "sub", target_is_directory=True)
    for command in ("cd link && git diff", "cd link && git push origin HEAD"):
        assert "판단할 수 없는" in (call(repo, command) or ""), command
    (repo / "tool").mkdir()
    assert call(repo, "cd tool && git diff") is None


def test_the_null_devices_stay_devices():
    assert effects("echo x > /dev/null") == {"read"}
    assert effects("echo x > NUL") == {"read"}
    assert effects("echo x > /dev/null", ctx=POSIX) == {"read"}
    assert effects("Write-Output x > $null", "ps") == {"read"}


# -- Follow-up (a): git and gh writes and the network ----------------------------
# Each new row's effects, on a checkout whose one remote is `github.com`.
ROW_EFFECTS = {
    "git -c http.followRedirects=false push -u --no-recurse-submodules origin topic":
        {"write(/work/repo)", "network(github.com)"},
    "git merge --no-edit origin/main": {"write(/work/repo)"},
    "git rebase --continue": {"write(/work/repo)"},
    "git stash push -q -- notes.md": {"write(/work/repo)", "write(/work/repo/notes.md)"},
    "git stash pop -q": {"write(/work/repo)"},
    "git stash list": set(),
    "git checkout -q -b topic": {"write(/work/repo)"},
    "git restore --staged -- notes.md": {"write(/work/repo)", "write(/work/repo/notes.md)"},
    "git switch -c topic": {"write(/work/repo)"},
    "git branch -r": set(),
    "git branch -D topic": {"write(/work/repo)"},
    "git worktree list": set(),
    "git worktree add --detach /tmp/wt HEAD": {"write(/work/repo)", "write(/tmp/wt)"},
    "git worktree remove --force /tmp/wt": {"write(/work/repo)", "delete(/tmp/wt)"},
    "git -C sub status": set(),
    "git -c user.name=t -c user.email=t@t commit -qm x": {"write(/work/repo)"},
    "git -c core.whitespace=cr-at-eol diff": set(),
    "git config --get core.autocrlf": set(),
    "git config user.email": set(),
    "git ls-files --others --exclude-standard": set(),
    "git check-ignore -q artifacts": set(),
    "git remote -v": set(),
}


@pytest.mark.parametrize("command", ROW_EFFECTS)
def test_each_new_row_has_its_effects(command):
    assert effects(command, ctx=POSIX) == {"read"} | ROW_EFFECTS[command]


def test_the_new_rows_pass_in_an_attached_checkout(repo):
    for command in ("git merge --no-edit origin/main", "git stash && git stash pop", "git branch -D topic"):
        assert call(repo, command) is None, command


def test_a_worktree_of_the_same_repository_is_the_checkout(repo):
    """A linked worktree shares the checkout's configuration and remotes; another repository does not."""
    temp = Path(os.environ["TEMP"])
    wt = temp / "wt"
    wt.mkdir()
    (repo / ".git/worktrees/wt").mkdir(parents=True)
    (repo / ".git/worktrees/wt/commondir").write_text("../..\n", encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {(repo / '.git/worktrees/wt').as_posix()}\n", encoding="utf-8")
    other = temp / "other"
    (other / ".git").mkdir(parents=True)
    w, o = wt.as_posix(), other.as_posix()
    for command in (f"git -C {w} status", f"git -C {w} commit -qm x",
                    f"git -C {w} -c http.followRedirects=false push --no-recurse-submodules origin HEAD"):
        assert call(repo, command) is None, command
    for command in (f"git -C {o} status", f"cd {o} && git fetch origin"):
        assert "판단할 수 없는" in (call(repo, command) or ""), command


def test_git_does_not_write_while_its_hooks_are_writable(repo):
    """A hooks folder inside a write root holds code the agent may edit and git then runs."""
    config = repo / ".git/config"
    base = config.read_text(encoding="utf-8")
    # A relative value depends on the worktree that runs it; an inline key, an include or a per-worktree
    # file is configuration the reader does not see: each counts as a folder the task may write.
    for setting in ("[core]\n\thooksPath = .githooks", "[core] hooksPath = .githooks", "[core]\n\thooksPath = ../hooks",
                    '[core]\n\thooksPath = "hooks dir"', f"[core]\n\thooksPath = {(repo / 'hooks').as_posix()}",
                    "[include]\n\tpath = more.config", "[extensions]\n\tworktreeConfig = true"):
        config.write_text(base + setting + "\n", encoding="utf-8")
        # A file this reader does not open may also hold a promisor remote, which stops every git form earlier.
        hidden = "include" in setting or "worktreeConfig" in setting
        for command in ("git commit -qm x", "git merge --no-edit origin/main"):
            assert ("partial clone" if hidden else "훅 폴더") in (call(repo, command) or ""), (setting, command)
        assert (call(repo, "git status") is None) != hidden, setting
    config.write_text(base + f"[core]\n\thooksPath = {(repo.parent / 'hooks').as_posix()}\n", encoding="utf-8")
    assert call(repo, "git commit -qm x") is None


# -- Follow-up (a), review round 1 ------------------------------------------------

def test_a_protected_name_is_protected_in_every_worktree(repo):
    """Another worktree's copy of a protected file, committed there, would reach the checkout by history."""
    wt = Path(os.environ["TEMP"]).as_posix() + "/wt"
    for target in ("verification.toml", ".wiki/adapter.toml", ".claude/settings.json", ".codex/config.toml"):
        for command in (f"printf x > {wt}/{target}", f"echo x >> {wt}/{target}"):
            assert "허가를 정하는 파일" in (call(repo, command) or ""), command
    assert call(repo, f"printf x > {wt}/notes.md") is None


def test_a_worktree_is_removed_only_by_its_absolute_path():
    # git matches a relative operand as the tail of any registered worktree's path, wherever it is.
    assert any(e.startswith("top(") for e in effects("git worktree remove --force sibling", ctx=POSIX))


@pytest.mark.parametrize("command,dialect", [
    ("$env:GH_REPO='github.com/owner/other'; gh pr edit --body x", "ps"),
    ("$env:GH_REPO+='github.com/owner/other'; gh pr edit --body x", "ps"),
    ("$env:GH_REPO += 'github.com/owner/other'; gh pr edit --body x", "ps"),
    ("GH_REPO=owner/other gh pr edit --body x", "posix"),
])
def test_gh_never_targets_another_repository(command, dialect):
    assert any(e.startswith("top(") for e in effects(command, dialect, ctx=replace(POSIX, d=dialect)))


def test_a_powershell_assignment_written_as_one_word_is_still_an_assignment():
    for command in ("$env:PATH='C:/tools'; git status", "$env:PATH+=';C:/tools'; git status"):
        assert any("PATH" in e for e in effects(command, "ps")), command
    assert effects("$env:PYTHONIOENCODING='utf-8'; $x='rm'; $n+=1; git status", "ps") == {"read"}


# -- Follow-up (a), review round 5 ------------------------------------------------
# gh's and curl's HTTP clients follow a redirect the response names, which nothing on the command line turns
# off, and gh's pr create and pr merge also run git without the network options. Neither has a row (round 8).
@pytest.mark.parametrize("command", ["gh api user", "gh run view 5 --log", "gh pr view 3", "gh pr diff 3",
                                     "gh pr edit --body x", "gh pr create --fill", "gh pr merge --squash"])
def test_gh_is_only_a_spawn(command):
    assert effects(command, ctx=POSIX) == {"read", "spawn(gh)"}, command


def test_curl_is_only_a_spawn_again():
    assert effects("curl -s https://github.com/", ctx=POSIX) == {"read", "spawn(curl)"}


def test_a_host_is_read_from_the_authority_alone():
    for target in ("https://unlisted.example?@github.com", "https://unlisted.example#@github.com",
                   "https://unlisted.example/@github.com"):
        found = effects(f"HTTPS_PROXY='{target}' python -m ruff check tool", ctx=POSIX)
        assert "network(unlisted.example)" in found and "network(github.com)" not in found, target


def test_a_tls_key_log_is_a_write_the_guard_cannot_place():
    assert any("SSLKEYLOGFILE" in e for e in effects("SSLKEYLOGFILE=verification.toml git fetch origin", ctx=POSIX))


def test_a_per_worktree_configuration_leaves_the_remotes_unread(tmp_path):
    (tmp_path / "config").write_text('[extensions]\n\tworktreeConfig = true\n[remote "origin"]\n'
                                     '\turl = https://github.com/owner/repo.git\n', encoding="utf-8")
    found = effects("git push origin HEAD", ctx=replace(POSIX, remotes=guard_grant.remote_hosts(tmp_path)))
    assert any(e.startswith("top(") for e in found)


# -- Follow-up (a), review round 6 ------------------------------------------------

def test_a_repository_write_is_placed_at_the_worktree_root():
    # Moving between commits changes the whole worktree, not just the folder git runs in.
    for command in ("git -C sub switch main", "git -C sub stash pop", "cd sub && git merge --no-edit origin/main"):
        found = effects(command, ctx=POSIX)
        assert "write(/work/repo)" in found and "write(/work/repo/sub)" not in found, command


@pytest.mark.parametrize("command", [
    "git push origin HEAD", "git -c http.followRedirects=false push origin HEAD",
    "git push --no-recurse-submodules origin HEAD",
    "git -c http.followRedirects=true push --no-recurse-submodules origin HEAD",
    # A `-C` value is a directory, whatever it spells.
    "git -C http.followRedirects=false push --no-recurse-submodules origin HEAD",
    # A server may answer a fetch in protocol v2, whatever was asked, and name another host for the pack.
    "git fetch origin", "git pull --ff-only",
    "git -c http.followRedirects=false -c protocol.version=1 fetch --no-recurse-submodules origin",
    "git -c http.followRedirects=false -c protocol.version=1 pull --ff-only --no-recurse-submodules",
])
def test_git_network_turns_off_redirects_and_submodules(command):
    # Each lets the server's answer pick a host the grant never read: a redirect, a submodule's remote, a pack URI.
    assert any(e.startswith("top(") for e in effects(command, ctx=POSIX)), command


def test_a_configured_redirect_setting_leaves_the_remotes_unread(tmp_path):
    # A URL-specific setting wins over the one on the command line.
    (tmp_path / "config").write_text('[remote "origin"]\n\turl = https://github.com/owner/repo.git\n'
                                     '[http "https://github.com"]\n\tfollowRedirects = true\n', encoding="utf-8")
    assert guard_grant.remote_hosts(tmp_path) == (("origin", guard_grant.UNREAD),)


def test_a_drive_path_is_relative_on_posix():
    assert any(e.startswith("top(") for e in effects("git worktree remove --force C:/work/wt", ctx=POSIX))
    assert "delete(/tmp/wt)" in effects("git worktree remove --force /tmp/wt", ctx=POSIX)


@pytest.mark.parametrize("name", ["GIT_TRACE", "GIT_TRACE2_EVENT", "GIT_TRACE_PACKET"])
def test_a_git_trace_file_is_a_write_the_guard_cannot_place(name):
    assert any(name in e for e in effects(f"{name}=/tmp/t.txt git worktree list", ctx=POSIX))


def test_every_git_row_is_top_in_a_partial_clone(tmp_path):
    # Reading a missing object fetches it from the promisor remote, wherever its URL is rewritten to.
    (tmp_path / "config").write_text('[remote "origin"]\n\turl = https://github.com/owner/repo.git\n'
                                     '\tpromisor = true\n[extensions]\n\tpartialClone = origin\n', encoding="utf-8")
    ctx = replace(POSIX, remotes=guard_grant.remote_hosts(tmp_path))
    for command in ("git cat-file -p HEAD:notes.txt", "git status", "git fetch origin"):
        assert any(e.startswith("top(") for e in effects(command, ctx=ctx)), command


# -- Follow-up (a), review round 7 ------------------------------------------------
ORIGIN = '[remote "origin"]\n\turl = https://github.com/owner/repo.git\n'


@pytest.mark.parametrize("config,configs", [
    (ORIGIN + "[include]\n\tpath = more.config\n", []),
    (ORIGIN + "[extensions]\n\tworktreeConfig = true\n", []),
    (ORIGIN, ['[remote "origin"]\n\tpartialCloneFilter = blob:none\n']),
    (ORIGIN, ["[include]\n\tpath = ~/more.config\n"]),
    # A Trace2 target makes any git call append to that file (round 10).
    (ORIGIN, ["[trace2]\n\teventTarget = ../outside/f.txt\n"]),
    (ORIGIN + "[trace2]\n\tnormalTarget = ../outside/f.txt\n", []),
    # A submodule's own configuration, which a recursing read uses, may hold a promisor remote (round 11).
    (ORIGIN + '[submodule "child"]\n\turl = https://unlisted.example/child.git\n', []),
    (ORIGIN, ["[submodule]\n\trecurse = true\n"]),
    # Without the repository's own file, the global ones still apply.
    (None, ["[trace2]\n\teventTarget = ../outside/f.txt\n"]),
])
def test_a_promisor_setting_that_may_hide_makes_git_top(tmp_path, monkeypatch, config, configs):
    if config is not None:
        (tmp_path / "config").write_text(config, encoding="utf-8")
    monkeypatch.setattr(guard_grant, "git_configs", lambda: configs)
    remotes = guard_grant.remote_hosts(tmp_path)
    assert any(e.startswith("top(") for e in effects("git cat-file -p HEAD:notes.txt",
                                                     ctx=replace(POSIX, remotes=remotes)))


@pytest.mark.parametrize("place", [".gitconfig", ".config/git/config"])
def test_the_global_configuration_is_read_from_gits_home(tmp_path, monkeypatch, place):
    # Git on Windows takes HOME before USERPROFILE, which Python's home uses (round 12).
    home_at(monkeypatch, tmp_path / "profile")
    monkeypatch.setenv("HOME", str(tmp_path / "settings"))
    (tmp_path / "settings" / place).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "settings" / place).write_text("[trace2]\n\teventTarget = ../outside/f.txt\n", encoding="utf-8")
    (tmp_path / "config").write_text(ORIGIN, encoding="utf-8")
    assert (guard_grant.LAZY, guard_grant.UNREAD) in guard_grant.remote_hosts(tmp_path)


def test_a_bare_common_directory_is_protected(repo, tmp_path):
    # A worktree of a bare repository: its configuration and hooks have no `.git` in their path (round 13).
    temp = Path(os.environ["TEMP"])
    shared = temp / "shared.git"
    (shared / "worktrees/wt").mkdir(parents=True)
    (shared / "hooks").mkdir()
    (shared / "config").write_text((repo / ".git/config").read_text(encoding="utf-8"), encoding="utf-8")
    (shared / "worktrees/wt/commondir").write_text("../..\n", encoding="utf-8")
    wt = tmp_path / "work" / "wt"
    (wt / ".wiki").mkdir(parents=True)
    (wt / ".wiki/adapter.toml").write_text((repo / ".wiki/adapter.toml").read_text(encoding="utf-8"),
                                           encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {(shared / 'worktrees/wt').as_posix()}\n", encoding="utf-8")
    for target in (shared / "hooks/pre-commit", shared / "config", shared / "worktrees/wt/config.worktree"):
        assert call(wt, f"echo x > {target.as_posix()}"), target
    assert call(wt, f"echo x > {(temp / 'notes.md').as_posix()}") is None


def test_an_absorbed_submodule_makes_git_top(tmp_path, monkeypatch):
    (tmp_path / "config").write_text(ORIGIN, encoding="utf-8")
    monkeypatch.setattr(guard_grant, "git_configs", lambda: [])
    assert guard_grant.remote_hosts(tmp_path) == (("origin", "github.com"),)
    (tmp_path / "modules/child").mkdir(parents=True)
    remotes = guard_grant.remote_hosts(tmp_path)
    assert any(e.startswith("top(") for e in effects("git grep --cached x", ctx=replace(POSIX, remotes=remotes)))


@pytest.mark.parametrize("extra", ["[fetch]\n\tbundleURI = https://unlisted.example/r.bundle\n",
                                   "[bundle]\n\tversion = 1\n", '[remote "origin"]\n\tproxy = http://unlisted.example\n',
                                   "[http]\n\tproxy = http://unlisted.example\n", "[core]\n\tgitProxy = helper\n",
                                   "[http]\n\tcurloptResolve = github.com:443:192.0.2.1\n",
                                   "[http]\n\tcookieFile = ../outside/f.txt\n\tsaveCookies = true\n"])
def test_a_configured_host_besides_the_remote_leaves_the_remotes_unread(tmp_path, monkeypatch, extra):
    (tmp_path / "config").write_text(ORIGIN + extra, encoding="utf-8")
    monkeypatch.setattr(guard_grant, "git_configs", lambda: [])
    assert guard_grant.remote_hosts(tmp_path) == (("origin", guard_grant.UNREAD),)


def test_a_proxy_in_the_environment_leaves_the_remotes_unread(tmp_path, monkeypatch):
    (tmp_path / "config").write_text(ORIGIN, encoding="utf-8")
    # A certificate file named `ca-bundle.crt` is not a bundle URI.
    monkeypatch.setattr(guard_grant, "git_configs", lambda: ["[http]\n\tsslCAInfo = C:/ssl/ca-bundle.crt\n"])
    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    assert guard_grant.remote_hosts(tmp_path) == (("origin", "github.com"),)
    monkeypatch.setenv("HTTPS_PROXY", "http://unlisted.example")
    assert guard_grant.remote_hosts(tmp_path) == (("origin", guard_grant.UNREAD),)


# -- Follow-up (a), review round 8 ------------------------------------------------
# An edited ignore rule can stop hiding a protected file; git then stages or stashes it like any other.
@pytest.mark.parametrize("command", ["git add -A", "git add --all", "git add .", "git add .wiki/", "git add sub/..",
                                     "git stash push -u", "git stash -u", "git stash --include-untracked"])
def test_git_never_picks_up_untracked_files_it_was_not_named(command):
    assert any(e.startswith("top(") for e in effects(command, ctx=POSIX)), command


def test_a_named_file_is_staged_as_a_write_of_it(repo):
    (repo / "notes").mkdir()
    assert effects("git add -u notes.md", ctx=POSIX) == {"read", "write(/work/repo)", "write(/work/repo/notes.md)"}
    assert "허가를 정하는 파일" in (call(repo, "git add .wiki/adapter.toml") or "")
    assert "of a folder" in (call(repo, "git add notes") or "")
    assert call(repo, "git add notes.md") is None


def test_packfile_uris_leave_the_remotes_unread(tmp_path, monkeypatch):
    (tmp_path / "config").write_text(ORIGIN + "[fetch]\n\turiProtocols = https\n", encoding="utf-8")
    monkeypatch.setattr(guard_grant, "git_configs", lambda: [])
    assert guard_grant.remote_hosts(tmp_path) == (("origin", guard_grant.UNREAD),)
