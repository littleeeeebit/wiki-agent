"""Reject uncatalogued PowerShell instructions and unsafe ps5 forms."""

import ast
import json
from pathlib import Path
import re

from markdown_it import MarkdownIt

from common.shell import seven_only
from common.shell_catalogue import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
START = re.compile(
    r"^(?:&\s|\$[\w{].*(?:=|\()|(?:Get|Set|Add|Remove|Select|ForEach|Where|Test|Invoke|Start|Stop|Write|Out|ConvertTo|ConvertFrom|New|Join|Split|Resolve|Copy|Move)-[A-Za-z]+\b|"
    r"(?:python[\d.]*|git|gh|npm|npx|winget|pwsh|powershell|pytest|ruff|rg|adb|codex|claude)(?:\.exe)?\s)")


def instructions(text: str, *, raw: bool = False):
    """Fenced PowerShell and executable inline examples, including prompt strings."""
    rest = text.splitlines(keepends=True)
    for token in MarkdownIt("commonmark").parse(text):
        if token.type == "inline":
            children = list(reversed(token.children or []))
            while children:
                child = children.pop()
                children.extend(reversed(child.children or []))
                if child.type == "code_inline" and START.match(child.content.strip()):
                    yield child.content.strip()
        if token.type != "fence":
            continue
        start, end = token.map
        rest[start:end] = ["\n"] * (end - start)
        if not token.info.split() or token.info.split()[0].casefold() not in ("powershell", "pwsh", "ps1"):
            continue
        pending = ""
        for line in token.content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pending += (" " if pending else "") + line
            if line.endswith(("`", "|")):
                pending = pending.rstrip("`")
                continue
            yield pending
            pending = ""
        if pending:
            yield pending
    rest = "".join(rest)
    for line in rest.splitlines():
        match = re.match(r"\s*(?:In PowerShell|PowerShell):\s*(.+)", line, re.I)
        if match and not re.fullmatch(r"\{\w+\}.*", match[1]):
            yield match[1]
        line = line.strip()
        native = re.match(r"^(?:python[\d.]*|git|gh|npm|npx|winget|pytest|ruff|rg|adb|codex|claude)\s", line)
        if START.match(line) and (not native or raw and seven_only(line)):
            yield line


def normalized(line: str) -> str:
    return re.sub(r"\s+", " ", line.split(" #", 1)[0]).strip()


def catalogued(line: str) -> bool:
    for entry in COMMANDS.values():
        for form in (entry.pwsh7, entry.ps5):
            if not form or any(field in form for field in ("{command}", "{ps5_command}", "{argv}")):
                continue
            if normalized(line) == normalized(form):
                return True
            pattern = re.escape(normalized(form))
            pattern = re.sub(r"\\\{(?:python|script|repo)\\\}",
                             lambda _: r"(?:'(?:[^']|'')*'|\"(?:[^\"`]|`.)*\")", pattern)
            pattern = re.sub(r"\\\{\w+\\\}", lambda _: r"[\w./:<>{}-]+", pattern)
            if re.fullmatch(pattern, normalized(line)):
                return True
    return False


def check(root: Path = ROOT) -> list[tuple[str, str]]:
    findings = []
    for name, entry in COMMANDS.items():
        # Angle-bracket argument placeholders are documentation, not redirection.
        ps5 = re.sub(r"<[^>\n]+>", "placeholder", entry.ps5 or "")
        if entry.ps5 is not None and (features := seven_only(ps5)):
            findings.append(("PowerShell 목록", f"{name}: ps5 — {', '.join(features)}"))
        features = set(seven_only(re.sub(r"<[^>\n]+>", "placeholder", entry.pwsh7)))
        if features != set(entry.seven_only):
            findings.append(("PowerShell 목록", f"{name}: seven_only 선언이 다르다 — {sorted(features)}"))
    for directory in ("tool/prompts", "tool/main", "docs"):
        for path in sorted((root / directory).rglob("*")):
            if path.suffix not in (".md", ".py", ".json", ".txt"):
                continue
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".py":
                tree = ast.parse(text)
                texts = [node.value for node in ast.walk(tree)
                         if isinstance(node, ast.Constant) and isinstance(node.value, str)]
                texts += ["".join(part.value if isinstance(part, ast.Constant) else "{value}"
                                  for part in node.values) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)]
            elif path.suffix == ".json":
                texts = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
                texts = [json.loads('"' + value + '"') for value in texts]
            else:
                texts = [text]
            for command in dict.fromkeys(command for text in texts
                                         for command in instructions(text, raw=path.suffix != ".md")):
                if not catalogued(command):
                    findings.append(("PowerShell 목록", f"{path.relative_to(root).as_posix()}: 미등록 명령 — {command}"))
    return findings


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    findings = check()
    for _, message in findings:
        print(message)
    print(f"PowerShell catalogue: {len(findings)} findings")
    raise SystemExit(bool(findings))
