"""Grouping preserves comma-expression values and the statement collection boundary."""

import pytest

from common import shell
from common.powershell_splats import parameters
from common.powershell_syntax import bound_span
from test_shell_array_statements import CALLBACKS, callback_source
from test_shell_binding_order import host_outputs
from test_shell_functions import native
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures
from test_shell_splats import assert_refused

pytestmark = pytest.mark.usefixtures("ps5")
ARRAYS = [("@(1,2)", "@(0,0)"), ("@((1,2))", "@(0,0)"), ("@(((1,2)))", "@(0,0)"),
          ("@(@(1,2))", "@(0,0)"), ("@((1,2);3)", "@(0,0,0)"),
          ("@((1,2),3)", "@(@(0,0),0)"), ("@(@(1,2),3)", "@(@(0,0),0)"),
          ("@((1,(2,3)))", "@(0,@(0,0))"), ("@((,1))", "@(0)"),
          ("@(,(1,2))", "@(@(0,0))"), ("@(1,,2)", "@(0,@(0))"),
          ("@(,,1)", "@(@(0))"), ("@((,@()))", "@(@())")]
CONTEXTS = [("$options=@{{InputObject={array}; Process={body}}}; ForEach-Object @options", "$_"),
            ("$items={array}; $options=@{{InputObject=$items; Process={body}}}; ForEach-Object @options", "$_"),
            ("$saved=@{{InputObject={array}; Process={body}}}; $options=$saved; ForEach-Object @options", "$_"),
            ("$data=@{{Items={array}}}; $options=@{{InputObject=$data; Process={body}}}; ForEach-Object @options", "$_.Items")]
SHAPE = r"""
function Format-FixtureShape($value) {
    if ($value -is [array]) {
        $items = @(foreach ($item in $value) { Format-FixtureShape $item })
        return '@(' + ($items -join ',') + ')'
    }
    return '0'
}
"""
GROUPS = ["({values})", "(({values}))", "({values});@()"]


def grouped_callback(context, group):
    values = callback_source(",", "{array}")[2:-1]
    return context.format(array="@(" + group.format(values=values) + ")")


@pytest.mark.parametrize("major", [5, 7])
def test_reported_grouped_comma_form_is_compatible_at_every_boundary(monkeypatch, request, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "$options=@{InputObject=@((1,2)); Process={Write-Output ok}}; ForEach-Object @options"
    assert bound_span(source[:source.index("ForEach-Object")] + " & ", "options") is not None
    values = parameters(source, source.index("ForEach-Object"), "options")
    assert values[0][3] == "@(0,0)"
    assert shell.seven_only(source) == () and shell.classic_seven_only(source) is False
    for form in ("ps5", "pwsh7"):
        for name in ("gate", "registered-check"):
            assert shell.resolve(name, form=form, command=source) == source
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("array,shape", ARRAYS)
@pytest.mark.parametrize("context,expression", CONTEXTS)
def test_grouped_literal_arrays_compose_through_both_runner_and_hook_boundaries(request, array, shape, context, expression):
    agents = request.getfixturevalue("hosts")
    source = context.format(array=array, body="{Write-Output grouped-fixture}")
    for name in ("gate", "registered-check"):
        for form in ("ps5", "pwsh7"):
            assert shell.resolve(name, form=form, command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("array,shape", ARRAYS)
def test_positional_grouped_arrays_keep_recursive_element_shapes(array, shape):
    source = "$options=" + array + "; 1 | ForEach-Object @options"
    values = parameters(source, source.index("ForEach-Object"), "options")
    assert "@(" + ",".join(code for _, _, _, code, _ in values) + ")" == shape


@pytest.mark.parametrize("major", [5, 7])
def test_native_grouping_ast_records_distinct_array_and_comma_nesting(major):
    rows = native_commands([*[array for array, _ in ARRAYS], "@((1,))", "@((1,,))", "@((,))"], major)
    assert all(row["errors"] == 0 for row in rows[:len(ARRAYS)])
    assert all(row["errors"] > 0 for row in rows[len(ARRAYS):])
    assert all(row["version"].startswith(str(major) + ".") for row in rows)
    assert [sum(node["kind"] == "ParenExpressionAst" for node in row["grouping"]) for row in rows[:3]] == [0, 1, 2]
    assert rows[5]["arrayLiterals"][0]["elements"] == 2
    assert any(node["kind"] == "ParenExpressionAst" and node["parent"] == "ArrayLiteralAst" for node in rows[5]["grouping"])


@pytest.mark.parametrize("major", [5, 7])
def test_native_grouped_array_fixture_preserves_recursive_values_and_exit_status(major):
    import command_repair
    sources, expected = [], []
    for context, expression in CONTEXTS:
        for array, shape in ARRAYS:
            sources.append(context.format(array=array, body="{Write-Output (Format-FixtureShape " + expression + ")}"))
            expected.append(shape)
    original = SHAPE + "; ".join("& { " + source + " }" for source in sources)
    repaired = SHAPE + "; ".join("& { " + command_repair._pwsh(source, "ps") + " }" for source in sources)
    assert repaired == original
    for source in (original, repaired):
        result = native(source, major)
        assert result.returncode == 0 and result.stdout.splitlines() == expected and result.stderr == ""


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("context", CALLBACKS)
def test_grouped_comma_callbacks_keep_original_spans_and_binding(monkeypatch, request, group, context):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = grouped_callback(context, group)
    values = parameters(source, source.index("ForEach-Object"), "options")
    assert [block for _, _, _, _, blocks in values for block in blocks] == [source.index("{ function global:"), source.index("{ powershell -Command")]
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("major", [5, 7])
def test_native_grouped_callback_fixture_preserves_order_and_selected_function(monkeypatch, major):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    sources = [grouped_callback(context, group) for context in CALLBACKS for group in GROUPS]
    for original in sources:
        repaired = command_repair._pwsh(original, "ps")
        assert repaired == original
        for source in (original, repaired):
            result = native(source, major)
            assert result.returncode == 0 and result.stderr == ""
            assert result.stdout.splitlines() == ["phase-first", "selected-fixture", "phase-last"]


@pytest.mark.parametrize("group", GROUPS)
def test_grouped_map_arrays_keep_nested_blocks_as_input_data(monkeypatch, request, group):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    maps = "@{Process={function global:powershell {Write-Output data-fixture}}},@{Parallel={Write-Output worker-fixture}}"
    array = "@(" + group.format(values=maps) + ")"
    prefix = CONTEXTS[-1][0].format(array=array, body="{}")
    source = prefix + "; powershell -Command 'Write-Output external-fixture'"
    expected = prefix + "; pwsh -NoProfile -Command 'Write-Output external-fixture'"
    assert shell.seven_only(source) == ()
    assert agents[0]._pwsh(source, "ps") == expected
    host_outputs(agents, source, expected)


@pytest.mark.parametrize("value", ["@((1,$external))", "@((1,(Get-Fixture)))", "@((1,))", "@((1,,))", "@((,))"])
def test_unproved_or_malformed_grouped_operands_are_refused(request, value):
    agents = request.getfixturevalue("hosts")
    assert_refused(agents, CONTEXTS[0][0].format(array=value, body="{}"))
