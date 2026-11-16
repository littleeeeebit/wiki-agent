"""The owner's brief: a short plain-Korean page written from the agent's spec, shown first at a review.

`docs/plans/oneshot/8-human-sheet.md` is the design. The agent's spec stays as it is; the brief lives in
`oneshot.brief`, keyed by review, and nothing but the owner's view reads it. R1's brief is written after
`design` and `redesign`, R2's after the screen design and `refront`, R3's after the critique loop (and on
Android the phone round) passes. A brief that cannot be written leaves
the review on its full sheet and never stops the run. A snapshot names its review in `gate`; R1's carries
none, so the briefs 8a stored keep their `source`.
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
# R2's sections; the screens' pictures sit with their confirmation lines and the palettes beside, from the spec.
R2_SECTIONS = ("한 줄", "화면", "기능이 놓인 자리")
# R3's: placement was fixed at R2 and the build stays within it, so nothing moved to tell; `휴대폰에서` is an
# Android app's, which alone has a phone round.
R3_SECTIONS = ("한 줄", "화면", "휴대폰에서")
SECTIONS = {"r1": R1_SECTIONS, "r2": R2_SECTIONS, "r3": R3_SECTIONS}
OPTIONAL = {"r1": R1_OPTIONAL, "r2": set(), "r3": {"휴대폰에서"}}
FIXED = 20   # the latest fixes since R2 the R3 snapshot keeps: "what changed" needs the gist, not every round
CITED = re.compile(r"\(([^()]*)\)")
ID = re.compile(r"\b[A-Z]{1,2}\d+\b")
CITATION = re.compile(r"\s*[A-Z]{1,2}\d+(?:\s*[,·]\s*[A-Z]{1,2}\d+)*\s*")
BANNED = {"코드 표기(`)": re.compile(r"`"), "테스트 이름": re.compile(r"\btest_\w+"),
          "엔드포인트 경로": re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/"),
          "색 코드": re.compile(r"#[0-9a-fA-F]{6}\b")}

oneshot.GROUPS["brief"] = "review"   # the run's review model writes it: it did not write the design
oneshot.ROLES["brief"] = lambda: oneshot.ROLES["review"]()   # an empty choice is the review's own default


def gate_of(snap: dict) -> str:
    return snap.get("gate", "r1")


def snapshot(spec: dict, gate: str = "r1") -> dict:
    """A review's snapshot: every fact its brief may state, and nothing that changes without changing the gate."""

    o = spec["oneshot"]
    profile = o.get("profile") or {}
    shown = {"kind": kind_of(o), "visibility": profile.get("visibility"), "auth": profile.get("auth")}
    at = o.get(gate) or {}
    if gate in ("r2", "r3"):
        front = o.get("front") or {}
        # `shots` numbers the pictures: a new design or an edit turn shoots them again under a new number.
        snap = {"gate": gate, "sentence": o.get("sentence") or "", "screens": front.get("screens") or [],
                "states": front.get("states") or {}, "palettes": (front.get("tokens") or {}).get("palettes") or [],
                "shots": front.get("shots"), "first_screen": o.get("first_screen") or "",
                "features": [{k: f[k] for k in ("id", "name", "why")} for f in o.get("features") or []],
                "frequency": o.get("frequency") or {}, "profile": shown,
                "round": at.get("round", 1), "marks": at.get("marks") or {}}
        return snap | (finished(o) if gate == "r3" else {})
    return {"sentence": o.get("sentence") or "", "restated": (o.get("intent") or {}).get("restated") or "",
            "requirements": spec.get("requirements") or [], "glossary": o.get("glossary") or [],
            "stack": o.get("stack") or [], "design": o.get("design") or {}, "profile": shown,
            "round": at.get("round", 1), "marks": at.get("marks") or {}}


