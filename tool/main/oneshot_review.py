"""oneshot S4's backend review: a read-only reading of the build, between agents and before any pull request.

`docs/plans/oneshot/3-spec-backend.md` ("Backend review") is the design.
The input is frozen per HEAD: the design and the requirements go into the turn's prompt, the diff from the
base commit is written where `pr_diff` reads it, and a copy is kept in the run's folder as the record.
A refusal goes to the build and the new HEAD is reviewed again, up to the Review Loop's round cap; the
refusal that reaches it stops the run with `review`. Nothing here pushes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from agent import read_tools

from . import oneshot, oneshot_build, oneshot_front, oneshot_spec, runtime, specs, work  # noqa: F401 — `oneshot_front` registers `split`
from .oneshot_profile import environment, kinds_of, shape, waits_at
from .query import _lock as work_lock, hold

SEVERITIES = ("P0", "P1", "P2")
DEVICE_KINDS = ("android", "desktop")   # the kinds whose S7 runs the app on a device the host's tests cannot


def cap() -> int:
    """Refusals before the run stops: the Review Loop's round cap, so the review and its repair loop as a PR's do."""

    from . import loop   # it imports `specs`, which imports this module's neighbours

    return loop.settings()["rounds"]


def reviewer() -> dict:
    """The model the Review Loop settings name: an independent reader, not the builder's role."""

    from . import loop   # it imports `specs`, which imports this module's neighbours

    return {"model": loop.review_model(), "effort": loop.settings()["review_effort"]}


oneshot.ROLES["review"] = reviewer


def begin(repo: str) -> None:
    """After the build passed: one review worker; a start refused by a shutdown leaves it resumable."""

    try:
        oneshot.start_worker(oneshot_spec.store(repo), "review", review, (repo,), {})
    except HTTPException:
        oneshot_spec.store(repo).stop("restart", "서버가 꺼지는 중이라 백엔드 리뷰를 시작하지 못했다")


def frozen(spec: dict) -> tuple[str, str]:
    """`(head, how to read the diff)`, the diff from the base commit written for `pr_diff` and kept as the record."""

    tree, store = Path(spec["worktree"]), oneshot_spec.store(spec["repo"])
    head = specs.sh(["git", "rev-parse", "HEAD"], tree).stdout.strip()
    done = specs.sh(["git", "diff", "--no-color", spec["start_head"], head], tree, 120)
    if done.returncode:
        raise ValueError(f"리뷰할 diff 를 읽지 못했다 — {(done.stderr or '').strip()[-300:]}")
    store.folder.mkdir(parents=True, exist_ok=True)
    (store.folder / f"review-{head[:12]}.diff").write_text(done.stdout, encoding="utf-8", newline="\n")
    read_tools.save_diff(tree, done.stdout)
    how = ("read it with the `pr_diff` tool" if oneshot.codex_role("review", store) else
           f"read it from `{read_tools.diff_file(tree).resolve()}`; a line starting with ~ continues the line before it")
    return head, how


def review_text(spec: dict, head: str, how: str) -> str:
    o = spec["oneshot"]
    built = "core" if "modules" in o["design"] else "backend"
    return (f"Review HEAD `{head}` of this repository's {built} build.\n\nThe approved design:\n\n"
            f"{json.dumps(o['design'], ensure_ascii=False)}\n\nThe requirements:\n\n"
            f"{json.dumps(spec.get('requirements') or [], ensure_ascii=False)}\n\nThe environment profile and the stack:\n\n"
            f"{environment(o)}\n\nThe pinned contract tests, unchanged since "
            f"`{o['tests_head']}`: {', '.join(o['contract'])}.\n\nThe whole diff from the base commit "
            f"`{spec['start_head']}` to HEAD: {how}.\n" + earlier_text(o.get("reviews") or []))


STUCK = 3   # refusals of one area after which the build replaces its mechanism instead of patching the next case


def area_of(f: dict) -> str:
    return " ".join(str(f.get("area") or "").split()).casefold()


def refusals(reviews: list[dict]) -> list[dict]:
    """Every blocking finding of the refusals, oldest first, numbered by round."""

    return [{"round": n, "area": f.get("area") or "", **{k: f[k] for k in ("severity", "location", "reason")}}
            for n, r in enumerate(reviews, 1) if r["verdict"] == "refuse"
            for f in r["findings"] if f["severity"] != "P2" and "device" not in f]


def earlier_text(reviews: list[dict]) -> str:
    """The reviewer's memory: each earlier refusal, so a fixed one is checked, not found again in new words."""

    past = refusals(reviews)
    return (f"\nEarlier refusals of this build, oldest first; the builder has tried to fix each since:\n\n```json\n"
            f"{json.dumps(past, ensure_ascii=False)}\n```\n\nCheck each against this HEAD first. Leave out one the "
            "code now fixes; one that still holds, or that a fix moved elsewhere in the same mechanism, keeps that "
            "earlier finding's `area`, word for word.\n") if past else ""


