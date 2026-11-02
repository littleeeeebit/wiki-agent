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
    ("PATH=/opt/tools:$PATH python x.py", "posix", "PATH"),
    ("git -c core.sshCommand=helper fetch", "posix", "git config key"),
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
    reason = call(repo, "cargo build && curl https://unlisted.example/x && rm -rf ../other")
    assert "spawn(cargo)" in reason and "`[guard] executables` 에 `cargo`" in reason
    assert "network(unlisted.example)" in reason and "`[guard] network_hosts` 에 `unlisted.example`" in reason
    assert "delete(" in reason and "other" in reason and "`[guard] write_roots`" in reason
    assert reason.splitlines()[-1].startswith("허가: 쓰기 루트") and "npx" in reason and "pypi.org" in reason


def test_a_nested_shell_keeps_the_effects_it_wraps(repo):
    assert "delete(" in call(repo, "bash -c 'rm -rf ../other'")
    assert "delete(" in call(repo, "Remove-Item ../other -Recurse", tool="PowerShell")
    # Codex on Windows runs PowerShell; elsewhere the same text is an unknown program.
    codex = call(repo, tool="exec_command", host_name="codex", cmd="Remove-Item ../other -Recurse")
    assert ("delete(" if os.name == "nt" else "spawn(") in codex


# Soundness: a call passes only when every effect it has lies under the grant, and an effect the model
# cannot follow is `top`, which lies under none. Each form below once had an effect the model missed
# (PR #272 R1, F1–F9); each is built on a neutral target and must be denied for the reason named.
FORMS = {
    "F1-link-made-earlier": ("ln -s ../other link && rm link/target.txt", "Bash", "판단할 수 없는"),
    "F2-directory-destination": ("cp /tmp/adapter.toml .wiki/", "Bash", "허가를 정하는 파일"),
    "F3-constructor": ("[System.IO.FileStream]::new('C:/outside/target.txt', 'Create').Dispose()", "PowerShell",
                       "판단할 수 없는"),
    "F4-path-array": ("Remove-Item -LiteralPath 'keep','../other/target.txt'", "PowerShell", "판단할 수 없는"),
    "F5-read-verb-output": ("git diff HEAD~1 HEAD --output=../other.patch", "Bash", "쓰기 루트 밖이다"),
    "F6-function-call-site": ("f() { touch target.txt; }; cd ..; f", "Bash", "판단할 수 없는"),
    "F7-ansi-c-escape": ("rm -- $'a\\tb'", "Bash", "판단할 수 없는"),
    "F8-startup-file": ("bash --rcfile startup.rc -ic 'true'", "Bash", "판단할 수 없는"),
    "F9-reader-preprocessor": ("rg --pre ./helper needle README.md", "Bash", "판단할 수 없는"),
}


@pytest.mark.parametrize("form", FORMS)
def test_every_form_with_an_effect_outside_the_grant_is_denied(repo, form):
    command, tool, marker = FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and marker in reason, reason


# Monotonicity: wrapping a command keeps every effect it had, so no wrapper turns a denial into a pass.
WRAPPERS = {
    "bash-c": lambda c: "bash -c " + shlex.quote(c),
    "sh-lc": lambda c: "ls && sh -lc " + shlex.quote(c),
    "substitution": lambda c: f"echo $({c})",
}


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("form", [f for f, (_, tool, _) in FORMS.items() if tool == "Bash"])
def test_no_wrapper_turns_a_denied_form_into_a_pass(repo, form, wrapper):
    assert call(repo, WRAPPERS[wrapper](FORMS[form][0])), form


