"""`python tool/eval/spec_views.py {prepare,run,report} --config eval/spec-views/<experiment>.json`

The spec-views experiment (`docs/plans/loop/3-spec.md`, "Requirements and two
views"). One write session receives the same task in four forms:

    A  the spec brief the generator makes today, the feature off (`specs.brief`)
    B  that spec enriched once and frozen, as the human document (`specs.human`)
    C  the same snapshot as structured JSON (`specs.agent`, every requirement)
    D  C narrowed to the requirements the task needs (`specs.agent(spec, select)`),
       the rest by id in `.spec-views/spec.json` in the workspace

Tasks replay merged pull requests (`eval/spec-views/tasks.json`). Each cell starts
in a fresh one-commit repository from `git archive` of the merge base (a worktree
would show the merge commit, the answer, to `git log --all`) and runs one isolated
Claude Code session with the condition as its whole system prompt. The pull
request's own tests then come in from outside, digest-checked, under names the
session never saw; they and the affected test files run, the diff is checked for
forbidden paths, and the workspace is removed. The done-report is never evidence.

`prepare` checks the hidden tests against the reference, then generates, enriches,
selects and freezes each spec (`eval/spec-views/specs/`). `run` refuses an
uncommitted config and runs every pending cell under the ceilings. `report` is
`spec_views_report`. The journal, `raw/eval/spec-views/<experiment>.journal.jsonl`,
is appended only. The plan's "The comparison harness" section has the details.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import python_environment  # noqa: E402
from common.process import SUSPENDED, background_options, contained, killed, resumed, terminated  # noqa: E402
from main import channels, spec_views, specs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "raw" / "eval" / "spec-views"
SPECS = ROOT / "eval" / "spec-views" / "specs"
CONDITIONS = ("A", "B", "C", "D")
PAIRS = (("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"))
SCHEMA = "spec-views-experiment/1"
WRITE_TOOLS = "Bash,Read,Glob,Grep,Edit,Write"
READ_TOOLS = "Read,Glob,Grep"
START = "Start."
# Paths a session must never write: the evaluation config and frozen specs, the spec records, D's spec copy.
FORBIDDEN = ("eval/spec-views/", "raw/specs/", ".spec-views/")
D_FILE = ".spec-views/spec.json"
D_NOTE = ("Requirements listed in `omitted` and the person's original request are in `.spec-views/spec.json` "
          "(the frozen spec record) in this workspace: read them by id when you need them. Never edit that file.")
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


class Refused(ValueError):
    """The experiment cannot run as configured."""


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def git(root: Path, *args: str, binary: bool = False, check: bool = True):
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True,
                          **({} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}),
                          **background_options())
    if check and done.returncode:
        raise Refused(f"git {' '.join(args[:2])}: {(done.stderr if not binary else done.stderr.decode(errors='replace')).strip()}")
    return done.stdout


# -- configuration --------------------------------------------------------------

def config(path: Path, root: Path = ROOT) -> dict:
    """The experiment's config, refused unless the file is byte for byte the committed one."""

    path = Path(path).resolve()
    rel = path.relative_to(root).as_posix()
    data = path.read_bytes()
    committed = git(root, "show", f"HEAD:{rel}", binary=True, check=False)
    if not committed:
        raise Refused(f"{rel} is not committed: commit the config before any run")
    if sha(committed) != sha(data):
        raise Refused(f"{rel} differs from the committed config ({sha(data)[:12]} != {sha(committed)[:12]}): "
                      "a changed setting is a new experiment id")
    cfg = json.loads(data)
    if cfg.get("schema") != SCHEMA:
        raise Refused(f"{rel}: schema must be {SCHEMA}")
    cfg["config_digest"] = sha(data)
    return cfg


def source(cfg: dict) -> Path:
    """The repository whose history the tasks replay."""
    return Path(cfg.get("source_repo") or ROOT)


def journal_file(cfg: dict) -> Path:
    return Path(cfg.get("journal_dir") or RAW) / f"{cfg['experiment']}.journal.jsonl"


_journal_lock = threading.Lock()


