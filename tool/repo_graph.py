"""repo_graph — what an attached repository's knowledge points at."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402
from wiki.wikilib import front_matter, project_pages  # noqa: E402

NS = "repo"

# Documents get pointed at in two shapes: a markdown link, and the backticked
# path these repositories actually use more — `docs/development/TDD.md`, in
# instructions and in documents alike. Not counting the second one inflates
# the orphan count.
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+\.md)[^)]*\)")
# A line reference, `README.md:24`, points at the document as much as the bare
# path does; the plans cite findings that way.
BARE_PATH = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.md)(?::\d+(?:-\d+)?)?`")

# Where a reader walks in. Nothing points at the front door, so counting it as
# an orphan reports every repository's README forever and teaches the reader
# to skip the number.
ENTRY = {"README.md"}


def targets(text: str) -> set[str]:
    return set(MD_LINK.findall(text)) | set(BARE_PATH.findall(text))


def resolve(raw: str, source: str, known: set[str]) -> str | None:
    """A pointed-at path, resolved against the repository. `None` when not found.

    Relative first. `../architecture/x.md` only means anything from where the
    pointing document sits; resolved from the repository root it lands on the
    wrong file or on nothing at all.

    The filesystem is never touched. Whether the file exists is already
    answered by `known`, and `Path.resolve` measures against the current
    directory, which would make the answer depend on where this was run.
    """

    here = posixpath.dirname(source)
    for candidate in (posixpath.join(here, raw), raw):
        name = posixpath.normpath(candidate).lstrip("./")
        if name in known:
            return name
    return None


def decisions_of(repo: Path) -> list[dict]:
    found = []
    for path in sorted((repo / ".wiki" / "decisions").glob("*.md")):
        meta, _body = front_matter(path.read_text(encoding="utf-8"))
        found.append({
            "id": f".wiki/decisions/{path.stem}",
            "severity": str(meta.get("severity") or ""),
            "injectable": bool(meta.get("triggers")),
        })
    return found


def build(repo: Path) -> dict | None:
    """This repository's knowledge edges. Without `corpus.json`, nothing is built."""

    index = corpus.load(repo)
    if not index:
        return None

    docs = {doc["path"]: doc for doc in index.get("docs") or []}
    known = set(docs)
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for path in sorted(known):
        try:
            text = (repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Sorted. A set comes out in a different order each run, so a
        # committed file churns on every regeneration with identical content,
        # and output that churns is output nobody can diff.
        for raw in sorted(targets(text)):
            hit = resolve(raw, path, known)
            if hit and hit != path and (path, hit) not in seen:
                seen.add((path, hit))
                edges.append({"a": path, "b": hit, "kind": "link"})

    pages = project_pages(repo)
    for name, (meta, body, _path) in pages.items():
        pointed = {str(t) for t in (meta.get("reads") or [])} | targets(body)
        for raw in sorted(pointed):
            hit = resolve(raw, name, known)
            if hit:
                edges.append({"a": name, "b": hit, "kind": "reads", "cross": True})

    inbound = {edge["b"] for edge in edges}
    read = {edge["b"] for edge in edges if edge["kind"] == "reads"}
    orphans = sorted(known - inbound - ENTRY)
    records = decisions_of(repo)

    return {
        "ns": NS,
        "repo": repo.name,
        "nodes_from": ".wiki/corpus.json",
        "edges": edges,
        "orphans": orphans,
        "counts": {
            "docs": len(known),
            "linked": len(inbound - read),
            "read": len(read),
            "orphans": len(orphans),
            "pages": len(pages),
            "decisions": len(records),
            "injectable_decisions": sum(1 for d in records if d["injectable"]),
        },
    }


def write(repo: Path) -> dict | None:
    data = build(repo)
    if data is None:
        return None
    (repo / ".wiki" / "graph.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return data


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="붙은 저장소의 지식 그래프를 만든다")
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    data = write(repo)
    if data is None:
        print("`.wiki/corpus.json` 이 없다. `tool/corpus.py --write` 를 먼저 돌려라.")
        return 2

    counts = data["counts"]
    print(f"# repo_graph — {data['repo']}\n")
    print("| | 수 |")
    print("| --- | ---: |")
    for label, key in (
        ("문서", "docs"),
        ("  다른 문서가 가리킴", "linked"),
        ("  페이지가 가리킴", "read"),
        ("  **아무도 안 가리킴**", "orphans"),
        ("프로젝트 페이지", "pages"),
        ("결정 기록", "decisions"),
        ("  주입 대상", "injectable_decisions"),
    ):
        print(f"| {label} | {counts[key]:,} |")
    print(f"\n엣지 {len(data['edges']):,}개. `.wiki/graph.json` 에 썼다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
