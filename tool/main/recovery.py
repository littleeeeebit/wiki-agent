"""recovery — research before another nonconverging fix
(`docs/plans/reliability/8-recovery.md`).

A refused review that repeats a serious finding the last fix said it fixed
(A-A), brings one back after a round without it (A-B-A), or refuses at round
`BACKSTOP` or later, requires research before the next correction turn. The
barrier is `loop.corrected`: it asks `prepared` before any correction goes
out, and nothing sends an ordinary fix while an issue's research is owed.

The research is one bounded read-only turn with web tools, on the work cell's
model, within limits the person gives for that issue (positive and finite;
none are assumed). Its page is written and committed in the task's worktree
before the fix, as a draft; the fix records the observed result on it, so the
review reads both in one pull request. Two cycles per issue, then the loop
stops with the evidence and the options.

State lives in the spec's additive `recovery` field, one entry per issue id,
read and written only through `specs` under `specs._files`. A restart keeps
the counts and the spend: a cycle counts when it is claimed, and a turn
interrupted mid-flight leaves its spend unknown, never zero.
"""

from __future__ import annotations

import datetime
import math
import re
import threading
import time
from pathlib import Path

from agent import ChatSession
from common.budget import Budget, Cancelled, Exhausted

from . import planning, specs
from .query import ROOT

PROMPT = (ROOT / "tool/prompts/recovery-research.md").read_text(encoding="utf-8")
TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"   # read-only: the research edits nothing
SERIOUS = ("P0", "P1")
BACKSTOP = 7     # a refusal at this round or later researches before fixing again
CYCLES = 2       # research-and-fix cycles per issue, then a person decides
UNNAMED = "unidentified"   # serious findings with no server id share one backstop issue
PENDING = "pending — the fix records what it observed here, with the tests and the commit."
LISTS = ("failed_fixes", "counterevidence", "alternatives")
TEXTS = ("problem", "hypothesis", "theory", "applicability", "experiment", "limits")
WHY = "반복 발견 재조사"   # `loop.Why.RECOVERY`


# -- The trigger -------------------------------------------------------------------------

def serious(record: dict) -> list[dict]:
    return [f for f in record.get("items") or [] if f["grade"] in SERIOUS]


def _fixed(record: dict, fid: str) -> bool:
    """The round raised `fid` and its correction said it was fixed. A finding
    disagreed with or not reproduced, with evidence, was not fixed."""

    return any(f.get("id") == fid and f.get("disposition") == "fixed" for f in serious(record))


def trigger(rounds: list[dict], state: dict) -> list[dict]:
    """Every issue the last counted round requires research for, in the
    round's order. `rounds` are the counted rounds; the last is the refusal
    about to be corrected. Stale rounds never reach here, P2 is never
    serious, and a finding without a validated id never matches another."""

    last = rounds[-1]
    n = last["n"]
    ruled = {k for k, s in state.items() if s.get("phase") == "ruled"}
    out: dict[str, dict] = {}
    for f in serious(last):
        fid = f.get("id")
        if not fid or fid in ruled or fid in out:
            continue
        if len(rounds) >= 2 and _fixed(rounds[-2], fid):
            out[fid] = {"issue": fid, "kind": "repeat", "rounds": [rounds[-2]["n"], n],
                        "heads": [rounds[-2]["head"], last["head"]]}
        elif (len(rounds) >= 3 and _fixed(rounds[-3], fid)
                and not any(g.get("id") == fid for g in rounds[-2].get("items") or [])
                and any(g.get("id") != fid for g in serious(rounds[-2]))):
            out[fid] = {"issue": fid, "kind": "oscillation", "rounds": [r["n"] for r in rounds[-3:]],
                        "heads": [r["head"] for r in rounds[-3:]]}
    if n >= BACKSTOP:
        for f in serious(last):
            key = f.get("id") or UNNAMED
            done = any(a.get("status") == "fixed" for a in (state.get(key) or {}).get("attempts", []))
            if key not in ruled and key not in out and not done:
                out[key] = {"issue": key, "kind": "backstop", "rounds": [n], "heads": [last["head"]]}
    return list(out.values())


