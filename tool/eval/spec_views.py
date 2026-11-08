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

`prepare` (`spec_views_prepare`) checks the hidden tests against the reference, then
generates, enriches, selects and freezes each spec (`eval/spec-views/specs/`). `run`
refuses an uncommitted config or a frozen input that is not the one committed with it,
failed its reference or equivalence check, and runs every pending cell under the
ceilings. `report` is `spec_views_report`. The journal, `raw/eval/spec-views/<experiment>.journal.jsonl`,
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
from main import spec_views, specs  # noqa: E402

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


class Refused(ValueError):
    """The experiment cannot run as configured."""


class Ceiling(Exception):
    """A ceiling ran out; the message names it (`wall` or `usd`)."""


def bounded(limit: float, deadline: float | None) -> float:
    """`limit` seconds, cut to what the shared wall deadline leaves; none left is a ceiling stop."""
    if deadline is None:
        return limit
    left = deadline - time.monotonic()
    if left <= 0:
        raise Ceiling("wall")
    return min(limit, left)


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
    """The experiment's config, refused unless the file is the committed one. Its digest is the committed
    content's; `config_commit` is the commit that last changed it, which the frozen records are bound to."""

    path = Path(path).resolve()
    rel = path.relative_to(root).as_posix()
    committed = git(root, "show", f"HEAD:{rel}", binary=True, check=False)
    if not committed:
        raise Refused(f"{rel} is not committed: commit the config before any run")
    if not unchanged(root, rel, "HEAD"):
        raise Refused(f"{rel} differs from the committed config: a changed setting is a new experiment id")
    cfg = json.loads(committed)
    if cfg.get("schema") != SCHEMA:
        raise Refused(f"{rel}: schema must be {SCHEMA}")
    cfg.update(config_digest=sha(committed), config_root=str(root),
               config_commit=git(root, "log", "-1", "--format=%H", "--", rel).strip())
    return cfg


def unchanged(root: Path, rel: str, commit: str) -> bool:
    """The working file is `commit`'s version, compared as Git stores it (line-end normalisation applied)."""
    stored_id = git(root, "rev-parse", "-q", "--verify", f"{commit}:{rel}", check=False).strip()
    return bool(stored_id) and git(root, "hash-object", "--", rel, check=False).strip() == stored_id


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


def tree(cmd, cwd: Path, seconds: float, env: dict, shell: bool = False) -> tuple[int | None, str]:
    """`(exit code, output)` of `cmd` run in a job (a process group elsewhere) holding everything it starts.
    At `seconds` the whole tree is killed, so no grandchild keeps the output pipes or the workspace open;
    the code is then None."""

    proc = subprocess.Popen(cmd, shell=shell, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env,
                            creationflags=SUSPENDED | background_options().get("creationflags", 0),
                            start_new_session=os.name != "nt")
    job = contained(proc)
    resumed(proc)
    try:
        out, _ = proc.communicate(timeout=seconds)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        killed(job) if job else proc.kill()
        out, _ = proc.communicate()   # the pipes close once the tree is dead
        return None, out or ""
    finally:
        terminated(job)


def pytest(ws: Path, args: list[str], seconds: float, deadline: float | None = None) -> dict:
    """`{case name: outcome}` from one pytest run, plus its exit code; cut at the shared wall deadline."""

    junit = ws.parent / f"{ws.name}-{uuid.uuid4().hex[:8]}.xml"
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", "--maxfail=0",
           f"--junitxml={junit}", *args]
    seconds = bounded(seconds, deadline)
    code, output = tree(cmd, ws, seconds, {**python_environment(), "PYTHONDONTWRITEBYTECODE": "1"})
    tail = output[-1500:] if code is not None else f"timed out after {seconds:.0f} s: {output[-1000:]}"
    if code is None:
        junit.unlink(missing_ok=True)
        bounded(seconds, deadline)   # past the shared deadline: a ceiling stop, not a verdict
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


def verdicts(cases: dict[str, str], tests: list[str], code: int | None) -> dict[str, str]:
    """Per selected test: `passed` only when it ran and every one of its cases (parametrized ones too) passed;
    `failed` when any case failed, `skipped` when any was skipped, `missing` when none ran. A run that exits
    non-zero while every selected case passed (an error outside the cases, a timeout) passes none: `error`."""

    out = {}
    for t in tests:
        seen = [o for name, o in cases.items() if name == t or name.startswith(t + "[")]
        out[t] = ("missing" if not seen else "failed" if "failed" in seen
                  else "skipped" if any(o != "passed" for o in seen) else "passed")
    if code != 0 and all(v == "passed" for v in out.values()):
        out = dict.fromkeys(out, "error")
    return out


def check_hidden(ws: Path, cfg: dict, task: dict, seconds: float, deadline: float | None = None) -> dict:
    """Every hidden test copied in (digest checked) and run: `{test: passed|failed|skipped|missing|error}`."""

    out = {}
    for h in task["hidden"]:
        data = hidden_bytes(cfg, task, h)
        target = placed(h["path"])
        (ws / target).parent.mkdir(parents=True, exist_ok=True)
        (ws / target).write_bytes(data)
        got = pytest(ws, [f"{target}::{t}" for t in h["tests"]], seconds, deadline)
        out |= {f"{h['path']}::{t}": v for t, v in verdicts(got["cases"], h["tests"], got["code"]).items()}
    return out


