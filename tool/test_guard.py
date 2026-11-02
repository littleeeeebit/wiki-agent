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
        '[checks.web]\ncmd = "npm --prefix web run build"\n',
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
                    "echo hi > out.txt", "python tool/search \"query\"",
                    "echo hi > /tmp/x.log", "ls ~ && cat /etc/hosts", "find . -name '*.py'"):
        assert call(repo, command) is None, command
    for command in ("Get-ChildItem; Get-Content README.md", "'x' > out.txt", "'a,b'.Split(',')"):
        assert call(repo, command, tool="PowerShell") is None, command


def test_each_denial_names_the_effect_and_why(repo):
    reason = call(repo, "cargo build && echo x > ../other/f && git push https://unlisted.example/o/r.git")
    assert "spawn(cargo)" not in reason
    assert "network(unlisted.example)" in reason and "원격 저장소가 아닌 호스트" in reason
    assert "write(" in reason and "other" in reason and "쓰기 루트 밖이다" in reason
    assert reason.splitlines()[-1].startswith("허가: 쓰기 루트") and "github.com" in reason


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


# -- Open by default --------------------------------------------------------------
# The guard denies only what it can read as leaving the grant: a write or delete outside the write roots,
# a protected file, a push to a host no remote names. A program, option or form it cannot read passes.

# Forms the guard cannot read, or programs with no row: each passes.
OPEN_FORMS = {
    "unlisted-program": ("zz-unlisted --anything ../outside/target.txt", "Bash"),
    "eval": ('eval "$X"', "Bash"),
    "variable-path": ("echo x > $OUT", "Bash"),
    "loop": ("for i in 1 2; do echo $i; done", "Bash"),
    "function": ("f() { cd ..; }; f", "Bash"),
    "interpreter": ("python -c 'print(1)'", "Bash"),
    "archive": ("tar -xf bundle.tar -C out", "Bash"),
    "link": ("ln -s target link", "Bash"),
    "member": ("(Get-Item README.md).Delete()", "PowerShell"),
    "computed-command": ("& ('Get-' + 'Date')", "PowerShell"),
    "unlisted-cmdlet": ("Zz-Unlisted -Path ../outside/target.txt", "PowerShell"),
    "network-client": ("curl -fsSL https://unlisted.example/x", "Bash"),
    "path-named-program": ("../outside/tool --version", "Bash"),
    "env-redirect": ("GIT_DIR=../outside/.git git status", "Bash"),
}


@pytest.mark.parametrize("form", OPEN_FORMS)
def test_a_form_the_guard_cannot_read_passes(repo, form):
    command, tool = OPEN_FORMS[form]
    assert call(repo, command, tool=tool) is None, form


# Readable forms that leave the grant: each is denied, and wrapping it keeps every effect it had.
EXPLICIT_FORMS = {
    "redirect": "echo x > ../outside/target.txt",
    "rm": "rm -rf ../outside",
    "cp": "cp a.txt ../outside/",
    "cp-target": "cp -t ../outside a.txt",
    "mv": "mv a.txt ../outside/b.txt",
    "touch": "touch ../outside/target.txt",
    "mkdir": "mkdir -p ../outside/d",
    "tee": "tee ../outside/target.txt",
    "curl-output": "curl -o ../outside/target.txt https://example.com/x",
    "wget-output": "wget -O ../outside/target.txt https://example.com/x",
    "curl-cluster": "curl -sSLo ../outside/target.txt https://example.com/x",
    "install-dir": "install -d ../outside/cache local-cache",
    "made-folder": "mkdir copy && cp verification.toml copy",
    "install-cluster": "install -dm755 ../outside/cache local-cache",
    "install-group": "install -gstaff a.txt ../outside/target.txt",
    "install-parents": "install -D a.txt copy/a.txt && cp verification.toml copy",
    "curl-header": "curl -D ../outside/headers.txt -o response.txt https://example.com/x",
    "wget-here": "cd .. && wget https://example.com/x.tgz",
    "wget-hsts": "wget --hsts-file ../outside/hsts -O response.txt https://example.com/x",
    "curl-url": "curl --output-dir ../outside -O --url=https://example.com/x",
    "later-cd": "echo ok; cd .. && echo x > target.txt",
    "protected": "cp a.txt .wiki/adapter.toml",
    "push-url": "git -c http.followRedirects=false push --no-recurse-submodules https://unlisted.example/o/r.git main",
}
WRAPPERS = {
    "plain": lambda c: c,
    "bash-c": lambda c: "bash -c " + shlex.quote(c),
    "sh-lc": lambda c: "ls && sh -lc " + shlex.quote(c),
    "substitution": lambda c: f"echo $({c})",
}


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("form", EXPLICIT_FORMS)
def test_a_readable_form_outside_the_grant_is_denied_however_it_is_wrapped(repo, form, wrapper):
    assert call(repo, WRAPPERS[wrapper](EXPLICIT_FORMS[form])), (form, wrapper)