def observed(spec: dict, last: dict) -> dict | None:
    """The recovery state with each researched fix's outcome closed by `last`,
    the first counted round after it; `None` when nothing changed."""

    raised = {f.get("id") or UNNAMED for f in serious(last)}
    state, changed = {}, False
    for key, s in (spec.get("recovery") or {}).items():
        attempts = []
        for a in s.get("attempts", []):
            if a.get("status") == "fixed" and a.get("outcome") is None and a["round"] < last["n"]:
                a = {**a, "outcome": {"round": last["n"], "head": last["head"],
                                      "result": "repeated" if key in raised else "cleared"}}
                changed = True
            attempts.append(a)
        state[key] = {**s, "attempts": attempts}
    return state if changed else None


def fresh(key: str) -> dict:
    return {"issue_id": key, "phase": "required", "trigger": None, "cycles_started": 0, "active": None,
            "limits": None, "spent": {"seconds": 0.0, "calls": 0, "tokens": 0, "unknown": False},
            "attempts": []}


def limited(limits) -> dict:
    """Positive finite research limits, or `ValueError`. None are assumed."""

    if not isinstance(limits, dict):
        raise ValueError("리서치 한도(초·호출·토큰)를 정해야 시작한다")
    out = {}
    for key, kind in (("seconds", float), ("calls", int), ("tokens", int)):
        value = limits.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"리서치 한도 `{key}` 는 0 보다 큰 유한한 수여야 한다")
        if kind is int and not float(value).is_integer():
            raise ValueError(f"리서치 한도 `{key}` 는 양의 정수여야 한다")
        out[key] = kind(value)
    return out


# -- The barrier ---------------------------------------------------------------------------

def _save(repo: str, sid: str, issues: dict) -> None:
    with specs._files:
        if (spec := specs.load(repo, sid)) is not None:
            specs.update(repo, sid, recovery={**(spec.get("recovery") or {}), **issues})


def halt(lp, issues: dict, phase: str, detail: str) -> None:
    """Stop the loop for `issues` (id -> state), each left in `phase`."""

    from . import loop  # `loop` imports this module lazily

    for s in issues.values():
        s["phase"] = phase
    _save(lp.repo, lp.sid, issues)
    loop.stop(lp, lp.repo, lp.sid, loop.Why.RECOVERY, f"{', '.join(issues)} · {detail}")


UNKNOWN = "호스트가 사용량을 알려 주지 않았다 — 0 으로 치지 않는다. 지금 쓴 양을 하한으로 받아들이면 [계속] 한다"


def spendable(lp, key: str, s: dict) -> bool:
    """The issue's spend so far is known, or accepted as a lower bound, and
    within its limits; otherwise stopped. Checked before a turn goes out and
    again before the fix it would lead to."""

    if s["spent"].get("unknown") and not s["spent"].get("accepted"):
        halt(lp, {key: s}, "unknown", UNKNOWN)
        return False
    # Usage and the time a closing session took are known only after the turn: either may pass its limit.
    over = [f"{k} {s['spent'][k]}/{s['limits'][k]}" for k in ("seconds", "tokens") if s["spent"][k] > s["limits"][k]]
    if over:
        halt(lp, {key: s}, "budget", f"리서치가 한도를 넘었다 ({', '.join(over)}) — 한도를 늘려 [계속] 한다")
        return False
    return True


