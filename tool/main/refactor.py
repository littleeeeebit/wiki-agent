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
import secrets
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import debt
import improvement
import refactor_profile
from agent import ChatSession
from common import errorlog
from common.budget import Budget, Cancelled, Exhausted

from . import loop, runtime, specs, work
from .query import ROOT, _lock, current_repo, hold

RUNS = ROOT / "raw" / "refactor" / "runs"
MODES = {"cleanup": {"tiers": ("L0", "L1"), "limits": {"seconds": 1800, "calls": 24, "tokens": 500_000}},
         "restructure": {"tiers": ("L0", "L1", "L2"), "limits": {"seconds": 3600, "calls": 40, "tokens": 1_000_000}}}
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
TESTS_PROMPT = (
    "Write characterization tests that pin the current observable behaviour of the files listed, so a later "
    "refactoring can prove it changed nothing. Use the repository's existing test framework and conventions; "
    "the tests must pass on the code as it is. Create or edit test files only. Do not commit, push, delegate "
    "or ask questions. End with a fenced block tagged `refactor-tests` holding JSON: "
    '{"tests": [repository-relative test paths], "test_argv": [the command that runs exactly those tests '
    "from the repository root, as an argument list]}.")

router = APIRouter()
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
        self.repo, self.rid = repo, run["id"]
        self.halt = threading.Event()
        self.base = dict(run["spent"])
        left = {k: run["limits"][k] - self.base[k] for k in ("seconds", "calls", "tokens")}
        self.budget = Budget(seconds=max(0.0, left["seconds"]), calls=max(0, left["calls"]), candidates=0,
                             tokens=max(0, left["tokens"]), cancel=self.halt)
        self.started = time.monotonic()

    def spent(self) -> dict:
        active = time.monotonic() - self.started - self.budget.aside_ms / 1000
        return {"seconds": round(self.base["seconds"] + active, 1),
                "calls": self.base["calls"] + self.budget.used["calls"],
                "tokens": self.base["tokens"] + self.budget.used["tokens"]}

    def left(self) -> dict:
        run = load(self.repo.name, self.rid)
        spent = self.spent()
        return {"seconds": self.budget.left(), "calls": run["limits"]["calls"] - spent["calls"],
                "tokens": run["limits"]["tokens"] - spent["tokens"]}

    def note(self, **fields) -> dict:
        return update(self.repo.name, self.rid, spent=self.spent(), **fields)


@contextmanager
def owning(w: Worker):
    """The checkout is the run's from its first switch to its PR, so no other
    turn writes between them. Review waits outside: its loop needs the checkout."""

    try:
        release = hold(work._busy, _lock, str(w.repo), "그 저장소를 다른 요청이 쓰고 있다", kind="turn")
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


