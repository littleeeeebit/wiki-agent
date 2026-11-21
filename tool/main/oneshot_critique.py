"""oneshot S7's critique and R3: a read-only critic that sees the screenshots, the stop rule, and the final sheet.

`docs/plans/oneshot/4-frontend.md` ("Critique", "Fix and repeat", "R3") is the design. The critic is a
separate read-only Claude turn whose screenshots travel in the turn itself (`ChatSession.say(images=...)`);
it never sees the builder's reasoning. A round is clean when neither the harness nor the critic found
anything high. Two clean rounds in a row open R3; six rounds with high findings left stop with `critique`.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import oneshot, oneshot_brief as brief, oneshot_build, oneshot_desktop, oneshot_device as device, \
    oneshot_front_build as front_build, oneshot_pr, oneshot_review as review, oneshot_screens as screens, oneshot_spec
from .oneshot_profile import kinds_of, shape
from .oneshot_sheet import KIND_NAMES
from .query import ROOT

CRITIC_PROMPT = (ROOT / "tool/prompts/oneshot-critic.md").read_text(encoding="utf-8")
SEVERITIES = ("high", "medium", "low")
STATES = ("full", "empty", "error", "loading")   # the order the critic sees them in
MAX_ROUNDS = 6
MAX_IMAGES = 24
SHOT = re.compile(r"S[1-9]\d*-(desktop|phone|window)-(empty|full|loading|error)\.png")

router = APIRouter()


def critic() -> dict:
    """The critic's model: Claude, the one provider whose turns carry images."""

    return {"model": "opus", "effort": "high"}


oneshot.ROLES["critic"] = critic


def begin(repo: str) -> None:
    """After a build passed the harness: one critique worker; a start a shutdown refused stays resumable."""

    try:
        oneshot_spec.start(repo, "critique", stage="critique")
    except HTTPException:
        oneshot_spec.store(repo).stop("restart", "서버가 꺼지는 중이라 화면 비평을 시작하지 못했다")


def folder(repo: str, n: int) -> Path:
    return oneshot_spec.store(repo).folder / "build" / f"r{n}"


def pictures(spec: dict, n: int) -> tuple[list[Path], list[str]]:
    """The shots the critic sees, every screen's `full` first, at most `MAX_IMAGES`, and the names of those
    missing or reached through a link: a round the critic cannot see whole is never judged."""

    o, base = spec["oneshot"], oneshot_spec.store(spec["repo"]).folder
    names = [f"{s['id']}-{v}-{state}.png" for state in STATES for s in o["front"]["screens"]
             for v in front_build.views(o["profile"])][:MAX_IMAGES]
    found = {name: oneshot_spec.within(base, "build", f"r{n}", name) for name in names}
    return [p for p in found.values() if p and p.is_file()], [k for k, p in found.items() if not (p and p.is_file())]


def critique_of(value, spec: dict) -> list[dict]:
    """An `oneshot-critique` block, checked: each finding names a screen route, a viewport and a state."""

    o = spec["oneshot"]
    # An Android screen has no route: the critic names it by its id.
    routes, views = {s.get("route") or s["id"] for s in o["front"]["screens"]}, front_build.views(o["profile"])
    if not isinstance(value, list):
        raise ValueError("`oneshot-critique` 는 발견의 목록이다 (없으면 빈 목록)")
    out = []
    for n, f in enumerate(value, 1):
        if not isinstance(f, dict) or f.get("severity") not in SEVERITIES:
            raise ValueError(f"{n}번째 발견의 `severity` 는 high, medium, low 중 하나다")
        if f.get("route") not in routes or f.get("viewport") not in views or f.get("state") not in STATES:
            raise ValueError(f"{n}번째 발견의 `route`·`viewport`·`state` 가 찍은 화면에 없다")
        if not all(isinstance(f.get(k), str) and f[k].strip() for k in ("what", "fix")):
            raise ValueError(f"{n}번째 발견에 `what` 과 `fix` 가 있어야 한다")
        out.append({k: f[k].strip() if isinstance(f[k], str) else f[k]
                    for k in ("severity", "route", "viewport", "state", "what", "fix")})
    return out


