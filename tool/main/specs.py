"""specs — the task spec, from the conversation that settles it to the pull request.

A spec is one JSON file, `raw/specs/<repo>/<id>.json` in the hub: this
machine's operating state, never committed to the target repository. What
reaches the target is only what rides in the pull request. This module is the
file's only owner — reading, checking, saving and moving its state happen here
and nowhere else.

The loop in this stage: the `next` focus ends in a `spec` block, `[시작]` makes
the worktree and a write session whose system prompt is the spec, the
session's `done-report` is believed only after the gate passes again here, and
then the pull request goes up. What reaches the screen is read by a person and
stays Korean.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import translate
from common import worktree_home
from session_state import active_page, decisions, plans
from wiki import slots_for
from workspace import TASK, create, folder_for

from . import channels, query, work
from .query import ROOT, _lock, current_repo, hold, project

SPECS = ROOT / "raw" / "specs"
SPEC_PROMPT = (ROOT / "tool/prompts/work-spec.md").read_text(encoding="utf-8")

GATE_SECONDS = 20 * 60
TRANSLATE_SECONDS = 60   # the pull request's prose into Korean, one request
TAIL = 80          # lines of the gate's output kept on the spec
MAX_PLANS = 3      # materials for the candidates
MAX_ROWS = 10
MAX_PRS = 10
MAX_WARNINGS = 10
SLOTS = ("gate_cmd", "review_dir", "scratch_dirs", "live_cmd", "server_stop")   # what `[연결]` writes

# Every state a spec can be in. The list is the stage 3 plan's table; this
# stage moves through the first three, `머지됨` and `멈춤`, and the review loop
# of stage 4 uses the rest.
STATE = re.compile(r"정리됨|작업 중|PR #\d+|리뷰 대기|리뷰 R\d+|고치는 중 R\d+|머지 가능|머지 대기|머지됨|멈춤")

# A named block at the end of an answer: a fenced block whose info string is
# its name. A block cut off before its closing fence is not one.
BLOCK = re.compile(r"^```(candidates|choices|spec|done-report)[ \t]*\r?\n(.*?)^```[ \t]*$\n?", re.M | re.S)

# `path:line` or `path:line-line` as an answer cites it.
LINE = re.compile(r":\d+(?:[-–]\d+)?$")

router = APIRouter()

# Every read-modify-write of a spec file. Apart from `query._lock`, which the
# holds take: a gate that runs for minutes never waits on a hold.
_files = threading.RLock()


# -- The file ---------------------------------------------------------------

def file_of(repo: str, sid: str) -> Path:
    """`sid` came from the screen: only a task name, never a path."""

    if not TASK.fullmatch(sid):
        raise HTTPException(404, "그런 명세가 없다")
    return SPECS / repo / f"{sid}.json"


def load(repo: str, sid: str) -> dict | None:
    try:
        return json.loads(file_of(repo, sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(spec: dict) -> None:
    """Written whole to a temporary file and swapped in, so a reader never
    sees half a spec. Every save is told to the screens: the loop moves specs
    with nobody asking."""

    file = file_of(spec["repo"], spec["id"])
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(file)
    publish(spec)


def summary(spec: dict) -> dict:
    """One spec's place in the loop, as the rail and the notifications read it."""

    counted = [r for r in spec.get("rounds") or [] if not r.get("stale")]
    return {"repo": spec["repo"], "id": spec["id"], "state": spec["state"], "stopped": spec.get("stopped"),
            "pr": (spec.get("pr") or {}).get("number"), "round": len(counted), "worktree": spec.get("worktree"),
            "queued": spec.get("queued"),
            "waiting": bool(spec.get("worktree")) and work.waiting(spec["worktree"])}


def publish(spec: dict) -> None:
    work.feed.put({"kind": "spec", **summary(spec)})


def branch_of(spec: dict) -> str:
    """The pull request's head branch: the spec's own name, unless the spec
    was made from a pull request that came with its own branch."""

    return (spec.get("pr") or {}).get("branch") or spec.get("branch") or spec["id"]


def moved(spec: dict, state: str, **fields) -> dict:
    if not STATE.fullmatch(state):
        raise ValueError(f"그런 상태가 없다: {state}")
    spec.update(state=state, **fields)
    spec["history"].append({"ts": time.time(), "state": state})
    return spec


