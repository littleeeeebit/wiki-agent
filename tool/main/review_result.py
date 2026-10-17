"""Parse independent-review results without dispatch or persisted state."""

import json
import re


FIRST = re.compile(r"Round (\d+)\s*[·|—-]\s*PR #(\d+)\s*[·|—-]\s*([0-9a-f]{7,40})\b")
FINDING = re.compile(r"^\[(P0|P1|P2)\] (\S+):(\d+)")
FENCE = r"^```{}[ \t]*\r?\n(.*?)^```"
META = "finding-meta"


def bare(line: str) -> str:
    """A line without the markdown a model wraps a protocol line in."""

    return line.strip().strip("*`#> ").strip()


def parse(text: str, n: int, pr: int, head: str, known: set[str] = frozenset()) -> dict:
    """Parse round/head, findings and verdict; validate metadata against known ids.
    Missing or limited metadata leaves that finding without identity."""

    metas = re.findall(FENCE.format(META), text, re.M | re.S)
    text = re.sub(FENCE.format(META) + r"[ \t]*\r?\n?", "", text, flags=re.M | re.S)
    if re.search(rf"^\s*```{META}", text, re.M):
        # Opened and never closed: an attempt at identity, not an answer without one.
        raise ValueError("`finding-meta` 블록이 닫히지 않았다")
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if not lines:
        raise ValueError("빈 답이다")
    first = FIRST.match(bare(lines[0]))
    if not first:
        raise ValueError("첫 줄이 `Round <n> · PR #<번호> · <머리 커밋>` 이 아니다")
    if int(first[1]) != n:
        raise ValueError(f"라운드 번호가 {first[1]} 다 — {n} 이어야 한다")
    if int(first[2]) != pr:
        raise ValueError(f"PR 번호가 #{first[2]} 다 — #{pr} 이어야 한다")
    if not head.startswith(first[3]):
        raise ValueError(f"머리 커밋이 {first[3]} 다 — {head[:7]} 이어야 한다")
    last = bare(lines[-1])
    if last == "머지 허용":
        verdict = "allow"
    elif last.startswith("머지 불가"):
        verdict = "deny"
    else:
        raise ValueError("마지막 줄이 `머지 허용` 이나 `머지 불가` 가 아니다")
    findings: list[dict] = []
    for line in lines[1:-1]:
        found = FINDING.match(line.strip())
        if found:
            findings.append({"grade": found[1], "file": found[2], "line": int(found[3]),
                             "head": line.strip(), "body": []})
        elif findings:
            findings[-1]["body"].append(line)
    for f in findings:
        f["body"] = "\n".join(f["body"]).strip()
    if metas:
        try:
            meta = json.loads(metas[-1])
        except ValueError as exc:
            raise ValueError(f"`finding-meta` 가 JSON 이 아니다 — {exc}") from exc
        for f, m in zip(findings, described(meta, len(findings), known)):
            if not m.get("limited"):
                f["meta"] = m
    counts = {g: sum(f["grade"] == g for f in findings) for g in ("P0", "P1", "P2")}
    identity = "limited" if any("meta" not in f for f in findings) else "full"
    return {"verdict": verdict, "findings": findings, "counts": counts, "said": last, "identity": identity}


def described(meta, count: int, known: set[str]) -> list[dict]:
    """Validate one full or limited metadata entry per finding ordinal; ids are server-owned."""

    if not isinstance(meta, list) or not all(isinstance(m, dict) for m in meta):
        raise ValueError("`finding-meta` 는 객체의 목록이어야 한다")
    ordinals = [m.get("ordinal") for m in meta]
    if not all(type(o) is int for o in ordinals) or sorted(ordinals) != list(range(1, count + 1)):
        raise ValueError(f"`finding-meta` 의 ordinal 이 발견 1–{count} 과 하나씩 맞지 않는다")
    for m in meta:
        if "limited" in m:
            if m["limited"] is not True or set(m) != {"ordinal", "limited"}:
                raise ValueError("limited 항목은 ordinal 과 limited: true 만 받는다")
            continue
        if not all(isinstance(m.get(k), str) and m[k].strip() for k in ("component", "invariant")):
            raise ValueError("`finding-meta` 항목마다 `component` 와 `invariant` 가 있어야 한다")
        if m.get("existing_id") is not None and (not isinstance(m["existing_id"], str)
                                                 or m["existing_id"] not in known):
            raise ValueError(f"`finding-meta` 의 `existing_id` {m['existing_id']!r} 는 이 명세가 준 id 가 아니다")
    return sorted(meta, key=lambda m: m["ordinal"])


def block(name: str, text: str):
    """The JSON of the last fenced block named `name`, or `None`."""

    found = re.findall(FENCE.format(re.escape(name)), text, re.M | re.S)
    try:
        return json.loads(found[-1]) if found else None
    except ValueError:
        return None


def disposed(text: str) -> list[dict] | None:
    """The work cell's `disposition` block, entries that have their shape."""

    items = block("disposition", text)
    if not isinstance(items, list):
        return None
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("finding"), str)
            and i.get("action") in ("fixed", "not-reproduced", "disagree")
            and isinstance(i.get("id"), (str, type(None)))]