def prepared(lp, spec: dict, path: Path) -> list[dict] | None:
    """Before the correction of the last counted round: the states of every
    issue it owes research for, each with its committed page — `[]` when it
    owes none — or `None` after stopping with the reason. No ordinary fix
    goes out past this while any of them is owed."""

    from . import loop

    rounds = loop.counted(spec)
    record = rounds[-1]
    state = {k: dict(v) for k, v in (spec.get("recovery") or {}).items()}

    def claimed(s: dict) -> bool:
        a = s.get("active") or {}
        return a.get("round") == record["n"] and a.get("head") == record["head"]

    owed = {t["issue"]: t for t in trigger(rounds, state)}
    keys = list(dict.fromkeys([*owed, *(k for k, s in state.items() if claimed(s))]))
    new = [k for k in keys if not claimed(state.get(k) or {})]
    for k in new:
        s = state[k] = state.get(k) or fresh(k)
        s["trigger"] = {**owed[k], "evidence": [f"[{f['grade']}] {f['head']}" for f in serious(record)
                                                if (f.get("id") or UNNAMED) == k]}
    spent = {k: state[k] for k in new if state[k]["cycles_started"] >= CYCLES}
    if spent:
        halt(lp, spent, "exhausted", f"리서치 {CYCLES}번 뒤에도 같은 발견으로 거절됐다 (라운드 {record['n']}). "
             "목표·제약을 고치거나, 페이지의 다른 방안을 고르거나, 이 발견을 미루는 것을 적어 [계속] 한다")
        return None
    missing = {}
    for k in new:
        try:
            state[k]["limits"] = limited(state[k]["limits"])
        except ValueError as exc:
            missing[k], why = state[k], exc
    if missing:
        kinds = ", ".join(f"{k} {state[k]['trigger']['kind']}" for k in missing)
        halt(lp, missing, "limits", f"{kinds} 발동 (라운드 {record['n']}) — {why}")
        return None
    for k in new:
        s = state[k]
        if s.get("active"):   # an earlier round's cycle whose fix never went out still counts
            s["attempts"] = [*s.get("attempts", []), {**s["active"], "status": "abandoned", "outcome": None}]
        # The claim: the cycle counts from here, across restarts.
        s["cycles_started"] += 1
        s["active"] = {"cycle": s["cycles_started"], "round": record["n"], "head": record["head"],
                       "status": "researching", "inflight": False, "started": time.time()}
        s["phase"] = "researching"
    if new:
        _save(lp.repo, lp.sid, {k: state[k] for k in new})
    for k in keys:
        s = state[k]
        if s["active"]["status"] == "researching":
            if s["active"].get("inflight"):
                # A restart cut the turn after it was sent: its spend is not known, and never zero.
                s["spent"] = {**s["spent"], "unknown": True, "accepted": False}
                s["active"]["inflight"] = False
            if not spendable(lp, k, s) or not research(lp, spec, path, k, s):
                return None
        if not spendable(lp, k, s):
            return None
    return [state[k] for k in keys]


# -- The research turn ---------------------------------------------------------------------

def page_of(spec: dict, key: str, cycle: int) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-") or "issue"
    if spec.get("planning"):
        return f"{spec['planning']['artifact_root']}/research-{slug}-{cycle}.md"
    return f"docs/research/review-{spec['id'].lower()}-{slug}-{cycle}.md"


def asked(spec: dict, key: str, s: dict) -> str:
    from . import loop

    rounds = loop.counted(spec)
    t = s["trigger"]
    lines = [f"Review research for issue `{key}` ({t['kind']}, rounds {t['rounds']}), "
             f"cycle {s['active']['cycle']} of {CYCLES}.", "", "## The finding as the reviews raised it", ""]
    for r in rounds:
        for f in r.get("items") or []:
            if (f.get("id") or UNNAMED) == key and f["grade"] in SERIOUS:
                lines += [f"Round {r['n']} at `{r['head'][:7]}`: {f['head']}", f.get("body") or "",
                          f"Correction then: {f.get('disposition') or 'none yet'}", ""]
    earlier = [a for a in s.get("attempts", []) if a.get("page")]
    if earlier:
        lines += ["## Earlier research on this issue", ""]
        lines += [f"- cycle {a['cycle']}: `{a['page']}` — hypothesis: {a.get('hypothesis', '')}; "
                  f"outcome: {(a.get('outcome') or {}).get('result', 'unknown')}" for a in earlier]
        lines.append("")
    lines += ["Find why the fixes did not hold before anyone edits again, then end with the "
              "`recovery-research` block."]
    return "\n".join(lines)