# A second review round found more forms the model followed too far. Each rule is a property:
# a construct whose later effect the model cannot place soundly must reduce to `top` (or resolve
# the real target), so the call is denied. Inputs name neutral targets outside the grant.
ROUND2 = {
    # A copy into a name that is already a directory writes a descendant of it, not the name.
    "copy-into-existing-directory": ("cp ../adapter.toml .wiki", "Bash", "허가를 정하는 파일"),
    # A link (any flag spelling, hard or symbolic) redirects later paths; its target is not followable.
    "combined-link-flags": ("ln -sf ../outside link", "Bash", "판단할 수 없는"),
    "windows-junction": ("cmd /c mklink /J link ../outside", "Bash", "판단할 수 없는"),
    # A .NET method that writes is not made safe by its name alone; the receiver is unknown.
    "writing-instance-method": ("(Get-Item README.md).CopyTo('../outside/target.md')", "PowerShell",
                                "판단할 수 없는"),
    # Quoting only a parameter's value keeps it a named parameter, not a positional pathname.
    "quoted-parameter-value": ("Remove-Item -LiteralPath:'../outside/target.txt'", "PowerShell", "쓰기 루트 밖이다"),
    # ripgrep runs its preprocessor on each input file, so the child can mutate them.
    "ripgrep-preprocessor-file": ("rg --pre rm pattern README.md", "Bash", "판단할 수 없는"),
    # `-execdir` runs in the matched entry's directory, so a relative target lands somewhere unknown.
    "find-execdir-directory": ("find . -execdir rm target.txt ';'", "Bash", "판단할 수 없는"),
    # An arithmetic command runs the substitutions written inside it, and those its variables hold.
    "arithmetic-substitution": ("(( n = $(rm ../outside/target.txt) ))", "Bash", "판단할 수 없는"),
    # A git read verb can launch a program through an execution-capable option.
    "git-read-pager-program": ("git grep --open-files-in-pager=./helper pattern", "Bash", "판단할 수 없는"),
}


@pytest.mark.parametrize("form", ROUND2)
def test_each_followed_too_far_form_is_denied(repo, form):
    command, tool, marker = ROUND2[form]
    reason = call(repo, command, tool=tool)
    assert reason and marker in reason, (form, reason)


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("form", [f for f, (_, tool, _) in ROUND2.items() if tool == "Bash"])
def test_no_wrapper_turns_a_round2_denial_into_a_pass(repo, form, wrapper):
    assert call(repo, WRAPPERS[wrapper](ROUND2[form][0])), form


# A third review round named more forms that acted outside the grant. Each line below is a property,
# not one command: the rule it names holds across every syntax that reaches the same effect. Inputs
# name neutral targets (`outside/`, the protected `.wiki`/`verification.toml`), never a real command.

# F1 — a copy/move/extract into a directory writes files *under* it, so a protected file there is reached.
# The rule: the destination's descendants are named, whatever program or dialect writes them.
DEST_FORMS = {
    "cp-rename": ("cp src/adapter.toml .wiki", "Bash"),
    "cp-trailing-slash": ("cp src/adapter.toml .wiki/", "Bash"),
    "mv-into-dir": ("mv src/adapter.toml .wiki", "Bash"),
    "cp-target-flag": ("cp -t .wiki src/adapter.toml", "Bash"),
    "copy-item": ("Copy-Item src/adapter.toml -Destination .wiki", "PowerShell"),
    "copy-item-literal": ("Copy-Item -LiteralPath src/adapter.toml -Destination .wiki", "PowerShell"),
    "move-item": ("Move-Item src/adapter.toml .wiki", "PowerShell"),
    "copy-item-default-cwd": ("Copy-Item src/verification.toml", "PowerShell"),
    "cmd-copy": ('cmd /c "copy src\\adapter.toml .wiki"', "Bash"),
}


@pytest.mark.parametrize("form", DEST_FORMS)
def test_a_copy_into_a_protected_directory_is_denied(repo, form):
    command, tool = DEST_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and "허가를 정하는 파일" in reason, (form, reason)


def test_tar_creates_archives_but_never_extracts(repo):
    for command in ("tar -xPf payload.tar -C safe", "tar --absolute-names -xf payload.tar -C safe",
                    "tar -xf payload.tar -C safe", "tar xf payload.tar"):
        assert "판단할 수 없는" in call(repo, command), command
    assert call(repo, "tar -czf out.tar.gz src && tar -tf out.tar.gz") is None


