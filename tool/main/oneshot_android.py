"""The Android toolchain an `android` oneshot run builds with: found by path, installed only when the owner
presses install (`docs/plans/oneshot/7-android.md`, "Toolchain preflight").

Everything installs under `ROOT`: no system folder, `PATH` entry, existing SDK or the owner's Android
preferences are changed. Every path written or deleted is checked to be `ROOT`'s own, never reached through
a link or junction. Each archive is checked against the checksum its publisher lists before it is unpacked,
and a receipt records what was installed from where. The server owns the job: it refuses a start while
stopping, and shutdown waits for it; `sdkmanager` runs in a process job that dies with the server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from common import is_junction
from . import runtime, specs

router = APIRouter()
ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "wiki-agent-data" / "android"
PLATFORM, BUILD_TOOLS = "android-36", "36.0.0"
IMAGE = f"system-images;{PLATFORM};google_apis;x86_64"
JDK_URL = "https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip"   # + `.sha256sum.txt` beside it
REPOSITORY = "https://dl.google.com/android/repository/"
EXE, BAT = (".exe", ".bat") if os.name == "nt" else ("", "")
# (key, what the owner reads, its folder in the SDK, the files that prove it runs, relative to that folder)
PIECES = (("cmdline-tools", "SDK 명령줄 도구", "cmdline-tools/latest", (f"bin/sdkmanager{BAT}", f"bin/avdmanager{BAT}")),
          ("platform-tools", "플랫폼 도구 (adb)", "platform-tools", (f"adb{EXE}",)),
          ("emulator", "에뮬레이터", "emulator", (f"emulator{EXE}",)),
          ("platform", f"플랫폼 {PLATFORM}", f"platforms/{PLATFORM}", ("android.jar",)),
          ("build-tools", f"빌드 도구 {BUILD_TOOLS}", f"build-tools/{BUILD_TOOLS}",
           (f"aapt2{EXE}", f"zipalign{EXE}", f"apksigner{BAT}", f"d8{BAT}")),
          ("image", "시스템 이미지 (x86_64)", f"system-images/{PLATFORM}/google_apis/x86_64",
           ("system.img", "kernel-ranchu")))
ANSWERS = 30   # licence answers given to sdkmanager; each licence asks once
_lock = threading.Lock()
_job: dict = {"state": "idle", "step": "", "error": "", "log": []}
_thread: threading.Thread | None = None


def revision(folder: Path) -> str:
    try:
        text = (folder / "source.properties").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = re.search(r"^Pkg\.Revision=(.+)$", text, re.M)
    return found.group(1).strip() if found else ""


def java(home: Path) -> str:
    """The JDK's version from its `release` file, `""` unless it is a JDK 17 or later with both launchers."""

    try:
        found = re.search(r'^JAVA_VERSION="([^"]+)"', (home / "release").read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    version = found.group(1) if found else ""
    major = version.split(".")[0]
    ok = major.isdigit() and int(major) >= 17 and all((home / "bin" / f"{t}{EXE}").is_file() for t in ("java", "javac"))
    return version if ok else ""


def whole(sdk: Path, folder: str, proofs: tuple) -> bool:
    return all((sdk / folder / proof).is_file() for proof in proofs)


def roots() -> list[Path]:
    """SDK folders in the order they are taken: the owner's own, then this application's."""

    named = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")]
    return [*dict.fromkeys(Path(p) for p in named if p), ROOT / "sdk"]


def status() -> dict:
    """What the toolchain has now: one SDK folder that holds every piece if there is one, else this
    application's, each piece with its path and version, and the install job."""

    complete = [r for r in roots() if all(whole(r, folder, proofs) for _, _, folder, proofs in PIECES)]
    sdk = complete[0] if complete else ROOT / "sdk"
    homes = [ROOT / "jdk-17", *([Path(os.environ["JAVA_HOME"])] if os.environ.get("JAVA_HOME") else [])]
    jdk = next(((h, v) for h in homes if (v := java(h))), None)
    pieces = [{"key": "jdk", "label": "JDK 17", "path": str(jdk[0]) if jdk else "", "version": jdk[1] if jdk else ""}]
    for key, label, folder, proofs in PIECES:
        here = whole(sdk, folder, proofs)
        pieces.append({"key": key, "label": label, "path": str(sdk / folder) if here else "",
                       "version": revision(sdk / folder) if here else ""})
    try:
        receipt = json.loads((ROOT / "receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt = None
    with _lock:
        job = {**_job, "log": _job["log"][-20:]}
    if job["state"] == "idle" and (ROOT / "installing").exists():   # a server that went down mid-install
        job.update(state="failed", error="이전 설치가 서버 종료로 끊겼다 — 다시 누르면 이어 받는다")
    return {"sdk": str(sdk), "jdk": str(jdk[0]) if jdk else "", "pieces": pieces,
            "missing": [p["label"] for p in pieces if not p["path"]], "job": job, "receipt": receipt}


def missing() -> list[str]:
    """The card's errors for a missing toolchain, each with what installs it."""

    gone = status()["missing"]
    return [f"Android 도구가 없다: {', '.join(gone)} — 카드의 [도구 설치]가 {ROOT} 에 받는다"] if gone else []


# -- Install ---------------------------------------------------------------------------------------

def owned(path: Path) -> Path:
    """`path` when it is `ROOT`'s own: under `ROOT`, and no folder from `ROOT` down to it is a link or a
    junction, which could lead into an owner's SDK. Anything else is refused before it is written.

    Each write, replace and delete checks right before it acts, so a junction that was there or appeared
    during a network wait is refused. A process of the owner's that swaps folders in the microseconds
    between a check and its write is outside this: it already holds the rights to the owner's SDK."""

    root, target = Path(os.path.abspath(ROOT)), Path(os.path.abspath(path))
    if not target.is_relative_to(root):
        raise ValueError(f"{target} 는 {root} 밖이다")
    node = root
    for part in ("", *target.relative_to(root).parts):
        node = node / part if part else node
        if node.is_symlink() or is_junction(node):
            raise ValueError(f"{node} 는 링크다 — 설치는 {root} 의 폴더에만 쓴다")
    return target


def discard(path: Path) -> None:
    """Cleanup that never follows a link: a path that is no longer `ROOT`'s own is left alone."""

    try:
        owned(path).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def fetch(url: str, timeout: float = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wiki-agent"}),
                                timeout=timeout) as answer:
        return answer.read()


def download(url: str, to: Path, algorithm: str, checksum: str) -> Path:
    """`url` into `to`, kept only when its `algorithm` digest is `checksum`; a server stop cuts it."""

    owned(to.parent).mkdir(parents=True, exist_ok=True)
    digest, partial = hashlib.new(algorithm), owned(to.with_suffix(to.suffix + ".part"))
    try:
        # The partial file opens after the response arrives, so its check follows the network wait.
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wiki-agent"}),
                                    timeout=120) as answer, owned(partial).open("wb") as out:
            while chunk := answer.read(1 << 20):
                if runtime.stopping.is_set():
                    raise ValueError("서버가 종료 중이다")
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest().lower() != checksum.lower():
            raise ValueError(f"{url} 의 체크섬이 맞지 않는다")
        os.replace(owned(partial), owned(to))
    except BaseException:
        discard(partial)
        raise
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
    """The archive's single top folder becomes `to`; both are `ROOT`'s own or nothing is touched."""

    temporary = archive.with_suffix(".unpacked")
    shutil.rmtree(owned(temporary), ignore_errors=True)
    with zipfile.ZipFile(owned(archive)) as z:
        z.extractall(owned(temporary))
    tops = list(temporary.iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise ValueError(f"{archive.name} 의 맨 위 폴더가 하나가 아니다")
    owned(to.parent).mkdir(parents=True, exist_ok=True)
    shutil.rmtree(owned(to), ignore_errors=True)
    os.replace(owned(tops[0]), owned(to))
    shutil.rmtree(owned(temporary), ignore_errors=True)


def note(step: str = "", line: str = "") -> None:
    with _lock:
        if step:
            _job["step"] = step
        if line:
            _job["log"] = [*_job["log"][-199:], line]


def install() -> bool:
    """The owner's install button: one job at a time, in the background. `False` when one already runs."""

    global _thread
    if runtime.stopping.is_set():
        raise HTTPException(409, "서버가 종료 중이다")
    with _lock:
        if _job["state"] == "running":
            return False
        _job.update(state="running", step="", error="", log=[])
        _thread = threading.Thread(target=_install, daemon=True, name="oneshot-android-install")
        _thread.start()
    return True


def close_install() -> None:
    """Shutdown: the stop flag cuts a download and the `sdkmanager` job; this waits for the thread."""

    if _thread:
        _thread.join()


def isolated(sdk: Path, home: str, user: Path) -> dict:
    """This server's environment with the owner's `ANDROID_*` variables left out, pointed at one SDK and JDK,
    and with the Android tools' preferences (the debug signing key among them) in `user`."""

    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ANDROID_")}
    env.update(JAVA_HOME=home, ANDROID_HOME=str(sdk), ANDROID_USER_HOME=str(user))
    return env


def build_env() -> dict:
    """What an Android run's builds run in: the gate and the contract tests. Gradle's own caches and the SDK
    tools' preferences stay under `ROOT`, and the JDK comes first on `PATH`."""

    found = status()
    if found["missing"]:
        raise ValueError(missing()[0])
    user, gradle = owned(ROOT / "user"), owned(ROOT / "gradle")
    for made in (user, gradle):
        made.mkdir(parents=True, exist_ok=True)
    env = isolated(Path(found["sdk"]), found["jdk"], user)
    path = next((k for k in env if k.upper() == "PATH"), "PATH")
    env.update(GRADLE_USER_HOME=str(gradle), **{path: os.pathsep.join(
        [str(Path(found["jdk"]) / "bin"), str(Path(found["sdk"]) / "platform-tools"), env.get(path, "")])})
    return env


def sdkmanager(sdk: Path, home: str, wanted: list[str]) -> None:
    """Every package in one `sdkmanager` run, its tree owned by a process job. Its preferences and temporary
    files stay under `ROOT`; each licence it asks for is answered yes, as the card says pressing does."""

    folder, proofs = PIECES[0][2], PIECES[0][3]
    tool, user, scratch = owned(sdk / folder / proofs[0]), owned(ROOT / "user"), owned(ROOT / "tmp")
    for made in (user, scratch):
        made.mkdir(parents=True, exist_ok=True)
    if any(c in str(ROOT) for c in '"%^&|<>!'):
        raise ValueError(f"{ROOT} 에 명령에 쓸 수 없는 글자가 있다")
    env = isolated(sdk, home, user)
    env.update(TEMP=str(scratch), TMP=str(scratch), JAVA_TOOL_OPTIONS=f'-Djava.io.tmpdir="{scratch}"')
    # `sdkmanager` compares each answer line to `y` untrimmed; a piped `echo` block adds a space, a file does not.
    answers = owned(scratch / "licence-answers.txt")
    answers.write_bytes(b"y\n" * ANSWERS)
    packages = " ".join(f'"{p}"' for p in wanted)
    line = f'cmd.exe /d /s /c ""{tool}" --sdk_root="{sdk}" --install {packages} < "{answers}""'
    # A multi-gigabyte download prints little; the three hours and the stop flag bound it, not silence.
    code, text, cut = runtime.gate(None, str(ROOT), runtime.stopping, env, 3 * 3600, None, specs.kill, argv=line,
                                   idle=3 * 3600)
    for out in text.splitlines()[-40:]:
        note(line=out)
    if cut or code:
        raise ValueError(cut or f"sdkmanager 가 {code} 로 끝났다")


def _install() -> None:
    sources, marked = [], False
    try:
        if os.name != "nt":
            raise ValueError("이 버전은 Windows 에서만 Android 도구를 설치한다")
        owned(ROOT).mkdir(parents=True, exist_ok=True)
        owned(ROOT / "installing").write_text(str(time.time()), encoding="utf-8")
        marked = True
        now = status()
        downloads, sdk, home = ROOT / "downloads", owned(ROOT / "sdk"), now["jdk"]
        if not home:
            note("JDK 17 받는 중")
            checksum = fetch(JDK_URL + ".sha256sum.txt").decode().split()[0]
            unpack(download(JDK_URL, downloads / "jdk-17.zip", "sha256", checksum), ROOT / "jdk-17")
            sources.append({"url": JDK_URL, "sha256": checksum})
            home = str(ROOT / "jdk-17")
        if not whole(sdk, PIECES[0][2], PIECES[0][3]):
            note("SDK 명령줄 도구 받는 중")
            url, sha1 = cmdline_tools(fetch(REPOSITORY + "repository2-3.xml"))
            unpack(download(url, downloads / "cmdline-tools.zip", "sha1", sha1), sdk / PIECES[0][2])
            sources.append({"url": url, "sha1": sha1})
        note("플랫폼 도구, 에뮬레이터, 플랫폼, 빌드 도구, 시스템 이미지 받는 중 (수 GB)")
        wanted = ["platform-tools", "emulator", f"platforms;{PLATFORM}", f"build-tools;{BUILD_TOOLS}", IMAGE]
        sdkmanager(sdk, home, wanted)
        sources.append({"sdkmanager": wanted})
        after = status()
        if after["missing"]:
            raise ValueError(f"설치 뒤에도 없다: {', '.join(after['missing'])}")
        owned(ROOT / "receipt.json").write_text(json.dumps({"at": time.time(), "sdk": after["sdk"], "jdk": after["jdk"],
                                                            "pieces": after["pieces"], "sources": sources},
                                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        discard(ROOT / "installing")
        with _lock:
            _job.update(state="done", step="설치를 마쳤다")
    except Exception as exc:   # the owner reads what failed and presses again
        # Only this attempt's own marker; a stop keeps it, so the next server shows the cut install.
        if marked and not runtime.stopping.is_set():
            discard(ROOT / "installing")
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