def critique_text(spec: dict, look: dict, shown: list[Path]) -> str:
    o = spec["oneshot"]
    return ("Judge the built frontend from its screenshots, attached in this order:\n\n"
            + "\n".join(f"{i}. `{p.name}`" for i, p in enumerate(shown, 1))
            + f"\n\nThe approved R2 sheet:\n\n{screens.sheet(spec, lambda sid, v: f'{sid}-{v}-full.png')}\n\n"
            f"The owner's frequency map: {json.dumps(o['frequency'], ensure_ascii=False)}\n\nThe chosen palette: "
            f"{o['r2']['palette']}\n\nThe harness's mechanical findings on these shots:\n\n"
            f"{json.dumps(look['findings'], ensure_ascii=False)}\n")


def critique(repo: str) -> None:
    """One critique round, holding the worktree on the HEAD the build and its shots passed, as the backend
    review does: a tree that moved goes back to the build's checks, and so does a round with shots missing."""

    if oneshot.codex_role("critic"):
        oneshot_spec.store(repo).stop("critique", "비평가는 그림을 받는 Claude 세션이어야 한다")
        return

    def body(store, spec, tree):
        o = spec["oneshot"]
        look = o["looks"][-1]
        if why := stale(o):
            review.rebuild(repo, store, why, front_build.STAGE)
            return None
        # Several kinds: one kind a round, the critic seeing only that kind's shots and the harness's findings on them.
        kind = (critic_kind(o) or kinds_of(o)[-1]) if shape(o) == "kinds" else None
        one = front_build.as_kind(spec, kind) if kind else spec
        seen = {**look, "findings": [f for f in look["findings"] if f.get("kind") == kind]} if kind else look
        shown, missing = pictures(one, look["round"])
        if missing:
            review.rebuild(repo, store, f"비평할 화면 사진이 없다 — {', '.join(missing[:6])}", front_build.STAGE)
            return None
        found = oneshot.turned(store, "critic", CRITIC_PROMPT, critique_text(one, seen, shown), "oneshot-critique",
                               lambda value: critique_of(value, one), tree, tuple(shown))
        if found is None:   # a refused or failed image turn is never a critique that saw nothing wrong
            stopped = store.get().get("stopped") or {}
            if stopped.get("reason") == "block":
                store.stop("critique", stopped.get("detail") or "")
            return None
        return lambda: judged(repo, store, spec, seen, [{**f, "kind": kind} for f in found] if kind else found, kind)

    review.holding(repo, "화면 비평", body, "front_built_head", front_build.STAGE)


def stale(o: dict) -> str:
    """Why the last look is no evidence for a critique or the phone round after it: it ran none of the PC app's
    owed `look` checks, or names no freeze, or another than the run's (an earlier version's look); empty when
    it is evidence."""

    look = (o.get("looks") or [{}])[-1]
    if unproven := [x["id"] for x in oneshot_desktop.looked_for(o) if x["id"] not in (look.get("proven") or [])]:
        return f"PC 앱에서 확인할 {', '.join(unproven)} 을 마지막 화면 검사가 돌리지 않았다"
    if look.get("build") and (not look.get("freeze") or look["freeze"] != (o.get("desktop_build") or {}).get("freeze")):
        return "마지막 화면 검사가 지금 얼린 PC 앱 빌드의 것이 아니다"
    return ""


def passed(rounds: list[dict], head: str | None = None, freeze: str | None = None) -> bool:
    """Two clean critiques in a row, the last two of these; with `head`, of those on the HEAD the look passed
    (several kinds: a kind that passed before a fix is judged again on the fix). Only rounds of the look's
    `freeze` of the PC app count: any freeze, even of the same files under the same HEAD and whichever kind's
    round came between, clears every kind's clean rounds, and a build seen before never brings them back."""

    on = [r for r in rounds if (head is None or r["head"] == head) and r.get("freeze") == freeze]
    return len(on) >= 2 and on[-1]["clean"] and on[-2]["clean"]


