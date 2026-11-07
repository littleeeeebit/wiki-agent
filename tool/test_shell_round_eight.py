"""Declared executable aliases retain the target shell's capability boundary."""

import base64
import os
import shutil
import subprocess

import pytest

from common import shell
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")
DEFINITIONS = [
    "Set-Alias helper powershell", "New-Alias helper powershell", "sal helper powershell", "nal helper powershell",
    "Set-Alias -Value powershell -Name helper", "New-Alias -Name 'helper' -Value 'powershell'",
    "Set-Item Alias:helper powershell", "New-Item -Path Alias:helper -Value powershell",
    "Set-Alias -Name helper powershell", "Set-Alias helper -Value powershell",
    "Set-Item -Path Alias:helper powershell", "New-Item -Path Alias: -Name helper -Value powershell",
    "si Alias:helper powershell", "ni Alias:helper -Value powershell",
    r"Set-Item Alias:\helper powershell", r"New-Item Alias:\helper -Value powershell",
]
CALLS = ["& helper", "helper", "& 'helper'", ". helper", "$runner='helper'; & $runner"]
MODES = ["-Command 'Get-Error'", "'-Command' 'Write-Output ok;' 'Get-Error'",
         "-EncodedCommand '" + base64.b64encode("Get-Error".encode("utf-16-le")).decode() + "'"]


@pytest.mark.parametrize("definition", DEFINITIONS)
@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("major", [5, 7])
def test_classic_alias_bodies_are_denied_in_all_call_forms(monkeypatch, request, definition, call, mode, major):
    repair, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = definition + "; " + call + " " + mode
    assert shell.classic_seven_only(command)
    assert "Get-Error" in shell.seven_only(command)
    for name in ("gate", "registered-check"):
        with pytest.raises(shell.Refusal):
            shell.launch(name, command=command)
    for host, verdict in (("claude", claude.verdict), ("codex", codex.verdict)):
        payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
        assert repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert verdict(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("definition", DEFINITIONS)
@pytest.mark.parametrize("program", ["python", "pwsh", "powershell"])
@pytest.mark.parametrize("major", [5, 7])
def test_compatible_alias_targets_remain_runnable(monkeypatch, request, definition, program, major):
    repair, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = definition.replace("powershell", program) + "; & helper -Command 'Write-Output ok'"
    for name in ("gate", "registered-check"):
        assert shell.resolve(name, command=command) == command
        assert shell.launch(name, command=command)[0][0] == "fixture-shell"
    assert any(child.program == program and child.kind == "alias" for child in shell.invocations(command))
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for host, verdict in (("claude", claude.verdict), ("codex", codex.verdict)):
        assert repair.refusal(payload, host) is None
        output = (verdict(payload) or {}).get("hookSpecificOutput", {})
        assert output.get("permissionDecision") != "deny"
        assert output.get("updatedInput", payload["tool_input"])["command"] == command


def test_native_parser_confirms_alias_definition_and_call_shapes():
    rows = native_commands([definition + "; " + call + " -Command 'Get-Error'" for definition in DEFINITIONS for call in CALLS])
    assert all(row["errors"] == 0 for row in rows)
    assert all(len(row["commands"]) >= 2 for row in rows)


@pytest.mark.parametrize("definition", [
    "Set-Alias first powershell; Set-Alias helper first",
    "Set-Alias helper python; Set-Alias helper powershell",
    "Set-Alias install Set-Alias; install helper powershell",
    "if ($condition) { Set-Alias helper powershell }",
])
def test_alias_chains_and_changes_never_hide_classic_children(definition):
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=definition + "; & helper -Command 'Get-Error'")


@pytest.mark.parametrize("command", [
    "Write-Output 'Set-Alias helper powershell'",
    "# Set-Alias helper powershell\nWrite-Output ok",
    "$map=@{ Set-Alias='powershell'; helper='value' }",
])
def test_alias_definition_text_in_data_does_not_create_aliases(command):
    assert shell.resolve("gate", command=command) == command


@pytest.mark.parametrize("definition", ["Set-Alias helper $external", "Set-Alias $external powershell",
                                       "Set-Alias helper powershell -Scope Global", "Set-Alias helper helper",
                                       "Set-Alias helper powershell -Option Constant; Set-Alias helper python -Force",
                                       "Set-Alias helper powershell; Set-Alias helper ''",
                                       "Set-Alias helper powershell; New-Alias helper python"])
def test_uncertain_alias_targets_never_become_unrelated_programs(definition):
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=definition + "; & helper -Command 'Get-Error'")


@pytest.mark.parametrize("major", [5, 7])
def test_alias_lookup_does_not_mutate_shadowed_executable_names_or_data(monkeypatch, request, major):
    import command_repair
    _, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "Set-Alias powershell python; powershell outside/check.py"
    assert shell.resolve("gate", command=command) == command
    assert command_repair._pwsh(command, "ps") == command
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for verdict in (claude.verdict, codex.verdict):
        output = (verdict(payload) or {}).get("hookSpecificOutput", {})
        assert output.get("permissionDecision") != "deny"
        assert output.get("updatedInput", payload["tool_input"])["command"] == command


@pytest.mark.parametrize("value", ["$target", '"$target"'])
def test_literal_alias_values_can_use_proved_variables(value):
    command = "$target='powershell'; Set-Alias helper " + value + "; & helper -Command 'Get-Error'"
    assert shell.classic_seven_only(command)
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=command)


@pytest.mark.parametrize("name", ["helper", "HELPER", "helper.exe"])
@pytest.mark.parametrize("major", [5, 7])
def test_alias_identity_keeps_case_and_executable_suffix_separate(monkeypatch, name, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = f"Set-Alias {name} 'outside/powershell.exe'; & {name} -Command 'Get-Error'"
    assert shell.classic_seven_only(command)
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=command)


@pytest.mark.skipif(os.name != "nt", reason="Windows executable-alias fixture")
@pytest.mark.parametrize("definition", ["Set-Alias fixturechild {target}", "New-Alias fixturechild {target}",
                                      "Set-Item Alias:fixturechild {target}", "New-Item Alias:fixturechild -Value {target}",
                                      r"Set-Item Alias:\fixturechild {target}", r"New-Item Alias:\fixturechild -Value {target}"])
def test_native_alias_fixture_preserves_output_and_failure(monkeypatch, definition):
    executable = shutil.which("powershell")
    if not executable:
        pytest.skip("Windows PowerShell is not installed")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell(executable, "5.1", 5),))
    command = definition.format(target=shell.quote(executable, "ps5")) + " | Out-Null; & fixturechild -NoProfile -NonInteractive -Command 'Write-Output alias-fixture; exit 17'"
    argv, use_shell = shell.launch("gate", command=command)
    done = subprocess.run(argv, shell=use_shell, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert (done.returncode, done.stdout.splitlines(), done.stderr) == (17, ["alias-fixture"], "")
