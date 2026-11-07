"""A readable command name does not establish an external executable binding."""

import json
import os
import shutil
import subprocess

import pytest

from common import shell
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")
BODY = "param($Command) Write-Output helper"
PROVIDERS = ["Set-Item Function:powershell { " + BODY + " }",
             "New-Item -Path Function:powershell -Value { " + BODY + " }",
             "si Function:powershell { " + BODY + " }", "ni Function:powershell -Value { " + BODY + " }",
             "New-Item -Path Function: -Name powershell -Value { " + BODY + " }",
             r"Set-Item -LiteralPath 'Function:\powershell' -Value { " + BODY + " }",
             "$function:powershell = { " + BODY + " }", "${function:powershell} = { " + BODY + " }"]
DEFINITIONS = [header + " { " + BODY + " }" for header in (
    "function powershell", "filter powershell", "FuNcTiOn PoWeRsHeLl", "function local:powershell",
    "function script:powershell", "function global:powershell", "function private:powershell")] + PROVIDERS


def native(source, major):
    executable = shutil.which("pwsh" if major == 7 else "powershell")
    if os.name != "nt" or not executable:
        pytest.skip("The selected native PowerShell is not installed")
    return subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", source],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("definition", DEFINITIONS)
def test_native_function_experiment_preserves_binding_output_and_status(monkeypatch, major, definition):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = definition + "; [Console]::WriteLine('version:' + $PSVersionTable.PSVersion); " \
             "[Console]::WriteLine('binding:' + (Get-Command powershell).CommandType); " \
             "powershell -Command 'Write-Output external-child; exit 17'"
    original = native(source, major)
    repaired = native(command_repair._pwsh(source, "ps"), major)
    assert original.returncode == 0 and original.stderr == ""
    assert "binding:Function" in original.stdout or "binding:Filter" in original.stdout
    assert "helper" in original.stdout.splitlines() and "external-child" not in original.stdout
    assert (repaired.returncode, repaired.stdout, repaired.stderr) == (original.returncode, original.stdout, original.stderr)


@pytest.mark.parametrize("major", [5, 7])
def test_native_parser_records_function_declarations_and_real_command_spans(major):
    source = "function powershell { " + BODY + " }; powershell"
    parser = r"""
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput(SOURCE, [ref]$tokens, [ref]$errors)
$functions=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $true) |
    ForEach-Object { @{name=$_.Name; filter=$_.IsFilter; start=$_.Extent.StartOffset; end=$_.Extent.EndOffset;
                       bodyStart=$_.Body.Extent.StartOffset; bodyEnd=$_.Body.Extent.EndOffset} })
$commands=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst]}, $true) |
    ForEach-Object { @{name=$_.GetCommandName(); start=$_.CommandElements[0].Extent.StartOffset; end=$_.CommandElements[0].Extent.EndOffset} })
@{version=$PSVersionTable.PSVersion.ToString(); errors=@($errors).Count; functions=$functions; commands=$commands} | ConvertTo-Json -Compress -Depth 5
""".replace("SOURCE", shell.quote(source, "ps5"))
    result = native(parser, major)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)
    assert int(row["version"].split(".")[0]) == major
    assert row["errors"] == 0
    assert row["functions"] == [{"name": "powershell", "filter": False, "start": 0, "end": source.index("}") + 1,
                                  "bodyStart": source.index("{"), "bodyEnd": source.index("}") + 1}]
    assert row["commands"][-1] == {"name": "powershell", "start": source.rindex("powershell"), "end": len(source)}


@pytest.mark.parametrize("definition", DEFINITIONS)
@pytest.mark.parametrize("call", ["powershell", "& 'powershell'", '& "powershell"', "& ('powershell')",
                                 "$runner='powershell'; & $runner", "Set-Alias helper powershell; & helper"])
@pytest.mark.parametrize("major", [5, 7])
def test_known_functions_remain_calls_through_both_host_outputs(monkeypatch, request, definition, call, major):
    repair, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = definition + "; " + call + " -Command 'Get-Error'"
    assert repair._pwsh(source, "ps") == source
    assert list(shell.nested_commands(source)) == []
    assert shell.resolve("gate", command=source) == source
    assert list(shell.invocations(source))[-1].kind == "function"
    for verdict, tool, field in ((claude.verdict, "PowerShell", "command"), (codex.verdict, "functions.exec_command", "cmd")):
        payload = {"tool_name": tool, "tool_input": {field: source}}
        output = (verdict(payload) or {}).get("hookSpecificOutput", {})
        assert output.get("permissionDecision") != "deny"
        assert output.get("updatedInput", payload["tool_input"])[field] == source