def finished(o: dict) -> dict:
    """R3's own fields: the findings left, numbered `L<n>` in the order `r3_sheet` lists them, each on its
    screen; the looks a before and after compare; the fixes since R2; and on Android the phone round's
    device and measurements."""

    screens = (o.get("front") or {}).get("screens") or []
    looks = [x for x in o.get("looks") or [] if not any(f["severity"] == "high" for f in x["findings"])]
    phone = o.get("phone") or {}
    phone = phone if kind_of(o) == "android" and phone.get("round") else {}
    critiques = o.get("critiques") or []
    said = [("critic", f) for f in (critiques[-1:] or [{}])[0].get("findings", [])]
    said += [("harness", f) for f in (looks[-1]["findings"] if looks else [])]
    said += [("phone", f) for f in phone.get("findings") or []]
    left = []
    for n, (who, f) in enumerate(said, 1):
        sid = on_screen(f, screens)
        left.append({"key": f"L{n}", "screen": sid or next((s["id"] for s in screens if s.get("primary")), ""),
                     "named": bool(sid), "from": who,
                     **{k: f[k] for k in ("severity", "viewport", "state", "what", "fix") if f.get(k)}})
    # What was sent back since R2, in the order it was: a look's high findings go to the build's retry before
    # any critic sees that look, and an earlier critique's findings to the fix turn after it.
    sent = [(x["round"], 0, f) for x in o.get("looks") or [] for f in x["findings"] if f["severity"] == "high"]
    sent += [(c.get("look", 0), 1, f) for c in critiques[:-1] for f in c.get("findings") or []]
    fixed = [{"screen": on_screen(f, screens), "what": f["what"]} for _, _, f in sorted(sent, key=lambda s: s[:2])]
    return {"findings": left, "fixed": fixed[-FIXED:],
            "looks": {"before": looks[0]["round"], "after": looks[-1]["round"]} if looks else {},
            "phone": {k: phone.get(k) for k in ("model", "size", "timing", "memory_kb", "round")} if phone else None}


def on_screen(f: dict, screens: list[dict]) -> str:
    """The screen a finding belongs to, the first that matches: its `screen` field (the harness's and the
    phone's), its `route` as an id (the critic's on Android), its `route` as a web route; else empty."""

    ids, routes = {s["id"] for s in screens}, {s["route"]: s["id"] for s in screens if s.get("route")}
    return next((v for v in (f.get("screen"), f.get("route")) if v in ids), "") or routes.get(f.get("route"), "")


