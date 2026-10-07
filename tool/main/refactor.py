"""refactor — the `[리펙터링]` workflow (`docs/plans/refactor/`).

A run scans the selected repository for debt, freezes today's behaviour with
characterization tests in a pull request of their own, and then turns each step
into a stacked pull request whose patch `tool/refactor_profile.py` chose among
candidates. Every PR is an ordinary spec the review loop owns; the run is a
record under `raw/refactor/runs/<repo>/`.

The person's request authorizes Review Loop for the run's test PR and its L0–L1
step PRs (owner's decision, 2026-10-05); the next step starts once review allows
the previous one. Merge stays a person's. A restart stops a running run; resume
continues from the recorded step and never publishes twice.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from fastapi import HTTPException

import debt
import improvement
import refactor_profile
from agent import ChatSession
from common import errorlog, worktree_home
from common.budget import Budget, Cancelled

from . import loop, planning, refactor_continuation, refactor_finish, runtime, specs, work
from .query import ROOT, _lock, current_repo, hold
from .refactor_continuation import recover as recover
from .refactor_finish import body

RUNS = ROOT / "raw" / "refactor" / "runs"
MODES = {"cleanup": {"tiers": ("L0", "L1")}, "restructure": {"tiers": ("L0", "L1", "L2")},
         "full": {"tiers": ("L0", "L1", "L2", "L3")}}
AUTO_REVIEW = ("L0", "L1")    # tiers whose PRs the request itself sends to review
BLOCKING = ("L2", "L3")       # tiers that hold the repository while they run
REVIEWED = ("머지 가능", "머지 대기", "머지됨")
CLOSED = ("정리됨", "머지됨")  # a proposal not yet started, or a finished task
REQUEST = re.compile(r"[A-Za-z0-9-]{8,64}")
AUDIT_PROMPT = (
    "Audit the listed module for a structure-only refactoring. Read its callers, the state it owns and the seams "
    "it could split along. Do not edit anything. Propose one to three steps, smallest first, each with a tier "
    "from {tiers}: L0 mechanical, L1 inside the listed files with public names unchanged, L2 code may move "
    "between modules with every public import path still working. A step lists the repository-relative files "
    "it touches; an L2 step may name a new file under an existing directory. End with a fenced block tagged "
    '`refactor-plan` holding JSON: {{"steps": [{{"tier": "L1", "goal": "...", "files": ["..."]}}]}}.')
FULL_PROMPT = (
    "Audit this whole repository for structural debt. Start from the debt scan you are given, then read the "
    "architecture documents and `.omm/` when present. Do not edit anything. Answer in prose: the target "
    "structure, which seams to cut, and the order to get there in, smallest safe change first, with the tier "
    "each change needs (L0 mechanical, L1 inside files, L2 across modules, L3 a contract change).")
PLAN_RULES = (
    "This plan is a refactoring series. Every stage file must carry two plain lines: `Tier: L0`, `L1`, `L2` "
    "or `L3`, and `Files: ` with the repository-relative files it touches, comma-separated. An L3 stage also "
    "needs a `## Migration` section beside its `## Rollback`. Behaviour must not change below L3. The "
    "overview's `## Stages` section names every stage file by its file name, and only those: it is the list "
    "the run executes.")
TESTS_PROMPT = (
    "Write characterization tests that pin the current observable behaviour of the files listed, so a later "
    "refactoring can prove it changed nothing. Use the repository's existing test framework and conventions; "
    "the tests must pass on the code as it is. Create or edit test files only. Do not commit, push, delegate "
    "or ask questions. End with a fenced block tagged `refactor-tests` holding JSON: "
    '{"tests": [repository-relative test paths], "test_argv": [the command that runs exactly those tests '
    "from the repository root, as an argument list]}.")

_files = threading.RLock()   # reads too: on Windows a read racing `atomic`'s replace fails
_workers: dict[tuple[str, str], "Worker"] = {}
_launching = threading.Lock()   # a workerless cancel's release never interleaves with a resume


class Stop(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def fenced(tag: str, text: str):
    """The JSON in the last block tagged `tag`."""

    found = re.findall(rf"^```{tag}[ \t]*\r?\n(.*?)^```", text, re.M | re.S)
    try:
        return json.loads(found[-1])
    except (IndexError, ValueError) as exc:
        raise Stop("format", f"`{tag}` 블록을 읽지 못했다 — {exc}") from exc


# -- The record ----------------------------------------------------------------

def file_of(repo: str, rid: str) -> Path:
    return RUNS / repo / f"{rid}.json"


def load(repo: str, rid: str) -> dict | None:
    try:
        with _files:
            return json.loads(file_of(repo, rid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def update(repo: str, rid: str, **fields) -> dict:
    with _files:
        run = load(repo, rid)
        run.update(fields, updated=time.time())
        improvement.atomic(file_of(repo, rid), run)
        return run


def listing(repo: str) -> list[dict]:
    found = (load(repo, f.stem) for f in sorted((RUNS / repo).glob("*.json")))
    return sorted((r for r in found if r), key=lambda r: -r["created"])


def scope_of(repo: Path) -> str:
    _, key = improvement.identity(repo)
    return "hub" if key == improvement.identity(improvement.HUB)[1] else "project"


def hotspots(repo: Path, top: int | None = None) -> list[dict]:
    """Scanned files with debt by `refactor_profile`'s measure, worst first.
    Test files are left out: they are what freezes behaviour."""

    file = repo / debt.RATCHET
    caps = {**(debt.load(file) if file.exists() else debt.CAPS), "files": {}}
    rows = []
    for r in debt.scan(repo):
        cap = debt.limit(r["path"], caps)[0]
        owed = max(0, r["lines"] - cap) + r["dup"] + max(0, r["block"] - refactor_profile.BLOCK_MAX)
        if owed and not debt.TEST.search(r["path"]):
            rows.append({**r, "cap": cap, "debt": owed})
    return rows[:top] if top else rows


# -- The worker ----------------------------------------------------------------

class Worker:
    """One run on its own thread. Waiting for review is not charged to its time."""

    def __init__(self, repo: Path, run: dict) -> None:
        self.repo, self.rid, self.hub = repo, run["id"], run["scope"] == "hub"
        self.halt = threading.Event()
        self.base = dict(run["spent"])
        self.budget = Budget(seconds=None, calls=None, candidates=0, cancel=self.halt)
        self.unknown = self.base.get("unknown", False)
        self.started = time.monotonic()
        self.active: work.Run | None = None

    def cancel(self) -> None:
        self.halt.set()
        if self.active is not None:
            self.active.halt.set()
            self.active.chat.stop(self.active.halt)
        run = load(self.repo.name, self.rid)
        if run and run.get("plan") and run["phase"] == "plan":
            planner = planning._workers.get((self.repo.name, run["plan"]))
            if planner is not None:
                planner.cancel()

    def spent(self) -> dict:
        active = time.monotonic() - self.started - self.budget.aside_ms / 1000
        return {"seconds": round(self.base["seconds"] + active, 1),
                "calls": self.base["calls"] + self.budget.used["calls"],
                "tokens": self.base["tokens"] + self.budget.used["tokens"], "unknown": self.unknown}

    def note(self, **fields) -> dict:
        return update(self.repo.name, self.rid, spent=self.spent(), **fields)


def place(w: Worker, sid: str) -> Path:
    """Where `sid` is worked: the selected checkout, or the hub step's own linked worktree."""

    return worktree_home(w.repo) / sid if w.hub else w.repo


