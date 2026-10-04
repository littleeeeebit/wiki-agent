"""Read OMM documents; analyze behavior with a native model CLI on request/merge."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
import yaml

from agent import ChatSession, cli_command
from common import errorlog
from common.process import background_options
from .channels import WIKI
from . import query

router = APIRouter()
_lock = threading.Lock()
FIELDS = ("description", "diagram", "context", "constraint", "concern", "todo", "note")
DOCUMENT_FILES = {"meta.yaml", "config.yaml", "diagram.mmd", *(f"{field}.md" for field in FIELDS)}


def document_state(directory: Path) -> dict:
    """Fence staged publication against native writes and hierarchy changes."""
    if directory.resolve() != directory.parent.resolve() / directory.name:
        raise ValueError("Architecture documents cannot follow symbolic links")
    state = {}
    for source in directory.rglob("*"):
        relative = source.relative_to(directory)
        if source.is_symlink() or source.resolve() != directory.resolve() / relative:
            raise ValueError("Architecture documents cannot follow symbolic links")
        state[relative.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() and (
            source.name in DOCUMENT_FILES or source.name in ("analysis.json", "generated.json")) else None
    return state


def read_existing(root: Path) -> dict:
    """Reading never runs a model or rewrites a maintainer's documents."""
    directory = root / ".omm"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError(".omm must be a local directory")
    nodes = []
    for folder in sorted({p.parent for p in directory.rglob("*.md")} |
                         {p.parent for p in directory.rglob("diagram.mmd")}):
        node = {"path": folder.relative_to(directory).as_posix()}
        for field in FIELDS:
            target = folder / ("diagram.mmd" if field == "diagram" else f"{field}.md")
            if not target.resolve().is_relative_to(directory.resolve()) or any(
                    p.is_symlink() for p in (target, *target.parents) if p != root):
                raise ValueError("Architecture documents cannot follow symbolic links")
            node[field] = target.read_text(encoding="utf-8") if target.exists() else ""
        nodes.append(node)
    revision = hashlib.sha256(json.dumps(nodes, sort_keys=True).encode("utf-8")).hexdigest()
    return {"revision": revision, "files": None, "nodes": nodes, "installed": directory.is_dir(), "repo": root.name}


def analyze(root: Path, model: str | None, halt: threading.Event) -> list[dict]:
    """The model reads source; only our OMM writer may change architecture files."""
    prompt = (WIKI / "tool/prompts/architecture-scan.md").read_text(encoding="utf-8")
    chat = ChatSession(root, tools="Read,Glob,Grep", system=prompt, model=model)
    ended = threading.Event()

    def cancel():
        while not ended.wait(0.1):
            if halt.is_set():
                chat.stop(halt)
                return

    watcher = threading.Thread(target=cancel, daemon=True)
    watcher.start()
    final = ""
    try:
        for event in chat.say("Analyze this repository and return the architecture JSON. Read the actual execution paths.", halt):
            if event.kind == "error" or (event.kind == "done" and event.meta.get("error")):
                raise ValueError(event.text or "Architecture analysis failed")
            if event.kind == "done":
                final = event.text
        if halt.is_set():
            raise ValueError("Architecture analysis cancelled")
        match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", final, re.S)
        return json.loads(match.group(1) if match else final)["nodes"]
    finally:
        ended.set()
        chat.close()
        watcher.join()


