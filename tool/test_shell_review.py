"""Properties for the version resolver's reviewed boundaries; inputs are data."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tomllib
from types import SimpleNamespace

import pytest

from common import shell


def assert_ps5_parses(script):
    if os.name == "nt" and (parser := shutil.which("powershell")):
        # The real parser checks syntax without invoking any input command.
        parse = ("$tokens = $null; $errors = $null; "
                 "[void][System.Management.Automation.Language.Parser]::ParseInput("
                 "[Console]::In.ReadToEnd(), [ref]$tokens, [ref]$errors); "
                 "$errors | ForEach-Object { $_.Message }; exit @($errors).Count")
        parsed = subprocess.run([parser, "-NoProfile", "-NonInteractive", "-Command", parse],
                                input=script, capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert parsed.returncode == 0, parsed.stdout + parsed.stderr


@pytest.fixture
def ps5(monkeypatch):
    monkeypatch.setattr(shell, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))


@pytest.mark.parametrize("name", ["gate", "registered-check"])
@pytest.mark.parametrize("source", ["local-verification", "held-out-refactoring"])
def test_required_workflow_chains_have_a_ps5_form_without_running_them(ps5, name, source):
    import debt

    local = tomllib.loads((Path(__file__).parents[1] / "verification.toml").read_text(encoding="utf-8"))["verification"]["local"]
    command = local if source == "local-verification" else f'"{sys.executable}" check.py && {debt.command()}'
    resolved = shell.resolve(name, command=command)
    assert "pipeline chain" not in shell.seven_only(resolved)
    assert "if (" in resolved
    argv, use_shell = shell.launch(name, command=command)
    assert argv[0] == "powershell" and not use_shell
    assert_ps5_parses(argv[-1])


@pytest.mark.parametrize("literal", ["Get-Error", "a > b", "a && b", "$x ?? value", "ForEach-Object -Parallel", "Test-Json"])
@pytest.mark.parametrize("form", ['"{text}"', "'{text}'", '@"\n{text}\n"@', "@'\n{text}\n'@"])
def test_literal_data_never_becomes_a_seven_only_feature(ps5, monkeypatch, literal, form):
    import command_repair

    command = "Write-Output " + form.format(text=literal)
    assert shell.seven_only(command) == ()
    for name in ("gate", "registered-check"):
        assert shell.resolve(name, command=command) == command
    monkeypatch.setattr(command_repair, "os", shell.os)
    for host in ("claude", "codex"):
        payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
        assert command_repair.refusal(payload, host) is None
        assert command_repair.repair(payload, host)[0] is payload


@pytest.mark.parametrize("command", [
    'Write-Output "text $(Get-Error)"',
    'Write-Output "text $(Write-Output \"literal\"; Get-Error)"',
    'Write-Output @"\ntext $(Get-Error)\n"@',
    'Write-Output "text $(Write-Output \"nested $(Get-Error)\")"',
])
def test_expandable_subexpressions_remain_code_and_are_denied(ps5, command):
    assert "Get-Error" in shell.seven_only(command)
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.resolve("gate", command=command)


@pytest.mark.parametrize("extension", ["exe", "cmd", "bat", "ps1"])
@pytest.mark.parametrize("context", [
    '$program = ({value}); Write-Output $program',
    'Write-Output ({value})',
    '$programs = @({value})',
    '$programs = @{{ location = ({value}) }}',
    'Write-Output {{ {value} }}',
    '{value}',
    '{value}\nWrite-Output done',
    '{value} -f "text"',
    '{value} -ceq "text"',
    '{value} - "text"',
])
def test_grouped_or_standalone_executable_paths_remain_data(ps5, extension, context):
    command = context.format(value=f'"outside/target.{extension}"')
    for name in ("gate", "registered-check"):
        for form in ("ps5", "pwsh7"):
            assert shell.resolve(name, form=form, command=command) == command


@pytest.mark.parametrize("suffix", [".py", ".json", ".txt", ".md"])
@pytest.mark.parametrize("prefix", ["First inspect the repository:\n", "First inspect the repository:\n\n    "])
def test_multiline_instruction_commands_are_catalogue_checked(tmp_path, suffix, prefix):
    import shell_lint

    command = "Get-UnlistedCommand -Fixture"
    prompt = prefix + command
    folder = tmp_path / ("tool/main" if suffix == ".py" else "tool/prompts")
    folder.mkdir(parents=True)
    path = folder / ("instructions" + suffix)
    content = "PROMPT = " + repr(prompt) if suffix == ".py" else json.dumps({"prompt": prompt}) if suffix == ".json" else prompt
    path.write_text(content, encoding="utf-8")
    findings = shell_lint.check(tmp_path)
    assert any("미등록 명령" in message and command in message for _, message in findings)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell execution boundary")
@pytest.mark.parametrize("consumer", ["resolver", "server-gate", "refactor-runner"])
@pytest.mark.parametrize("codes,operators,expected", [
    ((0, 0, 0), ("&&", "&&"), (0, ["first", "second", "third"])),
    ((17, 0, 0), ("&&", "&&"), (17, ["first"])),
    ((0, 19, 0), ("&&", "&&"), (19, ["first", "second"])),
    ((17, 0, 0), ("&&", "||"), (0, ["first", "third"])),
    ((0, 19, 0), ("||", "&&"), (0, ["first", "third"])),
])
def test_ps5_fixture_chains_short_circuit_and_preserve_exit_codes(tmp_path, monkeypatch, codes, operators, expected, consumer):
    executable = shutil.which("powershell")
    if not executable:
        pytest.skip("Windows PowerShell is not installed")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell(executable, "5.1.19041.1", 5),))
    commands = [shell.command_line([sys.executable, "-c", f"print('{name}'); raise SystemExit({code})"])
                for name, code in zip(("first", "second", "third"), codes)]
    command = f"{commands[0]} {operators[0]} {commands[1]} {operators[1]} {commands[2]}"
    if consumer == "server-gate":
        from main import runtime, specs
        code, output, cut = runtime.gate(command, tmp_path, threading.Event(), None, 20, None, specs.kill)
        assert not cut
    else:
        if consumer == "refactor-runner":
            import refactor_profile
            done = refactor_profile.sh([command], tmp_path, shell=True, seconds=20)
        else:
            argv, use_shell = shell.launch("gate", command=command)
            done = subprocess.run(argv, shell=use_shell, capture_output=True, text=True, encoding="utf-8", timeout=20)
        code, output = done.returncode, done.stdout
    assert (code, output.splitlines()) == expected


@pytest.mark.parametrize("command", [
    'powershell -Command \'Write-Output "Get-Error"\'',
    'powershell -Command "Write-Output \'Get-Error\'"',
    'Write-Output "powershell -Command \'Get-Error\'"',
    'Write-Output "literal `$(Get-Error)"',
])
def test_nested_shell_literals_and_escaped_expansions_remain_data(ps5, monkeypatch, command):
    import command_repair

    monkeypatch.setattr(command_repair, "os", shell.os)
    assert shell.resolve("gate", command=command) == command
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    assert command_repair.refusal(payload) is None


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_pretool_returns_the_compatible_chain_instead_of_allowing_raw_ps7_syntax(ps5, monkeypatch, host):
    import command_repair

    monkeypatch.setattr(command_repair, "os", shell.os)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": "Write-Output first && Write-Output second"}}
    assert command_repair.refusal(payload, host) is None
    repaired, _ = command_repair.repair(payload, host)
    assert repaired is not payload
    assert "pipeline chain" not in shell.seven_only(repaired["tool_input"]["command"])


@pytest.mark.parametrize("separator", [" && ", " &&\n", " && # note\n", " `\n&& "])
def test_chain_formatting_keeps_supported_statements_compatible(ps5, separator):
    command = separator.join(("Write-Output first", "Write-Output second"))
    resolved = shell.resolve("gate", command=command)
    assert "pipeline chain" not in shell.seven_only(resolved)
    assert "Write-Output first" in resolved and "Write-Output second" in resolved
    assert_ps5_parses(resolved)


@pytest.mark.parametrize("command", ["Write-Output first &&", "Write-Output first && # note\n"])
def test_incomplete_chain_is_not_silently_repaired_to_a_success(ps5, command):
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.resolve("gate", command=command)


@pytest.mark.parametrize("literal", ['"first\nGet-Error"', '@"\nGet-Error\n"@', "@'\nGet-Error\n'@"])
def test_chain_lowering_preserves_multiline_literal_operands(ps5, literal):
    resolved = shell.resolve("gate", command="Write-Output first && Write-Output " + literal)
    assert literal in resolved
    assert shell.seven_only(resolved) == ()
    assert_ps5_parses(resolved)


@pytest.mark.parametrize("command", [
    '& "Get-Error"', "& 'Test-Json'",
    '& "outside/powershell.exe" -Command \'Get-Error\'',
    '& \'outside/powershell.exe\' -Command "Get-Error"',
    "powershell -Command @'\nGet-Error\n'@",
])
def test_explicit_quoted_command_invocations_still_require_their_capability(ps5, command):
    assert shell.seven_only(command)
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.resolve("gate", command=command)
