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


def rule_paragraph(body: str) -> str:
    """The `Rule.` paragraph whole, up to the blank line. `""` when there is none.

    Both spellings. Hub pages are English; a target repository's pages are
    Korean, and a failed translation hands the Korean original back — a
    parser that knows one spelling leaves a title with no rule under it.

    The whole paragraph, not its first line. The first line of a wrapped
    paragraph ends mid-sentence, and the clause that must hold on every turn
    is often the second sentence — `ask-with-arrow-key-options` forbids the
    asynchronous call there.
    """

    found = re.search(r"^(?:Rule|규칙)\..*?(?=\n[ \t]*\n|\Z)", body, re.M | re.S)
    return found.group(0).rstrip() if found else ""


# A repeated page carries its rule paragraph on every later turn of the
# session. Past this it stops being the short form it was declared to be.
REPEAT_MAX = 1200


def repeat_errors(meta: dict, body: str) -> list[str]:
    """What a page declaring `repeat` owes. A page that does not declare it owes nothing."""

    if "repeat" not in meta:
        return []
    if meta["repeat"] != "rule":
        return [f"repeat 는 `rule` 하나만 받는다: {meta['repeat']!r}"]
    paragraph = rule_paragraph(body)
    if not paragraph:
        return ["repeat: rule 인데 규칙 문단(`Rule.` 또는 `규칙.`)이 없다"]
    if len(paragraph) > REPEAT_MAX:
        return [f"repeat: rule 의 규칙 문단이 {len(paragraph):,}자다 — {REPEAT_MAX:,}자 이하로 줄여라"]
    return []


def metadata_errors(path: Path) -> list[str]:
    """A hook passes a failure; the health check surfaces the page it lost."""
    try:
        meta, body = front_matter(path.read_text(encoding="utf-8"), strict=True)
    except ValueError as error:
        return [str(error)]
    triggers = meta.get("triggers", [])
    if not isinstance(triggers, list):
        return ["triggers는 목록이어야 한다"]
    errors = repeat_errors(meta, body)
    for pattern in triggers:
        try:
            re.compile(str(pattern))
        except re.error:
            errors.append(f"잘못된 트리거 정규식: {pattern}")
    return errors


def hub_pages(wiki: Path = WIKI) -> dict[str, tuple[dict, str, Path]]:
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
