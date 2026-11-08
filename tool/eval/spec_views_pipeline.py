"""`python tool/eval/spec_views.py pipeline --config eval/spec-views/<main>.json`: the full-pipeline arm.

The fixed-spec comparison freezes one spec per task; adoption also needs the variance the generation step adds.
Here every (task, repetition) starts from the original text: the `next` generator makes a fresh spec with the
setting off (A's input), then the enrichment and D's selection make the candidate's input, and each condition runs
as one cell on that repetition's own spec, verified like any other cell. Rows go to the journal of
`cfg["pipeline"]["experiment"]` under its own ceilings, so the report keeps them apart from the fixed-spec results.
Every repetition's spec calls count toward its conditions' cost per success; nothing is cached.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import uuid
from pathlib import Path

from eval.spec_views import (ROOT, Ceiling, Ledger, Refused, cells, conditions, equivalence, finish, implementer,
                             journal, journal_file, pending, remove, rows, run_cell, source, unchanged, watch, workspace)
from eval.spec_views_prepare import StepFailed, enriched, generated, spender
from main import spec_views

# What a step failure may raise besides `StepFailed`: a spec block that does not parse or validate.
UNREADABLE = (StepFailed, ValueError, KeyError, AttributeError, TypeError)


def arm(cfg: dict) -> dict:
    """The main config as the pipeline arm's own experiment: its id, conditions, repetitions and ceilings."""
    p = cfg["pipeline"]
    return {**cfg, "experiment": p["experiment"], "kind": "pipeline", "conditions": p["conditions"],
            "pairs": p["pairs"], "repetitions": p["repetitions"], "ceilings": p["ceilings"]}


def originals(cfg: dict) -> dict[str, dict]:
    """The config's tasks with the original text and round command from the tasks file committed with it."""
    root = Path(cfg.get("config_root") or ROOT)
    rel = cfg["tasks_file"]
    if not cfg.get("config_commit") or not unchanged(root, rel, cfg["config_commit"]):
        raise Refused(f"{rel} is not the tasks file committed with the config")
    listed = {t["id"]: t for t in json.loads((root / rel).read_text(encoding="utf-8"))["tasks"]}
    missing = [t["id"] for t in cfg["tasks"] if t["id"] not in listed]
    if missing:
        raise Refused(f"{rel} has no original text for {', '.join(missing)}")
    return {t["id"]: {**t, "source": listed[t["id"]]["source"], "round": listed[t["id"]].get("round") or ""}
            for t in cfg["tasks"]}


def units(cfg: dict, past: list[dict]) -> list[tuple[str, int, list[str]]]:
    """`(task, repetition, conditions still to run)` in the seeded cell order, one unit per repetition."""
    left = set(pending(cfg, past))
    out: dict[tuple, list[str]] = {}
    for tid, cond, rep in cells(cfg):
        if (tid, cond, rep) in left:
            out.setdefault((tid, rep), []).append(cond)
    return [(tid, rep, conds) for (tid, rep), conds in out.items()]


def unit(cfg: dict, task: dict, rep: int, conds: list[str], impl, spend, ledger: Ledger, stop: threading.Event,
         scratch: Path | None = None) -> None:
    """One repetition: a fresh spec from the original text, then one cell per condition on it. A step that fails
    fails the conditions that needed it (a `spec` failure, infra when nothing reached the model)."""

    tag = uuid.uuid4().hex[:8]
    ws = Path(scratch or tempfile.gettempdir()) / "spec-views" / cfg["experiment"] / f"{task['id']}-p{rep}-{tag}"
    record, failed = None, {}
    try:
        workspace(source(cfg), task["merge_base"], ws)
        baseline, costs = generated(task, ws, spend)
        record = {"baseline": baseline, "enriched": baseline, "select": [], "costs": costs}
        if any(c != "A" for c in conds):
            try:
                record.update(enriched(task, ws, spend, baseline, costs))
            except Ceiling:
                conds = [c for c in conds if c == "A"]   # the rest stay pending; A has its spec
            except UNREADABLE as exc:
                failed |= {c: exc for c in conds if c != "A"}
    except Ceiling:
        return   # no room before a call: the unit stays pending for a resume
    except UNREADABLE as exc:
        failed = dict.fromkeys(conds, exc)
    finally:
        remove(ws)
    if record:
        keep = journal_file(cfg).parent / "pipeline" / cfg["experiment"] / f"{task['id']}-{rep}-{tag}.json"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        has = "requirements" in record["enriched"]
        journal(cfg, {"kind": "pipeline_spec", "task": task["id"], "repetition": rep, "unit": tag, "file": keep.as_posix(),
                      "spec_digest": record["enriched"].get("spec_digest"), "select": record["select"],
                      "blocked": spec_views.blocked(record["enriched"]) if has else [],
                      "equivalent": equivalence(record)["ok"] if has else None, "costs": record["costs"]})
    for c in conds:
        if c in failed:
            exc = failed[c]
            cut = stop.is_set()
            journal(cfg, {"kind": "cell", "task": task["id"], "condition": c, "repetition": rep,
                          "implementer": getattr(impl, "name", "unknown"), "model": cfg["implementer"].get("model"),
                          "effort": cfg["implementer"].get("effort"), "usd": 0.0, "success": False,
                          "failure": "ceiling" if cut else "spec", "cause": "ceiling" if cut else "spec",
                          "infra": bool(getattr(exc, "infra", False)) and not cut, "error": str(exc)[:500],
                          "wall_seconds": 0.0, "unit": tag})
            continue
        cap = None if stop.is_set() else ledger.reserve(cfg["limits"]["cell_usd"], cfg["limits"]["min_cell_usd"])
        if cap is None:
            return
        run_cell(cfg, task, record, c, rep, impl, ledger, cap, stop, "cell", scratch, {"unit": tag})


def run(cfg: dict, impl=None, call=None, scratch: Path | None = None) -> dict:
    """Every pending unit, `concurrency` at a time, inside the pipeline arm's own ceilings."""

    cfg = arm(cfg) if cfg.get("kind") != "pipeline" else cfg
    impl = impl or implementer(cfg)
    tasks = originals(cfg)
    past = rows(cfg)
    ledger, stop = Ledger(cfg, past), threading.Event()
    began = time.monotonic()
    todo = units(cfg, past)
    journal(cfg, {"kind": "start", "pending": sum(len(c) for *_, c in todo), "spent": round(ledger.spent, 4),
                  "conditions": conditions(cfg)})
    slots, threads, stopped = threading.Semaphore(cfg["concurrency"]), [], None

    watch(ledger, stop)
    for tid, rep, conds in todo:
        slots.acquire()
        room = ledger.usd_ceiling - ledger.spent - ledger.reserved
        if stop.is_set() or room < cfg["limits"]["prepare_usd"]:
            slots.release()
            stopped = "wall" if stop.is_set() else "usd"
            break

        def body(tid=tid, rep=rep, conds=conds):
            try:
                unit(cfg, tasks[tid], rep, conds, impl, spender(cfg, call, ledger, {"repetition": rep}, stop),
                     ledger, stop, scratch)
            finally:
                slots.release()

        th = threading.Thread(target=body)
        th.start()
        threads.append(th)
    return finish(cfg, ledger, stop, threads, began, stopped)
