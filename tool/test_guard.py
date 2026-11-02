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
        '[guard]\nwrite_roots = ["../shared"]\nnetwork_hosts = ["pypi.org"]\nexecutables = ["npx"]\n',
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
                    "git add -A && git commit -qm x && git push origin HEAD", "git status --short && git diff --stat",
                    "echo hi > out.txt", "python tool/search \"query\"", "echo x > ../shared/f",
                    "echo hi > /tmp/x.log", "ls ~ && cat /etc/hosts", "find . -name '*.py'"):
        assert call(repo, command) is None, command
    for command in ("Get-ChildItem; Get-Content README.md", "'x' > out.txt", "'a,b'.Split(',')"):
        assert call(repo, command, tool="PowerShell") is None, command


def test_each_denial_names_the_effect_and_the_grant_that_would_allow_it(repo):
    reason = call(repo, "GH_HOST=unlisted.example cargo build && echo x > ../other/f")
    assert "spawn(cargo)" in reason and "`[guard] executables` 에 `cargo`" in reason
    assert "network(unlisted.example)" in reason and "`[guard] network_hosts` 에 `unlisted.example`" in reason
    assert "write(" in reason and "other" in reason and "`[guard] write_roots`" in reason
    assert reason.splitlines()[-1].startswith("허가: 쓰기 루트") and "npx" in reason and "pypi.org" in reason


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
    for command in ("printf x > safe/a", "echo x > .wiki-notes.md", "git add -A && git commit -qm x"):
        assert call(repo, command) is None, command


# -- The closed table (spec rev 5) ------------------------------------------------
# Only a program the table lists, with the flags its row lists, yields effects other than `top`; a program
# with no row is a `spawn` only a grant naming it allows. Every form outside the table is denied by design,
# whichever of the two it reduces to. The rules below are walked over the tables themselves, so a row added
# later is held to them without a new test.

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
    # A cmdlet is not an executable a grant could name: outside the table it is `top`.
    assert any(e.startswith("top(") for e in effects("Zz-Unlisted ../outside/target.txt", "ps"))
    assert "허가된 실행 파일이 아니다" in call(repo, "xxd -r dump.hex ../outside/target.txt")


