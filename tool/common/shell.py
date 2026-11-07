"""One version-aware boundary for PowerShell text and launches."""

from dataclasses import dataclass, replace
import base64
import binascii
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import uuid

from . import process
from .powershell_aliases import Aliases
from .powershell_syntax import Refusal, arguments, bound_target, command_position, enclosed, syntax
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
        raise Refusal(f"PowerShell을 찾지 못했다. 연결 화면의 '{INSTALL_CONTROL}'을 사용하세요.")
    return max(found, key=lambda item: item.major)


def command_syntax(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Expose literal call targets, including transparent parenthesized targets."""
    bare, strings = syntax(text)
    for start, end in strings:
        if text[start] not in "'\"" or bare[start:end].strip() != "0":
            continue
        left, right = start, end
        before = bare[:left].rstrip()
        while before.endswith("("):
            after = bare[right:].lstrip()
            if not after.startswith(")"):
                break
            left = len(before) - 1
            right = len(bare) - len(after) + 1
            before = bare[:left].rstrip()
        word = text[start + 1:end - 1].replace("\\", "/").rsplit("/", 1)[-1]
        call = before.endswith("&") and not before.endswith("&&") or before.endswith(".") and command_position(before[:-1])
        if call and re.fullmatch(
            r"(?:Get-Error|Test-Json|powershell|pwsh)(?:\.exe)?", word, re.I
        ):
            bare = bare[:left] + word + " " * (right - left - len(word)) + bare[right:]
    return bare, strings


@dataclass(frozen=True)
class Invocation:
    program: str | None
    target_start: int
    target_end: int
    kind: str
    name: str | None = None


def invocations(text: str):
    """Retain literal children, literal blocks and unresolved executable targets."""
    bare, _ = command_syntax(text)
    masked, spans = syntax(text)
    quoted = dict(spans)
    found = {}

    def record(start):
        cursor, depth = start, 0
        while cursor < len(text):
            if cursor in quoted:
                cursor = quoted[cursor]
                continue
            if text[cursor] == "`":
                cursor += 2
                continue
            char = masked[cursor]
            if not depth and (char.isspace() or char in ";|&)]}<>"):
                break
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            cursor += 1
        value = text[start:cursor].strip()
        grouped = False
        while enclosed(value, "(", ")"):
            grouped = True
            value = value[1:-1].strip()
        variable = re.fullmatch(r'\$(\w+)|"\$(\w+)"', value)
        bound = variable is not None
        if bound:
            value = bound_target(text[:start], variable[1] or variable[2])
            if value is None:
                return Invocation(None, start, cursor, "unresolved")
            grouped = False
        if enclosed(value, "{", "}"):
            return Invocation(None, start, cursor, "scriptblock")
        if grouped and syntax(value)[1] != [(0, len(value))]:
            return Invocation(None, start, cursor, "unresolved")
        try:
            words = arguments(value)
        except Refusal:
            words = []
        if len(words) != 1:
            return Invocation(None, start, cursor, "unresolved")
        program = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        return Invocation(program, start, cursor, "bound" if bound else "literal", words[0])

    for call in re.finditer(r"(?<!&)&(?!&)|\.(?=\s)", bare):
        if command_position(bare[:call.start()]):
            start = call.end()
            while start < len(bare) and bare[start].isspace():
                start += 1
            found[start] = record(start)
    target = r"(?:(?:[A-Za-z]:|\.{1,2})?[/\\]+)?[A-Za-z_][\w./:\\-]*(?=\s|$)"
    for match in re.finditer(target, bare, re.I):
        if command_position(bare[:match.start()]):
            found.setdefault(match.start(), record(match.start()))
    aliases = Aliases(text)
    for start in sorted(found):
        item = found[start]
        if item.name is not None:
            program, aliased = aliases.resolve(item.name, start)
            if aliased:
                item = replace(item, program=program, kind="alias" if program is not None else "unresolved")
            aliases.define(program, text[item.target_end:], start)
        yield item


def nested_commands(text: str):
    """Read literal command bodies passed to an explicitly named PowerShell."""
    options = {name[:length].lower() for name in ("Command", "EncodedCommand") for length in range(1, len(name) + 1)} | {"ec"}
    for nested in invocations(text):
        if nested.kind == "unresolved":
            raise Refusal("PowerShell 실행 대상을 확정할 수 없다. 리터럴 실행 대상을 사용하세요.")
        if nested.program not in ("powershell", "pwsh"):
            continue
        argv = arguments(text[nested.target_end:])
        for index, value in enumerate(argv):
            option = value[1:].lower() if value.startswith(("-", "/")) else ""
            if option not in options:
                continue
            rest = argv[index + 1:]
            body = " ".join(rest)
            if option.startswith("e"):
                try:
                    body = base64.b64decode(re.sub(r"\s+", "", rest[0] if rest else ""), validate=True).decode("utf-16-le")
                except (binascii.Error, UnicodeError) as exc:
                    raise Refusal("인코딩된 PowerShell 명령을 읽을 수 없다.") from exc
            yield nested.program, body
            break


def classic_seven_only(text: str) -> bool:
    """An explicit Windows PowerShell child stays on 5.1 even under a 7 host."""
    return any(program == "powershell" and seven_only(body) or classic_seven_only(body)
               for program, body in nested_commands(text))


def seven_only(text: str) -> tuple[str, ...]:
    """Conservatively recognize unsupported syntax, including encoding defaults."""
    bare, _ = command_syntax(text)
    patterns = {
        "?.": r"\?\.", "??": r"\?\?", "ternary": r"(?<!\$)\?(?![?.])[^;{}]*?:",
        "ForEach-Object -Parallel": r"\bForEach-Object\b[^;|]*-Parallel\b",
        "Test-Json": r"\bTest-Json\b", "Get-Error": r"\bGet-Error\b",
        "-SkipCertificateCheck": r"-SkipCertificateCheck\b", "pipeline chain": r"&&|\|\|",
    }
    found = [name for name, pattern in patterns.items() if re.search(pattern, bare, re.I)]
    for _, body in nested_commands(text):
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
    """Repair legacy gate command positions; grouped and assigned paths remain data."""
    bare, quoted = syntax(line)
    starts = []
    for start, end in quoted:
        if line[start] not in "'\"":
            continue
        before = bare[:start].rstrip()
        if before.endswith("&") and not before.endswith("&&"):
            continue
        tail = bare[end:].lstrip(" \t")
        argument = re.match(r"[ \t]+\S", bare[end:]) and tail[0] not in ";|&})],+*/.?\n" and not re.match(
            r"-(?:[ic]?(?:eq|ne|lt|le|gt|ge|like|notlike|match|notmatch|contains|notcontains|in|notin|is|isnot)|"
            r"as|replace|split|join|and|or|xor|f|band|bor|bxor|shl|shr)\b|-(?=\s|\d|$)", tail, re.I)
        depth = sum(char in "([{" for char in before) - sum(char in ")]}" for char in before)
        standalone = (depth == 0 and re.match(r"[ \t\r]*(?:$|[;|\n]|&&)", bare[end:])
                      and not before.endswith(("=", ",", "`")))
        if (argument or standalone) and re.search(r"(?:^|[;|&{\n])\s*$", bare[:start]) and re.search(
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
    entry = COMMANDS[name]
    command = getattr(entry, form)
    if command is None and entry.example:
        raise Refusal("PowerShell 실행 대상을 확정할 수 없다. 리터럴 실행 대상을 사용하세요.")
    if command is not None:
        if form != "bash" and name in ("gate", "registered-check"):
            values["command"] = invoked(values["command"])
        if form == "ps5" and name in ("gate", "registered-check"):
            values["ps5_command"] = ps5_chains(values["command"])
        command = re.sub(r"\{(\w+)\}", lambda match: values.get(match[1], match[0]), command)
    if command is None or form == "ps5" and seven_only(command):
        raise Refusal(f"이 명령은 PowerShell 7이 필요하다. 연결 화면의 '{INSTALL_CONTROL}'을 사용하세요.")
    if form != "bash" and classic_seven_only(command):
        raise Refusal(f"Windows PowerShell에는 7 전용 명령을 넘길 수 없다. '{INSTALL_CONTROL}'에서 확인하세요.")
    return command


def launch(name: str, **values: str) -> tuple[str | list[str], bool]:
    """Popen arguments; POSIX keeps its existing shell behaviour."""
    if os.name != "nt":
        return resolve(name, form="bash", **values), True
    ps = chosen()
    line = resolve(name, form=ps.form, **values)
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
