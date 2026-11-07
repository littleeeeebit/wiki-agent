"""Removing an alias must never reuse its former executable target."""

import os
import shutil
import subprocess

import pytest

from common import shell
from test_shell_round_eight import MODES
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")
REMOVALS = [program + " Alias:powershell" for program in ("Remove-Item", "ri", "rm", "rmdir", "rd", "del", "erase")]
REMOVALS += ["Remove-Item -Path 'Alias:powershell'", r"Remove-Item -LiteralPath Alias:\powershell",
             "Remove-Item -Force Alias:powershell", "Remove-Alias powershell", "Remove-Alias -Name powershell"]


@pytest.mark.parametrize("removal", REMOVALS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("major", [5, 7])
def test_removed_aliases_restore_child_capability_checks(monkeypatch, request, removal, mode, major):
    repair, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "Set-Alias powershell python; " + removal + "; powershell " + mode
    for name in ("gate", "registered-check"):
        with pytest.raises(shell.Refusal):
            shell.launch(name, command=command)
    children = list(shell.nested_commands(command))
    assert len(children) == 1 and children[0][0] == "powershell"
    assert shell.classic_seven_only(command)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for host, verdict in (("claude", claude.verdict), ("codex", codex.verdict)):
        assert repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"
        output = (verdict(payload) or {}).get("hookSpecificOutput", {})
        if output.get("permissionDecision") != "deny":
            executed = output.get("updatedInput", {})["command"]
            assert major == 7
            assert not shell.classic_seven_only(executed)
            assert list(shell.nested_commands(executed)) == [("pwsh", children[0][1])]
    rewritten = repair._pwsh(command, "ps")
    expected = command.rsplit("; powershell ", 1)[0] + "; pwsh -NoProfile " + mode if major == 7 else command
    assert rewritten == expected


@pytest.mark.parametrize("removal", REMOVALS)
@pytest.mark.parametrize("major", [5, 7])
def test_compatible_alias_removal_keeps_checks_runnable(monkeypatch, removal, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "Set-Alias powershell python; " + removal + "; powershell -Command 'Write-Output ok'"
    if major == 5 and removal.startswith("Remove-Alias"):
        with pytest.raises(shell.Refusal):
            shell.resolve("gate", command=command)
    else:
        assert shell.resolve("gate", command=command) == command


@pytest.mark.parametrize("removal", ["Remove-Item Alias:power*", "Remove-Item Alias:powershell -WhatIf",
                                    "Remove-Item Alias:powershell -Exclude powershell", "Remove-Item $external",
                                    "Remove-Item Alias:powershell,Alias:helper",
                                    "Remove-Item outside/target.txt,Alias:powershell",
                                    "Get-Alias powershell | Remove-Item",
                                    "Get-Alias powershell | ri",
                                    "Get-Alias powershell | & 'Remove-Item'",
                                    "Get-Alias powershell | ri -Force",
                                    "Remove-Alias powershell -Scope Global", "& { Remove-Item Alias:powershell }"])
@pytest.mark.parametrize("major", [5, 7])
def test_unproved_removals_cannot_reuse_stale_targets(monkeypatch, removal, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "Set-Alias powershell python; " + removal + "; powershell -Command 'Get-Error'"
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=command)


@pytest.mark.parametrize("command", [
    "Set-Alias helper python; Remove-Item outside/target.txt; & helper outside/check.py",
    "Set-Alias helper python; Write-Output 'Remove-Item Alias:helper'; & helper outside/check.py",
    "Set-Alias helper python; # Remove-Item Alias:helper\n& helper outside/check.py",
    "Set-Alias helper python; Set-Item outside/target.txt -Value 'Alias:powershell,Function:powershell'; & helper outside/check.py",
    "Set-Alias helper python; New-Item -Path outside/target.txt -Value 'Alias:powershell,Function:powershell'; & helper outside/check.py",
    "Set-Alias helper python; New-Item outside/target.txt -ItemType File -Value 'label:Alias:powershell,Function:powershell'; & helper outside/check.py",
    "Set-Alias helper python; Set-Item -Value 'Alias:powershell,Function:powershell' outside/target.txt; & helper outside/check.py",
])
def test_unrelated_removals_and_quoted_examples_preserve_alias_data(command):
    assert shell.resolve("gate", command=command) == command
    assert any(item.program == "python" and item.kind == "alias" for item in shell.invocations(command))


def test_native_parser_accepts_removal_forms_without_running_them():
    rows = native_commands(["Set-Alias powershell python; " + removal + "; powershell -Command 'Get-Error'" for removal in REMOVALS])
    assert all(row["errors"] == 0 for row in rows)
    assert all(len(row["commands"]) == 3 for row in rows)


@pytest.mark.parametrize("remover", ["Set-Alias cleanup Remove-Item; cleanup Alias:powershell",
                                   "$runner='Remove-Item'; & $runner Alias:powershell"])
def test_alias_and_variable_selected_removers_update_the_same_state(remover):
    command = "Set-Alias powershell python; " + remover + "; powershell -Command 'Get-Error'"
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=command)
    assert shell.classic_seven_only(command)


@pytest.mark.parametrize("scope", ["& {{ {body} }}", ". {{ {body} }}", "if ($condition) {{ {body} }}"])
@pytest.mark.parametrize("major", [5, 7])
def test_removing_a_shadow_cannot_hide_an_outer_classic_alias(monkeypatch, scope, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    body = "Set-Alias helper python; Remove-Item Alias:helper; & helper -Command 'Get-Error'"
    command = "Set-Alias helper powershell; " + scope.format(body=body)
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=command)


@pytest.mark.parametrize("option", ["Constant", "ReadOnly"])
def test_potentially_failed_removal_does_not_claim_a_known_target(option):
    command = "Set-Alias powershell python -Option " + option + "; Remove-Item Alias:powershell -Force; powershell -Command 'Get-Error'"
    assert list(shell.invocations(command))[-1].kind == "unresolved"
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=command)