def counted(o: dict, rounds: list[dict], kind: str | None = None) -> list[dict]:
    """The rounds since the count restarted: `critique_base` restarts every kind's (a fix from the phone or R3),
    and a kind's cap stop restarts only that kind's (`critique_bases`), so the others keep their passes."""

    base = max(int(o.get("critique_base") or 0), int((o.get("critique_bases") or {}).get(kind or "", 0)))
    return [r for r in rounds[base:] if r.get("kind") == kind] if kind else rounds[base:]


def critic_kind(o: dict) -> str | None:
    """Several kinds: the first kind, in the owner's order, without two clean critiques on the HEAD the last look
    passed. A new HEAD has none, so every kind that passed before is judged again."""

    rounds, look = o.get("critiques") or [], o["looks"][-1]
    return next((k for k in kinds_of(o) if not passed(counted(o, rounds, k), look["head"], look.get("freeze"))), None)


def judged(repo: str, store, spec: dict, look: dict, found: list[dict], kind: str | None = None):
    """The round applied; what follows once the tree is free: a second look, the next kind's critique, or the
    fixes' build turn."""

    o = spec["oneshot"]
    rounds = [*(o.get("critiques") or []), {"look": look["round"], "head": look["head"], "findings": found,
                                            "clean": not any(f["severity"] == "high" for f in found + look["findings"]),
                                            "ts": time.time(), **({"kind": kind} if kind else {}),
                                            **({k: look[k] for k in ("build", "freeze") if look.get(k)})}]
    mine = counted(o, rounds, kind)   # the cap counts each kind's own
    done = passed(mine, look["head"] if kind else None, look.get("freeze"))
    if done and kind and critic_kind({**o, "critiques": rounds}):   # the next kind's turn, on the same shots
        oneshot_spec.saved(repo, oneshot={"critiques": rounds})
        return lambda: oneshot_spec.handoff(repo, "critique") or True
    if done and "android" in kinds_of(o):
        from . import oneshot_phone   # it imports this module
        return oneshot_phone.waits(repo, rounds)   # the owner's phone comes before R3
    if done:
        return oneshot_spec.briefing(repo, "r3", {"critiques": rounds, "stage": "r3", "r3": opening(o)})
    # A clean round at the cap earns the one confirming it (a new freeze may have cleared the earlier ones).
    if len(mine) >= MAX_ROUNDS and not (rounds[-1]["clean"] and len(mine) == MAX_ROUNDS):
        high = [f"{f['route']} {f['viewport']} {f['state']}: {f['what']}" for f in found if f["severity"] == "high"]
        detail = f"{KIND_NAMES[kind] + ' ' if kind else ''}{MAX_ROUNDS}회차에도 " + (
            "높은 발견이 남았다 — " + " / ".join(high) if high else "깨끗한 회차가 두 번 이어지지 않았다")
        with oneshot._lock:   # one write; `[재개]` starts a fresh count, and the stop keeps the count it reached
            # (`counted`), so the screen tells a cap stop from a failed critique, which leaves no critique behind
            restart = {"critique_bases": {**(o.get("critique_bases") or {}), kind: len(rounds)}} if kind \
                else {"critique_base": len(rounds)}
            store.put(critiques=rounds, **restart, working=None, stopped={
                "reason": "critique", "detail": detail, "state": store.get()[store.state], "counted": len(mine)})
        return None
    # Nothing to fix, or a clean round at the cap, whose lesser findings a rebuild would only trade for a new freeze
    # with no room left to pass: a second look at the same shots, `working` still set.
    if rounds[-1]["clean"] and (not found or len(mine) == MAX_ROUNDS):
        oneshot_spec.saved(repo, oneshot={"critiques": rounds})
        return lambda: oneshot_spec.handoff(repo, "critique") or True   # the next look holds the tree itself
    todo = found + [f for f in look["findings"] if f["severity"] == "high"]   # a mechanical high outranks a clean critique
    # `working` names the build and `fix` keeps its findings before the turn is sent: a server that dies in
    # between, or a session that cannot open, leaves a `[재개]` that sends the same fixes.
    spec = oneshot_spec.saved(repo, oneshot={"critiques": rounds, "stage": front_build.STAGE, "attempts": 0,
                                             "working": front_build.STAGE,
                                             "fix": {"who": "An independent read-only critic judged the screenshots",
                                                     "findings": todo}})
    return lambda: oneshot_build.send(repo, front_build.STAGE, front_turn(spec))