def deferrable(spec: dict) -> dict[str, set[str]]:
    """Each kind of the run that runs on a device, with the requirements that apply to it: where a finding
    only that device can show may wait (`docs/plans/oneshot/9-kinds.md`, "The backend review and what only a
    device can show"). A web app has none."""

    kinds = kinds_of(spec["oneshot"])
    return {k: {r["id"] for r in spec.get("requirements") or [] if k in (r.get("kinds") or kinds)}
            for k in kinds if k in DEVICE_KINDS}


def deferred(f: dict, devices: dict[str, set[str]]) -> bool:
    """`f`'s `device` names a P0 or P1 finding's kind with a device, a requirement of that kind, and a check."""

    d = f["device"]
    return f["severity"] != "P2" and isinstance(d, dict) and d.get("kind") in devices \
        and d.get("requirement") in devices[d["kind"]] and isinstance(d.get("check"), str) and bool(d["check"].strip())


def verdict_of(value, head: str, devices: dict[str, set[str]] | None = None) -> dict:
    """An `oneshot-review` block, checked: a verdict on the HEAD read, findings that justify it. A P0 or P1
    finding marked `device` waits for that kind's device (`devices`, from `deferrable`) and blocks nothing."""

    if not isinstance(value, dict) or value.get("verdict") not in ("approve", "refuse"):
        raise ValueError("`oneshot-review` 는 `verdict` 가 approve 나 refuse 인 객체다")
    if value.get("head") != head:
        raise ValueError(f"판정은 읽은 HEAD `{head}` 를 그대로 적어야 한다 — 받은 값 `{value.get('head')}`")
    findings = value.get("findings") or []
    if not isinstance(findings, list) or not all(
            isinstance(f, dict) and f.get("severity") in SEVERITIES
            and all(isinstance(f.get(k), str) and f[k].strip() for k in ("location", "reason", "evidence"))
            for f in findings):
        raise ValueError("`findings` 마다 `location`, `severity`(P0·P1·P2), `reason`, `evidence` 가 있어야 한다")
    if any("device" in f and not deferred(f, devices or {}) for f in findings):
        raise ValueError("`device` 는 P0·P1 발견에만 붙고, 기기가 있는 종류(" + (", ".join(devices or {}) or "이 실행엔 없다")
                         + "), 그 종류에 걸린 요구, 기기에서 볼 `check` 를 적는다")
    serious = [f for f in findings if f["severity"] != "P2" and "device" not in f]
    if not all(area_of(f) for f in serious):
        raise ValueError("P0·P1 발견마다 그것이 걸린 동작 방식의 이름 `area` 를 적는다")
    if value["verdict"] == "approve" and serious:
        raise ValueError("P0·P1 발견이 있는데 승인했다")
    if value["verdict"] == "refuse" and not serious:
        raise ValueError("거부에는 P0·P1 발견이 하나 이상 있어야 한다")
    return {"verdict": value["verdict"], "head": head,
            "findings": [{**{k: f[k] for k in ("location", "severity", "reason", "evidence")},
                          **({"area": " ".join(str(f["area"]).split())} if area_of(f) else {}),
                          **({"device": {k: f["device"][k] for k in ("kind", "requirement", "check")}}
                             if "device" in f else {})} for f in findings]}


def owing(found: dict) -> list[dict]:
    """The approving review's deferred findings, numbered `O<n>`: the spec's `owed` list, each a check its
    kind's S7 loop must pass on the device."""

    return [{"id": f"O{n}", **f["device"], **{k: f[k] for k in ("location", "severity", "reason", "evidence")},
             "head": found["head"]} for n, f in enumerate((f for f in found["findings"] if "device" in f), 1)]


def review(repo: str) -> None:
    def body(store, spec, tree):
        found = verdict(store, spec, tree)
        return None if found is None else lambda: decided(repo, store, spec, found)

    holding(repo, "백엔드 리뷰", body)


