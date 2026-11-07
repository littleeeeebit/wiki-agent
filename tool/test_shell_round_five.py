"""Assignment contexts agree with native command ASTs and preserve finding provenance."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from common import shell
from test_shell_round_two import ps5  # noqa: F401 -- shared pytest fixture

pytestmark = pytest.mark.usefixtures("ps5")
CONTEXTS = [
    "{child}", "$result = {child}", "$result={child}", "[string]$result = {child}",
    "$first = $second = {child}", "$result += {child}", "({child})", "$({child})",
    "& {{ {child} }}", "$result = ({child})", "$result = $({child})", "$result = & {{ {child} }}",
    "$result = if ($true) {{ {child} }}",
    "$result[0] = {child}", "$result.Value = {child}", "$script:result = {child}",
    "return {child}", "throw {child}", "exit {child}", "foreach ($item in {child}) {{}}",
    "$map = @{{ value = {child} }}", '$map = @{{ "value" = {child} }}',
]
TARGETS = ["powershell", '& ("outside/powershell.exe")']
MODES = ["-Command 'Get-Error'", "'-Command' 'Write-Output ok;' 'Get-Error'",
         "'-EncodedCommand' '" + base64.b64encode("Get-Error".encode("utf-16-le")).decode() + "'"]
DATA = [
    "Write-Output x=powershell -Command 'Get-Error'",
    "Write-Output x= powershell -Command 'Get-Error'",
    "Write-Output $result=powershell -Command 'Get-Error'",
    "$result = 'powershell -Command Get-Error'",
    '$result = "outside/powershell.exe"',
    "# powershell -Command 'Get-Error'\nWrite-Output ok",
    "& { Write-Output x= powershell -Command 'Get-Error' }",
    "$map = @{ value = Write-Output x= powershell -Command 'Get-Error' }",
    "Write-Output foreach in powershell -Command 'Get-Error'",
]


def native_commands(scripts, major=5):
    executable = shutil.which("pwsh" if major == 7 else "powershell")
    if os.name != "nt" or not executable:
        pytest.skip("Windows PowerShell AST differential oracle")
    script = r"""
    $scripts = ConvertFrom-Json ([Console]::In.ReadToEnd())
    $rows = @(foreach ($source in $scripts) {
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
        $commands = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
        $targets = @(foreach ($command in $commands) {
            $target = $command.CommandElements[0]
            while ($target -is [System.Management.Automation.Language.ParenExpressionAst] -and
                   $target.Pipeline.PipelineElements.Count -eq 1 -and
                   $target.Pipeline.PipelineElements[0] -is [System.Management.Automation.Language.CommandExpressionAst]) {
                $target = $target.Pipeline.PipelineElements[0].Expression
            }
            if ($target -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $target.Value }
        })
        $records = @(foreach ($command in $commands) {
            $target = $command.CommandElements[0]
            [PSCustomObject]@{ name = $command.GetCommandName(); kind = $target.GetType().Name;
                start = $target.Extent.StartOffset; end = $target.Extent.EndOffset; text = $target.Extent.Text }
        })
        $pairs = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.HashtableAst] }, $true) |
            ForEach-Object { foreach ($pair in $_.KeyValuePairs) {
                [PSCustomObject]@{ keyStart = $pair.Item1.Extent.StartOffset; keyEnd = $pair.Item1.Extent.EndOffset;
                    valueStart = $pair.Item2.Extent.StartOffset; valueEnd = $pair.Item2.Extent.EndOffset }
            } })
        $arrays = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.ArrayExpressionAst] }, $true) |
            ForEach-Object { [PSCustomObject]@{ start = $_.Extent.StartOffset; end = $_.Extent.EndOffset;
                statements = @($_.SubExpression.Statements).Count } })
        $arrayLiterals = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.ArrayLiteralAst] }, $true) |
            ForEach-Object { [PSCustomObject]@{ start = $_.Extent.StartOffset; end = $_.Extent.EndOffset;
                elements = @($_.Elements).Count } })
        [PSCustomObject]@{ errors = @($errors).Count; targets = $targets; commands = $records; pairs = $pairs;
            arrays = $arrays; arrayLiterals = $arrayLiterals; version = $PSVersionTable.PSVersion.ToString() }
    })
    ConvertTo-Json -InputObject $rows -Compress -Depth 5
    """
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", script],
                          input=json.dumps(scripts), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_native_parser_confirms_the_context_matrix_without_execution():
    positives = [context.format(child=target + " " + mode) for context in CONTEXTS for target in TARGETS for mode in MODES]
    rows = native_commands([*positives, *DATA])
    assert all(row["errors"] == 0 for row in rows)
    assert all(any("powershell" in target.lower() for target in row["targets"]) for row in rows[:len(positives)])
    assert all(not any("powershell" in target.lower() for target in row["targets"]) for row in rows[len(positives):])


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell assignment execution experiment")
def test_assignment_and_standalone_fixture_children_preserve_output_and_status():
    executable = shutil.which("powershell")
    if not executable:
        pytest.skip("Windows PowerShell is not installed")
    child = "& '" + executable.replace("'", "''") + "' -NoProfile -NonInteractive -Command 'Write-Output fixture-child; exit 17'"
    observations = []
    for source in (child, "$result = " + child + "; Write-Output $result"):
        done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", source + "; exit $LASTEXITCODE"],
                              capture_output=True, text=True, encoding="utf-8", timeout=20)
        observations.append((done.returncode, done.stdout.splitlines(), done.stderr))
    assert observations == [(17, ["fixture-child"], "")] * 2


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("major", [5, 7])
def test_literal_children_have_context_independent_capability_decisions(monkeypatch, context, target, mode, major):
    import command_repair
    import claude_pretool
    import codex_pretool

    command = context.format(child=target + " " + mode)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    monkeypatch.setattr(command_repair, "os", shell.os)
    monkeypatch.setattr(claude_pretool, "check", lambda *_: None)
    monkeypatch.setattr(codex_pretool, "checked", lambda *_: None)
    body = "Write-Output ok; Get-Error" if "Write-Output" in mode else "Get-Error"
    assert list(shell.nested_commands(command)) == [("powershell", body)]
    assert "Get-Error" in shell.seven_only(command)
    assert shell.classic_seven_only(command)
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.launch("gate", command=command)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    if major == 7 and target == "powershell":
        repaired = command_repair._pwsh(command, "ps")
        assert list(shell.nested_commands(repaired)) == [("pwsh", body)]
    for host in ("claude", "codex"):
        assert command_repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"
    for verdict in (claude_pretool.verdict, codex_pretool.verdict):
        answer = verdict(payload) or {}
        output = answer.get("hookSpecificOutput", {})
        if output.get("permissionDecision") != "deny":
            executed = output.get("updatedInput", {}).get("command", command)
            assert major == 7
            assert not shell.classic_seven_only(executed)
            assert list(shell.nested_commands(executed)) == [("pwsh", body)]


@pytest.mark.parametrize("command", DATA)
def test_noncommand_equals_and_literal_shell_data_remain_unchanged(monkeypatch, command):
    import command_repair

    assert list(shell.nested_commands(command)) == []
    monkeypatch.setattr(command_repair, "os", shell.os)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("pwsh", "7.5", 7),))
    assert command_repair._pwsh(command, "ps") == command


@pytest.mark.parametrize("finding,invariant", [("F4", "catalogue coverage"), ("F6", "child-shell capability enforcement")])
def test_research_identity_matches_its_declared_invariant(finding, invariant):
    filename = f"review-powershell-version-resolver-and-install-{finding.lower()}-1.md"
    text = (Path(__file__).parents[1] / "docs/research" / filename).read_text(encoding="utf-8")
    assert text.startswith(f"# Review research — {finding}, cycle 1")
    assert f"Finding: {finding}. Invariant: {invariant}." in text
    problem = text.split("## Problem\n", 1)[1].split("\n## ", 1)[0]
    assert invariant in problem
    assert text.splitlines()[2].startswith("Draft research")
