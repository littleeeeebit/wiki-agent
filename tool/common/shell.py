"""One version-aware boundary for PowerShell text and launches."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import uuid

from . import process
from .powershell_syntax import syntax
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
    bare, strings = syntax(text)
    for start, end in strings:
        before = bare[:start].rstrip()
        word = text[start + 1:end - 1].replace("\\", "/").rsplit("/", 1)[-1]
        if before.endswith("&") and not before.endswith("&&") and re.fullmatch(
            r"(?:Get-Error|Test-Json|powershell|pwsh)(?:\.exe)?", word, re.I
        ):
            bare = bare[:start] + word + " " * (end - start - len(word)) + bare[end:]
    patterns = {
        "?.": r"\?\.", "??": r"\?\?", "ternary": r"(?<!\$)\?(?![?.])[^;{}]*?:",
        "ForEach-Object -Parallel": r"\bForEach-Object\b[^;|]*-Parallel\b",
        "Test-Json": r"\bTest-Json\b", "Get-Error": r"\bGet-Error\b",
        "-SkipCertificateCheck": r"-SkipCertificateCheck\b", "pipeline chain": r"&&|\|\|",
    }
    found = [name for name, pattern in patterns.items() if re.search(pattern, bare, re.I)]
    bodies = dict(strings)
    wrapper = r"(?:^|[;&|{(\n])\s*(?:&\s*)?(?:[^\s;&|(){}]+[/\\])?(?:powershell|pwsh)(?:\.exe)?\s+[^;\n|]*?-(?:Command|c)\s+"
    for nested in re.finditer(wrapper, bare, re.I):
        if (end := bodies.get(nested.end())) is not None:
            value = text[nested.end():end]
            if value.startswith("@"):
                body = value.partition("\n")[2].rsplit("\n", 1)[0]
            else:
                body = value[1:-1].replace("''", "'") if value[0] == "'" else value[1:-1].replace('`"', '"').replace("``", "`")
            found.extend(feature for feature in seven_only(body) if feature not in found)
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
    """Repair legacy quoted programs with arguments; expressions remain data."""
    bare, quoted = syntax(line)
    starts = []
    for start, end in quoted:
        if line[start] not in "'\"":
            continue
        before = bare[:start].rstrip()
        if before.endswith("&") and not before.endswith("&&"):
            continue
        tail = bare[end:].lstrip(" \t")
        argument = re.match(r"[ \t]+\S", bare[end:]) and tail[0] not in ";|&})],+*/.\n" and not re.match(
            r"-(?:[ic]?(?:eq|ne|lt|le|gt|ge|like|notlike|match|notmatch|contains|notcontains|in|notin|is|isnot)|"
            r"as|replace|split|join|and|or|xor|f|band|bor|bxor|shl|shr)\b|-(?=\s|\d|$)", tail, re.I)
        if argument and re.search(r"(?:^|[;|&{\n])\s*$", bare[:start]) and re.search(
            r"(?:\.(?:exe|cmd|bat|ps1)|[/\\]python[\d.]*)$", line[start + 1:end - 1], re.I
        ):
            starts.append(start)
    for start in reversed(starts):
        line = line[:start] + "& " + line[start:]
    return line


def ps5_chains(text: str) -> str:
    """Lower statement-level pipeline chains, retaining left-to-right short circuiting."""
    bare, _ = syntax(text)
    segments, operators, operands = [], [], []
    depth, start, cursor = 0, 0, 0

    def finish(end):
        nonlocal start, operators, operands
        if operators and bare[start:end].strip():
            operands.append(text[start:end])
            operands = [operand.rstrip().removesuffix("`") for operand in operands]
            token = uuid.uuid4().hex
            ok, code = f"$wiki_ok_{token}", f"$wiki_code_{token}"
            capture = f"{ok} = $?; {code} = if ({ok}) {{ 0 }} elseif ($LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 1 }};"
            result = operands[0] + "\n" + capture
            for operator, operand in zip(operators, operands[1:]):
                condition = ok if operator == "&&" else "-not " + ok
                result += f"\nif ({condition}) {{ {operand}\n{capture} }}"
            result += f"\n$global:LASTEXITCODE = {code}; if (-not {ok}) {{ Write-Error 'Command failed' -ErrorAction SilentlyContinue }}"
            segments.append(result)
        else:
            segments.append("".join(operand + operator for operand, operator in zip(operands, operators)) + text[start:end])
        start, operators, operands = end, [], []

    while cursor < len(bare):
        char = bare[cursor]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif not depth and bare[cursor:cursor + 2] in ("&&", "||"):
            if not bare[start:cursor].strip():
                return text
            operands.append(text[start:cursor])
            operators.append(bare[cursor:cursor + 2])
            start = cursor + 2
            cursor += 1
        elif not depth and (char == ";" or char == "\n" and bare[start:cursor].strip()
                            and not text[:cursor].rstrip("\r").endswith("`")):
            finish(cursor)
            segments.append(char)
            start = cursor + 1
        cursor += 1
    finish(len(text))
    return "".join(segments)


def resolve(name: str, *, form: str | None = None, **values: str) -> str:
    form = form or (chosen().form if os.name == "nt" else "bash")
    if form not in ("bash", "pwsh7", "ps5"):
        raise ValueError(f"Unknown shell form: {form}")
    command = getattr(COMMANDS[name], form)
    if command is not None:
        if form == "ps5" and name in ("gate", "registered-check"):
            values["ps5_command"] = ps5_chains(values["command"])
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
