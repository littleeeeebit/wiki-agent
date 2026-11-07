"""Callback effects and loop labels do not change the selected callable during repair."""

import pytest

from common import shell
from test_shell_binding_order import host_outputs
from test_shell_functions import BODY, native
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")
CALLBACKS = ["1 | ForEach-Object {{ {definition} }}", "1 | % {{ {definition} }}", "1 | foreach {{ {definition} }}",
             "ForEach-Object -InputObject 1 -Process {{ {definition} }}",
             "1 | ForEach-Object -Begin {{ {definition} }} -Process {{}}",
             "1 | ForEach-Object -Process {{}} -End {{ {definition} }}",
             "1 | Where-Object {{ {definition}; $true }} | Out-Null", "1 | ? {{ {definition}; $true }} | Out-Null",
             "Set-Alias visit ForEach-Object; 1 | visit {{ {definition} }}",
             "1 | & 'ForEach-Object' {{ {definition} }}",
             r"1 | Microsoft.PowerShell.Core\ForEach-Object {{ {definition} }}",
             "& foreach -InputObject 1 {{ {definition} }}",
             "ForEach-Object -InputObject 1 -Process ({{ {definition} }})",
             "ForEach-Object -InputObject 1 -Process @({{ {definition} }})",
             "ForEach-Object -InputObject 1 -Process {{ {definition} }},{{}}",
             "ForEach-Object -InputObject 1 -Process {{}} -RemainingScripts {{ {definition} }}",
             "1 | ForEach-Object {{}} , {{ {definition} }}",
             "1 | ForEach-Object -vb {{ {definition} }}",
             "1 | ForEach-Object -Verbo {{ {definition} }}",
             "1 | ForEach-Object -InputObject {{ {definition}; 1 }} -Process {{}}",
             "1 | Where-Object -InputObject {{ {definition}; 1 }} -FilterScript {{$false}} | Out-Null"]
DEFINITIONS = ["function global:powershell { " + BODY + " }", "filter global:powershell { " + BODY + " }",
               "function script:powershell { " + BODY + " }", "function powershell { " + BODY + " }",
               "Set-Item Function:global:powershell { " + BODY + " }"]
LABELED = [":outer foreach ($value in 1,2) {{ {call} }}; {load}",
           ":outer for ($index=0; $index -lt 2; $index++) {{ {call} }}; {load}",
           "$index=0; :outer while ($index++ -lt 2) {{ {call} }}; {load}",
           "$index=0; :outer do {{ {call}; $index++ }} while ($index -lt 2); {load}",
           "$index=0; :outer do {{ {call}; $index++ }} until ($index -ge 2); {load}",
           ":outer switch (1,2) {{ default {{ {call} }} }}; {load}",
           "& {{ :stage_one foreach ($value in 1,2) {{ {call} }} }}; {load}",
           ":OUTER\nforeach ($value in 1,2) {{ {call} }}; {load}",
           "$index=0;:outer while ($index++ -lt 2) {{ {call} }}; {load}",
           "& {{:inner foreach ($value in 1,2) {{ {call} }} }}; {load}"]
DATA_INPUTS = ["ForEach-Object -InputObject {data} -Process {{}}",
               "ForEach-Object -Process {{}} -InputObject {data}",
               "ForEach-Object -InputObject ({data}) -Process {{}}",
               "ForEach-Object -InputObject @({data}) -Process {{}}",
               "ForEach-Object -InputObject:({data}) -Process {{}}",
               "ForEach-Object -Inp {data} -Proc {{}}", "ForEach-Object -InputObject {data} {{}}",
               "Where-Object -InputObject {data} -FilterScript {{$false}} | Out-Null",
               "1 | ForEach-Object -InputObject @({data}) -Process {{}}",
               "1 | Where-Object -InputObject @({data}) -FilterScript {{$false}} | Out-Null"]
NESTED_INPUTS = ["(helper -Parallel)", "$(helper -Parallel)", "@(helper -Parallel)",
                 "([object](helper -Parallel))", "(helper -Parallel:$true)", "((helper -Parallel))",
                 '"fixture $(helper -Parallel)"']


