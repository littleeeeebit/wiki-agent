"""refactor_api — the HTTP routes of the `[리펙터링]` workflow.

The workflow itself, its record and its worker are `refactor.py`; this module
only validates requests and calls it. Names are reached through the module,
`refactor.launch` and the rest, so what replaces them in a test replaces them here.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import debt
import improvement

from . import refactor, specs
from .query import _lock, current_repo

router = APIRouter()


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
            "modes": {k: v["limits"] for k, v in refactor.MODES.items()}, "scope": refactor.scope_of(repo)}


@router.get("/api/refactors")
def runs() -> dict:
    repo = current_repo()
    return {"repo": repo.name, "runs": refactor.listing(repo.name)}


@router.post("/api/refactors")
def start(body: Start) -> dict:
    repo = current_repo()
    if not refactor.REQUEST.fullmatch(body.request_id):
        raise HTTPException(400, "요청 키는 영문·숫자·- 8–64자다")
    if body.mode not in refactor.MODES:
        raise HTTPException(400, f"모드는 {', '.join(refactor.MODES)} 중 하나다")
    limits = body.limits
    if not (limits.seconds > 0 and limits.calls > 0 and limits.tokens > 0 and limits.seconds < float("inf")):
        raise HTTPException(400, "시간·호출·토큰 한도는 모두 0보다 큰 유한한 값이어야 한다")
    if not 1 <= body.top <= 10:
        raise HTTPException(400, "대상 수는 1–10 이다")
    if not specs.gate_of(repo):
        raise HTTPException(409, "연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
    scope = refactor.scope_of(repo)
    if scope == "hub":
        raise HTTPException(409, "wiki-agent 자신의 리펙터링은 아직 열지 않았다")
    with refactor._files:   # one transaction: two equal requests never both find nothing
        old = next((r for r in refactor.listing(repo.name) if r["request_id"] == body.request_id), None)
        if old is not None:
            return old
        if body.mode == "restructure":
            if not body.files:
                raise HTTPException(400, "재구성할 모듈의 파일을 골라라")
            for rel in body.files:
                try:
                    improvement.relative(rel)
                except improvement.Refused as exc:
                    raise HTTPException(400, str(exc)) from exc
                if not (repo / rel).is_file():
                    raise HTTPException(400, f"`{rel}` 이 저장소에 없다")
            if busy := refactor.others(repo, ""):
                raise HTTPException(409, f"열린 작업을 먼저 끝내라 — L2 단계는 저장소를 혼자 쓴다: {', '.join(busy)}")
        rid = secrets.token_hex(4)
        now = time.time()
        run = {"id": rid, "repo": repo.name, "mode": body.mode, "scope": scope, "request_id": body.request_id,
               "files": body.files, "top": body.top, "role": body.role.model_dump(),
               "reviewer": body.reviewer.model_dump() if body.reviewer else None, "limits": limits.model_dump(),
               "spent": {"seconds": 0, "calls": 0, "tokens": 0}, "phase": "scan", "state": "running",
               "stopped": None, "hotspots": [], "steps": [], "tests": None, "created": now, "updated": now}
        improvement.atomic(refactor.file_of(repo.name, rid), run)
    refactor.launch(repo, run)
    return refactor.load(repo.name, rid)


@router.post("/api/refactors/{rid}/cancel")
def cancel(rid: str) -> dict:
    repo = current_repo()
    mine(repo, rid)
    with refactor._launching:
        with _lock:
            w = refactor._workers.get((repo.name, rid))
        if w is not None:
            w.halt.set()
        else:   # stopped, e.g. by a restart that kept its block: nothing else would release it
            refactor.released(repo, rid)
    return refactor.load(repo.name, rid)


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
        if spec is None or (given := refactor.mark(repo, spec)) is None:
            raise HTTPException(409, f"`{sid}` 는 리뷰를 다시 통과해야 승인할 수 있다")
        marks[sid] = given
    return refactor.update(repo.name, rid, approved={**(run.get("approved") or {}), **marks})


@router.post("/api/refactors/{rid}/resume")
def resume(rid: str) -> dict:
    repo = current_repo()
    run = mine(repo, rid)
    if run["state"] != "stopped":
        raise HTTPException(409, "멈춘 리펙터링만 잇는다")
    refactor.launch(repo, run)
    return refactor.load(repo.name, rid)
