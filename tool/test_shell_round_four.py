"""Native child arguments and CommonMark inline instructions are inspected as values."""

import base64
import os
import shutil
import subprocess

import pytest

from common import shell
from test_shell_round_two import assert_denied, ps5  # noqa: F401 -- shared pytest fixture

pytestmark = pytest.mark.usefixtures("ps5")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell native argument experiment")
def test_native_split_command_experiment_matches_single_argument():
    executable = shutil.which("powershell")
    if not executable:
        pytest.skip("Windows PowerShell is not installed")
    prefix = [executable, "-NoProfile", "-NonInteractive", "-Command"]
    statements = ["Write-Output first;", "Write-Output second; exit 17"]
    observations = []
    for args in (statements, [" ".join(statements)]):
        done = subprocess.run([*prefix, *args], capture_output=True, text=True, encoding="utf-8", timeout=20)
        observations.append((done.returncode, done.stdout.splitlines(), done.stderr))
    assert observations == [(17, ["first", "second"], "")] * 2


@pytest.mark.parametrize("option", ["-Command", "-c", "-EncodedCommand", "-enc"])
@pytest.mark.parametrize("quote", ["'", '"'])
@pytest.mark.parametrize("target", ["powershell", "& 'powershell.exe'"])
@pytest.mark.parametrize("major", [5, 7])
def test_quoted_native_options_preserve_child_capability_checks(monkeypatch, option, quote, target, major):
    body = base64.b64encode("Get-Error".encode("utf-16-le")).decode() if "enc" in option.lower() else "Get-Error"
    command = f"{target} {quote}{option}{quote} '{body}'"
    assert list(shell.nested_commands(command)) == [("powershell", "Get-Error")]
    assert shell.seven_only(command) == ("Get-Error",)
    assert shell.classic_seven_only(command)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    assert_denied(command, monkeypatch)


@pytest.mark.parametrize("target", ["powershell", "& 'powershell.exe'"])
@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("split", [False, True])
def test_split_and_single_child_commands_have_the_same_denial(monkeypatch, target, major, split):
    body = "'Write-Output ok;' 'Get-Error'" if split else "'Write-Output ok; Get-Error'"
    command = target + " -Command " + body
    assert list(shell.nested_commands(command)) == [("powershell", "Write-Output ok; Get-Error")]
    assert shell.seven_only(command) == ("Get-Error",)
    assert shell.classic_seven_only(command)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    assert_denied(command, monkeypatch)


@pytest.mark.parametrize("delimiter", ["`", "``", "```", "`````"])
@pytest.mark.parametrize("directory", ["docs", "tool/prompts", "tool/main"])
@pytest.mark.parametrize("content", ["Get-UnlistedCommand -Fixture", " Get-UnlistedCommand -Fixture ", "Get-UnlistedCommand\n-Fixture"])
def test_commonmark_inline_instructions_are_catalogue_checked(tmp_path, delimiter, directory, content):
    import shell_lint

    folder = tmp_path / directory
    folder.mkdir(parents=True)
    (folder / "instructions.md").write_text(f"Run {delimiter}{content}{delimiter}.", encoding="utf-8")
    assert any("미등록 명령" in message and "Get-UnlistedCommand" in message for _, message in shell_lint.check(tmp_path))


@pytest.mark.parametrize("tail", ["$option 'Get-Error'", "-Command $body", '-Command "$(Get-Error)"'])
@pytest.mark.parametrize("major", [5, 7])
def test_computed_native_child_arguments_are_denied_as_unresolved(monkeypatch, tail, major):
    import command_repair

    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "powershell " + tail
    with pytest.raises(ValueError, match="명령 인수를 확정할 수 없다"):
        shell.launch("gate", command=command)
    monkeypatch.setattr(command_repair, "os", shell.os)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for host in ("claude", "codex"):
        assert command_repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("option", ["-Command", "'-Command'", '"-Command"'])
def test_split_literal_data_stays_safe_and_outer_statements_stay_separate(option):
    command = "powershell " + option + " 'Write-Output' '\"Get-Error\"'; Write-Output done"
    assert list(shell.nested_commands(command)) == [("powershell", 'Write-Output "Get-Error"')]
    assert shell.seven_only(command) == ()
    assert shell.launch("gate", command=command)[0][0] == "powershell"


@pytest.mark.parametrize("delimiter", ["`", "``", "```", "`````"])
def test_inline_literal_data_is_not_an_instruction(delimiter):
    import shell_lint

    assert list(shell_lint.instructions(f"The file is {delimiter}startup.rc{delimiter}.")) == []


@pytest.mark.parametrize("body", ['"Write-Output ok;`nGet-Error"', '"Get-`u{0045}rror"'])
def test_native_escape_decoding_cannot_hide_child_capabilities(monkeypatch, body):
    assert_denied('powershell "-Command" ' + body, monkeypatch)


@pytest.mark.parametrize("container", ["*{span}*", "[{span}](outside/target.txt)", "![{span}](outside/target.png)"])
def test_nested_inline_code_tokens_are_also_catalogue_checked(container):
    import shell_lint

    command = "Get-UnlistedCommand -Fixture"
    assert command in list(shell_lint.instructions(container.format(span="``" + command + "``")))
