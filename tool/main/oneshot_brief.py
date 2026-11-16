"""The owner's brief: a short plain-Korean page written from the agent's spec, shown first at a review.

`docs/plans/oneshot/8-human-sheet.md` is the design. The agent's spec stays as it is; the brief lives in
`oneshot.brief`, keyed by review, and nothing but the owner's view reads it. Step 8a writes R1's brief after
`design` and `redesign`. A brief that cannot be written leaves the review on its full sheet and never stops
the run.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from . import oneshot, oneshot_sheet as sheet
from .oneshot_profile import charged, cut, kind_of

PROMPT = oneshot.ROOT / "tool/prompts/oneshot-brief.md"
MAX_BRIEF = 4_000      # characters, the whole brief
MAX_SECTION = 1_500
MAX_LINE = 300
# R1's sections in their order; the confirmation lines (`확인해 주세요`) are their own field, shown after `돌아가는 순서`.
R1_SECTIONS = ("한 줄", "쓰는 장면", "물어보신 것", "돌아가는 순서", "빼기로 한 것", "알아 두실 것", "다 됐다고 보는 기준")
R1_OPTIONAL = {"물어보신 것", "돌아가는 순서", "빼기로 한 것"}   # a sentence may ask nothing; an app may have no pipeline
CITED = re.compile(r"\(([^()]*)\)")
ID = re.compile(r"\b[A-Z]{1,2}\d+\b")
CITATION = re.compile(r"\s*[A-Z]{1,2}\d+(?:\s*[,·]\s*[A-Z]{1,2}\d+)*\s*")
BANNED = {"코드 표기(`)": re.compile(r"`"), "테스트 이름": re.compile(r"\btest_\w+"),
          "엔드포인트 경로": re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/")}

oneshot.GROUPS["brief"] = "review"   # the run's review model writes it: it did not write the design
oneshot.ROLES["brief"] = lambda: oneshot.ROLES["review"]()   # an empty choice is the review's own default


def snapshot(spec: dict) -> dict:
    """R1's snapshot: every fact its brief may state, and nothing that changes without changing the gate."""

    o = spec["oneshot"]
    profile = o.get("profile") or {}
    return {"sentence": o.get("sentence") or "", "restated": (o.get("intent") or {}).get("restated") or "",
            "requirements": spec.get("requirements") or [], "glossary": o.get("glossary") or [],
            "stack": o.get("stack") or [], "design": o.get("design") or {},
            "profile": {"kind": kind_of(o), "visibility": profile.get("visibility"), "auth": profile.get("auth")},
            "round": (o.get("r1") or {}).get("round", 1), "marks": (o.get("r1") or {}).get("marks") or {}}