def update(repo: str, sid: str, **fields) -> dict | None:
    with _files:
        spec = load(repo, sid)
        if spec is not None:
            spec.update(fields)
            save(spec)
        return spec


def listing(repo: str) -> list[dict]:
    """The repository's specs, newest first. A dropped one is not listed."""

    found = []
    for file in (SPECS / repo).glob("*.json"):
        spec = load(repo, file.stem) if TASK.fullmatch(file.stem) else None
        if spec is not None:
            found.append(spec)
    return sorted(found, key=lambda s: s["history"][0]["ts"], reverse=True)


def owner(path: Path) -> dict | None:
    """The spec whose worktree `path` is. The id is the worktree's name, and
    the repository's name is its `<repo>-worktrees` folder's."""

    if not TASK.fullmatch(path.name):
        return None
    spec = load(path.parent.name.removesuffix("-worktrees"), path.name)
    return spec if spec and spec.get("worktree") and Path(spec["worktree"]) == path else None


# -- Checking what a person settles -----------------------------------------

def gate_of(repo: Path) -> str:
    """The adapter's `gate_cmd`, or empty: a repository not connected yet."""

    try:
        return slots_for(repo.name, repo).get("gate_cmd", "").strip()
    except (OSError, ValueError):
        return ""


def slugged(raw) -> str:
    """`raw` as a task name — lowercase, anything else `-` — or empty."""

    return folder_for(str(raw or ""))


def taken(repo: Path, name: str) -> bool:
    """A spec, a worktree folder or a branch already has that name. The branch
    too: `git worktree add -b` refuses one, and `[시작]` would fail late."""

    branch = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                            capture_output=True, timeout=10)
    return file_of(repo.name, name).exists() or (worktree_home(repo) / name).exists() or branch.returncode == 0


def unique(repo: Path, base: str) -> str:
    name, n = base, 1
    while taken(repo, name):
        n += 1
        tail = f"-{n}"
        name = base[:64 - len(tail)].rstrip("-") + tail
    return name


def strings(value, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"`{what}` 는 글줄의 목록이어야 한다")
    return [v.strip() for v in value if v.strip()]


def inside(repo: Path, ref: str) -> bool:
    target = (repo / LINE.sub("", ref)).resolve()
    return repo.resolve() in target.parents


def fields(repo: Path, block, gate: str) -> dict:
    """The fields a person settles, checked. `ValueError` says what is wrong.

    The gate is always the first done item: checking that the agent put it
    there would leave a way to miss it, so it cannot be left out at all."""

    if not isinstance(block, dict):
        raise ValueError("명세는 JSON 객체여야 한다")
    goal = block.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("`goal` 이 비었다")
    grounds = block.get("grounds") or {}
    if not isinstance(grounds, dict):
        raise ValueError("`grounds` 는 객체여야 한다")
    decided = []
    for d in block.get("decisions") or []:
        if not isinstance(d, dict) or not isinstance(d.get("what"), str) or not d["what"].strip():
            raise ValueError("`decisions` 의 항목마다 `what` 이 있어야 한다")
        decided.append({k: " ".join(str(d.get(k) or "").split()) for k in ("what", "why", "rejected")})
    listed = {k: strings(grounds.get(k), f"grounds.{k}") for k in ("pages", "files", "rules")}
    # A path outside the repository grounds nothing here. One inside that
    # does not exist is kept and shown as missing.
    listed["files"] = [f for f in listed["files"] if inside(repo, f)]
    return {
        "goal": " ".join(goal.split()),
        "out": strings(block.get("out"), "out"),
        "done": [gate] + [d for d in strings(block.get("done"), "done") if d != gate],
        "grounds": listed,
        "decisions": decided,
    }


def plan_row(repo: Path, plan) -> dict | None:
    """`{path, row}` when a table row with that first cell stands in that plan.
    Only `docs/plans/` and `.wiki/plan-active.md`; anything else is no row."""

    if not isinstance(plan, dict):
        return None
    rel, row = str(plan.get("path") or ""), str(plan.get("row") or "").strip()
    if not rel or not row:
        return None
    top = repo.resolve()
    file = (top / rel).resolve()
    if not (file == top / ".wiki" / "plan-active.md" or (top / "docs" / "plans") in file.parents):
        return None
    if file.suffix != ".md" or not file.is_file():
        return None
    if not re.search(rf"^\|\s*{re.escape(row)}\s*\|", file.read_text(encoding="utf-8"), re.M):
        return None
    return {"path": file.relative_to(top).as_posix(), "row": row}


