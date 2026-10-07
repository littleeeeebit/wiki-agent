"""specs — the task spec, from the conversation that settles it to the pull request.

A spec is one JSON file, `raw/specs/<repo>/<id>.json` in the hub: this
machine's operating state, never committed to the target repository. What
reaches the target is only what rides in the pull request. This module is the
file's only owner — reading, checking, saving and moving its state happen here
and nowhere else.

The loop in this stage: the `next` focus ends in a `spec` block, `[시작]` makes
the task branch and a write session whose system prompt is the spec, the
session's `done-report` is believed only after the gate passes again here, and
then the pull request goes up. What reaches the screen is read by a person and
stays Korean.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from fnmatch import fnmatchcase
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import debt
import translate
from common import errorlog, python_environment, worktree_home
from session_state import active_page, decisions, plans, steps_block
from wiki import adapter_path, slots_for
from workspace import TASK, create, folder_for

from . import channels, query, review_contract, work
from .decisions import extra_check, recommend, registered
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
# The review criteria a spec asks for (`docs/plans/reliability/6-review-profiles.md`).
PROFILES = ("plan", "code", "mixed")
PROFILE_VERSION = 1

# Every state a spec can be in. The list is the stage 3 plan's table; this
# stage moves through the first three, `머지됨` and `멈춤`, and the review loop
# of stage 4 uses the rest.
STATE = re.compile(r"정리됨|작업 중|PR #\d+|리뷰 대기|리뷰 R\d+|고치는 중 R\d+|머지 가능|머지 대기|머지됨|멈춤")

# A named block at the end of an answer: a fenced block whose info string is
# its name. A block cut off before its closing fence is not one.
BLOCK = re.compile(r"^```(candidates|choices|spec|spec-update|done-report)[ \t]*\r?\n(.*?)^```[ \t]*$\n?", re.M | re.S)

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
    # Windows can refuse a read during replacement; it is not a deleted task.
    with _files:
        try:
            return json.loads(file_of(repo, sid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            errorlog.record("task-read", exc, repo=repo, spec=sid)
            return None


def save(spec: dict) -> None:
    """Written whole to a temporary file and swapped in, so a reader never
    sees half a spec. Every save is told to the screens: the loop moves specs
    with nobody asking."""

    with _files:
        file = file_of(spec["repo"], spec["id"])
        birth = spec["history"][0]["ts"]
        current = load(spec["repo"], spec["id"])
        if current is not None and current["history"][0]["ts"] != birth:
            raise HTTPException(410, "이 이름으로 새 작업이 만들어졌다. 이전 작업은 저장할 수 없다")
        if current is None:
            for archived in (file.parent / "dropped").glob(f"{spec['id']}.*.json"):
                if json.loads(archived.read_text(encoding="utf-8"))["history"][0]["ts"] == birth:
                    raise HTTPException(410, "삭제한 작업은 다시 저장할 수 없다")
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_suffix(".tmp")
        temporary.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        # Windows readers can temporarily prevent the atomic swap.
        for attempt in range(40):
            try:
                temporary.replace(file)
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.025)
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


def merge_progress(repo: str, sid: str, stage: str, state: str = "running", *, reset: bool = False) -> None:
    """Persist merge stages through the existing spec feed, without authorizing merge."""
    with _files:
        spec = load(repo, sid)
        if spec is None:
            return
        previous = {} if reset else spec.get("merge_progress") or {}
        now = time.time()
        steps = list(previous.get("steps") or [])
        if not steps or steps[-1]["text"] != stage:
            steps.append({"text": stage, "ts": now})
        update(repo, sid, merge_progress={"stage": stage, "state": state, "started_at": previous.get("started_at", now),
                                         "updated_at": now, "steps": steps[-20:]})


def listing(repo: str) -> list[dict]:
    """The repository's specs, newest first. A dropped one is not listed."""

    found = []
    for file in (SPECS / repo).glob("*.json"):
        spec = load(repo, file.stem) if TASK.fullmatch(file.stem) else None
        if spec is not None:
            found.append(spec)
    return sorted(found, key=lambda s: s["history"][0]["ts"], reverse=True)


def owner(path: Path) -> dict | None:
    """Resolve ownership by checkout and branch, including legacy worktrees."""

    path = path.resolve()
    if TASK.fullmatch(path.name):
        spec = load(path.parent.name.removesuffix("-worktrees"), path.name)
        if spec and spec.get("worktree") and Path(spec["worktree"]).resolve() == path:
            return spec
    # Cleanup still needs a removed legacy tree's stored owner. Only branch
    # ownership requires a live checkout for its Git lookup.
    if not path.is_dir() or not (SPECS / path.name).is_dir():
        return None
    branch = sh(["git", "branch", "--show-current"], path).stdout.strip()
    sid = folder_for(branch)
    spec = load(path.name, sid) if sid else None
    return spec if spec and spec.get("worktree") and Path(spec["worktree"]).resolve() == path \
        and branch_of(spec) == branch else None


