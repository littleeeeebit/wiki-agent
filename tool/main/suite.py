"""Read live cells and recent executions from their existing scoped records."""

from collections import deque
import hashlib
import json
from pathlib import Path
import time

from fastapi import APIRouter
from workspace import worktrees

from . import channels, knowledge, loop, query, specs, work

router = APIRouter()
LIMIT = 100


def records(file: Path, root: Path) -> list[dict]:
    if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
        return []
    try:
        with file.open(encoding="utf-8") as stream:
            lines = deque(stream, maxlen=work.MAX_REPLAY)
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # A concurrent append or an old broken row is not a run.
        if isinstance(row, dict):
            rows.append(row)
    return rows


def entry(kind: str, target: str, row: dict, ordinal: int, prompt: dict | None = None, spec: dict | None = None) -> dict:
    prompt = prompt or {}
    key = row.get("turn") or row.get("run_id") or hashlib.sha256(
        f"{kind}:{target}:{ordinal}:{row.get('ts')}:{row.get('text')}".encode("utf-8")).hexdigest()
    stopped = row.get("cancelled") or row.get("error") in ("사람이 멈춤", "멈췄다")
    status = "stopped" if stopped else "failed" if row.get("error") else "completed"
    return {"id": str(key), "kind": kind, "target": target, "task": spec["id"] if spec else None,
            "path": spec.get("worktree") if spec else row.get("path"),
            "pr": (spec.get("pr") or {}).get("number") if spec else None,
            "status": status, "model": row.get("model") or "", "cell": row.get("cell") or "",
            "started_at": row.get("started_at") or prompt.get("ts"), "finished_at": row.get("ts"),
            "prompt": prompt.get("said") or prompt.get("text") or "", "text": row.get("text") or "",
            "error": row.get("error") or "", "steps": row.get("steps") or []}


def transcript(kind: str, target: str, rows: list[dict], spec: dict | None = None) -> list[dict]:
    out, prompt = [], None
    for i, row in enumerate(rows):
        if row.get("role") == "user":
            prompt = row
        elif row.get("role") == "assistant":
            out.append(entry(kind, target, row, i, prompt, spec))
            prompt = None
        elif row.get("role") == "context":
            prompt = None
    if prompt:
        pending = entry(kind, target, prompt, len(rows), prompt, spec)
        out.append({**pending, "status": "interrupted", "finished_at": None, "text": ""})
    return out


def live_entry(kind: str, target: str, run: work.Run, spec: dict | None = None) -> dict:
    with run.wake:
        events = list(run.events)
        halted = run.halt.is_set()
    answers = {str(e["meta"].get("id")) for e in events if e["kind"] == "answered"}
    waiting = any(e["kind"] == "approval" and not e["meta"].get("by")
                  and str(e["meta"].get("id")) not in answers for e in events)
    final = next((e["text"] for e in reversed(events) if e["kind"] == "done"), "")
    row = entry(kind, target, {"turn": run.turn, "cell": run.chat.id,
                              "model": run.chat.model, "steps": work.steps(events), "text": final}, 0, spec=spec)
    return {**row, "status": "stopping" if halted else "waiting" if waiting else "running",
            "started_at": run.started_at, "finished_at": None}


@router.get("/api/suite")
def listing() -> dict:
    with query._lock:
        name, repo = query.project(), query.current_repo()
        work_runs = dict(work._runs)
        review_runs = dict(loop._review_runs)
        conversations = {channel.id: query.recall(channel.id) for channel in channels.CHANNELS}
    tasks = {s["id"]: s for s in specs.listing(name)}
    by_path = {s["worktree"]: s for s in tasks.values() if s.get("worktree")}
    try:
        trees = worktrees(repo)
    except ValueError:
        trees = []  # Saved task history remains readable if its checkout disappeared.
    allowed_paths = {r["path"].resolve() for r in trees}
    out = []
    # ponytail: reuse bounded transcript tails; index them if very large logs
    # make this read-only view's two-second refresh measurably expensive.
    folder = work.LOGS / name
    files = {}
    if not folder.is_symlink():
        for file in folder.glob("*.jsonl"):
            files[file] = (tasks.get(file.stem), None)
    # Linked trees and a checkout without a spec use the older parent/name
    # record path. Read those exact files rather than a sibling's folder.
    for path in {*by_path, *(str(r["path"]) for r in trees)}:
        tree, spec = Path(path), by_path.get(path)
        if spec and spec.get("workspace_mode") == "branch":
            files.setdefault(work.LOGS / name / f"{spec['id']}.jsonl", (spec, path))
            spec = None  # Older primary-checkout turns predate branch-owned task logs.
        files.setdefault(work.LOGS / tree.parent.name / f"{tree.name}.jsonl", (spec, path))
    for file, (spec, path) in files.items():
        rows = records(file, work.LOGS)
        if path:
            rows = [r for r in rows if r.get("path") == path]
        kind = "planning" if spec and spec.get("planning") else "work"
        out += transcript(kind, spec["id"] if spec else file.stem, rows, spec)
    for (owner, sid), run in review_runs.items():
        if owner == name and not run.done:
            out.append(live_entry("review", sid, run, tasks.get(sid)))
    for spec in tasks.values():
        pr = (spec.get("pr") or {}).get("number")
        if pr:
            out += transcript("review", spec["id"], records(loop.folder(name, pr) / "progress.jsonl", loop.REVIEW), spec)
    for path, run in work_runs.items():
        if run.done:
            continue
        spec = by_path.get(path)
        if any(s.get("worktree") == path and s.get("workspace_mode") == "branch" for s in tasks.values()):
            spec = specs.owner(Path(path))
            if spec and spec["repo"] != name:
                spec = None
        # A session outside this project's task list must belong to one of
        # its actual worktrees; a sibling repository's cell never leaks here.
        if spec is None:
            if Path(path).resolve() not in allowed_paths:
                continue
        target = spec["id"] if spec else Path(path).name
        kind = "planning" if spec and spec.get("planning") else "work"
        row = live_entry(kind, target, run, spec)
        out.append({**row, "path": path})
    for channel, rows in conversations.items():
        out += transcript("query", channel, rows)
    for brief in knowledge.running(repo):
        live = knowledge.live(brief["run_id"], repo)
        if live is None:
            continue
        with live.wake:
            events = list(live.events)
        with query._lock:
            chat = query._sessions.get((str(repo), live.focus))
        out.append({**entry("query", live.focus, {"run_id": live.id}, 0), "status": "running",
                    "started_at": time.time() - (time.monotonic() - live.started), "finished_at": None,
                    "model": chat.model if chat else "", "cell": chat.id if chat else "",
                    "prompt": live.redact(live.question), "steps": [
                        {"kind": e["kind"], "text": e["text"]} for e in events
                        if e["kind"] in ("tool", "progress") and e.get("text")]})
    live_rows = [r for r in out if r["status"] in ("running", "waiting", "stopping")]
    live_ids = {r["id"] for r in live_rows}
    active_targets = {(r["kind"], r["target"]) for r in live_rows}
    history = [r for r in out if r["status"] not in ("running", "waiting", "stopping")
               and r["id"] not in live_ids
               and not (r["status"] == "interrupted" and (r["kind"], r["target"]) in active_targets)]
    history.sort(key=lambda r: r["finished_at"] or r["started_at"] or 0, reverse=True)
    return {"repo": name, "rows": [*live_rows, *history[:LIMIT]], "history_limit": LIMIT}