# F2 — creating a link and then writing under it escapes the grant through the link; `realpath` cannot
# see a link that does not exist yet. The rule: a link effect beside any other write is undecidable.
LINK_FORMS = {
    "cp-symbolic": ("cp -s ../outside/target.txt link && printf x > link", "Bash"),
    "cp-symbolic-long": ("cp --symbolic-link ../outside/target.txt link && printf x > link", "Bash"),
    "cp-symbolic-combined": ("cp -sf ../outside/target.txt link && printf x > link", "Bash"),
    "cp-archive-into": ("cp -a ../outside dir && printf x > dir/f", "Bash"),
    "cp-recursive-into": ("cp -r ../outside dir && printf x > dir/f", "Bash"),
    "rsync-archive-into": ("rsync -a ../outside/ dir && printf x > dir/f", "Bash"),
    "new-item-symlink": ("New-Item -ItemType SymbolicLink -Path link -Target ../outside; Set-Content link x",
                         "PowerShell"),
    "new-item-junction": ("ni -Type Junction link -Value ../outside; Set-Content link/f x", "PowerShell"),
    "loop-order": ("for i in 1 2; do printf x > link; cp -s ../outside link; done", "Bash"),
}


@pytest.mark.parametrize("form", LINK_FORMS)
def test_a_link_written_through_in_the_same_command_is_denied(repo, form):
    command, tool = LINK_FORMS[form]
    assert call(repo, command, tool=tool), form


def test_a_recursive_copy_alone_is_allowed(repo):
    # A tree nothing else in the call writes under is judged as a write at its own place.
    for command in ("cp -r src dst", "mkdir -p out && cp -r src out", "Copy-Item -Recurse src dst"):
        assert call(repo, command, tool="PowerShell" if "Copy-Item" in command else "Bash") is None, command


# F3 — a PowerShell member name does not fix its effect (`String.Replace` reads, `FileInfo.Replace`
# writes). The rule: every syntax that invokes a member routes through one gate, and a polymorphic
# name on an unknown receiver is undecidable, whichever way it was written.
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
    for ok in ("'a,b'.Split(',')", "'x'.Replace('x','y')", "(Get-Date).ToString()",
               "Get-ChildItem | ForEach-Object { $_.Name }", "Get-ChildItem | % Name",
               "(1..3).ForEach({ $_ * 2 })"):
        assert call(repo, ok, tool="PowerShell") is None, ok


# F11/F12/F13/F14 — one form each, on a neutral target, that acted outside the grant.
OUTSIDE_FORMS = {
    "F11-local-executable": ("./cat", "Bash", "허가된 실행 파일이 아니다"),
    "F11-local-modelled-program": ("./git push origin HEAD", "Bash", "허가된 실행 파일이 아니다"),
    "F13-curl-connect-to": ("curl --connect-to localhost:80:unlisted.example:80 http://localhost/", "Bash",
                            "판단할 수 없는"),
    "F14-gh-absolute-endpoint": ("gh api https://unlisted.example/path", "Bash", "허가된 호스트가 아니다"),
}


@pytest.mark.parametrize("form", OUTSIDE_FORMS)
def test_each_remaining_form_outside_the_grant_is_denied(repo, form):
    command, tool, marker = OUTSIDE_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and marker in reason, (form, reason)


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("form", list(LINK_FORMS) + list(DEST_FORMS))
def test_no_wrapper_turns_a_round3_denial_into_a_pass(repo, form, wrapper):
    table = LINK_FORMS if form in LINK_FORMS else DEST_FORMS
    command, tool = table[form]
    if tool != "Bash":
        return
    assert call(repo, WRAPPERS[wrapper](command)), form


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


# A fourth review round. Each table is one rule stated as a property; every form in it reaches an effect
# outside the grant by another syntax, and each must be denied for the reason named. Inputs are neutral.