def row_done(path: Path, plan: dict, n: int) -> bool:
    """Does the plan row's status cell say `완료 — PR #n` in the worktree's
    committed HEAD? Read from git, not the file: an edit left uncommitted is
    not in the pull request."""

    shown = sh(["git", "show", f"HEAD:{plan['path']}"], path)
    return not shown.returncode and re.search(
        rf"^\|\s*{re.escape(plan['row'])}\s*\|.*\|\s*완료 — PR #{n}\s*\|\s*$", shown.stdout, re.M) is not None


def missing(repo: Path, spec: dict) -> list[str]:
    return [f for f in spec["grounds"]["files"] if not (repo / LINE.sub("", f)).is_file()]


def approved(spec: dict) -> dict | None:
    """The round a merge is bound to: the last one counted, when it allowed."""

    counted = [r for r in spec.get("rounds") or [] if not r.get("stale")]
    return counted[-1] if counted and counted[-1]["verdict"] == "allow" else None


def view(repo: Path, spec: dict) -> dict:
    allowed = approved(spec)
    return {**spec, "missing": missing(repo, spec), "approved": allowed["head"] if allowed else None,
            "waiting": bool(spec.get("worktree")) and work.waiting(spec["worktree"])}


# -- Blocks -----------------------------------------------------------------

def blocks(text: str) -> tuple[str, list[dict]]:
    """The answer without its blocks, and each block as `{name, value}` or
    `{name, error}`. Taken out so the overlay never translates JSON."""

    found = []
    for m in BLOCK.finditer(text):
        try:
            found.append({"name": m.group(1), "value": json.loads(m.group(2))})
        except ValueError as exc:
            found.append({"name": m.group(1), "error": f"JSON 이 아니다 — {exc}"})
    return BLOCK.sub("", text).rstrip(), found


def _shape(name: str, value) -> None:
    """`ValueError` when a `candidates` or `choices` block lacks a field."""

    if name == "candidates":
        if not isinstance(value, list) or not value:
            raise ValueError("`candidates` 는 비지 않은 목록이어야 한다")
        for c in value:
            if not isinstance(c, dict) or not isinstance(c.get("title"), str) or not c["title"].strip():
                raise ValueError("후보마다 `title` 이 있어야 한다")
    elif name == "choices":
        if not isinstance(value, dict) or not isinstance(value.get("question"), str):
            raise ValueError("`choices` 에 `question` 이 없다")
        options = value.get("options")
        if not isinstance(options, list) or not options or not all(
                isinstance(o, dict) and isinstance(o.get("label"), str) and o["label"].strip() for o in options):
            raise ValueError("`choices` 의 `options` 마다 `label` 이 있어야 한다")


def card(repo: Path, block, gate: str, source: dict) -> str:
    """One `spec` block as a card on disk; its id.

    The same slug from the same conversation replaces its card while that has
    not started. Otherwise the name gets `-2`, `-3`."""

    made = fields(repo, block, gate)
    base = slugged(block.get("slug"))
    if not base:
        raise ValueError("`slug` 가 비었거나 쓸 수 있는 글자가 없다")
    source = {**source, "plan": plan_row(repo, block.get("plan"))}
    with _files:
        old = load(repo.name, base)
        if old and old["state"] == "정리됨" and source.get("session") \
                and old["source"].get("session") == source["session"]:
            old.update(made, rev=old["rev"] + 1, source=source)
            save(old)
            return base
        sid = unique(repo, base)
        save({"id": sid, "repo": repo.name, "rev": 1, **made, "source": source,
              "state": "정리됨", "stopped": None, "worktree": None, "pr": None,
              "report": None, "gate": None, "fault": None,
              "history": [{"ts": time.time(), "state": "정리됨"}]})
        return sid


