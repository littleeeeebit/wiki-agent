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
so the prose carries no code names at all. Each change names where it
happened right after it, in `where`, the one place code names go."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import decision
from session_state import run as git

from . import decisions, sweep

# The turn that asks for the text right before the pull request: the shared guide, then one section per
# `pr.form` form (`decisions.FORMS`), each with its spec → PR example where it has one.
WRITE = (Path(__file__).resolve().parents[1] / "prompts" / "pr-write.md").read_text(encoding="utf-8").strip()
_body, _now = WRITE.split("\n## Now\n")
SHARED, *_parts = re.split(r"^## Form: (\S+)[ \t]*\r?\n", _body, flags=re.M)
SECTIONS = {_parts[i]: _parts[i + 1].strip() for i in range(0, len(_parts), 2)}
NOW = "## Now\n\n" + _now.strip()
# Paths a reader checks by listing them: data, recorded results, notebooks, lockfiles, images.
DATA = re.compile(r"\.(json|jsonl|csv|tsv|parquet|ipynb|lock|png|jpe?g|gif|webp|svg|pdf)$", re.I)
FORM_FILES = 40
FILES_SHOWN = 30
TITLE_LIMIT = 80
KEYS = ("title", "context", "changes", "behavior", "why", "verify")
HANGUL = re.compile(r"[가-힣]")
# An order to the worker, as a spec's instructions read once translated.
ORDER = re.compile(r"(하십시오|하세요|하라|해라|할 것)[.!\s]*$")
CITED = re.compile(r"`([^`\n]+)`")
# A code name in prose, backticked or not: an identifier with `_` (`_helper`, `__init__` too), a file
# name, a path of two slashes or more — `A/B` and `HTTP/2` are prose.
CODEISH = re.compile(r"`[^`\n]+`|(?<!\w)(?=\w*_)(?=\w*[A-Za-z0-9])\w+"
                     r"|\b[\w-]+\.(?:py|tsx?|jsx?|md|json|toml|ya?ml|rs|go|java|kt|swift|sql)\b"
                     r"|\b[\w.-]+/[\w.-]+/[\w./-]+", re.A)
SHAPE = ('```pr-summary\n{"title": "<바뀐 결과를 쉬운 말로, 80자 이내>", '
         '"context": "<이 코드가 제품에서 무슨 일을 하는지, 처음 보는 사람에게 한두 문장>", '
         '"changes": [{"what": "<무엇이 달라졌는지 쉬운 말로>", "where": ["`<diff 에 있는 파일이나 함수>`"]}], '
         '"behavior": "<사용자·호출자가 겪는 동작 변화, 없으면 없다고>", '
         '"why": "<왜 바꿨는지, 한두 문장>", "verify": ["<어떻게 확인했는지 한 줄씩>"]}\n```')