@pytest.mark.parametrize("major", [5, 7])
def test_reported_no_argument_function_call_is_never_modernized(monkeypatch, major):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "function powershell { Write-Output helper }; powershell"
    assert command_repair._pwsh(source, "ps") == source


@pytest.mark.parametrize("setup", [
    "function powershell { " + BODY + " }; Set-Alias powershell python",
    "function powershell { " + BODY + " }; Set-Alias powershell python; Remove-Item Alias:powershell",
    ". { function powershell { " + BODY + " } }",
])
def test_alias_precedence_removal_and_dot_sourcing_preserve_function_behavior(monkeypatch, setup):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = setup + "; powershell -Command 'Get-Error'"
    assert command_repair._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    assert list(shell.nested_commands(source)) == []


@pytest.mark.parametrize("source", [
    "powershell -Command 'Write-Output ok'; function powershell { " + BODY + " }",
    "function install { function powershell { " + BODY + " } }; powershell -Command 'Write-Output ok'",
    "& { function powershell { " + BODY + " } }; powershell -Command 'Write-Output ok'",
    "function powershell { " + BODY + " }; Remove-Item Function:powershell; powershell -Command 'Write-Output ok'",
    "function powershell { " + BODY + " }; Clear-Item Function:powershell; powershell -Command 'Write-Output ok'",
])
def test_unbound_or_removed_function_names_still_modernize_external_calls(monkeypatch, source):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    result = command_repair._pwsh(source, "ps")
    assert result != source
    assert "pwsh -NoProfile -Command" in result
    assert list(shell.nested_commands(result)) == [("pwsh", "Write-Output ok")]


@pytest.mark.parametrize("setup", ["if ($condition) { function powershell { " + BODY + " } }",
                                   "function install { function powershell { " + BODY + " } }; install",
                                   "function global:install { function global:powershell { " + BODY + " } }; install",
                                   "Set-Item Function:powershell $external", "$function:powershell = $external"])
def test_uncertain_function_bindings_suppress_replacement_and_check_possible_children(monkeypatch, setup):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = setup + "; powershell -Command 'Get-Error'"
    assert command_repair._pwsh(source, "ps") == source
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=source)


@pytest.mark.parametrize("destination", ["function pwsh { " + BODY + " }", "Set-Alias pwsh python",
                                        "Set-Item Function:pwsh { " + BODY + " }"])
def test_shadowed_replacement_destination_never_changes_the_selected_callable(monkeypatch, destination):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = destination + "; powershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=source.replace("Write-Output ok", "Get-Error"))


@pytest.mark.parametrize("major", [5, 7])
def test_real_external_children_inside_functions_are_still_inspected(monkeypatch, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "function powershell { & 'outside/powershell.exe' -Command 'Get-Error' }; powershell"
    assert list(shell.nested_commands(source)) == [("powershell", "Get-Error")]
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=source)


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("setup,binding,marker", [
    ("function install { function powershell { " + BODY + " } }", "Application", "external-child"),
    ("& { function powershell { " + BODY + " } }", "Application", "external-child"),
    (". { function powershell { " + BODY + " } }", "Function", "helper"),
    ("if ($true) { function powershell { " + BODY + " } }", "Function", "helper"),
    ("function powershell { " + BODY + " }; Remove-Item Function:powershell", "Application", "external-child"),
    ("function powershell { " + BODY + " }; Set-Alias powershell python; Remove-Item Alias:powershell", "Function", "helper"),
    ("function pwsh { " + BODY + " }", "Application", "external-child"),
    ("Set-Alias pwsh python", "Application", "external-child"),
    ("function powershell { " + BODY + " }; Clear-Item Function:powershell", "Application", "external-child"),
    ("function ri { Write-Output helper }; function powershell { " + BODY + " }; ri Function:powershell", "Application", "external-child"),
])
def test_native_scope_and_mutation_fixtures_preserve_command_identity(monkeypatch, major, setup, binding, marker):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = setup + "; [Console]::WriteLine('binding:' + (Get-Command powershell).CommandType); " \
             "powershell -NoProfile -Command 'Write-Output external-child; exit 17'"
    original = native(source, major)
    repaired = native(command_repair._pwsh(source, "ps"), major)
    assert "binding:" + binding in original.stdout.splitlines()
    if marker is None:
        assert "helper" not in original.stdout and "external-child" not in original.stdout
    else:
        assert marker in original.stdout.splitlines()
    assert (repaired.returncode, repaired.stdout, repaired.stderr) == (original.returncode, original.stdout, original.stderr)


