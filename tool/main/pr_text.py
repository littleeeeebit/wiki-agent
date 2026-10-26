"""The pull request a person reads. Its prose is the worker's `pr-summary`,
written in Korean from the final diff; the rest is what the server knows for
a fact — the changed files, the checks, the gate.

No spec sentence goes up. A spec is a plan and a set of instructions written
for the worker; translated, it read as orders (`헬퍼를 추가하십시오`) and as
predictions, never as what the change did. When the summary fails its checks
twice, the body says so and keeps to the facts."""

from __future__ import annotations

import re

from . import sweep

TRANSLATE_SECONDS = 60   # the review loop's P2 comment into Korean, one request
FILES_SHOWN = 30
TITLE_LIMIT = 80
KEYS = ("title", "changes", "why", "review")
HANGUL = re.compile(r"[가-힣]")
# An order to the worker, as a spec's instructions read once translated.
ORDER = re.compile(r"(하십시오|하세요|하라|해라|할 것)[.!\s]*$")
CITED = re.compile(r"`([^`\n]+)`")
SHAPE = ('```pr-summary\n{"title": "<한국어 제목: 바뀐 결과, 80자 이내>", '
         '"changes": ["<바뀐 점 한 줄씩, 파일·함수는 `백틱`으로>"], '
         '"why": "<왜 바꿨는지, 한두 문장>", "review": ["<리뷰어가 먼저 볼 곳과 이유; 비어도 된다>"]}\n```')


def summary(value) -> dict | None:
    """The `pr-summary` block's four fields, or None when it is not shaped as one."""

    def lines(v) -> bool:
        return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)

    if not isinstance(value, dict) or set(value) != set(KEYS):
        return None
    if not (isinstance(value["title"], str) and value["title"].strip() and lines(value["changes"])
            and value["changes"] and isinstance(value["why"], str) and lines(value["review"])):
        return None
    return {"title": " ".join(value["title"].split()), "changes": value["changes"],
            "why": value["why"].strip(), "review": value["review"]}


def problems(told: dict | None, diff: str) -> list[str]:
    """Why the summary cannot go up, for the worker to fix: empty when it can.
    A name it cites must be in the diff, so it cannot describe the plan instead."""

    if told is None:
        return [f"`pr-summary` is missing or not exactly the keys {', '.join(KEYS)}"]
    texts = [told["title"], *told["changes"], told["why"], *told["review"]]
    out = [f"not Korean: {t}" for t in texts if t and not HANGUL.search(t)]
    if len(told["title"]) > TITLE_LIMIT:
        out.append(f"the title is {len(told['title'])} characters, over {TITLE_LIMIT}")
    out += [f"an order, not a description: {t}" for t in texts if ORDER.search(t)]
    out += [f"`{c}` is not in this branch's diff" for c in dict.fromkeys(c for t in texts for c in CITED.findall(t))
            if c not in diff]
    return out


def asking(found: list[str]) -> str:
    """The turn that sends a failed summary back once, with what failed."""

    return ("The pull request's title and summary come only from your `pr-summary`; the spec's sentences never "
            "go up. Yours failed these checks:\n" + "\n".join(f"- {p}" for p in found) +
            "\n\nChange nothing and commit nothing. Read the branch's diff and write the block again, in Korean, "
            "describing what the finished change does for a reviewer who has not read the spec: not the plan, "
            "and not a translation of the spec. Then send the same `test-cleanup` and `done-report` blocks:\n\n"
            + SHAPE)


def title_of(spec: dict) -> str:
    told = spec.get("summary")
    return told["title"] if told else f"{spec['id']} — PR 요약이 검사를 통과하지 못했습니다"


def _files(numstat: str) -> list[str]:
    """`git diff --numstat`'s rows as the body lists them, the totals first."""

    rows = [line.split("\t", 2) for line in numstat.splitlines() if line.count("\t") >= 2]
    if not rows:
        return []
    def count(n: str) -> int:
        return int(n) if n.isdigit() else 0   # a binary file counts `-`

    added, removed = sum(count(a) for a, _, _ in rows), sum(count(d) for _, d, _ in rows)
    lines = [f"{len(rows)}개 파일 · +{added} −{removed}", ""]
    lines += [f"- `{p}` " + ("바이너리" if a == "-" else f"+{a} −{d}") for a, d, p in rows[:FILES_SHOWN]]
    if len(rows) > FILES_SHOWN:
        lines.append(f"- 외 {len(rows) - FILES_SHOWN}개")
    return lines


def body_of(spec: dict, numstat: str = "") -> str:
    """The pull request's body. The section names are the ones
    `harvest.record` reads: `변경 요약` becomes the record's what, and
    `변경 이유` its why."""

    told = spec.get("summary")
    lines = ["## 변경 요약", ""]
    lines += [f"- {c}" for c in told["changes"]] if told else [
        "작업자의 PR 요약이 두 번 모두 검사를 통과하지 못해, 확인된 사실만 올립니다. "
        "무엇이 바뀌었는지는 아래 파일 목록과 diff 로 확인해 주세요."]
    lines.append("")
    if told and told["why"]:
        lines += ["## 변경 이유", "", told["why"], ""]
    if files := _files(numstat):
        lines += ["## 바뀐 파일", "", *files, ""]
    if told and told["review"]:
        lines += ["## 리뷰 포인트", "", *(f"- {r}" for r in told["review"]), ""]
    # The worker's record as it wrote it: translated, it read as slop like the spec's sentences did.
    checks = [f"- [x] {i['item']}" + (f" — {i['evidence']}" if i.get("evidence") else "") for i in spec["report"]]
    last = (spec["gate"].get("tail") or "").splitlines()[-1:] or [""]
    what = "변경이 닿는 확인" if spec["gate"].get("selection") == "mapped" else "패치 확인" if spec["gate"].get("selection") == "deferred" else "게이트"
    checks += [f"- [x] 서버의 {what} 기록 — `{spec['gate']['cmd']}` · {last[0]}".rstrip(" ·")]
    if spec["gate"].get("selection") in {"mapped", "deferred"}:
        checks += ["- [ ] 전체 게이트 — 리뷰가 허용한 커밋에서 머지 전에 돈다"]
    checks += [f"- [x] Jev 가 고른 추가 확인 — `{c['cmd']}` · 통과" for c in spec.get("checks") or []
               if c["ok"] and c.get("head") == spec["gate"].get("head")]
    lines += ["## 확인", "", f"<details><summary>확인 기록 {len(checks)}건 (원문) — {last[0] or '서버 게이트 통과'}</summary>", "",
              *checks, "", "</details>", ""]
    from . import refactor_run
    lines += [*sweep.section(spec.get("test_cleanup")), *refactor_run.section(spec)]
    grounds = spec["grounds"]
    cited = ", ".join(f"`{g}`" for g in grounds["pages"] + grounds["files"]) or "없음"
    lines += ["## 명세", "", f"`raw/specs/{spec['repo']}/{spec['id']}.json` · 근거: {cited}", ""]
    return "\n".join(lines)