def journal(cfg: dict, row: dict) -> dict:
    row = {"ts": time.time(), "experiment": cfg["experiment"], "config_digest": cfg.get("config_digest"), **row}
    file = journal_file(cfg)
    with _journal_lock:
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def rows(cfg: dict) -> list[dict]:
    try:
        return [json.loads(line) for line in journal_file(cfg).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def frozen(task_id: str, folder: Path = SPECS) -> dict:
    return json.loads((folder / f"{task_id}.json").read_text(encoding="utf-8"))


# -- workspaces -------------------------------------------------------------------

def workspace(source: Path, commit: str, dest: Path, extra: dict[str, str] | None = None) -> str:
    """`dest` as a new repository holding `commit`'s tree (plus `extra` files) in one commit, no history."""

    tree = git(source, "archive", "--format=tar", commit, binary=True)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(tree)) as tar:
        tar.extractall(dest, filter="data")
    for rel, text in (extra or {}).items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text(text, encoding="utf-8")
    git(dest, "init", "-q", "-b", "main")
    git(dest, "add", "-A")
    git(dest, "-c", "user.name=spec-views", "-c", "user.email=spec-views@localhost", "commit", "-q", "-m",
        f"start at {commit[:12]}")
    return git(dest, "rev-parse", "HEAD").strip()


def remove(path: Path) -> None:
    def writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    for _ in range(5):
        shutil.rmtree(path, onexc=writable) if path.exists() else None
        if not path.exists():
            return
        time.sleep(1)


# -- the four inputs --------------------------------------------------------------

def inputs(condition: str, record: dict) -> tuple[str, dict[str, str]]:
    """`(system prompt, files placed in the workspace before its first commit)` for a condition."""

    spec, rules = record["enriched"], specs.SPEC_PROMPT.rstrip() + "\n\n"
    head = {"id": spec["id"], "rev": spec.get("rev"), "goal": spec["goal"], "out": spec.get("out") or []}
    if condition == "A":
        return rules + specs.brief(record["baseline"]), {}
    if condition == "B":
        return rules + specs.human(spec), {}
    if condition == "C":
        shown = {**head, "original": spec.get("original"), **specs.agent(spec)}
        return rules + "```json\n" + json.dumps(shown, ensure_ascii=False, indent=2) + "\n```\n", {}
    if condition == "D":
        shown = {**head, **specs.agent(spec, record["select"]), "omitted_note": D_NOTE}
        return (rules + "```json\n" + json.dumps(shown, ensure_ascii=False, indent=2) + "\n```\n",
                {D_FILE: json.dumps(spec, ensure_ascii=False, indent=2) + "\n"})
    raise Refused(f"unknown condition {condition!r}")


def equivalence(record: dict) -> dict:
    """B and C carry the same requirement facts, goal, scope and original; D owes what its selection reaches."""

    spec = record["enriched"]
    human = specs.human(spec)
    full = specs.agent(spec)
    b_c = specs.equivalent(human, full)
    head = [_one(spec["goal"]), *[_one(o) for o in spec.get("out") or []]]
    lost = [h for h in head if h not in human]
    original = spec.get("original") or ""
    quoted = "\n".join(f"> {line}" if line else ">" for line in original.split("\n"))
    if original and quoted not in human:
        lost.append("original")
    d = specs.equivalent(human, specs.agent(spec, record["select"]))
    done = [item for item in record["baseline"].get("done") or []
            if not any(v["item"] == " ".join(item.split()) for r in spec["requirements"] for v in r.get("verify", []))]
    ok = not (b_c["missing_in_human"] or b_c["missing_in_agent"] or lost or d["missing_in_human"] or d["missing_in_agent"])
    return {"ok": ok, "b_c": b_c, "head_missing_in_human": lost, "d": d, "done_not_carried": done,
            "brief_unchanged": specs.brief(record["baseline"]) == specs.brief(spec)}


def _one(text: str) -> str:
    return " ".join(str(text).split())


# -- the implementer ----------------------------------------------------------------