def answered(repo: Path, found: list[dict], source: dict) -> list[dict]:
    """The blocks of a `next` answer as the screen draws them. A `spec` block
    becomes cards on disk and is sent as their ids, one entry per spec."""

    gate = gate_of(repo)
    out = []
    for block in found:
        name = block["name"]
        if name == "done-report" or "error" in block:
            if "error" in block:
                out.append(block)
            continue
        if name != "spec":
            try:
                _shape(name, block["value"])
                out.append(block)
            except ValueError as exc:
                out.append({"name": name, "error": str(exc)})
            continue
        value = block["value"]
        for one in value if isinstance(value, list) else [value]:
            try:
                if not gate:
                    raise ValueError("연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
                out.append({"name": "spec", "id": card(repo, one, gate, source)})
            except ValueError as exc:
                out.append({"name": "spec", "error": str(exc)})
    return out


# -- What the candidates are made of ----------------------------------------

def sh(args: list[str], cwd: Path, timeout: float = 60) -> subprocess.CompletedProcess:
    """Every `git` and `gh` this module runs. One seam, so a test can stand in
    for GitHub and the remote."""

    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def said(done: subprocess.CompletedProcess) -> str:
    """The first line of what a failed command said."""

    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return lines[0] if lines else f"종료 코드 {done.returncode}"


def part(title: str, read) -> str:
    """One section of the materials. A source that cannot be read says why in
    one line, and the rest go on."""

    try:
        body = read() or "(none)"
    except Exception as exc:
        body = f"(could not read: {exc})"
    return f"## {title}\n\n{body}"


def _plans(repo: Path) -> str:
    out = [f"### `{path.relative_to(repo).as_posix()}`\n" + "\n".join(f"- {row}" for row in rows)
           for path, rows in plans(repo, MAX_PLANS, MAX_ROWS)]
    page, stale = active_page(repo)
    if page:
        out.append("### `.wiki/plan-active.md`" + (" (may be stale)" if stale else "") + f"\n{page}")
    return "\n\n".join(out)


def _prs(repo: Path) -> str:
    done = sh(["gh", "pr", "list", "--state", "open", "--limit", str(MAX_PRS),
               "--json", "number,title,headRefName,isDraft,reviewDecision,url"], repo, 30)
    if done.returncode:
        raise RuntimeError(said(done))
    return "\n".join(
        f"- #{r['number']} {r['title']} · `{r['headRefName']}`" + (" · draft" if r.get("isDraft") else "")
        + (f" · {r['reviewDecision']}" if r.get("reviewDecision") else "") + f" · {r['url']}"
        for r in json.loads(done.stdout))


def _warnings(repo: Path) -> str:
    if repo.resolve() == channels.WIKI.resolve():
        import lint

        found = lint.check()[2]
    else:
        import repo_lint

        found = repo_lint.check(repo)
    return "\n".join(f"- {kind}: {message}" for kind, message in found[:MAX_WARNINGS])


def materials(repo: Path) -> str:
    """The first message of `[후보 내기]`: what the server gathered, whole. It
    is also what the record keeps, so a resumed CLI saw the same."""

    return "\n\n".join([
        "Propose three to five next tasks for this repository from these materials.",
        part("Plans with rows left", lambda: _plans(repo)),
        part("Open pull requests", lambda: _prs(repo)),
        part("Recent decisions", lambda: "\n".join(f"- {t} — {w}" if w else f"- {t}" for t, w in decisions(repo))),
        part("Lint warnings", lambda: _warnings(repo)),
        part("Review P2 left", lambda: "\n".join(f"- `{s['id']}`: {p}" for s in listing(repo.name)
                                                 for p in s.get("p2") or [])),
        part("Empty adapter slots", lambda: _slots(repo)),
    ])


def _slots(repo: Path) -> str:
    """The slots `[연결]` could not guess. Filled in a worktree, they reach
    the original through a pull request."""

    if not (repo / ".wiki/adapter.toml").is_file():
        return ""
    values = slots_for(repo.name, repo)
    return "\n".join(f"- `{k}` in `.wiki/adapter.toml`" for k in SLOTS if not values.get(k, "").strip())


def told(repo: Path, spec: dict, text: str) -> None:
    """A result row in the `next` conversation, where the spec came from."""

    query.remember("next", "result", text, repo=repo, spec=spec["id"])


# -- The screen -------------------------------------------------------------

class Edit(BaseModel):
    rev: int
    goal: str
    out: list[str] = []
    done: list[str] = []
    slug: str = ""


class Start(BaseModel):
    model: str = ""
    effort: str = ""


def noticed(repo: Path, spec: dict) -> dict:
    """A pull request merged on GitHub moves its spec to `머지됨`, once."""

    pr = spec.get("pr")
    if not pr or spec["state"] == "머지됨":
        return spec
    if spec["state"] == "머지 대기":
        # Merged through `[머지]`: the loop's table reads what became of it.
        from . import loop

        loop.landed(repo, spec)
        return load(repo.name, spec["id"]) or spec
    try:
        done = sh(["gh", "pr", "view", str(pr["number"]), "--json", "state,mergedAt"], repo, 30)
        state = json.loads(done.stdout).get("state") if not done.returncode else ""
    except (OSError, ValueError, subprocess.TimeoutExpired):
        state = ""
    if state != "MERGED":
        return spec
    with _files:
        spec = load(repo.name, spec["id"]) or spec
        if spec["state"] == "머지됨":
            return spec
        save(moved(spec, "머지됨"))
    told(repo, spec, f"PR #{pr['number']} 머지됨 — {spec['goal']}")
    return spec


@router.get("/api/specs")
def specs() -> dict:
    with _lock:
        name, repo = project(), current_repo()
    return {"project": name, "gate": gate_of(repo),
            "specs": [view(repo, noticed(repo, s)) for s in listing(name)]}


@router.put("/api/specs/{sid}")
def edit(sid: str, body: Edit) -> dict:
    """The card's edit. Only a spec not started yet; a stale `rev` is 409."""

    repo = current_repo()
    gate = gate_of(repo)
    if not gate:
        raise HTTPException(409, "연결 먼저 — 이 저장소의 adapter 에 `gate_cmd` 가 없다")
    with _files:
        spec = load(repo.name, sid)
        if spec is None:
            raise HTTPException(404, "그런 명세가 없다")
        if spec["rev"] != body.rev:
            raise HTTPException(409, f"다른 곳에서 먼저 고쳤다 — 지금은 판 {spec['rev']}")
        if spec["state"] != "정리됨":
            raise HTTPException(409, "시작한 명세는 고치지 않는다. 목표가 바뀌면 새 명세다")
        try:
            made = fields(repo, {**spec, "goal": body.goal, "out": body.out, "done": body.done}, gate)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        new = sid
        if body.slug and body.slug != sid:
            base = slugged(body.slug)
            if not base:
                raise HTTPException(400, "이름은 소문자·숫자·- 만, 64자까지")
            new = base if base == sid else unique(repo, base)
        spec.update(made, id=new, rev=spec["rev"] + 1)
        save(spec)
        if new != sid:
            file_of(repo.name, sid).unlink()
    return view(repo, spec)


def aside(repo: str, sid: str) -> None:
    """Set aside, not deleted: `raw/specs/<repo>/dropped/`."""

    away = SPECS / repo / "dropped" / f"{sid}.{time.time_ns()}.json"
    away.parent.mkdir(parents=True, exist_ok=True)
    file_of(repo, sid).replace(away)


@router.post("/api/specs/{sid}/drop")
def drop(sid: str) -> dict:
    """A started spec has a worktree and maybe a pull request, and is not
    dropped until merged — or until a person deletes its worktree (`forsaken`)."""

    repo = current_repo()
    with _files:
        spec = load(repo.name, sid)
        if spec is None:
            raise HTTPException(404, "그런 명세가 없다")
        if spec["state"] not in ("정리됨", "머지됨"):
            raise HTTPException(409, "시작한 명세는 머지된 뒤에 버린다")
        aside(repo.name, sid)
    return {"ok": True}


def forsaken(path: Path) -> None:
    """A person deleted the worktree of an unmerged spec: the task goes with
    it. Kept, the rail went on showing a task whose worktree was gone. A
    pull request it opened stays on GitHub and can go into a loop from the
    list again. A merged spec stays: it is off the rail already, and its P2
    are material for the next candidates."""

    with _files:
        spec = owner(path)
        if spec is None or spec["state"] == "머지됨":
            return
        # The repository as `owner` read it: from the path. Gone already is
        # nothing to set aside.
        repo = path.parent.name.removesuffix("-worktrees")
        if not file_of(repo, spec["id"]).exists():
            return
        aside(repo, spec["id"])
    publish(spec)


@router.post("/api/specs/{sid}/start")
def start(sid: str, body: Start) -> dict:
    """`[시작]`: the worktree first, then the spec moves, then the first turn.

    In that order, a worktree that could not be made leaves the spec exactly
    as it was; the other way round, a failure had to be rolled back."""

    with _lock:
        repo = current_repo()
        release = hold(work._busy, _lock, str(worktree_home(repo) / sid), "그 작업트리를 다른 요청이 쓰고 있다")
    try:
        with _files:
            spec = load(repo.name, sid)
            if spec is None:
                raise HTTPException(404, "그런 명세가 없다")
            if spec["state"] != "정리됨":
                raise HTTPException(409, f"이미 시작했다 — {spec['state']}")
            try:
                path = create(repo, sid)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
            # The model is kept for the turns the loop sends this worktree.
            save(moved(spec, "작업 중", worktree=str(path), cell={"model": body.model, "effort": body.effort}))
        # Made: from here it is a turn, and a switch no longer waits for it.
        release.held.kind = "turn"
        run = work.begin(path, work.session(path, body.model, body.effort), "Start.", release)
    except BaseException:
        release()
        raise
    return {"path": str(path), "turn": run.turn, "session_id": run.session_id}


# -- The work session -------------------------------------------------------

def system(path: Path) -> str:
    """The system prompt of a write session in `path`: the spec, when a spec
    owns that worktree. Read whenever a session is made, so one made again
    after a restart still has it."""

    spec = owner(path)
    if spec is None:
        return ""
    shown = {k: spec[k] for k in ("id", "goal", "out", "done", "grounds", "decisions")}
    return SPEC_PROMPT.rstrip() + "\n\n```json\n" + json.dumps(shown, ensure_ascii=False, indent=2) + "\n```\n"


def note(run, text: str) -> None:
    """A line in the turn's events, as a tool line: the screen shows it and the
    record keeps it."""

    run.put({"kind": "tool", "text": text, "meta": {}, "session_id": run.chat.id, "parent_id": run.chat.parent_id})


def kill(proc: subprocess.Popen) -> None:
    """The gate's whole tree. The shell alone dies and leaves its test runner."""

    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGKILL)


