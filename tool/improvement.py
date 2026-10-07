"""Bounded harness experiments, separately owned by the hub and each project.

Commands are trusted domain adapters, not candidate code. They consume one JSON
request on stdin and return one JSON result. Selection never writes into the
source checkout, deploys hooks, publishes a PR, or changes the repository gate.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath

from common.process import background_options, resumed

HUB = Path(__file__).resolve().parents[1]
STORE = HUB / "raw" / "improvement"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
STRUCTURAL = {"client_tool", "skill", "memory", "subagent"}
SUPERVISOR = ("tool/improvement.py", "tool/improve.py", "tool/improvement_host.py",
              "tool/improvement_evaluate.py", "tool/main/improvements.py",
              "eval/", "tool/eval/", "tool/test_", "tool/conftest.py")


class Refused(ValueError):
    """A boundary, measurement or persisted allowance cannot be established."""


def usage_ceiling(value: object) -> dict:
    """Validate an adapter's declared, provider-enforced usage upper bound."""
    if not isinstance(value, dict) or set(value) != {"calls", "tokens"} or any(
            type(value[k]) is not int or value[k] < 0 for k in value):
        raise Refused("Declare a provider-enforced calls/tokens usage ceiling")
    return value


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def git(repo: Path, *args: str, data: str | None = None) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
                          input=data, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, **background_options())
    if done.returncode:
        raise Refused(done.stderr.strip() or f"git {args[0]} failed")
    return done.stdout.strip() if "-z" not in args else done.stdout