def claude(workspace: Path, system: str, message: str, limits: dict, stop: threading.Event, *,
           tools: str = WRITE_TOOLS, production: bool = False) -> dict:
    """One Claude Code session in `workspace`, run to its result, a deadline, its budget or `stop`.

    Isolated (the default): `system` replaces the host's system prompt, no user or project settings,
    hooks or MCP servers, nothing persisted. `production`: the app's write-session flags — the host's
    own prompt with `system` appended and the person's settings and hooks loaded."""

    from agent.chat_local import cli_command

    fd, prompt = tempfile.mkstemp(prefix="spec-views-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(system)
    cmd = [*cli_command("claude"), "-p", "--output-format", "stream-json", "--verbose", "--tools", tools,
           "--dangerously-skip-permissions", "--max-budget-usd", f"{limits['usd']:.2f}"]
    cmd += ["--model", limits["model"]] if limits.get("model") else []
    cmd += ["--effort", limits["effort"]] if limits.get("effort") else []
    if production:
        cmd += ["--append-system-prompt-file", prompt, "--disable-slash-commands", "--settings",
                json.dumps({"fastMode": False})]
    else:
        cmd += ["--system-prompt-file", prompt, "--setting-sources", "", "--settings",
                json.dumps({"disableAllHooks": True, "fastMode": False}), "--strict-mcp-config", "--no-session-persistence"]
    got = {"result": "", "usd": None, "tokens": None, "turns": None, "error": "", "cause": "", "infra": False,
           "model_output": False}
    cause: list[str] = []
    try:
        proc = subprocess.Popen(cmd, cwd=workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                creationflags=SUSPENDED | background_options().get("creationflags", 0),
                                start_new_session=os.name != "nt")
    except OSError as exc:
        Path(prompt).unlink(missing_ok=True)
        return {**got, "error": f"could not start claude: {exc}", "cause": "error", "infra": True}
    job = contained(proc)
    resumed(proc)
    deadline = time.monotonic() + limits["seconds"]

    def watch() -> None:
        while proc.poll() is None:
            if stop.is_set() or time.monotonic() > deadline:
                cause.append("ceiling" if stop.is_set() else "timeout")
                killed(job) if job else proc.kill()
                return
            time.sleep(0.5)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    usage: dict[str, dict] = {}
    try:
        proc.stdin.write(message)
        proc.stdin.close()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                msg = ev.get("message") or {}
                usage[msg.get("id") or uuid.uuid4().hex] = msg.get("usage") or {}
                got["model_output"] = True
            elif ev.get("type") == "result":
                u = ev.get("usage") or {}
                got.update(result=str(ev.get("result") or ""), usd=ev.get("total_cost_usd"), turns=ev.get("num_turns"),
                           tokens=_tokens([u]))
                if ev.get("subtype") == "error_max_budget_usd":
                    cause.append("budget")
                elif ev.get("is_error"):
                    got["error"] = str(ev.get("result") or ev.get("subtype") or "error")[:500]
        proc.wait()
    finally:
        watcher.join(timeout=5)
        terminated(job) if job else None
        Path(prompt).unlink(missing_ok=True)
    if got["tokens"] is None and usage:
        got["tokens"] = _tokens(list(usage.values()))
    if cause:
        got["cause"] = cause[0]
    elif got["usd"] is None or got["error"]:
        got["cause"] = "error"
        got["error"] = got["error"] or ("".join(stderr)[-500:] or f"claude exited {proc.returncode} without a result")
    # Nothing reached the model and we did not stop it: the infrastructure failed, not the condition.
    got["infra"] = got["cause"] == "error" and not got["model_output"]
    return got


def _tokens(usages: list[dict]) -> dict:
    keys = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cache_read_input_tokens",
            "cache_write": "cache_creation_input_tokens"}
    return {k: sum(int(u.get(v) or 0) for u in usages) for k, v in keys.items()}


def implementer(cfg: dict, production: bool = False):
    """The configured implementer as `(workspace, system, limits, stop) -> result`."""

    def run(ws: Path, system: str, limits: dict, stop: threading.Event) -> dict:
        return claude(ws, system, START, {**cfg["implementer"], **limits}, stop, production=production)

    run.name = "claude-cli" + ("-production" if production else "")
    return run


# -- evaluation -------------------------------------------------------------------------

def hidden_store(cfg: dict) -> Path:
    return Path(cfg.get("hidden_dir") or RAW / "hidden")


