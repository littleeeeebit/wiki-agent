"""Uninspected script loads cannot prove the identity of later command names."""

import os
import shutil
import subprocess

import pytest

from common import shell
from test_shell_round_eight import MODES
from test_shell_round_five import native_commands
from test_shell_round_six import hosts, ps5  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.usefixtures("ps5")
LOADS = [". ./startup.ps1", ". './startup.ps1'", '. "./startup.ps1"', ". ('./startup.ps1')",
         "$startup='./startup.ps1'; . $startup", "& './startup.ps1'", "./startup.ps1",
         "Import-Module ./startup.psm1", "ipmo ./startup.psm1",
         "Set-Alias load Import-Module; load ./startup.psm1",
         "$startup={ function powershell { Write-Output helper } }; . $startup",
         ". ({ function powershell { Write-Output helper } })"]
CALLS = ["powershell", "& 'powershell'", "$runner='powershell'; & $runner", "pwsh"]


@pytest.mark.parametrize("load", LOADS)
@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("major", [5, 7])
def test_opaque_loads_preserve_later_calls_through_both_host_outputs(monkeypatch, request, load, call, major):
    repair, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = load + "; " + call
    assert repair._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    for verdict, tool, field in ((claude.verdict, "PowerShell", "command"), (codex.verdict, "functions.exec_command", "cmd")):
        payload = {"tool_name": tool, "tool_input": {field: source}}
        output = (verdict(payload) or {}).get("hookSpecificOutput", {})
        assert output.get("permissionDecision") != "deny"
        assert output.get("updatedInput", payload["tool_input"])[field] == source


@pytest.mark.parametrize("load", LOADS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("major", [5, 7])
def test_opaque_loaded_aliases_cannot_hide_classic_child_bodies(monkeypatch, request, load, mode, major):
    repair, claude, codex = request.getfixturevalue("hosts")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = "Set-Alias powershell python; Set-Alias helper python; " + load + "; helper " + mode
    for name in ("gate", "registered-check"):
        with pytest.raises(shell.Refusal):
            shell.launch(name, command=source)
    for verdict, tool, field in ((claude.verdict, "PowerShell", "command"), (codex.verdict, "functions.exec_command", "cmd")):
        output = verdict({"tool_name": tool, "tool_input": {field: source}})["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
    assert repair._pwsh(source, "ps") == source


def test_import_syntax_is_parsed_without_loading_any_file():
    rows = native_commands([load + "; powershell" for load in LOADS])
    assert all(row["errors"] == 0 for row in rows)


@pytest.mark.parametrize("load", LOADS)
@pytest.mark.parametrize("major", [5, 7])
def test_literal_compatible_arguments_after_loading_remain_runnable(monkeypatch, load, major):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = load + "; powershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source


@pytest.mark.parametrize("setup", [". ./startup.ps1", "Import-Module ./startup.psm1",
                                   "Set-Alias load Import-Module; load ./startup.psm1"])
def test_deferred_calls_cannot_use_namespace_proofs_from_before_an_import(monkeypatch, setup):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "function probe { powershell -Command 'Write-Output ok' }; " + setup + "; probe"
    assert command_repair._pwsh(source, "ps") == source


@pytest.mark.parametrize("prefix", ["Write-Output '. ./startup.ps1'", "Write-Output 'Import-Module ./startup.psm1'",
                                    "Get-Item ./startup.ps1", "& './outside/helper.exe'",
                                    "function unused { . ./startup.ps1 }",
                                    "function Import-Module { Write-Output ok }; Import-Module ./startup.psm1",
                                    "Set-Alias ipmo Write-Output; ipmo ./startup.psm1"])
def test_data_nonloaders_and_uncalled_bodies_do_not_disable_external_modernization(monkeypatch, prefix):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = prefix + "; powershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps").endswith("; pwsh -NoProfile -Command 'Write-Output ok'")


def test_a_root_call_before_loading_still_uses_its_current_binding(monkeypatch):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = "powershell -Command 'Write-Output ok'; . ./startup.ps1"
    assert command_repair._pwsh(source, "ps").startswith("pwsh -NoProfile -Command")


@pytest.mark.parametrize("major", [5, 7])
def test_explicit_paths_after_imports_keep_their_own_capability_decision(monkeypatch, major):
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    source = ". ./startup.ps1; & 'outside/python.exe' outside/check.py $result"
    assert shell.resolve("gate", command=source) == source
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=". ./startup.ps1; & 'outside/powershell.exe' -Command 'Get-Error'")