def gate(cmd: str, cwd: Path, halt: threading.Event) -> tuple[int | None, str, str]:
    """Run the gate in `cwd`, a shell string as the adapter wrote it:
    `(exit code, output, why it was cut)`. A stop of the turn stops it too,
    and a stop that came while a fast gate ran still cuts it: the stop is
    read after the gate ends, not only while it waits."""

    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            start_new_session=os.name != "nt")
    deadline, cut = time.monotonic() + GATE_SECONDS, ""
    while True:
        try:
            out, _ = proc.communicate(timeout=1)
            cut = cut or ("사람이 멈춤" if halt.is_set() else "")
            return (None if cut else proc.returncode), out, cut
        except subprocess.TimeoutExpired:
            if cut:
                continue
            if halt.is_set():
                cut = "사람이 멈춤"
            elif time.monotonic() > deadline:
                cut = f"{GATE_SECONDS // 60}분 안에 끝나지 않았다"
            if cut:
                kill(proc)


def judge(path: Path, cmd: str, halt: threading.Event, noted=lambda text: None) -> dict:
    """The server's own check of a done report: nothing uncommitted, and the
    gate passes again in the worktree. What the agent said is not evidence.
    The review loop checks every head it sends for review the same way."""

    status = sh(["git", "status", "--porcelain"], path)
    head = sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    if status.returncode or status.stdout.strip():
        reason = "커밋 안 된 변경" if not status.returncode else f"git status 실패 — {said(status)}"
        return {"ok": False, "reason": reason, "cmd": cmd, "tail": status.stdout.strip(), "head": head,
                "ts": time.time()}
    noted(f"게이트 · {cmd}")
    code, out, cut = gate(cmd, path, halt)
    ok = code == 0
    reason = "" if ok else cut or f"게이트가 {code} 로 끝났다"
    return {"ok": ok, "reason": reason, "cmd": cmd, "code": code,
            "tail": "\n".join(out.splitlines()[-TAIL:]), "head": head, "ts": time.time()}


