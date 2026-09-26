"""harvest — dig decisions out of records that already exist."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import translate

MAX_WHY = 400      # How much of the reason to carry
MAX_WHAT = 260
TRANSLATE_SECONDS = 30   # one record's title, what and why, one request

# The domain table: (name, what to look for in a branch or title, the
# triggers that call that domain in).
#
# Order matters. The first match from the top wins, so the narrow entries
# come first. A PR that is in none of them has no domain, and no domain means
# it is never injected — a file with no trigger is better than a trigger
# forced onto a common word.
DOMAINS: list[tuple[str, tuple[str, ...], list[str]]] = [
    ("vision", ("vision", "screen", "frame", "화면", "시각", "프레임"),
     ["화면", "vision", "프레임", "스크린", "캡처", "공유"]),
    ("memory", ("memory", "mem0", "recall", "기억", "회상"),
     ["기억", "memory", "회상", "mem0", "저장소.{0,4}기억"]),
    ("tts", ("tts", "qwen", "voice", "audio", "음성", "발화"),
     ["tts", "qwen", "음성", "목소리", "합성", "재생"]),
    # `프롬프트` and `응답` must not stand alone. `사용자 프롬프트를 작성해줘`
    # (a session handover) and `대화 프롬프트` (the product) are the same word,
    # and the first is far more common. Only the compounds are matched.
    ("dialogue", ("dialogue", "response", "prompt", "persona", "gemini",
                  "openai", "대화", "응답", "프롬프트"),
     ["대화\\s*(모델|프롬프트|응답|생성)", "응답\\s*(정책|수리|스키마)",
      "페르소나", "말투", "gemini", "openai"]),
    ("browser", ("browser", "frontend", "client", "브라우저", "클라이언트"),
     ["브라우저", "frontend", "클라이언트", "화면 클라"]),
    ("api", ("api", "route", "turn", "latency", "telemetry", "지연", "턴"),
     ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]),
    ("infra", ("ci", "launcher", "migration", "postgres", "런처", "게이트"),
     ["런처", "게이트", "postgres", "마이그레이션", "ci"]),
]


def commits(repo: Path, limit: int) -> list[dict]:
    """Read decisions out of commit messages, for a repository with no PRs.

    Commit messages in these repositories have the same shape as a PR body —
    one title line, then a body saying what and why. So the same extractor
    works unchanged.
    """

    sep = "\x1e"
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "log", f"-{limit}",
             f"--format=%H{sep}%ad{sep}%s{sep}%b{sep}", "--date=short"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
    except Exception:
        return []
    if done.returncode != 0:
        return []
    found = []
    for block in done.stdout.split("\x1e\n"):
        parts = block.split(sep)
        if len(parts) < 4:
            continue
        sha, date, subject, body = parts[0].strip(), parts[1], parts[2], parts[3]
        if not sha:
            continue
        found.append({
            "number": int(sha[:6], 16) % 1000,
            "sha": sha[:9],
            "title": subject,
            "body": body,
            "mergedAt": date,
            "headRefName": "",
        })
    return found


def prs(repo: Path, limit: int) -> list[dict]:
    try:
        done = subprocess.run(
            ["gh", "pr", "list", "--state", "merged", "--limit", str(limit),
             "--json", "number,title,body,mergedAt,headRefName"],
            cwd=repo, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
        )
    except Exception:
        return []
    if done.returncode != 0:
        return []
    try:
        return json.loads(done.stdout)
    except Exception:
        return []


def section(body: str, name: str) -> str:
    hit = re.search(rf"^##+\s*{name}\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S)
    return hit.group(1).strip() if hit else ""


def _prose(block: str) -> str:
    """Strip markdown heading lines from a paragraph and flatten it to one line.

    An empty string when nothing is left.
    """

    lines = [ln for ln in block.splitlines() if not re.match(r"\s*#{1,6}\s", ln)]
    return " ".join(" ".join(lines).split())


def squeeze(text: str, limit: int) -> str:
    """Strip bold and list markers, then cut on sentence boundaries."""

    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.M)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind("."), cut.rfind("다 "), cut.rfind("다."))
    return (cut[: stop + 1] if stop > limit // 2 else cut).strip() + " …"


def triggers_for(title: str, branch: str) -> tuple[str, list[str]]:
    """Which domain this decision belongs to, and the words that call it in.

    What calls a decision in is a subject, not a word. Pulling words out of
    the title catches common ones — `세션`, `어떻게`, `메모리`, `요약` — that
    have nothing to do with the decision. So one domain is chosen and only
    that domain's words are attached. A domain is also the unit these
    repositories split PRs along: two domains never share a PR.
    """

    text = f"{branch} {title}".lower()
    for domain, marks, triggers in DOMAINS:
        if any(_marks(text, m) for m in marks):
            return domain, triggers
    return "", []


def _marks(text: str, mark: str) -> bool:
    """Does this marker stand as a word of its own?

    Read as a substring, a short ASCII marker matches inside a word — `ci` in
    `de-ci-sion`, `turn` in `re-turn`, `frame` in `frame-work`. A domain
    brings triggers with it and `inject.py` injects anything with triggers
    like a rule, so a misclassification surfaces that record in every
    unrelated session.

    A Korean marker is matched by containment instead. It is routinely a
    compound with a particle attached, which a regex word boundary does not
    see, so requiring one would drop markers that really are standing as
    words. The change only narrows, so what it misses is a marker that was
    never standing as a word in the first place.
    """

    if not mark.isascii():
        return mark in text
    return re.search(rf"(?<![a-z0-9]){re.escape(mark)}(?![a-z0-9])", text) is not None


# What a record says when its PR body had nothing to squeeze. `main` counts
# the second to report the PRs whose reasons were never written down.
NO_WHAT = "(the PR body has no summary section)"
NO_WHY = "(the PR body has no reason section; the grounds for this decision were not recorded)"


def record(pr: dict, deadline: float) -> tuple[str, str]:
    """`(file name, text)` of one decision record, in English.

    The wiki is read by agents, and what they are asked arrives already
    rendered in English, so the record is written in English whatever
    language the PR was written in. `deadline` bounds the translation; past
    it, or with no key, the prose stays as the PR wrote it. The domain and
    its triggers come from the original title: they match what a person
    types, and that is Korean."""

    number = pr["number"]
    date = str(pr.get("mergedAt") or "")[:10] or "0000-00-00"
    title = " ".join(str(pr.get("title") or "").split())
    branch = str(pr.get("headRefName") or "")
    # GitHub returns the body with CRLF. The fallback below splits paragraphs
    # on `\n\n`, so without this normalisation a CRLF body is one single block:
    # `왜` comes out empty every time and `무엇` swallows a truncated copy of
    # the whole body. The record for PR #95 came out exactly like that.
    body = str(pr.get("body") or "").replace("\r\n", "\n").replace("\r", "\n")

    what = squeeze(section(body, "변경 요약"), MAX_WHAT)
    why = squeeze(section(body, "변경 이유"), MAX_WHY)
    if not (what or why):
        # A commit message with no section headings. The first paragraph is
        # the what and the rest is the why.
        #
        # A heading line is not material. Almost every PR body in these
        # repositories opens with a markdown heading, so taking the first
        # block as it stands makes the record read `무엇. ## 결론`. Records
        # 131 to 135 on 2026-09-10 all went out that way. Only the heading
        # *line* is stripped rather than the block, because with no blank line
        # between heading and body the two are one block and dropping it would
        # take the body with it.
        blocks = [_prose(b) for b in body.split("\n\n")]
        blocks = [b for b in blocks if b]
        what = squeeze(blocks[0], MAX_WHAT) if blocks else ""
        why = squeeze(" ".join(blocks[1:]), MAX_WHY) if len(blocks) > 1 else ""
    domain, trig = triggers_for(title, branch or title)
    title, what, why = translate.translate([title, what, why], translate.KO_EN, deadline)

    # The number goes in the filename. On the branch name alone, a reused name
    # collides and the later record quietly overwrites the earlier one.
    slug = re.sub(r"[^a-z0-9]+", "-", (branch or pr.get("sha", "")).lower()).strip("-")
    name = f"{date}-{number:03d}-{slug or 'commit'}"[:74]

    lines = [
        "---",
        "scope: project",
        # No domain means it is never injected. The file still exists for
        # search and for the session-start summary, but it is not forced onto
        # a common word to give it a trigger.
        "severity: contract" if (why and trig) else "severity: preference",
        f"triggers: {json.dumps(trig, ensure_ascii=False)}",
        f"domain: {domain}" if domain else "domain: ''",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"pr: {number}",
        f"merged: {date}",
        f"branch: {json.dumps(branch, ensure_ascii=False)}",
        "---",
        "",
        f"# {title}",
        "",
        f"What. {what or NO_WHAT}",
        "",
        f"Why. {why or NO_WHY}",
        "",
        f"Source. PR #{number} · `{branch}`" if branch
        else f"Source. Commit `{pr.get('sha', '')}`",
        "",
    ]
    return name, "\n".join(lines)


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="PR 본문에서 결정 기록을 캐낸다")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--from", dest="source", choices=("prs", "commits"), default="prs",
        help="PR 이 없는 저장소는 `commits`",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    repo = args.project.expanduser().resolve()
    merged = (prs if args.source == "prs" else commits)(repo, args.limit)
    if not merged:
        print(f"{args.source} 에서 아무것도 못 읽었다.")
        return 2

    out = repo / ".wiki" / "decisions"
    written, thin, homeless = 0, [], []
    by_domain: dict[str, int] = {}
    what = "머지된 PR" if args.source == "prs" else "커밋"
    print(f"# harvest — {repo.name}\n")
    print(f"{what} {len(merged)}건\n")
    for pr in merged:
        # Without `--write` only the first is shown; the rest need no translation.
        name, text = record(pr, time.monotonic() + (TRANSLATE_SECONDS if args.write else 0))
        if NO_WHY in text:
            thin.append(f"#{pr['number']} {pr['title'][:52]}")
        domain, _ = triggers_for(str(pr.get("title") or ""), str(pr.get("headRefName") or ""))
        by_domain[domain or "(없음)"] = by_domain.get(domain or "(없음)", 0) + 1
        if not domain:
            homeless.append(f"#{pr['number']} {pr['title'][:52]}")
        if args.write:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{name}.md").write_text(text, encoding="utf-8")
            written += 1

    print(f"이유 절이 있는 것 {len(merged) - len(thin)}건 · 없는 것 {len(thin)}건\n")
    print("## 도메인별\n")
    for domain, count in sorted(by_domain.items(), key=lambda i: -i[1]):
        print(f"- {domain}: {count}건")
    print()
    if homeless:
        print("## 도메인을 못 정한 PR — 파일로만 남고 주입은 안 된다\n")
        for line in homeless[:10]:
            print(f"- {line}")
        print("\n표에 표시를 더하거나, 그대로 두라. 흔한 낱말에 억지로 걸면 "
              "적중률이 오르고 정작 규칙이 밀린다.\n")
    if thin:
        print("## 근거가 안 적힌 PR — 캐도 빈 자리로 남는다\n")
        for line in thin[:12]:
            print(f"- {line}")
        print()
    if not args.write:
        name, text = record(merged[0], time.monotonic() + TRANSLATE_SECONDS)
        print(f"## 미리보기 — `.wiki/decisions/{name}.md`\n")
        print("```markdown")
        print(text.rstrip())
        print("```\n")
        print("`--write` 를 주면 전부 쓴다.")
        return 0
    print(f"썼다: {out} ({written}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