def read(final: str, path: Path) -> dict:
    """The research block, checked, or `ValueError`."""

    from . import loop

    got = loop.block("recovery-research", final)
    if not isinstance(got, dict):
        raise ValueError("`recovery-research` 블록이 없거나 JSON 객체가 아니다")
    out = {k: planning.words(got.get(k), k) for k in TEXTS}
    for k in LISTS:
        value = got.get(k)
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise ValueError(f"`{k}` 는 글줄 목록이어야 한다")
        out[k] = [v.strip() for v in value]
    out["sources"] = planning.sourced(got.get("sources"), path)
    known = {x["id"] for x in out["sources"]}
    claims = got.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("`claims` 가 비었다")
    out["claims"] = []
    for c in claims:
        if not isinstance(c, dict) or c.get("kind") not in ("source", "interpretation"):
            raise ValueError("주장마다 `kind` 가 source 나 interpretation 인 객체여야 한다")
        cited = planning.ids(c.get("sources") or [], "claims.sources")
        if not cited or set(cited) - known:
            raise ValueError(f"주장 `{c.get('id')}` 이 없는 출처를 가리키거나 출처가 없다")
        out["claims"].append({"id": planning.words(c.get("id"), "claims.id"),
                              "text": planning.words(c.get("text"), "claims.text"), "sources": cited,
                              "kind": c["kind"]})
    return out


def rendered(spec: dict, key: str, s: dict, found: dict) -> str:
    t, a = s["trigger"], s["active"]
    pr = (spec.get("pr") or {}).get("number")
    out = [f"# Review research — {key}, cycle {a['cycle']}", "",
           f"Draft research before the fix of review round {a['round']}"
           + (f" of PR #{pr}" if pr else "") + f", written {datetime.date.today().isoformat()}. "
           "It is not a verified fact or a shared rule: the review reads it with the fix.", "",
           "## Problem", "", found["problem"], "",
           f"Trigger: {t['kind']}, rounds {', '.join(map(str, t['rounds']))}.", ""]
    out += [f"- {e}" for e in t.get("evidence", [])] + [""]
    out += ["## Failed fixes", ""] + [f"- {x}" for x in found["failed_fixes"]] + [""]
    out += ["## Sources", ""]
    for x in found["sources"]:
        out += [f"### {x['id']} — {x['title']}", "", f"- URL: {x['url']}", f"- Retrieved: {x['retrieved']}",
                f"- Locator: {x['locator']}", f"- Fragment: {x['fragment']}",
                f"- Applicability: {x['applicability']}",
                f"- Counterevidence: {x['counterevidence'] or 'none given'}",
                f"- Validation: {x['validation']}", ""]
    out += ["## Claims", ""] + [f"- {c['id']} ({'interpretation' if c['kind'] == 'interpretation' else 'source'};"
                                f" {', '.join(c['sources'])}): {c['text']}" for c in found["claims"]] + [""]
    out += ["## Core theory", "", found["theory"], "", "## Applicability", "", found["applicability"], "",
            "## Hypothesis", "", found["hypothesis"], ""]
    out += ["## Counterevidence", ""] + [f"- {x}" for x in found["counterevidence"] or ["none given"]] + [""]
    out += ["## Alternatives", ""] + [f"- {x}" for x in found["alternatives"] or ["none given"]] + [""]
    out += ["## Experiment", "", found["experiment"], "", "## Result", "", PENDING, "",
            "## Unresolved limits", "", found["limits"], ""]
    return "\n".join(out)


def charge(lp, key: str, s: dict, **spent) -> None:
    s["spent"] = {**s["spent"], **spent}
    _save(lp.repo, lp.sid, {key: s})


