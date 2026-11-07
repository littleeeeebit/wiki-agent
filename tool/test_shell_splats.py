"""Literal splats retain parameter roles and capability requirements at both boundaries."""

import base64

import pytest

from common import shell
from test_shell_binding_order import host_outputs
from test_shell_functions import native
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")
MAPS = ["$options=@{{{key}={body}}}", "$OPTIONS=@{{{key}={body}}}",
        "$options=@{{'{key}'={body}}}", '$options=@{{"{key}"={body}}}',
        "$options=([ordered]@{{{key}={body}}})", "$first=@{{{key}={body}}}; $options=$first",
        "$options=@{{{key}={body}; Verbose=$false}}", "$options=@{{\n# fixture\n{key}={body}\n}}"]
CALLS = [("Process", "1 | ForEach-Object @options"), ("Process", "1 | % @options"),
         ("Process", "1 | foreach @options"), ("Begin", "1 | ForEach-Object -Process {} @options"),
         ("End", "1 | ForEach-Object -Process {} @options"),
         ("RemainingScripts", "1 | ForEach-Object -Process {} @options"),
         ("FilterScript", "1 | Where-Object @options | Out-Null"),
         ("Process", "1 | & 'ForEach-Object' @options"),
         ("Process", "& 'ForEach-Object' -InputObject 1 @options"),
         ("Process", r"1 | Microsoft.PowerShell.Core\ForEach-Object @options")]
BODY = "{ function global:powershell { param($Command) Write-Output splat-fixture } }"


def assert_refused(agents, source):
    for name in ("gate", "registered-check"):
        with pytest.raises(shell.Refusal):
            shell.resolve(name, command=source)
        with pytest.raises(shell.Refusal):
            shell.launch(name, command=source)
    host_outputs(agents, source, source, denied=True)


