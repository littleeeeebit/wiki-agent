"""Known variable executables remain usable; unexpected hook faults pass at entry."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from common import shell
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")


@pytest.mark.parametrize("program,args", [("python", "outside/check.py"), ("outside/python.exe", "outside/check.py"),
                                         ("pwsh", "-Command 'Write-Output ok'"), ("powershell", "-Command 'Write-Output ok'")])
@pytest.mark.parametrize("target", ["$runner", "($runner)", '"$runner"'])
@pytest.mark.parametrize("major", [5, 7])
def test_known_compatible_variable_targets_preserve_commands_and_host_acceptance(monkeypatch, hosts, program, args, target, major):  # noqa: F811 -- imported pytest fixture
    repair, claude, codex = hosts
    command = f"$runner = '{program}'; & {target} {args}"
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    for name in ("gate", "registered-check"):
        assert shell.resolve(name, command=command) == command
        assert shell.launch(name, command=command)[0][0] == "fixture-shell"
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    for host, verdict in (("claude", claude.verdict), ("codex", codex.verdict)):
        assert repair.refusal(payload, host) is None
        answer = verdict(payload) or {}
        output = answer.get("hookSpecificOutput", {})
        assert output.get("permissionDecision") != "deny"
        assert output.get("updatedInput", payload["tool_input"])["command"] == command


@pytest.mark.parametrize("binding", ["$runner='python'", "$original='python'; $runner=$original", "$runner='pwsh'; $runner='python'",
                                    "$runner=('python')", "[string]$runner='python'", "$runner='python' # note\n"])
def test_straight_line_literal_bindings_resolve_without_rewriting(binding):
    command = binding + "; & $runner outside/check.py"
    assert shell.resolve("gate", command=command) == command
    assert any(target.program == "python" and target.kind == "bound" for target in shell.invocations(command))


@pytest.mark.parametrize("body", ["Write-Output ok", "Get-Error"])
@pytest.mark.parametrize("major", [5, 7])
def test_resolved_classic_variable_child_uses_its_own_capabilities(monkeypatch, body, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "$runner='powershell'; & $runner -Command '" + body + "'"
    assert list(shell.nested_commands(command)) == [("powershell", body)]
    if body == "Get-Error":
        with pytest.raises(ValueError, match="PowerShell 7 설치"):
            shell.launch("gate", command=command)
    else:
        assert shell.resolve("gate", command=command) == command


@pytest.mark.parametrize("statement", ["$runner=$external", "if ($condition) { $runner='powershell' }", "$runner += 'x'",
                                     "& { $runner='powershell' }", "Invoke-Fixture"])
def test_uncertain_mutation_invalidates_literal_target_knowledge(statement):
    command = "$runner='python'; " + statement + "; & $runner outside/check.py"
    with pytest.raises(ValueError, match="리터럴 실행 대상"):
        shell.resolve("gate", command=command)


def test_variable_held_literal_scriptblock_remains_usable():
    command = "$runner={ Write-Output ok }; & $runner"
    assert shell.resolve("gate", command=command) == command


@pytest.mark.parametrize("binding", ["[string]$runner={ 'powershell' }", "$runner={ powershell @args }"])
def test_variable_resolution_does_not_guess_casts_or_forwarded_arguments(binding):
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=binding + "; & $runner -Command 'Get-Error'")


def test_partially_executed_chain_cannot_reuse_a_stale_binding(monkeypatch):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("pwsh", "7.5", 7),))
    command = "$runner='python'; $runner='powershell' && & $runner -Command 'Get-Error'"
    with pytest.raises(shell.Refusal):
        shell.launch("gate", command=command)


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("error", ["RuntimeError", "ValueError"])
def test_unexpected_analysis_faults_pass_at_real_hook_entry(host, error):
    root = Path(__file__).parent
    script = f"""
import os, runpy, sys
from types import SimpleNamespace
sys.path.insert(0, {str(root)!r})
from common import shell
import command_repair, guard
shell.os = command_repair.os = guard.os = SimpleNamespace(**{{**vars(os), 'name':'nt'}})
shell._detected = (shell.PowerShell('fixture-shell','5.1',5),)
def unavailable(*args, **kwargs):
    raise {error}('fixture-private-detail')
shell.invocations = unavailable
runpy.run_path({str(root / (host + '_pretool.py'))!r}, run_name='__main__')
"""
    payload = {"tool_name": "PowerShell", "tool_input": {"command": "& $runner outside/check.py"}}
    done = subprocess.run([sys.executable, "-c", script], input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", timeout=20)
    assert done.returncode == 0
    assert done.stdout == ""
    assert done.stderr.strip() == error


@pytest.mark.skipif(os.name != "nt", reason="Windows variable-selected registered-check fixture")
@pytest.mark.parametrize("consumer", ["gate", "registered-check", "server-gate", "refactor-runner"])
def test_known_variable_fixture_checks_execute_and_preserve_failure(tmp_path, monkeypatch, consumer):
    import shutil
    from test_shell_review import fixture_result
    executable = shutil.which("powershell")
    if not executable:
        pytest.skip("Windows PowerShell is not installed")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell(executable, "5.1", 5),))
    fixture = tmp_path / "check.py"
    fixture.write_text("print('fixture-ran')\nraise SystemExit(17)\n", encoding="utf-8")
    command = "$runner=" + shell.quote(sys.executable, "ps5") + "; & $runner " + shell.quote(str(fixture), "ps5")
    code, output = fixture_result(command, consumer, tmp_path)
    assert (code, output.splitlines()) == (17, ["fixture-ran"])