def switched(w: Worker, sid: str, start: str) -> None:
    """The checkout on `sid`, made from `start` the first time."""

    idle(w)
    if git(w.repo, "branch", "--show-current") == sid:
        return
    if git(w.repo, "status", "--porcelain"):
        raise Stop("dirty", "저장소에 커밋 안 된 변경이 있다")
    exists = not specs.sh(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{sid}"], w.repo).returncode
    git(w.repo, "switch", *((sid,) if exists else ("-c", sid, start)))


def tested(w: Worker, argv: list[str]) -> subprocess.CompletedProcess:
    """The characterization command, cut with its whole tree, orphans included,
    by the run's time left or its cancel."""

    done = refactor_profile.sh(argv, w.repo, seconds=w.budget.left(), halt=w.halt)
    if done.returncode == refactor_profile.CUT:
        raise Stop("cancelled" if w.halt.is_set() else "budget", "특성 테스트 명령을 끊었다")
    return done


def turn(w: Worker, run: dict, text: str, system: str = TESTS_PROMPT, write: bool = True) -> str:
    """One native turn in the checkout the caller owns, shown in the agent
    pane like any work turn; its tokens are charged, and unknown usage stops
    the run. A read-only turn gets read tools only — no shell — and must leave
    HEAD and the working tree as it found them."""

    try:
        w.budget.call()
    except (Exhausted, Cancelled) as exc:
        raise Stop("budget" if isinstance(exc, Exhausted) else "cancelled", str(exc)) from exc
    role = run["role"]
    state = lambda: (git(w.repo, "rev-parse", "HEAD"), git(w.repo, "status", "--porcelain"))  # noqa: E731
    before = None if write else state()
    chat = ChatSession(w.repo, write=write, bypass=write, system=system, model=role["model"] or None,
                       effort=role["effort"] or None, **({} if write else {"tools": "Read,Glob,Grep"}))
    active = work.Run(chat)
    with _lock:
        work._runs[str(w.repo)] = active
    timer = threading.Timer(w.budget.left(), lambda: (active.halt.set(), chat.stop(active.halt)))
    timer.daemon = True
    timer.start()
    final, failed, tokens = "", "", {}
    try:
        work.remember(w.repo, "user", text)
        work.feed.put({"kind": "started", "path": str(w.repo), "turn": active.turn})
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
        timer.cancel()
        chat.close()
        work.remember(w.repo, "assistant", final, error=failed, steps=work.steps(active.events), turn=active.turn,
                      cell=chat.id, started_at=active.started_at)
        active.finish()
        work.feed.put({"kind": "work-record", "path": str(w.repo)})
    if type(tokens.get("in")) is not int or type(tokens.get("out")) is not int:
        raise Stop("budget_unknown", "호스트가 사용량을 알려 주지 않았다 — 0 으로 치지 않고 멈춘다")
    w.budget.charge({"input_tokens": tokens["in"], "output_tokens": tokens["out"]})
    if failed or active.halt.is_set():
        raise Stop("cancelled" if w.halt.is_set() else "host", failed or "턴이 끊겼다")
    if before is not None and state() != before:
        raise Stop("read_only_wrote", "읽기 전용 턴이 저장소를 바꿨다 — 그 상태 위에서는 이어가지 않는다")
    return final


def others(repo: Path, rid: str) -> list[str]:
    """Open tasks in `repo` that are not this run's."""

    return [s["id"] for s in specs.listing(repo.name)
            if s["state"] not in CLOSED and (s.get("refactor") or {}).get("run") != rid]


def blocked(w: Worker, sid: str, on: bool) -> None:
    """Hold or release the repository for an L2–L3 step. The hold lives on the
    step's spec, so `specs.checkout_idle` refuses new tasks and a restart keeps it."""

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


def spec_for(w: Worker, run: dict, sid: str, goal: str, base: str, start: str) -> dict:
    gate = specs.gate_of(w.repo)
    now = time.time()
    spec = {"id": sid, "repo": w.repo.name, "rev": 1, "goal": goal, "out": [], "done": [gate],
            "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [],
            "review_profile": "code", "review_profile_version": specs.PROFILE_VERSION, "artifact_root": None,
            "source": {"focus": "refactor", "turn": now, "plan": None}, "state": "작업 중", "stopped": None,
            "worktree": str(w.repo), "workspace_mode": "branch", "branch": sid, "start_head": start,
            "return_branch": base, "pr": None, "report": None, "gate": None, "fault": None,
            "cell": run["role"], **({"reviewer": run["reviewer"]} if run.get("reviewer") else {}),
            "refactor": {"run": run["id"]}, "history": [{"ts": now, "state": "작업 중"}]}
    with specs._files:
        if specs.load(w.repo.name, sid) is None:
            specs.save(spec)
    return specs.load(w.repo.name, sid)


def published(w: Worker, sid: str, base: str, title: str, body: str, tier: str) -> int:
    """Push and open (or find) the PR from `sid` into `base`; send it to review
    when the request covers its tier."""

    if w.halt.is_set():
        raise Stop("cancelled")
    git(w.repo, "push", "-u", "origin", sid)
    try:
        n, url = specs.pull_request(w.repo, sid, base, title, body)
    except RuntimeError as exc:
        raise Stop("publish_failed", str(exc)) from exc
    head = git(w.repo, "rev-parse", "HEAD")
    with specs._files:
        spec = specs.load(w.repo.name, sid)
        if not (spec.get("pr") or {}).get("number"):
            specs.save(specs.moved(spec, f"PR #{n}", pr={"number": n, "url": url, "base": base, "head": head,
                                                         "branch": sid}))
    if tier in AUTO_REVIEW:
        loop.kick(w.repo.name, sid)
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


def body(goal: str, lines: list[str]) -> str:
    return "\n".join(["## 변경 요약", "", goal, "", "## 확인", "", *[f"- {line}" for line in lines], ""])


# -- The phases ----------------------------------------------------------------

def scan(w: Worker, run: dict) -> dict:
    if run["mode"] != "cleanup":   # the person chose the module; its measurements are shown, not filtered
        return w.note(phase="audit", hotspots=[r for r in debt.scan(w.repo) if r["path"] in run["files"]])
    rows = hotspots(w.repo, run["top"]) if not run["files"] else \
        [r for r in hotspots(w.repo) if r["path"] in run["files"]]
    if not rows:
        raise Stop("nothing", "줄일 부채가 없다")
    steps = [{"n": k + 1, "tier": "L1", "files": [r["path"]], "state": "pending", "spec": None,
              "goal": f"Lower the debt of {r['path']}: {r['lines']} lines (cap {r['cap']}), {r['dup']} duplicated "
                      f"lines, longest block {r['block']}. {MODES[run['mode']]['tiers'][-1]} changes only."}
             for k, r in enumerate(rows)]
    return w.note(phase="tests", hotspots=rows, steps=steps)


def audited(w: Worker, run: dict) -> dict:
    """A read-only audit of the chosen module into one to three steps."""

    tiers = MODES[run["mode"]]["tiers"]
    with owning(w):
        final = turn(w, run, "Module:\n" + "\n".join(f"- {f}" for f in run["files"]),
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
                          "goal": s["goal"].strip()})
    except (KeyError, TypeError, AssertionError, improvement.Refused) as exc:
        raise Stop("format", f"refactor-plan 이 맞지 않다 — {exc}") from exc
    return w.note(phase="tests", steps=steps)


