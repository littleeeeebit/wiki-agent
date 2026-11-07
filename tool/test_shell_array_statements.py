"""Proved array statements compose through splats without losing roles or source spans."""

import pytest

from common import shell
from common.powershell_splats import parameters
from test_shell_binding_order import host_outputs
from test_shell_functions import native
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures
from test_shell_splats import assert_refused

pytestmark = pytest.mark.usefixtures("ps5")
SEPARATORS = [",", ";", "\n", "\r\n"]
VALUES = ["1,2", "1;2", "1\n2", "1\r\n2", "1,\n2;\n3", "1, # fixture\n2;\n# fixture\n3",
          "'first;second';'last,field'", "", "1", "@(1;2);3"]
EXPECTED = ["1,2", "1,2", "1,2", "1,2", "1,2,3", "1,2,3", "first;second,last,field", "", "1", "1,2,3"]
CONTEXTS = [("$options=@{{InputObject=@({values}); Process={body}}}; ForEach-Object @options", "$_"),
            ("$items=@({values}); $options=@{{InputObject=$items; Process={body}}}; ForEach-Object @options", "$_"),
            ("$saved=@{{InputObject=@({values}); Process={body}}}; $options=$saved; ForEach-Object @options", "$_"),
            ("$items=@({values}); $data=@{{Items=$items}}; $options=@{{InputObject=$data; Process={body}}}; ForEach-Object @options", "$_.Items")]
CALLBACKS = ["$options=@{{Process={array}}}; 1 | ForEach-Object @options",
             "$items={array}; $options=@{{Process=$items}}; 1 | ForEach-Object @options",
             "$options={array}; 1 | ForEach-Object @options"]


def callback_source(separator, context):
    first = "{ function global:powershell { param($Command) Write-Output selected-fixture }; Write-Output phase-first }"
    second = "{ powershell -Command 'Write-Output external-fixture'; Write-Output phase-last }"
    return context.format(array="@(" + first + separator + second + ")")


def native_pair(source, major):
    import command_repair
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    original, updated = native(source, major), native(repaired, major)
    assert (original.returncode, original.stdout, original.stderr) == (updated.returncode, updated.stdout, updated.stderr)
    return updated


@pytest.mark.parametrize("separator", SEPARATORS)
@pytest.mark.parametrize("major", [5, 7])
def test_reported_array_forms_are_accepted_by_every_inspection_boundary(monkeypatch, request, separator, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "$options=@{InputObject=@(1" + separator + "2); Process={Write-Output ok}}; ForEach-Object @options"
    position = source.index("ForEach-Object")
    checks = {"parameters": lambda: parameters(source, position, "options"),
              "features": lambda: shell.seven_only(source), "classic": lambda: shell.classic_seven_only(source),
              "repair": lambda: agents[0]._pwsh(source, "ps")}
    for name in ("gate", "registered-check"):
        for form in ("ps5", "pwsh7"):
            checks[name + ":" + form] = lambda name=name, form=form: shell.resolve(name, form=form, command=source)
        checks[name + ":launch"] = lambda name=name: shell.launch(name, command=source)
    observations = {}
    for label, check in checks.items():
        try:
            check()
            observations[label] = "accepted"
        except shell.Refusal:
            observations[label] = "refused"
    for agent, tool, field in ((agents[1], "PowerShell", "command"), (agents[2], "functions.exec_command", "cmd")):
        output = (agent.verdict({"tool_name": tool, "tool_input": {field: source}}) or {}).get("hookSpecificOutput", {})
        observations[tool] = output.get("permissionDecision", "accepted")
    assert set(observations.values()) == {"accepted"}, observations


@pytest.mark.parametrize("values", VALUES)
@pytest.mark.parametrize("context,expression", CONTEXTS)
def test_literal_array_statements_compose_through_collections(request, values, context, expression):
    agents = request.getfixturevalue("hosts")
    source = context.format(values=values, body="{Write-Output array-fixture}")
    for name in ("gate", "registered-check"):
        for form in ("ps5", "pwsh7"):
            assert shell.resolve(name, form=form, command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("major", [5, 7])
def test_native_parser_distinguishes_array_statements_from_comma_elements(major):
    rows = native_commands(["@(1" + separator + "2)" for separator in SEPARATORS], major=major)
    assert all(row["errors"] == 0 for row in rows)
    assert [row["arrays"][0]["statements"] for row in rows] == [1, 2, 2, 2]
    assert [[node["elements"] for node in row["arrayLiterals"]] for row in rows] == [[2], [], [], []]


@pytest.mark.parametrize("context,expression", CONTEXTS)
@pytest.mark.parametrize("values,expected", list(zip(VALUES, EXPECTED)))
@pytest.mark.parametrize("major", [5, 7])
def test_native_array_value_fixture_keeps_collected_statement_output(monkeypatch, context, expression, values, expected, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = context.format(values=values, body="{Write-Output (" + expression + " -join ',')}")
    result = native_pair(source, major)
    assert result.returncode == 0 and result.stdout == expected + "\n" and result.stderr == ""


@pytest.mark.parametrize("separator", SEPARATORS)
@pytest.mark.parametrize("context", CALLBACKS)
def test_callback_statement_arrays_keep_binding_spans(monkeypatch, request, separator, context):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = callback_source(separator, context)
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("separator", SEPARATORS)
@pytest.mark.parametrize("context", CALLBACKS)
@pytest.mark.parametrize("major", [5, 7])
def test_native_callback_array_fixture_preserves_phase_order_and_binding(monkeypatch, separator, context, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    result = native_pair(callback_source(separator, context), major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["phase-first", "selected-fixture", "phase-last"] and result.stderr == ""


@pytest.mark.parametrize("separator", SEPARATORS)
def test_maps_collected_by_array_statements_keep_nested_blocks_as_data(monkeypatch, request, separator):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    maps = "@{Process={function global:powershell {Write-Output data-fixture}}}" + separator + "@{Parallel={Write-Output worker-fixture}}"
    prefix = "$data=@{Items=@(" + maps + ")}; $options=@{InputObject=$data; Process={}}; ForEach-Object @options"
    source = prefix + "; powershell -Command 'Write-Output external-fixture'"
    expected = prefix + "; pwsh -NoProfile -Command 'Write-Output external-fixture'"
    assert shell.seven_only(source) == ()
    assert agents[0]._pwsh(source, "ps") == expected
    host_outputs(agents, source, expected)


@pytest.mark.parametrize("values", ["1; $external", "1; Update-Fixture", "1; $(Get-Fixture)", "1; $value=2"])
def test_unproved_array_statements_remain_refused(request, values):
    agents = request.getfixturevalue("hosts")
    source = CONTEXTS[0][0].format(values=values, body="{}")
    assert_refused(agents, source)
