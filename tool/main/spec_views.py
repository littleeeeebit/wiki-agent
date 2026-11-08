"""spec_views — a spec's requirements section, its version, and the two views rendered from one snapshot.

A spec record (`specs`) may carry `requirements`: numbered items with their
grounds, decision status and verification. From one frozen snapshot of the
record, code renders two views — `human`, the Markdown a person reviews, and
`agent`, the structured input a write session receives — and `equivalent`
proves they carry the same ids, statuses, conditions, negations, units and
verify items. No model call takes part in either rendering.

The agent view reaches a write session only while the hub's `.env` says
`SPEC_VIEWS=on`. Off, the default, today's agent input is unchanged.
`docs/plans/loop/3-spec.md` ("Requirements and two views") is the design.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

import decision
from common import settings, swap

KINDS = ("original", "policy", "repository", "decision")
STATUSES = ("confirmed", "assumed", "undecided", "not_applicable")
CONDITIONS = ("preconditions", "postconditions", "invariants", "failures")
KEYS = frozenset({"id", "text", "source", "status", *CONDITIONS, "depends_on", "context", "verify", "global"})
RID = re.compile(r"R[1-9]\d*")
# Read off every text of a requirement: a dropped "not" or a changed unit is a different requirement.
NEGATION = re.compile(r"\b(?:not|never|no|none|without|cannot|neither|nor)\b|n't\b", re.I)
UNIT = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|ms|s|seconds?|minutes?|min|hours?|h|days?|bytes?|[KMG]B|lines?|"
                  r"tokens?|characters?|chars?|turns?|rounds?|files?)(?![\w])", re.I)
SECTION = "## Requirements (agent view)"
VERSION = re.compile(r'"spec_version": (\d+)')
LABELS = {"preconditions": "Precondition", "postconditions": "Postcondition", "invariants": "Invariant",
          "failures": "Failure"}
CONDITION = {label: kind for kind, label in LABELS.items()}


# -- The record -------------------------------------------------------------

def on() -> bool:
    """`SPEC_VIEWS=on` in the hub `.env` (or the environment). Anything else is off."""

    try:
        found = settings.entries(decision.env_file())
    except (OSError, ValueError):
        found = {}
    return (settings.pick(found, "SPEC_VIEWS", "off")[0] or "").strip().lower() == "on"


def _texts(value, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{what} 는 비지 않은 글줄의 목록이어야 한다")
    return [" ".join(v.split()) for v in value]


def requirements(value) -> list[dict]:
    """`value` checked and normalized, raising `ValueError` that names the item and the key."""

    if not isinstance(value, list):
        raise ValueError("`requirements` 는 목록이어야 한다")
    out, seen = [], set()
    for n, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValueError(f"`requirements` {n}번째 항목은 객체여야 한다")
        rid = item.get("id")
        if not isinstance(rid, str) or not RID.fullmatch(rid):
            raise ValueError(f"`requirements` {n}번째 항목의 `id` 는 R1, R2 … 여야 한다 — {rid!r}")
        where = f"`requirements` {rid}"
        if unknown := sorted(set(item) - KEYS):
            raise ValueError(f"{where}: 모르는 키 — {', '.join(unknown)}")
        if rid in seen:
            raise ValueError(f"{where}: id 가 겹친다")
        seen.add(rid)
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}: `text` 가 비었다")
        source = item.get("source")
        if not isinstance(source, dict) or set(source) != {"kind", "locator"} or source["kind"] not in KINDS \
                or not isinstance(source["locator"], str) or not source["locator"].strip():
            raise ValueError(f"{where}: `source` 는 {{kind: {'|'.join(KINDS)}, locator}} 여야 한다")
        if item.get("status") not in STATUSES:
            raise ValueError(f"{where}: `status` 는 {', '.join(STATUSES)} 중 하나다 — {item.get('status')!r}")
        if "global" in item and not isinstance(item["global"], bool):
            raise ValueError(f"{where}: `global` 은 참·거짓이다")
        verify = item.get("verify") or []
        if not isinstance(verify, list) or not all(
                isinstance(v, dict) and set(v) == {"item", "expected"}
                and all(isinstance(v[k], str) and v[k].strip() for k in v) for v in verify):
            raise ValueError(f"{where}: `verify` 항목마다 `item` 과 `expected` 가 있어야 한다")
        made = {"id": rid, "text": " ".join(text.split()), "source": {"kind": source["kind"],
                "locator": " ".join(source["locator"].split())}, "status": item["status"]}
        for key in (*CONDITIONS, "depends_on", "context"):
            if key in item:
                made[key] = _texts(item[key], f"{where}: `{key}`")
        if verify:
            made["verify"] = [{k: " ".join(v[k].split()) for k in ("item", "expected")} for v in verify]
        if item.get("global"):
            made["global"] = True
        out.append(made)
    for r in out:
        if bad := [t for t in r.get("depends_on", []) if t not in seen or t == r["id"]]:
            raise ValueError(f"`requirements` {r['id']}: `depends_on` 이 정의되지 않은 id 를 가리킨다 — {', '.join(bad)}")
    return out


def digest(reqs: list[dict]) -> str:
    """sha256 of the canonical requirements JSON."""

    canonical = json.dumps(reqs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stamped(current: dict | None, spec: dict) -> None:
    """Before every save: `original` and each kept requirement's `source` stay as stored, and
    `spec_version` moves on whenever the requirements change. The one place all save paths pass."""

    before = current if current is not None else spec
    if current is not None and "original" in current:
        spec["original"] = current["original"]
    if "requirements" not in spec:
        return
    kept = {r["id"]: r["source"] for r in (current or {}).get("requirements") or []}
    for r in spec["requirements"]:
        if r["id"] in kept:
            r["source"] = kept[r["id"]]
    now = digest(spec["requirements"])
    if now != before.get("spec_digest"):
        spec["spec_version"] = (before.get("spec_version") or 0) + 1
    else:
        spec["spec_version"] = before.get("spec_version") or 1
    spec["spec_digest"] = now


def introduced(old: list[dict], new: list[dict], where: str) -> None:
    """A requirement a revision adds is a decision taken during the work, whatever it claimed."""

    have = {r["id"] for r in old}
    for r in new:
        if r["id"] not in have:
            r["source"] = {"kind": "decision", "locator": where}


def requested(rows: list[dict]) -> str:
    """The person's own words in the conversation that settled the spec, verbatim and in order."""

    return "\n\n".join(r.get("said", r.get("text", "")) for r in rows if r.get("role") == "user")