def _lines(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def summary(value) -> dict | None:
    """The `pr-summary` block's fields, or None when it is not shaped as one."""

    if not isinstance(value, dict) or set(value) != set(KEYS):
        return None
    texts = ("title", "context", "behavior", "why")

    def change(c) -> bool:
        return (isinstance(c, dict) and set(c) == {"what", "where"} and isinstance(c["what"], str)
                and c["what"].strip() and _lines(c["where"]))   # empty only for a change that names no place

    changes = value["changes"]
    if not (all(isinstance(value[k], str) and value[k].strip() for k in texts) and _lines(value["verify"])
            and value["verify"] and isinstance(changes, list) and changes and all(map(change, changes))):
        return None
    return {**{k: " ".join(value[k].split()) for k in texts}, "verify": value["verify"],
            "changes": [{"what": " ".join(c["what"].split()), "where": c["where"]} for c in changes]}


def _prose(told: dict) -> list[str]:
    return [told["title"], told["context"], *(c["what"] for c in told["changes"]), told["behavior"], told["why"],
            *told["verify"]]


def problems(told: dict | None, diff: str) -> list[str]:
    """Why the summary cannot go up, for the worker to fix: empty when it can.
    A name it cites must be in the diff, so it cannot describe the plan instead."""

    if told is None:
        return [f"`pr-summary` is missing or not exactly the keys {', '.join(KEYS)}"]
    prose = _prose(told)
    out = [f"not Korean: {t}" for t in prose if not HANGUL.search(t)]
    if len(told["title"]) > TITLE_LIMIT:
        out.append(f"the title is {len(told['title'])} characters, over {TITLE_LIMIT}")
    where = [w for c in told["changes"] for w in c["where"]]
    out += [f"an order, not a description: {t}" for t in [*prose, *where] if ORDER.search(t)]
    out += [f"a code name in prose ({m.group(0)}), say what it does in plain words and name it in that change's "
            f"`where`: {t}" for t in prose if (m := CODEISH.search(t))]
    out += [f"a `where` entry names nothing in backticks: {w}" for w in where if not CITED.search(w)]
    out += [f"`{c}` is not in this branch's diff" for c in dict.fromkeys(c for w in where for c in CITED.findall(w))
            if c not in diff]
    return out


def guide(form: str) -> str:
    """The shared rules and `form`'s section, as the worker reads them."""

    return f"{SHARED.strip()}\n\n## Form: {form}\n\n{SECTIONS[form]}\n\n{NOW}"


def form_facts(spec: dict, numstat: str) -> dict:
    """What the `pr.form` choice reads off the branch, code's baseline and Jev alike: the goal, the
    diff's files with their line counts (`None` for a binary file), the artifact root, the review
    criteria asked and a survey run's refactoring mode."""

    rows = [line.split("\t", 2) for line in numstat.splitlines() if line.count("\t") >= 2]
    return {"goal": spec["goal"], "artifact_root": spec.get("artifact_root"),
            "criteria": list((spec.get("review") or {}).get("criteria") or []),
            "refactor_mode": (spec.get("refactor") or {}).get("mode"),
            "files": [{"path": p, "added": int(a) if a.isdigit() else None, "removed": int(d) if d.isdigit() else None}
                      for a, d, p in rows]}


def form_baseline(facts: dict) -> str:
    """Code's `pr.form`, from the diff alone. The wiki's own indexes and records under `.wiki/` say
    nothing of the change and are left out, unless they are all there is. Code never reads a fix
    from a diff: that is left to the feature form."""

    from . import review_contract   # it reaches `specs`, which imports this module

    if "refactor" in facts["criteria"] or facts["refactor_mode"] == "preserve":
        return "refactor-no-behaviour"
    files = [f for f in facts["files"] if not f["path"].startswith(".wiki/")] or facts["files"]
    root = (facts["artifact_root"] or "").strip("/")
    if files and all(review_contract.prose(f["path"]) or (root and f["path"].startswith(root + "/")) for f in files):
        return "plan-document"
    if files and all(f["added"] is None or DATA.search(f["path"]) for f in files):
        return "files-only"
    return "feature-with-behaviour-change"


def form_state(facts: dict) -> dict:
    """What Jev reads for `pr.form`: English, the paths sent as they are."""

    files = facts["files"]
    return {"task": "A coding agent finished this change and is about to write its pull request.",
            "goal": facts["goal"], "files_changed": len(files),
            "lines": {"added": sum(f["added"] or 0 for f in files), "removed": sum(f["removed"] or 0 for f in files)},
            "changed_files": files[:FORM_FILES], "review_criteria": facts["criteria"],
            "artifact_root": {"path": facts["artifact_root"]} if facts["artifact_root"] else None,
            "refactoring_mode": facts["refactor_mode"]}


def writing(spec: dict, repo: Path, path: Path, cancel: threading.Event | None = None) -> str:
    """The first request for the text, sent once the report is in, in the form code's baseline reads
    off the diff. Jev's `pr.form` is recorded beside it (`decisions.choose_form`); only a fitted point
    a person activated hands Jev's form on instead."""

    start = spec.get("start_head") or "origin/HEAD"
    facts = form_facts(spec, git(path, "-c", "core.quotepath=off", "diff", "--numstat", start, "HEAD"))
    form = decisions.choose_form("pr.form", form_state(facts), form_baseline(facts), log=(spec["repo"], spec["id"]),
                                 occasion=f"pr:{(spec.get('writing') or {}).get('head', '')}",
                                 cfg=decision.config(repo), cancel=cancel)["form"]
    return guide(form).replace("<start>", start[:12])


def asking(found: list[str]) -> str:
    """The turn that sends a failed summary back once, with what failed."""

    return ("The pull request's title and summary come only from your `pr-summary`; the spec's sentences never "
            "go up. Yours failed these checks:\n" + "\n".join(f"- {p}" for p in found) +
            "\n\nChange nothing and commit nothing. Read the branch's diff and write the block again, in Korean, "
            "for a colleague who knows neither the spec nor this part of the code: what the code is for, what "
            "changed and whether anyone notices, in plain words, with code names only in each change's `where`. Not the plan, "
            "and not a translation of the spec. Follow the guide and the examples sent before, and send only the "
            "block:\n\n" + SHAPE)


def title_of(spec: dict) -> str:
    told = summary(spec.get("summary"))   # a summary saved in an earlier shape counts as none
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

    told = summary(spec.get("summary"))
    if told:
        lines = ["## 배경", "", told["context"], "", "## 바뀐 점", "",
                 *(line for c in told["changes"]
                   for line in (f"- {c['what']}", *([f"  - 위치: {', '.join(c['where'])}"] if c["where"] else []))), "",
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
    if files := _files(numstat):
        lines += ["## 바뀐 파일", "", *files, ""]
    from . import refactor_run
    lines += [*sweep.section(spec.get("test_cleanup")), *refactor_run.section(spec)]
    grounds = spec["grounds"]
    cited = ", ".join(f"`{g}`" for g in grounds["pages"] + grounds["files"]) or "없음"
    lines += ["## 명세", "", f"`raw/specs/{spec['repo']}/{spec['id']}.json` · 근거: {cited}", ""]
    return "\n".join(lines)
