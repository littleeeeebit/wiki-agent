"""The Android toolchain for oneshot `android` runs (`docs/plans/oneshot/7-android.md`, "Toolchain preflight")."""

import hashlib
import io
import json
import os
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

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


def jdk(where: Path, version: str, launchers=("java", "javac")) -> Path:
    (where / "bin").mkdir(parents=True)
    for launcher in launchers:
        (where / "bin" / f"{launcher}{android.EXE}").write_text("")
    (where / "release").write_text(f'JAVA_VERSION="{version}"\n')
    return where


def sdk(root: Path, skip: str = "", without: str = "") -> Path:
    for key, _, folder, proofs in android.PIECES:
        if key != skip:
            for proof in proofs:
                if proof != without:
                    (root / folder / proof).parent.mkdir(parents=True, exist_ok=True)
                    (root / folder / proof).write_text("")
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


def test_metadata_without_the_launchers_is_not_a_tool(home):
    """A damaged SDK or JDK kept its `source.properties` and `release`; it cannot run, so it stays missing."""

    sdk(android.ROOT / "sdk", without=f"aapt2{android.EXE}")
    jdk(android.ROOT / "jdk-17", "17.0.21", launchers=("javac",))
    assert set(android.status()["missing"]) == {"JDK 17", f"빌드 도구 {android.BUILD_TOOLS}"}


def test_the_owners_complete_sdk_is_used_and_a_partial_one_is_not(home):
    os.environ["ANDROID_HOME"] = str(sdk(home / "partial", skip="image"))
    assert android.status()["sdk"] == str(android.ROOT / "sdk")
    owners = sdk(home / "local" / "Android" / "Sdk")
    found = android.status()
    assert found["sdk"] == str(owners)
    assert all(p["version"] == "36.0.0" for p in found["pieces"] if p["key"] != "jdk")


def test_a_build_runs_on_the_found_toolchain_with_its_caches_and_preferences_in_the_application_folder(home):
    with pytest.raises(ValueError, match="JDK 17"):
        android.build_env()
    sdk(android.ROOT / "sdk")
    jdk(android.ROOT / "jdk-17", "17.0.21")
    with patch.dict(os.environ, {"ANDROID_SDK_ROOT": str(home / "owners-sdk"), "PATH": "C:/owner/bin"}):
        env = android.build_env()
    assert env["JAVA_HOME"] == str(android.ROOT / "jdk-17") and env["ANDROID_HOME"] == str(android.ROOT / "sdk")
    assert env["ANDROID_USER_HOME"] == str(android.ROOT / "user") and env["GRADLE_USER_HOME"] == str(android.ROOT / "gradle")
    assert "ANDROID_SDK_ROOT" not in env and (android.ROOT / "gradle").is_dir()
    assert env["PATH"].split(os.pathsep)[:2] == [str(android.ROOT / "jdk-17" / "bin"), str(android.ROOT / "sdk" / "platform-tools")]


def test_the_newest_stable_windows_command_line_tools_are_picked():
    assert android.cmdline_tools(MANIFEST) == (android.REPOSITORY + "new-win.zip", "new")
    with pytest.raises(ValueError):
        android.cmdline_tools(b"<sdk-repository/>")


def test_a_download_is_kept_only_when_its_checksum_matches(home):
    source = home / "archive.zip"
    source.write_bytes(b"archive bytes")
    good, out = hashlib.sha256(b"archive bytes").hexdigest(), android.ROOT / "downloads"
    with pytest.raises(ValueError):
        android.download(source.as_uri(), out / "a.zip", "sha256", "0" * 64)
    assert not list(out.iterdir())   # nothing half-kept
    assert android.download(source.as_uri(), out / "a.zip", "sha256", good).read_bytes() == b"archive bytes"


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
    jdk_zip = zipped("jdk-17.0.21+7", {"release": 'JAVA_VERSION="17.0.21"\n', "bin/java.exe": "", "bin/javac.exe": ""})
    (served / "jdk.zip").write_bytes(jdk_zip)
    (served / "jdk.zip.sha256sum.txt").write_text(f"{hashlib.sha256(jdk_zip).hexdigest()}  jdk.zip\n")
    tools = zipped("cmdline-tools", {"bin/sdkmanager.bat": "", "source.properties": "Pkg.Revision=23.0\n"})
    (served / "new-win.zip").write_bytes(tools)
    (served / "repository2-3.xml").write_bytes(MANIFEST.replace(b"<checksum>new</checksum>",
                                                                f"<checksum>{hashlib.sha1(tools).hexdigest()}</checksum>".encode()))
    ran = []

    def gate(cmd, cwd, halt, env, timeout, owned_jobs, kill, argv=None, idle=None):
        assert idle == timeout   # silence alone never cuts the install
        ran.append((argv, env))
        sdk(android.ROOT / "sdk")
        return 0, "done\n", ""

    with patch.object(android, "JDK_URL", (served / "jdk.zip").as_uri()), \
         patch.object(android, "REPOSITORY", served.as_uri() + "/"), \
         patch.object(android.runtime, "gate", gate), \
         patch.dict(os.environ, {"ANDROID_USER_HOME": str(home / "owners-prefs")}):
        assert android.install()
        assert not android.install()   # one job at a time
        android.close_install()
    found = android.status()
    assert found["job"]["state"] == "done", found["job"]
    assert found["missing"] == [] and found["jdk"] == str(android.ROOT / "jdk-17")
    (line, env), = ran
    answers = android.ROOT / "tmp" / "licence-answers.txt"
    assert f'--sdk_root="{android.ROOT / "sdk"}"' in line and f'"{android.IMAGE}"' in line and f'< "{answers}"' in line
    assert answers.read_bytes() == b"y\n" * android.ANSWERS
    # Preferences and temporary files stay in the application's folder, never the owner's.
    assert env["JAVA_HOME"] == str(android.ROOT / "jdk-17") and env["ANDROID_USER_HOME"] == str(android.ROOT / "user")
    assert env["TEMP"] == env["TMP"] == str(android.ROOT / "tmp")
    assert not (android.ROOT / "installing").exists()
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
        android.close_install()
    found = android.status()
    assert found["job"]["state"] == "failed" and "체크섬" in found["job"]["error"]
    assert not (android.ROOT / "jdk-17").exists() and not (android.ROOT / "receipt.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")