@pytest.mark.parametrize("binding", MAPS)
@pytest.mark.parametrize("key,call", CALLS)
@pytest.mark.parametrize("major", [5, 7])
def test_splatted_callbacks_preserve_the_selected_function(monkeypatch, request, binding, key, call, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = binding.format(key=key, body=BODY) + "; " + call + "; powershell -Command 'Write-Output external-fixture'"
    assert agents[0]._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("binding", MAPS)
@pytest.mark.parametrize("program", ["ForEach-Object", "%", "foreach"])
@pytest.mark.parametrize("key", ["Parallel", "Para"])
def test_splatted_parallel_is_denied_before_a_ps5_launch(request, binding, program, key):
    agents = request.getfixturevalue("hosts")
    source = binding.format(key=key, body="{ Write-Output worker-fixture }") + "; 1 | " + program + " @options"
    assert "ForEach-Object -Parallel" in shell.seven_only(source)
    assert_refused(agents, source)


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("encoded", [False, True])
def test_splatted_parallel_classic_children_are_denied_under_either_parent(monkeypatch, request, major, encoded):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    body = "$options=@{Parallel={Write-Output worker-fixture}}; 1 | ForEach-Object @options"
    argument = "-EncodedCommand " + base64.b64encode(body.encode("utf-16-le")).decode() if encoded else "-Command " + shell.quote(body, "ps5")
    source = "& 'outside/powershell.exe' " + argument
    assert_refused(agents, source)


@pytest.mark.parametrize("binding", ["$options=$external", "$options=@{Process={}}; $options.Process=$external",
                                    "$options=@{Process={}}; Update-Fixture", "if ($condition) { $options=@{Process={}} }"])
@pytest.mark.parametrize("major", [5, 7])
def test_unproved_splats_cannot_claim_compatible_callback_parameters(monkeypatch, request, binding, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = binding + "; 1 | ForEach-Object @options"
    assert_refused(agents, source)


@pytest.mark.parametrize("source", ["$options=@{Process={}}; 1 | ForEach-Object @script:options",
                                  "$options=@{Process={}}; Update-Fixture | ForEach-Object @options",
                                  "$options=@{Process=$external}; 1 | ForEach-Object @options",
                                  "$options=@{Process={}}; 1 | ForEach-Object @options -Process {}"])
def test_uncertain_or_conflicting_splat_ownership_is_refused(request, source):
    agents = request.getfixturevalue("hosts")
    assert_refused(agents, source)


@pytest.mark.parametrize("collection", [BODY, "@(" + BODY + ")"])
def test_positional_splats_preserve_callback_roles(monkeypatch, request, collection):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "$options=" + collection + "; 1 | ForEach-Object @options; powershell -Command 'Write-Output external-fixture'"
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("prefix,call", [("$options=@{InputObject=" + BODY + "}", "ForEach-Object @options -Process {}"),
                                      ("$options=@('-Parallel')", "1 | ForEach-Object @options"),
                                      ("$options='-Parallel'", "1 | ForEach-Object @options"),
                                      ("$options=@{Parallel=" + BODY + "}", "Write-Output @options | Out-Null"),
                                      ("$options=@{Parallel={}}; $options=@{Process={}}", "1 | ForEach-Object @options")])
def test_splatted_data_and_replaced_maps_do_not_create_capabilities_or_functions(monkeypatch, request, prefix, call):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = prefix + "; " + call + "; powershell -Command 'Write-Output external-fixture'"
    expected = source.rsplit("; powershell", 1)[0] + "; pwsh -NoProfile -Command 'Write-Output external-fixture'"
    assert "ForEach-Object -Parallel" not in shell.seven_only(source)
    assert agents[0]._pwsh(source, "ps") == expected
    host_outputs(agents, source, expected)


def test_splatted_parallel_workers_do_not_bind_the_callers_function(monkeypatch, request):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "$options=@{Parallel=" + BODY + "}; 1 | ForEach-Object @options; powershell -Command 'Write-Output external-fixture'"
    expected = source.rsplit("; powershell", 1)[0] + "; pwsh -NoProfile -Command 'Write-Output external-fixture'"
    assert "ForEach-Object -Parallel" in shell.seven_only(source)
    assert agents[0]._pwsh(source, "ps") == expected
    host_outputs(agents, source, expected)


def test_known_functions_own_their_splatted_parallel_parameter(request):
    agents = request.getfixturevalue("hosts")
    source = "function ForEach-Object { param($Parallel) Write-Output fixture }; " \
             "$options=@{Parallel={}}; ForEach-Object @options"
    assert "ForEach-Object -Parallel" not in shell.seven_only(source)
    assert shell.resolve("gate", command=source) == source
    host_outputs(agents, source, source)


def test_splat_values_keep_deferred_binding_checks_inside_their_original_spans(monkeypatch, request):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "$options=@{Process={ powershell -Command 'Write-Output external-fixture'; " + \
             "function global:powershell { param($Command) Write-Output splat-fixture } }}; 1,2 | ForEach-Object @options"
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("binding", [*MAPS, "$options={body}", "$options=@({body})"])
def test_native_splatted_callback_fixture_keeps_its_output(monkeypatch, major, binding):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = binding.format(key="Process", body=BODY) + "; 1 | ForEach-Object @options; " \
             "powershell -Command 'Write-Output external-fixture; exit 17'"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["splat-fixture"] and result.stderr == ""


@pytest.mark.parametrize("major", [5, 7])
def test_native_repeated_splat_fixture_preserves_each_iterations_callable(monkeypatch, major):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "$options=@{Process={ powershell -Command 'Write-Output first-fixture; exit 17'; " \
             "function global:powershell { param($Command) Write-Output second-fixture } }}; " \
             "1,2 | ForEach-Object @options; exit 0"
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == ["first-fixture", "second-fixture"] and result.stderr == ""


def test_native_parser_accepts_the_splat_matrix_without_executing_it():
    sources = [binding.format(key=key, body=BODY) + "; " + call for binding in MAPS for key, call in CALLS]
    assert all(row["errors"] == 0 for row in native_commands(sources))
