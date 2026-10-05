"""refactor_profile — one refactoring step as a frozen `improvement.py` experiment.

`prepare` freezes the step: its characterization tests must already be committed
and pass on today's code, and its debt is measured. The experiment then lets
candidate patches compete: a candidate that fails a frozen test or the ratchet is
inadmissible, and among the rest the largest drop in debt wins. Held-out
validation is the repository's whole gate. `drive` runs one round and, when no
candidate survives, exactly one more with the failures as feedback; a second
failure ends in a proposal to split the step.

The tasks the evaluator runs are this file's own commands:

    python tool/refactor_profile.py preserve|shrink|gate --spec <frozen.json>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import debt  # noqa: E402
import improvement  # noqa: E402
from common.process import background_options  # noqa: E402
from wiki import slots_for  # noqa: E402

STORE = improvement.HUB / "raw" / "refactor"
CANDIDATES = {"L0": 1, "L1": 2, "L2": 3, "L3": 3}
BLOCK_MAX = 80     # a longer indented body counts as debt
TAIL = 4000


# -- The step's debt -------------------------------------------------------------

def prefixes(files: list[str]) -> list[str]:
    """The directories a step may touch: where its files live."""

    return sorted({f"{PurePosixPath(f).parent.as_posix()}/" if "/" in f else f for f in files})


def owned(rel: str, scope: list[str]) -> bool:
    return any(rel.startswith(p) if p.endswith("/") else rel == p for p in scope)


def debt_of(root: Path, scope: list[str]) -> int:
    """Lines over the caps, duplicated lines and block length over `BLOCK_MAX`,
    summed over the code under `scope`. Splitting a giant file lowers it; moving
    code around without shortening anything does not."""

    file = root / debt.RATCHET
    ratchet = debt.load(file) if file.exists() else {**debt.CAPS, "exclude": []}
    caps = {**ratchet, "files": {}}
    total = 0
    for rel, m in debt.measure(root, ratchet["exclude"]).items():
        if owned(rel, scope):
            total += max(0, m["lines"] - debt.limit(rel, caps)[0]) + m["dup"] + max(0, m["block"] - BLOCK_MAX)
    return total


# -- Freezing a step -------------------------------------------------------------

def sh(argv: list[str], cwd: Path, shell: bool = False) -> subprocess.CompletedProcess:
    if not shell:
        # `npm` is `npm.cmd` on Windows, which a shell-less spawn does not find by itself.
        argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    return subprocess.run(argv if not shell else argv[0], cwd=cwd, shell=shell, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **background_options())


def prepare(repo: Path, scope: str, name: str, step: dict, role: dict, limits: dict,
            store: Path = STORE) -> Path:
    """The experiment config for `step` = `{goal, tier, files, tests, test_argv}`;
    `test_argv` runs the characterization tests from a checkout's root."""

    repo = repo.resolve()
    if step.get("tier") not in CANDIDATES or not step.get("files") or not step.get("tests") \
            or not step.get("goal", "").strip() or not step.get("test_argv"):
        raise improvement.Refused("A step needs a goal, a tier L0–L3, files, tests and a test command")
    for rel in [*step["files"], *step["tests"]]:
        improvement.relative(rel)
    for rel in step["tests"]:
        if sh(["git", "ls-files", "--error-unmatch", "--", rel], repo).returncode:
            raise improvement.Refused(f"Characterization test {rel} is not committed; candidates would not see it")
    done = sh(step["test_argv"], repo)
    if done.returncode:
        raise improvement.Refused("The characterization tests fail on today's code; nothing to freeze\n"
                                  + (done.stdout + done.stderr)[-TAIL:])
    gate = slots_for(repo.name, repo).get("gate_cmd", "").strip()
    if not gate:
        raise improvement.Refused("The repository has no gate_cmd; connect it first")
    # L0–L1 stay inside the listed files, for edits and for credit alike; L2–L3
    # may add files beside them, so they own the files' directories.
    scope_paths = sorted(step["files"]) if step["tier"] in ("L0", "L1") else prefixes(step["files"])
    folder = store.resolve() / scope / name
    if folder.exists():
        raise improvement.Refused("A refactor step with this name was already prepared")
    folder.mkdir(parents=True)
    frozen = {"goal": step["goal"], "tier": step["tier"], "files": step["files"], "tests": step["tests"],
              "test_argv": step["test_argv"], "scope": scope_paths, "baseline": debt_of(repo, scope_paths),
              "gate": f"{gate} && {debt.command()}"}
    spec = folder / "frozen.json"
    spec.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    me = [sys.executable, str(HERE / "refactor_profile.py")]
    tasks = {"schema": "wiki-improvement-tasks/1",
             "tasks": {"preserve": {"argv": [*me, "preserve", "--spec", str(spec)], "inference": False,
                                    "seconds": 1800},
                       "shrink": {"argv": [*me, "shrink", "--spec", str(spec)], "inference": False,
                                  "scored": True, "seconds": 600},
                       "gate": {"argv": [*me, "gate", "--spec", str(spec)], "inference": False, "seconds": 3600}},
             "guards": {"evolve": {"preserved": ["preserve"]}, "held_out": {"preserved": ["gate"]}}}
    manifest = folder / "tasks.json"
    manifest.write_text(json.dumps(tasks, indent=2), encoding="utf-8", newline="\n")
    n = CANDIDATES[step["tier"]]
    host = [sys.executable, str(HERE / "improvement_host.py"), "--model", role.get("model") or "default",
            "--effort", role.get("effort") or "default", "--profile", "refactor", "--spec", str(spec)]
    config = {
        "schema": "wiki-improvement/1", "profile": "refactor", "soft_caps": ["propose"],
        "model": role.get("model") or "default",
        "rounds": 2, "candidates": n, "trials": 1, "edits_min": 1, "edits_max": 1,
        "stall_window": 1, "prune_window": 1,
        # Deterministic metrics: no noise band, no cost to trade, any real drop in debt counts.
        "delta": 0, "beta0": 0, "beta1": 0, "w_score": 1, "w_cost": 1, "w_novelty": 0,
        "limits": limits,
        "caps": {"propose": {"calls": 1, "tokens": max(1, limits["tokens"] // (2 * n))},
                 "critic": {"calls": 0, "tokens": 0}, "evaluate": {"calls": 0, "tokens": 0}},
        "tasks": {"evolve": ["preserve", "shrink"], "held_out": ["gate"]},
        "components": {"step": scope_paths}, "guards": ["preserved"],
        "protected": step["tests"],
        "commands": {"propose": host, "critic": host,
                     "evaluate": [sys.executable, str(HERE / "improvement_evaluate.py"), "--tasks", str(manifest)]},
        "controller_files": [str(spec), str(manifest), str(HERE / "refactor_profile.py"), str(HERE / "debt.py"),
                             *(str(repo / t) for t in step["tests"])],
    }
    path = folder / "experiment.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return path


def drive(repo: Path, scope: str, name: str, config: Path, store: Path = improvement.STORE) -> dict:
    """Run the step: `{"state": "adopted", "branch", ...}` or `{"state": "split", "reasons"}`."""

    experiment = improvement.Experiment(repo, scope, name, store=store)
    state = experiment.read() if experiment.state_file.exists() else experiment.initialize(config)
    base = state["base"]
    while state["incumbent"]["commit"] == base and len(state["rounds"]) < state["contract"]["rounds"]:
        state = experiment.round()
    if state["incumbent"]["commit"] == base:
        return {"state": "split", "reasons": [c["reason"] for c in state["history"]],
                "summary": improvement.summary(state)}
    return {"state": "adopted", **experiment.handoff()}


# -- The tasks the evaluator runs, in the candidate checkout ----------------------

def task(action: str, spec: dict, root: Path) -> int:
    if action == "shrink":
        now = debt_of(root, spec["scope"])
        reward = (1.0 if now == 0 else 0.0) if spec["baseline"] == 0 else \
            min(1.0, max(0.0, (spec["baseline"] - now) / spec["baseline"]))
        print(json.dumps({"reward": reward, "debt": now}))
        return 0
    if action == "preserve":
        done = sh(spec["test_argv"], root)
        if done.returncode:
            sys.stderr.write((done.stdout + done.stderr)[-TAIL:])
            return 1
        problems, _ = debt.check(root)
        if problems:
            sys.stderr.write("ratchet:\n" + "\n".join(problems))
            return 1
        return 0
    done = sh([spec["gate"]], root, shell=True)
    if done.returncode:
        sys.stderr.write((done.stdout + done.stderr)[-TAIL:])
    return done.returncode


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    action, flag, file = sys.argv[1:4]
    if action not in ("preserve", "shrink", "gate") or flag != "--spec":
        print("usage: refactor_profile.py preserve|shrink|gate --spec <frozen.json>", file=sys.stderr)
        return 2
    return task(action, json.loads(Path(file).read_text(encoding="utf-8")), Path.cwd())


if __name__ == "__main__":
    sys.exit(main())
