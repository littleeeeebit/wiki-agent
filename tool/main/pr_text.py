"""The pull request a person reads. Its prose is the worker's `pr-summary`,
written in Korean from the final diff; the rest is what the server knows for
a fact — the changed files, the checks, the gate.

No spec sentence goes up. A spec is a plan and a set of instructions written
for the worker; translated, it read as orders (`헬퍼를 추가하십시오`) and as
predictions, never as what the change did. When the summary fails its checks
twice, the body says so and keeps to the facts.

The reader is a colleague who knows neither the spec nor this part of the
code. #262's first worker summary passed every check and still read as a
list of function names, `proactive_check와 proactive_trigger에 각각 있던 …`:
so the prose fields carry no code names at all, and the names go in
`code`, the one field written for someone already in the code."""

from __future__ import annotations

import re
from pathlib import Path

from . import sweep

# The turn that asks for the text right before the pull request: a guide and three spec → PR examples.
WRITE = (Path(__file__).resolve().parents[1] / "prompts" / "pr-write.md").read_text(encoding="utf-8").strip()
FILES_SHOWN = 30
TITLE_LIMIT = 80
KEYS = ("title", "context", "changes", "behavior", "why", "verify", "code")
PROSE = ("title", "context", "changes", "behavior", "why", "verify")
HANGUL = re.compile(r"[가-힣]")
# An order to the worker, as a spec's instructions read once translated.
ORDER = re.compile(r"(하십시오|하세요|하라|해라|할 것)[.!\s]*$")
CITED = re.compile(r"`([^`\n]+)`")
# A code name in prose, backticked or not: an identifier with `_`, a file name, a path.
CODEISH = re.compile(r"`[^`\n]+`|\b[A-Za-z0-9]+_[A-Za-z0-9_]+\b"
                     r"|\b[\w-]+\.(?:py|tsx?|jsx?|md|json|toml|ya?ml|rs|go|java|kt|swift|sql)\b"
                     r"|[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+", re.A)
SHAPE = ('```pr-summary\n{"title": "<바뀐 결과를 쉬운 말로, 80자 이내>", '
         '"context": "<이 코드가 제품에서 무슨 일을 하는지, 처음 보는 사람에게 한두 문장>", '
         '"changes": ["<무엇이 달라졌는지 쉬운 말로 한 줄씩>"], '
         '"behavior": "<사용자·호출자가 겪는 동작 변화, 없으면 없다고>", '
         '"why": "<왜 바꿨는지, 한두 문장>", "verify": ["<어떻게 확인했는지 한 줄씩>"], '
         '"code": ["`<diff 에 있는 이름>` — <그 코드가 하는 일>"]}\n```')