def check_affected(ws: Path, task: dict, start: str, seconds: float, deadline: float | None = None) -> dict:
    """Each affected test file, restored to the merge base, run whole (or by its `-k`): passed or failed."""

    out = {}
    for a in task.get("affected") or []:
        git(ws, "checkout", start, "--", a["path"])
        got = pytest(ws, [a["path"], *(["-k", a["k"]] if a.get("k") else [])], seconds, deadline)
        failed = [n for n, o in got["cases"].items() if o == "failed"]
        out[a["path"]] = {"ok": got["code"] == 0 and not failed, "failed": failed[:20], "code": got["code"],
                          **({"tail": got["tail"]} if got["code"] != 0 else {})}
    return out


def changes(ws: Path, start: str) -> dict[str, str]:
    """`{path: status letter}` of everything the session changed since `start`, committed or not."""
    git(ws, "add", "-A")
    parts = [p for p in git(ws, "diff", "--cached", "--name-status", "--no-renames", "-z", start).split("\0") if p]
    return dict(zip(parts[1::2], parts[0::2]))


def evaluate(ws: Path, cfg: dict, task: dict, start: str, deadline: float | None = None) -> dict:
    """Success: every hidden test passes, the affected files pass, no forbidden path changed. A critical
    regression (fixed before any run, `critical_regression` in the config): a forbidden change, an affected
    test that fails, or a tracked file deleted. Raises `Ceiling` when the shared deadline passes first."""

    changed = changes(ws, start)
    deleted = [p for p, status in changed.items() if status == "D"]
    hidden_paths = sorted(placed(h["path"]) for h in task["hidden"])
    # Untracked files under a forbidden path, ignore rules or not: `ls-files --others` without `--exclude*`.
    unseen = [p for p in git(ws, "ls-files", "--others", "-z", "--", *FORBIDDEN, *hidden_paths).split("\0") if p]
    forbidden = sorted({p for p in changed if p.startswith(FORBIDDEN) or p in hidden_paths} | set(unseen))
    edited = [a["path"] for a in task.get("affected") or [] if a["path"] in changed]
    seconds = cfg["limits"]["test_seconds"]
    hidden = check_hidden(ws, cfg, task, seconds, deadline)
    affected = check_affected(ws, task, start, seconds, deadline)
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
        # Preparation, reference checks and every run invocation count toward the same hours.
        self.elapsed = sum(r.get("elapsed", 0) for r in past if r.get("kind") in ("invocation", "preparation"))
        self.deadline = time.monotonic() + cfg["ceilings"]["hours"] * 3600 - self.elapsed

    def left(self, limit: float) -> float:
        return bounded(limit, self.deadline)

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


def conditions(cfg: dict) -> list[str]:
    """The conditions an experiment runs: its config's `conditions`, A to D unless it names others."""
    return list(cfg.get("conditions") or CONDITIONS)


def cells(cfg: dict) -> list[tuple[str, str, int]]:
    """Every (task, condition, repetition), shuffled by the config's seed."""

    order = [(t["id"], c, r) for t in cfg["tasks"] for c in conditions(cfg) for r in range(1, cfg["repetitions"] + 1)]
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
             stop: threading.Event, kind: str = "cell", scratch: Path | None = None, extra: dict | None = None) -> dict:
    began = time.time()
    ws = Path(scratch or tempfile.gettempdir()) / "spec-views" / cfg["experiment"] / \
        f"{task['id']}-{condition}{rep}-{uuid.uuid4().hex[:6]}"
    row = {"kind": kind, "task": task["id"], "condition": condition, "repetition": rep,
           "implementer": getattr(impl, "name", "unknown"), "model": cfg["implementer"].get("model"),
           "effort": cfg["implementer"].get("effort"), "code_commit": git(ROOT, "rev-parse", "HEAD").strip(),
           "merge_base": task["merge_base"], "spec_version": record["enriched"].get("spec_version"),
           "spec_digest": record["enriched"].get("spec_digest"), "usd_cap": round(cap, 4), "retries": 0,
           **(extra or {})}
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
            ev = evaluate(ws, cfg, task, start, ledger.deadline)
            row.update(ev)
            row["success"] = bool(ev["success"] and not got["cause"])
            if got["cause"] and not row.get("failure"):
                row["failure"] = got["cause"]
            elif not ev["success"]:
                row["failure"] = "verification"
            if kind == "production_baseline" and task.get("gate"):
                row["production_gate"] = gate(ws, task["gate"], cfg["limits"]["gate_seconds"], ledger.deadline)
    except Ceiling:   # the wall ran out before verification finished: no verdict, a ceiling failure
        row.update(success=False, failure="ceiling", cause="ceiling")
    except Exception as exc:   # the harness or the machine failed: recorded, never a pass
        row.update(success=False, infra=True, cause="error", error=f"{type(exc).__name__}: {exc}"[:500],
                   usd_bound=row.get("usd_bound", 0))
    finally:
        remove(ws)
    row["wall_seconds"] = round(time.time() - began, 1)
    ledger.settle(cap, _charged(row))
    return journal(cfg, row)