def placed(path: str) -> str:
    """Where a hidden test file goes in the workspace: beside the original, under a name the session never saw."""
    p = Path(path)
    return (p.parent / f"test_hidden_{p.name.removeprefix('test_')}").as_posix()


def stored(cfg: dict, task_id: str, path: str) -> Path:
    return hidden_store(cfg) / task_id / path.replace("/", "__")


def pytest(ws: Path, args: list[str], seconds: float) -> dict:
    """`{case name: outcome}` from one pytest run, plus its exit code."""

    junit = ws.parent / f"{ws.name}-{uuid.uuid4().hex[:8]}.xml"
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", "--maxfail=0",
           f"--junitxml={junit}", *args]
    try:
        done = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=seconds, env={**python_environment(), "PYTHONDONTWRITEBYTECODE": "1"},
                              **background_options())
        code, tail = done.returncode, (done.stdout + done.stderr)[-1500:]
    except subprocess.TimeoutExpired as exc:
        code, tail = None, f"timed out after {seconds} s: {str(exc.output or '')[-1000:]}"
    cases = {}
    try:
        for case in ET.parse(junit).getroot().iter("testcase"):
            outcome = "passed"
            for child in case:
                if child.tag in ("failure", "error"):
                    outcome = "failed"
                elif child.tag == "skipped" and outcome == "passed":
                    outcome = "skipped"
            cases[case.get("name")] = outcome
    except (OSError, ET.ParseError):
        pass
    finally:
        junit.unlink(missing_ok=True)
    return {"code": code, "cases": cases, "tail": tail}


def hidden_bytes(cfg: dict, task: dict, h: dict) -> bytes:
    data = stored(cfg, task["id"], h["path"]).read_bytes()
    if sha(data) != h["digest"]:
        raise Refused(f"{task['id']}: hidden test {h['path']} digest {sha(data)[:12]} != {h['digest'][:12]}")
    return data


def check_hidden(ws: Path, cfg: dict, task: dict, seconds: float) -> dict:
    """Every hidden test copied in (digest checked) and run: `{test: passed|failed|skipped|missing}`."""

    out = {}
    for h in task["hidden"]:
        data = hidden_bytes(cfg, task, h)
        target = placed(h["path"])
        (ws / target).parent.mkdir(parents=True, exist_ok=True)
        (ws / target).write_bytes(data)
        got = pytest(ws, [f"{target}::{t}" for t in h["tests"]], seconds)
        for t in h["tests"]:
            seen = [o for name, o in got["cases"].items() if name == t or name.startswith(t + "[")]
            out[f"{h['path']}::{t}"] = ("failed" if "failed" in seen else "skipped" if seen and set(seen) == {"skipped"}
                                        else "passed" if seen else "missing")
    return out


def check_affected(ws: Path, task: dict, start: str, seconds: float) -> dict:
    """Each affected test file, restored to the merge base, run whole (or by its `-k`): passed or failed."""

    out = {}
    for a in task.get("affected") or []:
        git(ws, "checkout", start, "--", a["path"])
        got = pytest(ws, [a["path"], *(["-k", a["k"]] if a.get("k") else [])], seconds)
        failed = [n for n, o in got["cases"].items() if o == "failed"]
        out[a["path"]] = {"ok": got["code"] == 0 and not failed, "failed": failed[:20], "code": got["code"],
                          **({"tail": got["tail"]} if got["code"] != 0 else {})}
    return out


def changes(ws: Path, start: str) -> dict[str, str]:
    """`{path: status letter}` of everything the session changed since `start`, committed or not."""
    git(ws, "add", "-A")
    parts = [p for p in git(ws, "diff", "--cached", "--name-status", "--no-renames", "-z", start).split("\0") if p]
    return dict(zip(parts[1::2], parts[0::2]))