def research(lp, spec: dict, path: Path, key: str, s: dict) -> bool:
    """The bounded research turn of the active cycle, then its page written
    and committed. `False` after stopping with the reason."""

    from . import loop

    limits, spent = s["limits"], s["spent"]
    budget = Budget(seconds=max(0.0, limits["seconds"] - spent["seconds"]),
                    calls=max(0, limits["calls"] - spent["calls"]), candidates=0,
                    tokens=max(0, limits["tokens"] - spent["tokens"]), cancel=lp.halt)
    chosen = spec.get("cell") or {}
    text = asked(spec, key, s)
    found = None
    for attempt in range(2):
        try:
            budget.call()
        except Exhausted as exc:
            halt(lp, {key: s}, "budget", f"리서치 한도를 다 썼다 ({exc}) — 한도를 늘려 [계속] 한다")
            return False
        except Cancelled:
            return False
        s["active"]["inflight"] = True
        charge(lp, key, s, calls=s["spent"]["calls"] + 1)
        chat = ChatSession(path, tools=TOOLS, system=PROMPT, model=chosen.get("model") or None,
                           effort=chosen.get("effort") or None)
        started = time.monotonic()
        expired = threading.Event()
        run = lp.run = planning.opened(path, chat)
        if lp.halt.is_set():
            run.halt.set()

        def expire() -> None:
            expired.set()
            run.halt.set()
            chat.stop(run.halt)

        timer = threading.Timer(budget.left(), expire)
        timer.daemon = True
        timer.start()
        try:
            final, failed, meta, _, web = planning.consume(path, run, text)
            cut = run.halt.is_set()   # the turn's own stop (`/api/work/stop`), the deadline's too
        finally:
            timer.cancel()
            run.finish()
            lp.run = None
            chat.close()
        tokens = meta.get("tokens") or {}
        known = type(tokens.get("in")) is int and type(tokens.get("out")) is int
        if known:
            budget.charge({"input_tokens": tokens["in"], "output_tokens": tokens["out"]})
        s["active"]["inflight"] = False
        unknown = s["spent"].get("unknown") or not known
        charge(lp, key, s, seconds=round(s["spent"]["seconds"] + time.monotonic() - started, 1),
               tokens=s["spent"]["tokens"] + (tokens["in"] + tokens["out"] if known else 0), unknown=unknown,
               # A lower bound accepted before covers the turns before; this one's missing usage is new.
               accepted=bool(s["spent"].get("accepted")) and known)
        if lp.halt.is_set():
            return False
        if expired.is_set():
            halt(lp, {key: s}, "deadline", "리서치 시간 한도에 닿아 턴을 끊었다 — 한도를 늘려 [계속] 한다")
            return False
        if cut:
            halt(lp, {key: s}, "host", "리서치 턴을 멈췄다 — [계속] 하면 같은 사이클의 리서치를 다시 돈다")
            return False
        if failed:
            halt(lp, {key: s}, "host", f"리서치 턴이 실패했다 — {failed}")
            return False
        if not web:
            halt(lp, {key: s}, "web_unavailable", "이 턴에 웹 검색·가져오기가 한 번도 돌지 않았다 — 웹 도구가 없는 호스트다")
            return False
        try:
            found = read(final, path)
            break
        except ValueError as exc:
            if attempt:
                halt(lp, {key: s}, "format", f"리서치 답을 읽지 못했다 — {exc}")
                return False
            if not spendable(lp, key, s):
                return False   # no second turn on a spend not known, or already over
            text = (f"Your answer could not be read: {exc}\n\nAnswer again, ending with exactly the "
                    "`recovery-research` block.")
    rel = page_of(spec, key, s["active"]["cycle"])
    release = loop.wait_hold(lp, path)
    if release is None:
        return False
    try:
        content = rendered(spec, key, s, found)
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        planning.atomic(path / rel, content)
        planning.committed(path, [rel], f"docs: research {key} before fixing it again (cycle {s['active']['cycle']})")
        commit = planning.head_of(path)
    except (OSError, RuntimeError) as exc:
        halt(lp, {key: s}, "page", f"리서치 페이지를 쓰거나 커밋하지 못했다 — {exc}")
        return False
    finally:
        release()
    s["active"].update(status="researched", page=rel, page_commit=commit, hypothesis=found["hypothesis"],
                       experiment=found["experiment"], sources=[x["url"] for x in found["sources"]],
                       page_sha256=planning.sha(content))
    s["phase"] = "fixing"
    _save(lp.repo, lp.sid, {key: s})
    return True


# -- Around the fix ------------------------------------------------------------------------