def _lines(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def summary(value) -> dict | None:
    """The `pr-summary` block's fields, or None when it is not shaped as one."""

    if not isinstance(value, dict) or set(value) != set(KEYS):
        return None
    texts = ("title", "context", "behavior", "why")
    if not (all(isinstance(value[k], str) and value[k].strip() for k in texts)
            and all(_lines(value[k]) and value[k] for k in ("changes", "verify")) and _lines(value["code"])):
        return None
    return {k: " ".join(value[k].split()) if k in texts else value[k] for k in KEYS}


def _prose(told: dict) -> list[str]:
    return [t for k in PROSE for t in ([told[k]] if isinstance(told[k], str) else told[k])]


def problems(told: dict | None, diff: str) -> list[str]:
    """Why the summary cannot go up, for the worker to fix: empty when it can.
    A name it cites must be in the diff, so it cannot describe the plan instead."""

    if told is None:
        return [f"`pr-summary` is missing or not exactly the keys {', '.join(KEYS)}"]
    prose = _prose(told)
    out = [f"not Korean: {t}" for t in prose if not HANGUL.search(t)]
    if len(told["title"]) > TITLE_LIMIT:
        out.append(f"the title is {len(told['title'])} characters, over {TITLE_LIMIT}")
    out += [f"an order, not a description: {t}" for t in [*prose, *told["code"]] if ORDER.search(t)]
    out += [f"a code name in prose ({m.group(0)}), say what it does in plain words and list it under `code`: {t}"
            for t in prose if (m := CODEISH.search(t))]
    out += [f"a `code` entry names nothing in backticks: {c}" for c in told["code"] if not CITED.search(c)]
    out += [f"`{c}` is not in this branch's diff" for c in dict.fromkeys(c for t in told["code"] for c in CITED.findall(t))
            if c not in diff]
    return out


def writing(spec: dict) -> str:
    """The first request for the text, sent once the report is in."""

    return WRITE.replace("<start>", (spec.get("start_head") or "origin/HEAD")[:12])


def asking(found: list[str]) -> str:
    """The turn that sends a failed summary back once, with what failed."""

    return ("The pull request's title and summary come only from your `pr-summary`; the spec's sentences never "
            "go up. Yours failed these checks:\n" + "\n".join(f"- {p}" for p in found) +
            "\n\nChange nothing and commit nothing. Read the branch's diff and write the block again, in Korean, "
            "for a colleague who knows neither the spec nor this part of the code: what the code is for, what "
            "changed and whether anyone notices, in plain words, with code names only under `code`. Not the plan, "
            "and not a translation of the spec. Follow the guide and the examples sent before, and send only the "
            "block:\n\n" + SHAPE)


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
    `harvest.record` reads: `배경` and `변경 이유` become the record's why,
    and `바뀐 점`, the first section that is neither, its what."""

    told = spec.get("summary")
    if told:
        lines = ["## 배경", "", told["context"], "", "## 바뀐 점", "", *(f"- {c}" for c in told["changes"]), "",
                 "## 동작 변화", "", told["behavior"], "", "## 변경 이유", "", told["why"], ""]
    else:
        lines = ["## 바뀐 점", "", "작업자의 PR 요약이 두 번 모두 검사를 통과하지 못해, 확인된 사실만 올립니다. "
                 "무엇이 바뀌었는지는 아래 파일 목록과 diff 로 확인해 주세요.", ""]
    # The worker's record as it wrote it: translated, it read as slop like the spec's sentences did.
    checks = [f"- [x] {i['item']}" + (f" — {i['evidence']}" if i.get("evidence") else "") for i in spec["report"]]
    last = (spec["gate"].get("tail") or "").splitlines()[-1:] or [""]
    what = "변경이 닿는 확인" if spec["gate"].get("selection") == "mapped" else "패치 확인" if spec["gate"].get("selection") == "deferred" else "게이트"
    checks += [f"- [x] 서버의 {what} 기록 — `{spec['gate']['cmd']}` · {last[0]}".rstrip(" ·")]
    if spec["gate"].get("selection") in {"mapped", "deferred"}:
        checks += ["- [ ] 전체 게이트 — 리뷰가 허용한 커밋에서 머지 전에 돈다"]
    checks += [f"- [x] Jev 가 고른 추가 확인 — `{c['cmd']}` · 통과" for c in spec.get("checks") or []
               if c["ok"] and c.get("head") == spec["gate"].get("head")]
    lines += ["## 확인", "", *(f"- {v}" for v in (told["verify"] if told else [])), *([""] if told else []),
              f"<details><summary>확인 기록 {len(checks)}건 (원문) — {last[0] or '서버 게이트 통과'}</summary>", "",
              *checks, "", "</details>", ""]
    if told and told["code"]:
        lines += ["## 코드 위치", "", *(f"- {c}" for c in told["code"]), ""]
    if files := _files(numstat):
        lines += ["## 바뀐 파일", "", *files, ""]
    from . import refactor_run
    lines += [*sweep.section(spec.get("test_cleanup")), *refactor_run.section(spec)]
    grounds = spec["grounds"]
    cited = ", ".join(f"`{g}`" for g in grounds["pages"] + grounds["files"]) or "없음"
    lines += ["## 명세", "", f"`raw/specs/{spec['repo']}/{spec['id']}.json` · 근거: {cited}", ""]
    return "\n".join(lines)
