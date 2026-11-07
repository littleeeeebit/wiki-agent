"""Callable identity follows resolved bindings and the order in which code runs."""

import pytest

from common import shell
from test_shell_functions import BODY, native
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")
RESOLVED_CALLS = [
    "function helper.ps1 { Write-Output ok }; helper.ps1",
    "filter helper.ps1 { Write-Output ok }; helper.ps1",
    "function helper.ps1 { Write-Output ok }; & 'helper.ps1'",
    "function helper.ps1 { Write-Output ok }; Set-Alias helper helper.ps1; helper",
    "function helper.ps1 { Write-Output ok }; $runner='helper.ps1'; & $runner",
    "Set-Alias helper.ps1 Write-Output; helper.ps1 ok",
    "Set-Alias helper.ps1 powershell; helper.ps1 -Command 'Write-Output ok'",
    "function helper { Write-Output ok }; Set-Alias helper.ps1 helper; helper.ps1",
    "function HeLpEr.PS1 { Write-Output ok }; HeLpEr.PS1",
]
ORDERED = ["& {{ {call} }}; {load}", "{call}; {load}", ". {{ {call} }}; {load}",
           "& {{ . {{ {call} }} }}; {load}", "& {{ {call}; {load} }}", ". {{ {call}; {load} }}"]
COMPLETED = [
    ("foreach ($value in 1,2) {{ {call} }}; {load}", 2),
    ("for ($index=0; $index -lt 2; $index++) {{ {call} }}; {load}", 2),
    ("$index=0; while ($index++ -lt 2) {{ {call} }}; {load}", 2),
    ("$index=0; do {{ {call}; $index++ }} while ($index -lt 2); {load}", 2),
    ("$index=0; do {{ {call}; $index++ }} until ($index -ge 2); {load}", 2),
    ("if ($true) {{ {call} }}; {load}", 1),
    ("if ($false) {{}} elseif ($true) {{ {call} }}; {load}", 1),
    ("if ($false) {{}} else {{ {call} }}; {load}", 1),
    ("try {{ {call} }} finally {{}}; {load}", 1),
    ("try {{}} finally {{ {call} }}; {load}", 1),
    ("try {{ throw 'fixture' }} catch {{ {call} }}; {load}", 1),
    ("try {{ throw 'fixture' }} catch [System.Exception] {{ {call} }}; {load}", 1),
    ("switch (1,2) {{ default {{ {call} }} }}; {load}", 2),
    ("switch -Regex -CaseSensitive ('x') {{ 'x' {{ {call} }} }}; {load}", 1),
    ("switch -File './startup.rc' {{ default {{ {call} }} }}; {load}", 2),
    ("& {{ foreach ($value in 1,2) {{ {call} }} }}; {load}", 2),
    ("foreach ($value in 1,2) {{ & {{ {call} }} }}; {load}", 2),
    ("if ($true) {{ foreach ($value in 1,2) {{ {call} }} }}; {load}", 2),
]
ORDERED += [context for context, _ in COMPLETED]
DEFERRED = ["function probe {{ {call} }}; {load}; probe",
            "function probe {{ & {{ {call} }} }}; {load}; probe",
            "foreach ($value in 1,2) {{ {call}; {load} }}",
            "& {{ foreach ($value in 1,2) {{ {call}; {load} }} }}",
            "$index=0; do {{ {call}; $index++ }} while ($index -lt 2 -and $({load}; $true))",
            "$index=0; do {{ {call}; $index++ }} until ($index -ge 2 -or $({load}; $false))",
            "Register-ObjectEvent ($source) {{ {call} }}; {load}",
            "Write-Output switch ($source) {{ {call} }}; {load}",
            "& if {{ {call} }}; {load}", "Set-Item -LiteralPath Alias:foreach -Value Register-ObjectEvent -Force; & foreach ($source) {{ {call} }}; {load}",
            "Set-Item -LiteralPath Alias:foreach -Value Register-ObjectEvent -Force; $source | foreach {{ {call} }}; {load}",
            "Set-Item -LiteralPath Alias:foreach -Value Register-ObjectEvent -Force; $source | foreach ($filter) {{ {call} }}; {load}",
            "$callback={{ {call} }}; {load}; & $callback"]
