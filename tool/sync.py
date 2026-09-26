"""sync — bring the wiki level with where the repository actually is.

A Stop hook. Everything it prints is read by a person, so the strings stay
Korean while the reasoning around them does not.
"""

from __future__ import annotations

# First import of the entry point: it keeps the stack from before whatever
# time limit kills this.
import hook_diagnostics  # noqa: F401
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402
import harvest  # noqa: E402
import repo_graph  # noqa: E402
import repo_lint  # noqa: E402

STAMP = ".sync"
DEFAULT_EVERY = 6 * 3600  # How many seconds between looking for new decisions
TRANSLATE_BUDGET = 12     # Seconds of the hook's 30 that new records may spend becoming English


def stale_docs(repo: Path, roots: list[str]) -> tuple[bool, str]:
    """Is the listing older than the documents? File times only.

    The reason comes back with the answer because it is printed: "rebuilt" on
    its own tells the person nothing about what moved.
    """

    index = repo / ".wiki" / "corpus.json"
    if not index.exists():
        return True, "목록이 없다"
    mine = index.stat().st_mtime
    known = {d["path"] for d in (corpus.load(repo) or {}).get("docs", [])}

    paths = corpus.walk(repo, roots)
    seen = {path.relative_to(repo).as_posix() for path in paths}
    newer = sum(1 for path in paths if path.stat().st_mtime > mine)
    gone = known - seen
    added = seen - known
    if added or gone:
        return True, f"문서 {len(added)}개 늘고 {len(gone)}개 사라졌다"
    if newer:
        return True, f"문서 {newer}개가 목록보다 나중에 고쳐졌다"
    return False, ""


SEQUENCE = re.compile(r"^\d{4}-\d{2}-\d{2}-(\d{1,4})-")


def recorded(repo: Path) -> set[int]:
    """The PR numbers already written down.

    Reading only the `pr:` line misses every record written by hand. Those
    follow the same naming rule — `<date>-<number>-<slug>` — without putting
    `pr:` in the front matter, so the number in the filename is read as well.
    On 2026-09-17 records 013, 015 and 016 were overwritten through that gap.
    """

    found = set()
    for path in (repo / ".wiki" / "decisions").glob("*.md"):
        match = SEQUENCE.match(path.stem)
        if match:
            found.add(int(match.group(1)))
        for line in path.read_text(encoding="utf-8").splitlines()[:12]:
            if line.startswith("pr:"):
                try:
                    found.add(int(line.split(":", 1)[1].strip()))
                except ValueError:
                    pass
                break
    return found


def due(repo: Path, every: int) -> bool:
    stamp = repo / ".wiki" / STAMP
    if not stamp.exists():
        return True
    try:
        return time.time() - float(stamp.read_text(encoding="utf-8").strip()) > every
    except Exception:
        return True


def touch(repo: Path) -> None:
    stamp = repo / ".wiki" / STAMP
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{time.time():.0f}", encoding="utf-8")


def new_decisions(repo: Path, limit: int) -> list[str]:
    """Harvest the decisions not yet recorded. Returns the filenames written.

    PRs first, then commits when there is no remote or `gh` cannot run. Two
    repositories working in different ways should still end up with the same
    kind of record.
    """

    known = recorded(repo)
    written = []
    source = harvest.prs(repo, limit) or harvest.commits(repo, limit)
    end = time.monotonic() + TRANSLATE_BUDGET
    for pr in source:
        if pr["number"] in known:
            continue
        name, text = harvest.record(pr, end)
        if time.monotonic() > end:
            # Translation ran out of this hook's time: written now, it stays
            # in the PR's language for good. The next sync takes it up.
            break
        out = repo / ".wiki" / "decisions"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{name}.md"
        # Never overwrite a file that is there. A harvested record is a PR
        # body squeezed into shape; what sits at the same path may be the full
        # text somebody wrote by hand. If the number check is wrong again,
        # this is where it stops being destructive.
        if path.exists():
            continue
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(name)
    return written


def main() -> int:
    # A hook's stdout is a pipe and its default here is cp949. The encoding is
    # not left to the environment: the first Korean character would end the
    # write, and with it the message this hook exists to deliver.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="위키를 저장소 상태에 맞춘다")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--roots", nargs="*", default=corpus.DEFAULT_ROOTS,
    )
    parser.add_argument("--every", type=int, default=DEFAULT_EVERY)
    parser.add_argument("--force", action="store_true", help="시간 제한을 무시한다")
    parser.add_argument("--quiet", action="store_true", help="바뀐 것이 없으면 침묵")
    args = parser.parse_args()

    repo = args.project.expanduser().resolve()
    if not (repo / ".wiki").is_dir():
        return 0

    lines: list[str] = []

    stale, why = stale_docs(repo, args.roots)
    if stale:
        docs = corpus.collect(repo, args.roots)
        (repo / ".wiki" / "corpus.json").write_text(
            json.dumps({"docs": docs}, ensure_ascii=False), encoding="utf-8"
        )
        lines.append(f"목록을 다시 만들었다 — {why} (문서 {len(docs)}개)")

        # The knowledge graph sits on top of the listing, so it is rebuilt
        # only when the listing moved. Rebuilding every time reads every
        # document, and a hook that expensive is a hook the person turns off.
        graph = repo_graph.write(repo)
        if graph:
            counts = graph["counts"]
            lines.append(
                f"지식 그래프 — 문서 {counts['docs']}개 중 "
                f"아무도 안 가리키는 것 {counts['orphans']}개"
            )

    if args.force or due(repo, args.every):
        try:
            fresh = new_decisions(repo, 30)
        except Exception:
            fresh = []
        touch(repo)
        if fresh:
            lines.append(
                f"결정 기록 {len(fresh)}건을 캤다: " + ", ".join(fresh[:4])
            )

    # This repository's axis only. The hub wiki's own health is held by the
    # hub's gate and by `after-merge` — spilling another axis's findings here
    # prints a line this session can do nothing about, every time, and a
    # reader who learns to skip one line learns to skip the block.
    findings = repo_lint.check(repo)
    if findings:
        lines.append(f"검진 발견 {len(findings)}건")
        lines += [f"  - {kind}: {message}" for kind, message in findings[:6]]

    if not lines:
        if not args.quiet:
            print("위키가 저장소와 맞는다.")
        return 0

    text = "위키 자동 갱신\n" + "\n".join(f"- {line}" for line in lines)
    if sys.stdin.isatty():
        print(text)
        return 0
    json.dump({"systemMessage": text}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    # Whatever happens, a hook does not stop the session. Leave the name of
    # what went wrong and pass.
    try:
        _code = main()
    except Exception as _error:  # noqa: BLE001
        print(f"hook skipped: {type(_error).__name__}", file=sys.stderr)
        _code = 0
    raise SystemExit(_code)