@pytest.mark.parametrize("directive", ["using module ./startup.psm1", "#Requires -Modules Startup",
                                      "# comment mentioning <#\n#Requires -Modules Startup",
                                      "#requires -Modules @{ModuleName='Startup';ModuleVersion='1.0'}"])
def test_module_directives_invalidate_namespace_proofs_before_execution(monkeypatch, directive):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = directive + "\npowershell"
    assert command_repair._pwsh(source, "ps") == source
    assert shell.resolve("gate", command=source) == source
    with pytest.raises(shell.Refusal):
        shell.resolve("gate", command=directive + "\nhelper -Command 'Get-Error'")


@pytest.mark.parametrize("data", ["Write-Output '#Requires -Modules Startup'", "<#\n#Requires -Modules Startup\n#>",
                                 "<# outer <# nested #>\n#Requires -Modules Startup\n#>",
                                 "Write-Output @'\n#Requires -Modules Startup\n'@", "# a comment containing <#\nWrite-Output ok"])
def test_requirement_spelling_in_data_and_comments_does_not_import_commands(monkeypatch, data):
    import command_repair
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    source = data + "\npowershell -Command 'Write-Output ok'"
    assert command_repair._pwsh(source, "ps").endswith("pwsh -NoProfile -Command 'Write-Output ok'")


@pytest.mark.skipif(os.name != "nt", reason="Windows imported-function fixture")
@pytest.mark.parametrize("loader", ["dot", "call", "module", "using", "requires"])
@pytest.mark.parametrize("introduced", ["powershell", "pwsh"])
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("major", [5, 7])
def test_native_bounded_import_fixture_preserves_callable_output_and_status(tmp_path, monkeypatch, loader, introduced, deferred, major):
    import command_repair
    executable = shutil.which("pwsh" if major == 7 else "powershell")
    if not executable:
        pytest.skip("The selected native PowerShell is not installed")
    path = tmp_path / ("startup.psm1" if loader in ("module", "using", "requires") else "startup.ps1")
    if loader == "requires":
        path = tmp_path / "Startup" / "Startup.psm1"
        path.parent.mkdir()
    name = "global:" + introduced if loader == "call" else introduced
    body = "function " + name + " { param([switch]$NoProfile, $Command) Write-Output import-fixture }"
    if path.suffix == ".psm1":
        body += "; Export-ModuleMember -Function " + introduced
    path.write_text(body, encoding="utf-8")
    quoted = shell.quote(str(path), "ps5")
    load = {"module": "Import-Module -DisableNameChecking ", "using": "using module ", "requires": "#Requires -Modules ",
            "dot": ". ", "call": "& "}[loader] + quoted
    if loader == "requires":
        load = "#Requires -Modules Startup"
    call = "powershell -NoProfile -Command 'Write-Output external-child; exit 17'"
    before, after = ("function probe { " + call + " }; ", "probe") if deferred else ("", call)
    metadata = "[Console]::WriteLine('binding:' + (Get-Command powershell).CommandType); "
    source = load + "\n" + before + metadata + after if loader in ("using", "requires") else before + load + "; " + metadata + after
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", "7.1", 7),))
    def run(command, label):
        if loader == "requires":
            entry = tmp_path / (label + ".ps1")
            entry.write_text(command, encoding="utf-8")
            invocation = ["-File", str(entry)]
        else:
            invocation = ["-Command", command]
        return subprocess.run([executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *invocation],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                              env={**os.environ, "PSMODULEPATH": str(tmp_path) + os.pathsep + os.environ.get("PSMODULEPATH", "")})
    original, repaired = run(source, "original"), run(command_repair._pwsh(source, "ps"), "repaired")
    binding, marker = ("Function", "import-fixture") if introduced == "powershell" else ("Application", "external-child")
    assert "binding:" + binding in original.stdout.splitlines(), original.stderr
    assert marker in original.stdout.splitlines()
    assert (repaired.returncode, repaired.stdout, repaired.stderr) == (original.returncode, original.stdout, original.stderr)
