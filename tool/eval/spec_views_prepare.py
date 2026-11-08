"""`python tool/eval/spec_views.py prepare --config <draft>`: the spec-views experiment's frozen inputs.

Per task, once: the hidden tests extracted and checked against the reference, the baseline spec generated
(feature off), enriched (on), D's selection made, all frozen and checked equivalent. Every model call and
the reference check run inside the experiment's ceilings: spend is reserved from the shared ledger, time
counts toward the same eight hours, and a `preparation` row journals the elapsed time even on failure.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from eval.spec_views import (READ_TOOLS, SPECS, Ceiling, Ledger, Refused, _charged, check_affected, check_hidden,
                             claude, discriminates, equivalence, git, journal, remove, rows, sha, source, stored,
                             workspace)
from main import channels, spec_views, specs

GENERATE = ("A person asked for the change below; their words follow verbatim. Nobody can answer questions in "
            "this run: do not ask, settle open points yourself and say so in `decisions`. Read the repository as "
            "you need, then end your answer with exactly one `spec` block for this change.\n\n---\n\n")
ENRICH = ("Below are a person's request, verbatim, and the spec already settled for it. Emit the same spec again "
          "as one `spec` block with a `requirements` section added. Keep `slug`, `goal`, `out`, `done` and "
          "`decisions` exactly as they are. Carry every `done` item as a `verify` item whose `item` is the done "
          "text verbatim, and every decision as a requirement with `source.kind` `decision`. Nobody can answer "
          "questions: read the repository as you need and mark what you could not settle `assumed` or "
          "`undecided`.\n\n## Request\n\n{original}\n\n## Spec\n\n```json\n{spec}\n```\n")
SELECT = ("A write session will implement the spec below. List the requirement ids it must see in full to "
          "implement and verify this change; the others stay available to it by id. Answer with one line: a "
          "JSON list of ids, nothing else.\n\n```json\n{spec}\n```\n")


def spec_block(text: str) -> dict:
    found = re.findall(r"```spec[ \t]*\r?\n(.*?)```", text or "", re.S)
    if not found:
        raise Refused("the generator returned no spec block")
    block = json.loads(found[-1])
    block = block[0] if isinstance(block, list) else block
    if not isinstance(block.get("goal"), str) or not isinstance(block.get("done", []), list):
        raise Refused("the spec block has no goal or done list")
    return block


def prepare(cfg: dict, tasks_file: Path, call=None, folder: Path = SPECS) -> list[dict]:
    """The config's task entries with their digests; a frozen task is kept as it is.

    `call(workspace, system, message, tools, limits)` is one model session; `limits` holds the USD cap and the
    seconds the ceilings leave."""

    call = call or (lambda ws, system, message, tools, limits: claude(
        ws, system, message, {**cfg["planner"], **limits}, threading.Event(), tools=tools))
    ledger = Ledger(cfg, rows(cfg))
    began = time.monotonic()

    def spend(ws: Path, system: str, message: str, tools: str, task: dict, step: str) -> dict:
        usd = cfg["limits"]["prepare_usd"]
        cap = ledger.reserve(usd, usd)
        if cap is None:
            raise Ceiling("wall" if time.monotonic() >= ledger.deadline else "usd")
        got = call(ws, system, message, tools, {"usd": cap, "seconds": ledger.left(cfg["limits"]["session_seconds"])})
        row = journal(cfg, {"kind": step, "task": task["id"], "usd": got["usd"], "tokens": got["tokens"],
                            "turns": got["turns"], "cause": got["cause"], "error": got["error"],
                            **({"usd_bound": cap} if got["usd"] is None else {})})
        ledger.settle(cap, _charged(row))
        if got["cause"] or got["error"]:
            raise Refused(f"{task['id']}: {step} failed — {got['error'] or got['cause']}")
        return {**got, "cost": {k: row[k] for k in ("usd", "tokens", "turns")}}

    listed = json.loads(Path(tasks_file).read_text(encoding="utf-8"))
    entries = []
    try:
        for task in listed["tasks"]:
            ledger.left(1)   # the wall ceiling, before any work on the task
            if not task.get("vetted_by"):
                raise Refused(f"{task['id']}: hidden tests not vetted by a person (`vetted_by`)")
            hidden = []
            for h in task["hidden"]:
                data = git(source(cfg), "show", f"{task['merge_commit']}:{h['path']}", binary=True)
                target = stored(cfg, task["id"], h["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                hidden.append({**h, "digest": sha(data)})
            task = {**task, "hidden": hidden}
            file = folder / f"{task['id']}.json"
            if not file.exists():
                record = {"task": task["id"], "reference": reference(cfg, task, ledger.deadline)}
                journal(cfg, {"kind": "reference", "task": task["id"], **record["reference"]})
                record.update(freeze(cfg, task, spend))
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record = json.loads(file.read_text(encoding="utf-8"))
            check = equivalence(record)
            journal(cfg, {"kind": "equivalence", "task": task["id"], "spec_digest": record["enriched"]["spec_digest"],
                          "spec_version": record["enriched"]["spec_version"], **check})
            entries.append({k: task[k] for k in ("id", "merge_base", "merge_commit", "hidden", "affected", "gate",
                                                 "labels") if k in task}
                           | {"spec_version": record["enriched"]["spec_version"],
                              "spec_digest": record["enriched"]["spec_digest"], "select": record["select"],
                              "equivalent": check["ok"]})
    except Ceiling as stop:
        journal(cfg, {"kind": "ceiling_stop", "ceiling": str(stop), "spent": round(ledger.spent, 4),
                      "hours": round((ledger.elapsed + time.monotonic() - began) / 3600, 3), "during": "prepare"})
        raise Refused(f"the {stop} ceiling stopped preparation") from None
    finally:
        journal(cfg, {"kind": "preparation", "elapsed": round(time.monotonic() - began, 1), "tasks": len(entries)})
    return entries


def reference(cfg: dict, task: dict, deadline: float | None = None) -> dict:
    """The hidden tests pass on the pull request's own result and fail before it; the affected files pass on both."""

    out = {}
    for name, commit in (("merge_commit", task["merge_commit"]), ("merge_base", task["merge_base"])):
        ws = Path(tempfile.gettempdir()) / "spec-views" / "reference" / f"{task['id']}-{name}-{uuid.uuid4().hex[:6]}"
        try:
            start = workspace(source(cfg), commit, ws)
            seconds = cfg["limits"]["test_seconds"]
            out[name] = {"hidden": check_hidden(ws, cfg, task, seconds, deadline),
                         "affected": {k: v["ok"] for k, v in check_affected(ws, task, start, seconds, deadline).items()}}
        finally:
            remove(ws)
    out["discriminates"] = discriminates(out)
    return out