def opening(o: dict) -> dict:
    """R3's next round: no decisions yet, the screens the owner's last edits changed marked."""

    return {"round": (o.get("r3") or {}).get("round", 0) + 1, "decisions": {},
            "marks": (o.get("r3") or {}).get("next_marks") or {}}


def front_turn(spec: dict) -> str:
    """The build-front turn, with the fixes it is sent for until a build of them passes the harness."""

    fix = spec["oneshot"].get("fix")
    return fixing(spec, fix["findings"], fix["who"]) if fix else front_build.build_front_text(spec)


def fixing(spec: dict, found: list[dict], who: str) -> str:
    ordered = sorted(found, key=lambda f: SEVERITIES.index(f.get("severity", "low")))
    return (f"{who}. Fix every finding below, high first, within the approved design, and finish the turn "
            f"again with its `done-report`.\n\n```json\n{json.dumps(ordered, ensure_ascii=False, indent=2)}\n```\n\n"
            + front_build.build_front_text(spec))


oneshot_spec.WORKERS["critique"] = critique
oneshot_spec.RUNNING["critique"] = "critique"
oneshot_spec.SHEETS.add("r3")   # an approval the worktree refused: `[재개]` puts the sheet back, decisions kept
oneshot_build.STAGES[front_build.STAGE] = (*oneshot_build.STAGES[front_build.STAGE][:2], front_turn, begin)


# -- R3 ------------------------------------------------------------------------------------------

