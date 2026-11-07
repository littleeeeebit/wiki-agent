"""Hashtable roles and unresolved executable targets stay visible at the boundary."""

import base64
import os
import shutil
import subprocess

import pytest

from common import shell
from test_shell_round_five import native_commands
from test_shell_round_two import ps5  # noqa: F401 -- shared pytest fixture

pytestmark = pytest.mark.usefixtures("ps5")
KEYS = ["powershell", "powershell.exe", "pwsh", "PowerShell"]
ENTRIES = ["{key} = 'value'", "first='value'; {key} = 'value'", "first='value'\n# note\n{key} = 'value'"]
MAPS = ["$map = @{{ {entry} }}", "$map = [ordered]@{{ {entry} }}", "$map = @{{ nested=@{{ {entry} }} }}", "& {{ $map = @{{ {entry} }} }}"]
TARGETS = ["$target", "($target)", '"$target"', "(Get-Item outside/target.txt).Name", "$env:TARGET", "$script:target",
           "('power'+'shell')", "$(Out-Null; 'powershell')", "(Get-Item)"]
BINDINGS = ["$target='powershell'; {call}", "$target='pwsh'; $target='powershell'; {call}",
            "if ($condition) {{ $target='powershell' }}; {call}", "& {{ $target=$external; {call} }}", "{call}",
            "$target='pwsh'; & {{ $target='powershell'; {call} }}"]
MODES = ["-Command 'Get-Error'", "'-Command' 'Write-Output ok;' 'Get-Error'",
         "-EncodedCommand '" + base64.b64encode("Get-Error".encode("utf-16-le")).decode() + "'"]
PAIRED = [
    "$map = @{ powershell = powershell -Command 'Write-Output ok' }",
    "$map = @{ (powershell -Command 'Write-Output ok') = 'value' }",
    "$map = @{ powershell = { powershell -Command 'Write-Output ok' } }",
    "$map = @{ powershell = @{ pwsh = powershell -Command 'Write-Output ok' } }",
]


@pytest.fixture
def hosts(monkeypatch):
    import command_repair
    import claude_pretool
    import codex_pretool
    import guard
    monkeypatch.setattr(command_repair, "os", shell.os)
    monkeypatch.setattr(guard, "os", shell.os)
    monkeypatch.setattr(claude_pretool, "check", lambda *_: None)
    monkeypatch.setattr(codex_pretool, "checked", lambda *_: None)
    return command_repair, claude_pretool, codex_pretool


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("container", MAPS)
@pytest.mark.parametrize("major", [5, 7])
def test_hashtable_keys_remain_data_in_discovery_and_actual_host_outputs(monkeypatch, hosts, key, entry, container, major):
    repair, claude, codex = hosts
    command = container.format(entry=entry.format(key=key))
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    assert all(match.kind == "scriptblock" for match in shell.invocations(command))
    assert repair._pwsh(command, "ps") == command
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for verdict in (claude.verdict, codex.verdict):
        answer = verdict(payload) or {}
        given = answer.get("hookSpecificOutput", {}).get("updatedInput", payload["tool_input"])
        assert given["command"] == command


def test_native_oracle_separates_exact_key_and_command_spans():
    controls = [c.format(entry=e.format(key=k)) for c in MAPS for e in ENTRIES for k in KEYS]
    controls += [source.replace("powershell.exe", "'powershell.exe'") for source in controls if "powershell.exe" in source]
    sources = [*controls, *PAIRED]
    rows = native_commands(sources)
    for source, row in zip(sources, rows):
        if row["errors"]:
            # Bare dotted keys are invalid in the native grammar; they remain unchanged inputs.
            assert "powershell.exe" in source and "'powershell.exe'" not in source
            continue
        expected = [(c["start"], c["end"]) for c in row["commands"] if str(c["name"]).lower() in ("powershell", "pwsh")]
        actual = [(match.target_start, match.target_end) for match in shell.invocations(source) if match.program in ("powershell", "pwsh")]
        assert actual == expected
        for start, end in actual:
            assert not any(start == pair["keyStart"] and end == pair["keyEnd"] for pair in row["pairs"])