@pytest.mark.parametrize("context", DATA_INPUTS)
@pytest.mark.parametrize("major", [5, 7])
def test_input_values_do_not_define_callback_bindings(monkeypatch, request, context, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    prefix = context.format(data="{ " + DEFINITIONS[0] + " }")
    call = "powershell -Command 'Write-Output child'"
    source = prefix + "; " + call
    expected = prefix + "; " + (call.replace("powershell", "pwsh -NoProfile", 1) if major == 7 else call)
    assert list(shell.invocations(source))[-1].kind == "literal"
    assert agents[0]._pwsh(source, "ps") == expected
    assert shell.resolve("gate", command=expected) == expected
    host_outputs(agents, source, expected)
    incompatible = prefix + "; powershell -Command 'Get-Error'"
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=incompatible)


@pytest.mark.parametrize("value", NESTED_INPUTS)
@pytest.mark.parametrize("program", ["ForEach-Object", "%", "foreach"])
@pytest.mark.parametrize("major", [5, 7])
def test_nested_parameters_do_not_change_the_outer_callback_role(monkeypatch, request, value, program, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "function helper { param([switch]$Parallel) 1 }; " + program + " -InputObject " + value + \
             " -Process { " + DEFINITIONS[0] + " }; powershell -Command 'Write-Output child'"
    assert "ForEach-Object -Parallel" not in shell.seven_only(source)
    assert agents[0]._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("context", DATA_INPUTS)
def test_native_input_data_fixture_runs_the_selected_external_child(monkeypatch, major, context):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    data = "{ function global:powershell { param($Command) Write-Output data-fixture } }"
    source = "$ErrorActionPreference='Stop'; try { " + context.format(data=data) + " } catch { " \
             "if ($_.FullyQualifiedErrorId -notmatch '^(ScriptBlockArgumentNoInput|InputObjectNotBound),') { throw } }; " \
             "powershell -Command 'Write-Output $PSVersionTable.PSVersion.Major; exit 17'; exit $LASTEXITCODE"
    result = native(command_repair._pwsh(source, "ps"), major)
    assert result.returncode == 17 and result.stdout.splitlines() == ["7"] and result.stderr == ""


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("value", NESTED_INPUTS)
def test_native_nested_parameter_fixture_keeps_the_callback_function(monkeypatch, major, value):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "function helper { param([switch]$Parallel) 1 }; ForEach-Object -InputObject " + value + \
             " -Process { function global:powershell { param($Command) Write-Output ownership-fixture } }; " \
             "powershell -Command 'Write-Output external-fixture; exit 17'"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["ownership-fixture"] and result.stderr == ""


@pytest.mark.parametrize("callback", CALLBACKS)
@pytest.mark.parametrize("definition", DEFINITIONS)
@pytest.mark.parametrize("major", [5, 7])
def test_possible_callback_definitions_are_not_discarded(monkeypatch, request, callback, definition, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = callback.format(definition=definition) + "; powershell -Command 'Write-Output child'"
    assert agents[0]._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("callback", CALLBACKS)
def test_callback_execution_is_not_assumed_when_a_classic_child_is_possible(monkeypatch, request, callback):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = callback.format(definition=DEFINITIONS[0]) + "; powershell -Command 'Get-Error'"
    assert agents[0]._pwsh(source, "ps") == source
    for name in ("gate", "registered-check"):
        with pytest.raises(shell.Refusal):
            shell.launch(name, command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("callback", CALLBACKS)
def test_completed_callbacks_keep_the_binding_before_a_later_load(monkeypatch, request, callback):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    call = "powershell -Command 'Get-Error'"
    source = callback.format(definition=call) + "; . ./startup.ps1"
    expected = callback.format(definition=call.replace("powershell", "pwsh -NoProfile", 1)) + "; . ./startup.ps1"
    assert agents[0]._pwsh(source, "ps") == expected
    host_outputs(agents, source, expected)


@pytest.mark.parametrize("context", ["1,2 | ForEach-Object {{ {call}; {load} }}",
                                   "1 | ForEach-Object -Begin {{ {load} }} -Process {{ {call} }}",
                                   "1 | ForEach-Object -Process {{ {call} }} -Begin {{ {load} }}"])
def test_callback_iterations_and_phases_cannot_reuse_stale_bindings(monkeypatch, request, context):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = context.format(call="powershell -Command 'Get-Error'", load=". ./startup.ps1")
    assert agents[0]._pwsh(source, "ps") == source
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("context", LABELED[:6])
def test_labeled_iterations_still_check_possible_classic_children(monkeypatch, request, context):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = context.replace("; {load}", "").format(call="powershell -Command 'Get-Error'; . ./startup.ps1")
    assert agents[0]._pwsh(source, "ps") == source
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("context", ["1 | ForEach-Object -Begin {{ {definition} }} -Process {{ {call} }}",
                                   "1 | ForEach-Object -Process {{ {call} }} -Begin {{ {definition} }}"])
def test_callback_phase_definitions_are_not_bound_by_source_order(monkeypatch, request, context):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = context.format(definition=DEFINITIONS[0], call="powershell -Command 'Write-Output child'")
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("context", LABELED)
@pytest.mark.parametrize("load", [". ./startup.ps1", "Invoke-Expression 'Write-Output ok'"])
def test_labels_preserve_completed_control_flow_order(monkeypatch, request, context, load):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    call = "powershell -Command 'Get-Error'"
    source = context.format(call=call, load=load)
    expected = context.format(call=call.replace("powershell", "pwsh -NoProfile", 1), load=load)
    assert agents[0]._pwsh(source, "ps") == expected
    assert shell.resolve("gate", command=expected) == expected
    host_outputs(agents, source, expected)


@pytest.mark.parametrize("label", [":outer ", ":stage_one\n", "Write-Output marker;:outer "])
@pytest.mark.parametrize("major", [5, 7])
def test_labeled_iterator_children_cannot_bypass_capability_checks(monkeypatch, request, label, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = label + "foreach ($value in & 'outside/powershell.exe' -Command 'Get-Error') {}"
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("prefix", ["$stored={ function global:powershell { " + BODY + " } }",
                                    "Write-Output { function global:powershell { " + BODY + " } }",
                                    "Set-Alias ForEach-Object Write-Output; 1 | ForEach-Object { function global:powershell { " + BODY + " } }",
                                    "1 | ForEach-Object -Parallel { function global:powershell { " + BODY + " } }"])
def test_data_and_worker_definitions_do_not_become_caller_bindings(monkeypatch, prefix):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = prefix + "; powershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps").endswith("; pwsh -NoProfile -Command 'Write-Output ok'")


@pytest.mark.parametrize("program", ["ForEach-Object", "foreach", "%"])
@pytest.mark.parametrize("flag", ["-Parallel", "-Para"])
def test_resolved_parallel_callbacks_are_refused_on_ps5(monkeypatch, request, program, flag):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "5.1", 5),))
    source = "1 | " + program + " " + flag + " { Write-Output worker-fixture }"
    assert "ForEach-Object -Parallel" in shell.seven_only(source)
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("prefix", [". ./startup.ps1; ",
                                   "if ($condition) { function ForEach-Object { param($Parallel) } }; "])