def write_nodes(root: Path, nodes: list[dict], halt: threading.Event) -> None:
    """Validate all paths and stage CLI output before replacing generated fields."""
    read_existing(root)
    if not isinstance(nodes, list) or not nodes or len(nodes) > 120:
        raise ValueError("Architecture requires 1–120 described elements")
    paths = set()
    directory = root / ".omm"
    original = document_state(directory)
    manifest = directory / "analysis.json"
    legacy = directory / "generated.json"
    previous = []
    for record in (legacy, manifest):
        if record.is_symlink():
            raise ValueError("Architecture manifest cannot follow symbolic links")
        if record.exists():
            owned = json.loads(record.read_text(encoding="utf-8"))
            if not isinstance(owned, list) or any(not isinstance(p, str) or not re.fullmatch(
                    r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", p) for p in owned):
                raise ValueError("Invalid architecture ownership manifest")
            previous.extend(owned)
    for node in nodes:
        if (not isinstance(node, dict) or not isinstance(node.get("path"), str)
                or not re.fullmatch(r"[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*", node["path"])
                or node["path"] in paths or not isinstance(node.get("description"), str)
                or not node["description"].strip()
                or any(not isinstance(node.get(field, ""), str) for field in FIELDS)):
            raise ValueError("Invalid or duplicate architecture element")
        paths.add(node["path"])
    if "overall-architecture" not in paths:
        raise ValueError("Architecture requires an overall perspective")
    for path in paths | set(previous):
        if path in paths and "/" in path and path.rsplit("/", 1)[0] not in paths:
            raise ValueError("Architecture child requires a described parent")
        folder = root / ".omm" / path
        if any(p.is_symlink() for p in (folder, *folder.parents) if p != root):
            raise ValueError("Architecture output cannot follow symbolic links")
    for node in nodes:
        diagram = node.get("diagram", "")
        if re.search(r"(?m)^\s*(?:graph|flowchart|subgraph|end)\s*\[", diagram):
            raise ValueError("Mermaid node IDs cannot use reserved keywords")
        for child in re.findall(r"(?m)^\s*([a-z][a-z0-9-]*)\s*\[", diagram):
            if f"{node['path']}/{child}" not in paths:
                raise ValueError("Every diagram component requires a described child")
    command = cli_command("omm")
    git = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=root, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=10, **background_options())
    env = {**os.environ, **({"GIT_DIR": git.stdout.strip(), "GIT_WORK_TREE": str(root)}
                           if not git.returncode else {})}
    with tempfile.TemporaryDirectory(prefix="wiki-omm-") as temporary:
        stage = Path(temporary)
        if directory.exists():
            if any(source.is_symlink() for source in directory.rglob("*")):
                raise ValueError("Architecture staging cannot follow symbolic links")
            shutil.copytree(directory, stage / ".omm")
            if document_state(stage / ".omm") != original:
                raise ValueError("Architecture documents changed during staging; retry the scan")
        removed = []
        for path in sorted(set(previous) - paths, key=lambda p: (p.count("/"), p), reverse=True):
            folder = stage / ".omm" / path
            for filename in ("description.md", "diagram.mmd"):
                (folder / filename).unlink(missing_ok=True)
            if folder.is_dir() and all(p.name == "meta.yaml" and p.is_file() for p in folder.iterdir()):
                subprocess.run([*command, "delete", path], cwd=stage, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", check=True, timeout=30,
                               **background_options())
                removed.append(path)
        for node in nodes:
            if not node.get("diagram"):
                (stage / ".omm" / node["path"] / "diagram.mmd").unlink(missing_ok=True)
        for node in nodes:
            for field in FIELDS:
                text = node.get(field, "")
                target = root / ".omm" / node["path"] / f"{field}.md"
                if field not in ("description", "diagram") and target.exists():
                    continue
                if not text:
                    continue
                existing = directory / node["path"] / ("diagram.mmd" if field == "diagram" else f"{field}.md")
                if ((existing.parent / "meta.yaml").exists() and existing.exists()
                        and existing.read_text(encoding="utf-8") == text):
                    continue
                if halt.is_set():
                    raise ValueError("Architecture analysis cancelled")
                subprocess.run([*command, "write", node["path"], field, "-"], input=text,
                               cwd=stage, text=True, encoding="utf-8", errors="replace",
                               env=env, capture_output=True, check=True, timeout=30, **background_options())
        # Native delete removes the directory but leaves the parent's registry.
        # Retained maintainer-only elements still belong to that registry.
        for metadata in (stage / ".omm").rglob("meta.yaml"):
            content = yaml.safe_load(metadata.read_text(encoding="utf-8"))
            if not isinstance(content, dict):
                raise ValueError("Invalid native architecture metadata")
            children = sorted(p.name for p in metadata.parent.iterdir() if p.is_dir() and not p.name.startswith("."))
            if content.get("children") != children:
                content["children"] = children
                write_changed(metadata, yaml.safe_dump(content, sort_keys=False, allow_unicode=True))
        checked = subprocess.run([*command, "validate"], cwd=stage, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=30,
                                 **background_options())
        if checked.returncode:
            raise ValueError(f"Invalid architecture diagram: {checked.stdout} {checked.stderr}")
        if document_state(directory) != original:
            raise ValueError("Architecture documents changed during staging; retry the scan")
        for source in (stage / ".omm").rglob("*"):
            if not source.is_file() or source.name not in DOCUMENT_FILES:
                continue
            target = root / ".omm" / source.relative_to(stage / ".omm")
            if halt.is_set():
                raise ValueError("Architecture analysis cancelled")
            if target.is_symlink():
                raise ValueError("Architecture output cannot follow symbolic links")
            if target.exists() and source.name not in ("description.md", "diagram.mmd", "meta.yaml"):
                continue
            content = source.read_text(encoding="utf-8")
            if target.exists() and target.read_text(encoding="utf-8") == content:
                continue
            write_changed(target, content)
    for path in sorted(set(previous) - paths, key=lambda p: (p.count("/"), p), reverse=True):
        for filename in ("description.md", "diagram.mmd"):
            (directory / path / filename).unlink(missing_ok=True)
        folder = directory / path
        if path in removed and folder.is_dir() and all(p.name == "meta.yaml" and p.is_file() for p in folder.iterdir()):
            (folder / "meta.yaml").unlink(missing_ok=True)
            folder.rmdir()
    for node in nodes:
        if not node.get("diagram"):
            (directory / node["path"] / "diagram.mmd").unlink(missing_ok=True)
    write_changed(manifest, json.dumps(sorted(paths), indent=2) + "\n")
    legacy.unlink(missing_ok=True)


def write_changed(target: Path, content: str) -> None:
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as stream:
        stream.write(content)
    staged = Path(stream.name)
    try:
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def scan(root: Path, *, model: str | None = None, halt: threading.Event | None = None) -> dict:
    root = root.resolve()
    halt = halt or threading.Event()
    with _lock:
        write_nodes(root, analyze(root, model, halt), halt)
        return read_existing(root)


def refresh(root: Path = WIKI, *, create: bool = False):
    try:
        return scan(root) if create and not (root / ".omm").exists() else read_existing(root)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, f"구조를 읽거나 생성하지 못했다 — {exc}") from exc