def source(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def lacking(snap: dict) -> str:
    """Empty when the snapshot holds what every required section reads, else what is missing."""

    missing = [k for k in ("sentence", "restated", "requirements", "design") if not snap[k]]
    return f"요약에 쓸 정보가 없다: {', '.join(missing)}" if missing else ""


def lines(snap: dict) -> dict[str, list[str]]:
    """R1's mandatory confirmation lines, by key, each with the items it covers: every `assumed` requirement,
    every assumption, and every item marked since the last round. The server fixes them; the writer words them."""

    keys = [r["id"] for r in snap["requirements"] if r.get("status") == "assumed"]
    keys += [a["id"] for a in snap["design"].get("assumptions") or []]
    keys += [k for k in snap["marks"] if k not in keys]
    return {k: [k] for k in keys}


def about(snap: dict, key: str) -> str:
    reqs = {r["id"]: r["text"] for r in snap["requirements"]}
    assumed = {a["id"]: a["what"] for a in snap["design"].get("assumptions") or []}
    said = reqs.get(key) or assumed.get(key) or ""
    mark = snap["marks"].get(key)
    return f"{key}: {said}" + (f" (changed since the last round: {mark})" if mark else "")


def checked(value, snap: dict, mandatory: dict[str, list[str]], attempt: int) -> dict:
    """An `oneshot-brief` block as R1 stores it, or `ValueError` with why it was refused. Translationese refuses
    the first answer and is kept as a warning on the second, as on every gate sheet."""

    if not isinstance(value, dict) or not isinstance(value.get("sections"), list) or not isinstance(value.get("confirm"), list):
        raise ValueError("`oneshot-brief` 는 `sections` 와 `confirm` 목록을 가진 객체다")
    sections = []
    for s in value["sections"]:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not isinstance(s.get("text"), str) or not s["text"].strip():
            raise ValueError("각 `sections` 항목은 `name` 과 비지 않은 `text` 를 가진다")
        sections.append({"name": s["name"], "text": s["text"].strip()})
    names = [s["name"] for s in sections]
    if names != [n for n in R1_SECTIONS if n in names] or len(set(names)) != len(names):
        raise ValueError(f"섹션은 이 순서로 한 번씩만 둔다: {', '.join(R1_SECTIONS)}")
    if missing := [n for n in R1_SECTIONS if n not in names and n not in R1_OPTIONAL]:
        raise ValueError(f"빠진 섹션이 있다: {', '.join(missing)}")
    confirm = []
    for c in value["confirm"]:
        if not isinstance(c, dict) or not isinstance(c.get("key"), str) or not isinstance(c.get("text"), str) or not c["text"].strip():
            raise ValueError("각 `confirm` 줄은 `key` 와 비지 않은 `text` 를 가진다")
        confirm.append({"key": c["key"], "text": c["text"].strip()})
    keys = [c["key"] for c in confirm]
    if dup := sorted({k for k in keys if keys.count(k) > 1}):
        raise ValueError(f"확인 줄이 두 번 나온다: {', '.join(dup)}")
    if gone := [k for k in mandatory if k not in keys]:
        raise ValueError(f"확인받아야 할 줄이 빠졌다: {', '.join(gone)}")
    if extra := [k for k in keys if k not in mandatory]:
        raise ValueError(f"확인 줄로 정하지 않은 키다: {', '.join(extra)}")
    texts = [*((s["name"], s["text"]) for s in sections), *((c["key"], c["text"]) for c in confirm)]
    if long := [n for n, t in texts if len(t) > (MAX_LINE if n in mandatory else MAX_SECTION)]:
        raise ValueError(f"너무 길다: {', '.join(long)}")
    if (total := sum(len(t) for _, t in texts)) > MAX_BRIEF:
        raise ValueError(f"요약이 {total}자다 — {MAX_BRIEF}자 안으로 줄여라")
    # A citation is parentheses holding ids and nothing else, `(R3, A2)`: `(갤럭시 S21)` and `(A4 용지)` are words.
    known = set(sheet.decidable(snap["design"], snap["requirements"]))
    cited = {i for _, t in texts for c in CITED.finditer(t) if CITATION.fullmatch(c.group(1)) for i in ID.findall(c.group(1))}
    if unknown := sorted(cited - known):
        raise ValueError(f"명세에 없는 번호를 댔다: {', '.join(unknown)}")
    paths = {p["path"] for p in snap["design"].get("api") or [] if len(p.get("path") or "") > 1}
    if named := sorted({p for _, t in texts for p in paths if p in t}):   # a path without its method is still one
        raise ValueError(f"사람이 읽는 요약에 쓰지 않는 표기가 있다: 엔드포인트 경로 {', '.join(named)}")
    left_out = [r["id"] for r in snap["requirements"] if r.get("status") == "not_applicable"]
    out_text = next((s["text"] for s in sections if s["name"] == "빼기로 한 것"), "")
    if unnamed := [i for i in left_out if not re.search(rf"\b{i}\b", out_text)]:
        raise ValueError(f"`빼기로 한 것` 에 빠진 요구가 있다: {', '.join(unnamed)}")
    if banned := sorted({f"{n} {what}" for n, t in texts for what, p in BANNED.items() if p.search(t)}):
        raise ValueError(f"사람이 읽는 요약에 쓰지 않는 표기가 있다: {'; '.join(banned)}")
    warnings = [f"{n} 번역체: " + "; ".join(f"{p} `{s}`" for p, s in hits) for n, t in texts if (hits := sheet.translationese(t))]
    if warnings and attempt == 0:
        raise ValueError("번역체가 있다 — " + " / ".join(warnings))
    return {"sections": sections, "confirm": confirm, "warnings": warnings}


def write(store: oneshot.Store, spec_of, save) -> None:
    """R1's brief, written once after a design or redesign turn under `working: brief`, and the review's
    `working` cleared. Nothing here stops the run: a refused or failed turn, or any failure reading or saving,
    leaves `failed` with why, and the review falls back to its full sheet. A result for a gate that has since
    changed is dropped."""

    try:
        written(store, spec_of, save)
    except Exception as exc:   # the brief is the owner's convenience: no failure in it may stop the run
        with oneshot._lock:   # a save that fails here too reaches the worker's guard, which stops with why
            o = spec_of()["oneshot"]
            if o.get("working") != "brief":
                return
            failed = {"source": None, "failed": f"예상하지 못한 실패 — {type(exc).__name__}: {exc}"}
            fields = {"brief": {**(o.get("brief") or {}), "r1": failed}, "working": None}
            # The run goes idle here, so a turn whose charge was not saved is settled in this same save, once.
            # Should this save fail too, `orphaned` settles the flight it leaves on resume or at start-up.
            if isinstance(exc, Unsettled):
                fields.update(inflight=None, spend=exc.spend)
            else:
                fields.update(orphaned({**o, "working": None}) or {})
            save(**fields)


def orphaned(o: dict) -> dict | None:
    """A brief turn still recorded in flight with no brief being written: its charge, as a cut turn
    (cost unreported), with `inflight` cleared in the same save. `None` when there is none."""

    flight = o.get("inflight") or {}
    if flight.get("role") != "brief" or o.get("working") == "brief":
        return None
    sent = float(flight.get("sent") or time.time())
    elapsed, lived = max(0.0, time.time() - sent), max(0.0, float(flight.get("seen") or sent) - sent)
    return {"inflight": None, "spend": cut(o.get("spend"), "brief", flight.get("codex", False), elapsed, lived)}


class Unsettled(RuntimeError):
    """A finished turn whose charge could not be saved; `spend` is the ledger with it charged."""

    def __init__(self, cause: Exception, spend: dict) -> None:
        super().__init__(f"턴의 비용을 저장하지 못했다 — {cause}")
        self.spend = spend


def settled(store: oneshot.Store, spend: dict) -> None:
    try:
        store.put(inflight=None, spend=spend)
    except Exception as exc:
        raise Unsettled(exc, spend) from exc


def written(store: oneshot.Store, spec_of, save) -> None:
    snap = snapshot(spec_of())
    src, mandatory = source(snap), lines(snap)
    entry: dict = {"source": src, "lines": mandatory, "written": time.time()}
    entry.update({"failed": why} if (why := lacking(snap)) else asked(store, snap, mandatory))
    with oneshot._lock:
        o = spec_of()["oneshot"]
        if source(snapshot(spec_of())) != src or o.get("stage") != "r1":   # written for a gate that is gone
            if o.get("working") == "brief":
                save(working=None)
            return
        save(brief={**(o.get("brief") or {}), "r1": entry}, working=None)


def asked(store: oneshot.Store, snap: dict, mandatory: dict[str, list[str]]) -> dict:
    """The writer's turn, asked again once with the reason; `{"failed": why}` when both are refused or a
    turn breaks. Charged like any turn, and recorded in flight so a restart charges a cut one."""

    text = (f"The snapshot:\n\n{json.dumps(snap, ensure_ascii=False)}\n\nThe confirmation lines, by key "
            f"(one `confirm` entry each, no other):\n\n" + "\n".join(f"- {about(snap, k)}" for k in mandatory) + "\n")
    system, reason = PROMPT.read_text(encoding="utf-8"), ""
    for attempt in range(2):
        if oneshot.runtime.stopping.is_set():
            return {"failed": "서버가 꺼지는 중이라 요약을 쓰지 못했다"}
        codex = oneshot.codex_role("brief", store)
        with oneshot._lock:
            store.put(inflight={"role": "brief", "codex": codex, "sent": time.time()})
        started = time.monotonic()
        try:
            # No tools: every fact comes from the snapshot, never from the folder or the repository.
            final, meta, codex = oneshot.ask(store, "brief", system, text if not reason else
                                             f"{text}\n\nYour last answer was refused: {reason}. Return it again.",
                                             None, (), "")
        except Exception as exc:
            with oneshot._lock:
                cost = (getattr(exc, "meta", None) or {}).get("cost_usd")
                settled(store, charged(store.get().get("spend"), "brief", codex, cost, time.monotonic() - started))
            return {"failed": f"요약 턴이 실패했다 — {exc}"}
        with oneshot._lock:
            settled(store, charged(store.get().get("spend"), "brief", codex, meta.get("cost_usd"),
                                   time.monotonic() - started))
        try:
            return checked(oneshot.block(final, "oneshot-brief"), snap, mandatory, attempt)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            reason = str(exc)
    return {"failed": f"요약이 두 번 거부됐다 — {reason}"}


def recovered(o: dict) -> dict | None:
    """At start-up, the `oneshot` fields that repair a brief: one the server went down writing is recorded
    as failed, so the review shows its sheet, and `oneshot.restarted` then charges the cut turn and stops the
    stage with `restart`; a brief turn left in flight on a run no longer writing it is settled (`orphaned`)."""

    if o.get("working") != "brief":
        return orphaned(o)
    r1 = {**((o.get("brief") or {}).get("r1") or {}), "failed": "서버가 다시 켜져 요약을 쓰지 못했다"}
    return {"brief": {**(o.get("brief") or {}), "r1": r1}}
