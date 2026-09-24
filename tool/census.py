"""census — the first diagnostic to run when attaching a new project.

Where a session log lives is `sessions.py`'s answer, not this file's. It used
to be this file's, and the mirror — a live screen with nothing diagnostic
about it — imported the report generator to find a log.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from workspace import INJECTED, SESSIONS, folder, logs  # noqa: E402

DEFAULT_MARKERS = HERE / "markers" / "ko.toml"


@dataclass
class Turn:
    session: str
    order: int
    at: str
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class Markers:
    correction: list[str] = field(default_factory=list)
    resume: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    incidents: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Markers":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(
            correction=list(data.get("correction", [])),
            resume=list(data.get("resume", [])),
            partial=list(data.get("partial", [])),
            incidents={k: list(v) for k, v in (data.get("incidents") or {}).items()},
        )


def human_turns(files: list[Path]) -> list[Turn]:
    """Of the `type=user` records, only what a person typed. Tool results and
    injected text are removed.

    Files, not a directory. Which files are this checkout's is `sessions.logs`'s
    answer — one directory can hold two checkouts' sessions, and counting both
    as one project's is a census of a conversation that never happened.
    """

    turns: list[Turn] = []
    for path in sorted(files):
        for order, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("type") != "user":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                body = content
            elif isinstance(content, list):
                # One block that is not text means this is a tool result.
                if not content or any(
                    not isinstance(b, dict) or b.get("type") != "text" for b in content
                ):
                    continue
                body = "\n".join(str(b.get("text") or "") for b in content)
            else:
                continue
            body = body.strip()
            if not body or any(mark in body for mark in INJECTED):
                continue
            turns.append(Turn(path.stem, order, str(record.get("timestamp") or ""), body))
    turns.sort(key=lambda t: (t.at, t.session, t.order))
    return turns


def skeleton(text: str) -> str:
    """Strip numbers, paths and hashes down to the skeleton, to find re-pastes."""

    t = " ".join(text.split())
    t = re.sub(r"#\d+", "#N", t)
    t = re.sub(r"\b[0-9a-f]{7,40}\b", "SHA", t)
    t = re.sub(r"\d+", "N", t)
    t = re.sub(r"[A-Za-z0-9_./\\-]{12,}", "PATH", t)
    return t[:220]


def cluster(turns: list[Turn], threshold: float = 0.88) -> dict[str, list[Turn]]:
    """Group utterances whose skeletons are close.

    Grouping only on an exact match splits on one character — a particle, a
    typo. A `difflib` ratio prevents that, works in any language, and at this
    scale (hundreds) even O(n^2) is cheap.
    """

    from difflib import SequenceMatcher

    reps: list[str] = []
    groups: dict[str, list[Turn]] = {}
    for turn in turns:
        skel = skeleton(turn.text)
        for rep in reps:
            # Lengths far apart cannot match. Skipping the comparison is what
            # keeps this cheap.
            if min(len(skel), len(rep)) / max(len(skel), len(rep), 1) < threshold:
                continue
            if SequenceMatcher(None, skel, rep).ratio() >= threshold:
                groups[rep].append(turn)
                break
        else:
            reps.append(skel)
            groups[skel] = [turn]
    return groups


def governing_text(project: Path, names: list[str]) -> str:
    parts = []
    for name in names:
        path = project / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="세션 로그로 무엇이 고장 나는지 센다")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--transcripts", type=Path, default=SESSIONS)
    parser.add_argument("--markers", type=Path, default=DEFAULT_MARKERS)
    parser.add_argument(
        "--governing",
        nargs="*",
        default=["CLAUDE.md", "AGENTS.md"],
        help="대상 저장소에서 항상 로드되는 파일",
    )
    parser.add_argument("--out", type=Path, help="사람 발화를 jsonl 로 저장할 경로")
    parser.add_argument("--samples", type=int, default=12, help="표지에 안 걸린 표본 수")
    parser.add_argument(
        "--since",
        help="이 시각 이후만 센다. `today` 또는 ISO 날짜(2026-09-01). "
             "회고가 오늘 난 것만 보려고 쓴다",
    )
    args = parser.parse_args()

    files = logs(args.project, args.transcripts)
    if not files:
        directory = folder(args.project, args.transcripts)
        where = directory if directory.is_dir() else args.transcripts
        print(f"이 체크아웃의 세션 로그를 못 찾았다: {where}", file=sys.stderr)
        return 2

    markers = Markers.load(args.markers)
    turns = human_turns(files)
    if not turns:
        print("사람 발화가 없다. --transcripts 경로를 확인하라.", file=sys.stderr)
        return 2

    window = ""
    if args.since:
        # `at` is an ISO string, so comparing it lexically is comparing it by
        # time. Nothing is parsed.
        cut = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if args.since == "today"
            else args.since
        )
        whole = len(turns)
        turns = [t for t in turns if t.at >= cut]
        window = f" · `--since {cut}` 로 {whole}건 중 {len(turns)}건"
        if not turns:
            print(f"{cut} 이후 사람 발화가 없다.", file=sys.stderr)
            return 2

    print(f"# census — {args.project.name}\n")
    print(f"세션 {len({t.session for t in turns})}개에서 사람 발화 "
          f"{len(turns)}건 / {sum(t.chars for t in turns):,}자{window}\n")

    # --- 1. Repeated instructions
    #
    # Grouping only on identical skeletons splits on one character of a
    # particle — `PR #N를` against `PR #N을`. Ten instances of the same
    # instruction really did come back as 5 and 5. So the skeletons are
    # grouped by similarity instead, which is not tied to a language.
    groups = cluster(turns)
    repeats = sorted(
        ((k, v) for k, v in groups.items() if len(v) > 1), key=lambda i: -len(i[1])
    )
    print("## 반복 지시 — 같은 말을 다시 시킨 자리\n")
    print("실수가 아니라 자동화되지 않은 관습이다. 위키가 가장 싸게 없앤다.\n")
    print("| 횟수 | 총 글자 | 지시문 |")
    print("| ---: | ---: | --- |")
    for skel, group in repeats[:10]:
        chars = sum(t.chars for t in group)
        print(f"| {len(group)} | {chars:,} | {skel[:82]} |")
    print()

    # --- 2. Is that rule already written down? This census's central number
    governing = governing_text(args.project, args.governing)
    if governing and repeats:
        # Ranked by characters re-entered, not by how many times. The
        # 7-character `이어서 해라` appears 23 times and carries no rule in it.
        # What carries a rule is long and pasted repeatedly.
        heaviest = max(repeats, key=lambda item: sum(t.chars for t in item[1]))[1]
        print("## 이미 적혀 있는데도 다시 쳤는가\n")
        print("이 census 의 핵심 지표다. 가장 많은 글자가 재입력된 지시문의 문장을 "
              f"`{'`, `'.join(args.governing)}` 와 대조한다. 이미 있는데 다시 쳤다면 "
              "그 규칙은 적혀만 있고 작동하지 않는다 — 병목은 검색이 아니라 강제다.\n")
        sentences = [
            s.strip()
            for s in re.split(r"[.\n·]", heaviest[0].text)
            if 12 < len(s.strip()) < 90
        ]
        print(f"- {len(heaviest)}회 반복 · 총 {sum(t.chars for t in heaviest):,}자 재입력\n")
        print("백분율을 내지 않는다. 문자열 대조로는 같은 규칙이 다른 말로 적힌 "
              "것을 못 잡는다(실제로 손으로 개념 대조하니 12/16 이었는데 문자열로는 "
              "0/42 가 나왔다). 대신 대조할 문장을 뽑아 준다 — 각 문장이 이미 "
              "적혀 있는지는 사람이 판단한다.\n")
        print("| 낱말이 걸림 | 재입력된 규칙 문장 |")
        print("| :---: | --- |")
        for sentence in sentences[:16]:
            # Drop the common words and keep the distinctive ones as a hint.
            # A hint, not a verdict.
            words = [w for w in re.findall(r"[A-Za-z_./]{4,}|[가-힣]{2,}", sentence)]
            hint = "○" if any(w in governing for w in words) else " "
            print(f"| {hint} | {sentence[:78]} |")
        print()

    # --- 3. Counts by category
    def hits(patterns: list[str], limit: int | None = None) -> list[Turn]:
        found = [t for t in turns if any(re.search(p, t.text) for p in patterns)]
        return [t for t in found if limit is None or t.chars < limit]

    print("## 부류별\n")
    print("| 건수 | 부류 |")
    print("| ---: | --- |")
    print(f"| {len(hits(markers.correction))} | 교정 — 내가 틀렸다고 말한 자리 |")
    print(f"| {len(hits(markers.resume, 120))} | 재개 요구 — 멈추지 말았어야 할 자리 |")
    print(f"| {len(hits(markers.partial))} | 부분 수행 — 나머지를 다시 시킨 자리 |")
    print()

    if markers.incidents:
        print("## 같은 실패가 몇 번 지적됐나\n")
        print("| 횟수 | 무엇 |")
        print("| ---: | --- |")
        for name, patterns in sorted(
            markers.incidents.items(), key=lambda i: -len(hits(i[1]))
        ):
            print(f"| {len(hits(patterns))} | {name} |")
        print()

    # --- 4. What the markers missed, as a check on their bias
    missed = [t for t in turns if not any(re.search(p, t.text) for p in markers.correction)]
    print("## 표지에 안 걸린 표본\n")
    print("이 표본을 눈으로 읽어라. 교정인데 표지가 못 잡은 것이 보이면 "
          f"`{args.markers.name}` 에 그 어휘를 더한다. 이 절이 없으면 census 는 "
          "표지를 쓴 사람의 편향을 잰다.\n")
    step = max(1, len(missed) // max(1, args.samples))
    for turn in missed[::step][: args.samples]:
        print(f"- {' '.join(turn.text.split())[:110]}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="\n") as handle:
            for turn in turns:
                handle.write(json.dumps(
                    {"session": turn.session, "order": turn.order, "at": turn.at,
                     "chars": turn.chars, "text": turn.text},
                    ensure_ascii=False,
                ) + "\n")
        print(f"\n사람 발화를 {args.out} 에 저장했다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
