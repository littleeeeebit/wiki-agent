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
from pydantic import BaseModel
import yaml

from agent import ChatSession, cli_command, read_tools
from common.process import background_options
from .channels import WIKI
from . import query

router = APIRouter()
_lock = threading.Lock()
FIELDS = ("description", "diagram", "context", "constraint", "concern", "todo", "note")
DOCUMENT_FILES = {"meta.yaml", "config.yaml", "diagram.mmd", *(f"{field}.md" for field in FIELDS)}


class Invalid(ValueError):
    """The model's architecture answer broke the document contract; nothing was published."""


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


def analyze(root: Path, model: str | None, halt: threading.Event, rejected: str = "") -> list[dict]:
    """The model reads source; only our OMM writer may change architecture files."""
    ask = "Analyze this repository and return the architecture JSON. Read the actual execution paths."
    if rejected:
        ask += f" A previous answer was rejected; do not repeat this: {rejected}"
    try:
        return answered(root, model, None, halt, ask)["nodes"]
    except (KeyError, TypeError) as exc:
        raise Invalid(f"Architecture answer is not the requested JSON — {exc}") from exc


def answered(root: Path, model: str | None, effort: str | None, halt: threading.Event, ask: str,
             system: str = "architecture-scan") -> dict:
    """One read-only turn on an architecture prompt, its JSON answer parsed."""
    prompt = (WIKI / f"tool/prompts/{system}.md").read_text(encoding="utf-8")
    chat = ChatSession(root, tools="Read,Glob,Grep", system=prompt, model=model, effort=effort)
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
        for event in chat.say(ask, halt):
            if event.kind == "error" or (event.kind == "done" and event.meta.get("error")):
                raise ValueError(event.text or "Architecture analysis failed")
            if event.kind == "done":
                final = event.text
        if halt.is_set():
            raise ValueError("Architecture analysis cancelled")
        match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", final, re.S)
        try:
            found = json.loads(match.group(1) if match else final)
        except ValueError as exc:
            raise Invalid(f"Architecture answer is not the requested JSON — {exc}") from exc
        if not isinstance(found, dict):
            raise Invalid("Architecture answer is not the requested JSON object")
        return found
    finally:
        ended.set()
        chat.close()
        watcher.join()


def write_nodes(root: Path, nodes: list[dict], halt: threading.Event) -> None:
    """Validate all paths and stage CLI output before replacing generated fields."""
    read_existing(root)
    if not isinstance(nodes, list) or not nodes or len(nodes) > 120:
        raise Invalid("Architecture requires 1–120 described elements")
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
            raise Invalid("Invalid or duplicate architecture element")
        paths.add(node["path"])
    if "overall-architecture" not in paths:
        raise Invalid("Architecture requires an overall perspective")
    for path in paths | set(previous):
        if path in paths and "/" in path and path.rsplit("/", 1)[0] not in paths:
            raise Invalid("Architecture child requires a described parent")
        folder = root / ".omm" / path
        if any(p.is_symlink() for p in (folder, *folder.parents) if p != root):
            raise ValueError("Architecture output cannot follow symbolic links")
    for node in nodes:
        diagram = node.get("diagram", "")
        if re.search(r"(?m)^\s*(?:graph|flowchart|subgraph|end)\s*\[", diagram):
            raise Invalid("Mermaid node IDs cannot use reserved keywords")
        for child in re.findall(r"(?m)^\s*([a-z][a-z0-9-]*)\s*\[", diagram):
            if f"{node['path']}/{child}" not in paths:
                raise Invalid(f"Every diagram component requires a described child — "
                              f"`{node['path']}` draws `{child}` but no `{node['path']}/{child}` element")
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
            said = f"{checked.stdout} {checked.stderr}"
            # Only rule-coded findings (`error [rule] line N`) are the model's;
            # a bare `error:` is the validator or the filesystem failing.
            rejected = re.search(r"(?m)^\s*error \[[\w-]+\]", said) and not re.search(r"(?m)^\s*error:", said)
            raise (Invalid if rejected else ValueError)(f"Invalid architecture diagram: {said}")
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
        try:
            write_nodes(root, analyze(root, model, halt), halt)
        except Invalid as exc:
            # The model often slips on the contract; one fresh answer, told why.
            write_nodes(root, analyze(root, model, halt, str(exc)), halt)
        return read_existing(root)


DIFF_CHARS = 60_000   # the model reads a file itself where the diff is cut


def touched(nodes: list[dict], files: list[str]) -> set[str]:
    """The elements that name a changed file or its folder, and each component a diagram labels with one."""
    names = {*files, *(f.rsplit("/", 1)[0] + "/" for f in files if "/" in f)}
    found = set()
    for node in nodes:
        if any(name in node.get(field, "") for field in FIELDS for name in names):
            found.add(node["path"])
        for line in node.get("diagram", "").splitlines():
            if (child := re.match(r"\s*([a-z][a-z0-9-]*)\s*\[", line)) and any(name in line for name in names):
                found.add(f"{node['path']}/{child.group(1)}")
    # ponytail: matches source paths as written in the text; an element naming none is shown by path only
    return found