BINDINGS = ["$path='Function:powershell'", '$path="Function:powershell"',
            "$base='Function:powershell'; $path=$base", "$path=('Function:powershell')",
            "[string]$path='Function:powershell'"]
MUTATIONS = ["Set-Item $path { " + BODY + " }", "Set-Item -Path $path -Value { " + BODY + " }",
             'Set-Item -LiteralPath "$path" -Value { ' + BODY + " }", "New-Item -Path $path -Value { " + BODY + " }",
             "si $path { " + BODY + " }", "ni $path -Value { " + BODY + " }"]
GENERATORS = ["Invoke-Expression CODE", "iex CODE", "& 'Invoke-Expression' CODE", "Invoke-Expression -Command CODE",
              "Set-Alias evaluate Invoke-Expression; evaluate CODE", "$evaluate='Invoke-Expression'; & $evaluate CODE",
              "$code=CODE; Invoke-Expression $code", "CODE | Invoke-Expression",
              r"Microsoft.PowerShell.Utility\Invoke-Expression CODE"]


def host_outputs(agents, source, expected, denied=False):
    _, claude, codex = agents
    for verdict, tool, field in ((claude.verdict, "PowerShell", "command"), (codex.verdict, "functions.exec_command", "cmd")):
        payload = {"tool_name": tool, "tool_input": {field: source}}
        output = (verdict(payload) or {}).get("hookSpecificOutput", {})
        if denied:
            assert output["permissionDecision"] == "deny"
        else:
            assert output.get("permissionDecision") != "deny"
            assert output.get("updatedInput", payload["tool_input"])[field] == expected


@pytest.mark.parametrize("prefix", RESOLVED_CALLS)
@pytest.mark.parametrize("body", ["Get-Error", "Write-Output ok"])
@pytest.mark.parametrize("major", [5, 7])
def test_resolved_callable_identity_controls_script_load_detection(monkeypatch, request, prefix, body, major):
    agents = request.getfixturevalue("hosts")
    repair = agents[0]
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    call = "powershell -Command '" + body + "'"
    source = prefix + "; " + call
    expected = prefix + "; " + (call.replace("powershell", "pwsh -NoProfile", 1) if major == 7 else call)
    assert repair._pwsh(source, "ps") == expected
    assert list(shell.invocations(source))[-1].kind == "literal"
    host_outputs(agents, source, expected, denied=major == 5 and body == "Get-Error")
    if major == 5 and body == "Get-Error":
        with pytest.raises(shell.Refusal):
            shell.launch("gate", command=source)
    else:
        assert shell.resolve("gate", command=expected) == expected


@pytest.mark.parametrize("context", ORDERED)
@pytest.mark.parametrize("load", [". ./startup.ps1", "Import-Module ./startup.psm1", "Invoke-Expression 'Write-Output ok'"])
@pytest.mark.parametrize("body", ["Get-Error", "Write-Output ok"])
def test_completed_immediate_blocks_keep_the_binding_before_a_later_load(monkeypatch, request, context, load, body):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    call = "powershell -Command '" + body + "'"
    source = context.format(call=call, load=load)
    expected = context.format(call=call.replace("powershell", "pwsh -NoProfile", 1), load=load)
    assert agents[0]._pwsh(source, "ps") == expected
    assert shell.resolve("gate", command=expected) == expected
    host_outputs(agents, source, expected)


