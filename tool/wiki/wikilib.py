"""wikilib — how a page is read, so that every tool reads it the same way."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]  # `tool/`
WIKI = Path(os.environ.get("WIKI_ROOT") or HERE.parent)
SCOPES = ("operator", "craft")


def front_matter(text: str, *, strict: bool = False) -> tuple[dict[str, object], str]:
    """Read the front matter as YAML.

    A hand-written line-by-line parser burned this twice. Once a comma inside
    a regex's `{0,10}` split the list and the page stopped matching at all;
    once it simply could not read the nested `conflicts_with` shape that
    `SCHEMA.md` specifies — and a format the parser cannot read is a
    specification that does not exist.
    """

    if not text.startswith("---"):
        if strict:
            raise ValueError("front matter가 없다")
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        if strict:
            raise ValueError("front matter가 닫히지 않았다")
        return {}, text
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as error:
        if strict:
            raise ValueError("front matter YAML 파싱 실패") from error
        meta = {}
    if strict and (not isinstance(meta, dict) or not meta):
        raise ValueError("front matter는 비어 있지 않은 매핑이어야 한다")
    return (meta if isinstance(meta, dict) else {}), text[end + 4 :].lstrip("\n")


def metadata_errors(path: Path) -> list[str]:
    """A hook passes a failure; the health check surfaces the page it lost."""
    try:
        meta, _body = front_matter(path.read_text(encoding="utf-8"), strict=True)
    except ValueError as error:
        return [str(error)]
    triggers = meta.get("triggers", [])
    if not isinstance(triggers, list):
        return ["triggers는 목록이어야 한다"]
    errors = []
    for pattern in triggers:
        try:
            re.compile(str(pattern))
        except re.error:
            errors.append(f"잘못된 트리거 정규식: {pattern}")
    return errors


def pages(wiki: Path = WIKI) -> dict[str, tuple[dict, str, Path]]:
    """The hub's rule pages, named `scope/name`."""

    found: dict[str, tuple[dict, str, Path]] = {}
    for scope in SCOPES:
        directory = wiki / scope
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            meta, body = front_matter(path.read_text(encoding="utf-8"))
            found[f"{scope}/{path.stem}"] = (meta, body, path)
    return found


def project_pages(repo: Path) -> dict[str, tuple[dict, str, Path]]:
    """A target repository's `.wiki/`, without the decision records — there are
    many of them and they carry no links."""

    directory = repo / ".wiki"
    if not directory.is_dir():
        return {}
    found = {}
    for path in sorted(directory.glob("*.md")):
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        found[f".wiki/{path.stem}"] = (meta, body, path)
    return found


def links_of(meta: dict, body: str) -> set[str]:
    """`[[name]]` in the body and `links:` in the front matter are both links.

    Counting only the body reports a page connected solely through `links:` as
    an orphan. That happened: three of five pages came back as orphans and all
    three had links in their front matter.
    """

    declared = {str(name) for name in (meta.get("links") or [])}
    return set(re.findall(r"\[\[([^\]]+)\]\]", body)) | declared


def resolve(target: str, names: set[str]) -> str | None:
    """`[[name]]` carries no scope, so it is resolved by the tail of the path."""

    if target in names:
        return target
    for name in names:
        if name.split("/", 1)[1] == target:
            return name
    return None


def git_ok(repo: Path, ref: str) -> bool:
    """Does that ref actually exist in this repository?

    `subprocess` is imported inside the function. The injection hook reads
    this module on every utterance without ever calling git, and would pay
    that import's measured 14ms each time. A hook that gets expensive gets
    turned off, and a hook that is off is a hook that does not exist.
    """

    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, timeout=10,
        ).returncode == 0
    except Exception:
        return False
