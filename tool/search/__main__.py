"""`python tool/search "<query>" --project <repo> [--k 8]` — sections that
match, with the `path:line` each starts at.

Relative paths inside the repository; a hub page outside it keeps its absolute
path, so `Read` can open what is printed.
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search import ask  # noqa: E402


def local(query: str, project: str | None, k: int) -> list[dict]:
    """BM25 in this process, for when no daemon can be reached — a sandbox
    that forbids the connection, say. No vectors: loading the model here
    would cost more than the question."""

    from search import HUB
    from search.daemon import Embedder, Index

    index = Index(HUB, Path(project) if project else None, Embedder(None))
    index.refresh()
    return index.search(query, k)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/search", description="위키와 저장소 문서를 검색한다")
    parser.add_argument("query")
    parser.add_argument("--project", default=None, help="대상 저장소. 없으면 허브 규칙만")
    parser.add_argument("--k", type=int, default=8)
    args = parser.parse_args()
    project = str(Path(args.project).expanduser().resolve()) if args.project else None

    # The chat may be slow. Start the daemon if it is not there and wait for
    # it, rather than answering with nothing. Switched off, go straight to BM25.
    results = None
    for _attempt in range(0 if os.environ.get("WIKI_SEARCH") == "off" else 20):
        results = ask(args.query, project, timeout=5.0, k=args.k, wait=60.0)
        if results is not None:
            break
        time.sleep(1.0)
    if results is None and os.environ.get("WIKI_SEARCH") == "off":
        results = local(args.query, project, args.k)
    elif results is None:
        print("검색 데몬에 닿지 않아 이 프로세스에서 BM25 만으로 찾았다", file=sys.stderr)
        results = local(args.query, project, args.k)

    root = Path(project) if project else None
    for hit in results:
        path = Path(hit["path"])
        try:
            where = path.relative_to(root).as_posix() if root else path.as_posix()
        except ValueError:
            where = path.as_posix()
        print(f"## {where}:{hit['line']} — {hit['heading']}\n\n{hit['text'].strip()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