def blocked(spec: dict) -> list[tuple[str, str]]:
    """`(requirement, undecided target)` for every dependency on an undecided requirement."""

    status = {r["id"]: r["status"] for r in spec.get("requirements") or []}
    return [(r["id"], t) for r in spec.get("requirements") or [] for t in r.get("depends_on", [])
            if status.get(t) == "undecided"]


# -- The two views ----------------------------------------------------------

def human(spec: dict) -> str:
    """The review document: purpose, behaviour, constraints, undecided items, conflicts and the original."""

    reqs = spec.get("requirements") or []
    status = {r["id"]: r["status"] for r in reqs}
    out = [f"# {spec['id']} — revision {spec.get('rev')}, spec version {spec.get('spec_version')}", "",
           "## Purpose", "", spec["goal"], "", "## Behaviour", ""]
    for r in reqs:
        out += [f"### {r['id']} — {r['status']}", "", r["text"], "",
                f"- Source: {r['source']['kind']} — {r['source']['locator']}"]
        out += [f"- {LABELS[k]}: {c}" for k in CONDITIONS for c in r.get(k, [])]
        out += [f"- Depends on: {t}" for t in r.get("depends_on", [])]
        out += [f"- Context: `{p}`" for p in r.get("context", [])]
        out += [f"- Verify: {v['item']} ⇒ {v['expected']}" for v in r.get("verify", [])]
        out += ["- Global: applies to every task"] if r.get("global") else []
        out.append("")
    out += ["## Constraints", ""]
    out += [f"- Out of scope: {o}" for o in spec.get("out") or []]
    out += [f"- Every task carries {r['id']}" for r in reqs if r.get("global")]
    out += ["", "## Undecided", ""]
    out += [f"- {r['id']}: {r['text']}" for r in reqs if r["status"] == "undecided"] or ["- None"]
    out += ["", "## Conflicts", ""]
    out += [f"- {r['id']} depends on {t}, which is {status[t]}" for r in reqs for t in r.get("depends_on", [])
            if status.get(t) in ("undecided", "not_applicable")] or ["- None"]
    out += ["", "## Original request (verbatim)", ""]
    original = spec.get("original")
    out += [f"> {line}" if line else ">" for line in original.split("\n")] if original else ["Not recorded."]
    return "\n".join(out) + "\n"


