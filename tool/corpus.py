"""corpus — build a target repository's document listing. The agent chooses."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEAD = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.M)
SKIP_DIRS = {"node_modules", ".git", "artifacts", "__pycache__"}

# No ceiling here. A listing is raw material and raw material grows. Trimming
# what goes in and how much is the hook's job; the side that counts counts
# everything there is.


def summarize(path: Path, root: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    heads = HEAD.findall(text)
    title = next((h[1] for h in heads if h[0] == "#"), path.stem)
    body = HEAD.sub("", text)
    first = next(
        (" ".join(p.split()) for p in body.split("\n\n") if len(p.strip()) > 40), ""
    )
    return {
        "path": path.relative_to(root).as_posix(),
        "title": " ".join(title.split()),
        "heads": [" ".join(h[1].split()) for h in heads[1:14]],
        "lead": first[:220],
        "chars": len(text),
    }


DEFAULT_ROOTS = ["docs", "."]


def walk(root: Path, roots: list[str]) -> list[Path]:
    """The document files. `.` means `*.md` at the repository root and no deeper.

    Documents scattered at the root went unseen for a long time. This
    repository's own listing was a single README.md, with SCHEMA,
    ENFORCEMENT, MAINTENANCE and index all missing — the wiki could not see
    its own documents in its own listing.

    One file can fall under two roots, so duplicates are removed while the
    order is kept.
    """

    found: list[Path] = []
    for name in roots:
        if name == ".":
            found += sorted(root.glob("*.md"))
            continue
        base = root / name
        if base.is_file() and base.suffix == ".md":
            found.append(base)
        elif base.is_dir():
            found += [
                path for path in sorted(base.rglob("*.md"))
                if not SKIP_DIRS & set(path.parts)
            ]
    return list(dict.fromkeys(found))


def collect(root: Path, roots: list[str]) -> list[dict]:
    return [summarize(path, root) for path in walk(root, roots)]


def catalog(docs: list[dict]) -> str:
    """The listing grouped by directory, carried at session start exactly as is.

    Paths and titles, nothing else. All 109 of them fit in 8,972 characters;
    any amount of body text doubles that, and what a listing is worth is
    "what exists" rather than "what it says".
    """

    from collections import defaultdict

    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for doc in docs:
        parent = str(Path(doc["path"]).parent).replace("\\", "/")
        groups[parent].append((Path(doc["path"]).name, doc["title"]))

    lines: list[str] = []
    for parent in sorted(groups):
        lines.append(f"{parent}/" if parent != "." else "(저장소 루트)")
        lines += [f"  {n} — {t}" for n, t in sorted(groups[parent])]
    return "\n".join(lines)


def load(project: Path) -> dict | None:
    path = project / ".wiki" / "corpus.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def rebuild(repo: Path) -> str:
    """The command that rewrites `repo`'s listing, as a finding tells it.
    Kept beside `main` so the hint cannot drift from the arguments again."""

    return f"python tool/corpus.py --project {repo} --write"


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="저장소 문서의 목록을 만든다")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--roots", nargs="*", default=DEFAULT_ROOTS,
    )
    parser.add_argument("--catalog", action="store_true", help="목록만 찍는다")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    root = args.project.expanduser().resolve()

    if args.catalog:
        index = load(root)
        if not index:
            print("목록이 없다. `--write` 로 먼저 만들어라.", file=sys.stderr)
            return 2
        print(catalog(index["docs"]))
        return 0

    docs = collect(root, args.roots)
    if not docs:
        print("문서를 못 찾았다.", file=sys.stderr)
        return 2
    listing = catalog(docs)

    print(f"# corpus — {root.name}\n")
    print(f"문서 {len(docs)}개 · 본문 {sum(d['chars'] for d in docs):,}자")
    print(f"목록 {len(listing):,}자 — 세션 시작에 이대로 실린다\n")
    biggest = sorted(docs, key=lambda d: -d["chars"])[:5]
    print("## 가장 큰 문서 — 통째로는 못 싣는다\n")
    for doc in biggest:
        print(f"- {doc['chars']:>7,}자  `{doc['path']}`")
    print()

    if not args.write:
        print("`--write` 를 주면 `.wiki/corpus.json` 에 쓴다.")
        return 0

    out = root / ".wiki" / "corpus.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"docs": docs}, ensure_ascii=False), encoding="utf-8"
    )
    print(f"썼다: {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
