"""Keep ordinary Agent continuations attached to their stopped refactoring run."""

from pathlib import Path
import time

from fastapi import HTTPException

from . import specs, work


def task(run: dict) -> dict | None:
    """Only a task recorded by this run can replace its automatic execution."""
    from . import refactor

    if run["state"] != "stopped" or (run["repo"], run["id"]) in refactor._workers:
        return None
    saved = run.get("continuation")
    ids = [saved["spec"]] if saved else [
        (run.get("tests") or {}).get("spec"), *(s.get("spec") for s in run["steps"])]
    for sid in filter(None, ids):
        spec = specs.load(run["repo"], sid)
        if not spec or (spec.get("refactor") or {}).get("run") != run["id"]:
            continue
        birth = spec["history"][0]["ts"]
        if saved:
            if saved["birth"] == birth:
                return spec
            continue
        # Older sessions predate the handoff checkpoint. Read their exact task
        # record, never another branch's record in the same checkout.
        file = work.LOGS / run["repo"] / f"{sid}.jsonl"
        cutoff = max(birth, run["created"], (run.get("stopped") or {}).get("ts", float("inf")))
        from .suite import records

        if any(r.get("role") == "user" and r.get("ts", 0) > cutoff for r in records(file, work.LOGS)):
            return spec
    return None


def view(run: dict) -> dict:
    spec = task(run)
    if spec is None:
        if saved := run.get("continuation"):
            return {**run, "state": "continued", "continuation": {**saved,
                "state": "연결된 작업을 찾을 수 없음", "pr": None, "running": False,
                "fault": "연결된 명세가 삭제되거나 바뀌었다. 이전 자동 단계를 재개하지 않는다"}}
        return run
    active = work._runs.get(spec.get("worktree"))
    running = bool(active and not active.done and specs.owner(Path(spec["worktree"])) == spec)
    return {**run, "state": "continued", "continuation": {
        "spec": spec["id"], "birth": spec["history"][0]["ts"], "state": spec["state"],
        "pr": spec.get("pr"), "running": running, "fault": spec.get("fault")}}


def take(path: Path) -> None:
    """An accepted ordinary turn takes ownership; the old stages cannot replay."""
    from . import refactor

    spec = specs.owner(path)
    rid = (spec.get("refactor") or {}).get("run") if spec else None
    if not rid:
        return
    with refactor._launching:
        run = refactor.load(spec["repo"], rid)
        if run and run["state"] == "stopped" and (spec["repo"], rid) not in refactor._workers:
            ids = [(run.get("tests") or {}).get("spec"), *(s.get("spec") for s in run["steps"])]
            if spec["id"] in ids:
                refactor.update(spec["repo"], rid, continuation={"spec": spec["id"], "birth": spec["history"][0]["ts"]})


def guard(run: dict) -> None:
    if run.get("continuation") or task(run) is not None:
        raise HTTPException(409, "에이전트 작업으로 이어진 리펙터링이다. 해당 작업의 명세·PR에서 계속해라")


def recover() -> None:
    """Preserve manual handoffs across restart without replaying either owner."""
    from . import refactor

    for folder in refactor.RUNS.glob("*"):
        for run in refactor.listing(folder.name):
            if run["state"] == "running":
                refactor.update(folder.name, run["id"], state="stopped",
                                stopped={"reason": "restart", "detail": "서버가 다시 시작됐다 — [재개] 로 잇는다", "ts": time.time()})
            elif (spec := task(run)) is not None and not run.get("continuation"):
                refactor.update(folder.name, run["id"], continuation={"spec": spec["id"], "birth": spec["history"][0]["ts"]})
