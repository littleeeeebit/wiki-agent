"""Local oh-my-mermaid documents for the selected repository."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from common.process import background_options
from .channels import WIKI
from . import query

router = APIRouter()
_lock = threading.Lock()
SOURCE = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".rs", ".kt", ".kts", ".java"}


def scan(root: Path) -> dict:
    """Inventory Git-visible source and resolve local Python/TypeScript imports.

    Generated description/diagram fields belong to this scanner. Other OMM
    fields remain available for a maintainer's context and constraints.
    """
    names = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                           check=True, capture_output=True, timeout=10, **background_options()).stdout
    sources = {}
    for name in sorted(set(names.decode("utf-8").split("\0"))):
        path = root / name
        if (not name or path.suffix not in SOURCE or name.startswith((".omm/", "web/tests/"))
                or path.name.startswith("test_") or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root.resolve())):
            continue
        sources[name] = path.read_text(encoding="utf-8")
    revision = hashlib.sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def group(name):
        parts = name.split("/")
        if parts[0] == "tool":
            return "tool/" + (parts[1] if len(parts) > 2 else "commands")
        if name.startswith("web/src/"):
            module = parts[2] if len(parts) > 3 else "entry"
            return "web/" + ("canvas" if module == "graph" else module)
        if name.startswith("web/src-tauri/"):
            return "desktop"
        return parts[0] if len(parts) > 1 else "entry"

    groups = {}
    for name in sources:
        groups.setdefault(group(name), []).append(name)
    edges = set()
    for name, text in sources.items():
        references = []
        if name.endswith(".py"):
            try:
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, ast.Import):
                        references.extend(alias.name.replace(".", "/") for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        base = name.split("/")[:-1]
                        if node.level:
                            base = base[:len(base) - node.level + 1]
                            prefix = "/".join(base + (node.module or "").split(".")).rstrip("/")
                        else:
                            prefix = (node.module or "").replace(".", "/")
                        references.append(prefix)
                        references.extend(prefix + "/" + alias.name for alias in node.names)
            except SyntaxError:
                pass  # A file mid-edit remains in the inventory until it parses.
        elif name.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
            for ref in re.findall(r'(?:from\s*|import\s*\(\s*|import\s*)[\'\"]([^\'\"]+)[\'\"]', text):
                if ref.startswith("@/"):
                    references.append("src/" + ref[2:])
                    references.append("web/src/" + ref[2:])
                elif ref.startswith("."):
                    candidate = (root / name).parent / ref
                    references.append(candidate.resolve().relative_to(root.resolve()).as_posix()
                                      if candidate.resolve().is_relative_to(root.resolve()) else "")
        for ref in references:
            target = next((p for base in (ref, "src/" + ref, "tool/" + ref)
                           for p in (base, base + ".py", base + "/__init__.py", base + ".ts", base + ".tsx",
                                     base + ".js", base + ".jsx", base + ".mjs", base + "/index.ts", base + "/index.tsx")
                           if p in sources), None)
            if target and group(target) != group(name):
                edges.add((group(name), group(target)))

    nodes = []

    def diagram(children, links):
        def identifier(name):
            readable = name.replace("/", "-")
            return readable if readable != "end" and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", readable) else "n" + hashlib.sha256(name.encode()).hexdigest()[:12]

        lines = ["graph LR"]
        for child, label in children:
            caption = (child + "\\n" + label).replace('"', "#quot;")
            lines.append(f'    {identifier(child)}["{caption}"]')
        for source, target, label in sorted(links):
            lines.append(f'    {identifier(source)} -->|"{label}"| {identifier(target)}')
        return "\n".join(lines) + "\n"

    perspective = "overall-architecture"
    roots = sorted({g.split("/")[0] for g in groups})
    root_links = {(a.split("/")[0], b.split("/")[0], "imports") for a, b in edges
                  if a.split("/")[0] != b.split("/")[0]}
    if root.resolve() == WIKI.resolve():
        root_links = {(a, b, label) for a, b, label in [("web", "tool", "HTTP / SSE"),
                      ("desktop", "web", "desktop window"), ("android", "web", "paired WebView")]
                      if a in roots and b in roots}
    nodes.append({"path": perspective, "description": "Application source architecture, regenerated from Git-visible files.\n",
                  "diagram": diagram([(r, "web/src-tauri/" if r == "desktop" else r + "/") for r in roots],
                                     root_links)})
    for parent in roots:
        members = sorted(g for g in groups if g.split("/")[0] == parent)
        children = [(g.split("/")[-1], "tool/*.py" if g == "tool/commands" else "web/src/*" if g == "web/entry"
                     else "web/src/graph/" if g == "web/canvas"
                     else g.replace("web/", "web/src/", 1) + "/" if g.startswith("web/") else g + "/")
                    for g in members if "/" in g]
        inventory = sorted(p for g in members for p in groups[g])
        nodes.append({"path": f"{perspective}/{parent}",
                      "description": f"# {parent}\n\nSource files ({len(inventory)}):\n\n" + "\n".join(f"- `{p}`" for p in inventory) + "\n",
                      "diagram": diagram(children, [(a.split("/")[-1], b.split("/")[-1], "imports")
                        for a, b in edges if a in members and b in members]) if children else ""})
        for member in members:
            if "/" not in member:
                continue
            nodes.append({"path": f"{perspective}/{member}", "diagram": "",
                          "description": f"# {member}\n\nSource files ({len(groups[member])}):\n\n" +
                          "\n".join(f"- `{p}`" for p in groups[member]) + "\n"})
    directory = root / ".omm"
    if directory.is_symlink():
        raise ValueError(".omm must be a local directory")

    def write_changed(target, content):
        if target.exists() and target.read_text(encoding="utf-8") == content:
            return
        # The live watcher and a frontend build can refresh concurrently.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as temporary:
            temporary.write(content)
        staged = Path(temporary.name)
        try:
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)

    for node in nodes:
        for field, filename in (("description", "description.md"), ("diagram", "diagram.mmd")):
            target = directory / node["path"] / filename
            if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()) or any(p.is_symlink() for p in target.parents if p != root):
                raise ValueError("Architecture output cannot follow symbolic links")
            if field == "diagram" and not node[field]:
                if target.exists():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            write_changed(target, node[field])
        for field in ("context", "constraint", "concern", "todo", "note"):
            target = directory / node["path"] / f"{field}.md"
            if target.is_symlink():
                raise ValueError("Architecture notes cannot follow symbolic links")
            node[field] = target.read_text(encoding="utf-8") if target.exists() else ""
    # Remove only obsolete scanner-owned fields; preserve a maintainer's notes.
    manifest = directory / "generated.json"
    if manifest.is_symlink():
        raise ValueError("Architecture manifest cannot follow symbolic links")
    previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    current = [node["path"] for node in nodes]
    for path in set(previous) - set(current):
        target = directory / path
        if not target.resolve().is_relative_to(directory.resolve()) or target.is_symlink():
            raise ValueError("Invalid architecture manifest path")
        for field in ("description.md", "diagram.mmd"):
            (target / field).unlink(missing_ok=True)
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    content = json.dumps(current, indent=2) + "\n"
    write_changed(manifest, content)
    revision = hashlib.sha256((revision + json.dumps(nodes, sort_keys=True)).encode("utf-8")).hexdigest()
    return {"revision": revision, "files": len(sources), "nodes": nodes}


def read_existing(root: Path) -> dict:
    """Read unowned OMM documents without replacing their diagrams or notes."""
    directory = root / ".omm"
    nodes = []
    for folder in sorted({p.parent for p in directory.rglob("*.md")} |
                         {p.parent for p in directory.rglob("diagram.mmd")}):
        node = {"path": folder.relative_to(directory).as_posix()}
        for field in ("description", "diagram", "context", "constraint", "concern", "todo", "note"):
            target = folder / ("diagram.mmd" if field == "diagram" else f"{field}.md")
            if not target.resolve().is_relative_to(directory.resolve()) or any(
                    p.is_symlink() for p in (target, *target.parents) if p != root):
                raise ValueError("Architecture documents cannot follow symbolic links")
            node[field] = target.read_text(encoding="utf-8") if target.exists() else ""
        nodes.append(node)
    revision = hashlib.sha256(json.dumps(nodes, sort_keys=True).encode("utf-8")).hexdigest()
    return {"revision": revision, "files": None, "nodes": nodes}


def refresh(root: Path = WIKI, *, create: bool = False):
    with _lock:
        try:
            directory = root / ".omm"
            if directory.is_symlink():
                raise ValueError(".omm must be a local directory")
            if directory.exists() and not directory.is_dir():
                raise ValueError(".omm must be a local directory")
            if not directory.exists() and not create and root != WIKI:
                data = {"revision": "missing", "files": 0, "nodes": [], "installed": False}
            elif directory.exists() and not (directory / "generated.json").exists():
                data = {**read_existing(root), "installed": True}
            else:
                data = {**scan(root), "installed": True}
            return {**data, "repo": root.name}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(503, f"구조를 갱신하지 못했다 — {exc}") from exc


def watch(stop: threading.Event):
    while not stop.is_set():
        try:
            refresh()
            root = query.current_repo()
            if root != WIKI:
                refresh(root)
        except HTTPException:
            pass
        stop.wait(5)


@router.get("/api/architecture")
def architecture():
    root = query.current_repo()
    return refresh(root)


@router.post("/api/architecture")
def add_architecture():
    # Bind the write to the checked selection until all generated files exist.
    with query._lock:
        root = query.current_repo()
        return refresh(root, create=True)