def frozen(w: Worker, run: dict) -> dict:
    """The characterization PR every step stacks on."""

    with owning(w):
        sid = characterized(w, run)
    reviewed(w, sid)
    return w.note(phase="steps")


def characterized(w: Worker, run: dict) -> str:
    t = dict(run.get("tests") or {})
    files = run["files"] or sorted({f for s in run["steps"] for f in s["files"]})
    if not t.get("spec"):
        try:
            idle(w)
            _, start, base = specs.fork(w.repo, specs.unique(w.repo, f"refactor-{w.rid}-tests"))
        except (ValueError, RuntimeError) as exc:
            raise Stop("busy", str(exc)) from exc
        t = {"spec": git(w.repo, "branch", "--show-current"), "base": base, "start": start}
        run = w.note(tests=t)
    sid = t["spec"]
    switched(w, sid, t["start"])
    if not t.get("tests"):
        spec_for(w, run, sid, f"Characterization tests for {', '.join(files)}", t["base"], t["start"])
        final = turn(w, run, "Files:\n" + "\n".join(f"- {f}" for f in files))
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
        changed = {line[3:].strip('"') for line in specs.sh(["git", "status", "--porcelain", "-uall"], w.repo)
                   .stdout.splitlines()}
        if code or not changed or not changed <= set(tests):
            raise Stop("tests_touched_code", f"테스트 밖을 바꿨다: {sorted({*code, *(changed - set(tests))})}")
        done = tested(w, argv)
        if done.returncode:
            raise Stop("tests_fail", (done.stdout + done.stderr)[-2000:])
        git(w.repo, "add", "--", *tests)
        git(w.repo, "commit", "-m", f"Add characterization tests for {', '.join(files)}")
        t.update(tests=tests, test_argv=argv)
        run = w.note(tests=t)
    if not t.get("pr"):
        t["pr"] = published(w, sid, t["base"], f"Characterization tests for {', '.join(files)}",
                            body("Pin today's behaviour before refactoring.", [f"`{f}`" for f in t["tests"]]), "L0")
        w.note(tests=t)
    return sid


