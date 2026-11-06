"""One version-aware boundary for PowerShell text and launches."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading

from . import process
from .shell_catalogue import COMMANDS

INSTALL_CONTROL = "PowerShell 7 설치"
DOWNLOAD = "https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows"
VERSION = "$PSVersionTable.PSVersion.ToString()"


@dataclass(frozen=True)
class PowerShell:
    executable: str
    version: str
    major: int

    @property
    def form(self) -> str:
        return "pwsh7" if self.major >= 7 else "ps5"

    def prefix(self) -> list[str]:
        return [self.executable, "-NoProfile", "-NonInteractive", "-Command"]


_lock = threading.RLock()
_detected: tuple[PowerShell, ...] | None = None


def detect(*, refresh: bool = False) -> tuple[PowerShell, ...]:
    """Probe each executable once, except after an explicit install/re-probe."""
    global _detected
    with _lock:
        if _detected is None or refresh:
            found = []
            for name in ("pwsh", "powershell"):
                executable = shutil.which(name)
                # An installer cannot change its parent's PATH. Its standard
                # machine install remains discoverable from this running server.
                if not executable and refresh and name == "pwsh" and os.name == "nt":
                    installed = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PowerShell/7/pwsh.exe"
                    executable = str(installed) if installed.is_file() else None
                if not executable:
                    continue
                try:
                    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", VERSION],
                                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                                          timeout=10, **process.background_options())
                    version = done.stdout.strip()
                    major = int(version.split(".")[0])
                    if done.returncode == 0 and (name == "pwsh" and major >= 7
                                                 or name == "powershell" and version.split(".")[:2] == ["5", "1"]):
                        found.append(PowerShell(executable, version, major))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    continue
            _detected = tuple(found)
        return _detected


def chosen() -> PowerShell:
    found = detect()
    if not found:
        raise ValueError(f"PowerShell을 찾지 못했다. 연결 화면의 '{INSTALL_CONTROL}'을 사용하세요.")
    return max(found, key=lambda item: item.major)


def seven_only(text: str) -> tuple[str, ...]:
    """Conservatively recognize unsupported syntax, including encoding defaults."""
    # Single-quoted data cannot contain operators. Double-quoted expressions
    # can execute subexpressions, so keep those visible to the check.
    bare = re.sub(r"""'(?:[^']|'')*'|"(?:[^"`]|`.)*"|\#[^\n]*""",
                  lambda match: match[0] if match[0].startswith('"') else "", text)
    patterns = {
        "?.": r"\?\.", "??": r"\?\?", "ternary": r"\?(?![?.])[\s\S]*?:",
        "ForEach-Object -Parallel": r"\bForEach-Object\b[^;|]*-Parallel\b",
        "Test-Json": r"\bTest-Json\b", "Get-Error": r"\bGet-Error\b",
        "-SkipCertificateCheck": r"-SkipCertificateCheck\b", "pipeline chain": r"&&|\|\|",
    }
    found = [name for name, pattern in patterns.items() if re.search(pattern, bare, re.I)]
    for nested in re.finditer(r"\b(?:powershell|pwsh)(?:\.exe)?[\"']?[^\n]*?-(?:Command|c)\s+(['\"])(.*?)\1",
                              text, re.I | re.S):
        found.extend(feature for feature in seven_only(nested[2]) if feature not in found)
    if re.search(r"(?<![>=])>>(?![=&])|(?<![>=])>(?![=&])", bare) or any(
        not re.search(r"(?:^|\s)-Encoding\b", re.sub(r'"(?:[^"`]|`.)*"', "", statement), re.I)
        for statement in re.findall(r"\b(?:Get-Content|Set-Content|Add-Content|Out-File|Tee-Object|tee)\b[^;\n|]*", bare, re.I)
    ):
        found.append("UTF-8 default encoding")
    return tuple(found)


def quote(value: str, form: str) -> str:
    return "'" + value.replace("'", "'\\''" if form == "bash" else "''") + "'"


def command_line(argv: list[str]) -> str:
    if os.name != "nt":
        return shlex.join(argv)
    form = chosen().form
    return resolve("argv", form=form, argv=" ".join(
        word if re.fullmatch(r"[\w./:\\=-]+", word, re.ASCII) else quote(word, form) for word in argv))


def invoked(line: str) -> str:
    """Old adapter commands quoted executables for cmd; PowerShell needs &."""
    quoted = re.compile(r'"(?:[^"`]|`.)*"|\'(?:[^\']|\'\')*\'')
    bare = quoted.sub(lambda match: "x" * len(match[0]), line)
    starts = []
    for match in quoted.finditer(line):
        before = bare[:match.start()].rstrip()
        if before.endswith("&") and not before.endswith("&&"):
            continue
        if (not before or before[-1] in ";|&{(\n") and re.search(
            r"(?:\.(?:exe|cmd|bat|ps1)|[/\\]python[\d.]*)$", match[0][1:-1], re.I
        ):
            starts.append(match.start())
    for start in reversed(starts):
        line = line[:start] + "& " + line[start:]
    return line


def resolve(name: str, *, form: str | None = None, **values: str) -> str:
    form = form or (chosen().form if os.name == "nt" else "bash")
    if form not in ("bash", "pwsh7", "ps5"):
        raise ValueError(f"Unknown shell form: {form}")
    command = getattr(COMMANDS[name], form)
    if command is not None:
        command = re.sub(r"\{(\w+)\}", lambda match: values.get(match[1], match[0]), command)
        if form != "bash" and name in ("gate", "registered-check"):
            command = invoked(command)
    if command is None or form == "ps5" and seven_only(command):
        raise ValueError(f"이 명령은 PowerShell 7이 필요하다. 연결 화면의 '{INSTALL_CONTROL}'을 사용하세요.")
    return command


def launch(name: str, **values: str) -> tuple[str | list[str], bool]:
    """Popen arguments; POSIX keeps its existing shell behaviour."""
    if os.name != "nt":
        return resolve(name, form="bash", **values), True
    ps = chosen()
    line = resolve(name, form=ps.form, **values)
    if re.search(r"\bpowershell(?:\.exe)?[\"']?\s+-", line, re.I) and seven_only(line):
        raise ValueError(f"Windows PowerShell에는 7 전용 명령을 넘길 수 없다. '{INSTALL_CONTROL}'에서 확인하세요.")
    # Preserve native exit codes; a PowerShell statement failure is also a
    # failed check. Both versions support these explicit encoding settings.
    script = ("$ErrorActionPreference = 'Stop'; "
              "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
              "$OutputEncoding = [Console]::OutputEncoding; $global:LASTEXITCODE = 0; "
              "& { " + line + "\n}; if (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE }; exit 1 }; exit $LASTEXITCODE")
    return [*ps.prefix(), script], False


def status() -> dict:
    found = detect() if os.name == "nt" else ()
    selected = max(found, key=lambda item: item.major) if found else None
    return {"windows": os.name == "nt", "executable": selected.executable if selected else None,
            "version": selected.version if selected else None, "major": selected.major if selected else None,
            "installed": [{"executable": item.executable, "version": item.version, "major": item.major}
                          for item in found],
            "needs_install": os.name == "nt" and (selected is None or selected.major < 7)}


def terminal() -> list[str]:
    ps = chosen()
    return [ps.executable, *resolve("terminal", form=ps.form).split()]