@pytest.mark.skipif(os.name != "nt", reason="Windows alias-removal fixture")
@pytest.mark.parametrize("removal,major", [(removal, major) for removal in REMOVALS
                                         for major in ([7] if removal.startswith("Remove-Alias") else [5, 7])])
def test_native_alias_removal_restores_lookup_and_preserves_failure(monkeypatch, removal, major):
    executable = shutil.which("pwsh" if major == 7 else "powershell")
    if not executable:
        pytest.skip("The selected native PowerShell is not installed")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell(executable, f"{major}.1", major),))
    command = "Set-Alias powershell python; " + removal + "; powershell -NoProfile -NonInteractive -Command 'Write-Output removal-fixture; exit 17'"
    argv, use_shell = shell.launch("gate", command=command)
    result = subprocess.run(argv, shell=use_shell, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert (result.returncode, result.stdout.splitlines(), result.stderr) == (17, ["removal-fixture"], "")


MIXED_TARGETS = ["Function:powershell,Alias:powershell", "Alias:powershell,Function:powershell",
                 "'Function:powershell','Alias:powershell'", "'Alias:powershell','Function:powershell'",
                 "outside/target.txt,Function:powershell,Alias:powershell",
                 "Function:powershell, Alias:powershell", "Alias:powershell, Function:powershell",
                 "outside/target.txt, Function:powershell, Alias:powershell"]
MUTATIONS = [program + " {paths}" for program in ("Remove-Item", "ri", "rm", "rmdir", "rd", "del", "erase")]
MUTATIONS += ["Remove-Item -Path {paths}", "Remove-Item -LiteralPath {paths}",
              "Set-Item -Path {paths} -Value 'powershell.exe'", "New-Item -Path {paths} -Value 'powershell.exe'",
              "Set-Item -Value 'powershell.exe' {paths}", "New-Item -Value 'powershell.exe' {paths}",
              "si {paths} -Value 'powershell.exe'", "ni {paths} -Value 'powershell.exe'"]


@pytest.mark.parametrize("targets", MIXED_TARGETS)
@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("major", [5, 7])
def test_mixed_provider_mutations_cannot_supply_stale_compatibility_evidence(monkeypatch, request, targets, mutation, mode, major):
    repair, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "Set-Alias powershell python; function powershell { Write-Output helper }; " \
             + mutation.format(paths=targets) + "; powershell " + mode
    for name in ("gate", "registered-check"):
        with pytest.raises(shell.Refusal):
            shell.launch(name, command=source)
    for host, verdict, tool, field in (("claude", claude.verdict, "PowerShell", "command"),
                                      ("codex", codex.verdict, "functions.exec_command", "cmd")):
        payload = {"tool_name": tool, "tool_input": {field: source}}
        assert repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert verdict(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_parser_accepts_mixed_provider_mutations_without_executing_them():
    sources = [mutation.format(paths=targets) for mutation in MUTATIONS for targets in MIXED_TARGETS]
    rows = native_commands(sources)
    assert all(row["errors"] == 0 for row in rows)


@pytest.mark.parametrize("targets", MIXED_TARGETS)
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_each_affected_function_binding_loses_its_previous_proof(targets, mutation):
    from common.powershell_functions import Functions
    prefix = "function powershell { Write-Output helper }; "
    operation = mutation.format(paths=targets)
    source = prefix + operation + "; powershell"
    functions = Functions(source)
    assert functions.binding("powershell", len(prefix)) == "function"
    functions.define(operation.split()[0].lower(), source[len(prefix) + len(operation.split()[0]):], len(prefix))
    assert functions.binding("powershell", len(source) - 1) in ("ambiguous", "unresolved")