def changed(root: Path, since: str, *, model: str, effort: str = "", halt: threading.Event | None = None) -> bool:
    """`[구조 문서 새로 고침]` at a merge: only what the pull request changed since `since`. The model
    gets the changed files, their diff, every element this analysis wrote (`analysis.json`) by path and
    in full only those naming a changed file (`touched`), and answers the ones to rewrite and to
    remove; what it leaves out stays as it is, and an empty answer
    writes nothing (user's choice: a whole-repository analysis on every merge cost too much). An answer
    breaking the contract gets one more turn, told why, as `scan` does. True when it wrote."""
    root, halt = root.resolve(), halt or threading.Event()

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, check=True, **background_options()).stdout
    changes = [f for f in git("diff", "--no-renames", "--name-only", "-z", since, "HEAD", "--", ".",
                              ":(exclude).omm").split("\0") if f]
    # A file the source tools refuse (`.env`, keys) never reaches the model, not even as removed lines.
    files = [f for f in changes if not read_tools.private(Path(f))]
    manifest = root / ".omm" / "analysis.json"
    owned = set(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.is_file() else set()
    if not files or not owned:
        return False   # nothing changed, or nothing generated to keep current: a full scan is `[구조 문서]`
    text = git("diff", "--no-renames", since, "HEAD", "--", ".", ":(exclude).omm",
               *(f":(exclude,literal){f}" for f in changes if f not in files))
    with _lock:
        everything = [{k: n[k] for k in ("path", *FIELDS) if n[k]}
                      for n in read_existing(root)["nodes"] if n["path"] in owned]
        named = touched(everything, files)
        shown = [n for n in everything if n["path"] in named]
        ask = ("This pull request changed these files since commit " + since + ":\n" + "\n".join(files)
               + "\n\nIts diff" + (f", cut at {DIFF_CHARS} characters — read the files for the rest" if len(text) > DIFF_CHARS
                                   else "") + ":\n```diff\n" + text[:DIFF_CHARS] + "\n```\n\nEvery element of the "
               "analysis, by path:\n" + "\n".join(n["path"] for n in everything) + "\n\nThe elements that name a "
               "changed file, in full:\n```json\n" + json.dumps(shown, ensure_ascii=False, indent=1) + "\n```")

        def merged(rejected: str = "") -> list[dict] | None:
            found = answered(root, model, effort or None, halt,
                             ask + (f"\n\nA previous answer was rejected; do not repeat this: {rejected}" if rejected else ""),
                             "architecture-update")
            nodes, removed = found.get("nodes"), found.get("removed")
            if not isinstance(nodes, list) or not isinstance(removed, list) or any(
                    not isinstance(n, dict) or not isinstance(n.get("path"), str)
                    or not isinstance(n.get("description"), str) or not n["description"].strip() for n in nodes) \
                    or any(not isinstance(p, str) for p in removed):
                raise Invalid("Architecture update needs `nodes` as elements with a path and a description, "
                              "and `removed` as paths")
            paths = [n["path"] for n in nodes]
            if len(set(paths)) != len(paths):
                raise Invalid("Architecture update returned an element path more than once")
            if not nodes and not removed:
                return None
            gone = lambda path: any(path == p or path.startswith(p + "/") for p in removed)  # noqa: E731
            if any(gone(p) for p in paths):
                raise Invalid("Architecture update both rewrites and removes an element")
            kept = {n["path"]: n for n in everything if not gone(n["path"])}
            return list({**kept, **{n["path"]: n for n in nodes}}.values())

        try:
            nodes = merged()
            if nodes is not None:
                write_nodes(root, nodes, halt)
        except Invalid as exc:
            nodes = merged(str(exc))
            if nodes is not None:
                write_nodes(root, nodes, halt)
        return nodes is not None


SETTINGS = {"omm_model": "", "omm_effort": ""}   # beside the loop's, in `raw/chat/main.json`


def settings() -> dict:
    """The model `[구조 문서 새로 고침]` runs on at a merge; none chosen skips it (user's choice)."""
    from . import loop   # `loop` reaches this module through `maintenance`

    return loop.settings(SETTINGS)


class Settings(BaseModel):
    omm_model: str = ""
    omm_effort: str = ""


@router.get("/api/architecture/settings")
def get_settings() -> dict:
    return settings()


@router.post("/api/architecture/settings")
def set_settings(body: Settings) -> dict:
    from . import loop, planning
    from .channels import CLAUDE_MODEL
    from .refactor_api import CODEX_MODEL

    model, effort = body.omm_model.strip(), body.omm_effort.strip()
    if model and not (CODEX_MODEL.fullmatch(model[6:]) if model.startswith("codex:") else CLAUDE_MODEL.fullmatch(model)):
        raise HTTPException(400, "구조 문서 새로 고침 모델 이름을 받지 않는다")
    if effort and not planning.EFFORT.fullmatch(effort):
        raise HTTPException(400, "추론 강도를 받지 않는다")
    loop.store(omm_model=model, omm_effort=effort if model else "")
    return settings()


def refresh(root: Path = WIKI, *, create: bool = False):
    try:
        return scan(root) if create and not (root / ".omm").exists() else read_existing(root)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, f"구조를 읽거나 생성하지 못했다 — {exc}") from exc


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