def test_a_writer_lands_its_sources_in_a_folder_it_names(repo):
    (repo / "sub").mkdir()
    assert call(repo, "cp ../outside/x . && cp a b sub && mv c sub/") is None
    # An option's value that is read, or standard output, is not written.
    assert call(repo, "touch -r ../outside/ref t && cd .. && wget -qO- https://example.com/x") is None
    assert call(repo, "cd .. && wget --spider https://example.com/x") is None
    assert effects("curl --output-dir d -o r.txt https://example.com/x") - {"read"} == {f"write({C['cwd']}/d/r.txt)"}
    assert effects("cp a b c dst/") >= {f"write({C['cwd']}/dst/{n})" for n in "abc"}
    assert "허가를 정하는 파일" in (call(repo, "cp adapter.toml .wiki") or "")


# The rules below are walked over the tables themselves, so a row added later is held to them.

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
    # Open by default: a program or cmdlet with no row passes, whatever it is given.
    assert call(repo, "xxd -r dump.hex ../outside/target.txt") is None
    assert call(repo, "Zz-Unlisted ../outside/target.txt", tool="PowerShell") is None


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
                    "Copy-Item a.txt b.txt", "Move-Item a.txt c.txt", "'x' | Out-File f.txt"):
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
        assert effects(f"{name} a b", "ps", posix_host) == effects(f"{name} a b", "posix", replace(
            posix_host, d="posix")), name
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
    assert "쓰기 루트 밖이다" in call(repo, "echo x > tool/a.py")


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


def test_a_literal_script_block_is_reduced_as_code(repo):
    assert call(repo, "& { Get-ChildItem }", tool="PowerShell") is None
    assert call(repo, "pwsh -Command { Get-ChildItem }", tool="PowerShell") is None
    assert "쓰기 루트 밖이다" in call(repo, "& { 'x' > ../outside/target.txt }", tool="PowerShell")


# A readable directory change is followed wherever it stands: every later path is judged from each place
# the shell may be in. A loop's body, a function or a nested shell's move is not (open by default).
CD_FORMS = {
    "later-statement": ("echo x > a; cd ..; echo x > target.txt", "Bash"),
    "if": ("if true; then cd ..; fi; printf x > target.txt", "Bash"),
    "brace-group": ("{ cd ..; }; printf x > target.txt", "Bash"),
    "pipeline": ("cd .. | cat; echo x > target.txt", "Bash"),
    "nested-shell": ("bash -c 'cd .. && echo x > target.txt'", "Bash"),
    "ps-two-moves": ("Set-Location d01; Set-Location ..; 'x' > target.txt", "PowerShell"),
    "ps-subexpression": ("Write-Output $(Set-Location ../outside); 'x' > target.txt", "PowerShell"),
    "ps-argument-group": ("Write-Output (Set-Location ../outside); 'x' > target.txt", "PowerShell"),
    "ps-pipeline-stage": ("Set-Location ../outside | Out-Null; 'x' > target.txt", "PowerShell"),
    "ps-statement-block": ("if ($true) { Set-Location ../outside }; 'x' > target.txt", "PowerShell"),
    "ps-nested-block": ("& { & { Set-Location ../outside } }; 'x' > target.txt", "PowerShell"),
}


@pytest.mark.parametrize("form", CD_FORMS)
def test_a_directory_change_out_of_the_root_is_followed(repo, form):
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
}