@pytest.mark.parametrize("major", [5, 7])
def test_key_value_repairs_change_only_native_command_spans_and_still_parse(monkeypatch, hosts, major):
    repair, claude, codex = hosts
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    rows = native_commands(PAIRED)
    outputs = []
    for source, row in zip(PAIRED, rows):
        expected = source
        if major == 7:
            spans = [(c["start"], c["end"]) for c in row["commands"] if str(c["name"]).lower() == "powershell"]
            for start, end in reversed(spans):
                expected = expected[:start] + "pwsh -NoProfile" + expected[end:]
        assert repair._pwsh(source, "ps") == expected
        payload = {"tool_name": "PowerShell", "tool_input": {"command": source}}
        for verdict in (claude.verdict, codex.verdict):
            answer = verdict(payload) or {}
            assert answer.get("hookSpecificOutput", {}).get("updatedInput", payload["tool_input"])["command"] == expected
        outputs.append(expected)
    assert all(row["errors"] == 0 for row in native_commands(outputs))


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("binding", BINDINGS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("major", [5, 7])
def test_unresolved_executable_targets_are_refused_by_every_boundary(monkeypatch, hosts, target, binding, mode, major):
    repair, claude, codex = hosts
    command = binding.format(call="& " + target + " " + mode)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    for check in (lambda: shell.resolve("gate", command=command), lambda: shell.launch("gate", command=command)):
        with pytest.raises(shell.Refusal):
            check()
    for host, verdict, name, field in (("claude", claude.verdict, "PowerShell", "command"),
                                      ("codex", codex.verdict, "functions.exec_command", "cmd")):
        payload = {"tool_name": name, "tool_input": {field: command}}
        assert repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert verdict(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_oracle_retains_unknown_target_kinds_without_execution():
    scripts = ["$target='powershell'; " + operator + " " + target + " -Command 'Get-Error'" for operator in ("&", ".") for target in TARGETS]
    rows = native_commands(scripts)
    assert all(row["errors"] == 0 for row in rows)
    assert all(any(c["name"] is None and c["kind"] != "ScriptBlockExpressionAst" for c in row["commands"]) for row in rows)
    for source, row in zip(scripts, rows):
        expected = [(c["start"], c["end"]) for c in row["commands"] if c["name"] is None]
        actual = [(c.target_start, c.target_end) for c in shell.invocations(source) if c.kind in ("unresolved", "bound")]
        assert actual == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows native variable-target experiment")
@pytest.mark.parametrize("operator", ["&", "."])
def test_native_variable_fixture_preserves_output_and_failure_status(operator):
    executable = shutil.which("powershell")
    if not executable:
        pytest.skip("Windows PowerShell is not installed")
    script = "$target='" + executable.replace("'", "''") + "'; " + operator + " $target -NoProfile -NonInteractive -Command 'Write-Output fixture-child; exit 17'; exit $LASTEXITCODE"
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert (done.returncode, done.stdout.splitlines(), done.stderr) == (17, ["fixture-child"], "")


@pytest.mark.parametrize("major", [5, 7])
def test_repair_failure_fallback_preserves_expected_target_refusal(monkeypatch, hosts, major):
    repair, claude, codex = hosts
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture analysis failure")
    monkeypatch.setattr(repair, "repair", unavailable)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": "& $target -Command 'Get-Error'"}}
    for verdict in (claude.verdict, codex.verdict):
        assert verdict(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["$target='powershell'; Write-Output $target", "Write-Output '& $target -Command Get-Error'",
                                    '& { Write-Output "Get-Error" }', '& "python" -c \'print("fixture")\'',
                                    '. "powershell" -Command \'Write-Output ok\''])
def test_unused_data_and_known_literal_targets_remain_supported(command):
    assert shell.resolve("gate", command=command) == command


@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("mode", ["Command", "EncodedCommand"])
def test_unresolved_child_nested_under_literal_shell_is_also_refused(monkeypatch, hosts, major, mode):
    repair, claude, codex = hosts
    body = '$target="powershell"; & $target -Command "Get-Error"'
    argument = base64.b64encode(body.encode("utf-16-le")).decode() if mode == "EncodedCommand" else "'" + body + "'"
    command = "pwsh -" + mode + " " + argument
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=command)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    assert claude.verdict(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert codex.verdict(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unresolved_documented_input_has_no_executable_form():
    import shell_lint
    from common.shell_catalogue import COMMANDS
    entry = COMMANDS["unresolved-target-example"]
    assert entry.ps5 is None and entry.pwsh7 is None
    assert shell_lint.catalogued(entry.example)
    for form in ("ps5", "pwsh7"):
        with pytest.raises(ValueError, match="리터럴 실행 대상"):
            shell.resolve("unresolved-target-example", form=form)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("major", [5, 7])
def test_dot_invocation_uses_the_same_unresolved_policy(monkeypatch, hosts, target, major):
    repair, claude, codex = hosts
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = ". " + target + " -Command 'Get-Error'"
    with pytest.raises(ValueError, match="리터럴 실행 대상"):
        shell.launch("gate", command=command)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for verdict in (claude.verdict, codex.verdict):
        assert verdict(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"