def checkout_idle(repo: Path, run: str = "") -> None:
    """No branch switch while a loop or a planner still owns this checkout,
    and no new task while an L2–L3 refactor step holds the repository (`run`
    names the refactor run that is asking, which its own hold lets through)."""
    for spec in listing(repo.name):
        held = spec.get("refactor") or {}
        if held.get("block") and held.get("run") != run and spec["state"] != "머지됨":
            raise HTTPException(409, f"리펙터링 단계 `{spec['id']}` 가 이 저장소를 잡고 있다 — 끝나거나 취소되면 풀린다")
        if spec.get("workspace_mode") != "branch" or spec.get("worktree") != str(repo):
            continue
        if spec["state"] == "머지됨" and not spec.get("cleanup_complete"):
            raise HTTPException(409, "머지 후 로컬 기본 브랜치 동기화·정리를 마친 뒤 새 작업을 시작하세요")
        if re.fullmatch(r"리뷰 대기|리뷰 R\d+|고치는 중 R\d+|머지 대기", spec["state"]):
            raise HTTPException(409, "이 저장소의 리뷰·머지를 마친 뒤 브랜치를 바꿔라")
        if spec.get("planning") and spec["planning"].get("phase") not in ("handoff", "stopped"):
            raise HTTPException(409, "이 저장소의 계획 작업을 마친 뒤 브랜치를 바꿔라")
        if spec.get("survey", {}).get("running") and spec["state"] != "멈춤":
            raise HTTPException(409, "이 저장소의 위키 조사를 마친 뒤 브랜치를 바꿔라")


def fork(repo: Path, sid: str) -> tuple[Path, str, str]:
    """New tasks fork the selected non-task branch, not a previous task's commits."""
    parent = owner(repo)
    branch = sh(["git", "branch", "--show-current"], repo).stdout.strip()
    base = (parent.get("return_branch") or (parent.get("pr") or {}).get("base")) \
        if parent and parent.get("workspace_mode") == "branch" else branch
    merged = parent.get("merge") if parent and parent.get("workspace_mode") == "branch" else None
    if merged:
        base = merged["base"]
    source = base or ""
    if source:
        upstream = sh(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                       f"{source}@{{upstream}}"], repo)
        if merged and (upstream.returncode or upstream.stdout.strip() != f"origin/{base}"):
            raise RuntimeError(f"`{base}` 의 upstream 을 확인한 뒤 새 작업을 시작해라")
        if not upstream.returncode:
            ancestry = sh(["git", "merge-base", "--is-ancestor", f"refs/heads/{source}",
                           upstream.stdout.strip()], repo)
            if merged and ancestry.returncode:
                raise RuntimeError(f"`{base}` 의 미게시 변경·분기를 해결한 뒤 새 작업을 시작해라")
            if not ancestry.returncode:
                source = upstream.stdout.strip()  # A fast-forward, without moving a sibling's checked-out ref.
    path = create(repo, sid, base=source)
    return path, sh(["git", "rev-parse", "HEAD"], path).stdout.strip(), base or branch


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


def backed(ref: str, paths: set[str]) -> bool:
    """`ref` — `docs/x.md:12`, a page `operator/x` — names one of `paths`."""

    return LINE.sub("", ref).removesuffix(".md") in {p.removesuffix(".md") for p in paths}


def profile_of(spec: dict) -> dict:
    """The review profile a spec asked for. A spec from before profiles, or
    one made for a pull request that came without one, is reviewed as code:
    a plan is never inferred, least of all from a `.md` suffix."""

    return {"review_profile": spec.get("review_profile") or "code",
            "review_profile_version": spec.get("review_profile_version") or PROFILE_VERSION,
            "artifact_root": spec.get("artifact_root")}


def profiled(repo: Path, block: dict) -> dict:
    return review_contract.profile_fields(repo, block)