# Forms earlier rounds found acting outside the grant, each on a neutral target. None has a row in the
# core table, so each is denied; the follow-up specs that add their rows bring their own effect tests.
OUTSIDE_TABLE_FORMS = {
    # File writers, copies and moves.
    "rm": ("rm -rf ../outside", "Bash"),
    "touch": ("touch .wiki/adapter.toml", "Bash"),
    "cp-into-directory": ("cp ../adapter.toml .wiki", "Bash"),
    "cp-target-flag": ("cp -t .wiki src/adapter.toml", "Bash"),
    "mv-into-dir": ("mv src/adapter.toml .wiki", "Bash"),
    "mktemp-outside": ("mktemp -p ../outside", "Bash"),
    "copy-item": ("Copy-Item src/adapter.toml -Destination .wiki", "PowerShell"),
    "move-item": ("Move-Item src/adapter.toml .wiki", "PowerShell"),
    "set-content": ("Set-Content ../outside/target.txt x", "PowerShell"),
    "remove-item": ("Remove-Item -LiteralPath:'../outside/target.txt'", "PowerShell"),
    "cmd-copy": ('cmd /c "copy src\\adapter.toml .wiki"', "Bash"),
    "cmd-del": ("cmd /c del /s target.txt", "Bash"),
    # Links and trees.
    "ln": ("ln -s ../outside link", "Bash"),
    "cp-symbolic": ("cp -s ../outside/target.txt link && printf x > link", "Bash"),
    "cp-archive": ("cp -a ../outside dir", "Bash"),
    "rsync-archive": ("rsync -a ../outside/ dir", "Bash"),
    "new-item-symlink": ("New-Item -ItemType SymbolicLink -Path link -Value ../outside", "PowerShell"),
    "new-item-junction": ("ni -ItemType Junction link -Value ../outside", "PowerShell"),
    "mklink": ("cmd /c mklink /J link ..\\outside", "Bash"),
    "rename-item": ("Rename-Item link b; Set-Content b/f x", "PowerShell"),
    # Archives.
    "tar-extract": ("tar -xf bundle.tar -C safe", "Bash"),
    "tar-remove-files": ("tar -cf saved.tar --remove-files ../outside/target.txt", "Bash"),
    "unzip": ("unzip bundle.zip -d safe", "Bash"),
    "gzip": ("gzip ../outside/target.txt", "Bash"),
    "expand-archive": ("Expand-Archive bundle.zip -DestinationPath safe", "PowerShell"),
    # Network clients.
    "curl": ("curl -O http://localhost:8000/verification.toml", "Bash"),
    "curl-connect-to": ("curl --connect-to localhost:80:unlisted.example:80 http://localhost/", "Bash"),
    "wget": ("wget http://localhost:8000/verification.toml", "Bash"),
    "web-request": ("Invoke-WebRequest http://localhost:8000/x -OutFile .wiki", "PowerShell"),
    "gh-api": ("gh api https://unlisted.example/path", "Bash"),
    "gh-clone": ("gh repo clone owner/repo out; printf x > out/link/target.txt", "Bash"),
    "gh-pr-checkout": ("gh pr checkout 3", "Bash"),
    # Script interpreters and code that runs code.
    "awk": ("awk 'BEGIN { print \"x\" > \"../outside/target.txt\" }'", "Bash"),
    "sed-write": ("sed 's/a/b/w ../outside/target.txt' startup.rc", "Bash"),
    "xargs": ("ls | xargs rm", "Bash"),
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
    "ansi-c-escape": ("rm -- $'a\\tb'", "Bash"),
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
    "git-merge": ("git merge topic", "Bash"),
    "git-pull": ("git pull", "Bash"),
    "git-rebase": ("git rebase main", "Bash"),
    "git-reset": ("git reset --hard HEAD~1", "Bash"),
    "git-stash": ("git stash pop", "Bash"),
    "git-fetch": ("git fetch origin", "Bash"),
    "git-clone": ("git clone https://github.com/owner/repo out", "Bash"),
    "git-dash-c": ("git -C sub status", "Bash"),
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


# A command named by a path is that file, not the program its basename suggests: it never borrows a row
# or a granted name, wherever the file lies and whichever runner starts it.
def test_a_path_named_executable_is_judged_by_its_path(repo):
    outside = repo.parent / "outside"
    outside.mkdir()
    (outside / "cat").write_text("#!/bin/sh\n", encoding="utf-8")
    for command, tool in (("../outside/cat README.md", "Bash"), ("./python tool/x.py", "Bash"),
                          ("../outside/python tool/x.py", "Bash"), ("./git push origin HEAD", "Bash"),
                          ("/usr/bin/time python tool/search q", "Bash"), ("& ./python x", "PowerShell")):
        assert "허가된 실행 파일이 아니다" in (call(repo, command, tool=tool) or ""), command
    assert call(repo, "cat README.md && git status") is None


def test_a_path_granted_by_path_is_allowed(repo):
    adapter = repo / ".wiki/adapter.toml"
    adapter.write_text(adapter.read_text(encoding="utf-8").replace(
        'executables = ["npx"]', 'executables = ["npx", ".venv/bin/python"]'), encoding="utf-8")
    assert call(repo, ".venv/bin/python -m pytest") is None
    assert "허가된 실행 파일이 아니다" in call(repo, "../outside/.venv/bin/python -m pytest")


# A variable that redirects a client to another host reaches that host, whatever program it is set for.
@pytest.mark.parametrize("command", [
    "GH_HOST=unlisted.example python -m ruff check tool",
    "GH_REPO=unlisted.example/o/r python -m ruff check tool",
    "HTTPS_PROXY=http://unlisted.example:8080 python -m ruff check tool",
])
def test_a_variable_that_redirects_the_network_is_denied(repo, command):
    assert "network(unlisted.example)" in (call(repo, command) or ""), command


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
    "git-restore": ("git restore --source=HEAD~1 -- .wiki/adapter.toml", "Bash"),
    "git-checkout": ("git checkout HEAD~1 -- ../outside/target.txt", "Bash"),
}


