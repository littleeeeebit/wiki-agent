"""refactor_api — the HTTP routes of the `[리펙터링]` workflow.

The workflow itself, its record and its worker are `refactor.py`; this module
only validates requests and calls it. Names are reached through the module,
`refactor.launch` and the rest, so what replaces them in a test replaces them here.
"""

from __future__ import annotations

import secrets
import hashlib
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import debt
import improvement

from . import refactor, refactor_continuation, specs
from .query import _lock, current_repo

router = APIRouter()


class Role(BaseModel):
    model: str = ""
    effort: str = ""


class Start(BaseModel):
    request_id: str
    mode: str
    files: list[str] = []
    role: Role = Role()
    reviewer: Role | None = None
    goal: str = ""
    done: list[str] = []
    out: list[str] = []


def mine(repo: Path, rid: str) -> dict:
    run = refactor.load(repo.name, rid) if refactor.REQUEST.fullmatch(rid) else None
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
    return {"repo": repo.name, "rows": refactor.hotspots(repo)[:50], "ratchet": ratchet,
            "modes": refactor.MODES, "scope": refactor.scope_of(repo)}


@router.get("/api/refactors")
def runs() -> dict:
    repo = current_repo()
    return {"repo": repo.name, "runs": [refactor_continuation.view(r) for r in refactor.listing(repo.name)]}


@router.get("/api/refactors/{rid}")
def status(rid: str) -> dict:
    repo = current_repo()
    run = refactor_continuation.view(mine(repo, rid))
    with _lock:
        worker = refactor._workers.get((repo.name, rid))
        return {**run, "spent": worker.spent()} if worker else run


def checked(repo: Path, body: Start) -> None:
    if not refactor.REQUEST.fullmatch(body.request_id):
        raise HTTPException(400, "요청 키는 영문·숫자·- 8–64자다")
    if body.mode not in refactor.MODES:
        raise HTTPException(400, f"모드는 {', '.join(refactor.MODES)} 중 하나다")
    if not specs.gate_of(repo):
        raise HTTPException(409, "연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
    scope = refactor.scope_of(repo)
    if scope == "hub" and body.mode == "full":   # the planner forks the checkout the server runs from
        raise HTTPException(409, "wiki-agent 자신은 전면 리펙터링을 열지 않는다 — 정리나 모듈 재구성으로 하라")
    if body.mode == "restructure" and not body.files:
        raise HTTPException(400, "재구성할 모듈을 대화로 찾아 범위를 정해라")
    for rel in body.files:
        try:
            improvement.relative(rel)
        except improvement.Refused as exc:
            raise HTTPException(400, str(exc)) from exc
        target = (repo / rel).resolve()
        if repo.resolve() not in target.parents or not target.is_file():
            raise HTTPException(400, f"`{rel}` 이 저장소 안의 파일이 아니다")


@router.post("/api/refactors")
def start(body: Start) -> dict:
    return begun(current_repo(), body)


def begun(repo: Path, body: Start, sid: str | None = None) -> dict:
    checked(repo, body)
    scope = refactor.scope_of(repo)
    with refactor._files:   # one transaction: two equal requests never both find nothing
        old = next((r for r in refactor.listing(repo.name) if r["request_id"] == body.request_id), None)
        if old is not None:
            return old
        if body.mode == "restructure":
            if busy := refactor.others(repo, ""):
                raise HTTPException(409, f"열린 작업을 먼저 끝내라 — L2 단계는 저장소를 혼자 쓴다: {', '.join(busy)}")
        rid = secrets.token_hex(4)
        now = time.time()
        run = {"id": rid, "repo": repo.name, "mode": body.mode, "scope": scope, "request_id": body.request_id,
               "files": body.files, "role": body.role.model_dump(), "goal": body.goal,
               "done": body.done, "out": body.out, "spec": sid,
               "reviewer": body.reviewer.model_dump() if body.reviewer else None,
               "spent": {"seconds": 0, "calls": 0, "tokens": 0}, "phase": "scan", "state": "running",
               "stopped": None, "hotspots": [], "steps": [], "tests": None, "finish_version": 1,
               "created": now, "updated": now}
        improvement.atomic(refactor.file_of(repo.name, rid), run)
    refactor.launch(repo, run)
    return refactor.load(repo.name, rid)


def from_spec(repo: Path, sid: str, role: dict) -> dict:
    from .query import config

    settings = dict(config("refactor"))
    with specs._files:
        spec = specs.load(repo.name, sid)
        if spec is None:
            raise HTTPException(404, "그런 명세가 없다")
        request = spec.get("refactor") or {}
        if request.get("run"):
            return mine(repo, request["run"])
        if spec["state"] != "정리됨":
            raise HTTPException(409, "명세를 다시 정해라")
        if request.get("mode") != settings.get("refactor_mode", "cleanup") or \
                spec["source"].get("refactor_generation", 0) != settings.get("refactor_generation", 0):
            raise HTTPException(409, "모드가 바뀌었다. 대화에서 명세를 재검토해라")
        key = hashlib.sha256(f"{sid}:{spec['history'][0]['ts']}".encode()).hexdigest()[:32]
        body = Start(request_id=f"spec-refactor-{key}", mode=request["mode"], files=request["files"],
                     goal=spec["goal"], done=spec["done"][1:], out=spec["out"], role=Role(**role))
        run = begun(repo, body, sid)
        specs.update(repo.name, sid, refactor={**request, "run": run["id"]})
        return run


@router.post("/api/refactors/{rid}/cancel")
def cancel(rid: str) -> dict:
    repo = current_repo()
    mine(repo, rid)
    with refactor._launching:
        refactor_continuation.guard(mine(repo, rid))
        with _lock:
            w = refactor._workers.get((repo.name, rid))
        if w is not None:
            w.cancel()
        else:   # stopped, e.g. by a restart that kept its block: nothing else would release it
            refactor.released(repo, rid)
    return refactor.load(repo.name, rid)


@router.post("/api/refactors/{rid}/approve")
def approve(rid: str) -> dict:
    """The person lets the step waiting on them finish, so the next may start —
    for the step as it stands now, and only while its review still allows it."""

    repo = current_repo()
    run = mine(repo, rid)
    refactor_continuation.guard(run)
    waiting = [s["spec"] for s in run["steps"] if s["state"] == "awaiting"]
    if not waiting:
        raise HTTPException(409, "승인을 기다리는 단계가 없다")
    marks = {}
    for sid in waiting:
        spec = specs.load(repo.name, sid)
        if spec is None or (given := refactor.mark(repo, spec)) is None:
            raise HTTPException(409, f"`{sid}` 는 리뷰를 다시 통과해야 승인할 수 있다")
        marks[sid] = given
    return refactor.update(repo.name, rid, approved={**(run.get("approved") or {}), **marks})


@router.post("/api/refactors/{rid}/resume")
def resume(rid: str) -> dict:
    repo = current_repo()
    run = mine(repo, rid)
    refactor_continuation.guard(run)
    if run.get("scope_change"):
        raise HTTPException(409, "목적·모드 변경은 대화에서 선택하고 새 명세로 시작해라")
    if run["state"] != "stopped":
        raise HTTPException(409, "멈춘 리펙터링만 잇는다")
    refactor.launch(repo, run)
    return refactor.load(repo.name, rid)