def after_merge(root: Path, spec: dict) -> None:
    """Queue once for this merge, after cleanup has synchronized the base."""
    from . import specs

    if (root / ".omm").is_dir() and not spec.get("architecture_refresh"):
        specs.update(spec["repo"], spec["id"], architecture_refresh={"state": "pending"})


def watch(stop: threading.Event):
    from . import channels, specs, work

    while not stop.wait(5):
        for directory in specs.SPECS.glob("*"):
            root = channels.repo_for(directory.name)
            if root is None:
                continue
            for spec in specs.listing(directory.name):
                if stop.is_set():
                    return
                if ((spec.get("architecture_refresh") or {}).get("state") != "pending"
                        or spec["state"] != "머지됨" or not spec.get("cleanup_complete")):
                    continue
                try:
                    release = query.hold(work._busy, work._lock, str(root), "", kind="turn")
                except HTTPException:
                    continue
                try:
                    branch = specs.sh(["git", "branch", "--show-current"], root)
                    dirty = specs.sh(["git", "status", "--porcelain", "--", ".", ":(exclude).omm"], root)
                    if (branch.returncode or dirty.returncode or dirty.stdout.strip()
                            or branch.stdout.strip() != spec["merge"]["base"]):
                        continue
                    scan(root, model=(spec.get("cell") or {}).get("model") or None, halt=stop)
                    specs.update(spec["repo"], spec["id"], architecture_refresh={"state": "complete"})
                except Exception as exc:
                    errorlog.record("architecture-scan", exc, repo=spec["repo"], spec=spec["id"])
                    if not stop.is_set():
                        specs.update(spec["repo"], spec["id"], architecture_refresh={"state": "failed", "error": str(exc)})
                finally:
                    release()


@router.get("/api/architecture")
def architecture():
    return refresh(query.current_repo())


@router.post("/api/architecture")
def add_architecture():
    from . import work

    with query._lock:
        root = query.current_repo()
        release = query.hold(work._busy, work._lock, str(root), "", kind="turn")
    try:
        return refresh(root, create=True)
    finally:
        release()
