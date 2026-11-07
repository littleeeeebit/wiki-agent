"""Splat values capture proved bindings at collection construction, not invocation."""

import pytest

from common import shell
from test_shell_binding_order import host_outputs
from test_shell_functions import native
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures
from test_shell_splats import BODY, CALLS, assert_refused

pytestmark = pytest.mark.usefixtures("ps5")
CAPTURES = ["$callback={body}; $options=@{{{key}=$callback}}",
            "$callback={body}; $options=@{{{key}=($callback)}}",
            "$callback=({body}); $options=@{{{key}=$callback}}",
            "$first={body}; $callback=$first; $options=@{{{key}=$callback}}",
            "$callback={body}; $options=@{{{key}=$callback}}; $callback={{Write-Output replaced-fixture}}",
            "$callback={{Write-Output unused-fixture}}; $callback={body}; $options=@{{{key}=$callback}}",
            "$callback={body}; $first=@{{{key}=$callback}}; $options=$first",
            "$callback={body}; $options=@{{{key}=@($callback)}}",
            "$callback={body}; $items=@($callback); $options=@{{{key}=$items}}",
            "$callback={body}; $items=@($callback); $callback={{Write-Output replaced-fixture}}; $options=@{{{key}=$items}}",
            "$callback={body}\n$options=@{{{key}=$callback}}",
            "$callback={body}\r\n$options=@{{{key}=$callback}}",
            "$callback={body}; $options=@{{{key}=$callback}}; $callback=$external",
            "$callback={body}; $items=@($callback); $callback=$external; $options=@{{{key}=$items}}",
            "$callback={body}; $options=@{{{key}=$callback}}; $unrelated=$external"]
POSITIONAL = ["$callback={body}; $options=@($callback)",
              "$callback={body}; $saved=@($callback); $callback={{Write-Output replaced-fixture}}; $options=$saved"]


@pytest.mark.parametrize("capture", [*CAPTURES, *POSITIONAL])
@pytest.mark.parametrize("major", [5, 7])
def test_proved_callback_values_are_compatible_at_both_boundaries(monkeypatch, request, capture, major):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = capture.format(body="{Write-Output captured-fixture}", key="Process") + "; 1 | ForEach-Object @options"
    assert shell.seven_only(source) == ()
    assert shell.classic_seven_only(source) is False
    for name in ("gate", "registered-check"):
        assert shell.resolve(name, command=source) == source
        assert isinstance(shell.launch(name, command=source)[0], list)
    host_outputs(agents, source, source)


@pytest.mark.parametrize("capture,key,call", [(capture, key, call) for index, capture in enumerate(CAPTURES)
                                           for key, call in CALLS if "@($callback)" not in capture or key in ("Process", "RemainingScripts")])
def test_captured_callback_functions_keep_their_original_source_binding(monkeypatch, request, capture, key, call):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = capture.format(body=BODY, key=key) + "; " + call + "; powershell -Command 'Write-Output external-fixture'"
    assert agents[0]._pwsh(source, "ps") == source
    host_outputs(agents, source, source)


@pytest.mark.parametrize("capture", CAPTURES[:7])
@pytest.mark.parametrize("key", ["Parallel", "Para"])
def test_captured_parallel_values_still_require_powershell_seven(request, capture, key):
    agents = request.getfixturevalue("hosts")
    source = capture.format(body="{Write-Output worker-fixture}", key=key) + "; 1 | ForEach-Object @options"
    assert "ForEach-Object -Parallel" in shell.seven_only(source)
    assert_refused(agents, source)


@pytest.mark.parametrize("prefix", ["$callback=$external", "$callback={}; $callback=$external",
                                   "$callback={}; Update-Fixture", "if ($condition) { $callback={} }"])
def test_unproved_callback_values_remain_refused(request, prefix):
    agents = request.getfixturevalue("hosts")
    assert_refused(agents, prefix + "; $options=@{Process=$callback}; 1 | ForEach-Object @options")


@pytest.mark.parametrize("prefix,call,keeps_function", [
    ("$data=1; $options=@{InputObject=$data; Process=$callback}", "ForEach-Object @options", True),
    ("$data=$false; $options=@{Verbose=$data; Process=$callback}", "1 | ForEach-Object @options", True),
    ("$data='-Parallel'; $options=@($data)", "1 | ForEach-Object @options", False),
    ("$options=@{InputObject=$callback}", "ForEach-Object @options -Process {}", False),
    ("$data=@($callback); $options=@{InputObject=$data}", "1 | ForEach-Object @options -Process {}", False),
    ("$options=@{InputObject=$callback; Process={}}", "1 | ForEach-Object @options", True)])
def test_captured_data_values_keep_their_parameter_role(monkeypatch, request, prefix, call, keeps_function):
    agents = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "$callback=" + BODY + "; " + prefix + "; " + call + "; powershell -Command 'Write-Output external-fixture'"
    expected = source if keeps_function else source.rsplit("; powershell", 1)[0] + "; pwsh -NoProfile -Command 'Write-Output external-fixture'"
    assert "ForEach-Object -Parallel" not in shell.seven_only(source)
    assert agents[0]._pwsh(source, "ps") == expected
    host_outputs(agents, source, expected)


@pytest.mark.parametrize("capture", [*CAPTURES, *POSITIONAL])
@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("body,tail,output", [("{Write-Output captured-fixture}", "", ["captured-fixture"]),
                                             (BODY, "; powershell -Command 'Write-Output external-fixture; exit 17'", ["splat-fixture"])])
def test_native_captured_value_fixture_preserves_the_constructed_value(monkeypatch, major, capture, body, tail, output):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = capture.format(body=body, key="Process") + "; 1 | ForEach-Object @options" + tail
    repaired = command_repair._pwsh(source, "ps")
    assert repaired == source
    result = native(repaired, major)
    assert result.returncode == 0 and result.stdout.splitlines() == output and result.stderr == ""