# F1 — a path effect on a directory that holds a protected file may write that file, whatever the program
# names it (a destination directory, a URL's or a server's file name, a patch). The rule lives in the judge.
PROTECTED_ANCESTOR_FORMS = {
    "rsync-into-directory": ("rsync ../adapter.toml .wiki", "Bash"),
    "scp-into-directory": ("scp ../adapter.toml .wiki", "Bash"),
    "rsync-tree-into-root": ("rsync -r ../x/ .", "Bash"),
    "curl-remote-name": ("curl -O http://localhost:8000/verification.toml", "Bash"),
    "web-request-folder": ("Invoke-WebRequest http://localhost:8000/x -MaximumRedirection 0 -OutFile .wiki",
                           "PowerShell"),
    "web-request-here": ("Invoke-WebRequest http://localhost:8000/x -MaximumRedirection 0 -OutFile .", "PowerShell"),
}


@pytest.mark.parametrize("form", PROTECTED_ANCESTOR_FORMS)
def test_a_write_to_a_directory_holding_a_protected_file_is_denied(repo, form):
    command, tool = PROTECTED_ANCESTOR_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and "허가를 정하는 파일" in reason, (form, reason)


def test_every_ancestor_of_a_protected_path_is_judged_protected_for_every_path_kind(repo):
    g = guard_grant.grant(repo, {})
    for p in g.protected:
        a = Path(p).parent
        while contained(str(a), g.roots) and a != a.parent:
            for kind in ("write", "write_tree", "delete"):
                assert "허가를 정하는 파일" in (guard_grant.outside((kind, str(a)), g) or ""), (kind, a)
            a = a.parent


def test_ordinary_writes_beside_protected_files_stay_allowed(repo):
    for command in ("rsync a.txt safe/", "printf x > safe/a", "curl -O http://localhost:8000/x.txt",
                    "git checkout -q -b topic && git commit -qm x", "gh pr checkout 3"):
        assert call(repo, command) is None, command
    assert call(repo, "Invoke-WebRequest http://localhost:8000/x -MaximumRedirection 0 -OutFile safe/out.bin",
                tool="PowerShell") is None


# F2 — a command that lays down a tree (recursive copy, rename, a git checkout) may carry a link in it, so
# another command in the same call that writes at or beneath that place is undecidable.
TREE_LINK_FORMS = {
    "copy-item-recurse": ("Copy-Item -Recurse ../d d; Set-Content d/link/f x", "PowerShell", "판단할 수 없는"),
    "mv-rename": ("mv a/link b; printf x > b/f", "Bash", "판단할 수 없는"),
    "rename-item": ("Rename-Item link b; Set-Content b/f x", "PowerShell", "판단할 수 없는"),
    "git-mv": ("git mv a/link b; printf x > b/f", "Bash", "판단할 수 없는"),
    "git-checkout": ("git checkout other; printf x > link/f", "Bash", "판단할 수 없는"),
    "moved-link-own-path": ("mv sub/link moved; printf x > moved", "Bash", "판단할 수 없는"),
    "copied-tree-copied-again": ("cp -r ../outside dst; cp -r src dst", "Bash", "판단할 수 없는"),
}


@pytest.mark.parametrize("form", TREE_LINK_FORMS)
def test_a_tree_written_through_in_the_same_call_is_denied(repo, form):
    command, tool, marker = TREE_LINK_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and marker in reason, (form, reason)


def test_a_tree_alone_or_a_checkout_without_symlinks_stays_allowed(repo):
    # Extraction and copies alone: `test_a_link_alone_is_allowed`.
    for command in ("mv a.txt b.txt", "git checkout other"):
        assert call(repo, command) is None, command
    assert call(repo, "Rename-Item sub/a.txt b.txt", tool="PowerShell") is None
    config = repo / ".git/config"
    config.write_text(config.read_text(encoding="utf-8") + "[core]\n\tsymlinks = false\n", encoding="utf-8")
    assert call(repo, "git checkout other; printf x > f") is None