def briefed(spec: dict, researched: list[dict]) -> str:
    """What the correction turn is told of the research it must build on."""

    where = ("Return each page as a `plan-file` block with its `## Result` section filled."
             if spec.get("planning") else "Commit the pages together with the fix.")
    out = [f"\n\nBefore this correction the server required research on {len(researched)} recurring finding(s). "
           "Read each page first, run its experiment, then fix. Replace the line under `## Result` in each page "
           f"with what you observed, naming the tests and the commit. {where} The pages stay drafts: do not "
           "present them as verified."]
    for s in researched:
        a, t = s["active"], s["trigger"]
        out.append(f"\n- `{s['issue_id']}` ({t['kind']}, rounds {t['rounds']}), cycle {a['cycle']} of {CYCLES}: "
                   f"`{a['page']}`, committed at `{a['page_commit'][:7]}`. Hypothesis: {a['hypothesis']} "
                   f"Proposed experiment: {a['experiment']}")
    return "".join(out)


def unrecorded(path: Path, researched: list[dict]) -> str:
    """Why the correction did not record every research result, or `""`."""

    for s in researched:
        page = s["active"]["page"]
        shown = specs.sh(["git", "show", f"HEAD:{page}"], path)
        if shown.returncode:
            return f"리서치 페이지 `{page}` 가 수정 커밋에 없다"
        body = shown.stdout.split("## Result", 1)[-1].split("\n## ", 1)[0]
        if "## Result" not in shown.stdout or PENDING in body or not body.strip():
            return f"리서치 페이지 `{page}` 의 Result 에 관찰한 결과를 적지 않았다"
    return ""


def finished(spec: dict, researched: list[dict], path: Path) -> dict:
    """The recovery state once the researched fix went out: each attempt kept,
    its outcome left to the next round. Saved with the round's disposition in
    one write, so a restart never sees the fix done and the attempt open."""

    state = dict(spec.get("recovery") or {})
    head = planning.head_of(path)
    for r in researched:
        s = state[r["issue_id"]]
        a = {**s["active"], "status": "fixed", "fix_head": head, "outcome": None, "spent": dict(s["spent"])}
        state[r["issue_id"]] = {**s, "attempts": [*s.get("attempts", []), a], "active": None, "phase": "fixed"}
    return state


STOPS = ("limits", "budget", "deadline", "unknown", "exhausted", "host", "web_unavailable", "format", "page")


def resumed(spec: dict, limits: dict | None, note: str) -> dict:
    """`[계속]` of a recovery stop, for every issue it stopped: the person's
    limits, accepted spend, or ruling. The changed spec fields, or
    `ValueError`."""

    state = {k: {**v, "spent": dict(v["spent"])} for k, v in (spec.get("recovery") or {}).items()}
    stopped = [k for k, s in state.items() if s.get("phase") in STOPS]
    if not stopped:
        raise ValueError("멈춘 재조사가 없다")
    fields: dict = {}
    if exhausted := [k for k in stopped if state[k]["phase"] == "exhausted"]:
        if not note:
            raise ValueError("목표·제약 변경, 다른 방안, 미룸 중 정한 것을 적어야 잇는다")
        for k in exhausted:
            state[k].update(phase="ruled", ruling=note)
        fields["rulings"] = [*(spec.get("rulings") or []), *(f"{k}: {note}" for k in exhausted)]
    for k in stopped:
        s = state[k]
        if s["phase"] == "ruled":
            continue
        if limits is not None or s["phase"] in ("limits", "budget", "deadline"):
            given = limited(limits)
            if any(given[x] <= s["spent"][x] for x in given):
                raise ValueError(f"`{k}` 의 새 한도는 이미 쓴 양보다 커야 한다")
            s["limits"] = given
        if s["phase"] == "unknown":
            s["spent"]["accepted"] = True    # the spend so far stands as a lower bound
        a = s.get("active")
        s["phase"] = ("fixing" if a["status"] == "researched" else "researching") if a else "required"
    return {**fields, "recovery": state}


def interrupted(spec: dict) -> dict | None:
    """A restart's view of an in-flight research turn: its spend unknown."""

    state = spec.get("recovery") or {}
    if not any((s.get("active") or {}).get("inflight") for s in state.values()):
        return None
    out = {}
    for k, s in state.items():
        s = dict(s)
        if (s.get("active") or {}).get("inflight"):
            s["active"] = {**s["active"], "inflight": False}
            s["spent"] = {**s["spent"], "unknown": True, "accepted": False}
        out[k] = s
    return out
