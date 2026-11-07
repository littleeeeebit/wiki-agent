"""Expressions, CommonMark fences and encoded shell bodies retain their meaning."""

import base64

import pytest

from common import shell
from test_shell_round_two import assert_denied, ps5  # noqa: F401 -- pytest discovers the shared fixture

pytestmark = pytest.mark.usefixtures("ps5")


@pytest.mark.parametrize("extension", ["exe", "cmd", "bat", "ps1"])
@pytest.mark.parametrize("operator", ['?? "fallback"', '??= "fallback"', '? "first" : "second"'])
@pytest.mark.parametrize("quote", ["'", '"'])
def test_ps7_expression_operators_never_turn_path_values_into_invocations(monkeypatch, extension, operator, quote):
    command = quote + f"outside/target.{extension}" + quote + " " + operator
    assert shell.invoked(command) == command
    for name in ("gate", "registered-check"):
        assert shell.resolve(name, form="pwsh7", command=command) == command
        with pytest.raises(ValueError, match="PowerShell 7 설치"):
            shell.resolve(name, form="ps5", command=command)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("pwsh", "7.5", 7),))
    assert command in shell.launch("gate", command=command)[0][-1]


@pytest.mark.parametrize("fence", ["~~~", "````", "~~~~", "`````", "~~~~~", "````````", "~~~~~~~~"])
@pytest.mark.parametrize("container", ["{block}", "> {block}", "- instructions\n\n  {block}"])
def test_commonmark_powershell_fences_are_catalogue_checked(tmp_path, fence, container):
    import shell_lint

    block = f"{fence}PowerShell\nGet-UnlistedCommand -Fixture\n{fence}\n"
    prefix = "> " if container.startswith(">") else "  " if container.startswith("-") else ""
    text = container.format(block=block.replace("\n", "\n" + prefix).rstrip())
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "instructions.md").write_text(text, encoding="utf-8")
    assert any("미등록 명령" in message and "Get-UnlistedCommand" in message for _, message in shell_lint.check(tmp_path))


@pytest.mark.parametrize("option", ["encodedcommand", "ENCODEDCOMMAND", "ec", *(
    "EncodedCommand"[:length] for length in range(1, len("EncodedCommand") + 1)
)])
@pytest.mark.parametrize("major", [5, 7])
@pytest.mark.parametrize("target", ["powershell", '& ("outside/powershell.exe")'])
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_encoded_classic_commands_cannot_bypass_capability_checks(monkeypatch, option, major, target, quote):
    body = base64.b64encode("Get-Error".encode("utf-16-le")).decode("ascii")
    command = f"{target} -{option} {quote}{body}{quote}"
    assert shell.seven_only(command) == ("Get-Error",)
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    assert_denied(command, monkeypatch)


@pytest.mark.parametrize("body", [
    'Write-Output "Get-Error"', 'Write-Output "a > b"', 'Write-Output "value ?? fallback"',
    "Get-Error", "Test-Json", '$value ?? "fallback"', "Write-Output text > outside/target.txt",
])
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_encoding_preserves_the_body_capability_classification(monkeypatch, body, quote):
    encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
    command = "powershell -NoProfile -EncodedCommand " + quote + encoded + quote + " -NonInteractive"
    assert shell.seven_only(command) == shell.seven_only(body)
    if shell.seven_only(body):
        assert_denied(command, monkeypatch)
    else:
        assert shell.launch("gate", command=command)[0][0] == "powershell"
    assert shell.seven_only("Write-Output '" + command.replace("'", "''") + "'") == ()


@pytest.mark.parametrize("encoded", ["%%%", base64.b64encode(b"odd").decode(), base64.b64encode(b"\x00\xd8").decode()])
@pytest.mark.parametrize("major", [5, 7])
def test_unreadable_encoded_bodies_are_denied_instead_of_skipping_inspection(monkeypatch, encoded, major):
    import command_repair

    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("fixture-shell", f"{major}.1", major),))
    command = "powershell -EncodedCommand " + encoded
    with pytest.raises(ValueError, match="인코딩된 PowerShell 명령"):
        shell.launch("gate", command=command)
    monkeypatch.setattr(command_repair, "os", shell.os)
    payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
    assert all(command_repair.refusal(payload, host)["hookSpecificOutput"]["permissionDecision"] == "deny"
               for host in ("claude", "codex"))


@pytest.mark.parametrize("outer", ["Command", "EncodedCommand"])
def test_nested_classic_encoded_bodies_cannot_bypass_the_parent_boundary(monkeypatch, outer):
    encoded = base64.b64encode("Get-Error".encode("utf-16-le")).decode("ascii")
    body = "powershell -EncodedCommand " + encoded
    argument = base64.b64encode(body.encode("utf-16-le")).decode("ascii") if outer == "EncodedCommand" else "'" + body + "'"
    monkeypatch.setattr(shell, "_detected", (shell.PowerShell("pwsh", "7.5", 7),))
    assert_denied("pwsh -" + outer + " " + argument, monkeypatch)


@pytest.mark.parametrize("marker", ["`", "~"])
@pytest.mark.parametrize("length", [4, 8])
def test_example_fences_keep_nested_powershell_text_as_data(marker, length):
    import shell_lint

    text = marker * length + "markdown\n```PowerShell\nGet-UnlistedCommand -Fixture\n```\n" + marker * length
    assert list(shell_lint.instructions(text)) == []