def holding(repo: str, what: str, body, passed: str = "built_head", build: str = "build") -> None:
    """A stage that reads the worktree holds it, as a work turn does, so no other turn writes the tree it
    reads. `body(store, spec, tree)` runs on the HEAD the build passed (`passed` names it; a moved tree
    goes back to the `build` stage), on a clean tree, and returns the
    callable that saves its result, or `None` after stopping; the tree is checked again before that is
    called. A moved tree goes back to the build's checks. A callable the save returns runs once the tree
    is let go: the refusal's build turn, or the next stage's worker, which then holds the tree itself
    (it returns true). Otherwise
    the instruction the person queued for this worktree meanwhile goes, as after any turn."""

    store = oneshot_spec.store(repo)
    spec = oneshot_spec.spec_of(repo)
    tree = Path(spec["worktree"])
    try:
        release = hold(work._busy, work_lock, str(tree), "작업트리를 다른 턴이 쓰고 있다", kind="turn")
    except HTTPException as exc:   # whoever holds it sends the queued instruction when it lets go
        store.stop("block", f"{exc.detail} — 끝난 뒤 [재개] 로 {what}을 다시 한다")
        return
    passed_on = False
    try:
        following = None
        try:
            if why := moved(spec, tree, passed):
                rebuild(repo, store, why, build)
                return
            save = body(store, spec, tree)
            if save is None:
                return
            if why := moved(spec, tree, passed):
                rebuild(repo, store, f"{what} 동안 작업트리가 바뀌었다 — {why}", build)
                return
            following = save()
        finally:
            release()
        if callable(following):   # a save that only saves returns the spec
            passed_on = bool(following())
    finally:   # an exception included: the worker records the stop, and the instruction still goes
        if not passed_on:   # a server going down drops it, as `dispatch` drops one after a stopped turn
            work.dispatch(tree, SimpleNamespace(halt=runtime.stopping, turn=""))


def approval(repo: str, what: str, then, passed: str = "built_head", build: str = "build") -> None:
    """An owner's approval holds the worktree as a turn does, on the HEAD the build passed (`passed`): a
    tree another turn holds refuses it (409, the decisions kept), and a moved one goes back to the `build`
    stage's checks before anything is written. `then(store, spec, tree)` returns true when what follows
    sends the instruction queued for the worktree itself."""

    spec, store = oneshot_spec.spec_of(repo), oneshot_spec.store(repo)
    tree = oneshot_spec.own_tree(spec)
    if not tree:
        store.stop("folder", f"작업트리가 `{oneshot.BRANCH}` 브랜치의 그 작업트리가 아니다: {spec['worktree']}")
        return
    release = hold(work._busy, work_lock, str(tree), "작업트리를 다른 턴이 쓰고 있다 — 끝난 뒤 다시 승인하라",
                   kind="turn")
    passed_on = False
    try:
        if why := moved(spec, tree, passed):
            rebuild(repo, store, f"{what} 전에 작업트리가 바뀌었다 — {why}", build)
        else:
            passed_on = bool(then(store, spec, tree))
    finally:
        release()
        if not passed_on:
            work.dispatch(tree, SimpleNamespace(halt=runtime.stopping, turn=""))


def moved(spec: dict, tree: Path, passed: str = "built_head") -> str:
    """Empty when the worktree is the HEAD the build passed (`passed` names it), with nothing uncommitted,
    or how it is not."""

    built = spec["oneshot"].get(passed)
    head = specs.sh(["git", "rev-parse", "HEAD"], tree).stdout.strip()
    if head != built:
        return f"HEAD `{head[:12]}` 가 빌드를 통과한 `{str(built)[:12]}` 가 아니다"
    return "작업트리에 커밋하지 않은 변경이 있다" if specs.dirty(tree) else ""


def decided(repo: str, store, spec: dict, found: dict):
    """The verdict applied; what follows once the tree is free: the split's worker, or the refusal's build turn."""

    o = spec["oneshot"]
    reviews = [*(o.get("reviews") or []), {**found, "ts": time.time()}]
    if found["verdict"] == "approve" and waits_at(o) == "split":   # several kinds: the split is the next step's (9c)
        with oneshot._lock:
            oneshot_spec.saved(repo, oneshot={"stage": "split", "reviews": reviews, "owed": owing(found),
                                              "working": None, "stopped": {
                "reason": "kind", "detail": "여러 종류의 기능 나누기는 다음 버전에서 이어진다 — 백엔드 리뷰가 승인했다",
                "state": "split"}})
        return None
    if found["verdict"] == "approve":
        # `working` names the split before its worker starts: a server that dies in between leaves it for `recover`.
        oneshot_spec.saved(repo, oneshot={"stage": "split", "reviews": reviews, "owed": owing(found),
                                          "working": "split"})
        return lambda: oneshot_spec.handoff(repo, "split") or True   # the split holds the tree itself
    refused = int(o.get("refused") or 0) + 1
    if refused >= cap():
        detail = f"백엔드 리뷰가 {refused}번 거부했다 — " + \
            " / ".join(f"[{f['severity']}] {f['location']} {f['reason']}" for f in found["findings"][:5])
        with oneshot._lock:   # one write: the stop names the HEAD it refused at the cap (`capped`), so a server
            # dying in between never leaves a refusal behind a `restart`, and a later cap change moves nothing
            store.put(reviews=reviews, refused=refused, working=None, stopped={
                "reason": "review", "detail": detail, "state": store.get()[store.state], "capped": found["head"]})
        return None
    # `working` names the build before it is sent: a server that dies in between leaves it for `recover`.
    spec = oneshot_spec.saved(repo, oneshot={"stage": "build", "reviews": reviews, "refused": refused,
                                             "attempts": 0, "working": "build"})
    return lambda: oneshot_build.send(repo, "build", fixing(spec, found))


