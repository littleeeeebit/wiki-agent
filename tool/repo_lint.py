"""repo_lint — find where a target repository's knowledge is rotting.

Every finding it returns is printed for a person, so those strings are Korean.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from wiki import metadata_errors, project_pages  # noqa: E402

NEWER_ALLOWED = 3   # A listing this far behind is not worth saying anything about


def stale_index(repo: Path) -> list[tuple[str, str]]:
    """Is the document listing older than the documents?

    The index is derived, so it falls behind whenever a document changes. An
    index that has fallen behind is worse than none: it points confidently at
    a document that was deleted.
    """

    index = repo / ".wiki" / "corpus.json"
    if not index.exists():
        return []

    mine = index.stat().st_mtime
    docs = repo / "docs"
    newer = [
        path for path in docs.rglob("*.md") if path.stat().st_mtime > mine
    ] if docs.is_dir() else []

    gone = []
    try:
        listed = json.loads(index.read_text(encoding="utf-8")).get("docs") or []
    except (OSError, ValueError):
        listed = []
    for doc in listed:
        if not (repo / doc["path"]).exists():
            gone.append(doc["path"])

    if gone:
        return [(
            "낡은 목록",
            f"목록이 없는 문서 {len(gone)}개를 가리킨다 (예: `{gone[0]}`). "
            "`tool/corpus.py --write` 로 다시 만들어라",
        )]
    if len(newer) > NEWER_ALLOWED:
        return [(
            "낡은 목록",
            f"문서 {len(newer)}개가 목록보다 나중에 고쳐졌다. "
            "`tool/corpus.py --write` 로 다시 만들어라",
        )]
    return []


def dangling_pointers(repo: Path) -> list[tuple[str, str]]:
    """Does what a project page points at actually exist?

    A project page points at the authoritative document rather than carrying
    it. When the thing pointed at moves, the page goes on saying the wrong
    thing confidently and quietly, so whether that path exists is checked by a
    machine every time.
    """

    found = []
    for name, (meta, _body, _path) in project_pages(repo).items():
        for target in meta.get("reads") or []:
            if not (repo / str(target)).exists():
                found.append(("끊어진 포인터", f"`{name}` 이 `{target}` 를 가리키는데 없다"))
        if str(meta.get("severity")) in ("landmine", "contract") and not (
            meta.get("triggers") or []
        ):
            found.append(
                ("안 실리는 규칙", f"`{name}` 에 `triggers` 가 없어 아무 때도 안 실린다")
            )
    return found


def misplaced_scope(repo: Path) -> list[tuple[str, str]]:
    """Has a hub-scope page ended up inside a target repository?

    `operator` and `craft` name no repository, so the hub holds them. A copy
    sitting here means learning the same lesson again in every repository,
    which is the accident this wiki was built out of.

    Why this is a check and not prose: both places are called "the wiki", and
    a session read "record it in the wiki" as the current repository's
    `.wiki/` and wrote a repository-independent rule there. The injection
    header now states the source as an absolute path, but that only works if
    the reader reads that line. This goes red whether anyone reads or not.
    """

    found = []
    for name, (meta, _body, _path) in project_pages(repo).items():
        scope = str(meta.get("scope") or "").strip()
        if scope in ("operator", "craft"):
            found.append(
                (
                    "범위가 어긋난 페이지",
                    f"`{name}` 의 scope 가 `{scope}` 인데 저장소의 `.wiki/` 에 있다. "
                    "저장소를 안 가리는 규칙은 허브가 든다",
                )
            )
    return found


def check(repo: Path) -> list[tuple[str, str]]:
    """This repository's findings. Printing them is the caller's job."""

    from apply import wiring_drift
    malformed = [
        ("페이지 형식 오류", f"`{path.relative_to(repo).as_posix()}`: {error}")
        for path in sorted((repo / ".wiki").glob("**/*.md"))
        for error in metadata_errors(path)
    ]
    # The emphasis check belongs here so that its contract also holds in a
    # target repository. A hook is called before the write, so it never sees
    # the document an `Edit` or a patch is about to produce. This check reads
    # the file on disk and closes that gap. Left only on the hub, a fragment
    # passes in every installed repository and nobody looks again — which is
    # enforcement that is written down as attached and is not.
    from lint import loud_emphasis

    return (stale_index(repo) + dangling_pointers(repo) + misplaced_scope(repo)
            + malformed + wiring_drift(repo) + loud_emphasis(repo))


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="대상 저장소의 지식을 검진한다")
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    findings = check(repo)

    print(f"# repo_lint — {repo.name}\n")
    if not findings:
        print("새 발견 없음.")
        return 0

    print(f"## 발견 {len(findings)}건\n")
    kinds: dict[str, list[str]] = {}
    for kind, message in findings:
        kinds.setdefault(kind, []).append(message)
    for kind, messages in kinds.items():
        print(f"### {kind} — {len(messages)}건\n")
        for message in messages:
            print(f"- {message}")
        print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