def test_a_function_removing_its_own_binding_cannot_hide_a_real_child():
    source = "function powershell { Remove-Item Function:powershell; powershell -Command 'Get-Error' }; powershell"
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=source)


@pytest.mark.parametrize("source", ["Write-Output 'function powershell { Write-Output helper }'",
                                   "# function powershell { Write-Output helper }\npowershell -Command 'Write-Output ok'",
                                   "Set-Item outside/target.txt -Value 'Function:powershell'; powershell -Command 'Write-Output ok'"])
def test_function_spelling_in_data_never_creates_a_binding(monkeypatch, source):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    result = command_repair._pwsh(source, "ps")
    assert result == source if source.startswith("Write-Output") else "pwsh -NoProfile -Command" in result


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("name,call,kind", [("powershell.exe", "powershell.exe", "function"),
                                           ("powershell", "powershell.exe", "literal"),
                                           ("powershell.exe", "powershell", "literal")])
def test_executable_suffixes_remain_part_of_function_identity(monkeypatch, major, name, call, kind):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "function " + name + " { " + BODY + " }; " + call + " -Command 'Write-Output ok'"
    assert list(shell.invocations(source))[-1].kind == kind
    result = command_repair._pwsh(source, "ps")
    assert (result == source) == (kind == "function" or major == 5)


@pytest.mark.parametrize("major", [5, 7])
def test_native_executable_suffix_function_keeps_its_own_binding(monkeypatch, major):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "function powershell.exe { " + BODY + " }; powershell.exe -Command 'Write-Output external-child; exit 17'"
    result = native(command_repair._pwsh(source, "ps"), major)
    assert (result.returncode, result.stdout.splitlines(), result.stderr) == (0, ["helper"], "")


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("definition", ["Set-Item Function:powershell $external", "$function:powershell = $external"])
def test_unknown_function_bodies_are_not_treated_as_empty_compatible_commands(monkeypatch, definition, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=definition + "; powershell")


@pytest.mark.parametrize("remover", ["Remove-Item", "ri", "rm", "rmdir", "rd", "del", "erase", "Clear-Item", "cli"])
def test_builtin_alias_precedence_keeps_function_removal_visible(monkeypatch, remover):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "function " + remover + " { Write-Output helper }; function powershell { " + BODY + " }; " \
             + remover + " Function:powershell; powershell -Command 'Write-Output ok'"
    # The full cmdlet names are intentionally functions; only built-in aliases bypass those names.
    if remover in ("Remove-Item", "Clear-Item"):
        assert command_repair._pwsh(source, "ps") == source
    else:
        assert "pwsh -NoProfile -Command" in command_repair._pwsh(source, "ps")


def test_removing_a_builtin_alias_reveals_a_same_named_function():
    source = "function ri { " + BODY + " }; Remove-Item Alias:ri; ri -Command 'Get-Error'"
    assert list(shell.invocations(source))[-1].kind == "function"
    assert shell.resolve("gate", command=source) == source


@pytest.mark.parametrize("major", [5, 7])
def test_native_builtin_alias_metadata_precedes_same_named_functions(major):
    from common.powershell_aliases import DEFAULTS
    declarations = "; ".join("function " + name + " { Write-Output helper }" for name in DEFAULTS)
    names = ",".join(shell.quote(name, "ps5") for name in DEFAULTS)
    result = native(declarations + "; @(" + names + ") | ForEach-Object { $c=Get-Command $_; "
                    "[Console]::WriteLine($c.Name.ToLower() + ':' + $c.CommandType + ':' + $c.Definition.ToLower()) }", major)
    assert result.returncode == 0 and result.stderr == ""
    assert sorted(result.stdout.splitlines()) == sorted(name + ":Alias:" + target for name, target in DEFAULTS.items())


def test_a_failed_new_alias_cannot_override_a_builtin_binding():
    source = "function powershell { " + BODY + " }; New-Alias ri python; ri Function:powershell; powershell -Command 'Get-Error'"
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=source)
