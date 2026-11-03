"""`fd` and `jq`: found where winget put them, installed in the background once, and run by path
from a session whose PATH predates the install (`cli_tools`, `command_repair`)."""

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import cli_tools
import command_repair


@pytest.fixture
def no_path(monkeypatch, tmp_path):
    """Neither tool on PATH; winget's folders and the temp folder under `tmp_path`."""
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name in cli_tools.PACKAGES else real(name))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(cli_tools.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path / "local" / "Microsoft" / "WinGet"


def test_a_tool_winget_put_in_its_package_folder_is_found_off_path(no_path):
    assert cli_tools.found("fd") == ""
    exe = no_path / "Packages" / "sharkdp.fd_Microsoft.Winget.Source_8wekyb3d8bbwe" / "fd-v10" / "fd.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert cli_tools.found("fd") == exe.as_posix()
    assert cli_tools.found("jq") == ""


def test_the_hook_puts_an_installed_tool_on_path_and_leaves_text_alone(no_path):
    exe = no_path / "Links" / "jq.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    command = "cat a.json | jq .name && echo 'jq is fine'"
    fixed, notes = command_repair.repaired(command)
    folder = re.sub(r"^([A-Za-z]):", lambda m: "/" + m[1].lower(), exe.parent.as_posix())
    assert fixed == f"PATH=\"$PATH\":'{folder}'; {command}", "Git Bash reads /c/..., not C:/..."
    assert "not on this session's PATH" in notes[0]
    path = f"$env:PATH += ';{exe.parent.as_posix()}'; "
    assert command_repair.repaired(command, "ps")[0] == path + command
    for first in ["# types\nUsing namespace System.Text\njq .", "using namespace A\n# more\nusing namespace B\njq .",
                  "[OutputType([string])]\nparam()\njq .", "param($a)\njq .", "begin { }\nend { jq . }",
                  "<# Header #> end { jq . }"]:
        fixed, notes = command_repair.repaired(first, "ps")   # what must come first stays first (R3-1, R4-1)
        assert fixed == first and "call it by that path" in notes[0], first
    typed = "[Text.Encoding]::UTF8 | Out-Null; jq ."
    assert command_repair.repaired(typed, "ps")[0] == path + typed, "a type literal is not an attribute"
    assert command_repair.repaired("cat <<'TXT'\njq .x\nTXT")[0] == "cat <<'TXT'\njq .x\nTXT"
    assert command_repair.repaired("rg -n jq src")[0] == "rg -n jq src", "an argument is not a command"


@pytest.mark.skipif(os.name == "nt", reason="`bash` on Windows may be WSL's; CI runs this on Linux")
def test_the_shell_not_the_pattern_decides_what_runs(no_path, monkeypatch, tmp_path):
    """The command is never edited, so data the pattern mistakes for a command keeps its meaning
    (review rounds 1 and 2 of #286), and the folder is on PATH for a real command word."""

    tool = tmp_path / "tools" / "jq"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\necho fake\n")
    tool.chmod(0o755)
    monkeypatch.setattr(cli_tools, "found", lambda name: tool.as_posix())

    def run(command):
        done = subprocess.run(["bash", "--noprofile", "--norc", "-c", command], capture_output=True,
                              text=True, env={"PATH": "/usr/bin:/bin"})
        return done.returncode, done.stdout

    for data in [r"printf '%s\n' foo\;jq bar", "jq=2; echo $((1 + (jq)))", "jq=2; echo $((jq + 1))",
                 "'printf' jq", "case jq in\n jq) echo yes;;\nesac", "(( jq + 1 )); echo $?"]:
        fixed = command_repair.repaired(data)[0]
        assert run(fixed) == run(data), (data, fixed)
    fixed = command_repair.repaired('jq . 2>/dev/null; printf %s "$PATH"')[0]
    assert run(fixed)[1].endswith(f":{tool.parent.as_posix()}")


def test_calls_arriving_together_start_one_install_and_a_failed_start_frees_it(no_path, monkeypatch):
    """Two calls reading the marker together, absent or past `HELD`, start one install (R1-3);
    a start that fails removes the marker so the next call tries again (R1-4)."""

    monkeypatch.setattr(cli_tools.sys, "platform", "win32")
    started = []
    monkeypatch.setattr(cli_tools.subprocess, "Popen", lambda argv, **_: started.append(argv))
    real = Path.exists

    def slow(path):   # widen the window between reading the marker and writing it
        if path.name.startswith("wiki-agent-install-") and not path.name.endswith(".lock"):
            time.sleep(0.3)
        return real(path)

    monkeypatch.setattr(Path, "exists", slow)
    marker = no_path.parents[2] / "wiki-agent-install-jq"
    for stale in [False, True]:
        if stale:
            os.utime(marker, (time.time() - cli_tools.HELD - 1,) * 2)
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(lambda _: cli_tools.started("jq"), range(2))) == [True, True]
        assert len(started) == 1 + stale

    marker = no_path.parents[2] / "wiki-agent-install-fd"
    monkeypatch.setattr(cli_tools.subprocess, "Popen", lambda argv, **_: (_ for _ in ()).throw(OSError("spawn")))
    assert cli_tools.started("fd") is False and not marker.exists()
    monkeypatch.setattr(cli_tools.subprocess, "Popen", lambda argv, **_: started.append(argv))
    assert cli_tools.started("fd") and len(started) == 3


def test_a_missing_tool_starts_one_background_install_and_says_so(no_path, monkeypatch):
    started = []
    monkeypatch.setattr(cli_tools.sys, "platform", "win32")
    monkeypatch.setattr(cli_tools.subprocess, "Popen", lambda argv, **_: started.append(argv))
    for _ in range(2):
        fixed, notes = command_repair.repaired("fd -e py")
        assert fixed == "fd -e py" and "installed in the background" in notes[0]
    assert started == [[sys.executable, cli_tools.__file__, "fd"]], "the second call finds the first running"


def test_a_stalled_winget_falls_back_to_the_pinned_release_file(no_path, monkeypatch):
    """Setup runs winget, then the release file when winget times out; a wrong digest installs nothing."""

    monkeypatch.setattr(cli_tools.sys, "platform", "win32")
    monkeypatch.setattr(cli_tools.shutil, "which", lambda name: "winget.exe" if name == "winget" else None)
    monkeypatch.setattr(cli_tools, "_stalled", False)
    ran = []

    def stalled(argv, **kwargs):
        ran.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(cli_tools.subprocess, "run", stalled)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("fd-v10/fd.exe", b"fd binary")
    files = {"fd.zip": archive.getvalue(), "jq.exe": b"jq binary"}
    monkeypatch.setattr(cli_tools, "RELEASES", {
        "fd": ("https://x/fd.zip", hashlib.sha256(files["fd.zip"]).hexdigest()),
        "jq": ("https://x/jq.exe", "0" * 64)})
    monkeypatch.setattr(cli_tools, "urlopen", lambda url, **_: io.BytesIO(files[url.rsplit("/", 1)[-1]]))

    assert all("없음" in line for line in cli_tools.install(check=True)) and not ran
    fd, jq = cli_tools.install()
    assert [a[-1] for a in ran] == ["sharkdp.fd"], "after one stall the next tool skips winget"
    assert "릴리스 파일" in fd and (cli_tools.bin_dir() / "fd.exe").read_bytes() == b"fd binary"
    assert "설치 실패" in jq and "SHA256" in jq and not (cli_tools.bin_dir() / "jq.exe").exists()
    assert cli_tools.found("fd") == (cli_tools.bin_dir() / "fd.exe").as_posix()
