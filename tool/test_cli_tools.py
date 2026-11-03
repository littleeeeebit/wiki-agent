"""`fd` and `jq`: found where winget put them, installed in the background once, and run by path
from a session whose PATH predates the install (`cli_tools`, `command_repair`)."""

import hashlib
import io
import shutil
import subprocess
import sys
import zipfile

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


def test_the_hook_runs_an_installed_tool_by_path_and_leaves_text_alone(no_path):
    exe = no_path / "Links" / "jq.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    fixed, notes = command_repair.repaired("cat a.json | jq .name && echo 'jq is fine'")
    assert fixed == f"cat a.json | {exe.as_posix()} .name && echo 'jq is fine'"
    assert "not on this session's PATH" in notes[0]
    assert command_repair.repaired("cat <<'TXT'\njq .x\nTXT")[0] == "cat <<'TXT'\njq .x\nTXT"
    assert command_repair.repaired("rg -n jq src")[0] == "rg -n jq src", "an argument is not a command"


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