@pytest.mark.parametrize("context", DEFERRED)
def test_repeated_and_deferred_blocks_still_check_possible_classic_children(monkeypatch, request, context):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = context.format(call="powershell -Command 'Get-Error'", load=". ./startup.ps1")
    assert agents[0]._pwsh(source, "ps") == source
    for name in ("gate", "registered-check"):
        with pytest.raises(shell.Refusal):
            shell.launch(name, command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("binding", BINDINGS)
@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("major", [5, 7])
def test_literal_bound_provider_paths_preserve_the_created_function(monkeypatch, request, binding, mutation, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = binding + "; " + mutation + "; powershell -Command 'Get-Error'"
    assert agents[0]._pwsh(source, "ps") == source
    assert list(shell.invocations(source))[-1].kind == "function"
    assert list(shell.nested_commands(source)) == []
    assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("operation", ["Remove-Item", "Clear-Item", "ri", "cli"])
def test_bound_provider_removals_restore_the_external_identity(monkeypatch, operation):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "function powershell { " + BODY + " }; $path='Function:powershell'; " + operation + " $path; powershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps").endswith("; pwsh -NoProfile -Command 'Write-Output ok'")


@pytest.mark.parametrize("prefix", ["$path='outside/target.txt'; Set-Item $path value",
                                    "$path='outside/target.txt'; New-Item -Path $path -Value value"])
def test_proved_file_provider_paths_do_not_invent_function_bindings(monkeypatch, prefix):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = prefix + "; powershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps").endswith("; pwsh -NoProfile -Command 'Write-Output ok'")


@pytest.mark.parametrize("setup,command", [("Set-Alias write Set-Item; ", "write"), ("$writer='Set-Item'; ", "& $writer")])
def test_resolved_mutators_share_the_literal_provider_path_proof(monkeypatch, request, setup, command):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = setup + "$path='Function:powershell'; " + command + " $path { " + BODY + " }; powershell -Command 'Get-Error'"
    assert agents[0]._pwsh(source, "ps") == source
    assert list(shell.invocations(source))[-1].kind == "function"
    host_outputs(agents, source, source)


@pytest.mark.parametrize("binding", ["$path=$external", "$path='Function:powershell'; $path=$external",
                                    "if ($condition) { $path='Function:powershell' }"])
def test_unproved_provider_paths_cannot_claim_an_external_callable(monkeypatch, request, binding):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = binding + "; Set-Item $path { " + BODY + " }; powershell -Command 'Get-Error'"
    assert agents[0]._pwsh(source, "ps") == source
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("target", ["./startup.ps1", "'./startup.ps1'"])
def test_aliases_resolved_to_external_scripts_still_invalidate_bindings(monkeypatch, target):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "Set-Alias load " + target + "; load; powershell -Command 'Get-Error'"
    assert command_repair._pwsh(source, "ps") == source
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=source)


def test_native_parser_accepts_the_binding_and_order_tables_without_execution():
    samples = RESOLVED_CALLS + [row.format(call="powershell", load=". ./startup.ps1") for row in ORDERED + DEFERRED]
    samples += [binding + "; " + mutation for binding in BINDINGS for mutation in MUTATIONS]
    assert all(row["errors"] == 0 for row in native_commands(samples))


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("context", ORDERED)
def test_native_ordered_fixture_runs_the_expected_selected_child(tmp_path, monkeypatch, major, context):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    path = tmp_path / "startup.ps1"
    path.write_text("", encoding="utf-8")
    data = tmp_path / "startup.rc"
    data.write_text("first\nsecond\n", encoding="utf-8")
    call = "powershell -NoProfile -Command 'Write-Output order-fixture; exit 17'"
    source = context.format(call=call, load=". " + shell.quote(str(path), "ps5")) + "; exit $LASTEXITCODE"
    expected = context.format(call=call.replace("powershell", "pwsh", 1), load=". " + shell.quote(str(path), "ps5")) + "; exit $LASTEXITCODE"
    source, expected = (value.replace("'./startup.rc'", shell.quote(str(data), "ps5")) for value in (source, expected))
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == expected
    result = native(repaired, major)
    assert result.returncode == 17 and result.stdout.splitlines() == ["order-fixture"] * dict(COMPLETED).get(context, 1) and result.stderr == ""


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_native_bound_provider_fixture_keeps_function_output(monkeypatch, major, mutation):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "$path='Function:powershell'; " + mutation + "; powershell -Command 'Write-Output external-child; exit 17'"
    original, repaired = native(source, major), native(command_repair._pwsh(source, "ps"), major)
    assert original.returncode == 0 and "helper" in original.stdout.splitlines() and original.stderr == ""
    assert "external-child" not in original.stdout.splitlines()
    assert (repaired.returncode, repaired.stdout, repaired.stderr) == (original.returncode, original.stdout, original.stderr)


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("prefix", RESOLVED_CALLS)
def test_native_resolved_suffix_fixture_still_runs_the_selected_child(monkeypatch, major, prefix):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    call = "powershell -NoProfile -Command 'Write-Output suffix-fixture; exit 17'"
    source = prefix + "; " + call + "; exit $LASTEXITCODE"
    expected = prefix + "; " + call.replace("powershell", "pwsh", 1) + "; exit $LASTEXITCODE"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == expected
    result = native(repaired, major)
    assert result.returncode == 17 and "suffix-fixture" in result.stdout.splitlines() and result.stderr == ""


