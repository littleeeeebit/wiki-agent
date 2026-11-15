"""The Android toolchain an `android` oneshot run builds with: found by path, installed only when the owner
presses install (`docs/plans/oneshot/7-android.md`, "Toolchain preflight").

Everything installs under `ROOT`: no system folder, `PATH` entry or existing SDK is changed. Each archive is
checked against the checksum its publisher lists before it is unpacked, and a receipt records what was
installed from where.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()
ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "wiki-agent-data" / "android"
PLATFORM, BUILD_TOOLS = "android-36", "36.0.0"
IMAGE = f"system-images;{PLATFORM};google_apis;x86_64"
JDK_URL = "https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip"   # + `.sha256sum.txt` beside it
REPOSITORY = "https://dl.google.com/android/repository/"
EXE, BAT = (".exe", ".bat") if os.name == "nt" else ("", "")
# (key, what the owner reads, a file that proves it, the folder whose `source.properties` names its version)
PIECES = (("cmdline-tools", "SDK 명령줄 도구", f"cmdline-tools/latest/bin/sdkmanager{BAT}", "cmdline-tools/latest"),
          ("platform-tools", "플랫폼 도구 (adb)", f"platform-tools/adb{EXE}", "platform-tools"),
          ("emulator", "에뮬레이터", f"emulator/emulator{EXE}", "emulator"),
          ("platform", f"플랫폼 {PLATFORM}", f"platforms/{PLATFORM}/android.jar", f"platforms/{PLATFORM}"),
          ("build-tools", f"빌드 도구 {BUILD_TOOLS}", f"build-tools/{BUILD_TOOLS}/source.properties",
           f"build-tools/{BUILD_TOOLS}"),
          ("image", "시스템 이미지 (x86_64)", f"system-images/{PLATFORM}/google_apis/x86_64/system.img",
           f"system-images/{PLATFORM}/google_apis/x86_64"))
_lock = threading.Lock()
_job: dict = {"state": "idle", "step": "", "error": "", "log": []}


def revision(folder: Path) -> str:
    try:
        text = (folder / "source.properties").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = re.search(r"^Pkg\.Revision=(.+)$", text, re.M)
    return found.group(1).strip() if found else ""


def java(home: Path) -> str:
    """The JDK's version from its `release` file, `""` unless it is a JDK 17 or later."""

    try:
        found = re.search(r'^JAVA_VERSION="([^"]+)"', (home / "release").read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    version = found.group(1) if found else ""
    return version if (home / "bin" / f"javac{EXE}").exists() and version.split(".")[0].isdigit() \
        and int(version.split(".")[0]) >= 17 else ""


def roots() -> list[Path]:
    """SDK folders in the order they are taken: the owner's own, then this application's."""

    named = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")]
    return [*dict.fromkeys(Path(p) for p in named if p), ROOT / "sdk"]


def status() -> dict:
    """What the toolchain has now: one SDK folder that holds every piece if there is one, else this
    application's, each piece with its path and version, and the install job."""

    complete = [r for r in roots() if all((r / proof).exists() for _, _, proof, _ in PIECES)]
    sdk = complete[0] if complete else ROOT / "sdk"
    homes = [ROOT / "jdk-17", *([Path(os.environ["JAVA_HOME"])] if os.environ.get("JAVA_HOME") else [])]
    jdk = next(((h, v) for h in homes if (v := java(h))), None)
    pieces = [{"key": "jdk", "label": "JDK 17", "path": str(jdk[0]) if jdk else "", "version": jdk[1] if jdk else ""}]
    for key, label, proof, folder in PIECES:
        here = (sdk / proof).exists()
        pieces.append({"key": key, "label": label, "path": str(sdk / folder) if here else "",
                       "version": revision(sdk / folder) if here else ""})
    try:
        receipt = json.loads((ROOT / "receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt = None
    with _lock:
        job = {**_job, "log": _job["log"][-20:]}
    return {"sdk": str(sdk), "jdk": str(jdk[0]) if jdk else "", "pieces": pieces,
            "missing": [p["label"] for p in pieces if not p["path"]], "job": job, "receipt": receipt}


def missing() -> list[str]:
    """The card's errors for a missing toolchain, each with what installs it."""

    gone = status()["missing"]
    return [f"Android 도구가 없다: {', '.join(gone)} — 카드의 [도구 설치]가 {ROOT} 에 받는다"] if gone else []


# -- Install ---------------------------------------------------------------------------------------

def fetch(url: str, timeout: float = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wiki-agent"}),
                                timeout=timeout) as answer:
        return answer.read()


def download(url: str, to: Path, algorithm: str, checksum: str) -> Path:
    """`url` into `to`, kept only when its `algorithm` digest is `checksum`."""

    to.parent.mkdir(parents=True, exist_ok=True)
    digest, partial = hashlib.new(algorithm), to.with_suffix(to.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wiki-agent"}),
                                timeout=120) as answer, partial.open("wb") as out:
        while chunk := answer.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest().lower() != checksum.lower():
        partial.unlink(missing_ok=True)
        raise ValueError(f"{url} 의 체크섬이 맞지 않는다")
    os.replace(partial, to)
    return to


def cmdline_tools(manifest: bytes) -> tuple[str, str]:
    """The newest stable Windows `cmdline-tools` archive in Google's repository manifest: `(url, sha1)`."""

    best = None
    for package in ET.fromstring(manifest).iter("remotePackage"):
        channel = package.find("channelRef")
        if not package.get("path", "").startswith("cmdline-tools;") or channel is None \
                or channel.get("ref") != "channel-0":
            continue
        rev = package.find("revision")
        number = tuple(int(rev.findtext(k) or 0) for k in ("major", "minor", "micro")) if rev is not None else ()
        for archive in package.iter("archive"):
            if archive.findtext("host-os") == "windows":
                url, sha1 = archive.findtext("complete/url"), archive.findtext("complete/checksum")
                if url and sha1 and (best is None or number > best[0]):
                    best = (number, url, sha1)
    if best is None:
        raise ValueError("저장소 목록에 Windows 명령줄 도구가 없다")
    return REPOSITORY + best[1], best[2]


def unpack(archive: Path, to: Path) -> None:
    """The archive's single top folder becomes `to`."""

    temporary = archive.with_suffix(".unpacked")
    shutil.rmtree(temporary, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(temporary)
    tops = list(temporary.iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise ValueError(f"{archive.name} 의 맨 위 폴더가 하나가 아니다")
    shutil.rmtree(to, ignore_errors=True)
    to.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tops[0], to)
    shutil.rmtree(temporary, ignore_errors=True)


def note(step: str = "", line: str = "") -> None:
    with _lock:
        if step:
            _job["step"] = step
        if line:
            _job["log"] = [*_job["log"][-199:], line]


def install() -> bool:
    """The owner's install button: one job at a time, in the background. `False` when one already runs."""

    with _lock:
        if _job["state"] == "running":
            return False
        _job.update(state="running", step="", error="", log=[])
    threading.Thread(target=_install, daemon=True, name="oneshot-android-install").start()
    return True


def _install() -> None:
    sources = []
    try:
        if os.name != "nt":
            raise ValueError("이 버전은 Windows 에서만 Android 도구를 설치한다")
        now = status()
        downloads, sdk = ROOT / "downloads", ROOT / "sdk"
        if not now["jdk"]:
            note("JDK 17 받는 중")
            checksum = fetch(JDK_URL + ".sha256sum.txt").decode().split()[0]
            unpack(download(JDK_URL, downloads / "jdk-17.zip", "sha256", checksum), ROOT / "jdk-17")
            sources.append({"url": JDK_URL, "sha256": checksum})
        if not (sdk / PIECES[0][2]).exists():
            note("SDK 명령줄 도구 받는 중")
            url, sha1 = cmdline_tools(fetch(REPOSITORY + "repository2-3.xml"))
            unpack(download(url, downloads / "cmdline-tools.zip", "sha1", sha1), sdk / "cmdline-tools" / "latest")
            sources.append({"url": url, "sha1": sha1})
        note("플랫폼 도구, 에뮬레이터, 플랫폼, 빌드 도구, 시스템 이미지 받는 중 (수 GB)")
        wanted = ["platform-tools", "emulator", f"platforms;{PLATFORM}", f"build-tools;{BUILD_TOOLS}", IMAGE]
        # Pressing install accepts the Android SDK licences the card names; sdkmanager asks once per licence.
        done = subprocess.run([str(sdk / PIECES[0][2]), f"--sdk_root={sdk}", "--install", *wanted],
                              input="y\n" * 30, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=3 * 3600,
                              env={**os.environ, "JAVA_HOME": str(ROOT / "jdk-17") if not now["jdk"] else now["jdk"]})
        for line in (done.stdout + done.stderr).splitlines()[-40:]:
            note(line=line)
        if done.returncode:
            raise ValueError(f"sdkmanager 가 {done.returncode} 로 끝났다")
        sources.append({"sdkmanager": wanted})
        after = status()
        if after["missing"]:
            raise ValueError(f"설치 뒤에도 없다: {', '.join(after['missing'])}")
        (ROOT / "receipt.json").write_text(json.dumps({"at": time.time(), "sdk": after["sdk"], "jdk": after["jdk"],
                                                       "pieces": after["pieces"], "sources": sources},
                                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with _lock:
            _job.update(state="done", step="설치를 마쳤다")
    except Exception as exc:   # the owner reads what failed and presses again
        with _lock:
            _job.update(state="failed", error=f"{type(exc).__name__}: {exc}")


@router.get("/api/oneshot-android/toolchain")
def toolchain() -> dict:
    return status()


@router.post("/api/oneshot-android/toolchain/install")
def pressed() -> dict:
    if not install():
        raise HTTPException(409, "도구 설치가 이미 진행 중이다")
    return status()
