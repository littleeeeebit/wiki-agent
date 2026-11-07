"""Command positions, fence labels, comments and grouped call targets are distinct."""

import os
import shutil
from types import SimpleNamespace

import pytest

from common import shell
from test_shell_review import fixture_result


@pytest.fixture
def ps5(monkeypatch):
    monkeypatch.setattr(shell, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))


def assert_denied(command, monkeypatch):
    import command_repair

    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.launch("gate", command=command)
    monkeypatch.setattr(command_repair, "os", shell.os)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for host in ("claude", "codex"):
        assert command_repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("extension", ["exe", "cmd", "bat", "ps1"])
@pytest.mark.parametrize("context", ["{value}", "Write-Output first; {value}", "{value} | Write-Output", "{value}\nWrite-Output done"])
def test_standalone_legacy_gate_programs_are_invoked_without_arguments(ps5, extension, context):
    value = f'"outside/target.{extension}"'
    command = context.format(value=value)
    expected = context.format(value="& " + value)
    for name in ("gate", "registered-check"):
        for form in ("ps5", "pwsh7"):
            assert shell.resolve(name, form=form, command=command) == expected


@pytest.mark.parametrize("label", ["powershell", "PowerShell", "POWERSHELL", "pWsH", "PS1"])
@pytest.mark.parametrize("directory", ["docs", "tool/prompts", "tool/main"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_powershell_fences_are_catalogued_independently_of_case(tmp_path, label, directory, newline):
    import shell_lint

    folder = tmp_path / directory
    folder.mkdir(parents=True)
    (folder / "instructions.md").write_bytes(newline.join((f"```{label}", "Get-UnlistedCommand -Fixture", "```", "")).encode("utf-8"))
    assert any("미등록 명령" in message for _, message in shell_lint.check(tmp_path))


@pytest.mark.parametrize("expression", ["(Write-Output ok)", "(1, 2)[0]", "& { Write-Output ok }"])
@pytest.mark.parametrize("feature", ["Get-Error", "Test-Json", "a > b", "$x ?? other"])
def test_comments_after_completed_expressions_do_not_require_ps7(ps5, monkeypatch, expression, feature):
    import command_repair

    command = expression + "# " + feature
    assert shell.seven_only(command) == ()
    for name in ("gate", "registered-check"):
        assert shell.resolve(name, command=command) == command
    monkeypatch.setattr(command_repair, "os", shell.os)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for host in ("claude", "codex"):
        assert command_repair.refusal(payload, host) is None
        assert command_repair.repair(payload, host)[0] is payload


@pytest.mark.parametrize("program", ['"powershell.exe"', "'outside/powershell.exe'", '"Get-Error"', "'Test-Json'"])
@pytest.mark.parametrize("depth", [0, 1, 2, 3])
def test_grouped_literal_call_targets_cannot_bypass_ps5_capabilities(ps5, monkeypatch, program, depth):
    target = "(" * depth + program + ")" * depth
    command = "& " + target + (" -Command 'Get-Error'" if "powershell" in program else "")
    assert shell.seven_only(command)
    assert_denied(command, monkeypatch)


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("depth", [0, 1, 2, 3])
@pytest.mark.parametrize("body", ["'Get-Error'", '"Test-Json"', "Get-Error", "@'\nGet-Error\n'@"])
def test_classic_child_capabilities_are_enforced_independently_of_parent_version(ps5, monkeypatch, major, depth, body):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "& " + "(" * depth + '"outside/powershell.exe"' + ")" * depth + " -Command " + body
    assert_denied(command, monkeypatch)


@pytest.mark.parametrize("depth", [0, 1, 2, 3])
def test_grouped_safe_child_and_literal_shell_text_remain_allowed(ps5, monkeypatch, depth):
    target = "(" * depth + '"outside/powershell.exe"' + ")" * depth
    command = "& " + target + " -Command 'Write-Output \"Get-Error\"'"
    assert shell.seven_only(command) == ()
    assert shell.launch("gate", command=command)[0][0] == "powershell"
    literal = "Write-Output '" + command.replace("'", "''") + "'"
    assert shell.seven_only(literal) == ()
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("pwsh", "7.5", 7),))
    assert shell.launch("gate", command="Get-Error; " + command)[0][0] == "pwsh"


@pytest.mark.parametrize("separator", [" && ", " || "])
def test_standalone_programs_are_invoked_before_chain_lowering(ps5, separator):
    command = separator.join(('"outside/first.cmd"', '"outside/second.cmd"'))
    for name in ("gate", "registered-check"):
        for form in ("ps5", "pwsh7"):
            resolved = shell.resolve(name, form=form, command=command)
            assert '& "outside/first.cmd"' in resolved
            assert '& "outside/second.cmd"' in resolved


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell execution boundary")
@pytest.mark.parametrize("consumer", ["gate", "registered-check", "server-gate", "refactor-runner"])
def test_standalone_fixture_check_really_runs_and_preserves_failure(tmp_path, monkeypatch, consumer):
    executable = shutil.which("powershell")
    if not executable:
        pytest.skip("Windows PowerShell is not installed")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell(executable, "5.1", 5),))
    fixture = tmp_path / "check.cmd"
    fixture.write_text("@echo fixture-ran\n@exit /b 17\n", encoding="utf-8")
    command = '"' + fixture.as_posix() + '"'
    code, output = fixture_result(command, consumer, tmp_path)
    assert code == 17
    assert output.splitlines() == ["fixture-ran"]


@pytest.mark.parametrize("expression", ["(Write-Output ok)", "(1, 2)[0]", "& { Write-Output ok }"])
def test_comment_ends_at_newline_and_does_not_hide_later_code(ps5, expression):
    command = expression + "# Test-Json\nGet-Error"
    assert shell.seven_only(command) == ("Get-Error",)
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.resolve("gate", command=command)