def evaluate(ws: Path, cfg: dict, task: dict, start: str) -> dict:
    """Success: every hidden test passes, the affected files pass, no forbidden path changed. A critical
    regression (fixed before any run, `critical_regression` in the config): a forbidden change, an affected
    test that fails, or a tracked file deleted."""

    changed = changes(ws, start)
    deleted = [p for p, status in changed.items() if status == "D"]
    hidden_paths = {placed(h["path"]) for h in task["hidden"]}
    forbidden = [p for p in changed if p.startswith(FORBIDDEN) or p in hidden_paths]
    edited = [a["path"] for a in task.get("affected") or [] if a["path"] in changed]
    seconds = cfg["limits"]["test_seconds"]
    hidden = check_hidden(ws, cfg, task, seconds)
    affected = check_affected(ws, task, start, seconds)
    # A skipped hidden test is no pass: every one passed on the reference (`reference`).
    ok = all(v == "passed" for v in hidden.values()) and all(a["ok"] for a in affected.values()) \
        and not forbidden
    critical = bool(forbidden or deleted or not all(a["ok"] for a in affected.values()))
    return {"success": ok, "hidden": hidden, "affected": affected, "forbidden": forbidden, "deleted": deleted,
            "critical_regression": critical, "affected_edited": edited, "changed": sorted(changed)[:200],
            "changed_count": len(changed)}


# -- running cells ---------------------------------------------------------------------

class Ledger:
    """What the experiment has spent and holds in reserve, against its ceilings."""

    def __init__(self, cfg: dict, past: list[dict]):
        self.usd_ceiling = cfg["ceilings"]["usd"]
        self.lock = threading.Lock()
        self.spent = sum(_charged(r) for r in past)
        self.reserved = 0.0
        self.elapsed = sum(r.get("elapsed", 0) for r in past if r.get("kind") == "invocation")
        self.deadline = time.monotonic() + cfg["ceilings"]["hours"] * 3600 - self.elapsed

    def reserve(self, cap: float, floor: float) -> float | None:
        with self.lock:
            room = self.usd_ceiling - self.spent - self.reserved
            if room < floor or time.monotonic() >= self.deadline:
                return None
            got = min(cap, room)
            self.reserved += got
            return got

    def settle(self, reserved: float, charged: float) -> None:
        with self.lock:
            self.reserved -= reserved
            self.spent += charged


def _charged(row: dict) -> float:
    """A row's cost toward the ceiling: what the host reported, else its whole reserved cap (unknown is not zero)."""
    if row.get("usd") is not None:
        return float(row["usd"])
    return float(row.get("usd_bound") or 0)


def cells(cfg: dict) -> list[tuple[str, str, int]]:
    """Every (task, condition, repetition), shuffled by the config's seed."""

    order = [(t["id"], c, r) for t in cfg["tasks"] for c in CONDITIONS for r in range(1, cfg["repetitions"] + 1)]
    random.Random(cfg["seed"]).shuffle(order)
    return order


def pending(cfg: dict, past: list[dict]) -> list[tuple[str, str, int]]:
    """Cells without a final row. An infrastructure failure is run once more; a second one is final."""

    seen: dict[tuple, list[dict]] = {}
    for r in past:
        if r.get("kind") == "cell":
            seen.setdefault((r["task"], r["condition"], r["repetition"]), []).append(r)
    return [c for c in cells(cfg) if not seen.get(c) or (len(seen[c]) == 1 and seen[c][0].get("infra"))]