@pytest.mark.parametrize("prefix,target,kind", [
    (". ./startup.ps1; ", "powershell", "imported"),
    ("Invoke-Expression 'function powershell { Write-Output fixture }'; ", "powershell", "imported"),
    ("if ($condition) { function powershell { Write-Output fixture } }; ", "powershell", "ambiguous"),
    ("", "& $target", "unresolved"),
])
@pytest.mark.parametrize("body", ["Write-Output fixture", "Get-Error"])
@pytest.mark.parametrize("major", [5, 7])
def test_callable_identity_states_preserve_compatible_calls_and_reject_unsafe_children(monkeypatch, request, prefix, target, kind, body, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = prefix + target + " -Command '" + body + "'"
    assert list(shell.invocations(source))[-1].kind == kind
    assert agents[0]._pwsh(source, "ps") == source
    denied = kind == "unresolved" or body == "Get-Error"
    for form in ("ps5", "pwsh7"):
        for name in ("gate", "registered-check"):
            if denied:
                with pytest.raises(shell.Refusal):
                    shell.resolve(name, form=form, command=source)
            else:
                assert shell.resolve(name, form=form, command=source) == source
    host_outputs(agents, source, source, denied=denied)


@pytest.mark.parametrize("generator", GENERATORS)
@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("body", ["Write-Output child", "Get-Error"])
def test_generated_bindings_are_preserved_and_possible_classic_children_checked(monkeypatch, request, generator, major, body):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    prefix = generator.replace("CODE", shell.quote("function powershell { " + BODY + " }", "ps5"))
    source = prefix + "; powershell -Command '" + body + "'"
    assert agents[0]._pwsh(source, "ps") == source
    assert list(shell.invocations(source))[-1].kind == "imported"
    if body == "Get-Error":
        for name in ("gate", "registered-check"):
            with pytest.raises(shell.Refusal):
                shell.launch(name, command=source)
    else:
        assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source, denied=body == "Get-Error")


@pytest.mark.parametrize("prefix", ["Write-Output 'Invoke-Expression'", "function unused { Invoke-Expression 'Write-Output ok' }",
                                    "function Invoke-Expression { param($Command) Write-Output ok }; Invoke-Expression 'Write-Output ok'",
                                    "Set-Item -LiteralPath Alias:iex -Value Write-Output -Force; iex 'Write-Output ok'"])
def test_generator_names_in_data_or_unexecuted_code_do_not_change_root_bindings(monkeypatch, prefix):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = prefix + "; powershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps").endswith("; pwsh -NoProfile -Command 'Write-Output ok'")


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("generator", GENERATORS)
def test_native_generated_binding_fixture_preserves_output_and_status(monkeypatch, major, generator):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    definition = "function powershell { param($Command) Write-Output generated-fixture }"
    source = generator.replace("CODE", shell.quote(definition, "ps5")) + "; powershell -Command 'Write-Output external-fixture; exit 17'"
    original, repaired = native(source, major), native(command_repair._pwsh(source, "ps"), major)
    assert original.returncode == 0 and original.stdout.splitlines() == ["generated-fixture"] and original.stderr == ""
    assert (repaired.returncode, repaired.stdout, repaired.stderr) == (original.returncode, original.stdout, original.stderr)