def test_possible_parallel_cmdlets_remain_refused_on_ps5(request, prefix):
    agents = request.getfixturevalue("hosts")
    source = prefix + "1 | ForEach-Object -Parallel { Write-Output worker-fixture }"
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=source)
    host_outputs(agents, source, source, denied=True)


def test_a_known_function_owns_its_parallel_parameter(request):
    agents = request.getfixturevalue("hosts")
    source = "function ForEach-Object { param($Parallel) Write-Output fixture }; ForEach-Object -Parallel { Write-Output worker-fixture }"
    assert "ForEach-Object -Parallel" not in shell.seven_only(source)
    assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("source", ["Write-Output :outer powershell '-Command' 'Write-Output ok'", "Write-Output :outer foreach"])
def test_label_like_arguments_do_not_create_command_positions(monkeypatch, source):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    assert command_repair._pwsh(source, "ps") == source


@pytest.mark.parametrize("target", ["powershell", "Write-Output", "Register-ObjectEvent"])
def test_language_foreach_does_not_invoke_a_same_named_alias(monkeypatch, request, target):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "Set-Item -LiteralPath Alias:foreach -Value " + target + " -Force; " \
             "foreach ($value in 1) { " + DEFINITIONS[0] + " }; powershell -Command 'Write-Output child'"
    assert agents[0]._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("name", ["foreach", "%", "where", "?"])
