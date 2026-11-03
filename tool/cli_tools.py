"""`fd` and `jq`, the commands `operator/command-guide` sends the agent to for finding files and
reading JSON, installed with winget (`docs/plans/agent-commands/0-overview.md`, step 4).

Setup installs them (`setup_agents.py --global`). A command that needs one this session cannot
find runs it from where it was put, since a session keeps the PATH it started with; one not
installed at all starts an install in the background, once (`command_repair`).

winget updates its source by adding an app package first, and on a PC whose app deployment
stalls (seen 2026-11-04: the Add operation starts and never ends) it waits forever. So winget
gets `WAIT` seconds, and after that the official release file, checked against a pinned digest,
goes into `BIN`.
"""

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen

PACKAGES = {"fd": "sharkdp.fd", "jq": "jqlang.jq"}
WINGET = ["install", "--exact", "--silent", "--disable-interactivity",
          "--accept-source-agreements", "--accept-package-agreements", "--id"]
# ponytail: x64 only; Windows on ARM runs it emulated, pin the arm64 assets if that ever matters.
RELEASES = {
    "fd": ("https://github.com/sharkdp/fd/releases/download/v10.5.0/fd-v10.5.0-x86_64-pc-windows-msvc.zip",
           "a227701b8551c35a9931d9f6da75503cf86d88e182d71fb849a70864c5d57cd7"),
    "jq": ("https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-windows-amd64.exe",
           "a6fc67fedaf9128a3309a1e2ebb8b986aeccf70122ee46d2cb4849e423f0c627"),
}
WAIT = 180   # seconds winget gets before the release file is fetched instead
HELD = 600   # seconds a started background install keeps others from starting another
_stalled = False   # winget timed out once in this process


def bin_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "wiki-agent" / "bin"


def found(name: str) -> str:
    """Where `name` runs from: PATH, or the folders a running session's PATH misses; `""`."""

    if path := shutil.which(name):
        return path
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    for path in [winget / "Links" / f"{name}.exe", bin_dir() / f"{name}.exe",
                 *sorted((winget / "Packages").glob(f"{PACKAGES[name]}_*/**/{name}.exe"))]:
        if path.is_file():
            return path.as_posix()
    return ""


def downloaded(name: str) -> str:
    """Fetch the pinned release of `name` into `bin_dir()`; the executable's path."""

    url, expected = RELEASES[name]
    with urlopen(url, timeout=60) as response:
        payload = response.read(50 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != expected:
        raise RuntimeError(f"{url} 의 SHA256 이 고정값과 다르다")
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = next(m for m in archive.namelist() if m.rsplit("/", 1)[-1] == f"{name}.exe")
            payload = archive.read(member)
    target = bin_dir() / f"{name}.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    partial.write_bytes(payload)
    partial.replace(target)
    return target.as_posix()


def fetched(name: str) -> str:
    """Install `name`: winget for `WAIT` seconds, then the release file; one line for the person."""

    global _stalled
    how = "winget"
    if (winget := shutil.which("winget")) and not _stalled:
        try:
            subprocess.run([winget, *WINGET, PACKAGES[name]], capture_output=True, timeout=WAIT)
        except (subprocess.TimeoutExpired, OSError):
            _stalled = True   # the next tool would wait on the same stuck source
    if _stalled:
        how = "winget 이 멈춰 릴리스 파일"
    if path := found(name):
        return f"{name}: 설치됨 ({path}, {how})"
    try:
        return f"{name}: 설치됨 ({downloaded(name)}, 릴리스 파일)"
    except (OSError, RuntimeError, StopIteration, zipfile.BadZipFile) as error:
        return f"{name}: 설치 실패 — {error}. `winget install --id {PACKAGES[name]} -e` 로 설치하라"


def started(name: str) -> bool:
    """Start `fetched(name)` in the background unless one started within `HELD`; whether one runs."""

    if sys.platform != "win32":
        return False   # both installs are Windows builds; elsewhere the package manager has them
    marker = Path(tempfile.gettempdir()) / f"wiki-agent-install-{name}"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < HELD:
            return True
        marker.write_text(str(os.getpid()), encoding="utf-8")
        subprocess.Popen([sys.executable, str(Path(__file__)), name], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError:
        return False
    return True


def install(check: bool = False) -> list[str]:
    """Setup's step: each tool installed, found, or why not; one line each for the person."""

    if sys.platform != "win32":
        return [f"{name}: Windows 가 아니다 — 패키지 관리자로 설치하라" for name in PACKAGES if not found(name)]
    return [f"{name}: 있음 ({path})" if (path := found(name))
            else f"{name}: 없음 — `winget install --id {PACKAGES[name]} -e` 로 설치하라" if check
            else fetched(name) for name in PACKAGES]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for tool in sys.argv[1:]:
        print(fetched(tool))
