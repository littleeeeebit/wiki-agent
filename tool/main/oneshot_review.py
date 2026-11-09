"""oneshot S4's backend review: a read-only reading of the build, between agents and before any pull request.

`docs/plans/oneshot/3-spec-backend.md` ("Backend review") is the design.
The input is frozen per HEAD: the design and the requirements go into the turn's prompt, the diff from the
base commit is written where `pr_diff` reads it, and a copy is kept in the run's folder as the record.
A refusal goes to the build once; a second refusal stops the run with `review`. Nothing here pushes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from agent import read_tools

from . import oneshot, oneshot_build, oneshot_spec, runtime, specs, work
from .query import ROOT, _lock as work_lock, hold

REVIEW_PROMPT = (ROOT / "tool/prompts/oneshot-review.md").read_text(encoding="utf-8")
SEVERITIES = ("P0", "P1", "P2")
MAX_REFUSALS = 2


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
    how = ("read it with the `pr_diff` tool" if oneshot.codex_role("review") else
           f"read it from `{read_tools.diff_file(tree).resolve()}`; a line starting with ~ continues the line before it")
    return head, how


def review_text(spec: dict, head: str, how: str) -> str:
    o = spec["oneshot"]
    return (f"Review HEAD `{head}` of this repository's backend build.\n\nThe approved design:\n\n"
            f"{json.dumps(o['design'], ensure_ascii=False)}\n\nThe requirements:\n\n"
            f"{json.dumps(spec.get('requirements') or [], ensure_ascii=False)}\n\nThe environment profile:\n\n"
            f"{json.dumps(o['profile'], ensure_ascii=False)}\n\nThe pinned contract tests, unchanged since "
            f"`{o['tests_head']}`: {', '.join(o['contract'])}.\n\nThe whole diff from the base commit "
            f"`{spec['start_head']}` to HEAD: {how}.\n")


def verdict_of(value, head: str) -> dict:
    """An `oneshot-review` block, checked: a verdict on the HEAD read, findings that justify it."""

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
    serious = [f for f in findings if f["severity"] != "P2"]
    if value["verdict"] == "approve" and serious:
        raise ValueError("P0·P1 발견이 있는데 승인했다")
    if value["verdict"] == "refuse" and not serious:
        raise ValueError("거부에는 P0·P1 발견이 하나 이상 있어야 한다")
    return {"verdict": value["verdict"], "head": head,
            "findings": [{k: f[k] for k in ("location", "severity", "reason", "evidence")} for f in findings]}


def review(repo: str) -> None:
    """The review holds the worktree from freezing its input until its verdict is saved, so no other turn
    writes the tree it reads. A refusal releases it before the build turn takes it; then the instruction the
    person queued for this worktree meanwhile goes, as after any turn, behind that build turn."""

    store = oneshot_spec.store(repo)
    spec = oneshot_spec.spec_of(repo)
    tree = Path(spec["worktree"])
    try:
        release = hold(work._busy, work_lock, str(tree), "작업트리를 다른 턴이 쓰고 있다", kind="turn")
    except HTTPException as exc:   # whoever holds it sends the queued instruction when it lets go
        store.stop("block", f"{exc.detail} — 끝난 뒤 [재개] 로 백엔드 리뷰를 다시 한다")
        return
    try:
        following = decided(repo, store, spec, tree)
    finally:
        release()
    try:
        if following:
            following()
    finally:
        # A server going down drops it, as `dispatch` drops one after a stopped turn.
        work.dispatch(tree, SimpleNamespace(halt=runtime.stopping, turn=""))


def decided(repo: str, store, spec: dict, tree: Path):
    """The verdict applied; the refusal's build turn, if any, returned to be sent once the tree is free."""

    o = spec["oneshot"]
    found = verdict(repo, store, spec, tree)
    if found is None:
        return None
    reviews = [*(o.get("reviews") or []), {**found, "ts": time.time()}]
    if found["verdict"] == "approve":
        oneshot_spec.saved(repo, oneshot={"stage": "split", "reviews": reviews, "working": None})
        return None
    refused = int(o.get("refused") or 0) + 1
    if refused >= MAX_REFUSALS:
        store.put(reviews=reviews, refused=refused)
        store.stop("review", f"백엔드 리뷰가 {refused}번 거부했다 — " +
                   " / ".join(f"[{f['severity']}] {f['location']} {f['reason']}" for f in found["findings"][:5]))
        return None
    # `working` names the build before it is sent: a server that dies in between leaves it for `recover`.
    spec = oneshot_spec.saved(repo, oneshot={"stage": "build", "reviews": reviews, "refused": refused,
                                             "attempts": 0, "working": "build"})
    return lambda: oneshot_build.send(repo, "build", fixing(spec, found))


def verdict(repo: str, store, spec: dict, tree: Path) -> dict | None:
    """The checked verdict on the HEAD the build passed, or `None` after stopping. A tree that is not that
    HEAD, clean, before or after the turn sends the run back to the build's checks."""

    o = spec["oneshot"]

    def moved() -> str:
        head = specs.sh(["git", "rev-parse", "HEAD"], tree).stdout.strip()
        if head != o.get("built_head"):
            return f"HEAD `{head[:12]}` 가 빌드를 통과한 `{str(o.get('built_head'))[:12]}` 가 아니다"
        return "작업트리에 커밋하지 않은 변경이 있다" if specs.dirty(tree) else ""

    if why := moved():
        rebuild(repo, store, why)
        return None
    try:
        head, how = frozen(spec)
    except (ValueError, OSError, RuntimeError) as exc:
        store.stop("review", str(exc))
        return None
    found = oneshot.turned(store, "review", REVIEW_PROMPT, review_text(spec, head, how), "oneshot-review",
                           lambda value: verdict_of(value, head), tree)
    if found is not None and (why := moved()):
        rebuild(repo, store, f"리뷰하는 동안 작업트리가 바뀌었다 — {why}")
        return None
    return found


def rebuild(repo: str, store, why: str) -> None:
    """The tree is not what the build passed: `[재개]` sends the build turn, whose checks run again."""

    oneshot_spec.saved(repo, oneshot={"stage": "build", "attempts": 0})
    store.stop("gate", f"{why} — [재개] 로 빌드 검사부터 다시 한다")


def fixing(spec: dict, found: dict) -> str:
    serious = [f for f in found["findings"] if f["severity"] != "P2"]
    return (f"An independent read-only review refused HEAD `{found['head']}`. Fix every finding below "
            "where it points, without changing the pinned contract tests; a finding you disagree with gets "
            "evidence in your answer instead of a change.\n\n```json\n"
            f"{json.dumps(serious, ensure_ascii=False, indent=2)}\n```\n\n" + oneshot_build.build_text(spec))


def build_turn(spec: dict) -> str:
    """A resumed build's turn: the review's findings again while its refusal of the built HEAD stands."""

    o = spec["oneshot"]
    last = (o.get("reviews") or [None])[-1]
    if last and last["verdict"] == "refuse" and last["head"] == o.get("built_head"):
        return fixing(spec, last)
    return oneshot_build.build_text(spec)


def resume(repo: str) -> None:
    """`[재개]` in `review`: the refusals counted afresh, the review run again on the current HEAD."""

    oneshot_spec.saved(repo, oneshot={"refused": 0})
    begin(repo)