def _closure(by_id: dict, start: list[str]) -> list[str]:
    seen, todo = [], list(start)
    while todo:
        rid = todo.pop(0)
        if rid not in seen:
            seen.append(rid)
            todo += by_id[rid].get("depends_on", []) if rid in by_id else []
    return seen


def agent(spec: dict, task: list[str] | None = None) -> dict:
    """The structured input for a write session. `task` selects requirement ids; `None` is all of them.

    A selection must hold every `global` requirement, and every `depends_on` target it reaches must
    exist: its contract travels beside the selection. Otherwise `ValueError` names the missing ids —
    never a view that silently lost one."""

    reqs = spec.get("requirements") or []
    by_id = {r["id"]: r for r in reqs}
    chosen = [r["id"] for r in reqs] if task is None else list(dict.fromkeys(task))
    missing = [rid for rid in chosen if rid not in by_id]
    missing += [r["id"] for r in reqs if r.get("global") and r["id"] not in chosen]
    reached = [t for t in _closure(by_id, chosen) if t not in chosen]
    missing += [t for t in reached if t not in by_id]
    if missing:
        raise ValueError(f"작업 선택이 요구사항을 떨어뜨린다 — {', '.join(dict.fromkeys(missing))}")
    return {"spec_version": spec.get("spec_version"), "spec_digest": spec.get("spec_digest"),
            "requirements": [by_id[rid] for rid in chosen],
            "contracts": [{k: by_id[t][k] for k in ("id", "status", "text", "postconditions", "invariants")
                           if k in by_id[t]} for t in reached],
            "omitted": [r["id"] for r in reqs if r["id"] not in chosen and r["id"] not in reached],
            "omitted_note": "Requirements listed in `omitted` exist in the spec; ask for any of them by id."}


def section(spec: dict) -> str:
    """What a write session's system prompt carries after the spec while `SPEC_VIEWS=on`."""

    return (f"\n{SECTION}\n\nThe requirements of this spec at version {spec.get('spec_version')} "
            f"(digest `{spec.get('spec_digest')}`). Each has an id, its ground (`source`), a decision status, "
            "its conditions and the checks that verify it. Treat `assumed` as provisional and do not build on "
            "an `undecided` requirement: ask the person. A `spec-update` may also carry `requirements`, the "
            "full list: each existing requirement keeps its `source`, a new one is recorded as a decision.\n\n"
            "```json\n" + json.dumps(agent(spec), ensure_ascii=False, indent=2) + "\n```\n")


def prompted(prompt: str) -> int | None:
    """The `spec_version` a system prompt was built from, or `None` when it carries no agent view."""

    at = prompt.find(SECTION)
    found = VERSION.search(prompt, at) if at >= 0 else None
    return int(found.group(1)) if found else None


def views(spec: dict) -> dict:
    return {"human": human(spec), "agent": agent(spec), "spec_version": spec.get("spec_version"),
            "spec_digest": spec.get("spec_digest")}


# -- Equivalence --------------------------------------------------------------

def _facts(r: dict) -> Counter:
    rid, found = r["id"], Counter()
    found[("id", rid)] += 1
    found[("status", rid, r["status"])] += 1
    found[("text", rid, r["text"])] += 1
    texts = [r["text"]]
    for kind in CONDITIONS:
        for c in r.get(kind, []):
            found[("condition", rid, kind, c)] += 1
            texts.append(c)
    for t in r.get("depends_on", []):
        found[("depends_on", rid, t)] += 1
    for v in r.get("verify", []):
        found[("verify", rid, v["item"], v["expected"])] += 1
        texts += [v["item"], v["expected"]]
    for text in texts:
        for word in NEGATION.findall(text):
            found[("negation", rid, word.lower())] += 1
        for unit in UNIT.findall(text):
            found[("unit", rid, re.sub(r"\s+", "", unit).lower())] += 1
    return found