def freeze(cfg: dict, task: dict, spend) -> dict:
    """The baseline spec (feature off), its enrichment (on) and D's selection, each one journalled model call."""

    original = task["source"]["text"]
    ws = Path(tempfile.gettempdir()) / "spec-views" / "prepare" / f"{task['id']}-{uuid.uuid4().hex[:6]}"
    costs = {}
    try:
        workspace(source(cfg), task["merge_base"], ws)
        plain = channels.get("next").preamble
        got = spend(ws, plain, GENERATE + original, READ_TOOLS, task, "generation")
        costs["generation"] = got["cost"]
        block = spec_block(got["result"])
        gate_cmd = task.get("round") or ""
        baseline = {"id": task["id"], "rev": 1, "goal": block["goal"], "out": block.get("out") or [],
                    "done": ([gate_cmd] if gate_cmd else []) + [d for d in block.get("done") or [] if d != gate_cmd],
                    "grounds": block.get("grounds") or {}, "decisions": block.get("decisions") or []}
        on = plain + "\n\n" + channels.REQUIREMENTS
        shown = {k: baseline[k] for k in ("goal", "out", "done", "decisions")} | {"slug": task["id"]}
        got = spend(ws, on, ENRICH.format(original=original, spec=json.dumps(shown, ensure_ascii=False, indent=2)),
                    READ_TOOLS, task, "enrichment")
        costs["enrichment"] = got["cost"]
        reqs = spec_views.requirements(spec_block(got["result"]).get("requirements") or [])
        enriched = {**baseline, "original": original, "requirements": reqs}
        spec_views.stamped(None, enriched)
        got = spend(ws, "", SELECT.format(spec=json.dumps(specs.agent(enriched), ensure_ascii=False)), "", task,
                    "selection")
        costs["selection"] = got["cost"]
        picked = json.loads(re.search(r"\[.*?\]", got["result"], re.S).group(0))
        ids = [r["id"] for r in reqs]
        select = [i for i in ids if i in picked or any(r["id"] == i and r.get("global") for r in reqs)]
    finally:
        remove(ws)
    return {"baseline": baseline, "enriched": enriched, "select": select or ids, "costs": costs,
            "selection_cost": costs["selection"]}