@contextmanager
def owning(path: Path):
    """The checkout is the run's from its first switch to its PR, so no other
    turn writes between them. Review waits outside: its loop needs the checkout."""

    try:
        release = hold(work._busy, _lock, str(path), "그 저장소를 다른 요청이 쓰고 있다", kind="turn")
    except HTTPException as exc:
        raise Stop("busy", exc.detail) from exc
    try:
        yield
    finally:
        release()


def git(repo: Path, *args: str) -> str:
    done = specs.sh(["git", *args], repo, 120)
    if done.returncode:
        raise Stop("git", specs.said(done))
    return done.stdout.strip()


def idle(w: Worker) -> None:
    """Before any fork or switch: no loop, planner or other run's L2–L3 block owns the checkout."""

    try:
        specs.checkout_idle(w.repo, run=w.rid)
    except HTTPException as exc:
        raise Stop("busy", exc.detail) from exc


def switched(w: Worker, sid: str, start: str) -> Path:
    """The checkout on `sid`, made from `start` the first time: the selected
    checkout for a project; for the hub a linked worktree of the step's own, so
    the running server's files never change under it. Merge cleanup removes it
    as it does any linked task's."""

    idle(w)
    if w.hub:
        path = place(w, sid)
        if not path.exists():
            exists = not specs.sh(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{sid}"], w.repo).returncode
            git(w.repo, "worktree", "add", *((str(path), sid) if exists else ("-b", sid, str(path), start)))
        common = lambda p: Path(git(p, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()  # noqa: E731
        if git(path, "branch", "--show-current") != sid or common(path) != common(w.repo):   # kept, not reused
            raise Stop("worktree", f"`{path}` 이 이 저장소의 `{sid}` 작업 트리가 아니다 — 확인하고 치운 뒤 [재개] 하라")
        return path
    if git(w.repo, "branch", "--show-current") != sid:
        if git(w.repo, "status", "--porcelain"):
            raise Stop("dirty", "저장소에 커밋 안 된 변경이 있다")
        exists = not specs.sh(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{sid}"], w.repo).returncode
        git(w.repo, "switch", *((sid,) if exists else ("-c", sid, start)))
    return w.repo


def tested(w: Worker, argv: list[str], where: Path) -> subprocess.CompletedProcess:
    """The characterization command, cancelled with its whole process tree."""

    done = refactor_profile.sh(argv, where, halt=w.halt)
    if done.returncode == refactor_profile.CUT:
        raise Stop("cancelled", "특성 테스트 명령을 끊었다")
    return done


def turn(w: Worker, run: dict, text: str, system: str = TESTS_PROMPT, write: bool = True,
         where: Path | None = None) -> str:
    """One native turn in the checkout `where`, which the caller owns, shown in
    the agent pane like any work turn; usage is recorded without a ceiling.
    A read-only turn gets read tools only — no shell — and must
    leave HEAD and the working tree as it found them."""

    where = where or w.repo
    try:
        w.budget.call()
    except Cancelled as exc:
        raise Stop("cancelled", str(exc)) from exc
    role = run["role"]
    state = lambda: (git(where, "rev-parse", "HEAD"), git(where, "status", "--porcelain"))  # noqa: E731
    before = None if write else state()
    if w.halt.is_set():
        raise Stop("cancelled")
    chat = ChatSession(where, write=write, bypass=write, system=system, model=role["model"] or None,
                       effort=role["effort"] or None, **({} if write else {"tools": "Read,Glob,Grep"}))
    active = work.Run(chat)
    w.active = active
    if w.halt.is_set():
        active.halt.set()
    with _lock:
        work._runs[str(where)] = active
    final, failed, tokens = "", "", {}
    try:
        work.remember(where, "user", text)
        work.feed.put({"kind": "started", "path": str(where), "turn": active.turn})
        for ev in chat.say(text, active.halt):
            if w.halt.is_set():
                active.halt.set()
                chat.stop(active.halt)
            active.put({"kind": ev.kind, "text": ev.text, "meta": ev.meta, "session_id": chat.id, "parent_id": None})
            if ev.kind == "done":
                final, tokens = ev.text, ev.meta.get("tokens") or {}
            if ev.kind == "error" or ev.meta.get("error"):
                failed = ev.text or "호스트 오류"
    finally:
        chat.close()
        work.remember(where, "assistant", final, error=failed, steps=work.steps(active.events), turn=active.turn,
                      cell=chat.id, started_at=active.started_at)
        active.finish()
        w.active = None
        work.feed.put({"kind": "work-record", "path": str(where)})
    if type(tokens.get("in")) is not int or type(tokens.get("out")) is not int:
        w.unknown = True
    else:
        w.budget.charge({"input_tokens": tokens["in"], "output_tokens": tokens["out"]})
    if failed or active.halt.is_set():
        raise Stop("cancelled" if w.halt.is_set() else "host", failed or "턴이 끊겼다")
    if before is not None and state() != before:
        raise Stop("read_only_wrote", "읽기 전용 턴이 저장소를 바꿨다 — 그 상태 위에서는 이어가지 않는다")
    if re.search(r"^```refactor-scope[ \t]*$", final, re.M):
        change = fenced("refactor-scope", final)
        try:
            specs._shape("choices", change)
        except ValueError as exc:
            raise Stop("format", str(exc)) from exc
        w.note(scope_change=change)
        raise Stop("scope", "목적이나 모드를 바꿔야 한다 — 대화에서 선택하고 명세를 다시 정해라")
    return final


def others(repo: Path, rid: str) -> list[str]:
    """Open tasks in `repo` that are not this run's."""

    return [s["id"] for s in specs.listing(repo.name)
            if s["state"] not in CLOSED and (s.get("refactor") or {}).get("run") != rid]


def blocked(w: Worker, sid: str, on: bool) -> None:
    """Hold or release the repository for an L2–L3 step. The hold lives on the
    step's spec, so `specs.checkout_idle` refuses new tasks and a restart keeps it."""

    # A hub step holds only its own tree, so it holds the selected checkout while it looks and holds:
    # a task start checks and claims that checkout in one step, so it lands before the look or after the hold.
    with owning(w.repo) if on and w.hub else nullcontext():
        if on and (busy := others(w.repo, w.rid)):
            raise Stop("open_tasks", f"열린 작업이 있어 L2 이상 단계를 시작하지 않는다: {', '.join(busy)}")
        with specs._files:
            spec = specs.load(w.repo.name, sid)
            if spec is not None and spec["refactor"].get("block") is not on:
                specs.save({**spec, "refactor": {**spec["refactor"], "block": on}})


def released(repo: Path, rid: str) -> None:
    for spec in specs.listing(repo.name):
        if (spec.get("refactor") or {}).get("run") == rid and spec["refactor"].get("block"):
            with specs._files:
                fresh = specs.load(repo.name, spec["id"])
                specs.save({**fresh, "refactor": {**fresh["refactor"], "block": False}})


def spec_for(w: Worker, run: dict, sid: str, goal: str, base: str, start: str, where: Path) -> dict:
    gate = specs.gate_of(w.repo)
    now = time.time()
    spec = {"id": sid, "repo": w.repo.name, "rev": 1, "goal": goal, "out": run.get("out", []), "done": [gate],
            "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [],
            "review_profile": "code", "review_profile_version": specs.PROFILE_VERSION, "artifact_root": None,
            "source": {"focus": "refactor", "turn": now, "plan": None}, "state": "작업 중", "stopped": None,
            # A hub step's linked worktree has no mode, as a linked task's never had.
            "worktree": str(where), **({} if w.hub else {"workspace_mode": "branch"}), "branch": sid, "start_head": start,
            "return_branch": base, "pr": None, "report": None, "gate": None, "fault": None,
            "cell": run["role"], **({"reviewer": run["reviewer"]} if run.get("reviewer") else {}),
            "refactor": {"run": run["id"]}, "history": [{"ts": now, "state": "작업 중"}]}
    with specs._files:
        if specs.load(w.repo.name, sid) is None:
            specs.save(spec)
    return specs.load(w.repo.name, sid)


def published(w: Worker, sid: str, where: Path, base: str, title: str, body: str, tier: str) -> int:
    """Push and open (or find) the PR from `sid` into `base`; send it to review
    when the request covers its tier."""

    if w.halt.is_set():
        raise Stop("cancelled")
    git(where, "push", "-u", "origin", sid)
    try:
        n, url = specs.pull_request(where, sid, base, title, body)
    except RuntimeError as exc:
        raise Stop("publish_failed", str(exc)) from exc
    head = git(where, "rev-parse", "HEAD")
    with specs._files:
        spec = specs.load(w.repo.name, sid)
        if not (spec.get("pr") or {}).get("number"):
            specs.save(specs.moved(spec, f"PR #{n}", pr={"number": n, "url": url, "base": base, "head": head,
                                                         "branch": sid}))
    if tier in AUTO_REVIEW:
        loop.kick(w.repo.name, sid)
    return n


def recovered(repo: Path, sid: str, tier: str) -> int | None:
    """The PR `published` saved on the spec before the run's own checkpoint.
    For a tier the request reviews, the review request a stop may have cut off
    after it: taken only while the spec is still where `published` left it, and
    started as an automatic kick, which a person's stop in between still wins."""

    with specs._files:
        spec = specs.load(repo.name, sid)
        n = ((spec or {}).get("pr") or {}).get("number")
        due = bool(n) and tier in AUTO_REVIEW and spec["state"] == f"PR #{n}" and not spec.get("rounds")
        if due:
            specs.save(specs.moved(spec, "리뷰 대기"))
    if due:
        loop.kick(repo.name, sid, automatic=True)
    return n


def waited(w: Worker, ready) -> None:
    """Wait until `ready()`; the time is set aside from the run's."""

    started = time.monotonic()
    try:
        while not w.halt.wait(3):
            if ready():
                return
        raise Stop("cancelled")
    finally:
        w.budget.aside(time.monotonic() - started)


def live(w: Worker, sid: str) -> dict:
    spec = specs.load(w.repo.name, sid)
    if spec is None:
        raise Stop("spec_gone", f"`{sid}` 명세가 없어졌다")
    if spec["state"] == "멈춤":
        raise Stop("review_stopped", (spec.get("stopped") or {}).get("reason") or "리뷰가 멈췄다")
    return spec


def reviewed(w: Worker, sid: str) -> None:
    """Wait until review allows `sid`."""

    waited(w, lambda: live(w, sid)["state"] in REVIEWED)
    refactor_finish.reviewed(w, sid)


def head_of(repo: Path, spec: dict) -> str:
    """The spec's branch head. Once merge cleanup has pruned a merged branch,
    the allowed round's head, but only when GitHub says the merged PR delivered
    exactly that head; a merge adopted from GitHub is not bound to it otherwise."""

    head = specs.sh(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{spec['id']}"], repo).stdout.strip()
    allowed, n = specs.approved(spec), (spec.get("pr") or {}).get("number")
    if head or spec["state"] != "머지됨" or not allowed or not n:
        return head
    try:
        view = loop.gh_json(repo, ["pr", "view", str(n), "--json", "state,headRefOid"])
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        return ""   # unread is unproven; the approval wait reads it again
    return allowed["head"] if view.get("state") == "MERGED" and view.get("headRefOid") == allowed["head"] else ""


def mark(repo: Path, spec: dict) -> dict | None:
    """What an approval is given for: the spec's revision and its branch head,
    only while the counted review round allowed exactly that head."""

    allowed = specs.approved(spec)
    head = head_of(repo, spec)
    if spec["state"] not in REVIEWED or not allowed or not head or allowed["head"] != head:
        return None
    return {"rev": spec["rev"], "head": head}


def approved(w: Worker, sid: str) -> bool:
    """The person approved `sid` as it stands now, and its review still allows it:
    a revision or a new commit since the approval needs review and approval again."""

    spec = live(w, sid)
    given = (load(w.repo.name, w.rid).get("approved") or {}).get(sid)
    return given is not None and given == mark(w.repo, spec)


def purpose(run: dict) -> str:
    """The settled request every audit and candidate must honor."""
    return "\n\n".join([f"Agreed goal: {run.get('goal', '')}",
        "Completion conditions:\n" + "\n".join(f"- {s}" for s in run.get("done", [])),
        "Out of scope:\n" + "\n".join(f"- {s}" for s in run.get("out", [])),
        "Discover and adjust files needed for this purpose. Do not expand the goal or the permitted mode. "
        "If the goal or mode must change, return a refactor-scope block with a reason and choices, then stop."])


# -- The phases ----------------------------------------------------------------

def scan(w: Worker, run: dict) -> dict:
    if run["mode"] == "full":
        return w.note(phase="audit", hotspots=hotspots(w.repo, 20))
    if run["mode"] != "cleanup":   # the person chose the module; its measurements are shown, not filtered
        return w.note(phase="audit", hotspots=[r for r in debt.scan(w.repo) if r["path"] in run["files"]])
    rows = hotspots(w.repo) if not run["files"] else \
        [r for r in hotspots(w.repo) if r["path"] in run["files"]]
    if run.get("goal"):
        return w.note(phase="audit", hotspots=rows)
    if not rows:
        raise Stop("nothing", "줄일 부채가 없다")
    steps = [{"n": k + 1, "tier": "L1", "files": [r["path"]], "state": "pending", "spec": None,
              "goal": f"Lower the debt of {r['path']}: {r['lines']} lines (cap {r['cap']}), {r['dup']} duplicated "
                      f"lines, longest block {r['block']}. {MODES[run['mode']]['tiers'][-1]} changes only."}
             for k, r in enumerate(rows)]
    return w.note(phase="tests", hotspots=rows, steps=steps)


def audited(w: Worker, run: dict) -> dict:
    """A read-only audit of the chosen module into one to three steps."""

    if run["mode"] == "full":
        return charted(w, run)
    tiers = MODES[run["mode"]]["tiers"]
    with owning(w.repo):
        final = turn(w, run, purpose(run) + "\n\nInitial files:\n" + "\n".join(f"- {f}" for f in run["files"])
                     + "\n\nScan:\n" + json.dumps(run["hotspots"], ensure_ascii=False),
                     AUDIT_PROMPT.format(tiers=", ".join(tiers)), write=False)
    plan = fenced("refactor-plan", final)
    try:
        given = plan["steps"]
        assert isinstance(given, list) and 1 <= len(given) <= 3, "1–3 steps"
        steps = []
        for k, s in enumerate(given):
            assert s["tier"] in tiers, f"tier {s['tier']!r} is outside {tiers}"
            assert isinstance(s["goal"], str) and s["goal"].strip(), "a step needs a goal"
            assert isinstance(s["files"], list) and s["files"], "a step needs files"
            for rel in s["files"]:
                improvement.relative(rel)
            steps.append({"n": k + 1, "tier": s["tier"], "files": s["files"], "state": "pending", "spec": None,
                          "goal": s["goal"].strip() + "\n\n" + purpose(run)})
    except (KeyError, TypeError, AssertionError, improvement.Refused) as exc:
        raise Stop("format", f"refactor-plan 이 맞지 않다 — {exc}") from exc
    return w.note(phase="tests", steps=steps)


def charted(w: Worker, run: dict) -> dict:
    """The whole-repository audit, handed to the planner as a refactor series."""

    if not run.get("audit"):
        table = "\n".join(f"- {r['path']}: {r['lines']} lines (cap {r['cap']}), {r['dup']} duplicated, longest "
                          f"block {r['block']}, {r['churn']} commits in 180 days" for r in run["hotspots"])
        with owning(w.repo):
            final = turn(w, run, purpose(run) + "\n\nDebt scan, worst first:\n" + (table or "(nothing over the caps)"), FULL_PROMPT,
                         write=False)
        run = w.note(audit=final[-(planning.MAX_CONTEXT - len(PLAN_RULES) - 100):])
    if current_repo().resolve() != w.repo.resolve():
        raise Stop("moved", "다른 저장소가 선택됐다 — 이 저장소로 돌아와 [재개] 하라")
    found = None
    if run.get("planner"):   # reuse the saved planning request
        try:
            found = planning.begun(planning.Plan(**run["planner"]), create=False)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise Stop("plan_refused", str(exc.detail)) from exc
    if found is None:   # create the request only if none carries its key
        role = planning.Role(**run["role"])
        run = w.note(planner=planning.Plan(
            request_id=f"refactor-{w.rid}-plan", refactor=True,
            goal=run.get("goal") or "Pay down the structural debt the audit found, in stages that each keep behaviour unless their "
                 "tier is L3, so every stage can become one reviewed refactoring pull request.",
            context=f"{PLAN_RULES}\n\n{purpose(run)}\n\n## Audit\n\n{run['audit']}"[:planning.MAX_CONTEXT],
            roles=planning.Roles(planner=role, reviser=role,
                                 reviewer=planning.Role(**(run["reviewer"] or run["role"]))),
            limits=None).model_dump())
        try:
            found = planning.begun(planning.Plan(**run["planner"]))
        except HTTPException as exc:
            raise Stop("plan_refused", str(exc.detail)) from exc
    return w.note(phase="plan", plan=found["id"])


def staged(w: Worker, run: dict) -> dict:
    """After the plan PR merges and the checkout is back on its base, its
    stages become the run's steps."""

    def ready() -> bool:
        spec = specs.load(w.repo.name, run["plan"])
        if spec is None:
            raise Stop("spec_gone", f"계획 `{run['plan']}` 이 없어졌다")
        if spec["state"] == "멈춤" or spec["planning"]["phase"] == "stopped":
            raise Stop("plan_stopped", f"계획 `{run['plan']}` 이 멈췄다 — 계획을 이은 뒤 이 리펙터링을 [재개] 하라")
        return spec["state"] == "머지됨" and bool(spec.get("cleanup_complete"))

    waited(w, ready)
    spec = specs.load(w.repo.name, run["plan"])
    p, commit = spec["planning"], spec["merge"]["commit"]
    # The stage files that merged, not the outline written before review: a revision may add or drop one.
    names = git(w.repo, "ls-tree", "--name-only", f"{commit}:{p['artifact_root']}").splitlines()
    stages = sorted((int(name.split("-")[0]), name) for name in names
                    if planning.NAME.fullmatch(name) and name != "0-overview.md")
    if not stages or len({n for n, _ in stages}) != len(stages):
        raise Stop("format", f"머지된 계획의 단계 파일을 읽지 못했다: {', '.join(names)}")
    # The overview's `## Stages` is the manifest: a numbered file it leaves out, or one it names that is
    # missing, is a plan that disagrees with itself, never a step run or skipped silently.
    if {name for _, name in stages} != (manifest := planning.listed(
            git(w.repo, "show", f"{commit}:{p['artifact_root']}/0-overview.md"))):
        raise Stop("format", f"머지된 개요의 `## Stages` 가 단계 파일과 다르다: 개요 {sorted(manifest)}, "
                             f"파일 {[name for _, name in stages]}")
    steps = []
    for n, name in stages:
        rel = f"{p['artifact_root']}/{name}"
        text = git(w.repo, "show", f"{commit}:{rel}")
        try:
            t = planning.tiered(text)
        except ValueError as exc:
            raise Stop("format", f"{rel}: {exc}") from exc
        title = (re.search(r"^#[ \t]+(.+)$", text, re.M) or [None, name])[1]
        steps.append({"n": n, "tier": t["tier"], "files": t["files"], "state": "pending", "spec": None,
                      "goal": f"{title}, as `{rel}` describes:\n\n{text[:6000]}"})
    # Record planner usage when leaving this phase; its time was set aside while waiting.
    used, spent = p["spent"], w.spent()
    run = update(w.repo.name, w.rid, phase="tests", steps=steps,
                 spent={k: spent[k] + used[k] for k in ("seconds", "calls", "tokens")})
    w.budget.used["calls"] += used["calls"]
    w.budget.used["tokens"] += used["tokens"]
    w.unknown = w.unknown or used.get("unknown", False)
    w.budget.aside(-used["seconds"])
    return run


def frozen(w: Worker, run: dict) -> dict:
    """The characterization PR every step stacks on."""

    sid = (run.get("tests") or {}).get("spec") or specs.unique(w.repo, f"refactor-{w.rid}-tests")
    with owning(place(w, sid)):
        characterized(w, run, sid)
    reviewed(w, sid)
    return w.note(phase="steps")


def characterized(w: Worker, run: dict, sid: str) -> None:
    t = dict(run.get("tests") or {})
    files = sorted({*run["files"], *(f for s in run["steps"] for f in s["files"] if (w.repo / f).is_file())})
    if not t.get("spec"):
        idle(w)
        if w.hub:   # from the branch the server runs, without switching it
            t = {"spec": sid, "base": git(w.repo, "branch", "--show-current"), "start": git(w.repo, "rev-parse", "HEAD")}
        else:
            try:
                _, start, base = specs.fork(w.repo, sid)
            except (ValueError, RuntimeError) as exc:
                raise Stop("busy", str(exc)) from exc
            t = {"spec": git(w.repo, "branch", "--show-current"), "base": base, "start": start}
        run = w.note(tests=t)
    sid = t["spec"]
    if not t.get("pr") and (n := recovered(w.repo, sid, "L0")):
        t["pr"] = n
        run = w.note(tests=t)
    where = place(w, sid)
    if not t.get("pr"):   # a published test branch is its PR's, never remade from before the tests
        where = switched(w, sid, t["start"])
    if not t.get("tests"):
        spec_for(w, run, sid, f"Characterization tests for {', '.join(files)}", t["base"], t["start"], where)
        final = turn(w, run, "Files:\n" + "\n".join(f"- {f}" for f in files), where=where)
        given = fenced("refactor-tests", final)
        try:
            tests, argv = given["tests"], given["test_argv"]
            for rel in tests:
                improvement.relative(rel)
            assert tests and argv and all(isinstance(a, str) and a for a in argv)
        except (KeyError, TypeError, AssertionError, improvement.Refused) as exc:
            raise Stop("format", f"refactor-tests 블록을 읽지 못했다 — {exc}") from exc
        # The host names its tests, so a name alone proves nothing: a test is a
        # test-file path (`Tests/` too, as Swift lays them out), never one of
        # the files being refactored.
        code = sorted(rel for rel in tests if rel in files or not debt.TEST.search(rel.lower()))
        changed = {line[3:].strip('"') for line in specs.sh(["git", "status", "--porcelain", "-uall"], where)
                   .stdout.splitlines()}
        if code or not changed or not changed <= set(tests):
            raise Stop("tests_touched_code", f"테스트 밖을 바꿨다: {sorted({*code, *(changed - set(tests))})}")
        done = tested(w, argv, where)
        if done.returncode:
            raise Stop("tests_fail", (done.stdout + done.stderr)[-2000:])
        git(where, "add", "--", *tests)
        git(where, "commit", "-m", f"Add characterization tests for {', '.join(files)}")
        t.update(tests=tests, test_argv=argv)
        run = w.note(tests=t)
    if not t.get("pr"):
        t["pr"] = published(w, sid, where, t["base"], f"Characterization tests for {', '.join(files)}",
                            body("Pin today's behaviour before refactoring.", [f"`{f}`" for f in t["tests"]]), "L0")
        w.note(tests=t)


def stepped(w: Worker, run: dict) -> dict:
    if (steps := refactor_finish.schedule(run)) != run["steps"]:
        run = w.note(steps=steps)
    previous = run["tests"]["spec"]
    for k, step in enumerate(run["steps"]):
        if step["state"] == "done":
            previous = step["spec"] or step["base"]
            continue
        sid = step["spec"] or specs.unique(w.repo, f"refactor-{w.rid}-{step['n']}")
        if not step["spec"]:
            run["steps"][k] = step = {**step, "spec": sid, "base": previous}
            run = w.note(steps=run["steps"])
        if step["state"] == "adopted" and (n := recovered(w.repo, sid, step["tier"])):
            run["steps"][k] = step = {**step, "state": "published", "pr": n}
            run = w.note(steps=run["steps"])
        where = place(w, sid)
        with owning(where):
            if step["state"] in ("pending", "adopted"):   # a published step's branch is its PR's, never remade
                below = specs.load(w.repo.name, previous)
                where = switched(w, sid, (below and head_of(w.repo, below)) or previous)
            if step["state"] == "pending":
                spec_for(w, run, sid, step["goal"], previous, git(where, "rev-parse", "HEAD"), where)
            if step["tier"] in BLOCKING:
                blocked(w, sid, True)
            if step["state"] == "pending":
                if step.get("kind"):
                    w.note(phase=step["kind"])
                adopted = competed(w, run, step, sid, where)
                run = load(w.repo.name, w.rid)
                step = run["steps"][k]
                if adopted.get("unchanged"):
                    run = refactor_finish.unchanged(w, run, k, sid)
                    continue
                git(where, "merge", "--ff-only", adopted["commit"])
                run["steps"][k] = step = {**step, "state": "adopted", "handoff": adopted.get("branch")}
                run = w.note(steps=run["steps"])
            if step["state"] == "adopted":
                if step.get("handoff"):   # only after the checkpoint: until then a resume needs it to re-adopt
                    specs.sh(["git", "branch", "-D", step["handoff"]], where)
                n = published(w, sid, where, previous, step["goal"], refactor_finish.summary(step), step["tier"])
                run["steps"][k] = step = {**step, "state": "published", "pr": n}
                run = w.note(steps=run["steps"])
        reviewed(w, sid)
        if step["tier"] not in AUTO_REVIEW:   # the person approves before the next step starts
            if step["state"] != "awaiting":
                run["steps"][k] = step = {**step, "state": "awaiting"}
                run = w.note(steps=run["steps"])
            waited(w, lambda: approved(w, sid))
            blocked(w, sid, False)
        run["steps"][k] = {**step, "state": "done"}
        run = w.note(steps=run["steps"])
        previous = sid
    return w.note(phase="done", state="done")


def competed(w: Worker, run: dict, step: dict, sid: str, where: Path) -> dict:
    """Run the step from `where` and record its usage."""

    if step.get("kind"):
        return refactor_finish.candidate(w, run, step, where)
    config = refactor_profile.STORE / run["scope"] / sid / "experiment.json"
    try:
        if not config.exists():
            config = refactor_profile.prepare(
                where, run["scope"], sid, {"goal": step["goal"], "tier": step["tier"], "files": step["files"],
                                           "tests": run["tests"]["tests"], "test_argv": run["tests"]["test_argv"]},
                run["role"], halt=w.halt, gate=specs.gate_of(w.repo))
        result = refactor_profile.drive(where, run["scope"], sid, config, halt=w.halt)
    except improvement.Refused as exc:
        try:
            state = improvement.Experiment(where, run["scope"], sid).read()
        except improvement.Refused:
            state = {}
        if change := state.get("scope_change"):
            try:
                specs._shape("choices", change)
            except ValueError as invalid:
                raise Stop("format", str(invalid)) from invalid
            w.note(scope_change=change)
            raise Stop("scope", "목적이나 모드를 바꿔야 한다 — 대화에서 선택하고 명세를 다시 정해라") from exc
        raise Stop("cancelled" if w.halt.is_set() else "runner", str(exc)) from exc
    finally:
        try:
            spent = improvement.Experiment(where, run["scope"], sid).read()["spent"]
            previous = (load(w.repo.name, w.rid).get("runner_spent") or {}).get(sid, {})
            for key in ("calls", "tokens"):
                w.budget.used[key] += max(0, spent[key] - previous.get(key, 0))
            w.unknown = w.unknown or spent.get("unknown", False)
            w.note(runner_spent={**(load(w.repo.name, w.rid).get("runner_spent") or {}), sid: spent})
        except improvement.Refused:
            pass
    if result["state"] == "split":
        raise Stop("split", "후보가 모두 실패했다 — 이 단계를 더 작게 나눠라: " + "; ".join(result["reasons"])[-1500:])
    return result


PHASES = {"scan": scan, "audit": audited, "plan": staged, "tests": frozen, "steps": stepped,
          "test_cleanup": stepped, "ratchet": stepped}


def drive(w: Worker) -> None:
    try:
        while (run := load(w.repo.name, w.rid))["phase"] in PHASES:
            if w.halt.is_set() or runtime.stopping.is_set():
                raise Stop("cancelled")
            PHASES[run["phase"]](w, run)
    except Stop as stop:
        if not runtime.stopping.is_set():   # a shutdown keeps the hold for the restart, as `recover` does
            released(w.repo, w.rid)
        w.note(state="stopped", stopped={"reason": stop.reason, "detail": stop.detail, "ts": time.time()})
    except Exception as exc:  # a broken worker still owes the run a reason
        errorlog.record("refactor", exc, repo=w.repo.name, run=w.rid)
        released(w.repo, w.rid)
        w.note(state="stopped", stopped={"reason": "broken", "detail": f"{type(exc).__name__}: {exc}",
                                         "ts": time.time()})
    finally:
        with _lock:
            _workers.pop((w.repo.name, w.rid), None)


def launch(repo: Path, run: dict) -> None:
    with _launching, _lock:
        if runtime.stopping.is_set():
            raise HTTPException(503, "서버가 종료 중이다")
        refactor_continuation.guard(load(repo.name, run["id"]))
        if (repo.name, run["id"]) in _workers:
            raise HTTPException(409, "이 리펙터링은 이미 돌고 있다")
        w = _workers[(repo.name, run["id"])] = Worker(repo, run)
    update(repo.name, run["id"], state="running", stopped=None)
    threading.Thread(target=drive, args=(w,), daemon=True).start()


def close_all() -> None:
    with _lock:
        workers = list(_workers.values())
    for w in workers:
        w.cancel()