def stepped(w: Worker, run: dict) -> dict:
    previous = run["tests"]["spec"]
    for k, step in enumerate(run["steps"]):
        if step["state"] == "done":
            previous = step["spec"]
            continue
        sid = step["spec"] or specs.unique(w.repo, f"refactor-{w.rid}-{step['n']}")
        if not step["spec"]:
            run["steps"][k] = step = {**step, "spec": sid, "base": previous}
            run = w.note(steps=run["steps"])
        known = specs.load(w.repo.name, sid) if step["state"] == "adopted" else None
        if known and (n := (known.get("pr") or {}).get("number")):   # published, then stopped before its checkpoint
            if step["tier"] in AUTO_REVIEW and known["state"] == f"PR #{n}" and not known.get("rounds"):
                loop.kick(w.repo.name, sid)   # and maybe before the review was asked for
            run["steps"][k] = step = {**step, "state": "published", "pr": n}
            run = w.note(steps=run["steps"])
        with owning(w):
            if step["state"] in ("pending", "adopted"):   # a published step's branch is its PR's, never remade
                below = specs.load(w.repo.name, previous)
                switched(w, sid, (below and head_of(w.repo, below)) or previous)
            if step["state"] == "pending":
                spec_for(w, run, sid, step["goal"], previous, git(w.repo, "rev-parse", "HEAD"))
            if step["tier"] in BLOCKING:
                blocked(w, sid, True)
            if step["state"] == "pending":
                adopted = competed(w, run, step, sid)
                git(w.repo, "merge", "--ff-only", adopted["commit"])
                run["steps"][k] = step = {**step, "state": "adopted", "handoff": adopted["branch"]}
                run = w.note(steps=run["steps"])
            if step["state"] == "adopted":
                if step.get("handoff"):   # only after the checkpoint: until then a resume needs it to re-adopt
                    specs.sh(["git", "branch", "-D", step["handoff"]], w.repo)
                n = published(w, sid, previous, step["goal"], body(step["goal"], [
                    "Frozen characterization tests pass on every candidate considered.",
                    "Selected by the largest drop in debt (`tool/refactor_profile.py`)."]), step["tier"])
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


def competed(w: Worker, run: dict, step: dict, sid: str) -> dict:
    """The step through the runner; its spend is charged to the run."""

    left = w.left()
    if left["seconds"] <= 0 or left["calls"] <= 0 or left["tokens"] <= 0:
        raise Stop("budget", "한도를 다 썼다")
    config = refactor_profile.STORE / run["scope"] / sid / "experiment.json"
    try:
        if not config.exists():
            config = refactor_profile.prepare(
                w.repo, run["scope"], sid, {"goal": step["goal"], "tier": step["tier"], "files": step["files"],
                                            "tests": run["tests"]["tests"], "test_argv": run["tests"]["test_argv"]},
                run["role"], left, halt=w.halt)
        # ponytail: a cancel lands after the runner returns; the runner's own limits bound the wait
        result = refactor_profile.drive(w.repo, run["scope"], sid, config)
    except improvement.Refused as exc:
        raise Stop("cancelled" if w.halt.is_set() else "runner", str(exc)) from exc
    finally:
        try:
            spent = improvement.Experiment(w.repo, run["scope"], sid).read()["spent"]
            w.budget.used["calls"] += spent["calls"]
            w.budget.used["tokens"] += spent["tokens"]
        except improvement.Refused:
            pass
    if result["state"] == "split":
        raise Stop("split", "후보가 모두 실패했다 — 이 단계를 더 작게 나눠라: " + "; ".join(result["reasons"])[-1500:])
    return result


PHASES = {"scan": scan, "audit": audited, "tests": frozen, "steps": stepped}


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
        if (repo.name, run["id"]) in _workers:
            raise HTTPException(409, "이 리펙터링은 이미 돌고 있다")
        w = _workers[(repo.name, run["id"])] = Worker(repo, run)
    update(repo.name, run["id"], state="running", stopped=None)
    threading.Thread(target=drive, args=(w,), daemon=True).start()


def recover() -> None:
    """At startup: a run the last server left running is stopped, never replayed."""

    for folder in RUNS.glob("*"):
        for run in listing(folder.name):
            if run["state"] == "running":
                update(folder.name, run["id"], state="stopped",
                       stopped={"reason": "restart", "detail": "서버가 다시 시작됐다 — [재개] 로 잇는다", "ts": time.time()})


def close_all() -> None:
    with _lock:
        workers = list(_workers.values())
    for w in workers:
        w.halt.set()


# -- Routes --------------------------------------------------------------------

class Role(BaseModel):
    model: str = ""
    effort: str = ""


class Limits(BaseModel):
    seconds: float
    calls: int
    tokens: int


class Start(BaseModel):
    request_id: str
    mode: str
    files: list[str] = []
    top: int = 3
    role: Role = Role()
    reviewer: Role | None = None
    limits: Limits


def mine(repo: Path, rid: str) -> dict:
    run = load(repo.name, rid) if REQUEST.fullmatch(rid) else None
    if run is None:
        raise HTTPException(404, "그런 리펙터링이 없다")
    return run