def valid(items) -> bool:
    return isinstance(items, list) and bool(items) and all(
        isinstance(i, dict) and isinstance(i.get("item"), str) and isinstance(i.get("pass"), bool) for i in items)


def body_of(spec: dict) -> str:
    """The pull request's body. The section names are the ones
    `harvest.record` reads: `변경 요약` becomes the record's what, and
    `변경 이유` its why."""

    lines = ["## 변경 요약", "", spec["goal"], ""]
    if spec["decisions"]:
        lines += ["## 변경 이유", ""]
        for d in spec["decisions"]:
            lines.append(f"- {d['what']}" + (f" — {d['why']}" if d["why"] else "")
                         + (f" (버린 것: {d['rejected']})" if d["rejected"] else ""))
        lines.append("")
    lines += ["## 확인", ""]
    lines += [f"- [x] {i['item']}" + (f" — {i['evidence']}" if i.get("evidence") else "") for i in spec["report"]]
    last = (spec["gate"].get("tail") or "").splitlines()[-1:] or [""]
    lines += [f"- [x] 서버가 작업트리에서 게이트를 다시 돌림 — `{spec['gate']['cmd']}` · {last[0]}".rstrip(" ·"), ""]
    grounds = spec["grounds"]
    cited = ", ".join(f"`{g}`" for g in grounds["pages"] + grounds["files"]) or "없음"
    lines += ["## 명세", "", f"`raw/specs/{spec['repo']}/{spec['id']}.json` · 근거: {cited}", ""]
    return "\n".join(lines)


