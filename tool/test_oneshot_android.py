"""The Android toolchain for oneshot `android` runs (`docs/plans/oneshot/7-android.md`, "Toolchain preflight")."""

import hashlib
import io
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from main import oneshot_android as android

MANIFEST = b"""<sdk:sdk-repository xmlns:sdk="x">
<remotePackage path="cmdline-tools;12.0"><revision><major>12</major><minor>0</minor></revision>
 <channelRef ref="channel-0"/><archives>
 <archive><complete><size>1</size><checksum>old</checksum><url>old-win.zip</url></complete><host-os>windows</host-os></archive>
 </archives></remotePackage>
<remotePackage path="cmdline-tools;23.0"><revision><major>23</major><minor>0</minor></revision>
 <channelRef ref="channel-0"/><archives>
 <archive><complete><size>1</size><checksum>mac</checksum><url>new-mac.zip</url></complete><host-os>macosx</host-os></archive>
 <archive><complete><size>1</size><checksum>new</checksum><url>new-win.zip</url></complete><host-os>windows</host-os></archive>
 </archives></remotePackage>
<remotePackage path="cmdline-tools;24.0-alpha01"><revision><major>24</major><minor>0</minor></revision>
 <channelRef ref="channel-2"/><archives>
 <archive><complete><size>1</size><checksum>alpha</checksum><url>alpha-win.zip</url></complete><host-os>windows</host-os></archive>
 </archives></remotePackage>
</sdk:sdk-repository>"""


