"""refactor — the run record and worker behind the `[리팩터링]` survey
(`docs/plans/refactor/7-purpose-modes.md`).

A run is a record under `raw/refactor/runs/<repo>/`; `refactor_survey` owns
its phases. A worker drives one run on its own thread through read-only
turns; a restart stops a running run and `[재개]` continues from its phase.
Runs of the earlier cleanup/restructure/full workflow stay readable here but
never run again: their records keep their meaning, their pipeline is gone.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from fastapi import HTTPException

import debt
import improvement
import refactor_profile
from agent import ChatSession
from common import errorlog
from common.budget import Budget, Cancelled

from . import refactor_survey, runtime, specs, work
from .query import ROOT, _lock

RUNS = ROOT / "raw" / "refactor" / "runs"
LEGACY = ("cleanup", "restructure", "full")   # the earlier workflow's modes: read, never run
REQUEST = re.compile(r"[A-Za-z0-9-]{8,64}")

_files = threading.RLock()   # reads too: on Windows a read racing `atomic`'s replace fails
_workers: dict[tuple[str, str], "Worker"] = {}
_launching = threading.Lock()   # a workerless cancel's release never interleaves with a resume


class Stop(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def block(tag: str, text: str) -> str:
    """The text of the last block tagged `tag`."""

    found = re.findall(rf"^```{tag}[ \t]*\r?\n(.*?)^```", text, re.M | re.S)
    if not found:
        raise Stop("format", f"`{tag}` 블록이 없다")
    return found[-1]


def fenced(tag: str, text: str):
    """The JSON in the last block tagged `tag`."""

    try:
        return json.loads(block(tag, text))
    except ValueError as exc:
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
        self.budget = Budget(seconds=None, calls=None, candidates=0, cancel=self.halt)
        self.unknown = self.base.get("unknown", False)
        self.started = time.monotonic()
        self.active: work.Run | None = None
        self.phase = ""   # the turn `refactor_survey.said` sent, its `:repair` too

    def live(self) -> dict | None:
        """The running turn as the survey panel shows it: a recheck is one turn of minutes, and its record
        does not move until the turn ends, so the panel had only a disabled button to go by."""

        run = self.active
        if run is None or run.done:
            return None
        tools = [e for e in list(run.events) if e["kind"] == "tool"]
        return {"phase": self.phase, "since": run.started_at, "tools": len(tools),
                "last": tools[-1]["text"][:160] if tools else ""}

    def cancel(self) -> None:
        self.halt.set()
        if self.active is not None:
            self.active.halt.set()
            self.active.chat.stop(self.active.halt)

    def spent(self) -> dict:
        active = time.monotonic() - self.started - self.budget.aside_ms / 1000
        return {"seconds": round(self.base["seconds"] + active, 1),
                "calls": self.base["calls"] + self.budget.used["calls"],
                "tokens": self.base["tokens"] + self.budget.used["tokens"], "unknown": self.unknown}

    def note(self, **fields) -> dict:
        return update(self.repo.name, self.rid, spent=self.spent(), **fields)


def git(repo: Path, *args: str) -> str:
    done = specs.sh(["git", *args], repo, 120)
    if done.returncode:
        raise Stop("git", specs.said(done))
    return done.stdout.strip()


def turn(w: Worker, run: dict, text: str, system: str, where: Path) -> str:
    """One read-only native turn in the worktree `where`, shown in the agent
    pane like any work turn; usage is recorded without a ceiling. It gets read
    tools only — no shell — and must leave HEAD and the tree as it found them.
    Every message of the turn comes back, not the last alone: a hook that has
    the model go on after its report leaves the report's block in an earlier one."""

    try:
        w.budget.call()
    except Cancelled as exc:
        raise Stop("cancelled", str(exc)) from exc
    role = run["role"]
    def state() -> tuple[str, str]:
        seen = specs.status(where)
        if seen.returncode:   # two failed reads must never compare equal
            raise Stop("git", seen.stdout.strip()[3:])
        return git(where, "rev-parse", "HEAD"), seen.stdout

    before = state()
    if w.halt.is_set():
        raise Stop("cancelled")
    chat = ChatSession(where, write=False, bypass=False, system=system, model=role["model"] or None,
                       effort=role["effort"] or None, tools="Read,Glob,Grep")
    active = work.Run(chat)
    w.active = active
    if w.halt.is_set():
        active.halt.set()
    with _lock:
        work._runs[str(where)] = active
    final, failed, tokens, said = "", "", {}, []
    try:
        work.remember(where, "user", text)
        work.feed.put({"kind": "started", "path": str(where), "turn": active.turn})
        for ev in chat.say(text, active.halt):
            if w.halt.is_set():
                active.halt.set()
                chat.stop(active.halt)
            active.put({"kind": ev.kind, "text": ev.text, "meta": ev.meta, "session_id": chat.id, "parent_id": None})
            if ev.kind == "progress":
                said.append(ev.text)
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
    if state() != before:
        raise Stop("read_only_wrote", "읽기 전용 턴이 저장소를 바꿨다 — 그 상태 위에서는 이어가지 않는다")
    return "\n\n".join([*said, final])