@pytest.mark.parametrize("form", TOP_FORMS)
def test_each_unevaluated_construct_reduces_to_top(repo, form):
    command, tool = TOP_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and "판단할 수 없는" in reason, (form, reason)


# R4 F11 — an executable is keyed by the name it is run by, suffix and path included: a grant for one
# file does not allow another that shares its stem.
def test_executable_identities_keep_their_suffix(repo):
    adapter = repo / ".wiki/adapter.toml"
    adapter.write_text(adapter.read_text(encoding="utf-8").replace(
        'executables = ["npx"]', 'executables = ["npx", "./helper.exe"]'), encoding="utf-8")
    assert call(repo, "./helper.exe --version") is None
    for command in ("./helper.cmd --version", "./helper --version", "./helper.bat"):
        assert "허가된 실행 파일이 아니다" in (call(repo, command) or ""), command


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
    for ok in ("git push origin HEAD", "git push", "git push -u origin topic", "cd tool && git push origin HEAD"):
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


@pytest.mark.parametrize("config,expected", [
    ('[remote "origin"]\n url = https://read.example/o/r.git\n pushurl = https://write.example/o/r.git\n',
     {"network(read.example)", "network(write.example)"}),
    ('[remote "origin"]\n url = https://read.example/o/r.git\n url = git@write.example:o/r.git\n',
     {"network(read.example)", "network(write.example)"}),
    ('[remote "origin"]\n url = ../other.git\n', "local path"),
    ('[remote "origin"]\n url = file:///c/other.git\n', "local path"),
    ('[remote "origin"]\n url = https://read.example/o/r.git\n[url "https://write.example/"]\n'
     ' insteadOf = https://read.example/\n', "rewritten"),
    ('[include]\n path = extra.config\n[remote "origin"]\n url = https://read.example/o/r.git\n', "rewritten"),
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
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "config").write_text(config, encoding="utf-8")
    found = effects("git push origin HEAD", ctx=replace(POSIX, remotes=guard_grant.remote_hosts(tmp_path)))
    if isinstance(expected, set):
        assert {e for e in found if e.startswith("network(")} == expected
    else:
        assert any(expected in e for e in found if e.startswith("top(")), found


def test_a_branch_upstream_given_as_a_url_is_a_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
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
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "config").write_text('[remote "origin"]\n url = https://read.example/o/r.git\n', encoding="utf-8")
    found = effects("git push origin HEAD", ctx=replace(POSIX, remotes=guard_grant.remote_hosts(tmp_path)))
    assert any("rewritten" in e for e in found), found


def test_git_runs_only_in_the_checkout_whose_configuration_is_protected(repo):
    (repo / "sub/.git").mkdir(parents=True)
    for command in ("cd sub && git diff -- sample.dat", "cd sub && git status", "cd sub && git commit -m x"):
        assert "판단할 수 없는" in (call(repo, command) or ""), command
    # Nothing may lay a repository down for git to trust, by a redirection or an edit tool.
    for command in ("echo x > sub2/.git", "echo x > sub/.git/config", "echo x > sub/.GIT/config"):
        assert "허가를 정하는 파일" in (call(repo, command) or ""), command
    assert "허가를 정하는 파일" in call(repo, tool="Write", file_path=str(repo / "sub/.git/config"), content="")
    assert call(repo, "git diff -- a.txt && echo x > .gitignore-notes") is None


def test_the_null_devices_stay_devices():
    assert effects("echo x > /dev/null") == {"read"}
    assert effects("echo x > NUL") == {"read"}
    assert effects("echo x > /dev/null", ctx=POSIX) == {"read"}
    assert effects("Write-Output x > $null", "ps") == {"read"}
