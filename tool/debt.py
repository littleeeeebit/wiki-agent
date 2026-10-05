"""debt — language-agnostic size and duplication metrics, and the ratchet that keeps them from growing.

`scan` ranks a repository's hotspots for a person or the refactoring workflow.
`check` runs after every final gate (`specs.required`): a file may not grow
past its entry in `.wiki/ratchet.json`, and a file without an entry stays under
the new-file cap with no duplicated block. Raising an entry needs a new
`reason` in the same change. `tighten` runs in merge preparation
(`main/maintenance.py`) and only ever lowers entries. A repository without
`.wiki/ratchet.json` passes `check`; `init` adopts the ratchet at today's numbers.

    python tool/debt.py scan [--top N] [--json]
    python tool/debt.py init | check [--base BRANCH] | tighten
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from fnmatch import fnmatchcase
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common.process import background_options  # noqa: E402

RATCHET = ".wiki/ratchet.json"
CAPS = {"new_file_max_lines": 800, "test_file_max_lines": 1500}
CODE = frozenset(".py .pyi .ts .tsx .js .jsx .mjs .cjs .vue .svelte .swift .kt .kts .java .scala .go .rs .rb .php "
                 ".cs .c .h .cc .cpp .hpp .m .mm .dart .lua .sh .ps1".split())
WINDOW = 6          # significant lines in a row that count as one duplicated block
CHURN_DAYS = 180
IMPORT = re.compile(r"(import|from|#include|using|use|require|package)\b")
COMMENT = ("#", "//", "/*", "*", "<!--", "--")
# A block whose header opens a container is a whole class or test suite, not one body.
CONTAINER = re.compile(r"((export|public|private|internal|abstract|final|data|sealed|open)\s+)*"
                       r"(class|interface|struct|impl|enum|namespace|module|extension|object|protocol|trait)\b"
                       r"|describe\(")
TEST = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[^/.]+$|\.(test|spec)\.[^/.]+$")
BRANCH = re.compile(r"[\w./-]{1,200}")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **background_options())
    if done.returncode:
        raise ValueError(done.stderr.strip() or f"git {args[0]} 실패")
    return done.stdout


# -- Metrics -----------------------------------------------------------------

def sources(root: Path, exclude: list[str]) -> dict[str, list[str]]:
    """Tracked and new, not ignored, code files as lines. Undecodable files are not code."""

    found = {}
    for rel in git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0"):
        file = root / rel
        if not rel or Path(rel).suffix.lower() not in CODE or any(fnmatchcase(rel, g) for g in exclude) \
                or file.is_symlink() or not file.is_file():
            continue
        try:
            found[rel] = file.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
    return found


def significant(line: str) -> str:
    """The line as compared for duplication, or empty: blank, punctuation,
    imports and comments repeat everywhere and prove nothing."""

    norm = " ".join(line.split())
    if len(re.findall(r"\w", norm)) < 2 or IMPORT.match(norm) or norm.startswith(COMMENT):
        return ""
    return norm


def imported(lines: list[str]) -> list[str]:
    """`lines` with the continuation lines of a multiline import blanked:
    `import {` … `}` or `from x import (` … `)` is still just an import."""

    out, closing = [], ""
    for line in lines:
        norm = " ".join(line.split())
        if closing:
            out.append("")
            closing = "" if closing in norm else closing
            continue
        out.append(line)
        if IMPORT.match(norm):
            for opener, closer in (("{", "}"), ("(", ")")):
                if norm.endswith(opener) or (opener in norm and closer not in norm):
                    closing = closer
    return out


def duplicated(files: dict[str, list[str]]) -> dict[str, int]:
    """Per file, how many lines sit in a block of `WINDOW` significant lines
    that also appears somewhere else, in this file or another. Exact text
    after whitespace collapses; renamed copies are not found."""

    places = defaultdict(list)
    for rel, lines in files.items():
        sig = [(i, n) for i, n in ((i, significant(line)) for i, line in enumerate(imported(lines))) if n]
        for k in range(len(sig) - WINDOW + 1):
            chunk = sig[k:k + WINDOW]
            places[tuple(n for _, n in chunk)].append((rel, [i for i, _ in chunk]))
    marked = defaultdict(set)
    for found in places.values():
        if len(found) > 1:
            for rel, indexes in found:
                marked[rel].update(indexes)
    return {rel: len(marked[rel]) for rel in files}


def longest_block(lines: list[str]) -> int:
    """The longest indented body under a header ending in `:` or `{` — a
    function's length, approximately, in any indented language."""

    best = 0
    for i, line in enumerate(lines):
        head = line.strip()
        if not head.endswith((":", "{")) or CONTAINER.match(head):
            continue
        indent, end = len(line) - len(line.lstrip()), i
        for j in range(i + 1, len(lines)):
            if lines[j].strip():
                if len(lines[j]) - len(lines[j].lstrip()) <= indent:
                    break
                end = j
        best = max(best, end - i + 1)
    return best