def r3_sheet(spec: dict, shot, phone_shot=None) -> str:
    """The R3 sheet in Markdown; `shot(round, name)` gives a screenshot's URL, and `phone_shot(round, name)`
    one of the phone round's, for an Android app (`docs/plans/oneshot/7-android.md`, "R3 and S8")."""

    o = spec["oneshot"]
    if shape(o) == "kinds":   # one section for each kind, in the owner's order, each read off that kind alone
        out = [f"# 완성된 화면 — {spec['repo']}", ""]
        for k in kinds_of(o):
            body = r3_sheet(brief.of_kind(spec, k), shot, phone_shot if k == "android" else None).split("\n")[2:]
            out += [f"## {KIND_NAMES[k]}", "", *(f"#{line}" if line.startswith("#") else line for line in body)]
        return "\n".join(out)
    front, views = o["front"], front_build.views(o["profile"])
    phone = (o.get("phone") or {}) if phone_shot and (o.get("phone") or {}).get("round") else {}
    passed = [x for x in o["looks"] if not brief.failed(x)]
    first_look, last = passed[0], passed[-1]
    marks = (o.get("r3") or {}).get("marks") or {}
    mark = lambda i: " (고친 화면)" if marks.get(i) else ""   # noqa: E731
    place = lambda s: s.get("route") or s.get("surface")   # noqa: E731 — a web route, or an Android surface
    primary = next(s for s in front["screens"] if s["primary"])
    lines = [f"# 완성된 화면 — {spec['repo']}", "", f"## 첫 화면 {primary['id']}{mark(primary['id'])} `{place(primary)}`",
             "", *[f"![{primary['id']} {v}]({shot(last['round'], f'{primary['id']}-{v}-full.png')})" for v in views],
             *([f"![{primary['id']} 휴대폰]({phone_shot(phone['round'], f'{primary['id']}-phone-full.png')})"] if phone else []),
             "", "## 기능이 놓인 자리", "", "| 칸 | 기능 | 화면 |", "| --- | --- | --- |"]
    for f in o["features"]:
        where = [s["id"] for s in front["screens"] if f["id"] in s["features"]]
        lines.append(f"| {screens.CLASS_NAMES[o['frequency'][f['id']]]} | {f['name']} (`{f['id']}`) | {', '.join(where) or '없음'} |")
    left = [*(o.get("critiques") or [{}])[-1].get("findings", []), *last["findings"]]
    on_phone = phone.get("findings") or []
    lines += ["", "## 남긴 발견", "",
              "높은 발견이 없는 회차가 두 번 이어져 멈춤 규칙이 남긴 것이다." if left or on_phone else "없다.", ""]
    lines += [f"- [{f['severity']}] {f.get('route') or f.get('screen')} {f['viewport']} {f['state']}: {f['what']}"
              + (f" → {f['fix']}" if f.get("fix") else "") for f in left]
    lines += [f"- [{f['severity']}] 휴대폰 {f['screen']} {f['state']}: {f['what']}" for f in on_phone]
    if owed := device.owed(spec):   # every look that passed ran each of them: the last one closed them
        lines += ["", "## 기기에서 확인한 지적", "", "백엔드 리뷰가 휴대폰에서만 보인다며 미룬 지적이다. "
                  f"모두 에뮬레이터 {last['round']}회차 `{last['head'][:12]}` 의 계측 테스트에서 통과했다.", "",
                  *[f"- {x['id']} [{x['severity']}] {x['requirement']} `{x['location']}`: {x['reason']} — 확인: {x['check']}"
                    for x in owed]]
    if phone:
        lines += ["", *measured(phone), "", *walkthroughs(spec)]
    lines += ["", "<details><summary>다른 화면, 상태, 회차마다의 전후, 기계 검사 기록</summary>", ""]
    for s in front["screens"]:
        lines += [f"### {s['id']}{mark(s['id'])} `{place(s)}` — {s['purpose']}", "",
                  *[f"![{s['id']} {state}]({shot(last['round'], f'{s['id']}-{views[0]}-{state}.png')})" for state in STATES],
                  *(["", *[f"휴대폰 {state}: ![{s['id']} 휴대폰 {state}]"
                           f"({phone_shot(phone['round'], f'{s['id']}-phone-{state}.png')})" for state in STATES]]
                    if phone else []),
                  "", f"전: ![{s['id']} 처음]({shot(first_look['round'], f'{s['id']}-{views[0]}-full.png')}) "
                  f"후: ![{s['id']} 마지막]({shot(last['round'], f'{s['id']}-{views[0]}-full.png')})", ""]
    for x in o["looks"]:
        counts = {sev: sum(f["severity"] == sev for f in x["findings"]) for sev in SEVERITIES}
        lines.append(f"- {x['round']}회차 `{x['head'][:12]}`: 높음 {counts['high']}, 보통 {counts['medium']}, 낮음 {counts['low']}")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def measured(phone: dict) -> list[str]:
    """The phone round's device and what it read off the app's first screen: frame timing and memory."""

    size, t = phone.get("size") or {}, phone.get("timing") or {}
    value = lambda v, unit="": "읽지 못함" if v is None else f"{v:g}{unit}"   # noqa: E731
    memory = phone.get("memory_kb")
    return [f"## 휴대폰에서 — {phone.get('model') or '이름 없는 기기'} "
            f"({size.get('width')}×{size.get('height')}, {size.get('dpi')}dpi)", "",
            "첫 화면을 네 번 쓸어 넘기는 동안 앱이 그린 프레임과, 그때의 메모리다.", "",
            "| 잰 것 | 값 |", "| --- | --- |",
            f"| 그린 프레임 | {value(t.get('frames'))} |",
            f"| 버벅인 프레임 | {value(t.get('janky'))} ({value(t.get('janky_percent'), '%')}) |",
            f"| 프레임 시간 50·90·99번째 백분위 | {value(t.get('p50_ms'), 'ms')} · {value(t.get('p90_ms'), 'ms')} · "
            f"{value(t.get('p99_ms'), 'ms')} |",
            f"| 메모리(PSS) | {'읽지 못함' if memory is None else f'{memory / 1024:.1f}MB'} |"]