def test_nothing_is_written_or_deleted_through_a_junction_out_of_the_application_folder(home):
    """A junction at `sdk/cmdline-tools` into an owner's SDK made the unpack delete the owner's `latest`."""

    import _winapi

    owners = home / "owner-sdk" / "cmdline-tools"
    (owners / "latest").mkdir(parents=True)
    (owners / "latest" / "owner.txt").write_text("owner SDK file")
    (android.ROOT / "sdk").mkdir(parents=True)
    _winapi.CreateJunction(str(owners), str(android.ROOT / "sdk" / "cmdline-tools"))
    archive = android.ROOT / "downloads" / "tools.zip"
    archive.parent.mkdir()
    archive.write_bytes(zipped("cmdline-tools", {"bin/sdkmanager.bat": "fixture"}))
    with pytest.raises(ValueError, match="링크"):
        android.unpack(archive, android.ROOT / "sdk" / "cmdline-tools" / "latest")
    assert (owners / "latest" / "owner.txt").read_text() == "owner SDK file"
    with pytest.raises(ValueError, match="밖"):
        android.owned(home / "elsewhere")


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")
def test_a_refused_root_deletes_nothing_behind_it(home):
    """`ROOT` a junction: the install refuses it, and its cleanup leaves the owner's `installing` alone."""

    import _winapi

    outside = home / "owners-folder"
    outside.mkdir()
    (outside / "installing").write_text("owner file")
    _winapi.CreateJunction(str(outside), str(android.ROOT))
    with patch.object(android.runtime, "stopping", threading.Event()):
        android._install()
    assert android._job["state"] == "failed" and "링크" in android._job["error"]
    assert (outside / "installing").read_text() == "owner file"


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")
def test_a_junction_made_during_the_network_wait_is_refused(home):
    import _winapi

    downloads, outside = android.ROOT / "downloads", home / "owner"
    downloads.mkdir(parents=True)
    outside.mkdir()
    (outside / "tools.zip").write_bytes(b"owner archive")

    def answer(*_, **__):
        downloads.rmdir()
        _winapi.CreateJunction(str(outside), str(downloads))
        return io.BytesIO(b"fixture archive")

    with patch.object(android.urllib.request, "urlopen", answer), pytest.raises(ValueError, match="링크"):
        android.download("https://example.invalid/x", downloads / "tools.zip", "sha256",
                         hashlib.sha256(b"fixture archive").hexdigest())
    assert sorted(p.name for p in outside.iterdir()) == ["tools.zip"]
    assert (outside / "tools.zip").read_bytes() == b"owner archive"


@pytest.mark.skipif(os.name != "nt", reason="the install runs on Windows only")
def test_the_real_command_line_gives_bare_yes_answers_and_outlasts_a_quiet_spell(home):
    """A fake `sdkmanager.bat` reads its first answer the way Android's reader does, untrimmed, after a
    silence longer than the verification gate's idle limit; the real `cmd.exe` line and gate run it."""

    root = home / "app with spaces"
    check = home / "check.py"
    check.write_text("import sys, time\nline = sys.stdin.readline().rstrip('\\r\\n')\ntime.sleep(1.5)\n"
                     "print(repr(line))\nsys.exit(0 if line.lower() in ('y', 'yes') else 7)\n")
    tool = root / "sdk" / android.PIECES[0][2] / android.PIECES[0][3][0]
    tool.parent.mkdir(parents=True)
    tool.write_text(f'@echo off\n"{os.sys.executable}" "{check}"\n')
    with patch.object(android, "ROOT", root), patch.object(android.runtime, "IDLE_SECONDS", 0.3), \
         patch.object(android.runtime, "stopping", threading.Event()):
        android.sdkmanager(root / "sdk", str(root / "jdk with spaces"), ["platform-tools"])
    assert android._job["log"] == ["'y'"]


def test_a_start_while_stopping_is_refused_and_a_cut_install_shows_after_a_restart(home):
    with patch.object(android.runtime, "stopping", threading.Event()) as stopping:
        stopping.set()
        with pytest.raises(HTTPException):
            android.install()
    android.ROOT.mkdir(parents=True)
    (android.ROOT / "installing").write_text("0")   # the marker a server left when it went down
    found = android.status()["job"]
    assert found["state"] == "failed" and "끊겼다" in found["error"]