def verdict(store, spec: dict, tree: Path) -> dict | None:
    """The checked verdict on the frozen HEAD, or `None` after stopping."""

    try:
        head, how = frozen(spec)
    except (ValueError, OSError, RuntimeError) as exc:
        store.stop("review", str(exc))
        return None
    return oneshot.turned(store, "review", oneshot.prompt("review", shape(spec["oneshot"])), review_text(spec, head, how),
                          "oneshot-review",
                          lambda value: verdict_of(value, head, deferrable(spec)), tree)


def rebuild(repo: str, store, why: str, build: str = "build") -> None:
    """The tree is not what the build passed: `[재개]` sends the `build` stage's turn, whose checks run again."""

    oneshot_spec.saved(repo, oneshot={"stage": build, "attempts": 0})
    store.stop("gate", f"{why} — [재개] 로 빌드 검사부터 다시 한다")


def fixing(spec: dict, found: dict) -> str:
    serious = [f for f in found["findings"] if f["severity"] != "P2" and "device" not in f]   # those wait for the device
    return (f"An independent read-only review refused HEAD `{found['head']}`. Fix every finding below "
            "where it points, without changing the pinned contract tests; a finding you disagree with gets "
            "evidence in your answer instead of a change.\n\n```json\n"
            f"{json.dumps(serious, ensure_ascii=False, indent=2)}\n```\n\n"
            + recurring(spec["oneshot"].get("reviews") or [], found, serious) + oneshot_build.build_text(spec))


def recurring(reviews: list[dict], found: dict, serious: list[dict]) -> str:
    """The earlier refusals in the areas this one names; an area refused `STUCK` times or more gets its mechanism
    replaced, by the same build turn: the run never stops for it."""

    areas = {area_of(f) for f in serious} - {""}
    past = [f for f in refusals(reviews[:-1]) if area_of(f) in areas]   # the last review is `found` itself
    if not past:
        return ""
    rounds = {a: 1 + len({f["round"] for f in past if area_of(f) == a}) for a in areas}
    stuck = sorted(a for a, n in rounds.items() if n >= STUCK)
    said = (f"Earlier refusals in the same areas, which earlier fixes did not end:\n\n```json\n"
            f"{json.dumps(past, ensure_ascii=False, indent=2)}\n```\n\n")
    if stuck:
        said += ("The review has refused " + ", ".join(f"`{a}` {rounds[a]} times" for a in stuck) + ": each fix of one "
                 "case has led to the next. Do not add one more special case. Read that area's findings together, "
                 "name the assumption they share, and replace the mechanism with one that meets the requirements "
                 "without it, removing the special cases the old one gathered; then check every finding of the "
                 "area, earlier ones included, against the new mechanism. Say in your answer what you replaced "
                 "and why.\n\n")
    return said


def build_turn(spec: dict) -> str:
    """A resumed build's turn: the review's findings again while its refusal of the built HEAD stands."""

    last = standing(spec["oneshot"])
    return fixing(spec, last) if last else oneshot_build.build_text(spec)


def standing(o: dict) -> dict | None:
    """The last review, while it is a refusal of the built HEAD."""

    last = (o.get("reviews") or [None])[-1]
    return last if last and last["verdict"] == "refuse" and last["head"] == o.get("built_head") else None


def capped(o: dict) -> bool:
    """Stopped at the cap with the built HEAD refused: `[재개]` repairs it, a second reading would only repeat.
    Read off the stop itself, never today's cap: any other stop of the review reads the HEAD again."""

    last = standing(o)
    return last is not None and (o.get("stopped") or {}).get("capped") == last["head"]


def resume(repo: str) -> None:
    """`[재개]` in `review`, the built HEAD not refused at the cap: the review run again on the current HEAD."""

    oneshot_spec.saved(repo, oneshot={"refused": 0})
    begin(repo)


def limits(spec: dict) -> dict:
    """What the screen counts a stage's tries against: the server's own caps, never a copy on the screen."""

    from . import oneshot_critique   # it imports this module

    return {"limits": {"attempts": oneshot_build.MAX_ATTEMPTS, "refusals": cap(),
                       "critiques": oneshot_critique.MAX_ROUNDS}}


oneshot_spec.VIEWS.append(limits)