PHASES = dict(refactor_survey.PHASES)


def drive(w: Worker) -> None:
    try:
        while (run := load(w.repo.name, w.rid))["phase"] in PHASES:
            if w.halt.is_set() or runtime.stopping.is_set():
                raise Stop("cancelled")
            PHASES[run["phase"]](w, run)
    except Stop as stop:
        w.note(state="stopped", stopped={"reason": stop.reason, "detail": stop.detail, "ts": time.time()})
    except Exception as exc:  # a broken worker still owes the run a reason
        errorlog.record("refactor", exc, repo=w.repo.name, run=w.rid)
        w.note(state="stopped", stopped={"reason": "broken", "detail": f"{type(exc).__name__}: {exc}",
                                         "ts": time.time()})
    finally:
        with _lock:
            _workers.pop((w.repo.name, w.rid), None)


def launch(repo: Path, run: dict) -> None:
    with _launching, _lock:
        if runtime.stopping.is_set():
            raise HTTPException(503, "서버가 종료 중이다")
        if run.get("kind") != "survey":
            raise HTTPException(409, "이전 정리·재구성·전면 리팩터링 실행은 읽기만 한다 — 새 모드로 조사해라")
        if (repo.name, run["id"]) in _workers:
            raise HTTPException(409, "이 리팩터링은 이미 돌고 있다")
        w = _workers[(repo.name, run["id"])] = Worker(repo, run)
    update(repo.name, run["id"], state="running", stopped=None)
    threading.Thread(target=drive, args=(w,), daemon=True).start()


def close_all() -> None:
    with _lock:
        workers = list(_workers.values())
    for w in workers:
        w.cancel()


def recover() -> None:
    """At start: a run left running is stopped, for `[재개]`. What the earlier
    workflow left waiting is let go, since nothing runs it again: its unstarted
    cards are set aside (`raw/specs/<repo>/dropped/`) and its steps' holds on
    the repository are released. Its started tasks and records stay as they are."""

    for folder in RUNS.glob("*"):
        for run in listing(folder.name):
            if run["state"] == "running":
                update(folder.name, run["id"], state="stopped",
                       stopped={"reason": "restart", "detail": "서버가 다시 시작됐다 — [재개] 로 잇는다", "ts": time.time()})
    for folder in specs.SPECS.glob("*"):
        for spec in specs.listing(folder.name):
            held = spec.get("refactor") or {}
            if held.get("survey") or not (held.get("mode") in LEGACY or held.get("run")):
                continue
            with specs._files:
                if spec["state"] == "정리됨" and not held.get("run"):
                    specs.aside(folder.name, spec["id"])
                elif held.get("block"):
                    specs.save({**spec, "refactor": {**held, "block": False}})