def _parsed(human_md: str) -> list[dict]:
    """The requirements of a `human` rendering, read back from its Behaviour section."""

    body = human_md.split("\n## Behaviour\n", 1)[-1].split("\n## Constraints\n", 1)[0]
    reqs = []
    for line in body.split("\n"):
        if head := re.fullmatch(r"### (R\d+) — (\S+)", line):
            reqs.append({"id": head.group(1), "status": head.group(2), "text": ""})
        elif not reqs or not line.strip():
            continue
        elif bullet := re.fullmatch(r"- (\w[\w ]*?): (.*)", line):
            label, value, r = bullet.group(1), bullet.group(2), reqs[-1]
            if label in CONDITION:
                r.setdefault(CONDITION[label], []).append(value)
            elif label == "Depends on":
                r.setdefault("depends_on", []).append(value)
            elif label == "Verify" and " ⇒ " in value:
                item, expected = value.split(" ⇒ ", 1)
                r.setdefault("verify", []).append({"item": item, "expected": expected})
        elif not line.startswith("- "):
            reqs[-1]["text"] = (reqs[-1]["text"] + " " + line).strip()
    return reqs


def equivalent(human_md: str, agent_json) -> dict:
    """What one rendering carries and the other lacks: `{"missing_in_human": [...], "missing_in_agent": [...]}`,
    each a sorted list of facts. Both empty is equivalence."""

    data = json.loads(agent_json) if isinstance(agent_json, str) else agent_json
    seen = {"human": sum((_facts(r) for r in _parsed(human_md)), Counter()),
            "agent": sum((_facts(r) for r in data.get("requirements") or []), Counter())}
    return {"missing_in_human": sorted("·".join(f) for f in seen["agent"] - seen["human"]),
            "missing_in_agent": sorted("·".join(f) for f in seen["human"] - seen["agent"])}


# -- Verification results -----------------------------------------------------

def results_file(spec: dict) -> Path:
    from . import specs   # it imports this module
    return specs.SPECS / spec["repo"] / f"{spec['id']}.verify.json"


def verified(spec: dict, verdict: dict) -> None:
    """The server's gate path, and only it, records a verdict against each requirement with verify items,
    keyed by the spec version and digest it ran for. The spec record never holds results."""

    if not spec.get("requirements"):
        return
    file = results_file(spec)
    try:
        kept = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    for r in spec["requirements"]:
        if r.get("verify"):
            kept[r["id"]] = {"spec_version": spec.get("spec_version"), "spec_digest": spec.get("spec_digest"),
                             "ok": bool(verdict.get("ok")), "head": verdict.get("head"), "cmd": verdict.get("cmd"),
                             "items": [v["item"] for v in r["verify"]], "ts": time.time()}
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    swap(temporary, file)


def results(spec: dict) -> dict[str, str]:
    """Per requirement with verify items: `pass`, `fail`, `stale` (recorded for another version) or `missing`."""

    try:
        kept = json.loads(results_file(spec).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    out = {}
    for r in spec.get("requirements") or []:
        if not r.get("verify"):
            continue
        got = kept.get(r["id"])
        out[r["id"]] = "missing" if not got else "stale" if (got.get("spec_version"), got.get("spec_digest")) != (
            spec.get("spec_version"), spec.get("spec_digest")) else "pass" if got.get("ok") else "fail"
    return out


# -- The first work turn --------------------------------------------------------

def first_turn(path: Path, run) -> str:
    """With `SPEC_VIEWS=on`, before the first turn is sent: the question that holds it when a requirement
    depends on an undecided one, else empty — after the session is rebuilt if its prompt's
    `spec_version` is not the saved record's."""

    from . import specs, work   # both import this module
    spec = specs.owner(path)
    if not on() or spec is None or "requirements" not in spec:
        return ""
    if stuck := blocked(spec):
        asked = ", ".join(f"{r} 가 정하지 않은 {t} 에 기댄다" for r, t in stuck)
        specs.update(spec["repo"], spec["id"], fault=f"시작 전에 정할 것 — {asked}")
        return (f"시작하기 전에 정해 줄 것이 있다 — {asked}. {', '.join(dict.fromkeys(t for _, t in stuck))} 를 "
                "정해 이 작업트리에 지시로 적어 보내면 그것으로 시작한다.")
    if prompted(getattr(run.chat, "_spec_system", "")) != spec.get("spec_version"):
        cell = spec.get("cell") or {}
        run.chat = work.session(path, cell.get("model", ""), cell.get("effort", ""), cell.get("fast", False))
        run.session_id = run.chat.id
    return ""
