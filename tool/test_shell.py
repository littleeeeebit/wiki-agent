"""Version selection and refusal at the actual shell boundary."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from common import shell
from common.shell_catalogue import COMMANDS, Command


def windows(monkeypatch, *modules):
    for module in (shell, *modules):
        monkeypatch.setattr(module, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))


@pytest.mark.parametrize("versions,expected", [
    ({"powershell": "5.1.19041.1"}, "powershell"),
    ({"pwsh": "7.5.3"}, "pwsh"),
    ({"pwsh": "7.5.3", "powershell": "5.1.19041.1"}, "pwsh"),
])
def test_version_is_probed_once_and_the_preferred_executable_is_exposed(monkeypatch, versions, expected):
    calls = []
    monkeypatch.setattr(shell, "_detected", None)
    monkeypatch.setattr(shutil, "which", lambda name: name if name in versions else None)

    def run(argv, **kwargs):
        assert argv[-1] == "$PSVersionTable.PSVersion.ToString()"
        assert kwargs["timeout"] == 10
        calls.append(argv[0])
        return subprocess.CompletedProcess(argv, 0, versions[argv[0]], "")

    monkeypatch.setattr(subprocess, "run", run)
    assert shell.chosen().executable == expected
    assert shell.chosen().version == versions[expected]
    assert shell.chosen().major == int(versions[expected][0])
    assert len(shell.detect()) == len(versions)
    assert calls == [name for name in ("pwsh", "powershell") if name in versions]
    if os.name == "nt":
        argv, use_shell = shell.launch("git-status")
        assert argv[0] == expected and not use_shell
        assert shell.chosen().form == ("pwsh7" if expected == "pwsh" else "ps5")


@pytest.mark.parametrize("failure", ["wrong-version", "exit-failure", "timeout", "missing"])
def test_a_name_or_failed_probe_is_not_a_version(monkeypatch, failure):
    monkeypatch.setattr(shell, "_detected", None)
    monkeypatch.setattr(shutil, "which", lambda name: name if failure != "missing" else None)

    def run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        return subprocess.CompletedProcess(argv, int(failure == "exit-failure"), "6.0.0", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.chosen()


@pytest.mark.parametrize("line,feature", [
    ("${x}?.Name", "?."), ("$x ?? 'fallback'", "??"), ("$x ? 1 : 0", "ternary"),
    ("1 | ForEach-Object -Parallel { $_ }", "ForEach-Object -Parallel"),
    ("'{}' | Test-Json", "Test-Json"), ("Get-Error", "Get-Error"),
    ("Invoke-WebRequest https://example.test -SkipCertificateCheck", "-SkipCertificateCheck"),
    ("Set-Content result.txt text", "UTF-8 default encoding"),
    ("Write-Output text > result.txt", "UTF-8 default encoding"),
    ('Write-Output "tag#$(Get-Error)"', "Get-Error"),
    ('Set-Content result.txt "-Encoding utf8"', "UTF-8 default encoding"),
    ("Get-Content -Raw result.txt", "UTF-8 default encoding"),
])
def test_ps5_never_receives_a_seven_only_form(monkeypatch, line, feature):
    windows(monkeypatch)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))
    assert feature in shell.seven_only(line)
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.launch("gate", command=line)
    monkeypatch.setitem(COMMANDS, "fixture-seven", Command("echo fixture", line, None, (feature,)))
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.launch("fixture-seven")


def test_launch_and_hook_installation_take_the_same_ps5_form(monkeypatch):
    import apply
    import setup_agents

    windows(monkeypatch, setup_agents)
    monkeypatch.setattr(apply, "sys", SimpleNamespace(**{**vars(sys), "platform": "win32"}))
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))
    monkeypatch.setitem(COMMANDS, "fixture", Command("echo bash", "Write-Output 'seven'", "Write-Output 'five'"))
    argv, use_shell = shell.launch("fixture")
    assert argv[0] == "powershell" and not use_shell
    assert "'five'" in argv[-1] and "'seven'" not in argv[-1]
    assert setup_agents.hook_shell("codex") == shell.chosen().prefix()
    settings = {}
    apply.configure(settings, None, None, "C:/fixture/python.exe", "codex")
    for groups in settings["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"].startswith('& "C:/fixture/python.exe" ')
                assert not shell.seven_only(hook["command"])


def test_a_ps5_hook_is_not_repaired_to_a_missing_pwsh(monkeypatch):
    import command_repair
    import claude_pretool
    import codex_pretool

    windows(monkeypatch, command_repair)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))
    assert command_repair.repaired("powershell -NoProfile -Command Get-Date")[0] == \
        "powershell -NoProfile -Command Get-Date"
    payload = {"tool_name": "Bash", "tool_input": {"command": "powershell -Command 'Get-Error'"}}
    for host in (claude_pretool, codex_pretool):
        output = host.verdict(payload)["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "PowerShell 7 설치" in output["permissionDecisionReason"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell live boundary")
@pytest.mark.parametrize("name,major", [("powershell", 5), ("pwsh", 7)])
def test_real_shell_preserves_native_exit_code_and_utf8_output(tmp_path, monkeypatch, name, major):
    from main import runtime, specs

    executable = shutil.which(name)
    if not executable:
        pytest.skip(f"{name} is not installed")
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell(executable, f"{major}.1.0", major),))
    command = "& " + shell.quote(Path(sys.executable).as_posix(), "ps5") + ' -c "print(\'한글\'); raise SystemExit(13)"'
    code, output, cut = runtime.gate(command, tmp_path, threading.Event(),
                                   {**os.environ, "PYTHONIOENCODING": "utf-8"}, 15, None, specs.kill)
    assert (code, output.strip(), cut) == (13, "한글", "")


def test_lint_rejects_uncatalogued_instructions_and_incompatible_ps5(tmp_path, monkeypatch):
    import shell_lint

    assert shell_lint.check() == []
    doc = tmp_path / "docs/new.md"
    doc.parent.mkdir()
    doc.write_text("```powershell\nGet-UnknownCommand -Fixture\n```\nRun `Get-OtherCommand -Fixture`.\n", encoding="utf-8")
    prompt = tmp_path / "tool/main/prompt.py"
    prompt.parent.mkdir(parents=True)
    prompt.write_text('PROMPT = "Get-AnotherCommand -Fixture"\n', encoding="utf-8")
    (prompt.parent / "formatted.py").write_text('PROMPT = f"Run `Get-FormattedCommand {value}`"\n', encoding="utf-8")
    (tmp_path / "tool/prompts").mkdir()
    (tmp_path / "tool/prompts/new.json").write_text('{"prompt": "Get-JsonCommand -Fixture"}', encoding="utf-8")
    findings = shell_lint.check(tmp_path)
    assert len(findings) == 5
    assert all("미등록 명령" in message for _, message in findings)
    monkeypatch.setitem(COMMANDS, "unsafe", Command("", "Get-Error", "Get-Error", ("Get-Error",)))
    assert any("unsafe: ps5" in message for _, message in shell_lint.check(tmp_path))


def test_native_arguments_and_legacy_quoted_executables_are_powershell_forms(monkeypatch):
    windows(monkeypatch)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))
    assert shell.command_line(["C:/Team $Ops/python.exe", "it's", 'x"y']) == \
        "& 'C:/Team $Ops/python.exe' 'it''s' 'x\"y'"
    assert shell.resolve("gate", command='"C:/Python/python.exe" check.py') == \
        '& "C:/Python/python.exe" check.py'
    assert shell.invoked('& "C:/Python/python.exe" check.py') == '& "C:/Python/python.exe" check.py'
    assert shell.invoked('Write-Output "C:/Python/python.exe"') == 'Write-Output "C:/Python/python.exe"'
    assert shell.invoked('"C:/python.exe" a && "C:/python.exe" b') == \
        '& "C:/python.exe" a && & "C:/python.exe" b'
    assert not shell.seven_only('Write-Output "text" 2>&1')
    assert not shell.seven_only("Get-Content -Raw -Encoding UTF8 result.txt")


def test_ps5_does_not_receive_a_repaired_tee_using_default_encoding(monkeypatch):
    import codex_pretool
    import command_repair
    import guard

    windows(monkeypatch, command_repair, guard)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("powershell", "5.1.19041.1", 5),))
    result = codex_pretool.verdict({"tool_name": "exec_command", "tool_input": {"cmd": "pytest -q | Select-Object -Last 2"}})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "PowerShell 7 설치" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_nested_classic_shell_cannot_receive_seven_syntax_even_from_pwsh7(monkeypatch):
    windows(monkeypatch)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("pwsh", "7.6.1", 7),))
    with pytest.raises(ValueError, match="PowerShell 7 설치"):
        shell.launch("gate", command="powershell -NoProfile -Command 'Get-Error'")