@router.get("/api/refactors/scan")
def scanned() -> dict:
    repo = current_repo()
    file = repo / debt.RATCHET
    try:
        ratchet = debt.load(file) if file.exists() else None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"repo": repo.name, "rows": hotspots(repo)[:50], "ratchet": ratchet,
            "modes": {k: v["limits"] for k, v in MODES.items()}, "scope": scope_of(repo)}


@router.get("/api/refactors")
def runs() -> dict:
    repo = current_repo()
    return {"repo": repo.name, "runs": listing(repo.name)}


@router.post("/api/refactors")
def start(body: Start) -> dict:
    repo = current_repo()
    if not REQUEST.fullmatch(body.request_id):
        raise HTTPException(400, "요청 키는 영문·숫자·- 8–64자다")
    if body.mode not in MODES:
        raise HTTPException(400, f"모드는 {', '.join(MODES)} 중 하나다")
    limits = body.limits
    if not (limits.seconds > 0 and limits.calls > 0 and limits.tokens > 0 and limits.seconds < float("inf")):
        raise HTTPException(400, "시간·호출·토큰 한도는 모두 0보다 큰 유한한 값이어야 한다")
    if not 1 <= body.top <= 10:
        raise HTTPException(400, "대상 수는 1–10 이다")
    if not specs.gate_of(repo):
        raise HTTPException(409, "연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
    scope = scope_of(repo)
    if scope == "hub":
        raise HTTPException(409, "wiki-agent 자신의 리펙터링은 아직 열지 않았다")
    with _files:   # one transaction: two equal requests never both find nothing
        old = next((r for r in listing(repo.name) if r["request_id"] == body.request_id), None)
        if old is not None:
            return old
        if body.mode != "cleanup":
            if not body.files:
                raise HTTPException(400, "재구성할 모듈의 파일을 골라라")
            for rel in body.files:
                try:
                    improvement.relative(rel)
                except improvement.Refused as exc:
                    raise HTTPException(400, str(exc)) from exc
                if not (repo / rel).is_file():
                    raise HTTPException(400, f"`{rel}` 이 저장소에 없다")
            if busy := others(repo, ""):
                raise HTTPException(409, f"열린 작업을 먼저 끝내라 — L2 단계는 저장소를 혼자 쓴다: {', '.join(busy)}")
        rid = secrets.token_hex(4)
        now = time.time()
        run = {"id": rid, "repo": repo.name, "mode": body.mode, "scope": scope, "request_id": body.request_id,
               "files": body.files, "top": body.top, "role": body.role.model_dump(),
               "reviewer": body.reviewer.model_dump() if body.reviewer else None, "limits": limits.model_dump(),
               "spent": {"seconds": 0, "calls": 0, "tokens": 0}, "phase": "scan", "state": "running",
               "stopped": None, "hotspots": [], "steps": [], "tests": None, "created": now, "updated": now}
        improvement.atomic(file_of(repo.name, rid), run)
    launch(repo, run)
    return load(repo.name, rid)


@router.post("/api/refactors/{rid}/cancel")
def cancel(rid: str) -> dict:
    repo = current_repo()
    mine(repo, rid)
    with _launching:
        with _lock:
            w = _workers.get((repo.name, rid))
        if w is not None:
            w.halt.set()
        else:   # stopped, e.g. by a restart that kept its block: nothing else would release it
            released(repo, rid)
    return load(repo.name, rid)


@router.post("/api/refactors/{rid}/approve")
def approve(rid: str) -> dict:
    """The person lets the step waiting on them finish, so the next may start —
    for the step as it stands now, and only while its review still allows it."""

    repo = current_repo()
    run = mine(repo, rid)
    waiting = [s["spec"] for s in run["steps"] if s["state"] == "awaiting"]
    if not waiting:
        raise HTTPException(409, "승인을 기다리는 단계가 없다")
    marks = {}
    for sid in waiting:
        spec = specs.load(repo.name, sid)
        if spec is None or (given := mark(repo, spec)) is None:
            raise HTTPException(409, f"`{sid}` 는 리뷰를 다시 통과해야 승인할 수 있다")
        marks[sid] = given
    return update(repo.name, rid, approved={**(run.get("approved") or {}), **marks})


@router.post("/api/refactors/{rid}/resume")
def resume(rid: str) -> dict:
    repo = current_repo()
    run = mine(repo, rid)
    if run["state"] != "stopped":
        raise HTTPException(409, "멈춘 리펙터링만 잇는다")
    launch(repo, run)
    return load(repo.name, rid)