def korean(spec: dict) -> dict:
    """The spec as the pull request shows it. The agents wrote it in English;
    GitHub is read by a person, so the prose goes up in Korean. Commands and
    their output (`evidence`) stay as they ran. Translation fails open: a
    line it could not translate goes up as it was."""

    kinds = ("what", "why", "rejected")
    texts = [spec["goal"], *(d[k] for d in spec["decisions"] for k in kinds), *(i["item"] for i in spec["report"])]
    done = iter(translate.translate(texts, translate.EN_KO, time.monotonic() + TRANSLATE_SECONDS))
    return {**spec, "goal": next(done), "decisions": [{k: next(done) for k in kinds} for _ in spec["decisions"]],
            "report": [{**i, "item": next(done)} for i in spec["report"]]}


def opened(repo: Path, path: Path, run, spec: dict):
    """Push, and open the pull request as the person's `gh`. The next turn to
    start, when the spec came from a plan row that now says done.

    A stop of the turn is read right before each thing that leaves this
    machine, the push and the pull request: the gate passing earlier is no
    leave to publish after a person said stop."""

    sid, branch = spec["id"], branch_of(spec)
    if run.halt.is_set():
        return failed(run, spec, "사람이 멈춤 — push 하지 않았다")
    note(run, f"push · origin {branch}")
    pushed = sh(["git", "push", "-u", "origin", branch], path, 120)
    if pushed.returncode:
        return failed(run, spec, f"push 실패 — {said(pushed)}")
    # A spec sent back by `[다시 PR]` goes to the base it was reviewed for.
    base = subprocess.CompletedProcess([], 0, spec["base"], "") if spec.get("base") else \
        sh(["gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"], path, 30)
    if base.returncode or not base.stdout.strip():
        return failed(run, spec, f"기본 브랜치를 모른다 — {said(base)}")
    if run.halt.is_set():
        return failed(run, spec, "사람이 멈춤 — push 는 했고 PR 은 만들지 않았다")
    note(run, "PR 을 한국어로 옮기는 중")
    shown = korean(spec)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(body_of(shown))
    try:
        made = sh(["gh", "pr", "create", "--base", base.stdout.strip(), "--head", branch,
                   "--title", shown["goal"], "--body-file", fh.name], path, 120)
    finally:
        os.unlink(fh.name)
    number = re.search(r"/pull/(\d+)", made.stdout)
    if made.returncode or not number:
        return failed(run, spec, f"PR 을 만들지 못했다 — {said(made)}")
    n, url = int(number.group(1)), made.stdout.strip().splitlines()[-1]
    plan = spec["source"].get("plan")
    with _files:
        spec = load(spec["repo"], sid)
        save(moved(spec, f"PR #{n}", fault=None, plan_commit="asked" if plan else None,
                   pr={"number": n, "url": url, "base": base.stdout.strip(), "head": spec["gate"].get("head", ""),
                       "branch": branch}))
    note(run, f"PR #{n} · {url}")
    told(repo, spec, f"PR #{n} — {spec['goal']}. 완료 조건 {len(spec['done'])}개 통과")
    if not plan:
        return lambda: reviewed(spec)
    # The row says the pull request's number, and that exists only now. This
    # commit is in the pull request too, so the review sees it.
    text = (f"PR #{n} is up. In `{plan['path']}`, change the status cell of the table row whose first cell "
            f"is `{plan['row']}` to `완료 — PR #{n}`, commit that one change, and stop. Change nothing else.")
    return lambda: again(path, run.chat, text, spec)


