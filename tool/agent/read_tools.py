"""Codex's shell-free counterpart of Claude's Read, Glob and Grep."""

import fnmatch
import subprocess
from pathlib import Path


PROFILE = "repo-read-v1"
SPECS = []
for name, description, properties, required in (
    ("repo_read", "Read a tracked UTF-8 source file, with numbered lines. No shell or private files.",
     {"path": {"type": "string"}, "offset": {"type": "integer", "minimum": 1},
      "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["path"]),
    ("repo_glob", "List tracked source paths matching a glob; * matches across directories.",
     {"pattern": {"type": "string"}}, ["pattern"]),
    ("repo_grep", "Search tracked UTF-8 source for literal text, returning path:line matches.",
     {"text": {"type": "string"}, "pattern": {"type": "string"}}, ["text"]),
):
    SPECS.append({"type": "function", "name": name, "description": description,
                  "inputSchema": {"type": "object", "properties": properties,
                                  "required": required, "additionalProperties": False}})


def private(path: Path) -> bool:
    name = path.name.lower()
    return (any(p.lower() in {".git", ".codex", ".claude", ".sandbox-secrets"} for p in path.parts)
            or (name.startswith(".env") and not name.endswith((".example", ".sample", ".template")))
            or name.endswith((".pem", ".key", ".p12", ".pfx"))
            or name in {"auth.json", "credentials.json", "sandbox_users.json", "id_rsa", "id_ed25519"})


def call(repo: Path, tool: str, args: dict) -> str:
    """Only fixed git inventory and bounded file reads; no model command is executed."""
    spec = next((s for s in SPECS if s["name"] == tool), None)
    if spec is None or not isinstance(args, dict):
        raise ValueError("지원하지 않는 읽기 도구다")
    schema = spec["inputSchema"]
    if set(args) - schema["properties"].keys() or any(k not in args for k in schema["required"]):
        raise ValueError("읽기 도구 인수가 잘못됐다")
    for key, value in args.items():
        if schema["properties"][key]["type"] == "string":
            if not isinstance(value, str) or not value or len(value) > 4096:
                raise ValueError("검색어와 경로는 비어 있지 않은 문자열이어야 한다")
        elif type(value) is not int or not 1 <= value <= (500 if key == "limit" else 10_000_000):
            raise ValueError("줄 범위가 잘못됐다")
    root = repo.resolve()
    inventory = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                               capture_output=True, timeout=10, check=True).stdout.decode("utf-8").split("\0")
    paths = {name: root / name for name in inventory if name and not private(Path(name))}

    def lines(path: Path) -> list[str]:
        resolved = path.resolve()
        if root not in resolved.parents or private(resolved.relative_to(root)) or not resolved.is_file():
            raise ValueError("리뷰 범위 밖 파일이다")
        with resolved.open("rb") as stream:
            data = stream.read(2_000_001)
        if len(data) > 2_000_000 or b"\0" in data:
            raise ValueError("2 MB 이하 텍스트 파일만 읽을 수 있다")
        return data.decode("utf-8").splitlines()

    if tool == "repo_read":
        target = Path(args["path"])
        target = (target if target.is_absolute() else root / target).absolute()
        # Match the lexical tracked path before resolving symlinks.
        path = next((p for p in paths.values() if p.absolute() == target), None)
        if path is None:
            raise ValueError("추적된 소스 파일만 읽을 수 있다")
        content = lines(path)
        start, limit = args.get("offset", 1), args.get("limit", 200)
        out = [f"{i}: {line}" for i, line in enumerate(content, 1) if start <= i < start + limit]
        out.append(f"(전체 {len(content)}줄; 다음 offset={start + limit})")
        result = "\n".join(out)
        return result if len(result) <= 60_000 else result[:60_000] + "\n(출력 제한: limit을 줄여 다시 읽어라)"
    out = []
    for name, path in sorted(paths.items()):
        if not fnmatch.fnmatchcase(name, args.get("pattern", "*")):
            continue
        if tool == "repo_glob":
            out.append(name)
        else:
            try:
                content = lines(path)
            except (OSError, UnicodeError, ValueError):
                continue
            out.extend(f"{name}:{i}: {line[:1000]}" for i, line in enumerate(content, 1) if args["text"] in line)
        if len(out) >= 200:
            return "\n".join(out[:200])[:60_000] + "\n(결과 제한: 검색 범위를 좁혀라)"
    result = "\n".join(out)
    if len(result) > 60_000:
        return result[:60_000] + "\n(출력 제한: 검색 범위를 좁혀라)"
    return result or "(결과 없음)"