# F3 — ForEach-Object's arguments are read the way PowerShell binds them; any argument list the reader
# cannot fully explain (a colon-bound value, a parenthesised value, another parameter) is undecidable.
FOREACH_FORMS = {
    "colon-bound": "Get-Item ../outside/target.txt | ForEach-Object -MemberName:Delete",
    "colon-bound-abbrev": "gi ../outside/target.txt | % -m:Delete",
    "parenthesised-member": "gi ../outside/target.txt | % ('Delete')",
    "parenthesised-membername": "gi ../outside/target.txt | % -MemberName ('Delete')",
    "input-object": "ForEach-Object -InputObject (Get-Item ../outside/target.txt) -MemberName Delete",
    "parenthesised-arguments": "gi README.md | % -MemberName MoveTo -ArgumentList ('../outside/t.md')",
    "out-variable-first": "gi ../outside/target.txt | % -OutVariable name Delete",
    "pipeline-variable-first": "gi ../outside/target.txt | % -pv name Delete",
    "argument-list-first": "gi ../outside/target.txt | % -ArgumentList name MoveTo",
}


@pytest.mark.parametrize("form", FOREACH_FORMS)
def test_a_foreach_argument_list_the_reader_cannot_explain_is_denied(repo, form):
    assert "판단할 수 없는" in (call(repo, FOREACH_FORMS[form], tool="PowerShell") or ""), form


def test_foreach_forms_the_reader_explains_stay_allowed(repo):
    for ok in ("Get-ChildItem | % -MemberName:Name", "Get-ChildItem | % -Process { $_.Name }",
               "1..2 | % -Begin {} -Process { $_ }", "'a.b' | % Split '.'"):
        assert call(repo, ok, tool="PowerShell") is None, ok


# F11 — a command named by a path is that file, not the program its basename suggests: it never borrows a
# model or a granted name, wherever the file lies and whichever runner starts it.
def test_a_path_named_executable_is_judged_by_its_path(repo):
    outside = repo.parent / "outside"
    outside.mkdir()
    (outside / "cat").write_text("#!/bin/sh\n", encoding="utf-8")
    for command, tool in (("../outside/cat README.md", "Bash"), ("./python tool/x.py", "Bash"),
                          ("../outside/python tool/x.py", "Bash"), ("/usr/bin/time python tool/search q", "Bash"),
                          ("Start-Process ../outside/python", "PowerShell"), ("& ./python x", "PowerShell")):
        assert "허가된 실행 파일이 아니다" in (call(repo, command, tool=tool) or ""), command
    assert call(repo, "cat README.md && git status") is None


def test_a_path_granted_by_path_is_allowed(repo):
    adapter = repo / ".wiki/adapter.toml"
    adapter.write_text(adapter.read_text(encoding="utf-8").replace(
        'executables = ["npx"]', 'executables = ["npx", ".venv/bin/python"]'), encoding="utf-8")
    assert call(repo, ".venv/bin/python -m pytest") is None
    assert "허가된 실행 파일이 아니다" in call(repo, "../outside/.venv/bin/python -m pytest")


# F12 — anything PowerShell evaluates in-process (a subexpression, a group, a pipeline stage) can move the
# location, so later relative paths are judged from every place it may now be.
LOCATION_FORMS = {
    "subexpression": "Write-Output $(Set-Location ../outside); Set-Content target.txt x",
    "argument-group": "Set-Content target.txt (Set-Location ../outside)",
    "pipeline-stage": "Set-Location ../outside | Out-Null; Set-Content target.txt x",
    "piped-location": "Get-Item ../outside | Set-Location; Set-Content target.txt x",
    "statement-block": "if ($true) { Set-Location ../outside }; Set-Content target.txt x",
    "nested-block": "& { & { Set-Location ../outside } }; Set-Content target.txt x",
    "bare-set-location": "Set-Location; Set-Content target.txt x",
}


@pytest.mark.parametrize("form", LOCATION_FORMS)
def test_a_location_moved_in_process_moves_later_paths(repo, form):
    assert call(repo, LOCATION_FORMS[form], tool="PowerShell"), form


def test_a_location_moved_in_another_process_does_not(repo):
    assert call(repo, '"$(Get-Date)"; Set-Content target.txt x', tool="PowerShell") is None
    assert call(repo, "pwsh -c 'Set-Location ..'; Set-Content target.txt x", tool="PowerShell") is None
    assert call(repo, "echo $(cd ..); echo x > target.txt") is None
    assert call(repo, "cd .. | cat; echo x > target.txt") is None