def reviewed(spec: dict) -> None:
    """The pull request is whole — the plan row's commit pushed, when there is
    one — so the review loop takes it. Started before that, the first round
    read the head from before the row's commit."""

    from . import loop  # `loop` imports this module

    loop.kick(spec["repo"], spec["id"])


def failed(run, spec: dict, reason: str) -> None:
    note(run, reason)
    update(spec["repo"], spec["id"], fault=reason)
    return None


def again(path: Path, chat, text: str, spec: dict) -> None:
    """The plan row's turn, once the turn that opened the pull request has let
    go of the worktree. A person who got there first keeps it."""

    try:
        release = hold(work._busy, _lock, str(path), "", kind="turn")
    except HTTPException:
        update(spec["repo"], spec["id"], fault="계획 행을 고칠 턴을 보내지 못했다 — 작업트리가 쓰이고 있다")
        return
    try:
        work.begin(path, chat, text, release)
    except BaseException:
        release()
        raise


def check(path: Path, run, final: str):
    """What an ended work turn means for the spec that owns the worktree.

    Called from the turn's own thread, still holding the worktree, so nothing
    else writes while the gate runs. Returns the next turn to start, if any.
    Never raises: a broken check is a line in the turn, not a broken turn."""

    try:
        return _check(path, run, final)
    except Exception as exc:
        note(run, f"명세 확인이 깨졌다 — {type(exc).__name__}: {exc}")
        return None


def _check(path: Path, run, final: str):
    spec = owner(path)
    repo = channels.repo_for(spec["repo"]) if spec else None
    if spec is None or repo is None:
        return None
    if spec["state"].startswith("PR #") and spec.get("plan_commit") == "asked":
        # Any turn that ends here is not the row's commit: a person's turn may
        # have got there first, or the agent answered without the edit. Only
        # a committed row that says so is pushed and closes the follow-up.
        plan = spec["source"]["plan"]
        if not row_done(path, plan, spec["pr"]["number"]):
            return failed(run, spec, f"계획 행 `{plan['path']}` {plan['row']} 이 커밋된 HEAD 에서 아직 "
                                     f"`완료 — PR #{spec['pr']['number']}` 가 아니다")
        if run.halt.is_set():
            return failed(run, spec, "사람이 멈춤 — 계획 행 커밋을 push 하지 않았다")
        pushed = sh(["git", "push", "origin", branch_of(spec)], path, 120)
        if pushed.returncode:
            return failed(run, spec, f"계획 행 커밋의 push 실패 — {said(pushed)}")
        note(run, f"push · 계획 행 `{plan['path']}` {plan['row']}")
        update(spec["repo"], spec["id"], plan_commit="pushed", fault=None)
        return lambda: reviewed(spec)
    if spec["state"] != "작업 중":
        return None
    report = next((b for b in blocks(final)[1] if b["name"] == "done-report"), None)
    if report is None:
        return None
    if "error" in report or not valid(report.get("value")):
        return failed(run, spec, f"완료 보고를 읽지 못했다 — {report.get('error') or '항목마다 item 과 pass 가 있어야 한다'}")
    items = report["value"]
    spec = update(spec["repo"], spec["id"], report=items)
    if len(items) < len(spec["done"]) or not all(i["pass"] for i in items):
        return failed(run, spec, "완료 보고에 통과하지 못했거나 빠진 항목이 있다. 판정하지 않는다")
    verdict = judge(path, spec["done"][0], run.halt, lambda text: note(run, text))
    spec = update(spec["repo"], spec["id"], gate=verdict)
    if not verdict["ok"]:
        return failed(run, spec, f"판정 실패 — {verdict['reason']}")
    note(run, "게이트 통과")
    return opened(repo, path, run, spec)