@pytest.mark.parametrize("mutation", ["Set-Alias {name} Write-Output", "Set-Alias {name} Write-Output -Force",
                                    "Remove-Item -LiteralPath 'Alias:{name}'", "Set-Item -LiteralPath 'Alias:{name}' Write-Output"])
@pytest.mark.parametrize("major", [5, 7])
def test_failed_readonly_alias_mutations_cannot_hide_callback_effects(monkeypatch, request, name, mutation, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    callback = "1 | " + name + " { " + DEFINITIONS[0] + ("; $true } | Out-Null" if name in ("where", "?") else " }")
    source = mutation.format(name=name) + "; " + callback + "; powershell -Command 'Write-Output child'"
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


def test_native_parser_accepts_callback_and_label_tables_without_execution():
    sources = [callback.format(definition=definition) for callback in CALLBACKS for definition in DEFINITIONS]
    sources += [context.format(call="powershell", load=". ./startup.ps1") for context in LABELED]
    sources += [label + "foreach ($value in & 'outside/powershell.exe' -Command 'Get-Error') {}"
                for label in (":outer ", ":stage_one\n", "Write-Output marker;:outer ")]
    sources += [context.format(data="{ " + DEFINITIONS[0] + " }") for context in DATA_INPUTS]
    sources += ["ForEach-Object -InputObject " + value + " -Process {}" for value in NESTED_INPUTS]
    assert all(row["errors"] == 0 for row in native_commands(sources))


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("callback", CALLBACKS)
def test_native_callback_fixture_keeps_the_function_selected(monkeypatch, major, callback):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    definition = "function global:powershell { param($Command) Write-Output callback-fixture }"
    source = callback.format(definition=definition) + "; powershell -Command 'Write-Output external-fixture; exit 17'"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["callback-fixture"] and result.stderr == ""


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("context", [LABELED[index] for index in (0, 4, 5)])
def test_native_labeled_fixture_runs_the_selected_child(tmp_path, monkeypatch, major, context):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    path = tmp_path / "startup.ps1"
    path.write_text("", encoding="utf-8")
    call = "powershell -NoProfile -Command 'Write-Output label-fixture; exit 17'"
    load = ". " + shell.quote(str(path), "ps5")
    source = context.format(call=call, load=load) + "; exit $LASTEXITCODE"
    expected = context.format(call=call.replace("powershell", "pwsh", 1), load=load) + "; exit $LASTEXITCODE"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == expected
    result = native(repaired, major)
    assert result.returncode == 17 and result.stdout.splitlines() == ["label-fixture", "label-fixture"] and result.stderr == ""


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("mutation", ["Set-Alias % Write-Output", "Set-Alias % Write-Output -Force", "Remove-Item Alias:%", "Set-Item Alias:% Write-Output"])
def test_native_readonly_failure_fixture_preserves_the_callback_function(monkeypatch, major, mutation):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "$ErrorActionPreference='SilentlyContinue'; " + mutation + "; 1 | % { function global:powershell { " \
             "param($Command) Write-Output readonly-fixture } }; powershell -Command 'Write-Output external-fixture; exit 17'"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["readonly-fixture"] and result.stderr == ""


@pytest.mark.parametrize("major", [5, 7])
def test_native_provider_reset_fixture_tracks_subsequent_alias_writes(monkeypatch, major):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "Set-Item -LiteralPath Alias:% -Value Write-Output -Force; Set-Alias % ForEach-Object; " \
             "1 | % { function global:powershell { param($Command) Write-Output reset-fixture } }; " \
             "powershell -Command 'Write-Output external-fixture; exit 17'"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["reset-fixture"] and result.stderr == ""


@pytest.mark.parametrize("major", [5, 7])
def test_native_builtin_removal_fixture_exposes_context_dependent_lookup(major):
    source = "& { function ri { Write-Output scope-fixture }; Remove-Item Alias:ri -Force; " \
             "[Console]::WriteLine((Get-Command ri).CommandType) }"
    result = native(source, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["Alias" if major == 5 else "Function"] and result.stderr == ""