def walkthroughs(spec: dict) -> list[str]:
    """Each core scenario as the owner reads it: what it does and what holds after. Its instrumented test ran
    on the emulator and on the phone, and a failure on either would have been a high finding."""

    reqs = [r for r in spec.get("requirements") or [] if r.get("postconditions")]
    return ["## 핵심 시나리오", "",
            "각 시나리오의 계측 테스트가 에뮬레이터와 휴대폰에서 모두 통과했다." if reqs else "핵심 시나리오가 없다.", "",
            *[f"- {r['id']} {r['text']} — 끝나면: {' / '.join(r['postconditions'])}" for r in reqs]]


class Decisions(BaseModel):
    decisions: dict[str, dict] = {}
    reading: dict = {}
    source: str = ""   # as on R1 (`oneshot_spec.Decisions`)
    lines: dict[str, dict] = {}
    rest: bool = False


def extra(spec: dict) -> dict:
    o = spec["oneshot"]
    if o.get("stage") != "r3" or oneshot_spec.held(o):   # a run held before R3 has no sheet
        return {"critiques": o.get("critiques") or []} if o.get("critiques") else {}
    phone_shot = (lambda n, name: f"/api/oneshot/spec/{spec['repo']}/phone/shot/{n}/{name}") \
        if "android" in kinds_of(o) else None
    shot = lambda n, name: f"/api/oneshot/spec/{spec['repo']}/look/{n}/{name}"   # noqa: E731
    return {"critiques": o.get("critiques") or [], "r3_items": [s["id"] for s in o["front"]["screens"]],
            "r3_sheet": r3_sheet(spec, shot, phone_shot),
            "r3_brief": {**brief.shown(spec, "r3"), "pictures": brief_pictures(spec, shot, phone_shot)}}


def brief_pictures(spec: dict, shot, phone_shot) -> dict[str, list[str]]:
    """The R3 brief's pictures, from the spec: the first screen before and after under `화면`, and with each
    finding's line the shot it was found on."""

    snap = brief.snapshot(spec, "r3")
    looks, phone, profile = snap["looks"], snap["phone"], spec["oneshot"]["profile"]
    if not looks:
        return {}
    view = lambda s: front_build.views(profile, s.get("kind", ""))[0]   # noqa: E731 — several kinds: each screen's own
    firsts = [s for s in snap["screens"] if s["primary"]]   # several kinds: each kind's first screen
    droid = next((s["id"] for s in firsts if s.get("kind", "android") == "android"), firsts[0]["id"])
    out = {"화면": [*(shot(looks[when], f"{s['id']}-{view(s)}-full.png") for s in firsts for when in ("before", "after")),
                  *([phone_shot(phone["round"], f"{droid}-phone-full.png")] if phone else [])]}
    for f in snap["findings"]:
        state = f.get("state") if f.get("state") in STATES else "full"
        out[f["key"]] = ([phone_shot(phone["round"], f"{f['screen']}-phone-{state}.png")] if f["from"] == "phone"
                         else [shot(looks["after"], f"{f['screen']}-{f.get('viewport') or view(f)}-{state}.png")])
    return out