def execute(argv: list[str], path: Path, data: bytes | None, seconds: float | None, env: dict,
            halt: threading.Event | None = None, process_group: bool = True) -> tuple[int, bytes, str]:
    """Bounded output and termination of this command's descendants on timeout or interrupt."""
    process_helpers = None
    options = background_options()
    if halt is not None:
        # Reuse the refactoring command's process-tree containment for a run
        # that must remain cancellable without a deadline.
        import refactor_profile as process_helpers
        if os.name == "nt":
            options["creationflags"] |= 0x4
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(argv, cwd=path, stdin=subprocess.PIPE, stdout=output, stderr=errors, env=env,
                                **({"start_new_session": process_group} if os.name != "nt" else {}), **options)
        job = None
        try:
            if process_helpers:
                job = process_helpers._contained(proc)
                resumed(proc)
            deadline = time.monotonic() + seconds if seconds is not None else float("inf")
            while True:
                if halt and halt.is_set():
                    raise Refused("cancelled")
                try:
                    proc.communicate(data, timeout=min(0.5, max(0, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    data = None
                    if time.monotonic() >= deadline:
                        raise
        except BaseException:
            if proc.poll() is None and process_helpers is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                                   timeout=10, **background_options())
                    if proc.poll() is None:
                        proc.kill()
                else:
                    import signal
                    try:
                        os.killpg(proc.pid, signal.SIGKILL) if process_group else proc.kill()
                    except ProcessLookupError:
                        pass
                proc.wait(timeout=10)
            raise
        finally:
            if process_helpers:
                process_helpers._ended(proc, job, process_group)
        output.seek(0)
        raw = output.read(2_000_001)
        if len(raw) > 2_000_000:
            raise Refused("Command output exceeded the record limit; usage is unknown")
        errors.seek(0)
        return proc.returncode, raw, errors.read(8_000).decode("utf-8", "replace")


def identity(repo: Path) -> tuple[Path, str]:
    repo = repo.resolve()
    if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise Refused("The experiment must name a repository root")
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    return repo, hashlib.sha256(os.path.normcase(str(common)).encode("utf-8")).hexdigest()


def owner(repo: Path, scope: str, hub: Path = HUB) -> tuple[Path, str]:
    repo, key = identity(repo)
    _, hub_key = identity(hub)
    if scope not in ("hub", "project") or (scope == "hub") != (key == hub_key):
        raise Refused("Hub experiments belong to wiki-agent; project experiments belong to a connected repository")
    return repo, key


def relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value:
        raise Refused("Paths must be repository-relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p.casefold() in (".", "..", ".git") or p.endswith((".", " "))
                                for p in value.split("/")):
        raise Refused("A path escapes the candidate or reaches Git metadata")
    return value


def matches(path: str, prefix: str) -> bool:
    if os.name == "nt":
        path, prefix = path.casefold(), prefix.casefold()
    return path.startswith(prefix) if prefix.endswith("/") else path == prefix


def finite(value: object, *, positive: bool = False) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def contract(config: dict, source: Path) -> dict:
    """Freeze the owner-supplied experiment, including external adapter inputs."""
    if not isinstance(config, dict) or config.get("schema") != "wiki-improvement/1":
        raise Refused("Expected schema wiki-improvement/1")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        raise Refused("Pin the policy model and its version")
    for field in ("rounds", "candidates", "trials", "edits_min", "edits_max", "stall_window", "prune_window"):
        if type(config.get(field)) is not int or config[field] <= 0:
            raise Refused(f"{field} must be a positive integer")
    if config["edits_min"] > config["edits_max"]:
        raise Refused("edits_min exceeds edits_max")
    for field in ("delta", "beta0", "beta1", "w_score", "w_cost", "w_novelty"):
        if not finite(config.get(field)):
            raise Refused(f"{field} must be finite and nonnegative")
    limits = config.get("limits", {})
    unlimited = config.get("profile") == "refactor" and limits is None
    if not unlimited and (not isinstance(limits, dict) or not finite(limits.get("seconds"), positive=True) or any(
            type(limits.get(k)) is not int or limits[k] <= 0 for k in ("calls", "tokens"))):
        raise Refused("Submit positive finite time, call and token limits")
    caps = config.get("caps")
    if not unlimited and (not isinstance(caps, dict) or set(caps) != {"propose", "critic", "evaluate"}):
        raise Refused("Declare hard usage caps for propose, critic and evaluate")
    for value in (caps or {}).values():
        cap = usage_ceiling(value)
        if not unlimited and any(cap[k] > limits[k] for k in cap):
            raise Refused("An operation's usage ceiling exceeds the total allowance")
    # A native host reports a turn's usage only after it ends, so its ceiling is
    # kept between turns: the refactor profile alone may accept that for proposals.
    soft = config.get("soft_caps", [])
    if soft and (config.get("profile") != "refactor" or not isinstance(soft, list) or set(soft) - {"propose"}):
        raise Refused("Only the refactor profile may declare a soft proposal ceiling")
    tasks = config.get("tasks", {})
    for split in ("evolve", "held_out"):
        ids = tasks.get(split)
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.strip() for i in ids) \
                or len(ids) != len(set(ids)):
            raise Refused(f"{split} must contain unique nonempty task IDs")
    if set(tasks["evolve"]) & set(tasks["held_out"]):
        raise Refused("Evolve and held-out task IDs overlap")
    components = config.get("components")
    if not isinstance(components, dict) or not components:
        raise Refused("Declare components and their editable path prefixes")
    for component, paths in components.items():
        if not NAME.fullmatch(component.replace("_", "-")) or not isinstance(paths, list) or not paths:
            raise Refused("Invalid component mapping")
        for path in paths:
            relative(path.rstrip("/"))
    guards = config.get("guards")
    if not isinstance(guards, list) or not guards or len(set(guards)) != len(guards) \
            or any(not isinstance(g, str) or not g for g in guards):
        raise Refused("Declare mandatory domain guard names")
    protected = [relative(p.rstrip("/")) + ("/" if p.endswith("/") else "")
                 for p in config.get("protected", [])]
    patterns = config.get("leakage_patterns", [])
    if not isinstance(patterns, list):
        raise Refused("leakage_patterns must be a list")
    for pattern in patterns:
        re.compile(pattern)
    files = {str(source.resolve()): file_hash(source)}
    commands = {}
    for stage in ("propose", "critic", "evaluate"):
        argv = config.get("commands", {}).get(stage)
        if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
            raise Refused(f"{stage} must be an argv array, never a shell string")
        executable = shutil.which(argv[0])
        if not executable:
            raise Refused(f"Cannot resolve executable {argv[0]}")
        normalized = [str(Path(executable).resolve())]
        files[normalized[0]] = file_hash(Path(normalized[0]))
        for arg in argv[1:]:
            path = (source.parent / arg).resolve()
            if path.is_file():
                arg = str(path)
                files[arg] = file_hash(path)
            normalized.append(arg)
        commands[stage] = normalized
    declared = config.get("controller_files")
    if not isinstance(declared, list) or not declared:
        raise Refused("Declare evaluator dependencies and immutable task snapshots in controller_files")
    for name in declared:
        path = (source.parent / name).resolve()
        files[str(path)] = file_hash(path)
    return {**config, "commands": commands, "protected": protected, "files": files,
            "supervisor": file_hash(Path(__file__)), "environment_hash": digest(dict(os.environ))}


def score(result: dict, ids: list[str], trials: int, guards: list[str]) -> dict:
    expected = {(i, t) for i in ids for t in range(trials)}
    found = {}
    rows = result.get("trials")
    if not isinstance(rows, list):
        raise Refused("Evaluator returned no trial records")
    for row in rows:
        if not isinstance(row, dict) or type(row.get("trial")) is not int:
            raise Refused("Malformed trial")
        key = (row.get("id"), row["trial"])
        if key not in expected or key in found:
            raise Refused("Unknown or duplicate trial")
        if not finite(row.get("reward")) or row["reward"] > 1 or not finite(row.get("tokens")):
            raise Refused("Rewards must be in [0, 1] and token costs must be known")
        found[key] = row
    missing = len(expected - found.keys())
    return {"score": sum(r["reward"] for r in found.values()) / len(expected),
            "cost": sum(r["tokens"] for r in found.values()) / len(expected), "missing": missing,
            "guards": {g: result.get("guards", {}).get(g) is True for g in guards},
            "per_task": {i: sum(found.get((i, t), {}).get("reward", 0) for t in range(trials)) / trials
                         for i in ids}, "trials": rows}


def admissible(candidate: dict, incumbent: dict, best: float, config: dict, novelty: int) -> tuple[bool, str]:
    if candidate["missing"] or not all(candidate["guards"].values()):
        return False, "incomplete evaluation or domain guard failed"
    gain = candidate["score"] - incumbent["score"]
    if candidate["score"] < best - config["delta"]:
        return False, "below the noise-adjusted best-score floor"
    if incumbent["cost"] == 0 and candidate["cost"] > 0:
        return False, "added cost has no nonzero reference for the relative cost rule"
    cost = (candidate["cost"] - incumbent["cost"]) / incumbent["cost"] if incumbent["cost"] else 0
    if gain > config["delta"]:
        ok = cost <= config["beta0"] + config["beta1"] * gain
    else:
        ok = config["w_score"] * gain - config["w_cost"] * cost + config["w_novelty"] * novelty > 0
    return ok, "admissible" if ok else "performance gain does not justify cost and complexity"


class Experiment:
    def __init__(self, repo: Path, scope: str, name: str, *, hub: Path = HUB, store: Path = STORE,
                 halt: threading.Event | None = None):
        if not NAME.fullmatch(name):
            raise Refused("Experiment names use 1-48 lowercase letters, digits and hyphens")
        self.repo, self.key = owner(repo, scope, hub)
        self.hub, self.scope, self.name = hub.resolve(), scope, name
        self.root = store.resolve() / scope / self.key / name
        self.state_file = self.root / "state.json"
        self._clock = None
        self.halt = halt

    @contextlib.contextmanager
    def locked(self):
        """OS-owned locking survives crashes without reclaiming another worker's lock."""
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "lock").open("a+b") as stream:
            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise Refused("This experiment already has a running operation") from exc
            try:
                yield
            finally:
                try:
                    if self._clock is not None:
                        state = self.read()
                        self.tick(state)
                        limits = state["contract"]["limits"]
                        exceeded = limits is not None and state["spent"]["seconds"] > limits["seconds"]
                        if exceeded:
                            state["stopped"] = state.get("stopped") or "The experiment's active time allowance is exhausted"
                        self.save(state)
                        if exceeded:
                            raise Refused(state["stopped"])
                finally:
                    self._clock = None
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_UN)

    def begin(self, state: dict) -> None:
        self._clock = (time.monotonic(), state["spent"]["seconds"])

    def tick(self, state: dict) -> None:
        if self._clock is not None:
            started, prior = self._clock
            state["spent"]["seconds"] = prior + time.monotonic() - started

    def read(self) -> dict:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refused("No readable experiment exists for this owner and name") from exc
        if (state["scope"], state["repo_key"], state["name"]) != (self.scope, self.key, self.name):
            raise Refused("Experiment ownership does not match")
        return state

    def save(self, state: dict) -> None:
        self.tick(state)
        atomic(self.state_file, state)

    def resume_cancelled(self) -> dict:
        """Retain cancelled refactor evidence and restart only its unfinished trial."""
        with self.locked():
            state = self.read()
            if state.get("stopped") != "cancelled":
                return state
            if state["contract"].get("profile") != "refactor" or state["contract"]["limits"] is not None:
                raise Refused("Only unlimited refactoring can resume a cancelled experiment")
            self.check({**state, "stopped": None, "active": None})
            self.begin(state)
            recovery = state.get("recovery", 0) + 1
            atomic(self.root / "cancellations" / f"{recovery}.json", state)
            state.update(stopped=None, active=None, recovery=recovery)
            if state["rounds"] and state["rounds"][-1]["phase"] == "stopped":
                state["rounds"].pop()
            if state.get("held_out") and state["held_out"]["phase"] == "running":
                state["held_out"] = None
            self.save(state)
            return self.measure_base(state) if state["incumbent"] is None else state

    def check(self, state: dict) -> None:
        if self.halt and self.halt.is_set():
            raise Refused("cancelled")
        cfg = state["contract"]
        if digest(cfg) != state["contract_hash"] or cfg["supervisor"] != file_hash(Path(__file__)):
            raise Refused("The experiment supervisor or contract changed; start a new experiment")
        if cfg["environment_hash"] != digest(dict(os.environ)):
            raise Refused("The experiment environment changed; start a new experiment")
        for name, recorded in cfg["files"].items():
            path = Path(name)
            if not path.is_file() or file_hash(path) != recorded:
                raise Refused("An evaluator, role command or frozen input changed; start a new experiment")
        if state.get("active"):
            raise Refused("An operation was interrupted with unknown usage; retain its evidence and start a new experiment")
        if state.get("stopped"):
            raise Refused(state["stopped"])

    def clean(self, path: Path, commit: str) -> None:
        if git(path, "rev-parse", "HEAD") != commit or git(path, "status", "--porcelain", "--untracked-files=all"):
            raise Refused("The measured candidate checkout changed")

    def checkout(self, label: str, commit: str) -> Path:
        path = self.root / "checkouts" / label
        if path.exists():
            self.clean(path, commit)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            git(self.repo, "worktree", "add", "--detach", str(path), commit)
        return path

    def invoke(self, state: dict, stage: str, path: Path, request: dict, artifact: str) -> dict:
        self.check(state)
        self.tick(state)
        cfg, used = state["contract"], state["spent"]
        if state.get("recovery"):
            artifact += f"-resume-{state['recovery']}"
        unlimited = cfg["limits"] is None
        remaining = {k: None if unlimited else cfg["limits"][k] - used[k] for k in ("seconds", "calls", "tokens")}
        cap = {} if unlimited else cfg["caps"][stage]
        if not unlimited and (remaining["seconds"] <= 0 or any(remaining[k] < cap[k] for k in cap)):
            state["stopped"] = "The experiment's persisted allowance is exhausted for the next operation's ceiling"
            self.save(state)
            raise Refused(state["stopped"])
        payload = {**request, "scope": self.scope, "repo_key": self.key, "root": str(path),
                   "model": cfg["model"], "profile": cfg.get("profile"),
                   "limits": {"seconds": remaining["seconds"], **cap}}
        atomic(self.root / "records" / f"{artifact}-request.json", payload)
        state["active"] = {"stage": stage, "artifact": artifact, "started": time.time()}
        self.save(state)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
               "WIKI_IMPROVEMENT_SCOPE": self.scope, "WIKI_IMPROVEMENT_REPO": self.key,
               "WIKI_IMPROVEMENT_MODEL": cfg["model"], "WIKI_IMPROVEMENT_CACHE": str(self.root / "cache" / artifact)}
        # Each adapter must use this root for memory, tool caches and trial state.
        # A worktree isolates source files, not an arbitrary process's filesystem access.
        reported = False
        try:
            code, raw, errors = execute(cfg["commands"][stage], path,
                                        json.dumps(payload, ensure_ascii=False).encode("utf-8"), remaining["seconds"], env,
                                        **({"halt": self.halt} if self.halt is not None else {}))
            atomic(self.root / "records" / f"{artifact}-process.json", {"exit_code": code, "stderr": errors})
            result = json.loads(raw.decode("utf-8"))
            atomic(self.root / "records" / f"{artifact}-result.json", result)
            usage = result.get("usage", {})
            if any((type(usage.get(k)) is not int or usage[k] < 0)
                   and not (unlimited and k == "tokens" and usage.get(k) is None) for k in ("calls", "tokens")):
                raise Refused("Adapter usage is missing or malformed; it is never charged as zero")
            used["calls"] += usage["calls"]
            if usage.get("tokens") is None:
                used["unknown"] = True
            else:
                used["tokens"] += usage["tokens"]
            reported = True
            if any(usage[k] > cap[k] for k in cap):
                if stage not in cfg.get("soft_caps", []):
                    raise Refused("Adapter violated its hard usage ceiling; retain evidence and replace the adapter")
                # Crossed, not kept: recorded; the total allowance still stops the next operation.
                state.setdefault("overruns", []).append({"stage": stage, "artifact": artifact, "usage": usage,
                                                         "cap": cap})
            if code:
                raise Refused(f"{stage} adapter failed; its process record was retained")
            if unlimited and result.get("scope_change"):
                state["scope_change"] = result["scope_change"]
                raise Refused("The agreed refactoring scope needs a new decision")
        except BaseException as exc:
            if unlimited and stage == "propose" and not reported:
                used["unknown"] = True
            state["stopped"] = str(exc) or type(exc).__name__
            if not isinstance(exc, Exception):
                raise
            raise Refused(str(exc)) from exc
        finally:
            self.tick(state)
            state["active"] = None
            self.save(state)
        if not unlimited and any(used[k] > cfg["limits"][k] for k in ("seconds", "calls", "tokens")):
            state["stopped"] = "An adapter exceeded the experiment's persisted allowance"
            self.save(state)
            raise Refused(state["stopped"])
        self.check(state)
        return result

    def evaluate(self, state: dict, path: Path, commit: str, split: str, label: str) -> dict:
        self.clean(path, commit)
        cfg = state["contract"]
        result = self.invoke(state, "evaluate", path,
                             {"stage": "evaluate", "commit": commit, "split": split,
                              "ids": cfg["tasks"][split], "trials": cfg["trials"]}, label)
        self.clean(path, commit)
        measured = score(result, cfg["tasks"][split], cfg["trials"], cfg["guards"])
        if result["usage"]["tokens"] < sum(row["tokens"] for row in measured["trials"]):
            state["stopped"] = "Evaluator usage omits measured policy tokens"
            self.save(state)
            raise Refused(state["stopped"])
        return measured

    def initialize(self, config_path: Path) -> dict:
        with self.locked():
            if self.state_file.exists():
                raise Refused("An experiment with this name already exists")
            cfg = contract(json.loads(config_path.read_text(encoding="utf-8")), config_path)
            if git(self.repo, "diff", "HEAD", "--name-only"):
                raise Refused("Commit or preserve tracked source changes before freezing a baseline")
            base = git(self.repo, "rev-parse", "HEAD")
            context = {}
            for name in cfg.get("context_files", []):
                relative(name)
                source = (self.repo / name).resolve()
                if not source.is_relative_to(self.repo):
                    raise Refused("Context must belong to this repository")
                context[name] = source.read_text(encoding="utf-8")
                cfg["files"][str(source)] = file_hash(source)
            if sum(len(text) for text in context.values()) > 60_000:
                raise Refused("Select at most 60,000 characters of repository context")
            state = {"schema": "wiki-improvement-state/1", "scope": self.scope, "repo_key": self.key,
                     "repo": str(self.repo), "name": self.name, "base": base, "contract": cfg,
                     "contract_hash": digest(cfg), "spent": {"seconds": 0.0, "calls": 0, "tokens": 0},
                     "rounds": [], "history": [], "active": None, "stopped": None, "incumbent": None,
                     "held_out": None, "handoff": None, "context": context}
            self.begin(state)
            self.save(state)
            return self.measure_base(state)

    def measure_base(self, state: dict) -> dict:
        path = self.checkout("base", state["base"])
        measured = self.evaluate(state, path, state["base"], "evolve", "baseline")
        if measured["missing"] or not all(measured["guards"].values()):
            state["stopped"] = "The baseline is incomplete or fails a mandatory guard"
            self.save(state)
            raise Refused(state["stopped"])
        state["incumbent"] = {"commit": state["base"], "checkout": str(path), "evaluation": measured}
        state["best"] = measured["score"]
        state["trajectory"] = [measured["score"]]
        self.save(state)
        return state

    def directives(self, state: dict, n: int) -> dict:
        cfg = state["contract"]
        budget = math.ceil(round(cfg["edits_min"] + (cfg["edits_max"] - cfg["edits_min"])
                                * (1 + math.cos(math.pi * n / cfg["rounds"])) / 2, 9))
        measured = [r for r in state["history"] if r.get("evaluation")]
        tried = {e["component"] for r in measured for e in r["edits"]}
        untried = sorted(set(cfg["components"]) - tried)
        window = cfg["stall_window"]
        trajectory = state["trajectory"]
        stalled = len(trajectory) > window and trajectory[-1] - trajectory[-1 - window] <= cfg["delta"]
        recent = [r for r in measured if n - r["round"] <= cfg["prune_window"]]
        prune = []
        for component in tried:
            gains = [r["gain"] for r in recent if any(e["component"] == component for e in r["edits"])]
            machinery = [e for r in measured if r["verdict"] == "accepted" for e in r["edits"]
                         if e["component"] == component]
            if machinery and (not gains or max(gains) <= 0):
                prune.append({"component": component, "accepted_edits": machinery})
        return {"edit_budget": budget, "stalled": stalled, "untried": untried, "prune": prune}

    def apply(self, state: dict, path: Path, proposal: dict, directives: dict, reserved: bool) -> list[dict]:
        cfg = state["contract"]
        edits, patch = proposal.get("edits"), proposal.get("patch")
        if not isinstance(edits, list) or not 1 <= len(edits) <= directives["edit_budget"] or not isinstance(patch, str):
            raise Refused("The proposal is empty or exceeds its independent-edit budget")
        declared = set()
        for edit in edits:
            component, hypothesis = edit.get("component"), edit.get("hypothesis")
            if component not in cfg["components"] or not isinstance(hypothesis, str) or not hypothesis.strip():
                raise Refused("Each edit needs a known component and a hypothesis")
            if not isinstance(edit.get("paths"), list) or not edit["paths"]:
                raise Refused("Each edit must declare its affected paths")
            for name in edit["paths"]:
                relative(name)
                if not any(matches(name, p) for p in cfg["components"][component]):
                    raise Refused("A component tag does not own the edit's paths")
                declared.add(name)
            signature = (component, " ".join(hypothesis.lower().split()))
            if any(r["verdict"] == "rejected" and any(
                    (e["component"], " ".join(e["hypothesis"].lower().split())) == signature for e in r["edits"])
                   for r in state["history"]):
                raise Refused("This hypothesis was already rejected; formulate a different experiment")
        if reserved and not any(e["component"] in directives["untried"] for e in edits):
            raise Refused("A stalled run reserved this candidate for an untried component")
        protected = [".wiki/adapter.toml", ".wiki/improvement.json", *cfg["protected"]]
        if self.scope == "hub":
            protected += list(SUPERVISOR)
        for name in cfg["files"]:
            try:
                protected.append(Path(name).relative_to(self.repo).as_posix())
            except ValueError:
                pass
        if any(any(matches(name, p) or (p == "tool/test_" and name.startswith(p)) for p in protected)
               for name in declared):
            raise Refused("A candidate cannot edit its supervisor, evaluator, task data or gate contract")
        if any(re.search(pattern, patch) for pattern in cfg.get("leakage_patterns", [])):
            raise Refused("A deterministic leakage screen rejected the patch")
        git(path, "apply", "--check", "--whitespace=error", "-", data=patch)
        git(path, "apply", "--whitespace=error", "-", data=patch)
        git(path, "add", "--all")
        changed = set(filter(None, git(path, "diff", "--cached", "--name-only", "--no-renames", "-z").split("\0")))
        if not changed or changed != declared:
            raise Refused("The patch and declared edit paths disagree")
        for name in changed:
            target = path / name
            if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != path.parent):
                raise Refused("An edited path traverses a symbolic link")
            if target.exists():
                blob = target.read_bytes()
                blob.decode("utf-8")
                if blob.startswith(b"\xef\xbb\xbf"):
                    raise Refused("Candidate text must be UTF-8 without BOM")
        return edits

    def round(self) -> dict:
        with self.locked():
            state = self.read()
            self.check(state)
            if state.get("held_out") or state.get("handoff"):
                raise Refused("Held-out evidence ends search; further tuning needs a fresh experiment and split")
            n, cfg = len(state["rounds"]), state["contract"]
            if any(r["phase"] != "complete" for r in state["rounds"]):
                raise Refused("An incomplete round cannot be replayed with a fresh allowance")
            if n >= cfg["rounds"] or state["incumbent"] is None:
                raise Refused("No initialized round remains")
            incumbent = state["incumbent"]
            self.begin(state)
            self.clean(Path(incumbent["checkout"]), incumbent["commit"])
            directives = self.directives(state, n)
            candidates = []
            # Persist the round before any paid call; an interrupted round is never redrawn.
            state["rounds"].append({"n": n, "base": incumbent["commit"], "phase": "running",
                                    "directives": directives, "candidates": candidates})
            self.save(state)
            try:
                for v in range(cfg["candidates"]):
                    label = f"r{n}-c{v}"
                    checkout = f"{label}-resume-{state['recovery']}" if state.get("recovery") else label
                    path = self.checkout(checkout, incumbent["commit"])
                    item = {"round": n, "candidate": v, "edits": [], "verdict": "rejected", "reason": "",
                            "commit": None, "checkout": str(path), "evaluation": None}
                    reserved = bool(directives["stalled"] and directives["untried"] and v == cfg["candidates"] - 1)
                    proposal = self.invoke(state, "propose", path,
                                           {"stage": "propose", "components": cfg["components"],
                                            "incumbent": incumbent["evaluation"], "history": state["history"],
                                            "context": state["context"],
                                            "directives": {**directives, "exploration_required": reserved}}, f"{label}-propose")
                    self.clean(path, incumbent["commit"])
                    try:
                        item["edits"] = [e for e in proposal.get("edits", []) if isinstance(e, dict)
                                         and e.get("component") in cfg["components"]
                                         and isinstance(e.get("hypothesis"), str) and e["hypothesis"].strip()]
                        item["edits"] = self.apply(state, path, proposal, directives, reserved)
                        staged = git(path, "diff", "--cached", "--binary")
                        critic = self.invoke(state, "critic", path,
                                             {"stage": "critic", "edits": item["edits"], "patch": proposal["patch"],
                                              "evolve_ids": cfg["tasks"]["evolve"]}, f"{label}-critic")
                        # The critic may read the staged patch but cannot change it.
                        if git(path, "rev-parse", "HEAD") != incumbent["commit"] \
                                or git(path, "diff", "--cached", "--binary") != staged \
                                or git(path, "diff", "HEAD", "--binary") != staged:
                            raise Refused("The critic changed the candidate checkout")
                        if critic.get("verdict") != "accept":
                            item["reason"] = "critic rejected: " + str(critic.get("reasons", []))
                        else:
                            git(path, "commit", "-m", f"Improvement experiment {self.name}: round {n}, candidate {v}")
                            item["commit"] = git(path, "rev-parse", "HEAD")
                            ev = item["evaluation"] = self.evaluate(state, path, item["commit"], "evolve", f"{label}-eval")
                            accepted_components = {e["component"] for r in state["history"] if r["verdict"] == "accepted"
                                                   for e in r["edits"]}
                            novelty = len(({e["component"] for e in item["edits"]} & STRUCTURAL) - accepted_components)
                            ok, item["reason"] = admissible(ev, incumbent["evaluation"], state["best"], cfg, novelty)
                            item["verdict"] = "admissible" if ok else "rejected"
                            item["gain"] = ev["score"] - incumbent["evaluation"]["score"]
                            item["cost_change"] = ev["cost"] - incumbent["evaluation"]["cost"]
                    except (ValueError, OSError) as exc:
                        item["reason"] = str(exc)
                        if state.get("stopped"):
                            raise
                    candidates.append(item)
                    self.save(state)
                self.check(state)
                eligible = [c for c in candidates if c["verdict"] == "admissible"]
                winner = max(eligible, key=lambda c: c["evaluation"]["score"]) if eligible else None
                for item in candidates:
                    if item is winner:
                        item["verdict"] = "accepted"
                    elif item["verdict"] == "admissible":
                        item["verdict"] = "lost"
                state["history"].extend(candidates)
                if winner:
                    self.clean(Path(winner["checkout"]), winner["commit"])
                    state["incumbent"] = {k: winner[k] for k in ("commit", "checkout", "evaluation")}
                    state["best"] = max(state["best"], winner["evaluation"]["score"])
                state["trajectory"].append(state["incumbent"]["evaluation"]["score"])
                state["rounds"][-1].update(phase="complete", winner=winner["commit"] if winner else None)
                self.save(state)
                return state
            except Exception as exc:
                state["stopped"] = state.get("stopped") or str(exc)
                state["rounds"][-1]["phase"] = "stopped"
                self.save(state)
                raise

    def handoff(self) -> dict:
        """Fresh held-out validation followed by a local review branch, never deployment."""
        with self.locked():
            state = self.read()
            self.check(state)
            if state["handoff"]:
                if git(self.repo, "rev-parse", f"refs/heads/{state['handoff']['branch']}") != state["handoff"]["commit"]:
                    raise Refused("The adoption branch changed after handoff")
                return state["handoff"]
            incumbent = state["incumbent"]
            if not incumbent or incumbent["commit"] == state["base"] or any(r["phase"] != "complete" for r in state["rounds"]):
                raise Refused("No completed improvement is available for handoff")
            self.begin(state)
            self.clean(Path(incumbent["checkout"]), incumbent["commit"])
            if not state["held_out"]:
                # Claim the reveal first. Failure or restart cannot turn these tasks into unseen data again.
                state["held_out"] = {"phase": "running", "commit": incumbent["commit"]}
                self.save(state)
                try:
                    base = self.evaluate(state, self.root / "checkouts/base",
                                         state["base"], "held_out", "heldout-base")
                    champion = self.evaluate(state, Path(incumbent["checkout"]), incumbent["commit"],
                                             "held_out", "heldout-champion")
                    passed = not base["missing"] and not champion["missing"] \
                        and all(base["guards"].values()) and all(champion["guards"].values()) \
                        and champion["score"] >= base["score"] - state["contract"]["delta"]
                    state["held_out"] = {"phase": "passed" if passed else "failed", "base": base,
                                         "champion": champion, "commit": incumbent["commit"]}
                    self.save(state)
                except Exception as exc:
                    state["stopped"] = state.get("stopped") or str(exc)
                    self.save(state)
                    raise
            if state["held_out"]["phase"] != "passed":
                raise Refused("Held-out validation did not pass; use new tasks for further tuning")
            branch = f"improve-{self.name[:40]}-{digest([self.key, self.name])[:12]}"
            refs = git(self.repo, "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}")
            if refs and refs != incumbent["commit"]:
                raise Refused("The adoption branch already points to another commit")
            if not refs:
                git(self.repo, "branch", branch, incumbent["commit"])
            report = {"scope": self.scope, "repo_key": self.key, "repo": str(self.repo), "experiment": self.name,
                      "branch": branch, "base": state["base"], "commit": incumbent["commit"],
                      "evidence": str(self.state_file), "contract_hash": state["contract_hash"],
                      "review_required": True, "gate_required": True, "deployed": False}
            atomic(self.root / "handoff.json", report)
            state["handoff"] = report
            self.save(state)
            return report


def summary(state: dict) -> dict:
    incumbent = state.get("incumbent") or {}
    evaluation = incumbent.get("evaluation") or {}
    return {"scope": state["scope"], "repo_key": state["repo_key"], "name": state["name"],
            "rounds": len(state["rounds"]), "round_limit": state["contract"]["rounds"],
            "commit": incumbent.get("commit"), "score": evaluation.get("score"), "cost": evaluation.get("cost"),
            "spent": state["spent"], "limits": state["contract"]["limits"], "stopped": state.get("stopped"),
            "active": state.get("active"), "held_out": (state.get("held_out") or {}).get("phase"),
            "handoff": state.get("handoff"), "overruns": state.get("overruns", [])}


def listing(repo: Path, *, hub: Path = HUB, store: Path = STORE) -> dict:
    _, key = identity(repo)
    _, hub_key = identity(hub)
    scope = "hub" if key == hub_key else "project"
    records = []
    for path in (store / scope / key).glob("*/state.json"):
        if NAME.fullmatch(path.parent.name):
            records.append(summary(Experiment(repo, scope, path.parent.name, hub=hub, store=store).read()))
    return {"scope": scope, "repo_key": key, "experiments": records}