def gate(ws: Path, command: str, seconds: float, deadline: float | None = None) -> dict:
    began = time.time()
    seconds = bounded(seconds, deadline)
    code, output = tree(command, ws, seconds, python_environment(), shell=True)
    if code is None:
        bounded(seconds, deadline)   # past the shared deadline: a ceiling stop
    return {"command": command, "ok": code == 0, "code": code, "seconds": round(time.time() - began, 1),
            "tail": output[-1500:] if code is not None else "timed out"}


def discriminates(ref: dict) -> bool:
    """The reference separates the answer: every hidden test passed on the merge commit, at least one actually
    failed on the merge base (skipped, missing or an error is no failure), and the affected files passed on both."""

    m, b = ref.get("merge_commit") or {}, ref.get("merge_base") or {}
    hidden_m, hidden_b = m.get("hidden") or {}, b.get("hidden") or {}
    return bool(hidden_m) and all(v == "passed" for v in hidden_m.values()) \
        and any(v == "failed" for v in hidden_b.values()) \
        and all(all(side.get("affected", {}).values()) for side in (m, b))


def admitted(cfg: dict, task: dict, folder: Path) -> dict:
    """The frozen record a task's cells may use, refused unless it is the one committed with the config, its
    requirements still hash to the configured digest, its selection is the configured one, the reference check
    discriminated with the affected files passing on both sides, and B and C are equivalent; every hidden
    test's digest is checked too."""

    tid, root = task["id"], Path(cfg.get("config_root") or ROOT)
    file = folder / f"{tid}.json"
    rel = file.resolve().relative_to(root.resolve()).as_posix()
    if not cfg.get("config_commit") or not unchanged(root, rel, cfg["config_commit"]):
        raise Refused(f"{tid}: frozen record {rel} is not the one committed with the config")
    record = json.loads(file.read_text(encoding="utf-8"))
    spec = record["enriched"]
    if not spec_views.digest(spec.get("requirements") or []) == spec.get("spec_digest") == task["spec_digest"]:
        raise Refused(f"{tid}: frozen spec digest differs from its requirements or the config")
    if record.get("select") != task.get("select"):
        raise Refused(f"{tid}: frozen selection differs from the config")
    if not discriminates(record.get("reference") or {}):   # recomputed from the outcomes, not the stored flag
        raise Refused(f"{tid}: the reference check failed; the hidden tests do not separate the answer")
    if not task.get("equivalent") or not equivalence(record)["ok"]:
        raise Refused(f"{tid}: B and C are not equivalent")
    for h in task["hidden"]:
        hidden_bytes(cfg, task, h)   # each cell checks again
    return record


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
    records = {tid: admitted(cfg, t, folder) for tid, t in tasks.items()}   # all of them before the first cell
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

    watch(ledger, stop)
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
    return finish(cfg, ledger, stop, threads, began, stopped)


def watch(ledger: Ledger, stop: threading.Event) -> None:
    """Set `stop` when the wall ceiling's deadline passes, so running sessions are cut there."""

    def timer():
        while not stop.is_set():
            if time.monotonic() >= ledger.deadline:
                stop.set()
                return
            time.sleep(1)

    threading.Thread(target=timer, daemon=True).start()


def finish(cfg: dict, ledger: Ledger, stop: threading.Event, threads: list, began: float, stopped: str | None) -> dict:
    """Wait for the running cells, then journal the invocation and any ceiling stop."""
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
    parser.add_argument("command", choices=("prepare", "run", "pipeline", "report"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=ROOT / "eval" / "spec-views" / "tasks.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            # Before the config is committed: it reads the draft and fills in the digests.
            from eval.spec_views_prepare import prepare   # it imports this module
            cfg = json.loads(args.config.read_text(encoding="utf-8"))
            cfg["tasks"] = prepare(cfg, args.tasks)
            args.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps([{k: t[k] for k in ("id", "spec_digest", "equivalent", "select")} for t in cfg["tasks"]],
                             indent=1))
            return 0
        cfg = config(args.config)
        cfg["path"] = args.config.as_posix()
        if cfg["kind"] == "main":   # held-out tasks only, and the first task's tests at the code the cells run on
            from eval import spec_views_main as main_report
            main_report.held_out(cfg)
            if args.command in ("run", "pipeline") and not main_report.implementation(cfg)["ok"]:
                raise Refused("the first task's tests fail at this commit: no main-experiment cell runs on it")
            if args.command == "report":
                print(main_report.write(cfg))
                return 0
        if args.command in ("run", "pipeline"):
            if args.command == "pipeline":
                from eval.spec_views_pipeline import run as pipeline
                print(json.dumps(pipeline(cfg)))
            else:
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