# F13/F14 — a network destination the guard cannot read (a bracketed field, a malformed override, an unknown
# endpoint, a variable that redirects the client) is undecidable, never dropped.
NETWORK_FORMS = {
    "gh-variable-endpoint": ('gh api "$ENDPOINT"', "판단할 수 없는"),
    "gh-variable-endpoint-after-option": ('gh api --cache 1h "$ENDPOINT"', "판단할 수 없는"),
    "gh-variable-endpoint-with-fields": ('gh api -X POST "$ENDPOINT" -f a=b', "판단할 수 없는"),
    "gh-variable-repo": ('gh -R "$R" issue list', "판단할 수 없는"),
    "gh-variable-hostname": ('gh --hostname "$H" api user', "판단할 수 없는"),
    "gh-repo-variable": ("GH_REPO=unlisted.example/o/r gh issue list", "network(unlisted.example)"),
    "gh-host-exported": ("export GH_HOST=unlisted.example; gh api user", "network(unlisted.example)"),
    "proxy-variable": ("HTTPS_PROXY=http://unlisted.example:8080 curl https://pypi.org/simple/x",
                       "network(unlisted.example)"),
}


@pytest.mark.parametrize("form", NETWORK_FORMS)
def test_a_network_destination_the_guard_cannot_read_is_denied(repo, form):
    command, marker = NETWORK_FORMS[form]
    reason = call(repo, command)
    assert reason and marker in reason, (form, reason)


def test_readable_network_destinations_stay_allowed(repo):
    for ok in ("curl -s http://localhost:8000/x", "curl -fsS -o out.json http://127.0.0.1:8000/api",
               "gh api repos/o/r/pulls", "gh api 'repos/{owner}/{repo}/pulls'", "gh api graphql -f query=x",
               "gh pr view 3", "GH_REPO=o/r gh issue list"):
        assert call(repo, ok) is None, ok


# F15 — a value computed at run time and then run (a command name, a nested shell's script) is undecidable;
# only a literal script block is reduced as code.
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
    assert "쓰기 루트 밖이다" in call(repo, "& { Remove-Item ../outside/target.txt }", tool="PowerShell")


# -- The closed table (spec rev 4) ----------------------------------------------
# Only a program the table lists, with the flags its row lists, yields effects other than `top`; a program
# with no row is a `spawn` only a grant naming it allows. The rules below are walked over the tables
# themselves, so a row added later is held to them without a new test.

def test_every_row_refuses_a_flag_it_does_not_list():
    from guard_models import KEYWORDS, TABLE
    from guard_windows import CMD_ROWS, PS_ROWS

    for name, r in TABLE.items():
        # A shell keyword (`time`) is read before any program: what follows it is the command.
        if not r.free and name not in KEYWORDS["posix"]:
            assert any(e.startswith("top(") for e in effects(f"{name} --zz-unmodelled x")), name
    for name in PS_ROWS:
        assert any(e.startswith("top(") for e in effects(f"{name} -ZzUnmodelled x", "ps")), name
    for name, switches in CMD_ROWS.items():
        if switches != "*":
            assert any(e.startswith("top(") for e in effects(f"cmd /c {name} /zz x")), name


def test_a_program_with_no_row_is_only_a_spawn(repo):
    assert effects("zz-unlisted-tool --anything ../outside/target.txt") == {"read", "spawn(zz-unlisted-tool)"}
    assert effects("Zz-Unlisted ../outside/target.txt", "ps") == {"read", "spawn(zz-unlisted)"}
    assert "허가된 실행 파일이 아니다" in call(repo, "xxd -r dump.hex ../outside/target.txt")


