"""Reuse the real injection functions to produce a cost and utterance table.

It does not score false positives — whether a rule belonged in a turn is a
judgement, and a number claiming to have made it would only hide that nobody
did.
"""

from __future__ import annotations

import argparse
from collections import Counter
import glob
import json
from pathlib import Path
import sys

from wiki import (
    REPO_BUDGET, RULE_BUDGET, budget, label, match_pages, pages, render_parts,
)


def measure(prompt: str, available: list, rule_limit=None, repo_limit=None) -> dict:
    """The size of the rendered block, without the header, source or separator.

    This is the size before translation. `inject` renders a target
    repository's `.wiki/` body after turning it English, so on a turn where
    the translation succeeded `trajectory.cost` is larger than this. Matching
    the two would mean a round trip per utterance, which is exactly what
    replaying a census is for avoiding. Subtract this difference before
    comparing the numbers.
    """
    matched = match_pages(prompt, available)
    rules, decisions, rule, repo, _trimmed = render_parts(matched, rule_limit, repo_limit)
    return {
        "names": [label(p) for _s, _b, p in rules + decisions],
        "rule": sum(map(len, rule)), "repo": sum(map(len, repo)),
    }


def census_paths(patterns: list[str]) -> list[Path]:
    """PowerShell does not expand a wildcard for a native command."""
    paths = []
    for pattern in patterns:
        found = sorted(glob.glob(pattern))
        if not found:
            raise ValueError(f"입력 파일이 없다: {pattern}")
        paths.extend(Path(p) for p in found)
    return list(dict.fromkeys(paths))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="실제 발화와 주입 결과를 대조한다")
    parser.add_argument("census", nargs="+")
    parser.add_argument("--samples", type=int, default=2, help="페이지별 적중·미적중 표본 수")
    parser.add_argument("--project", type=Path, help="지식을 측정할 저장소 (생략하면 미측정)")
    parser.add_argument("--adapter", help="기본값은 프로젝트 폴더 이름")
    args = parser.parse_args()
    project = args.project.expanduser().resolve() if args.project else None
    if project and not project.is_dir():
        parser.error(f"저장소가 없다: {project}")
    adapter = args.adapter or (project.name if project else None)
    try:
        paths = census_paths(args.census)
    except ValueError as error:
        parser.error(str(error))
    turns = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                turns.append((f"{path.as_posix()}:{number}", json.loads(line)["text"]))

    available = pages(adapter, project)
    limits = budget(adapter, RULE_BUDGET, project), budget(adapter, REPO_BUDGET, project)
    before = [measure(text, available) for _where, text in turns]
    after = [measure(text, available, *limits) for _where, text in turns]
    counts = Counter(name for row in after for name in row["names"])
    print(f"# 트리거 감사 — 실제 발화 {len(turns)}건\n")
    print(f"대상: {project} · 어댑터: {adapter}\n" if project else
          "공유 규칙만 측정. 프로젝트 지식은 **미측정** (--project 필요).\n")
    print("현재 페이지로 재생한 결과다. 과거 실행이나 자동 오탐 판정이 아니다.\n")
    print("글자 수는 번역 전 크기다. 헤더·출처·구분자는 제외한다.")
    print("번역이 성공한 턴의 trajectory.cost 는 이보다 크다 — 두 수를 그대로 비교하지 마라.")
    print("repo는 결정 블록이다. .wiki/*.md 규칙은 rule, SessionStart 목록은 별도다.\n")
    print("| 축 | 축약 전 최대 | 실제 블록 최대 | p95 | 중앙 | 예산 |")
    print("| --- | ---: | ---: | ---: | ---: | --- |")
    for axis, limit in zip(("rule", "repo"), limits):
        if axis == "repo" and project is None:
            print("| repo | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |")
            continue
        values = sorted(row[axis] for row in after) or [0]
        peak = max((row[axis] for row in before), default=0)
        print(f"| {axis} | {peak:,} | {max(values):,} | "
              f"{values[min(len(values)-1, int(len(values)*.95))]:,} | "
              f"{values[len(values)//2]:,} | {limit if limit else '없음 (축약 안 함)'} |")
    print("\n예산은 축약 목표다. 이름 목록의 최소 크기보다 작으면 초과할 수 있다.\n")
    print("| 걸린 발화 | 페이지 |")
    print("| ---: | --- |")
    for name, count in counts.most_common():
        print(f"| {count} | {name} |")

    print("\n## 페이지별 발화 대조 — 판정은 사람이 한다\n")
    for _meta, _body, path in available:
        name = label(path)
        if name == "operator/agent-delegation":
            continue  # an every-utterance rule must not hide another page's misses
        print(f"### {name}\n")
        for hit in (True, False):
            selected = [(where, text) for (where, text), row in zip(turns, after)
                        if (name in row["names"]) == hit]
            for where, text in selected[:max(0, args.samples)]:
                excerpt = " ".join(text.split())[:180]
                print(f"- {'걸림' if hit else '안 걸림'} {where} — {excerpt}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
