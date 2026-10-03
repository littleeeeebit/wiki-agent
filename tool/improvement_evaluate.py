"""Frozen task-command evaluator for either improvement scope.

Offline commands are explicitly declared as such; commands making inference
must return scored JSON with actual call/token usage. No cost is guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from improvement import execute


def evaluate(request: dict, manifest: dict, directory: Path) -> dict:
    if manifest.get("schema") != "wiki-improvement-tasks/1" or request.get("stage") != "evaluate":
        raise ValueError("Expected frozen improvement tasks and an evaluation request")
    started = time.monotonic()
    usage = {"calls": 0, "tokens": 0}
    rows = []
    for task_id in request["ids"]:
        task = manifest["tasks"][task_id]
        if type(task.get("inference")) is not bool:
            raise ValueError("Each task must explicitly declare whether it performs inference")
        argv = task["argv"]
        if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
            raise ValueError("Task commands must be argument arrays")
        seconds = task.get("seconds")
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Each task needs a positive finite timeout")
        executable = shutil.which(argv[0])
        if not executable:
            raise ValueError("Cannot resolve task executable")
        command = [executable]
        for argument in argv[1:]:
            if "{root}" in argument:
                argument = argument.replace("{root}", request["root"])
            else:
                file = directory / argument
                if file.is_file():
                    argument = str(file.resolve())
            command.append(argument)
        for trial in range(request["trials"]):
            remaining = request["limits"]["seconds"] - (time.monotonic() - started)
            if remaining <= 0:
                raise ValueError("Evaluation time allowance exhausted")
            key = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
            cache = Path(os.environ["WIKI_IMPROVEMENT_CACHE"]) / key / str(trial)
            cache.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "WIKI_IMPROVEMENT_MODEL": request["model"], "WIKI_IMPROVEMENT_CACHE": str(cache)}
            code, output, errors = execute(command, Path(request["root"]), None, min(seconds, remaining), env)
            if task["inference"]:
                result = json.loads(output.decode("utf-8"))
                cost = result.get("usage", {})
                if any(type(cost.get(k)) is not int or cost[k] < 0 for k in usage):
                    raise ValueError("Inference task usage is unavailable")
                reward = result["reward"]
                if type(reward) not in (int, float) or not math.isfinite(reward) or not 0 <= reward <= 1:
                    raise ValueError("Task reward must be in [0, 1]")
                reward = reward if code == 0 else 0
            else:
                cost = {"calls": 0, "tokens": 0}
                reward = float(code == 0)
            for key in usage:
                usage[key] += cost[key]
            rows.append({"id": task_id, "trial": trial, "reward": reward, "tokens": cost["tokens"],
                         "diagnostic": errors if reward < 1 else ""})
            if any(usage[key] > request["limits"][key] for key in usage):
                return {"error": "Evaluation allowance exceeded", "usage": usage, "trials": rows}
    guards = {}
    for name, ids in manifest["guards"][request["split"]].items():
        if not isinstance(ids, list) or any(task not in request["ids"] for task in ids):
            raise ValueError("Guard tasks must belong to the evaluated split")
        guards[name] = all(r["reward"] == 1 for r in rows if r["id"] in ids)
    return {"trials": rows, "guards": guards, "usage": usage}


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Evaluate immutable task commands in a candidate checkout")
    parser.add_argument("--tasks", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = args.tasks.resolve()
        result = evaluate(json.load(sys.stdin), json.loads(path.read_text(encoding="utf-8")), path.parent)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 1 if "error" in result else 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
