"""Nested literal maps remain data while compatible splatted commands pass."""

import pytest

from common import shell
from test_shell_binding_order import host_outputs
from test_shell_functions import native
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures
from test_shell_splats import assert_refused

pytestmark = pytest.mark.usefixtures("ps5")
DATA = [("", "@{Name='data-fixture'}", ""),
        ("", "(@{Name='data-fixture'})", ""),
        ("$data=@{Name='data-fixture'}; ", "$data", ""),
        ("$first=@{Name='data-fixture'}; $data=$first; ", "$data", ""),
        ("$data=[ordered]@{Name='data-fixture'}; ", "$data", ""),
        ("", "[ordered]@{Name='data-fixture'}", ""),
        ("", "@{Name='data-fixture'; Child=@{Name='child-fixture'}}", ""),
        ("$name='data-fixture'; $data=@{Name=$name}; $name='replaced-fixture'; ", "$data", ""),
        ("", "@{Name='data-fixture'; Items=@(@{Name='child-fixture'})}", ""),
        ("", "@{Name='data-fixture'; 'startup.rc'='value'; Parallel={Write-Output worker-fixture}}", ""),
        ("", "@{Name='data-fixture'; Process={function global:powershell { param($Command) Write-Output data-fixture }}}", ""),
        ("$data=@{Name='data-fixture'}; ", "$data", "$data=@{Name='replaced-fixture'}; ")]


@pytest.mark.parametrize("setup,data,after", DATA)
@pytest.mark.parametrize("major", [5, 7])
def test_literal_map_values_are_compatible_at_both_runner_and_hook_boundaries(monkeypatch, request, setup, data, after, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = setup + "$options=@{InputObject=" + data + "; Process={Write-Output map-fixture}}; " + after + "ForEach-Object @options"
    assert shell.seven_only(source) == ()
    for name in ("gate", "registered-check"):
        assert shell.resolve(name, command=source) == source
        assert isinstance(shell.launch(name, command=source)[0], list)
    host_outputs(agents, source, source)


@pytest.mark.parametrize("setup,data,after", DATA)
def test_nested_map_fields_do_not_become_callbacks_or_parallel_parameters(monkeypatch, request, setup, data, after):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    prefix = setup + "$options=@{InputObject=" + data + "; Process={}}; " + after + "ForEach-Object @options"
    source = prefix + "; powershell -Command 'Write-Output external-fixture'"
    expected = prefix + "; pwsh -NoProfile -Command 'Write-Output external-fixture'"
    assert shell.seven_only(source) == ()
    assert agents[0]._pwsh(source, "ps") == expected
    host_outputs(agents, source, expected)


@pytest.mark.parametrize("data", ["@{Name=$external}", "@{Name=(Get-Fixture)}", "@{$external='value'}",
                                 "@{Child=@{Name=$external}}", "@{Items=@(@{Name=$external})}"])
def test_unproved_nested_map_entries_remain_refused(request, data):
    agents = request.getfixturevalue("hosts")
    assert_refused(agents, "$options=@{InputObject=" + data + "; Process={}}; ForEach-Object @options")


@pytest.mark.parametrize("data", ["@{}", "[ordered]@{}", "@(@{},[ordered]@{})"])
def test_empty_and_array_wrapped_maps_remain_valid_data(request, data):
    agents = request.getfixturevalue("hosts")
    source = "$options=@{InputObject=" + data + "; Process={Write-Output empty-fixture}}; ForEach-Object @options"
    assert shell.resolve("gate", command=source) == source
    assert shell.resolve("registered-check", command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("setup,data,after", DATA)
@pytest.mark.parametrize("major", [5, 7])
def test_native_literal_map_fixture_keeps_the_input_value(monkeypatch, setup, data, after, major):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = setup + "$options=@{InputObject=" + data + "; Process={Write-Output $_.Name}}; " + after + "ForEach-Object @options"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["data-fixture"] and result.stderr == ""