# Each form the spec names as outside the table, on a neutral target: every one reduces to `top`.
OUTSIDE_TABLE_FORMS = {
    # Link-creating forms.
    "ln": ("ln -s ../outside link", "Bash"),
    "cp-symbolic": ("cp -s ../outside/target.txt link", "Bash"),
    "cp-hard-link": ("cp -l ../outside/target.txt link", "Bash"),
    "cp-hard-link-tree": ("cp -lR .wiki x", "Bash"),
    "cp-archive": ("cp -a ../outside dir", "Bash"),
    "cp-preserve-links": ("cp -rP ../outside dir", "Bash"),
    "rsync-archive": ("rsync -a ../outside/ dir", "Bash"),
    "rsync-links": ("rsync -rl ../outside/ dir", "Bash"),
    "rsync-link-dest": ("rsync -r --link-dest=../../outside src/ dst/", "Bash"),
    "new-item-symlink": ("New-Item -ItemType SymbolicLink -Path link -Value ../outside", "PowerShell"),
    "new-item-hard-link": ("New-Item -ItemType HardLink -Path link -Value ../outside/target.txt", "PowerShell"),
    "new-item-junction": ("ni -ItemType Junction link -Value ../outside", "PowerShell"),
    "mklink": ("cmd /c mklink /J link ..\\outside", "Bash"),
    # Archive extraction, and creation that removes its sources.
    "tar-extract": ("tar -xf bundle.tar -C safe", "Bash"),
    "unzip": ("unzip bundle.zip -d safe", "Bash"),
    "gunzip": ("gunzip saved.gz", "Bash"),
    "expand-archive": ("Expand-Archive bundle.zip -DestinationPath safe", "PowerShell"),
    "tar-remove-files": ("tar -cf saved.tar --remove-files ../outside/target.txt", "Bash"),
    "gzip-removes-source": ("gzip ../outside/target.txt", "Bash"),
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
    # Redirect-following and connection-override network flags.
    "curl-location": ("curl -L http://localhost:8000/x", "Bash"),
    "curl-connect-to": ("curl --connect-to localhost:80:unlisted.example:80 http://localhost/", "Bash"),
    "curl-resolve": ("curl --resolve localhost:80:203.0.113.7 http://localhost/", "Bash"),
    "curl-proxy": ("curl -x http://unlisted.example:8080 http://localhost/", "Bash"),
    "curl-remote-header-name": ("curl -OJ http://localhost:8000/x", "Bash"),
    "web-request-redirects": ("Invoke-WebRequest http://localhost:8000/x -OutFile safe/x", "PowerShell"),
    "wget": ("wget http://localhost:8000/verification.toml", "Bash"),
    # A destination the model cannot name the final file of.
    "patch": ("patch -p1 < change.diff", "Bash"),
    "robocopy": ("cmd /c robocopy src .wiki adapter.toml", "Bash"),
    "xcopy": ("cmd /c xcopy src .wiki", "Bash"),
    # Undecidable flags of modelled programs.
    "del-subdirectories": ("cmd /c del /s target.txt", "Bash"),
    "cmd-for-command": ("cmd /c for /f %i in ('dir') do echo %i", "Bash"),
    "hash-path": ("hash -p ./helper cat; cat", "Bash"),
}


@pytest.mark.parametrize("form", OUTSIDE_TABLE_FORMS)
def test_each_form_outside_the_table_reduces_to_top(repo, form):
    command, tool = OUTSIDE_TABLE_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and "판단할 수 없는" in reason, (form, reason)


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("form", [f for f, (_, tool) in OUTSIDE_TABLE_FORMS.items() if tool == "Bash"])
def test_no_wrapper_turns_an_outside_table_form_into_a_pass(repo, form, wrapper):
    assert call(repo, WRAPPERS[wrapper](OUTSIDE_TABLE_FORMS[form][0])), form


# R4 F1 — git restoring or checking out a pathspec writes those paths; a curl `-w` that writes a file is
# not in the row. The protected files stay protected whichever verb or option names them.
GIT_PATHSPEC_FORMS = {
    "restore-source": ("git restore --source=HEAD~1 -- .wiki/adapter.toml", "허가를 정하는 파일"),
    "checkout-pathspec": ("git checkout HEAD~1 -- verification.toml", "허가를 정하는 파일"),
    "checkout-outside": ("git checkout HEAD~1 -- ../outside/target.txt", "쓰기 루트 밖이다"),
    "curl-write-out-file": ("curl -w '%output{../outside/target.txt}x' http://localhost:8000/", "판단할 수 없는"),
}