def run_cell(cfg: dict, task: dict, record: dict, condition: str, rep: int, impl, ledger: Ledger, cap: float,
             stop: threading.Event, kind: str = "cell", scratch: Path | None = None) -> dict:
    began = time.time()
    ws = Path(scratch or tempfile.gettempdir()) / "spec-views" / cfg["experiment"] / \
        f"{task['id']}-{condition}{rep}-{uuid.uuid4().hex[:6]}"
    row = {"kind": kind, "task": task["id"], "condition": condition, "repetition": rep,
           "implementer": getattr(impl, "name", "unknown"), "model": cfg["implementer"].get("model"),
           "effort": cfg["implementer"].get("effort"), "code_commit": git(ROOT, "rev-parse", "HEAD").strip(),
           "merge_base": task["merge_base"], "spec_version": record["enriched"].get("spec_version"),
           "spec_digest": record["enriched"].get("spec_digest"), "usd_cap": round(cap, 4), "retries": 0}
    if condition == "D":
        row["input_construction"] = record.get("selection_cost") or {}
    try:
        system, files = inputs(condition, record)
        row["input_digest"] = sha(system)
        start = workspace(source(cfg), task["merge_base"], ws, files)
        got = impl(ws, system, {"usd": cap, "seconds": cfg["limits"]["session_seconds"]}, stop)
        row.update(usd=got["usd"], tokens=got["tokens"], turns=got["turns"], cause=got["cause"],
                   error=got["error"], infra=got["infra"], done_report=_done_report(got["result"]))
        if got["usd"] is None:
            row["usd_bound"] = round(cap, 4)
        if got["infra"]:
            row["success"] = False
        else:
            ev = evaluate(ws, cfg, task, start)
            row.update(ev)
            row["success"] = bool(ev["success"] and not got["cause"])
            if got["cause"] and not row.get("failure"):
                row["failure"] = got["cause"]
            elif not ev["success"]:
                row["failure"] = "verification"
            if kind == "production_baseline" and task.get("gate"):
                row["production_gate"] = gate(ws, task["gate"], cfg["limits"]["gate_seconds"])
    except Exception as exc:   # the harness or the machine failed: recorded, never a pass
        row.update(success=False, infra=True, cause="error", error=f"{type(exc).__name__}: {exc}"[:500],
                   usd_bound=row.get("usd_bound", 0))
    finally:
        remove(ws)
    row["wall_seconds"] = round(time.time() - began, 1)
    ledger.settle(cap, _charged(row))
    return journal(cfg, row)


def gate(ws: Path, command: str, seconds: float) -> dict:
    began = time.time()
    try:
        done = subprocess.run(command, shell=True, cwd=ws, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=seconds, env=python_environment(), **background_options())
        return {"command": command, "ok": done.returncode == 0, "code": done.returncode,
                "seconds": round(time.time() - began, 1), "tail": (done.stdout + done.stderr)[-1500:]}
    except subprocess.TimeoutExpired:
        return {"command": command, "ok": False, "code": None, "seconds": seconds, "tail": "timed out"}


def _done_report(text: str) -> object:
    found = re.findall(r"```done-report[ \t]*\r?\n(.*?)```", text or "", re.S)
    if not found:
        return None
    try:
        return json.loads(found[-1])
    except ValueError:
        return {"unparsed": found[-1][:2000]}


def run(cfg: dict, impl=None, production=None, folder: Path = SPECS, scratch: Path | None = None) -> dict:
    """Every pending cell, `concurrency` at a time, then the production baselines, inside the ceilings."""

    impl = impl or implementer(cfg)
    production = production if production is not None else implementer(cfg, production=True)
    tasks = {t["id"]: t for t in cfg["tasks"]}
    records = {}
    for tid in tasks:
        records[tid] = frozen(tid, folder)
        if records[tid]["enriched"].get("spec_digest") != tasks[tid]["spec_digest"]:
            raise Refused(f"{tid}: frozen spec digest differs from the config")
        for h in tasks[tid]["hidden"]:
            hidden_bytes(cfg, tasks[tid], h)   # every digest before the first cell; each cell checks again
    past = rows(cfg)
    ledger = Ledger(cfg, past)
    stop = threading.Event()
    began = time.monotonic()
    journal(cfg, {"kind": "start", "pending": len(pending(cfg, past)), "spent": round(ledger.spent, 4)})
    slots = threading.Semaphore(cfg["concurrency"])
    threads, stopped = [], None

    def launch(target, *args):
        def body():
            try:
                target(*args)
            finally:
                slots.release()
        th = threading.Thread(target=body)
        th.start()
        threads.append(th)

    def timer():
        while not stop.is_set():
            if time.monotonic() >= ledger.deadline:
                stop.set()
                return
            time.sleep(1)

    threading.Thread(target=timer, daemon=True).start()
    floor = cfg["limits"]["min_cell_usd"]
    queue = [("cell", c) for c in pending(cfg, past)]
    done_baselines = {r["task"] for r in past if r.get("kind") == "production_baseline"}
    queue += [("production_baseline", (tid, "A", 1)) for tid in tasks if tid not in done_baselines]
    for kind, (tid, cond, rep) in queue:
        slots.acquire()
        cap = None if stop.is_set() else ledger.reserve(cfg["limits"]["cell_usd"], floor)
        if cap is None:
            slots.release()
            stopped = "wall" if time.monotonic() >= ledger.deadline or stop.is_set() else "usd"
            break
        launch(run_cell, cfg, tasks[tid], records[tid], cond, rep, production if kind != "cell" else impl,
               ledger, cap, stop, kind, scratch)
    for th in threads:
        th.join()
    stopped = stopped or ("wall" if stop.is_set() else None)
    stop.set()
    elapsed = time.monotonic() - began
    left = pending(cfg, rows(cfg))
    journal(cfg, {"kind": "invocation", "elapsed": round(elapsed, 1), "stopped": stopped, "pending": len(left),
                  "spent": round(ledger.spent, 4)})
    if stopped:
        journal(cfg, {"kind": "ceiling_stop", "ceiling": stopped, "spent": round(ledger.spent, 4),
                      "hours": round((ledger.elapsed + elapsed) / 3600, 3), "pending": len(left)})
    return {"stopped": stopped, "pending": len(left), "spent": ledger.spent}


