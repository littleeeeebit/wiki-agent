"""refactor_api — the HTTP routes of the `[리팩터링]` survey.

The record and its worker are `refactor.py`, the phases `refactor_survey.py`;
this module only validates requests and calls them. Names are reached through
the module, `refactor.launch` and the rest, so what replaces them in a test
replaces them here. Runs of the earlier workflow are listed and read only.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import improvement

from . import refactor, refactor_survey, specs
from .query import _lock, current_repo

router = APIRouter()


class Role(BaseModel):
    model: str = ""
    effort: str = ""


class Start(BaseModel):
    request_id: str
    mode: str
    role: Role = Role()


def mine(repo: Path, rid: str) -> dict:
    run = refactor.load(repo.name, rid) if refactor.REQUEST.fullmatch(rid) else None
    if run is None:
        raise HTTPException(404, "그런 리팩터링이 없다")
    return run


@router.get("/api/refactors")
def runs() -> dict:
    repo = current_repo()
    return {"repo": repo.name, "scope": refactor.scope_of(repo), "runs": refactor.listing(repo.name)}


@router.get("/api/refactors/{rid}")
def status(rid: str) -> dict:
    repo = current_repo()
    run = mine(repo, rid)
    with _lock:
        worker = refactor._workers.get((repo.name, rid))
        return {**run, "spent": worker.spent()} if worker else run


def checked(repo: Path, body: Start) -> None:
    if not refactor.REQUEST.fullmatch(body.request_id):
        raise HTTPException(400, "요청 키는 영문·숫자·- 8–64자다")
    if body.mode not in refactor_survey.MODES:
        raise HTTPException(400, f"모드는 {', '.join(refactor_survey.MODES)} 중 하나다")
    if not specs.gate_of(repo):
        raise HTTPException(409, "연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")


@router.post("/api/refactors")
def start(body: Start) -> dict:
    """`[리팩터링 시작]`: a read-only survey; no code changes, commits or pull requests."""

    repo = current_repo()
    checked(repo, body)
    return surveyed(repo, body)


def surveyed(repo: Path, body: Start, recheck: str | None = None) -> dict:
    """A survey in the focus's current mode; one live survey per repository.
    Starting one retires every stopped older survey, so only the newest resumes.
    A `recheck` reads one stale card's pipeline again and leaves the rest alone."""

    from .query import config

    cfg = config("refactor", repo.name)
    if body.mode != cfg.get("refactor_mode"):
        raise HTTPException(409, "상단에서 고른 모드와 요청 모드가 다르다 — 화면을 새로 고쳐라")
    with refactor._files:
        found = refactor.listing(repo.name)
        if old := next((r for r in found if r["request_id"] == body.request_id), None):
            return old
        if live := next((r for r in found if r.get("kind") == "survey" and r["state"] == "running"), None):
            raise HTTPException(409, f"이 저장소의 조사 `{live['id']}` 가 돌고 있다 — 끝나거나 멈춘 뒤 시작해라")
        for r in found if recheck is None else ():
            if r.get("kind") == "survey" and r["state"] == "stopped":
                refactor_survey.removed(repo, r["id"])
                refactor.update(repo.name, r["id"], state="superseded")
        rid, now = secrets.token_hex(4), time.time()
        run = {"id": rid, "kind": "survey", "repo": repo.name, "mode": body.mode, "scope": refactor.scope_of(repo),
               "request_id": body.request_id, "role": body.role.model_dump(),
               "generation": cfg.get("refactor_generation", 0),
               "spent": {"seconds": 0, "calls": 0, "tokens": 0}, "phase": "baseline", "state": "running",
               "stopped": None, "models": [], "specs": [], "recheck": recheck, "created": now, "updated": now}
        improvement.atomic(refactor.file_of(repo.name, rid), run)
    refactor.launch(repo, run)
    return refactor.load(repo.name, rid)


@router.post("/api/refactors/specs/{sid}/recheck")
def recheck(sid: str, body: Start) -> dict:
    """`[다시 확인]`: one pipeline turn on the current commit for a card whose files changed."""

    repo = current_repo()
    checked(repo, body)
    from .query import config

    spec = specs.load(repo.name, sid)
    if spec is None or not (spec.get("refactor") or {}).get("survey") or spec["state"] != "정리됨":
        raise HTTPException(404, "다시 확인할 시작 전 조사 명세가 없다")
    if spec["refactor"]["mode"] != body.mode or \
            spec["source"].get("refactor_generation", 0) != config("refactor", repo.name).get("refactor_generation", 0):
        raise HTTPException(409, "명세 모드가 상단에서 고른 모드와 다르다 — 지금 모드로 다시 조사해라")
    return surveyed(repo, body, sid)


@router.post("/api/refactors/{rid}/cancel")
def cancel(rid: str) -> dict:
    repo = current_repo()
    mine(repo, rid)
    with refactor._launching, _lock:
        w = refactor._workers.get((repo.name, rid))
    if w is not None:
        w.cancel()
    return refactor.load(repo.name, rid)


@router.post("/api/refactors/{rid}/resume")
def resume(rid: str) -> dict:
    """`[재개]` a stopped survey from its phase; what finished is not asked again.
    One that has made no card yet takes the current generation when its mode is
    still chosen: a mode switched away and back retires cards, and it has none."""

    from .query import config

    repo = current_repo()
    run = mine(repo, rid)
    if run["state"] != "stopped":
        raise HTTPException(409, "멈춘 조사만 잇는다")
    cfg = config("refactor", repo.name)
    if not run.get("recheck") and run["mode"] == cfg.get("refactor_mode") \
            and not any((s.get("refactor") or {}).get("survey") == rid for s in specs.listing(repo.name)):
        run = refactor.update(repo.name, rid, generation=cfg.get("refactor_generation", 0))
    refactor.launch(repo, run)
    return refactor.load(repo.name, rid)