@pytest.mark.parametrize("form", GIT_PATHSPEC_FORMS)
def test_a_pathspec_or_write_out_reaching_a_protected_or_outside_file_is_denied(repo, form):
    command, marker = GIT_PATHSPEC_FORMS[form]
    reason = call(repo, command)
    assert reason and marker in reason, (form, reason)


# R4 F3 — whatever follows the last `.`/`::` is the member name; one the lexer cannot read as an
# identifier (an escape, a quote, a variable) is dynamic and undecidable.
@pytest.mark.parametrize("member", ["De`lete", "'Delete'", '"Delete"', "$m", "De``lete"])
def test_a_member_name_that_is_not_an_identifier_is_denied(repo, member):
    reason = call(repo, f"(Get-Item ../outside/target.txt).{member}()", tool="PowerShell")
    assert reason and "판단할 수 없는" in reason, member


# R4 F11 — an executable is keyed by the name it is run by, suffix and path included: a grant for one
# file does not allow another that shares its stem.
def test_executable_identities_keep_their_suffix(repo):
    adapter = repo / ".wiki/adapter.toml"
    adapter.write_text(adapter.read_text(encoding="utf-8").replace(
        'executables = ["npx"]', 'executables = ["npx", "./helper.exe"]'), encoding="utf-8")
    assert call(repo, "./helper.exe --version") is None
    for command in ("./helper.cmd --version", "./helper --version", "./helper.bat"):
        assert "허가된 실행 파일이 아니다" in (call(repo, command) or ""), command


# R4 F12 — a directory change in a body that runs any number of times (a loop, a function, a group) has
# no fixed set of places it ends in, in every dialect.
CD_IN_BODY_FORMS = {
    "for": ("cd sub && for i in 1 2; do cd ..; done; printf x > target.txt", "Bash"),
    "while": ("while true; do cd ..; done; printf x > target.txt", "Bash"),
    "brace-group": ("{ cd ..; }; printf x > target.txt", "Bash"),
    "function": ("f() { cd ..; }; printf x > target.txt", "Bash"),
    "ps-foreach": ("foreach ($i in 1..2) { Set-Location .. }; Set-Content target.txt x", "PowerShell"),
    "ps-while": ("while ($true) { cd .. }; Set-Content target.txt x", "PowerShell"),
    "ps-function": ("function f { Set-Location .. }; f; Set-Content target.txt x", "PowerShell"),
    "cmd-for": ("cmd /c for %i in (1 2) do cd ..", "Bash"),
}


@pytest.mark.parametrize("form", CD_IN_BODY_FORMS)
def test_a_directory_change_in_a_repeated_body_is_denied(repo, form):
    command, tool = CD_IN_BODY_FORMS[form]
    reason = call(repo, command, tool=tool)
    assert reason and "판단할 수 없는" in reason, (form, reason)


def test_a_directory_change_outside_any_body_stays_tracked(repo):
    assert call(repo, "cd sub && printf x > target.txt") is None
    assert call(repo, "if true; then cd sub; fi; printf x > target.txt") is None
    assert "쓰기 루트 밖이다" in call(repo, "cd .. && printf x > target.txt")


# R4 F18 — directory states are capped: past `STATES` possible places the call is `top`, and the guard's
# time grows at most linearly with the number of statements.
def test_directory_states_are_bounded():
    steps = "; ".join(f"Set-Location d{k:02}" for k in range(1, 25)) + "; Set-Content out.txt x"
    assert any("possible working directories" in e for e in effects(steps, "ps"))
    assert not any(e.startswith("top(") for e in effects("Set-Location d01; Set-Content out.txt x", "ps"))


def test_the_guards_time_grows_linearly_with_the_command():
    def best(n: int) -> float:
        command = "; ".join(f"Set-Location d{k}" if k % 2 else "Get-ChildItem" for k in range(n))
        took = []
        for _ in range(3):
            start = time.perf_counter()
            derive(command, "ps", CTX)
            took.append(time.perf_counter() - start)
        return min(took)

    small, large = best(200), best(1600)
    # Eight times the statements: linear work is about eight times the time, exponential is unbounded.
    assert large < small * 24, (small, large)