# -- preparation ------------------------------------------------------------------------------

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
    """Per task, once: hidden tests extracted and checked against the reference, the baseline spec generated
    (feature off), enriched (on), D's selection made, all frozen and checked equivalent. Returns the config's
    task entries with their digests; a frozen task is kept as it is."""

    call = call or (lambda ws, system, message, tools: claude(
        ws, system, message, {**cfg["planner"], "usd": cfg["limits"]["prepare_usd"],
                              "seconds": cfg["limits"]["session_seconds"]}, threading.Event(), tools=tools))
    listed = json.loads(Path(tasks_file).read_text(encoding="utf-8"))
    entries = []
    for task in listed["tasks"]:
        if not task.get("vetted_by"):
            raise Refused(f"{task['id']}: hidden tests not vetted by a person (`vetted_by`)")
        hidden = []
        for h in task["hidden"]:
            data = git(ROOT, "show", f"{task['merge_commit']}:{h['path']}", binary=True)
            target = stored(cfg, task["id"], h["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            hidden.append({**h, "digest": sha(data)})
        task = {**task, "hidden": hidden}
        file = folder / f"{task['id']}.json"
        if not file.exists():
            record = {"task": task["id"], "reference": reference(cfg, task)}
            journal(cfg, {"kind": "reference", "task": task["id"], **record["reference"]})
            record.update(freeze(cfg, task, call))
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        record = json.loads(file.read_text(encoding="utf-8"))
        check = equivalence(record)
        journal(cfg, {"kind": "equivalence", "task": task["id"], "spec_digest": record["enriched"]["spec_digest"],
                      "spec_version": record["enriched"]["spec_version"], **check})
        entries.append({k: task[k] for k in ("id", "merge_base", "merge_commit", "hidden", "affected", "gate", "labels")
                        if k in task} | {"spec_version": record["enriched"]["spec_version"],
                                         "spec_digest": record["enriched"]["spec_digest"], "select": record["select"],
                                         "equivalent": check["ok"]})
    return entries


def reference(cfg: dict, task: dict) -> dict:
    """The hidden tests pass on the pull request's own result and fail before it; the affected files pass on both."""

    out = {}
    for name, commit in (("merge_commit", task["merge_commit"]), ("merge_base", task["merge_base"])):
        ws = Path(tempfile.gettempdir()) / "spec-views" / "reference" / f"{task['id']}-{name}-{uuid.uuid4().hex[:6]}"
        try:
            start = workspace(ROOT, commit, ws)
            out[name] = {"hidden": check_hidden(ws, cfg, task, cfg["limits"]["test_seconds"]),
                         "affected": {k: v["ok"] for k, v in check_affected(ws, task, start,
                                                                             cfg["limits"]["test_seconds"]).items()}}
        finally:
            remove(ws)
    m, b = out["merge_commit"], out["merge_base"]
    out["discriminates"] = all(v == "passed" for v in m["hidden"].values()) and \
        any(v != "passed" for v in b["hidden"].values())
    return out


def freeze(cfg: dict, task: dict, call) -> dict:
    """The baseline spec (feature off), its enrichment (on) and D's selection, each one journalled model call."""

    original = task["source"]["text"]
    ws = Path(tempfile.gettempdir()) / "spec-views" / "prepare" / f"{task['id']}-{uuid.uuid4().hex[:6]}"
    costs = {}
    try:
        workspace(ROOT, task["merge_base"], ws)
        plain = channels.get("next").preamble
        got = call(ws, plain, GENERATE + original, READ_TOOLS)
        costs["generation"] = _cost(cfg, task, "generation", got)
        block = spec_block(got["result"])
        gate_cmd = task.get("round") or ""
        baseline = {"id": task["id"], "rev": 1, "goal": block["goal"], "out": block.get("out") or [],
                    "done": ([gate_cmd] if gate_cmd else []) + [d for d in block.get("done") or [] if d != gate_cmd],
                    "grounds": block.get("grounds") or {}, "decisions": block.get("decisions") or []}
        on = plain + "\n\n" + channels.REQUIREMENTS
        shown = {k: baseline[k] for k in ("goal", "out", "done", "decisions")} | {"slug": task["id"]}
        got = call(ws, on, ENRICH.format(original=original, spec=json.dumps(shown, ensure_ascii=False, indent=2)),
                   READ_TOOLS)
        costs["enrichment"] = _cost(cfg, task, "enrichment", got)
        reqs = spec_views.requirements(spec_block(got["result"]).get("requirements") or [])
        enriched = {**baseline, "original": original, "requirements": reqs}
        spec_views.stamped(None, enriched)
        got = call(ws, "", SELECT.format(spec=json.dumps(specs.agent(enriched), ensure_ascii=False)), "")
        costs["selection"] = _cost(cfg, task, "selection", got)
        picked = json.loads(re.search(r"\[.*?\]", got["result"], re.S).group(0))
        ids = [r["id"] for r in reqs]
        select = [i for i in ids if i in picked or any(r["id"] == i and r.get("global") for r in reqs)]
    finally:
        remove(ws)
    return {"baseline": baseline, "enriched": enriched, "select": select or ids, "costs": costs,
            "selection_cost": costs["selection"]}


def _cost(cfg: dict, task: dict, step: str, got: dict) -> dict:
    row = journal(cfg, {"kind": step, "task": task["id"], "usd": got["usd"], "tokens": got["tokens"],
                        "turns": got["turns"], "cause": got["cause"], "error": got["error"],
                        **({"usd_bound": cfg["limits"]["prepare_usd"]} if got["usd"] is None else {})})
    if got["cause"] or got["error"]:
        raise Refused(f"{task['id']}: {step} failed — {got['error'] or got['cause']}")
    return {k: row[k] for k in ("usd", "tokens", "turns")}


# -- final rows (`spec_views_report` reads the journal through these) -------------------------------

def finals(past: list[dict], kind: str = "cell") -> dict[tuple, dict]:
    """The row that counts for each cell: the rerun after an infrastructure failure, else the only one."""

    out: dict[tuple, dict] = {}
    for r in past:
        if r.get("kind") == kind:
            key = (r["task"], r["condition"], r["repetition"])
            if key not in out or out[key].get("infra"):
                out[key] = r
    return out


# -- the command line ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/spec_views.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("prepare", "run", "report"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=ROOT / "eval" / "spec-views" / "tasks.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            # Before the config is committed: it reads the draft and fills in the digests.
            cfg = json.loads(args.config.read_text(encoding="utf-8"))
            cfg["tasks"] = prepare(cfg, args.tasks)
            args.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps([{k: t[k] for k in ("id", "spec_digest", "equivalent", "select")} for t in cfg["tasks"]],
                             indent=1))
            return 0
        cfg = config(args.config)
        cfg["path"] = args.config.as_posix()
        if args.command == "run":
            print(json.dumps(run(cfg)))
            return 0
        from eval.spec_views_report import markdown, report   # it imports this module
        rep = report(cfg)
        out = RAW / f"{cfg['experiment']}.report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (ROOT / cfg["report"]["markdown"]).write_text(markdown(rep, cfg), encoding="utf-8")
        print(out)
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