def fields(repo: Path, block, gate: str, accepted: dict | None = None) -> dict:
    """The fields a person settles, checked. `ValueError` says what is wrong.

    The gate is always the first done item: checking that the agent put it
    there would leave a way to miss it, so it cannot be left out at all.

    `accepted` is a verified answer's evidence, by the id the draft cited it
    under (stage 7 of `docs/plans/jev/`): the card's grounds are then only
    what an accepted claim cited, each with its source revision. Grounds are
    facts; the goal and the decisions stay proposals a person approves."""

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
    if accepted is not None:
        paths = {c["path"] for c in accepted.values() if c.get("path")}
        listed = {k: [g for g in v if backed(g, paths)] for k, v in listed.items()}
        listed["evidence"] = [{"id": accepted[e]["evidence_id"], "cite": accepted[e]["cite"],
                               "revision": accepted[e]["revision"]}
                              for e in dict.fromkeys(strings(grounds.get("evidence"), "grounds.evidence"))
                              if e in accepted]
    return {
        "goal": " ".join(goal.split()),
        "out": strings(block.get("out"), "out"),
        "done": [gate] + [d for d in strings(block.get("done"), "done") if d != gate],
        "grounds": listed,
        "decisions": decided,
        **profiled(repo, block),
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
    if not re.search(rf"^\|\s*{re.escape(row)}\s*\|", steps(file.read_text(encoding="utf-8")), re.M):
        return None
    return {"path": file.relative_to(top).as_posix(), "row": row}


def steps(text: str) -> str:
    """The plan's steps table, where a row number means a step; the whole text
    when it has none (`.wiki/plan-active.md`). Another table in the same plan
    can have a row `1` too, and that row is not the step."""

    block = steps_block(text)
    return block.group(2) if block else text


def marker(path: Path, plan: dict) -> str:
    """What the row's status cell is set to, in the plan's own language:
    `완료` under a Korean `## 단계`, `Complete` in an English plan — the word
    English plans here use and session-start reporting reads as done."""

    try:
        block = steps_block((path / plan["path"]).read_text(encoding="utf-8"))
    except OSError:
        block = None
    return "완료" if block and block.group(1) == "단계" else "Complete"


def row_done(path: Path, plan: dict, n: int) -> bool:
    """Does the plan's step row say `Complete — PR #n` (`완료 — PR #n` in a
    Korean plan, `Done — PR #n` as the English plans once did) in the
    worktree's committed HEAD? Read from git, not the file: an edit left
    uncommitted is not in the pull request."""

    shown = sh(["git", "show", f"HEAD:{plan['path']}"], path)
    return not shown.returncode and re.search(
        rf"^\|\s*{re.escape(plan['row'])}\s*\|.*\|\s*(?:Complete|Done|완료) — PR #{n}\s*\|\s*$",
        steps(shown.stdout), re.M) is not None


def missing(repo: Path, spec: dict) -> list[str]:
    return [f for f in spec["grounds"]["files"] if not (repo / LINE.sub("", f)).is_file()]


def approved(spec: dict) -> dict | None:
    """The round a merge is bound to: the last one counted, when it allowed."""

    counted = [r for r in spec.get("rounds") or [] if not r.get("stale")]
    return counted[-1] if counted and counted[-1]["verdict"] == "allow" else None


def view(repo: Path, spec: dict) -> dict:
    """The spec as the screen reads it. `unproven` is `proven`'s own answer
    for the approved head in `머지 가능`, so the screen never judges a result
    by itself: empty only while the final gate stands for the current command
    and environment; `None` in every other state, where `[머지]` is not shown.
    The base is read as last fetched — this runs for every spec on every
    refresh — and `merge` fetches it before acting. A worktree that cannot be
    read is that spec's reason, not the listing's failure."""

    allowed = approved(spec)
    unproven = None
    if spec.get("state") == "머지 가능":
        path = Path(spec.get("worktree") or repo)
        try:
            unproven = proven(spec, allowed["head"], merge_base(path, allowed["base"], allowed["head"]),
                              digest(repo, path, required(repo, spec))) if allowed else "리뷰가 허용한 라운드가 없다"
            if not unproven and spec.get("implementation_environment") == "claude-cloud":
                from . import verification

                unproven = verification.merge_proven(repo, path, spec, allowed["head"],
                                                merge_base(path, allowed["base"], allowed["head"]))
            if not unproven:
                unproven = review_contract.merge_problem(repo, path, spec, allowed["head"],
                                                       merge_base(path, allowed["base"], allowed["head"]))
        except (OSError, subprocess.SubprocessError) as exc:
            unproven = f"작업트리를 읽지 못했다 — {exc}"
    return {**spec, **profile_of(spec), "missing": missing(repo, spec), "approved": allowed["head"] if allowed else None,
            "unproven": unproven, "waiting": bool(spec.get("worktree")) and work.waiting(spec["worktree"])}


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
        if not isinstance(value, dict):
            raise ValueError("`choices` 는 질문 또는 질문 목록이어야 한다")
        if "questions" in value:
            if not isinstance(value["questions"], list) or not value["questions"]:
                raise ValueError("`choices.questions` 는 비지 않은 목록이어야 한다")
            for question in value["questions"]:
                if not isinstance(question, dict) or "questions" in question:
                    raise ValueError("챕터마다 하나의 질문이 있어야 한다")
                _shape("choices", question)
            return
        if not isinstance(value.get("question"), str) or not value["question"].strip():
            raise ValueError("`choices` 에 `question` 이 없다")
        options = value.get("options")
        if not isinstance(options, list) or not options or not all(
                isinstance(o, dict) and isinstance(o.get("label"), str) and o["label"].strip() for o in options):
            raise ValueError("`choices` 의 `options` 마다 `label` 이 있어야 한다")
        for option in options:
            for key in ("note", "preview"):
                if key in option and not isinstance(option[key], str):
                    raise ValueError(f"`choices.options.{key}` 는 문자열이어야 한다")
        if "header" in value and not isinstance(value["header"], str):
            raise ValueError("`choices.header` 는 문자열이어야 한다")
        if "multi" in value and not isinstance(value["multi"], bool):
            raise ValueError("`choices.multi` 는 참·거짓이어야 한다")


def card(repo: Path, block, gate: str, source: dict, accepted: dict | None = None) -> str:
    """One `spec` block as a card on disk; its id.

    The same slug from the same conversation replaces its card while that has
    not started. Otherwise the name gets `-2`, `-3`."""

    made = fields(repo, block, gate, accepted)
    if source.get("focus") == "refactor":
        from . import refactor_api

        try:
            request = refactor_api.Start(request_id="draft-refactor", **(block.get("refactor") or {}))
            if request.mode != source.get("refactor_mode", "cleanup"):
                raise ValueError("명세 모드가 상단에서 고른 모드와 다르다. 현재 모드로 다시 정해라")
            refactor_api.checked(repo, request)
        except (HTTPException, ValueError) as exc:
            raise ValueError(getattr(exc, "detail", str(exc))) from exc
        made["refactor"] = {"mode": request.mode, "files": request.files}
    base = slugged(block.get("slug"))
    if not base:
        raise ValueError("`slug` 가 비었거나 쓸 수 있는 글자가 없다")
    source = {**source, "plan": plan_row(repo, block.get("plan"))}
    with _files:
        old = load(repo.name, base)
        if old and old["state"] == "정리됨" and not (old.get("refactor") or {}).get("run") and source.get("session") \
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


def answered(repo: Path, found: list[dict], source: dict, accepted: dict | None = None) -> list[dict]:
    """The blocks of a `next` answer as the screen draws them. A `spec` block
    becomes cards on disk and is sent as their ids, one entry per spec;
    `accepted` bounds their grounds when the answer was verified (`fields`)."""

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
                if name == "candidates":
                    # Jev's pick first, marked; the person still chooses.
                    block = {**block, "value": recommend(repo, block["value"])}
                out.append(block)
            except ValueError as exc:
                out.append({"name": name, "error": str(exc)})
            continue
        value = block["value"]
        for one in value if isinstance(value, list) else [value]:
            try:
                if not gate:
                    raise ValueError("연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
                out.append({"name": "spec", "id": card(repo, one, gate, source, accepted)})
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
    reason: str = "Updated by the person during the task"


class Start(BaseModel):
    model: str = ""
    effort: str = ""
    fast: bool = False


def noticed(repo: Path, spec: dict) -> dict | None:
    """A pull request merged on GitHub moves its spec to `머지됨`, once."""

    pr = spec.get("pr")
    if not pr or spec["state"] == "머지됨":
        return spec
    if spec["state"] == "머지 대기":
        # Merged through `[머지]`: the loop's table reads what became of it.
        from . import loop

        loop.landed(repo, spec)
        return load(repo.name, spec["id"])
    try:
        done = sh(["gh", "pr", "view", str(pr["number"]), "--json", "state,mergedAt"], repo, 30)
        state = json.loads(done.stdout).get("state") if not done.returncode else ""
    except (OSError, ValueError, subprocess.TimeoutExpired):
        state = ""
    if state != "MERGED":
        return spec
    with _files:
        spec = load(repo.name, spec["id"])
        if spec is None:
            return None
        if spec["state"] == "머지됨":
            return spec
        save(moved(spec, "머지 대기"))
    from . import loop
    loop.landed(repo, spec)
    return load(repo.name, spec["id"])


@router.get("/api/specs")
def specs() -> dict:
    with _lock:
        name, repo = project(), current_repo()
    return {"project": name, "gate": gate_of(repo),
            "specs": [view(repo, current) for s in listing(name) if (current := noticed(repo, s)) is not None]}


@router.put("/api/specs/{sid}")
def edit(sid: str, body: Edit) -> dict:
    """Revise a draft or an idle active task, preserving prior requirements."""

    repo = current_repo()
    gate = gate_of(repo)
    if not gate:
        raise HTTPException(409, "연결 먼저 — 이 저장소의 adapter 에 `gate_cmd` 가 없다")
    with _lock, _files:
        spec = editable_task(load(repo.name, sid))
        if spec["rev"] != body.rev:
            raise HTTPException(409, f"다른 곳에서 먼저 고쳤다 — 지금은 판 {spec['rev']}")
        if spec.get("worktree") in work._busy:
            raise HTTPException(409, "작업 중에는 에이전트에 변경 지시를 보내라. 턴 종료 때 명세에 반영한다")
        if spec["state"] in ("머지됨", "머지 대기") or re.fullmatch(r"리뷰 대기|리뷰 R\d+|고치는 중 R\d+", spec["state"]):
            raise HTTPException(409, "리뷰·머지를 멈춘 뒤 명세를 고쳐라")
        if spec["state"] != "정리됨":
            if body.slug and body.slug != sid:
                raise HTTPException(409, "시작한 작업의 브랜치 이름은 그대로 둔다")
            try:
                revise(repo, spec, body.model_dump())
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return view(repo, spec)
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


def revise(repo: Path, spec: dict, change: dict) -> None:
    """Persist a full spec revision; approval of earlier requirements expires."""
    if not isinstance(change, dict) or change.get("rev") != spec["rev"]:
        raise ValueError("명세 판이 바뀌었다. 현재 명세를 읽고 수정해라")
    reason = change.get("reason")
    if not isinstance(reason, str) or not reason.strip() or not all(k in change for k in ("goal", "out", "done")):
        raise ValueError("명세 변경에는 reason, goal, out, done 이 필요하다")
    gate = gate_of(repo)
    if not gate:
        raise ValueError("저장소의 gate_cmd 를 먼저 연결해라")
    made = fields(repo, {**spec, **{k: change[k] for k in ("goal", "out", "done", "review") if k in change}}, gate)
    spec.setdefault("revisions", []).append({"rev": spec["rev"], "ts": time.time(), "reason": reason.strip(),
                                            **{k: spec[k] for k in ("goal", "out", "done")}})
    for round_ in spec.get("rounds") or []:
        round_["stale"] = True
        round_["stale_reason"] = "Specification revised"
    spec.update(made, rev=spec["rev"] + 1, report=None, gate=None, validation=None, checks=[], fault=None,
                merge_request=None, maintenance=None, merge_progress=None)
    if spec.get("worktree") and not re.fullmatch(r"리뷰 대기|리뷰 R\d+|고치는 중 R\d+", spec["state"]):
        moved(spec, "작업 중", stopped=None)
    save(spec)


def aside(repo: str, sid: str) -> None:
    """Set aside, not deleted: `raw/specs/<repo>/dropped/`."""

    away = SPECS / repo / "dropped" / f"{sid}.{time.time_ns()}.json"
    away.parent.mkdir(parents=True, exist_ok=True)
    file_of(repo, sid).replace(away)


def dismissed(repo: str, branch: str) -> bool:
    sid = folder_for(branch)
    return bool(sid and load(repo, sid) is None and any((SPECS / repo / "dropped").glob(f"{sid}.*.json")))


def editable_task(spec: dict | None) -> dict:
    if spec is None:
        raise HTTPException(404, "그런 명세가 없다")
    request = spec.get("refactor") or {}
    if request.get("mode") and request.get("run"):
        raise HTTPException(409, "시작한 리펙터링은 실행 카드에서 관리해라. 목적·모드 변경은 새 명세로 정해라")
    return spec


@router.post("/api/specs/{sid}/delete")
def delete_task(sid: str) -> dict:
    """Archive a task by identity; a shared checkout and its Git changes remain."""
    from . import loop, planning

    with _lock:
        repo = current_repo()
    with _files:
        spec = editable_task(load(repo.name, sid))
    with _lock:
        worker = planning._workers.get((repo.name, sid))
    if worker is not None:
        worker.cancel()
        if worker.thread is not None:
            worker.thread.join(work.HALT_WAIT)
            if worker.thread.is_alive():
                raise HTTPException(409, "계획 작업이 멈춘 뒤 삭제해라")
    path = Path(spec["worktree"]) if spec.get("worktree") else None
    if path and spec.get("workspace_mode") != "branch" and path.resolve() != repo.resolve() and path.exists():
        work.clear(work.Removal(path=str(path), force=True))
    else:
        loop.halt_loop(repo.name, sid)
        # An inactive task shares another task's checkout; never stop that owner.
        active = owner(path) if path and path.exists() else None
        if active and active["id"] == sid:
            work.halt_all(str(path), repo)
            with _lock:
                release = hold(work._busy, _lock, str(path), "작업이 멈춘 뒤 삭제해라")
            try:
                work.forget(path)
                with _files:
                    if load(repo.name, sid) is not None:
                        aside(repo.name, sid)
            finally:
                release()
    with _files:
        if load(repo.name, sid) is not None:
            aside(repo.name, sid)
    work.feed.put({"kind": "sync"})
    return {"ok": True}


@router.post("/api/specs/{sid}/drop")
def drop(sid: str) -> dict:
    """A started spec has a worktree and maybe a pull request, and is not
    dropped until merged — or until a person deletes its worktree (`forsaken`)."""

    repo = current_repo()
    with _files:
        spec = editable_task(load(repo.name, sid))
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
        repo = spec["repo"]
        if not file_of(repo, spec["id"]).exists():
            return
        aside(repo, spec["id"])
    publish(spec)


@router.post("/api/specs/{sid}/start")
def start(sid: str, body: Start) -> dict:
    """`[시작]`: the worktree first, then the spec moves, then the first turn.

    In that order, a worktree that could not be made leaves the spec exactly
    as it was; the other way round, a failure had to be rolled back."""

    repo = current_repo()
    spec = load(repo.name, sid)
    if spec and (spec.get("refactor") or {}).get("mode"):
        from . import refactor_api

        run = refactor_api.from_spec(repo, sid, body.model_dump())
        return {"refactor": run["id"]}
    with _lock:
        repo = current_repo()
        checkout_idle(repo)
        release = hold(work._busy, _lock, str(repo), "이 저장소에서 다른 작업이 돌고 있다")
    try:
        with _files:
            spec = load(repo.name, sid)
            if spec is None:
                raise HTTPException(404, "그런 명세가 없다")
            if spec["state"] != "정리됨":
                raise HTTPException(409, f"이미 시작했다 — {spec['state']}")
            try:
                path, before, previous = fork(repo, sid)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
            # The model is kept for the turns the loop sends this worktree.
            save(moved(spec, "작업 중", worktree=str(path), workspace_mode="branch", branch=sid,
                        start_head=before, return_branch=previous,
                        cell={"model": body.model, "effort": body.effort, "fast": body.fast}))
        # Closing a session takes _lock; keep the same lock order as spec editing.
        work.forget(path)
        # Made: from here it is a turn, and a switch no longer waits for it.
        release.held.kind = "turn"
        # The server's own turn: Jev may gather evidence first, or ask the person instead.
        run = work.begin(path, work.session(path, body.model, body.effort, body.fast), "Start.", release, decide=True)
    except BaseException:
        release()
        raise
    return {"path": str(path), "turn": run.turn, "session_id": run.session_id}


@router.post("/api/specs/{sid}/checkout")
def activate(sid: str) -> dict:
    """Open a saved task branch in the same checkout, preserving its conversation."""
    with _lock:
        repo = current_repo()
        checkout_idle(repo)
        release = hold(work._busy, _lock, str(repo), "이 저장소에서 다른 작업이 돌고 있다")
    try:
        spec = load(repo.name, sid)
        if not spec or spec.get("workspace_mode") != "branch" or spec.get("worktree") != str(repo):
            raise HTTPException(404, "이 저장소의 작업 브랜치가 아니다")
        clean = sh(["git", "status", "--porcelain"], repo)
        if clean.returncode or clean.stdout.strip():
            raise HTTPException(409, "현재 변경을 커밋하거나 보관한 뒤 작업 브랜치를 열어라")
        switched = sh(["git", "switch", branch_of(spec)], repo)
        if switched.returncode:
            raise HTTPException(409, said(switched))
        work.forget(repo)
        return {"path": str(repo)}
    finally:
        release()


# -- The work session -------------------------------------------------------

def system(path: Path) -> str:
    """The system prompt of a write session in `path`: the spec, when a spec
    owns that worktree. Read whenever a session is made, so one made again
    after a restart still has it."""

    spec = owner(path)
    if spec is None:
        return ""
    shown = {**{k: spec[k] for k in ("id", "rev", "goal", "out", "done", "grounds", "decisions")},
             **profile_of(spec), **({"review": spec["review"]} if spec.get("review") else {})}
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


def gate(cmd: str, cwd: Path, halt: threading.Event, env: dict | None = None) -> tuple[int | None, str, str]:
    """Run the gate in `cwd`, a shell string as the adapter wrote it:
    `(exit code, output, why it was cut)`. A stop of the turn stops it too,
    and a stop that came while a fast gate ran still cuts it: the stop is
    read after the gate ends, not only while it waits."""

    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=python_environment(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
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


def judge(path: Path, cmds: list[str], halt: threading.Event, noted=lambda text: None, env: dict | None = None) -> dict:
    """The server's own check of a done report: nothing uncommitted, and the
    commands pass again in the worktree, in order, stopping at the first
    failure. What the agent said is not evidence. The review loop checks
    every head it sends for review the same way.

    HEAD and the status are read before and after: a command that commits or
    leaves a file behind fails, whatever it exited with — what passed must be
    what goes up. `cmd` joins the commands for the screens that show one."""

    cmd = " && ".join(cmds)
    status = sh(["git", "status", "--porcelain"], path)
    head = sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    if status.returncode or status.stdout.strip():
        reason = "커밋 안 된 변경" if not status.returncode else f"git status 실패 — {said(status)}"
        return {"ok": False, "reason": reason, "cmd": cmd, "commands": cmds, "code": None, "tail": status.stdout.strip(),
                "head": head, "ts": time.time()}
    code, out, cut = None, "", ""
    for one in cmds:
        noted(f"게이트 · {one}")
        code, out, cut = gate(one, path, halt, env=env) if env is not None else gate(one, path, halt)
        if code != 0:
            break
    after = sh(["git", "status", "--porcelain"], path)
    moved = "게이트가 HEAD 를 바꿨다" if sh(["git", "rev-parse", "HEAD"], path).stdout.strip() != head else \
        "게이트가 작업트리를 바꿨다" if after.returncode or after.stdout.strip() else ""
    ok = code == 0 and not moved
    reason = "" if ok else cut or (f"게이트가 {code} 로 끝났다" if code != 0 else moved)
    return {"ok": ok, "reason": reason, "cmd": cmd, "commands": cmds, "code": code,
            "tail": "\n".join(out.splitlines()[-TAIL:]), "head": head, "ts": time.time()}


# -- Which checks a head needs ----------------------------------------------
# A round runs the registered checks its changed paths map to; the final gate
# is the adapter's whole `gate_cmd`, once, on the head the review allowed.
# `docs/plans/reliability/2-tests.md` is the contract.

# A change to any of these can reach every consumer: the full gate.
SHARED = ("conftest.py", "requirements*.txt", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini",
          "package.json", "package-lock.json", ".wiki/adapter.toml")
LOCKS = ("requirements*.txt", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "package-lock.json",
         "web/package-lock.json", "uv.lock", "poetry.lock")


def required(repo: Path, spec: dict) -> str:
    """The full gate: the adapter's `gate_cmd` as it is now, else the one the
    spec was settled with, then the debt ratchet (`tool/debt.py`), which
    passes in a repository that has not adopted it."""

    return " && ".join(filter(None, [gate_of(repo) or spec["done"][0], debt.command(spec.get("base") or ""),
                                    review_contract.preservation_command(spec)]))


def rounded(repo: Path, path: Path, spec: dict, base: str, halt: threading.Event,
            noted=lambda text: None, chosen: dict | None = None) -> tuple[dict, dict]:
    """The round checks `selected` picks, run by `judge`: the verdict, kept as
    the spec's `gate` for the screens, and `validation.round`."""

    chosen = chosen or for_round(repo, path, spec, base)
    verdict = {**judge(path, chosen["commands"], halt, noted), "selection": chosen["selection"]}
    return verdict, {"head": verdict["head"], **chosen, "ok": verdict["ok"], "finished_at": verdict["ts"]}


def for_round(repo: Path, path: Path, spec: dict, base: str) -> dict:
    """One selection for dispatch and reuse, including mandatory preservation."""
    chosen = selected(repo, path, base, required(repo, spec))
    preserve = review_contract.preservation_command(spec)
    if preserve and chosen["selection"] == "mapped":
        chosen = {**chosen, "commands": list(dict.fromkeys([*chosen["commands"], preserve]))}
    return chosen


def local_base(spec: dict, path: Path) -> str:
    """The base a pull request not opened yet goes to, read without GitHub:
    the spec's own, else `origin/HEAD`. Empty selects the full gate. Nothing
    leaves this machine before the checks pass."""

    if spec.get("base"):
        return spec["base"]
    done = sh(["git", "rev-parse", "--abbrev-ref", "origin/HEAD"], path)
    return "" if done.returncode else done.stdout.strip().removeprefix("origin/")


def merge_base(path: Path, base: str, rev: str = "HEAD") -> str:
    """`rev`'s merge base with `origin/<base>` as this clone last fetched it, or empty."""

    done = sh(["git", "merge-base", f"origin/{base}", rev], path)
    return "" if done.returncode else done.stdout.strip()


def current_merge_base(path: Path, base: str, rev: str = "HEAD", *, budget=None) -> str:
    """`merge_base` after fetching `base` now: what a final result is bound to
    and checked against. A base that moved onto the branch's own commits
    changes it, and the last fetch would not show that. Empty when the fetch
    fails — a base that cannot be read proves nothing."""

    if budget is not None:
        budget.check()
    if sh(["git", "fetch", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], path,
          budget.left() if budget is not None else 120).returncode:
        return ""
    if budget is not None:
        budget.check()
    done = sh(["git", "merge-base", f"origin/{base}", rev], path,
              budget.left() if budget is not None else 60)
    if budget is not None:
        budget.check()
    return "" if done.returncode else done.stdout.strip()


def selected(repo: Path, path: Path, base: str, gate_cmd: str) -> dict:
    """`{base_oid, commands, selection}` for the worktree's HEAD.

    Every path changed since the merge base, both names of a rename and a
    deletion's too, must match some registered check's `paths`; the union of
    the matches runs. No merge base, a malformed map, an unmapped path or a
    shared file selects the full gate instead: an uncertain impact widens.
    `*` crosses `/` here (`fnmatch`), which only ever widens a match."""

    oid = merge_base(path, base)
    full = {"base_oid": oid, "commands": [gate_cmd], "selection": "full"}
    listed = sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", oid, "HEAD"], path) \
        if oid else None
    checks = registered(repo).values()
    if listed is None or listed.returncode or any(c["paths"] is None for c in checks):
        return full
    chosen = []
    for changed in filter(None, listed.stdout.splitlines()):
        if any(fnmatchcase(changed, s) or fnmatchcase(changed.rsplit("/", 1)[-1], s) for s in SHARED):
            return full
        hits = [c["cmd"] for c in checks if any(fnmatchcase(changed, g) for g in c["paths"])]
        if not hits:
            return full
        chosen += hits
    return {**full, "commands": list(dict.fromkeys(chosen)), "selection": "mapped"} if chosen else full


def digest(repo: Path, path: Path, cmd: str) -> str:
    """What the final gate's result is bound to beside the commit: the command,
    the server's Python, the adapter and the lock and config files present in
    the worktree. Not every property of the machine — only the changes this
    program can see invalidate a result. No credentials go in."""

    def sha(file: Path) -> str:
        try:
            return hashlib.sha256(file.read_bytes()).hexdigest()
        except OSError:
            return ""

    found = {f.relative_to(path).as_posix(): sha(f) for pattern in LOCKS for f in sorted(path.glob(pattern))}
    adapter = adapter_path(repo.name, repo)
    shown = {"cmd": cmd, "python": (sys.version, sys.executable), "adapter": sha(adapter) if adapter else "", "files": found}
    return hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()


def validate(repo: str, sid: str, **parts) -> dict | None:
    """The spec's `validation`, the only place it is written: `round` (the
    last targeted result), `final` (the full gate on an allowed head) and
    `phase` (`final_running` while that runs)."""

    with _files:
        spec = load(repo, sid)
        if spec is None:
            return None
        spec["validation"] = {"version": 1, "round": None, "final": None, "phase": None,
                              **(spec.get("validation") or {}), **parts}
        save(spec)
        return spec


def proven(spec: dict, head: str, base_oid: str, digested: str) -> str:
    """Why the final gate does not stand for `head`, or empty. A targeted
    round result is never proof; a missing, failed, running or elsewhere
    bound final is not either."""

    v = spec.get("validation") or {}
    final = v.get("final") or {}
    if v.get("phase") == "final_running":
        return "최종 게이트가 아직 돌고 있다"
    if not final:
        return "최종 게이트 결과가 없다"
    if not final.get("ok"):
        return f"최종 게이트가 통과하지 않았다 — {final.get('reason') or '끝나지 않았다'}"
    if final.get("head") != head:
        return "최종 게이트가 다른 커밋에서 돌았다"
    if not base_oid:
        return "base 를 지금 읽지 못했다"
    if final.get("base_oid") != base_oid:
        return "최종 게이트 뒤 base 가 바뀌었다"
    if final.get("environment_digest") != digested:
        return "최종 게이트 뒤 명령이나 환경이 바뀌었다"
    return ""


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
    what = "이 변경이 닿는 확인을" if spec["gate"].get("selection") == "mapped" else "게이트를"
    lines += [f"- [x] 서버가 작업트리에서 {what} 다시 돌림 — `{spec['gate']['cmd']}` · {last[0]}".rstrip(" ·")]
    if spec["gate"].get("selection") == "mapped":
        lines += ["- [ ] 전체 게이트 — 리뷰가 허용한 커밋에서 머지 전에 돈다"]
    lines += [f"- [x] Jev 가 고른 추가 확인 — `{c['cmd']}` · 통과" for c in spec.get("checks") or []
              if c["ok"] and c.get("head") == spec["gate"].get("head")]
    lines.append("")
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


def base_of(spec: dict, path: Path) -> subprocess.CompletedProcess:
    """The branch the pull request goes to. A spec sent back by `[다시 PR]`
    goes to the base it was reviewed for; any other, the repository's default."""

    return subprocess.CompletedProcess([], 0, spec["base"], "") if spec.get("base") else \
        sh(["gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"], path, 30)


def existing_pr(path: Path, branch: str, base: str) -> tuple[int, str] | None:
    """The open pull request of this repository from `branch` into `base`, or
    `None`. Every field is checked here, not left to `gh`'s filters: a fork's
    branch of the same name is someone else's. `RuntimeError` when GitHub
    cannot be read — nobody can say then whether one exists."""

    done = sh(["gh", "pr", "list", "--head", branch, "--base", base, "--state", "open",
               "--json", "number,url,headRefName,baseRefName,isCrossRepository"], path, 60)
    if done.returncode:
        raise RuntimeError(f"열린 PR 을 확인하지 못했다 — {said(done)}")
    rows = [r for r in json.loads(done.stdout)
            if r.get("headRefName") == branch and r.get("baseRefName") == base and not r.get("isCrossRepository")]
    if len(rows) > 1:
        raise RuntimeError(f"`{branch}` → `{base}` 로 열린 PR 이 {len(rows)}개다")
    return (int(rows[0]["number"]), rows[0]["url"]) if rows else None


def pull_request(path: Path, branch: str, base: str, title: str, body: str) -> tuple[int, str]:
    """`(number, url)` of the pull request from `branch` into `base`: the one
    already open, else a new one. A create that failed or timed out is not
    tried again — the list is read once more, since GitHub may have made it
    before the answer was lost. Reused PRs receive the current title and body.
    `RuntimeError` says why publication could not complete."""

    found = existing_pr(path, branch, base)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(body)
    try:
        if not found:
            try:
                made = sh(["gh", "pr", "create", "--base", base, "--head", branch,
                           "--title", title, "--body-file", fh.name], path, 120)
                why = said(made)
            except subprocess.TimeoutExpired:
                made, why = None, "시간 초과"
            number = re.search(r"/pull/(\d+)", made.stdout) if made is not None and not made.returncode else None
            if number:
                return int(number.group(1)), made.stdout.strip().splitlines()[-1]
            found = existing_pr(path, branch, base)
            if not found:
                raise RuntimeError(f"PR 을 만들지 못했다 — {why}")
        # The agent may have opened it early, or GitHub accepted a timed-out
        # create. In both cases publish the current requirements and evidence.
        try:
            edited = sh(["gh", "pr", "edit", str(found[0]), "--title", title, "--body-file", fh.name], path, 120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("PR 명세와 확인 기록을 갱신하지 못했다 — 시간 초과") from exc
        if edited.returncode:
            raise RuntimeError(f"PR 명세와 확인 기록을 갱신하지 못했다 — {said(edited)}")
        return found
    finally:
        os.unlink(fh.name)


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
    base = base_of(spec, path)
    if base.returncode or not base.stdout.strip():
        return failed(run, spec, f"기본 브랜치를 모른다 — {said(base)}")
    if run.halt.is_set():
        return failed(run, spec, "사람이 멈춤 — push 는 했고 PR 은 만들지 않았다")
    note(run, "PR 을 한국어로 옮기는 중")
    shown = korean(spec)
    try:
        n, url = pull_request(path, branch, base.stdout.strip(), shown["goal"], body_of(shown))
    except RuntimeError as exc:
        return failed(run, spec, str(exc))
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
    text = (f"PR #{n} is up. In `{plan['path']}`, change the status cell of the steps table row whose "
            f"first cell is `{plan['row']}` to `{marker(path, plan)} — PR #{n}`, commit that one change, "
            "and stop. Change nothing else.")
    return lambda: again(path, run.chat, text, spec)


def reviewed(spec: dict) -> None:
    """Publication ends here. The person's Review Loop request dispatches."""
    work.notice("PR 준비 완료 · 리뷰 시작을 기다린다", f"{spec['repo']} · {spec['id']}")


def failed(run, spec: dict, reason: str) -> None:
    errorlog.record("task-failure", reason, repo=spec["repo"], spec=spec["id"], turn=getattr(run, "turn", None))
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
        errorlog.record("task-check", exc, turn=getattr(run, "turn", None), path=str(path))
        note(run, f"명세 확인이 깨졌다 — {type(exc).__name__}: {exc}")
        return None


def _check(path: Path, run, final: str):
    spec = owner(path)
    repo = channels.repo_for(spec["repo"]) if spec else None
    if spec is None or repo is None:
        return None
    if spec.get("planning") and spec["planning"].get("phase") != "handoff" and spec["state"] == "작업 중":
        # A plan goes up through `planning` only; a done report here publishes nothing.
        return None
    changes = [b for b in blocks(final)[1] if b["name"] == "spec-update"]
    if changes:
        if len(changes) != 1 or "error" in changes[0]:
            return failed(run, spec, "명세 변경 블록은 올바른 JSON 객체 하나여야 한다")
        try:
            with _files:
                spec = load(spec["repo"], spec["id"])
                revise(repo, spec, changes[0].get("value"))
        except ValueError as exc:
            return failed(run, spec, f"명세 변경을 반영하지 못했다 — {exc}")
        note(run, f"명세 판 {spec['rev']} 반영 · 이전 리뷰 승인은 다시 확인한다")
    if spec["state"].startswith("PR #") and spec.get("plan_commit") == "asked":
        # Any turn that ends here is not the row's commit: a person's turn may
        # have got there first, or the agent answered without the edit. Only
        # a committed row that says so is pushed and closes the follow-up.
        plan = spec["source"]["plan"]
        if not row_done(path, plan, spec["pr"]["number"]):
            return failed(run, spec, f"계획 행 `{plan['path']}` {plan['row']} 이 커밋된 HEAD 에서 아직 "
                                     f"`{marker(path, plan)} — PR #{spec['pr']['number']}` 가 아니다")
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
    # The checks this change maps to, not the whole gate: that runs once the
    # review allows the exact head (`loop.finalized`).
    verdict, record = rounded(repo, path, spec, local_base(spec, path), run.halt, lambda text: note(run, text))
    update(spec["repo"], spec["id"], gate=verdict)
    spec = validate(spec["repo"], spec["id"], round=record)
    if not verdict["ok"]:
        return failed(run, spec, f"판정 실패 — {verdict['reason']}")
    note(run, "게이트 통과")
    # One registered check more, when Jev picks one. The gate already ran: nothing here skips it.
    go, why = extra_check(repo, path, run, spec)
    if not go:
        return failed(run, spec, why)
    return opened(repo, path, run, load(spec["repo"], spec["id"]) or spec)