def measure(root: Path, exclude: list[str] | None = None) -> dict[str, dict]:
    files = sources(root, exclude or [])
    dup = duplicated(files)
    return {rel: {"lines": len(lines), "dup": dup[rel], "block": longest_block(lines)} for rel, lines in files.items()}


def churn(root: Path) -> Counter:
    """Commits per file over the last `CHURN_DAYS`; empty when history is unreadable."""

    try:
        out = git(root, "log", f"--since={CHURN_DAYS}.days", "--format=", "--name-only")
    except ValueError:
        return Counter()
    return Counter(line for line in out.splitlines() if line)


def scan(root: Path) -> list[dict]:
    """Hotspots, worst first."""

    exclude = load(root / RATCHET)["exclude"] if (root / RATCHET).exists() else []
    changed = churn(root)
    rows = [{"path": rel, **m, "churn": changed[rel]} for rel, m in measure(root, exclude).items()]
    for row in rows:
        # ponytail: size × change frequency; a complexity measure would rank better
        row["score"] = (row["lines"] + row["dup"]) * (1 + row["churn"])
    return sorted(rows, key=lambda r: (-r["score"], r["path"]))


# -- The ratchet ---------------------------------------------------------------

def load(file: Path, text: str | None = None) -> dict:
    """`.wiki/ratchet.json`, checked: a malformed one fails the gate rather than passing it."""

    data = json.loads(file.read_text(encoding="utf-8") if text is None else text)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"{RATCHET}: version 1 이 아니다")
    for cap in CAPS:
        if not isinstance(data.get(cap), int) or data[cap] <= 0:
            raise ValueError(f"{RATCHET}: {cap} 는 양의 정수여야 한다")
    if not isinstance(data.get("exclude", []), list) or not all(isinstance(g, str) for g in data.get("exclude", [])):
        raise ValueError(f"{RATCHET}: exclude 는 glob 문자열 목록이다")
    entries = data.get("files")
    if not isinstance(entries, dict):
        raise ValueError(f"{RATCHET}: files 가 없다")
    for rel, e in entries.items():
        if not (isinstance(e, dict) and all(isinstance(e.get(k), int) and e[k] >= 0 for k in ("lines", "dup"))
                and isinstance(e.get("reason", ""), str)):
            raise ValueError(f"{RATCHET}: {rel} 항목은 lines·dup 정수와 reason 문자열이다")
    return {"exclude": [], "reason": "", **data}


def limit(rel: str, ratchet: dict) -> tuple[int, int]:
    """`(lines, dup)` a file may reach: its entry, else the caps with no duplication."""

    entry = ratchet["files"].get(rel)
    if entry:
        return entry["lines"], entry["dup"]
    cap = ratchet["test_file_max_lines" if TEST.search(rel) else "new_file_max_lines"]
    return cap, 0


def base_of(root: Path, base: str | None) -> str:
    """The merge base the change is judged against: with `base`, else the
    remote default branch. Empty when none can be read."""

    for ref in ([f"origin/{base}"] if base else ["origin/HEAD", "origin/main", "origin/master"]):
        try:
            return git(root, "merge-base", "HEAD", ref).strip()
        except ValueError:
            continue
    return ""


def loosened(old: dict, new: dict) -> list[str]:
    """Raises from `old` to `new` that carry no new reason."""

    problems = []
    for rel, e in new["files"].items():
        prev = old["files"].get(rel)
        lines, dup = limit(rel, old)
        if e["lines"] <= lines and e["dup"] <= dup:
            continue
        if not e.get("reason", "").strip() or prev and e.get("reason") == prev.get("reason"):
            problems.append(f"{rel}: 기준을 올렸는데 새 reason 이 없다")
    wider = any(new[c] > old[c] for c in CAPS) or set(new["exclude"]) - set(old["exclude"])
    if wider and (not new["reason"].strip() or new["reason"] == old["reason"]):
        problems.append(f"{RATCHET}: 상한이나 exclude 를 넓혔는데 새 최상위 reason 이 없다")
    return problems