def asked_fixes(spec: dict, body: Decisions, briefed: bool, comments: dict[str, str]) -> list[dict]:
    """The owner's R3 edits as the builder's findings. On the brief, one per finding edited, carrying that
    finding with where it was seen (the builder never sees the brief's `L<n>` or its pictures, and the
    harness says the same words of different states), and one per item edited on the folded sheet."""

    kinds = {s["id"]: {"kind": s["kind"]} for s in spec["oneshot"]["front"]["screens"] if s.get("kind")}   # several kinds
    if not briefed:
        return [{"severity": "high", "screen": k, **kinds.get(k, {}), "what": v} for k, v in comments.items()]
    left = {f["key"]: f for f in brief.snapshot(spec, "r3")["findings"]}
    return [*({"severity": "high", "screen": left[k]["screen"], **kinds.get(left[k]["screen"], {}),
               **{at: left[k][at] for at in ("viewport", "state") if left[k].get(at)},
               "finding": left[k]["what"], "what": d["edit"].strip()}
              for k, d in body.lines.items() if d.get("edit")),
            *({"severity": "high", "screen": i, **kinds.get(i, {}), "what": d["edit"].strip()}
              for i, d in body.decisions.items() if i in comments and brief.valid(d) and d.get("edit"))]


oneshot_spec.VIEWS.append(extra)


@router.post("/api/oneshot/spec/{repo}/r3")
def decide(repo: str, body: Decisions) -> dict:
    """Every screen approved moves the run to its pull request; edits go to the builder as one turn and
    the loop runs again from shooting, its stop rule counted afresh."""

    with oneshot._lock:
        spec = oneshot_spec.spec_of(repo)
        o = spec["oneshot"]
        oneshot_spec.idle(o, "r3")
        items = [s["id"] for s in o["front"]["screens"]]
        given, briefed = oneshot_spec.on_brief(spec, "r3", items, body)
        decisions, comments = oneshot_spec.decided(items, o["r3"]["decisions"], given)
        reading = oneshot_spec.read(o, "r3", body.reading, comments, briefed)
        if not comments:
            oneshot_spec.saved(repo, oneshot={"r3": {**o["r3"], "decisions": decisions}, "reading": reading})
            approved(repo)
        else:
            # `working` and `fix` are saved with the decisions: a server that dies before the turn is sent, or a
            # session that cannot open, leaves a `[재개]` that sends the owner's edits again.
            spec = oneshot_spec.saved(repo, oneshot={
                "stage": front_build.STAGE, "attempts": 0, "critique_base": len(o.get("critiques") or []),
                "working": front_build.STAGE, "reading": reading,
                "fix": {"who": "The owner reviewed the finished screens (R3)",
                        "findings": asked_fixes(spec, body, briefed, comments)},
                "r3": {**o["r3"], "decisions": decisions, "next_marks": {k: "edited" for k in comments}}})
    if not comments:
        if oneshot_spec.spec_of(repo)["oneshot"]["stage"] == oneshot_pr.STAGE:   # outside the lock: the turn takes the worktree's hold
            oneshot_pr.begin(repo)
        return oneshot_spec.view(oneshot_spec.spec_of(repo))
    oneshot_build.send(repo, front_build.STAGE, front_turn(spec))
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def approved(repo: str) -> None:
    """Every screen approved: the run moves to its pull request on the HEAD the critic judged. It holds the
    worktree as a turn does, so a tree another turn holds refuses the approval (409, the decisions kept),
    and a tree that moved since goes back to the build's checks."""

    def moving(store, spec, tree) -> bool:
        # `working` names the README turn before it is sent: a server that dies in between leaves it for
        # `recover`, and the turn that follows sends the queued instruction when it ends.
        oneshot_spec.saved(repo, oneshot={"stage": oneshot_pr.STAGE, "working": oneshot_pr.STAGE})
        return True

    review.approval(repo, "R3 승인", moving, "front_built_head", front_build.STAGE)


@router.get("/api/oneshot/spec/{repo}/look/{n}/{name}")
def look_shot(repo: str, n: int, name: str) -> FileResponse:
    oneshot_spec.spec_of(repo)   # a repository with a oneshot spec, never a path
    path = SHOT.fullmatch(name) and oneshot_spec.within(oneshot_spec.store(repo).folder, "build", f"r{n}", name)
    if not path or not path.is_file():   # a link or a junction on the way never serves a file outside the run
        raise HTTPException(404, "그런 화면 사진이 없다")
    return FileResponse(path, media_type="image/png")