@pytest.fixture
def home(tmp_path):
    """No SDK or JDK of this machine's: an empty application folder and no Android variables."""

    clean = {k: v for k, v in os.environ.items() if k not in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME")}
    with patch.dict(os.environ, {**clean, "LOCALAPPDATA": str(tmp_path / "local")}, clear=True), \
         patch.object(android, "ROOT", tmp_path / "app"), \
         patch.object(android, "_job", {"state": "idle", "step": "", "error": "", "log": []}):
        yield tmp_path


def jdk(where: Path, version: str) -> Path:
    (where / "bin").mkdir(parents=True)
    (where / "bin" / f"javac{android.EXE}").write_text("")
    (where / "release").write_text(f'JAVA_VERSION="{version}"\n')
    return where


def sdk(root: Path, skip: str = "") -> Path:
    for key, _, proof, folder in android.PIECES:
        if key != skip:
            (root / proof).parent.mkdir(parents=True, exist_ok=True)
            (root / proof).write_text("")
            (root / folder / "source.properties").write_text("Pkg.Revision=36.0.0\n")
    return root


def test_nothing_installed_is_every_piece_missing(home):
    found = android.status()
    assert found["sdk"] == str(android.ROOT / "sdk")
    assert len(found["missing"]) == 1 + len(android.PIECES)
    assert "JDK 17" in android.missing()[0]


def test_a_jdk_before_17_is_not_the_toolchains(home):
    os.environ["JAVA_HOME"] = str(jdk(home / "jdk11", "11.0.16.1"))
    assert "JDK 17" in android.status()["missing"]
    os.environ["JAVA_HOME"] = str(jdk(home / "jdk17", "17.0.21"))
    found = android.status()
    assert found["jdk"] == str(home / "jdk17") and "JDK 17" not in found["missing"]


def test_the_owners_complete_sdk_is_used_and_a_partial_one_is_not(home):
    os.environ["ANDROID_HOME"] = str(sdk(home / "partial", skip="image"))
    assert android.status()["sdk"] == str(android.ROOT / "sdk")
    owners = sdk(home / "local" / "Android" / "Sdk")
    found = android.status()
    assert found["sdk"] == str(owners)
    assert all(p["version"] == "36.0.0" for p in found["pieces"] if p["key"] != "jdk")


def test_the_newest_stable_windows_command_line_tools_are_picked():
    assert android.cmdline_tools(MANIFEST) == (android.REPOSITORY + "new-win.zip", "new")
    with pytest.raises(ValueError):
        android.cmdline_tools(b"<sdk-repository/>")


def test_a_download_is_kept_only_when_its_checksum_matches(tmp_path):
    source = tmp_path / "archive.zip"
    source.write_bytes(b"archive bytes")
    good = hashlib.sha256(b"archive bytes").hexdigest()
    with pytest.raises(ValueError):
        android.download(source.as_uri(), tmp_path / "out" / "a.zip", "sha256", "0" * 64)
    assert not list((tmp_path / "out").iterdir())   # nothing half-kept
    assert android.download(source.as_uri(), tmp_path / "out" / "a.zip", "sha256", good).read_bytes() == b"archive bytes"


def zipped(top: str, files: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(f"{top}/{name}", text)
    return out.getvalue()


@pytest.mark.skipif(os.name != "nt", reason="the install runs on Windows only")
def test_install_checks_each_archive_runs_sdkmanager_once_and_writes_a_receipt(home):
    served = home / "served"
    served.mkdir()
    jdk_zip = zipped("jdk-17.0.21+7", {"release": 'JAVA_VERSION="17.0.21"\n', "bin/javac.exe": ""})
    (served / "jdk.zip").write_bytes(jdk_zip)
    (served / "jdk.zip.sha256sum.txt").write_text(f"{hashlib.sha256(jdk_zip).hexdigest()}  jdk.zip\n")
    tools = zipped("cmdline-tools", {"bin/sdkmanager.bat": "", "source.properties": "Pkg.Revision=23.0\n"})
    (served / "new-win.zip").write_bytes(tools)
    (served / "repository2-3.xml").write_bytes(MANIFEST.replace(b"<checksum>new</checksum>",
                                                                f"<checksum>{hashlib.sha1(tools).hexdigest()}</checksum>".encode()))
    ran = []

    def sdkmanager(command, **options):
        ran.append((command, options["env"]["JAVA_HOME"], options["input"]))
        sdk(android.ROOT / "sdk")
        return subprocess.CompletedProcess(command, 0, "done\n", "")

    with patch.object(android, "JDK_URL", (served / "jdk.zip").as_uri()), \
         patch.object(android, "REPOSITORY", served.as_uri() + "/"), \
         patch.object(android.subprocess, "run", sdkmanager):
        assert android.install()
        assert not android.install()   # one job at a time
        for _ in range(200):
            if android.status()["job"]["state"] != "running":
                break
            time.sleep(0.05)
    found = android.status()
    assert found["job"]["state"] == "done", found["job"]
    assert found["missing"] == [] and found["jdk"] == str(android.ROOT / "jdk-17")
    (command, java_home, answers), = ran
    assert command[1] == f"--sdk_root={android.ROOT / 'sdk'}" and android.IMAGE in command
    assert java_home == str(android.ROOT / "jdk-17") and answers.startswith("y\n")
    receipt = json.loads((android.ROOT / "receipt.json").read_text(encoding="utf-8"))
    assert [s.get("url") for s in receipt["sources"][:2]] == [(served / "jdk.zip").as_uri(),
                                                              (served / "new-win.zip").as_uri()]


@pytest.mark.skipif(os.name != "nt", reason="the install runs on Windows only")
def test_a_checksum_that_does_not_match_fails_the_install_and_installs_nothing(home):
    served = home / "served"
    served.mkdir()
    (served / "jdk.zip").write_bytes(zipped("jdk", {"release": ""}))
    (served / "jdk.zip.sha256sum.txt").write_text("0" * 64 + "  jdk.zip\n")
    with patch.object(android, "JDK_URL", (served / "jdk.zip").as_uri()):
        android.install()
        for _ in range(200):
            if android.status()["job"]["state"] != "running":
                break
            time.sleep(0.05)
    found = android.status()
    assert found["job"]["state"] == "failed" and "체크섬" in found["job"]["error"]
    assert not (android.ROOT / "jdk-17").exists() and not (android.ROOT / "receipt.json").exists()