def source(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def lacking(snap: dict) -> str:
    """Empty when the snapshot holds what every required section reads, else what is missing."""

    needed = {"r1": ("sentence", "restated", "requirements", "design"), "r2": ("sentence", "screens", "features"),
              "r3": ("sentence", "screens", "looks")}[gate_of(snap)]
    missing = [k for k in needed if not snap[k]]
    return f"요약에 쓸 정보가 없다: {', '.join(missing)}" if missing else ""


def lines(snap: dict) -> dict[str, list[str]]:
    """A review's mandatory confirmation lines, by key, each with the items it covers. The server fixes them;
    the writer words them. R1: every `assumed` requirement, every assumption, and every item marked since the
    last round. R2: one per screen, covering the screen and its states. R3: one per finding left, covering
    its screen."""

    if gate_of(snap) == "r2":
        return {s["id"]: [s["id"], f"{s['id']}.states"] for s in snap["screens"]}
    if gate_of(snap) == "r3":
        return {f["key"]: [f["screen"]] for f in snap["findings"]}
    keys = [r["id"] for r in snap["requirements"] if r.get("status") == "assumed"]
    keys += [a["id"] for a in snap["design"].get("assumptions") or []]
    keys += [k for k in snap["marks"] if k not in keys]
    return {k: [k] for k in keys}


def about(snap: dict, key: str) -> str:
    if gate_of(snap) == "r3":
        f = next(f for f in snap["findings"] if f["key"] == key)
        where = f["screen"] if f["named"] else f"{f['screen']} (the finding named no screen: say so)"
        seen = ", ".join(f[k] for k in ("severity", "viewport", "state") if f.get(k))
        return (f"{key} on {where}, found by the {f['from']} ({seen}): {f['what']}"
                + (f"; the suggested fix: {f['fix']}" if f.get("fix") else ""))
    if gate_of(snap) == "r2":
        s = next(s for s in snap["screens"] if s["id"] == key)
        st = snap["states"].get(key) or {}
        marked = [f"{i}: {m}" for i in (key, f"{key}.states") if (m := snap["marks"].get(i))]
        return (f"{key}{' (the first screen)' if s.get('primary') else ''}: {s['purpose']}; states — "
                + "; ".join(f"{k}: {v}" for k, v in st.items())
                + (f" (changed since the last round: {', '.join(marked)})" if marked else ""))
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
    names, order = [s["name"] for s in sections], SECTIONS[gate_of(snap)]
    if names != [n for n in order if n in names] or len(set(names)) != len(names):
        raise ValueError(f"섹션은 이 순서로 한 번씩만 둔다: {', '.join(order)}")
    if missing := [n for n in order if n not in names and n not in OPTIONAL[gate_of(snap)]]:
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
    shown_screens = gate_of(snap) != "r1"   # R2 and R3 are about the screens
    known = ({s["id"] for s in snap["screens"]} | {p["id"] for p in snap["palettes"]}
             | {f["key"] for f in snap.get("findings") or []} if shown_screens
             else set(sheet.decidable(snap["design"], snap["requirements"])))
    cited = {i for _, t in texts for c in CITED.finditer(t) if CITATION.fullmatch(c.group(1)) for i in ID.findall(c.group(1))}
    if unknown := sorted(cited - known):
        raise ValueError(f"명세에 없는 번호를 댔다: {', '.join(unknown)}")
    paths = ({s["route"] for s in snap["screens"] if len(s.get("route") or "") > 1} if shown_screens
             else {p["path"] for p in snap["design"].get("api") or [] if len(p.get("path") or "") > 1})
    if named := sorted({p for _, t in texts for p in paths if re.search(rf"{re.escape(p)}(?![\w-])", t)}):
        what = "화면 경로" if shown_screens else "엔드포인트 경로"   # with or without a method
        raise ValueError(f"사람이 읽는 요약에 쓰지 않는 표기가 있다: {what} {', '.join(named)}")
    (screens_checked if shown_screens else design_checked)(snap, sections, texts)
    if banned := sorted({f"{n} {what}" for n, t in texts for what, p in BANNED.items() if p.search(t)}):
        raise ValueError(f"사람이 읽는 요약에 쓰지 않는 표기가 있다: {'; '.join(banned)}")
    warnings = [f"{n} 번역체: " + "; ".join(f"{p} `{s}`" for p, s in hits) for n, t in texts if (hits := sheet.translationese(t))]
    if warnings and attempt == 0:
        raise ValueError("번역체가 있다 — " + " / ".join(warnings))
    return {"sections": sections, "confirm": confirm, "warnings": warnings}


def screens_checked(snap: dict, sections: list[dict], texts: list[tuple[str, str]]) -> None:
    """R2: every feature on a screen named by its name where it sits. R3: `휴대폰에서` exactly when the app
    had a phone round. Both: no feature id in the owner's words."""

    names = {s["name"] for s in sections}
    placed = next((s["text"] for s in sections if s["name"] == "기능이 놓인 자리"), "")
    if gate_of(snap) == "r2" and (unnamed := [f["name"] for f in snap["features"]
                                              if snap["frequency"].get(f["id"]) != "never" and f["name"] not in placed]):
        raise ValueError(f"`기능이 놓인 자리` 에 빠진 기능이 있다 — 이름 그대로 써라: {', '.join(unnamed)}")
    if gate_of(snap) == "r3" and snap["phone"] and "휴대폰에서" not in names:
        raise ValueError("휴대폰 회차가 있었다 — `휴대폰에서` 섹션이 빠졌다")
    if gate_of(snap) == "r3" and not snap["phone"] and "휴대폰에서" in names:
        raise ValueError("휴대폰 회차가 없었다 — `휴대폰에서` 섹션은 두지 않는다")
    if coded := sorted({f["id"] for f in snap["features"] for _, t in texts if re.search(rf"(?<![\w-]){re.escape(f['id'])}(?![\w-])", t)}):
        raise ValueError(f"기능은 이름으로 쓴다, 코드 이름이 아니다: {', '.join(coded)}")


def design_checked(snap: dict, sections: list[dict], texts: list[tuple[str, str]]) -> None:
    """R1: every left-out requirement named, and on Android every permission and outside call."""

    left_out = [r["id"] for r in snap["requirements"] if r.get("status") == "not_applicable"]
    out_text = next((s["text"] for s in sections if s["name"] == "빼기로 한 것"), "")
    if unnamed := [i for i in left_out if not re.search(rf"\b{i}\b", out_text)]:
        raise ValueError(f"`빼기로 한 것` 에 빠진 요구가 있다: {', '.join(unnamed)}")
    if "modules" in snap["design"]:   # an Android app: every permission and every call leaving the phone is named
        risky = [r["id"] for r in snap["requirements"] if r.get("capability")]
        risky += [x["id"] for x in snap["design"].get("outside") or []]
        note = next((s["text"] for s in sections if s["name"] == "알아 두실 것"), "")
        if unnamed := [i for i in risky if not re.search(rf"\b{i}\b", note)]:
            raise ValueError(f"`알아 두실 것` 에 빠진 권한이나 폰 밖으로 나가는 호출이 있다: {', '.join(unnamed)}")


def shown(spec: dict, gate: str = "r1") -> dict:
    """A review's brief as the owner's view gives it: always the gate's current `source`; the brief when one
    was written from that `source` and still passes today's checks (a brief stored by an earlier version may
    not), else `failed` with why the review shows its full sheet (none for a run from before the brief)."""

    snap = snapshot(spec, gate)
    src = source(snap)
    entry = (spec["oneshot"].get("brief") or {}).get(gate) or {}
    if entry.get("source") == src and not entry.get("failed"):
        mandatory = lines(snap)
        try:
            ok = checked(entry, snap, mandatory, 1)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return {"source": src, "failed": f"저장된 요약이 지금 검사를 통과하지 못했다 — {exc}"}
        return {"source": src, "sections": ok["sections"], "confirm": ok["confirm"], "lines": mandatory,
                "warnings": ok["warnings"]}
    if entry.get("failed") and entry.get("source") in (src, None):
        return {"source": src, "failed": entry["failed"]}
    return {"source": src, **({"failed": "요약을 지금 설계로 다시 쓰지 못했다"} if entry else {})}


def valid(d) -> bool:
    return isinstance(d, dict) and (d.get("approve") is True and not d.get("edit")
                                    or isinstance(d.get("edit"), str) and bool(d["edit"].strip()))


def reduced(mandatory: dict[str, list[str]], given: dict, sheet_given: dict, rest: bool, items: list[str]) -> dict:
    """A brief submission's item decisions, or `ValueError`. Every line is checked first: each mandatory line
    decided, validly, and no other. Then each item a line covers takes its decision; an item covered more than
    once takes every edit, joined, each prefixed with where it came from, and an edit wins over an approval.
    `rest` approves every item still undecided. A decision from the folded sheet joins as its own line;
    one that is not valid passes through for `decided` to refuse as it does today."""

    if unknown := sorted(set(given) - set(mandatory)):
        raise ValueError(f"요약에 없는 확인 줄이다: {', '.join(unknown)}")
    if missing := [k for k in mandatory if k not in given]:
        raise ValueError(f"아직 정하지 않은 확인 줄이 있다: {', '.join(missing)}")
    if bad := [k for k in mandatory if not valid(given[k])]:
        raise ValueError(f"{', '.join(bad)}: 확인 줄을 승인하거나 고칠 내용을 적어라")
    said = [(f"확인 줄 {k}", given[k], i) for k, covered in mandatory.items() for i in covered]
    said += [("전체 시트", d, i) for i, d in sheet_given.items() if valid(d)]
    edits: dict[str, list[tuple[str, str]]] = {}
    out = {i: {"approve": True} for i in items} if rest else {}
    for where, d, i in said:
        if d.get("edit"):
            edits.setdefault(i, []).append((where, d["edit"].strip()))
        else:
            out[i] = {"approve": True}
    out |= {i: {"edit": e[0][1] if len(e) == 1 else "\n\n".join(f"[{w}] {t}" for w, t in e)} for i, e in edits.items()}
    return out | {i: d for i, d in sheet_given.items() if not valid(d)}


def write(store: oneshot.Store, spec_of, save, gate: str = "r1") -> None:
    """A review's brief, written once after the turn that ends at it under `working: brief`, and the review's
    `working` cleared. Nothing here stops the run: a refused or failed turn, or any failure reading or saving,
    leaves `failed` with why, and the review falls back to its full sheet. A result for a gate that has since
    changed is dropped."""

    try:
        written(store, spec_of, save, gate)
    except Exception as exc:   # the brief is the owner's convenience: no failure in it may stop the run
        with oneshot._lock:   # a save that fails here too reaches the worker's guard, which stops with why
            o = spec_of()["oneshot"]
            if o.get("working") != "brief":
                return
            failed = {"source": None, "failed": f"예상하지 못한 실패 — {type(exc).__name__}: {exc}"}
            fields = {"brief": {**(o.get("brief") or {}), gate: failed}, "working": None}
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


def written(store: oneshot.Store, spec_of, save, gate: str) -> None:
    snap = snapshot(spec_of(), gate)
    src, mandatory = source(snap), lines(snap)
    entry: dict = {"source": src, "lines": mandatory, "written": time.time()}
    entry.update({"failed": why} if (why := lacking(snap)) else asked(store, snap, mandatory))
    with oneshot._lock:
        o = spec_of()["oneshot"]
        if source(snapshot(spec_of(), gate)) != src or o.get("stage") != gate:   # written for a gate that is gone
            if o.get("working") == "brief":
                save(working=None)
            return
        save(brief={**(o.get("brief") or {}), gate: entry}, working=None)


def asked(store: oneshot.Store, snap: dict, mandatory: dict[str, list[str]]) -> dict:
    """The writer's turn, asked again once with the reason; `{"failed": why}` when both are refused or a
    turn breaks. Charged like any turn, and recorded in flight so a restart charges a cut one."""

    text = (f"The review: {gate_of(snap).upper()}.\n\nThe snapshot:\n\n{json.dumps(snap, ensure_ascii=False)}\n\n"
            f"The confirmation lines, by key "
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
    gate = o.get("stage") if o.get("stage") in SECTIONS else "r1"   # the review it was written for
    cut_one = {**((o.get("brief") or {}).get(gate) or {}), "failed": "서버가 다시 켜져 요약을 쓰지 못했다"}
    return {"brief": {**(o.get("brief") or {}), gate: cut_one}}