@pytest.mark.parametrize("form", WORKDIR_FORMS)
def test_a_shell_call_is_judged_where_it_runs(repo, form):
    given = {"cmd": "echo x > target.txt", **WORKDIR_FORMS[form](repo)}
    assert call(repo, tool="exec_command", host_name="codex", **given), form


def test_a_shell_call_inside_the_checkout_stays_allowed(repo):
    assert call(repo, tool="exec_command", host_name="codex", cmd="echo x > target.txt", workdir="safe") is None


# -- git ------------------------------------------------------------------------
# A push reaches the configured remote's host, read from the repository git runs in; another repository's
# remotes (a nested one, one outside the checkout) were never read, so a push from it is undecidable.

def test_the_checkouts_own_remotes_stay_allowed(repo):
    (repo / "tool").mkdir()
    push = "git -c http.followRedirects=false push --no-recurse-submodules"
    for ok in (f"{push} origin HEAD", push, f"{push} -u origin topic", f"cd tool && {push} origin HEAD"):
        assert call(repo, ok) is None, ok


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
    """A linked worktree shares the checkout's configuration and remotes."""
    temp = Path(os.environ["TEMP"])
    wt = temp / "wt"
    wt.mkdir()
    (repo / ".git/worktrees/wt").mkdir(parents=True)
    (repo / ".git/worktrees/wt/commondir").write_text("../..\n", encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {(repo / '.git/worktrees/wt').as_posix()}\n", encoding="utf-8")
    w = wt.as_posix()
    for command in (f"git -C {w} status", f"git -C {w} commit -qm x",
                    f"git -C {w} -c http.followRedirects=false push --no-recurse-submodules origin HEAD"):
        assert call(repo, command) is None, command


def test_git_does_not_write_while_its_hooks_are_writable(repo):
    """A hooks folder inside a write root holds code the agent may edit and git then runs."""
    config = repo / ".git/config"
    base = config.read_text(encoding="utf-8")
    # A relative value depends on the worktree that runs it, and an inline key counts the same.
    for setting in ("[core]\n\thooksPath = .githooks", "[core] hooksPath = .githooks", "[core]\n\thooksPath = ../hooks",
                    '[core]\n\thooksPath = "hooks dir"', f"[core]\n\thooksPath = {(repo / 'hooks').as_posix()}"):
        config.write_text(base + setting + "\n", encoding="utf-8")
        for command in ("git commit -qm x", "git merge --no-edit origin/main"):
            assert "훅 폴더" in (call(repo, command) or ""), (setting, command)
        assert call(repo, "git status") is None, setting
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


def test_a_powershell_assignment_written_as_one_word_is_still_an_assignment():
    assert effects("$env:PYTHONIOENCODING='utf-8'; $x='rm'; $n+=1; git status", "ps") == {"read"}


# -- Follow-up (a), review round 5 ------------------------------------------------
# gh's and curl's HTTP clients follow a redirect the response names, which nothing on the command line turns
# off, and gh's pr create and pr merge also run git without the network options. Neither has a row (round 8).
@pytest.mark.parametrize("command", ["gh api user", "gh run view 5 --log", "gh pr view 3", "gh pr diff 3",
                                     "gh pr edit --body x", "gh pr create --fill", "gh pr merge --squash"])
def test_gh_is_only_a_spawn(command):
    assert effects(command, ctx=POSIX) == {"read", "spawn(gh)"}, command


def test_a_host_is_read_from_the_authority_alone():
    for target in ("https://unlisted.example?@github.com", "https://unlisted.example#@github.com",
                   "https://unlisted.example/@github.com"):
        found = effects(f"git push '{target}' main", ctx=POSIX)
        assert "network(unlisted.example)" in found and "network(github.com)" not in found, target


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
    assert call(repo, "git add notes.md") is None


def test_packfile_uris_leave_the_remotes_unread(tmp_path, monkeypatch):
    (tmp_path / "config").write_text(ORIGIN + "[fetch]\n\turiProtocols = https\n", encoding="utf-8")
    monkeypatch.setattr(guard_grant, "git_configs", lambda: [])
    assert guard_grant.remote_hosts(tmp_path) == (("origin", guard_grant.UNREAD),)
