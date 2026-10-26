"""The pull request a person reads: a title that says what changed, and a body
that goes from what changed and why to how it was checked.

The agents write in English; GitHub is read by a person, so the prose goes up
in Korean. Commands and their output stay as they ran."""

from __future__ import annotations

import time

import translate

from . import sweep

TRANSLATE_SECONDS = 60   # the pull request's prose into Korean, one request
FILES_SHOWN = 30
RISK = {"high": "높음", "medium": "중간", "low": "낮음"}


def summary(value) -> dict | None:
    """The worker's `pr-summary` block, or None when it is not one: then the
    pull request falls back to the spec's own words."""

    def texts(v) -> bool:
        return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)

    if not isinstance(value, dict):
        return None
    title, changes, review = value.get("title"), value.get("changes"), value.get("review", [])
    if not (isinstance(title, str) and title.strip() and texts(changes) and changes and texts(review)):
        return None
    return {"title": " ".join(title.split()), "changes": changes, "review": review}


def _shown(spec: dict) -> dict:
    """What the body explains, in the agents' English. The spec's goal is the
    plan the worker started from, so it is the title only when nothing better says what changed."""

    told = spec.get("summary") or {}
    detail = (spec.get("refactor") or {}).get("detail") or {}
    risk = detail.get("risk") or {}
    return {"title": told.get("title") or detail.get("title") or spec["goal"],
            "changes": told.get("changes") or [], "review": told.get("review") or [],
            "problem": detail.get("problem") or "", "keep": detail.get("keep") or [],
            "risk": risk.get("level") or "", "risk_why": risk.get("why") or "", "rollback": detail.get("rollback") or ""}


def korean(spec: dict) -> dict:
    """The spec as the pull request shows it, its prose in Korean under
    `shown`. Translation fails open: a line it could not translate goes up as it was."""

    kinds = ("what", "why", "rejected")
    shown = _shown(spec)
    texts = [spec["goal"], *(d[k] for d in spec["decisions"] for k in kinds), *(i["item"] for i in spec["report"]),
             shown["title"], *shown["changes"], *shown["review"], shown["problem"], *shown["keep"],
             shown["risk_why"], shown["rollback"]]
    done = iter(translate.translate(texts, translate.EN_KO, time.monotonic() + TRANSLATE_SECONDS))

    def take(n: int) -> list[str]:
        return [next(done) for _ in range(n)]

    goal = next(done)
    decisions = [{k: next(done) for k in kinds} for _ in spec["decisions"]]
    report = [{**i, "item": next(done)} for i in spec["report"]]
    shown = {**shown, "title": next(done), "changes": take(len(shown["changes"])),
             "review": take(len(shown["review"])), "problem": next(done), "keep": take(len(shown["keep"])),
             "risk_why": next(done), "rollback": next(done)}
    return {**spec, "goal": goal, "decisions": decisions, "report": report, "shown": shown}


def title_of(spec: dict) -> str:
    return (spec.get("shown") or _shown(spec))["title"]


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
    `변경 이유` its why; `loop.minimal` reads `변경 이유`'s bullets as decisions."""

    shown = spec.get("shown") or _shown(spec)
    lines = ["## 변경 요약", "", *([f"- {c}" for c in shown["changes"]] or [spec["goal"]]), ""]
    if shown["problem"] or spec["decisions"]:
        lines += ["## 변경 이유", ""]
        if shown["problem"]:
            lines += [shown["problem"], ""]
        for d in spec["decisions"]:
            lines.append(f"- {d['what']}" + (f" — {d['why']}" if d["why"] else "")
                         + (f" (버린 것: {d['rejected']})" if d["rejected"] else ""))
        lines.append("")
    if shown["keep"]:
        lines += ["## 그대로 지킨 것", "", *(f"- {k}" for k in shown["keep"]), ""]
    if files := _files(numstat):
        lines += ["## 바뀐 파일", "", *files, ""]
    review = [*shown["review"]]
    if shown["risk"]:
        review.append(f"위험도 {RISK.get(shown['risk'], shown['risk'])}" + (f" — {shown['risk_why']}" if shown["risk_why"] else ""))
    if shown["rollback"]:
        review.append(f"되돌리기 — {shown['rollback']}")
    if review:
        lines += ["## 리뷰 포인트", "", *(f"- {r}" for r in review), ""]
    checks = [f"- [x] {i['item']}" + (f" — {i['evidence']}" if i.get("evidence") else "") for i in spec["report"]]
    last = (spec["gate"].get("tail") or "").splitlines()[-1:] or [""]
    what = "변경이 닿는 확인" if spec["gate"].get("selection") == "mapped" else "패치 확인" if spec["gate"].get("selection") == "deferred" else "게이트"
    checks += [f"- [x] 서버의 {what} 기록 — `{spec['gate']['cmd']}` · {last[0]}".rstrip(" ·")]
    if spec["gate"].get("selection") in {"mapped", "deferred"}:
        checks += ["- [ ] 전체 게이트 — 리뷰가 허용한 커밋에서 머지 전에 돈다"]
    checks += [f"- [x] Jev 가 고른 추가 확인 — `{c['cmd']}` · 통과" for c in spec.get("checks") or []
               if c["ok"] and c.get("head") == spec["gate"].get("head")]
    # The commands run for lines; folded, they no longer bury what changed.
    lines += ["## 확인", "", f"<details><summary>확인 기록 {len(checks)}건 — {last[0] or '서버 게이트 통과'}</summary>", "",
              *checks, "", "</details>", ""]
    from . import refactor_run
    lines += [*sweep.section(spec.get("test_cleanup")), *refactor_run.section(spec)]
    grounds = spec["grounds"]
    cited = ", ".join(f"`{g}`" for g in grounds["pages"] + grounds["files"]) or "없음"
    lines += ["## 명세", "", f"`raw/specs/{spec['repo']}/{spec['id']}.json` · 근거: {cited}", ""]
    return "\n".join(lines)
