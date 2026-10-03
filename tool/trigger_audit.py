"""Reuse the real injection functions to produce a cost and utterance table.

It does not score false positives — whether a rule belonged in a turn is a
judgement, and a number claiming to have made it would only hide that nobody
did.

    trigger_audit.py <census.jsonl>...          the census comparison
    trigger_audit.py replay <trajectory>...     today's pages over past utterances
    trigger_audit.py latency --project <repo>   the hook's own time, p50 and p95
    trigger_audit.py usage [--since] [--until]  weighted tokens per human utterance
    trigger_audit.py ab <repo>... --a <tree> --b <tree>   one task set, two hooks

Tokens are weighted by the API's rates as a stand-in for the subscription
limit, whose real weights are not published: input 1, cache write (one hour)
2, cache read 0.1, output 5.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import glob
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

import trajectory
from transcript import human_text
from wiki import (
    LIMIT, REPO_BUDGET, RULE_BUDGET, budget, compose, label, match_pages, pages,
    remembered, render_parts, repeatable, rule_paragraph, sent_whole, tag,
)
from workspace import INJECTED, SESSIONS, checkout, codex_homes, logs, parse


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
    # argparse writes its refusals here, in Korean.
    sys.stderr.reconfigure(encoding="utf-8")
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in COMMANDS:
        return COMMANDS[command](sys.argv[2:])
    return census(sys.argv[1:])


def census(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="실제 발화와 주입 결과를 대조한다")
    parser.add_argument("census", nargs="+")
    parser.add_argument("--samples", type=int, default=2, help="페이지별 적중·미적중 표본 수")
    parser.add_argument("--project", type=Path, help="지식을 측정할 저장소 (생략하면 미측정)")
    parser.add_argument("--adapter", help="기본값은 프로젝트 폴더 이름")
    args = parser.parse_args(argv)
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


# ---- reading what the hosts logged ------------------------------------------

WEIGHT = {"input": 1, "cache_write": 2, "cache_read": 0.1, "output": 5}

# What `inject.py` writes, and only it. Codex hands a hook's context over as a
# developer message, and the SessionStart listing arrives the same way.
INJECT_MARKS = ("<!-- wiki:rule-index", "<!-- wiki:english-rendering",
                "Below is what the wiki loaded")
FILED = re.compile(r"Full output saved to: (\S+?additionalContext\.txt)")


def weighted(tokens: dict) -> float:
    return sum(WEIGHT[k] * (tokens.get(k) or 0) for k in WEIGHT)


def when(text: str) -> dt.datetime:
    found = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return found if found.tzinfo else found.replace(tzinfo=dt.timezone.utc)


def pct(values: list, q: float):
    """Nearest rank. `0` for nothing."""
    values = sorted(values)
    return values[min(len(values) - 1, max(0, math.ceil(q * len(values)) - 1))] if values else 0


def transcripts() -> dict[str, tuple[str, Path]]:
    """`session → (host, file)` for every transcript on this machine.

    ponytail: a subagent's own transcript under `<session>/subagents/` is not
    read, so its tokens are missing from both sides of any comparison.
    """

    found = {}
    for sessions in codex_homes():
        for root in (sessions, sessions.parent / "archived_sessions"):
            for path in root.rglob("rollout-*.jsonl") if root.is_dir() else ():
                found[path.stem[-36:]] = ("codex", path)
    for path in SESSIONS.glob("*/*.jsonl"):
        found[path.stem] = ("claude", path)
    return found


def hook_sizes(content) -> list[tuple[int, bool]]:
    """`(characters, filed)` of each piece of a Claude `UserPromptSubmit` injection.

    Past its ceiling the host writes a piece to a file and puts a preview in
    the transcript; the size is then the file's. Pieces, because the ceiling
    applies to each — a turn where two installs both injected is two.
    """

    found = []
    for item in content if isinstance(content, list) else [content]:
        item = str(item)
        saved = FILED.search(item) if item.startswith("<persisted-output>") else None
        try:
            found.append((len(Path(saved.group(1)).read_text(encoding="utf-8", errors="replace")), True)
                         if saved else (len(item), False))
        except OSError:
            found.append((len(item), True))
    return found


def read_session(host: str, path: Path) -> dict:
    """One transcript as human turns: what each cost, and what the hook sent.

    A turn runs from a person's utterance to the next one, and every response
    in between is its cost, whatever woke it. Responses before the first
    utterance belong to no turn. A person's utterance is `transcript.human_text`
    for both hosts — census's rule, `INJECTED` and the length cap.

    Claude writes a row per content block and repeats the response's `usage`
    on each, so responses are joined by `message.id`, the last row winning —
    in one transcript 2,799 rows were 2,139 responses. A subagent's rows
    (`isSidechain`) cost the turn but are not the session's context. Codex's
    `token_usage_record` carries a `response_id` and is followed by a
    `token_count` for the same response, which is dropped. Rollouts from
    before it have only `token_count`, which repeats, so a repeat of the
    running total is dropped. Codex's `input_tokens` includes the cached part.

    Per turn: `idle`, minutes since the last response or utterance before it;
    `cold`, the first response's input that was not read from the cache —
    what coming back cost when the cache had gone cold. Claude writes it to
    the cache; Codex reports no cache write, so for it this is plain input.
    `pieces`, `hook` and `filed` from `hook_sizes` (Codex: the
    developer message `inject.py` wrote, never filed). `compacts` are
    `(time, context of the last response before it)`.
    """

    out: dict = {"cwd": None, "turns": [], "compacts": []}
    usage: dict[str, dict] = {}
    state = {"turn": None, "context": 0, "last": None, "total": None, "paired": False}

    def utterance(at: dt.datetime, text: str) -> None:
        before = [t for t in (state["last"], state["turn"] and state["turn"]["at"]) if t]
        state["turn"] = {"at": at, "text": text, "responses": [], "hook": None, "filed": False,
                         "pieces": [],
                         "idle": (at - max(before)).total_seconds() / 60 if before else None}
        out["turns"].append(state["turn"])

    def respond(key: str, at: dt.datetime, tokens: dict, context: int | None) -> None:
        if key not in usage and state["turn"]:
            state["turn"]["responses"].append(key)
        usage[key] = tokens
        state["last"] = at
        if context is not None:
            state["context"] = context

    def hooked(pieces: list[tuple[int, bool]]) -> None:
        if state["turn"]:
            state["turn"]["pieces"] += pieces
            state["turn"]["hook"] = sum(size for size, _f in state["turn"]["pieces"])
            state["turn"]["filed"] = any(filed for _s, filed in state["turn"]["pieces"])

    with path.open(encoding="utf-8", errors="replace") as handle:
        for n, line in enumerate(handle):
            rec = parse(line)
            if rec is None or not rec.get("timestamp"):
                continue
            at, kind = when(rec["timestamp"]), rec.get("type")
            if host == "claude":
                out["cwd"] = out["cwd"] or rec.get("cwd")
                if kind == "system" and rec.get("subtype") == "compact_boundary":
                    out["compacts"].append((at, state["context"]))
                elif kind == "user" and not rec.get("isSidechain") and not rec.get("isMeta"):
                    text = human_text(rec)
                    if text:
                        utterance(at, text)
                elif kind == "attachment":
                    extra = rec.get("attachment") or {}
                    if extra.get("type") == "hook_additional_context" \
                            and extra.get("hookEvent") == "UserPromptSubmit":
                        hooked(hook_sizes(extra.get("content")))
                elif kind == "assistant":
                    message = rec.get("message") or {}
                    used = message.get("usage")
                    if used and message.get("id"):
                        tokens = {"input": used.get("input_tokens") or 0,
                                  "cache_write": used.get("cache_creation_input_tokens") or 0,
                                  "cache_read": used.get("cache_read_input_tokens") or 0,
                                  "output": used.get("output_tokens") or 0}
                        respond(message["id"], at, tokens, None if rec.get("isSidechain") else
                                tokens["input"] + tokens["cache_write"] + tokens["cache_read"])
                continue
            payload = rec.get("payload") or {}
            if kind == "session_meta":
                out["cwd"] = out["cwd"] or payload.get("cwd")
            elif kind == "compacted":
                out["compacts"].append((at, state["context"]))
            elif kind == "token_usage_record":
                state["paired"] = True
                respond(str(payload.get("response_id") or n), at, *codex_tokens(payload.get("usage")))
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                total = (info.get("total_token_usage") or {}).get("total_tokens")
                # A record is followed by its own count — on this PC 7,830 of
                # 7,830 times — so only that count is the record's repeat. A
                # rollout that changed format midway keeps its earlier counts.
                if state["paired"]:
                    state["paired"] = False
                elif info.get("last_token_usage") and total != state["total"]:
                    respond(f"count:{n}", at, *codex_tokens(info["last_token_usage"]))
                state["total"] = total
            elif kind == "event_msg":
                text = None
                if payload.get("type") == "user_message":
                    text = payload.get("message")
                elif payload.get("type") == "item_completed" \
                        and (payload.get("item") or {}).get("type") == "UserMessage":
                    text = "\n".join(str(c.get("text") or "") for c in payload["item"].get("content") or []
                                     if isinstance(c, dict))
                text = human_text({"type": "user", "message": {"content": str(text)}}) if text else None
                if text:
                    utterance(at, text)
            elif kind == "response_item" and payload.get("role") == "developer":
                text = "\n".join(str(c.get("text") or "") for c in payload.get("content") or []
                                 if isinstance(c, dict))
                if any(mark in text for mark in INJECT_MARKS):
                    hooked([(len(text), False)])

    for turn in out["turns"]:
        keys = turn.pop("responses")
        tokens = Counter()
        for key in keys:
            tokens.update(usage[key])
        turn["tokens"] = {k: tokens.get(k, 0) for k in WEIGHT}
        turn["cost"] = weighted(turn["tokens"])
        first = usage[keys[0]] if keys else {}
        turn["cold"] = {k: first.get(k, 0) for k in ("input", "cache_write")}
    return out


def codex_tokens(used: dict | None) -> tuple[dict, int]:
    used = used or {}
    whole = used.get("input_tokens") or 0
    cached = used.get("cached_input_tokens") or 0
    written = used.get("cache_write_input_tokens") or 0
    return {"input": max(0, whole - cached - written), "cache_write": written,
            "cache_read": cached, "output": used.get("output_tokens") or 0}, whole


# ---- replay -----------------------------------------------------------------


def render(matched: list, limits: tuple, project, seen=frozenset(), repeat=frozenset(),
           limit: int | None = None) -> tuple:
    """`(body, full, names, squeezed)` as `inject` would send it, less the rendering.

    With no `limit` this is the hook before `--host`: no deduplication, the
    index on every turn."""

    rules, decisions, rule_parts, _repo, _t, body, squeezed = compose(
        matched, limits, "", str(project), seen, repeat, limit)
    return (body, sent_whole(rules, rule_parts),
            [label(p) for _s, _b, p in rules + decisions], squeezed)


def compacted_between(previous, at, compacts: list) -> bool:
    """Did a compact fall between this session's previous row and this one?

    A row's `at` is cut to the second and a transcript's compact time is not,
    so a compact 0.3 s before the injection in the same second would sort
    after it. Compared at the second, and inclusive at both ends: a compact
    in the same second as either row resets. Wrong that way, the replay
    sends a page in full once more than the hook did — the saving is
    understated, never overstated.
    """

    if previous is None:
        return False
    return any(previous <= c.replace(microsecond=0) <= at for c in compacts)


def recall_misses(matched: list, seen: set, repeat: set, injection: str,
                  squeezed: bool = False) -> list[str]:
    """The recall invariant — one judgement, shared by `replay` and the tests.

    Every rule page the triggers chose is in the injection word for word:
    the whole rule paragraph for a page that declared `repeat: rule` and was
    already seen or sits on a squeezed turn (`wiki.compose`), the full body
    for any other. Checking names alone goes green with the binding clauses
    gone. Decision records are left out: they never go in whole, and the
    summary `knowledge` makes of them is not what deduplication touches.
    """

    missing = []
    for _s, body, path in matched:
        name = label(path)
        if path.parent.name == "decisions":
            continue
        known = (name, tag(body)) in seen or squeezed
        need = rule_paragraph(body) if name in repeat and known and rule_paragraph(body) else body
        if need not in injection:
            missing.append(name)
    return missing


def replay(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="trajectory 를 지금 페이지로 다시 흘려 주입량을 잰다")
    parser.add_argument("trajectory", nargs="+")
    parser.add_argument("--project", type=Path, help="페이지를 읽을 저장소 (기본: 첫 trajectory 의 저장소)")
    parser.add_argument("--adapter", help="기본값은 프로젝트 폴더 이름")
    parser.add_argument("--since", help="이 시각 이후 행만 (UTC, 예: 2026-09-18)")
    parser.add_argument("--until", help="이 시각까지의 행만 — 같은 끝으로 잘라야 같은 수가 나온다")
    args = parser.parse_args(argv)
    try:
        paths = census_paths(args.trajectory)
    except ValueError as error:
        parser.error(str(error))
    project = (args.project or paths[0].resolve().parent.parent).expanduser().resolve()
    adapter = args.adapter or project.name
    since = when(args.since) if args.since else None
    until = when(args.until) if args.until else None

    available = pages(adapter, project)
    limits = budget(adapter, RULE_BUDGET, project), budget(adapter, REPO_BUDGET, project)
    repeat = repeatable(available)
    sessions: dict[object, list[dict]] = defaultdict(list)
    for path in paths:
        for i, row in enumerate(trajectory.read(path)):
            at = when(row.get("at") or "1970-01-01")
            if (since and at < since) or (until and at > until):
                continue
            # No id joins nothing — each such row is a session of its own.
            sessions[row.get("session") or (path, i)].append(row | {"_at": at})

    found = transcripts()
    missing = 0
    loads = {"기록": [0, 0], "지금 방식": [0, 0], "새 방식": [0, 0]}
    old_turn, new_turn, old_sum, new_sum = [], [], [], []
    turns_by_host, over, over_recorded = Counter(), Counter(), Counter()
    loaded_pages: Counter = Counter()
    kept, filed, misses = [], [], []
    for key, rows in sessions.items():
        rows.sort(key=lambda r: r["_at"])
        host, tx = found.get(key, (None, None)) if isinstance(key, str) else (None, None)
        missing += tx is None
        compacts = []
        if tx is not None:
            read = read_session(host, tx)
            compacts = [at for at, _context in read["compacts"]]
            for turn in read["turns"] if host == "claude" else []:
                for size, sent_to_file in turn["pieces"]:
                    (filed if sent_to_file else kept).append(size)
        limit = LIMIT.get(host or "", min(LIMIT.values()))
        before: dict[str, set] = {kind: set() for kind in loads}
        sim: list[dict] = []
        session_old = session_new = 0
        previous = None
        for row in rows:
            matched = match_pages(str(row.get("utterance") or ""), available)
            old_body, _full, names, _s = render(matched, limits, project)
            # What `inject.recall` would have read: this session's own
            # simulated rows, cleared by a compact the transcript records.
            reset = compacted_between(previous, row["_at"], compacts)
            seen = set() if reset or not isinstance(key, str) else remembered(sim, limit)
            new_body, full, _names, squeezed = render(matched, limits, project, seen, repeat, limit)
            new_size = len(new_body.encode("utf-8"))
            sim.append({"sent": new_size, "full": full, "reset": reset})
            misses += [(row.get("at"), name)
                       for name in recall_misses(matched, seen, repeat, new_body, squeezed)]
            previous = row["_at"]

            if reset:
                before["새 방식"] = set()
            for kind, got in (("기록", row.get("injected") or []), ("지금 방식", names),
                              ("새 방식", [n for n, _t in full])):
                loads[kind][0] += sum(n in before[kind] for n in got)
                loads[kind][1] += len(got)
                before[kind] |= set(got)
            loaded_pages.update(names)
            old_size = len(old_body.encode("utf-8"))
            old_turn.append(old_size)
            new_turn.append(new_size)
            session_old += old_size
            session_new += new_size
            turns_by_host[host or "?"] += 1
            over[host or "?"] += new_size > limit
            if isinstance(row.get("sent"), int):
                over_recorded[host or "?"] += row["sent"] > limit
        old_sum.append(session_old)
        new_sum.append(session_new)

    print(f"# 재생 — {sum(map(len, sessions.values()))}턴, 세션 {len(sessions)}개\n")
    print(f"대상: {project} · 어댑터: {adapter} · 기간: {args.since or '처음'} ~ {args.until or '끝'} (UTC)\n")
    print("페이지는 지금의 페이지다 — 과거 실행의 재현이 아니라 지금 규칙으로 그 발화를 받았다면이다.")
    print("크기는 UTF-8 바이트, 번역 전이고 영어본 블록은 뺐다 (`measure` 와 같은 차이). "
          "그래서 실제 `sent` 보다 작고, 한도를 넘는 턴은 실제보다 적게 나온다.")
    print("지금 방식은 호스트를 모르는 옛 훅(색인은 늘, 중복 제거 없음), "
          "새 방식은 `--host` 를 받은 훅(세션 내 중복 제거, 한도 넘는 턴은 규칙 문단만)이다.")
    print(f"`repeat: rule` 선언 페이지: {', '.join(sorted(repeat)) or '없음'}\n")
    if missing:
        print(f"transcript 를 못 찾은 세션 {missing}개는 호스트를 모르고 compact 없이 재생했다 — "
              "한도는 작은 쪽으로 셌고, 그 세션의 절감은 과대 추정이다.\n")

    print("## 반복률 — 같은 세션에 이미 실린 페이지를 또 실은 비율\n")
    print("| | 다시 실음 / 적재 | 비율 |\n| --- | ---: | ---: |")
    for kind, (again, all_) in loads.items():
        note = " (전문만)" if kind == "새 방식" else ""
        print(f"| {kind}{note} | {again:,} / {all_:,} | {again / all_:.0%} |" if all_ else f"| {kind} | 0 / 0 | — |")

    print("\n## 턴당 주입 크기\n")
    print("| | 중앙값 | p90 |\n| --- | ---: | ---: |")
    rows_ = [r for rs in sessions.values() for r in rs]
    cost = [r["cost"] for r in rows_ if isinstance(r.get("cost"), int)]
    print(f"| 기록 `cost` (글자, 규칙·결정 블록만) | {pct(cost, .5):,} | {pct(cost, .9):,} |")
    sent = [r["sent"] for r in rows_ if isinstance(r.get("sent"), int)]
    if sent:
        print(f"| 기록 `sent` (바이트, {len(sent)}턴) | {pct(sent, .5):,} | {pct(sent, .9):,} |")
    print(f"| 지금 방식 재생 (바이트) | {pct(old_turn, .5):,} | {pct(old_turn, .9):,} |")
    print(f"| 새 방식 재생 (바이트) | {pct(new_turn, .5):,} | {pct(new_turn, .9):,} |")

    print("\n## 세션 누적 주입량 (바이트)\n")
    print("| | 세션 합 중앙값 | 전체 합 |\n| --- | ---: | ---: |")
    print(f"| 지금 방식 | {pct(old_sum, .5):,} | {sum(old_sum):,} |")
    print(f"| 새 방식 | {pct(new_sum, .5):,} | {sum(new_sum):,} |")
    if sum(old_sum):
        print(f"\n감소율: 전체 합 {1 - sum(new_sum) / sum(old_sum):.0%}, "
              f"세션 합 중앙값 {1 - pct(new_sum, .5) / max(1, pct(old_sum, .5)):.0%}")

    print("\n## 호스트 한도를 넘은 턴\n")
    print("| 호스트 | 한도 (바이트) | 턴 | 새 방식 재생 초과 | 기록 `sent` 초과 |\n| --- | ---: | ---: | ---: | ---: |")
    for host, count in sorted(turns_by_host.items()):
        limit = LIMIT.get(host, min(LIMIT.values()))
        print(f"| {host} | {limit:,} | {count:,} | {over[host]:,} | {over_recorded[host]:,} |")
    if kept or filed:
        print(f"\nClaude 한도 근거 (이 세션들의 transcript): 전문으로 들어간 가장 큰 주입 "
              f"{max(kept, default=0):,}자, 파일로 빠진 가장 작은 주입 "
              f"{f'{min(filed):,}자' if filed else '없음'} — `wiki.LIMIT` 과 견줘라")

    print("\n## 페이지별 주입 크기 (바이트)\n")
    print("| 페이지 | 전문 | 규칙 문단만 | 실린 턴 |\n| --- | ---: | ---: | ---: |")
    for meta, body, path in available:
        severity = str(meta.get("severity"))
        if path.parent.name == "decisions" or severity not in ("landmine", "contract"):
            continue
        name = label(path)
        page = [(severity, body, path)]
        part = render_parts(page, None, None)[2][0]
        short = render_parts(page, None, None, repeatable={name}, squeeze=True)[2][0]
        rule_only = f"{len(short.encode('utf-8')):,}" if short != part else "—"
        print(f"| {name} | {len(part.encode('utf-8')):,} | {rule_only} | {loaded_pages[name]:,} |")

    print("\n## 회상 불변식 — 걸린 규칙 페이지가 주입에 글자 그대로 있는가\n")
    if misses:
        for at, name in misses[:20]:
            print(f"- 빠짐: {at} `{name}`")
        print(f"\n빠진 곳 {len(misses)}개 — 중복 제거가 규칙을 떨어뜨렸다")
        return 1
    print("빠진 곳 없음 — 선언 페이지는 규칙 문단이, 나머지는 전문이 모든 턴에 있다")
    return 0


# ---- latency ----------------------------------------------------------------

HEAVY = "훅 주입 인코딩 cp949 원인 진단 리뷰 루프 머지 async 비동기 pytest 마크다운 강조 화면 디자인 예산 측정"
UTTERANCES = {"빈 발화 (0장)": "", "상시 규칙만": "좋다, 그렇게 해라", "많이 걸림": HEAVY}


def latency(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="inject.py 를 훅처럼 하위 프로세스로 돌려 시간을 잰다")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--with-translation", action="store_true",
                        help="번역을 켠 종단 수치. 발화마다 꼬리를 달아 캐시를 피한다")
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    here = Path(__file__).resolve().parent

    # A throwaway copy of `.wiki/`. Running against the real one would write
    # these fake turns into its trajectory.
    root = Path(tempfile.mkdtemp())
    try:
        target = root / "project"
        if (project / ".wiki").is_dir():
            shutil.copytree(project / ".wiki", target / ".wiki",
                            ignore=shutil.ignore_patterns(trajectory.FILENAME))
        else:
            (target / ".wiki").mkdir(parents=True)
        env = dict(os.environ) | {"PYTHONIOENCODING": "utf-8"}
        if not args.with_translation:
            # The switches `test_inject.py` already uses. The cache answers
            # before the key is consulted, so it is redirected too.
            env |= {"GEMINI_API_KEY": "", "TRANSLATE_ENV": str(root / "absent.env"),
                    "TRANSLATE_CACHE": str(root / "cache.sqlite3")}
        print(f"# 훅 지연 — {args.runs}회, 번역 {'켬' if args.with_translation else '끔'}\n")
        print("파이썬 기동을 포함한다 — 훅이 실제로 치르는 값이다.\n")
        print("| 발화 | p50 (ms) | p95 (ms) |\n| --- | ---: | ---: |")
        for name, text in UTTERANCES.items():
            times = []
            for i in range(args.runs):
                prompt = f"{text} ({i})" if text and args.with_translation else text
                started = time.perf_counter()
                subprocess.run(
                    [sys.executable, str(here / "inject.py"), "--project", str(target)],
                    input=json.dumps({"prompt": prompt, "session_id": "latency"}, ensure_ascii=False),
                    capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                )
                times.append((time.perf_counter() - started) * 1000)
            print(f"| {name} | {pct(times, .5):,.0f} | {pct(times, .95):,.0f} |")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


# ---- usage ------------------------------------------------------------------

IDLE = 60  # Minutes. Past this the prompt cache (one hour) has gone cold
LENGTHS = ((1, 4), (5, 19), (20, None))


def repo_of(cwd: str | None, cache: dict) -> str | None:
    """Git identity of the nearest existing directory; never infer from a layout."""

    if not cwd:
        return None
    if cwd not in cache:
        here = Path(cwd)
        alive = next((p for p in (here, *here.parents) if p.is_dir()), None)
        _top, repo, _branch = checkout(alive) if alive else ("", "", "")
        cache[cwd] = repo or None
    return cache[cwd]


def shown(repos: set[str]) -> dict[str, str]:
    """Use clone names when unique, full paths when names collide."""
    names = Counter(Path(r).name for r in repos)
    return {r: Path(r).name if names[Path(r).name] == 1 else r for r in repos}


def usage(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="이 PC 의 transcript 로 사람 발화당 환산 토큰을 낸다")
    parser.add_argument("--since", help="UTC, 예: 2026-09-18T08:00 (기본: --until 7일 전)")
    parser.add_argument("--until", help="UTC (기본: 지금)")
    args = parser.parse_args(argv)
    until = when(args.until) if args.until else dt.datetime.now(dt.timezone.utc)
    since = when(args.since) if args.since else until - dt.timedelta(days=7)

    # `ab` stages its clones in the temporary folder. They are repositories,
    # named like the real ones, and not anyone's work.
    temp = str(Path(tempfile.gettempdir()).resolve()).lower()
    cache: dict[str, str | None] = {}
    turns: list[tuple[tuple[str, str], str, dict]] = []
    compacts: dict[tuple[str, str], list[int]] = defaultdict(list)
    skipped = Counter()
    for session, (host, path) in transcripts().items():
        try:
            if dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc) < since:
                continue
        except OSError:
            continue
        read = read_session(host, path)
        mine = [t for t in read["turns"] if since <= t["at"] < until]
        repo = repo_of(read["cwd"], cache)
        if repo is None or str(Path(read["cwd"]).resolve()).lower().startswith(temp):
            skipped["sessions"] += bool(mine)
            skipped["turns"] += len(mine)
            continue
        key = (repo, host)
        turns += [(key, session, t) for t in mine]
        compacts[key] += [c for at, c in read["compacts"] if since <= at < until]

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    length: Counter = Counter()
    for key, session, turn in turns:
        groups[key].append(turn)
        length[session] += 1
    label_of = shown({k[0] for k in groups})
    print(f"# 사용량 — {since:%Y-%m-%d %H:%M} ~ {until:%Y-%m-%d %H:%M} (UTC), "
          f"사람 발화 {len(turns):,}, 세션 {len(length):,}\n")
    print("환산은 입력 1 · 캐시 쓰기 2 · 캐시 읽기 0.1 · 출력 5 (API 요율 대리값). "
          "한 발화의 값은 그 발화부터 다음 사람 발화 전까지의 모든 응답이다. "
          "하네스 주입 발화는 사람 발화가 아니다 (`INJECTED`).\n")
    if skipped["sessions"]:
        print(f"저장소 밖이나 임시 폴더에서 돈 세션 {skipped['sessions']:,}개 "
              f"(발화 {skipped['turns']:,})는 뺐다 — 스크래치패드, 도구의 모델 호출, `ab` 의 사본.\n")

    print("## 저장소·호스트별\n")
    print("| 저장소 | 호스트 | 발화 | 발화당 중앙값 | 발화당 평균 | 환산 합 | 입력 | 캐시 쓰기 | 캐시 읽기 | 출력 |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    order = sorted(groups, key=lambda k: -sum(t["cost"] for t in groups[k]))
    for key in order:
        mine = groups[key]
        costs = [t["cost"] for t in mine]
        raw = {k: sum(t["tokens"][k] for t in mine) for k in WEIGHT}
        print(f"| {label_of[key[0]]} | {key[1]} | {len(mine):,} | {pct(costs, .5):,.0f} | "
              f"{sum(costs) / len(costs):,.0f} | {sum(costs):,.0f} | "
              + " | ".join(f"{raw[k]:,}" for k in WEIGHT) + " |")

    print("\n## 세션 길이별 — 세션의 사람 발화 수\n")
    print("| 구간 | 세션 | 발화 | 발화당 중앙값 | 발화당 평균 |\n| --- | ---: | ---: | ---: | ---: |")
    for low, high in LENGTHS:
        inside = {s for s, n in length.items() if n >= low and (high is None or n <= high)}
        costs = [t["cost"] for _k, s, t in turns if s in inside]
        name = f"{low}~{high}" if high else f"{low}+"
        print(f"| {name} | {len(inside):,} | {len(costs):,} | {pct(costs, .5):,.0f} | "
              f"{sum(costs) / len(costs) if costs else 0:,.0f} |")

    print("\n## 분해\n")
    print("- 훅 주입: 그 턴 `UserPromptSubmit` 주입의 글자 수 (Claude 는 attachment, Codex 는 "
          "developer 메시지). 파일로 빠진 턴은 호스트가 2KB 미리보기만 줬다")
    print(f"- 유휴 복귀: 직전 응답에서 {IDLE}분 넘게 지나 온 사람 발화. C 는 그 첫 응답에서 캐시로 "
          "읽지 못한 입력 (Claude 는 캐시 쓰기, Codex 는 입력)")
    print("- compact: 기간 안에 난 수와, 그 직전 응답의 문맥 (토큰)\n")
    print("| 저장소 | 호스트 | 훅 주입 턴 | 턴당 중앙값 | 합 | 파일로 빠짐 | 유휴 복귀 | C 합 | C 환산 | compact | 직전 문맥 중앙값 |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for key in order:
        mine = groups[key]
        hooks = [t["hook"] for t in mine if t["hook"] is not None]
        back = [t["cold"] for t in mine if t["idle"] is not None and t["idle"] >= IDLE]
        print(f"| {label_of[key[0]]} | {key[1]} | {len(hooks):,} | {pct(hooks, .5):,} | {sum(hooks):,} | "
              f"{sum(t['filed'] for t in mine):,} | {len(back):,} | {sum(sum(c.values()) for c in back):,} | "
              f"{sum(map(weighted, back)):,.0f} | {len(compacts[key]):,} | "
              f"{pct(compacts[key], .5):,} |")
    return 0


# ---- ab ---------------------------------------------------------------------


def tasks(project: Path, sessions: int, turns: int, seed: int) -> list[list[str]]:
    """`sessions` runs of `turns` consecutive utterances, as people typed them.

    Consecutive, because deduplication shows only from the second turn on. A
    harness line is not a person's, and a row cut at the old 500-character
    `KEEP` is not what was typed, so a session whose first `turns` utterances
    include one is not drawn. The same seed draws the same set.
    """

    by_session: dict[str, list[dict]] = defaultdict(list)
    for row in trajectory.rows(project / ".wiki"):
        if row.get("session"):
            by_session[row["session"]].append(row)
    drawn = []
    for key in sorted(by_session):
        people = [r for r in sorted(by_session[key], key=lambda r: str(r.get("at")))
                  if str(r.get("utterance") or "").strip()
                  and not any(mark in str(r["utterance"]) for mark in INJECTED)][:turns]
        if len(people) == turns and all(int(r.get("chars") or 0) == len(r["utterance"]) for r in people):
            drawn.append([r["utterance"] for r in people])
    return random.Random(seed).sample(drawn, min(sessions, len(drawn)))


def hooks_of(tree: Path) -> dict:
    """The user-level hook wiring as that worktree's own `apply` writes it.

    Run in that tree's `tool/`, so every command points at its `hook.py`.
    Handed to `claude` with `--setting-sources ""`, it is the only wiring the
    session has — a plain `claude -p` would call the user settings' hub for
    both arms.
    """

    code = ("import json, sys, apply; s = {}; "
            "apply.configure(s, None, None, sys.executable, 'claude'); "
            "print(json.dumps({'hooks': s['hooks']}))")
    done = subprocess.run([sys.executable, "-c", code], cwd=tree / "tool", capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=True)
    return json.loads(done.stdout)


def stage(project: Path, root: Path) -> Path:
    """A throwaway clone of `project`, with its `.wiki/` as it is on disk.

    `.wiki/` is copied over the clone because `adapter.toml` is not
    committed, and without it `hook.py` finds no attached repository and both
    arms pass silently. The trajectory starts empty, so its rows are this run's.
    """

    target = root / project.name
    subprocess.run(["git", "clone", "--quiet", "--local", str(project), str(target)],
                   check=True, capture_output=True)
    shutil.copytree(project / ".wiki", target / ".wiki", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(trajectory.FILENAME))
    return target


def spent(result: dict) -> dict:
    """What one `claude -p` call used, every model it called included."""

    models = (result.get("modelUsage") or {}).values()
    if models:
        return {"input": sum(m.get("inputTokens") or 0 for m in models),
                "cache_write": sum(m.get("cacheCreationInputTokens") or 0 for m in models),
                "cache_read": sum(m.get("cacheReadInputTokens") or 0 for m in models),
                "output": sum(m.get("outputTokens") or 0 for m in models)}
    used = result.get("usage") or {}
    return {"input": used.get("input_tokens") or 0,
            "cache_write": used.get("cache_creation_input_tokens") or 0,
            "cache_read": used.get("cache_read_input_tokens") or 0,
            "output": used.get("output_tokens") or 0}


def arm_ran(tree: Path, staged: Path, session: str) -> str | None:
    """Why this arm's hook is not the one that ran, or `None` when it is.

    Read off what the run left. The staged trajectory has rows for the
    session: a hook ran and recorded. The session's injection names `tree` as
    the hub in its source map: the hook that ran was this arm's.
    """

    if not any(r.get("session") == session for r in trajectory.rows(staged / ".wiki")):
        return "trajectory 에 이 세션의 행이 없다 — 훅이 안 돌았다"
    hub = f"Hub wiki `{tree.resolve()}`"
    path = next((p for p in logs(staged) if p.stem == session), None)
    if path is None:
        return "이 세션의 transcript 를 못 찾았다"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        extra = (parse(line) or {}).get("attachment") or {}
        if extra.get("type") != "hook_additional_context" or extra.get("hookEvent") != "UserPromptSubmit":
            continue
        content = extra.get("content")
        for item in map(str, content if isinstance(content, list) else [content]):
            # Past the host's ceiling the injection is in a file, and the
            # source map can sit past the preview.
            saved = FILED.search(item)
            if saved and Path(saved.group(1)).is_file():
                item = Path(saved.group(1)).read_text(encoding="utf-8", errors="replace")
            if hub in item:
                return None
    return f"주입의 허브가 {tree} 가 아니다"


def ab(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="같은 발화를 두 훅으로 흘려 환산 토큰을 견준다")
    parser.add_argument("repo", nargs="+", type=Path, help="발화를 뽑고 사본을 만들 저장소")
    parser.add_argument("--a", type=Path, required=True, help="A 팔의 허브 작업트리 (지금 main)")
    parser.add_argument("--b", type=Path, required=True, help="B 팔의 허브 작업트리 (바뀐 브랜치)")
    parser.add_argument("--model", help="claude --model. 착수 때 사람에게 묻는다")
    parser.add_argument("--sessions", type=int, default=3)
    parser.add_argument("--turns", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--dry-run", action="store_true", help="과제와 배선만 보이고 모델은 부르지 않는다")
    args = parser.parse_args(argv)
    arms = {"A": args.a.expanduser().resolve(), "B": args.b.expanduser().resolve()}
    repos = [r.expanduser().resolve() for r in args.repo]

    work = {repo: tasks(repo, args.sessions, args.turns, args.seed) for repo in repos}
    total = sum(len(s) * args.turns for s in work.values()) * len(arms) * args.repeats
    print(f"# A/B — 모델 {args.model or '기본'}, 반복 {args.repeats}, 모두 {total}턴\n")
    for name, tree in arms.items():
        print(f"- {name}: {tree}")
    for repo, sessions in work.items():
        print(f"- {repo.name}: 세션 {len(sessions)}개 × 발화 {args.turns}")
    if any(len(s) < args.sessions for s in work.values()):
        print(f"\n발화가 {args.turns}개 넘게 온전히 남은 세션이 모자란 저장소가 있다 — 뽑힌 만큼만 돈다.")

    root = Path(tempfile.mkdtemp(prefix="wiki-ab-"))
    try:
        settings = {}
        for name, tree in arms.items():
            settings[name] = root / f"settings-{name}.json"
            settings[name].write_text(json.dumps(hooks_of(tree)), encoding="utf-8")
        if args.dry_run:
            return 0
        from agent import cli_command

        base = [*cli_command("claude"), "-p", "--output-format", "json", "--permission-mode", "plan",
                "--setting-sources", "", *(["--model", args.model] if args.model else [])]
        results = []  # (repo, arm, repeat, tokens, sent)
        for repo, sessions in work.items():
            for repeat in range(args.repeats):
                # Arms alternate inside a repeat, so drift over the run lands on both.
                for name, tree in arms.items():
                    staged = stage(repo, root / f"{repo.name}-{name}-{repeat}")
                    tokens: Counter = Counter()
                    for number, utterances in enumerate(sessions):
                        session = None
                        for prompt in utterances:
                            done = subprocess.run(
                                [*base, "--settings", str(settings[name]),
                                 *(["--resume", session] if session else [])],
                                input=prompt, cwd=staged, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=1800)
                            result = json.loads(done.stdout or "{}")
                            if done.returncode or result.get("is_error") or not result.get("session_id"):
                                print(f"\n{repo.name} {name}: claude 실패 ({done.returncode}) "
                                      f"{(done.stderr or done.stdout)[-500:]}")
                                return 1
                            session = result["session_id"]
                            tokens.update(spent(result))
                        if number == 0:
                            wrong = arm_ran(tree, staged, session)
                            if wrong:
                                print(f"\n{repo.name} {name}: 경로 확인 실패 — {wrong}. 멈춘다.")
                                return 1
                    sent = [r["sent"] for r in trajectory.rows(staged / ".wiki")
                            if isinstance(r.get("sent"), int)]
                    results.append((repo.name, name, repeat, dict(tokens), sent))
                    print(f"  {repo.name} {name} #{repeat + 1}: {weighted(tokens):,.0f}", flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n| 저장소 | 팔 | 반복 | 환산 합 | 입력 | 캐시 쓰기 | 캐시 읽기 | 출력 | 주입 바이트 합 | 턴당 주입 중앙값 |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for repo, name, repeat, tokens, sent in results:
        print(f"| {repo} | {name} | {repeat + 1} | {weighted(tokens):,.0f} | "
              + " | ".join(f"{tokens.get(k, 0):,}" for k in WEIGHT)
              + f" | {sum(sent):,} | {pct(sent, .5):,} |")
    print("\n| 저장소 | A 평균 | B 평균 | 감소율 | 범위 (반복끼리) |\n| --- | ---: | ---: | ---: | --- |")
    for repo in [*dict.fromkeys(r[0] for r in results), None]:
        mine = [r for r in results if repo is None or r[0] == repo]
        side = {name: [sum(weighted(r[3]) for r in mine if r[1] == name and r[2] == k)
                       for k in range(args.repeats)] for name in arms}
        a, b = (sum(side[n]) / len(side[n]) for n in arms)
        cuts = [1 - y / x for x, y in zip(side["A"], side["B"]) if x]
        print(f"| {repo or '모두'} | {a:,.0f} | {b:,.0f} | {1 - b / a:.1%} | "
              f"{min(cuts):.1%} ~ {max(cuts):.1%} |" if a and cuts else f"| {repo or '모두'} | — | — | — | — |")
    return 0


COMMANDS = {"replay": replay, "latency": latency, "usage": usage, "ab": ab}


if __name__ == "__main__":
    raise SystemExit(main())