def check(root: Path, base: str | None = None) -> tuple[list[str], str]:
    """`(problems, note)`. The note says when raising was not checked."""

    file = root / RATCHET
    if not file.exists():
        return [], ""
    ratchet = load(file)
    problems = []
    for rel, m in sorted(measure(root, ratchet["exclude"]).items()):
        lines, dup = limit(rel, ratchet)
        if m["lines"] > lines:
            problems.append(f"{rel}: {m['lines']}줄 — 기준 {lines}줄을 넘었다")
        if m["dup"] > dup:
            problems.append(f"{rel}: 중복 {m['dup']}줄 — 기준 {dup}줄을 넘었다")
    oid = base_of(root, base)
    if not oid:
        return problems, "base 를 읽지 못해 기준 완화 검사는 건너뛰었다"
    try:
        old = git(root, "show", f"{oid}:{RATCHET}")
    except ValueError:
        return problems, ""   # adopted in this change: nothing to loosen from
    return problems + loosened(load(file, old), ratchet), ""


def write(file: Path, ratchet: dict) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(ratchet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")


def init(root: Path) -> dict:
    """Adopt the ratchet: every file over today's caps gets an entry at today's numbers."""

    file = root / RATCHET
    if file.exists():
        raise ValueError(f"{RATCHET} 가 이미 있다")
    ratchet = {"version": 1, **CAPS, "exclude": [], "reason": "", "files": {}}
    for rel, m in sorted(measure(root).items()):
        lines, dup = limit(rel, ratchet)
        if m["lines"] > lines or m["dup"] > dup:
            ratchet["files"][rel] = {"lines": m["lines"], "dup": m["dup"], "reason": "baseline at adoption"}
    write(file, ratchet)
    return ratchet


def tighten(root: Path) -> list[str]:
    """Lower every entry to today's numbers and drop entries the caps now
    cover or whose file is gone. `[RATCHET]` while the file differs from
    HEAD, else `[]`: a retry after an interrupted merge preparation finds it
    already lowered, and it is still that preparation's output."""

    file = root / RATCHET
    if not file.exists():
        return []
    ratchet = load(file)
    metrics = measure(root, ratchet["exclude"])
    files = {}
    for rel, e in ratchet["files"].items():
        if rel not in metrics:
            continue
        lowered = {**e, "lines": min(e["lines"], metrics[rel]["lines"]), "dup": min(e["dup"], metrics[rel]["dup"])}
        cap = limit(rel, {**ratchet, "files": {}})[0]
        if lowered["lines"] > cap or lowered["dup"]:
            files[rel] = lowered
    if files != ratchet["files"]:
        write(file, {**ratchet, "files": files})
    return [RATCHET] if git(root, "status", "--porcelain", "--", RATCHET).strip() else []


def command(base: str = "") -> str:
    """The shell command the final gate appends. A base that is not a plain
    branch name is left out rather than quoted into a shell."""

    tail = f" --base {base}" if base and BRANCH.fullmatch(base) and not base.startswith("-") else ""
    return f'"{sys.executable}" "{HERE / "debt.py"}" check{tail}'


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("scan", "init", "check", "tighten"))
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--base", default="")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(git(args.repo or Path.cwd(), "rev-parse", "--show-toplevel").strip())
    try:
        if args.action == "scan":
            rows = scan(root)[:args.top]
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            for r in [] if args.json else rows:
                print(f"{r['score']:>9}  {r['lines']:>5}줄  중복 {r['dup']:>4}  블록 {r['block']:>4}  "
                      f"변경 {r['churn']:>3}  {r['path']}")
        elif args.action == "init":
            print(f"{RATCHET}: 기준 {len(init(root)['files'])}개")
        elif args.action == "tighten":
            print(f"{RATCHET}: {'낮췄다' if tighten(root) else '그대로'}")
        else:
            problems, note = check(root, args.base or None)
            for p in problems:
                print(p)
            if note:
                print(note)
            if problems:
                print(f"\n래칫 실패 — 파일을 줄이거나 나누어라. 꼭 커져야 하면 {RATCHET} 의 그 항목을 올리고 "
                      "같은 변경에 새 reason 을 적어라.")
                return 1
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"래칫 오류 — {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
