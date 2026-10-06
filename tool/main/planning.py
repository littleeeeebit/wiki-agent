"""planning — the Plan action: research, a new plan folder, its pull request,
and the hand-off to document review (`docs/plans/reliability/7-planner.md`).

Main-layer composition. `specs` stays the owner of every spec write: a plan
spec is read and saved only through `specs.load`/`specs.save` under
`specs._files`, and this module keeps its run state in the spec's additive
`planning` field. A worker thread walks the phases while it holds the
worktree; each model turn is a `work.Run` there, so a screen tails it through
`/api/work/events` like any other turn.

Planner A is a read-only session with web tools and no shell. It returns
files as `plan-file` blocks; the server checks them and writes only inside a
new `docs/plans/<id>/` of the worktree it made, commits exactly those paths,
and opens or recovers the pull request once. Then A is closed, the loop takes
the pull request, a read-only reviser returns replacement files the server
writes (`revise`), and the review cell the spec names checks them. Nothing
implements or merges the plan. What reaches the screen is read by a person and
stays Korean; what the planner reads is English.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import improvement
from agent import ChatSession
from common.budget import Budget, Cancelled, Exhausted

from . import channels, loop, runtime, specs, work
from .query import ROOT, _lock, current_repo, hold

PROMPT = (ROOT / "tool/prompts/plan-planner.md").read_text(encoding="utf-8")
REVISER_PROMPT = (ROOT / "tool/prompts/plan-reviser.md").read_text(encoding="utf-8")

VERSION = 1
# Read, search and fetch; no `Bash`, which writes, and no edit tool.
PLANNER_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"
REVISER_TOOLS = "Read,Glob,Grep"
# How each host names a web search or fetch in its tool events.
WEB = {"WebSearch", "WebFetch", "webSearch", "web_search"}
# The phases a worker runs; a restart turns each into `stopped`.
RUNNING = ("collect", "research", "outline", "stages", "validate", "publish")
MAX_STAGES = 10
MAX_GOAL = 2_000
MAX_CONTEXT = 20_000
MAX_MATERIALS = 30_000
MAX_FILE = 60_000     # characters in one returned file
REQUEST = re.compile(r"[A-Za-z0-9-]{8,64}")
NAME = re.compile(r"0-overview\.md|[1-9]\d?-[a-z0-9][a-z0-9-]{0,40}\.md")
EFFORT = re.compile(r"[a-z]{1,16}")
BLOCK = re.compile(r"^```(plan-questions|plan-sources|plan-outline|plan-file)[ \t]*\r?\n(.*?)^```[ \t]*$",
                   re.M | re.S)
LINK = re.compile(r"\]\(([^)\s]+)\)")
TEMPORARY = re.compile(r"\.(.+)\.[0-9a-f]{32}\.tmp")   # what `atomic` swaps in from
OVERVIEW = ("Problem", "Constraints", "Decisions", "Stages", "Sources")
STAGE = ("Requirements", "Entry points", "Contracts", "Errors", "Edits", "Tests", "Rollback")
SOURCE = ("id", "title", "url", "retrieved", "locator", "fragment", "applicability", "counterevidence",
          "rejected", "validation")
# May be empty — a source nobody argues against says so by leaving these blank.
OPTIONAL = ("counterevidence", "rejected")

router = APIRouter()

_creating = threading.Lock()   # the request-id lookup and the spec it makes, as one step


# -- The request ----------------------------------------------------------------

class Role(BaseModel):
    model: str = ""
    effort: str = ""


class Roles(BaseModel):
    planner: Role
    reviser: Role
    reviewer: Role


class Limits(BaseModel):
    seconds: float
    calls: int
    tokens: int


class Plan(BaseModel):
    request_id: str
    goal: str
    context: str = ""
    slug: str = ""
    stages: int | None = None
    roles: Roles
    limits: Limits
    refactor: bool = False   # a full refactor's plan: every stage names its tier and files (`tiered`)


def normalized(body: Plan) -> dict:
    """The request as it is compared: the same key with this input is the
    same plan, with any other input a conflict."""

    roles = body.roles
    return {"goal": " ".join(body.goal.split()), "context": body.context.strip(), "slug": specs.slugged(body.slug),
            "stages": body.stages,
            "roles": {name: {"model": role.model.strip(), "effort": role.effort.strip()}
                      for name, role in (("planner", roles.planner), ("reviser", roles.reviser),
                                         ("reviewer", roles.reviewer))},
            "limits": body.limits.model_dump(), **({"refactor": True} if body.refactor else {})}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def checked(body: Plan) -> None:
    """400 for a request that cannot start: every limit strictly positive and
    finite — never unlimited, never a default of zero."""

    if not REQUEST.fullmatch(body.request_id):
        raise HTTPException(400, "요청 키는 영문·숫자·- 8–64자다")
    if not body.goal.strip() or len(body.goal) > MAX_GOAL:
        raise HTTPException(400, f"목표는 1–{MAX_GOAL}자다")
    if len(body.context) > MAX_CONTEXT:
        raise HTTPException(400, f"맥락은 {MAX_CONTEXT}자까지다")
    if body.stages is not None and not 1 <= body.stages <= MAX_STAGES:
        raise HTTPException(400, f"단계 수는 1–{MAX_STAGES}다")
    if body.slug and not specs.slugged(body.slug):
        raise HTTPException(400, "이름은 소문자·숫자·- 만, 64자까지")
    limits = body.limits
    if not (math.isfinite(limits.seconds) and limits.seconds > 0 and limits.calls > 0 and limits.tokens > 0):
        raise HTTPException(400, "시간·호출·토큰 한도는 모두 0보다 큰 유한한 값이어야 한다")
    for name, role in (("planner", body.roles.planner), ("reviser", body.roles.reviser),
                       ("reviewer", body.roles.reviewer)):
        model = role.model.strip()
        if model and not model.startswith("codex:") and not channels.CLAUDE_MODEL.fullmatch(model):
            raise HTTPException(400, f"{name} 모델 이름을 받지 않는다")
        if role.effort.strip() and not EFFORT.fullmatch(role.effort.strip()):
            raise HTTPException(400, f"{name} 추론 강도를 받지 않는다")


# -- State -------------------------------------------------------------------------

def planned(repo: str, sid: str, **fields) -> dict | None:
    """Merge `fields` into the spec's `planning`, under the files' lock."""

    with specs._files:
        spec = specs.load(repo, sid)
        if spec is None or not spec.get("planning"):
            return None
        spec["planning"] = {**spec["planning"], **fields}
        specs.save(spec)
        return spec


def halted(repo: str, sid: str, reason: str, detail: str = "", **fields) -> None:
    """`stopped`, keeping the phase it stopped in and everything completed.
    The first stop stands."""

    with specs._files:
        spec = specs.load(repo, sid)
        p = (spec or {}).get("planning")
        if not p or p["phase"] == "stopped":
            return
        spec["planning"] = {**p, **fields, "phase": "stopped",
                            "stopped": {"reason": reason, "detail": detail, "phase": p["phase"], "ts": time.time()}}
        specs.save(spec)


def drafts(repo: str, sid: str) -> Path:
    """Where the drafts wait until publication: beside the specs, never in a checkout."""

    return specs.SPECS.parent / "planning" / repo / sid


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def draft(repo: str, sid: str, rel: str) -> str | None:
    try:
        return (drafts(repo, sid) / Path(rel).name).read_text(encoding="utf-8")
    except OSError:
        return None


def kept(repo: str, sid: str, entry: dict) -> bool:
    """The draft still says what the manifest recorded."""

    text = draft(repo, sid, entry["path"])
    return text is not None and sha(text) == entry["sha256"]


def drafted(repo: str, sid: str, file: dict) -> dict:
    """Keep one returned file as a draft; its manifest entry."""

    folder = drafts(repo, sid)
    folder.mkdir(parents=True, exist_ok=True)
    atomic(folder / Path(file["path"]).name, file["content"])
    return {"path": file["path"], "sha256": sha(file["content"]), "requirement_ids": file["requirement_ids"],
            "source_ids": file["source_ids"]}


def atomic(file: Path, content: str) -> None:
    """UTF-8 without a BOM, `\\n` line ends, swapped in whole."""

    temporary = file.with_name(f".{file.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content.replace("\r\n", "\n").encode("utf-8"))
        os.replace(temporary, file)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


# -- What the planner returns --------------------------------------------------------

def found(text: str) -> dict[str, list]:
    """The answer's named blocks, by name, or `ValueError`."""

    out: dict[str, list] = {}
    for m in BLOCK.finditer(text):
        try:
            out.setdefault(m[1], []).append(json.loads(m[2]))
        except ValueError as exc:
            raise ValueError(f"`{m[1]}` 블록이 JSON 이 아니다 — {exc}") from exc
    return out


def words(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{what}` 이 비었다")
    return value.strip()


def ids(value, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"`{what}` 는 id 글줄의 목록이어야 한다")
    return list(dict.fromkeys(v.strip() for v in value))


def questioned(value) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError("`plan-questions` 는 질문 1–4개의 목록이어야 한다")
    out, seen = [], set()
    for q in value:
        if not isinstance(q, dict):
            raise ValueError("질문마다 객체여야 한다")
        qid = words(q.get("id"), "id")
        options = q.get("options")
        if qid in seen or not isinstance(options, list) or not 2 <= len(options) <= 4:
            raise ValueError(f"질문 `{qid}` 는 id 가 겹치지 않고 선택지가 2–4개여야 한다")
        seen.add(qid)
        out.append({"id": qid, "question": words(q.get("question"), "question"),
                    "options": [{"label": words(o.get("label") if isinstance(o, dict) else None, "label"),
                                 "note": str(o.get("note") or "")} for o in options]})
    return out


def sourced(value, path: Path) -> list[dict]:
    """The research's source records, or `ValueError`. A URL is a web source;
    anything else must be a file of the repository."""

    if not isinstance(value, list) or not value:
        raise ValueError("`plan-sources` 가 비었다")
    out, seen = [], set()
    for s in value:
        if not isinstance(s, dict):
            raise ValueError("출처마다 객체여야 한다")
        record = {k: (str(s.get(k) or "").strip() if k in OPTIONAL else words(s.get(k), f"sources.{k}"))
                  for k in SOURCE}
        if not re.fullmatch(r"S\d+", record["id"]) or record["id"] in seen:
            raise ValueError(f"출처 id `{record['id']}` 는 겹치지 않는 `S<n>` 이어야 한다")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["retrieved"]):
            raise ValueError(f"출처 `{record['id']}` 의 `retrieved` 는 YYYY-MM-DD 다")
        web = bool(re.match(r"https?://", record["url"]))
        if not web and not (specs.inside(path, record["url"]) and (path / record["url"]).is_file()):
            raise ValueError(f"출처 `{record['id']}` 는 URL 도 저장소의 파일도 아니다: {record['url']}")
        seen.add(record["id"])
        out.append({**record, "kind": "web" if web else "local",
                    "claims": ids(s.get("claims") or [], "claims")})
    if not any(s["kind"] == "web" for s in out):
        raise ValueError("웹 출처가 하나도 없다")
    return out


def outlined(value, requested: int | None) -> dict:
    """Requirements and stages, or `ValueError`: every requirement served by
    a stage, and a stage depending only on earlier ones — so no cycle."""

    if not isinstance(value, dict):
        raise ValueError("`plan-outline` 은 객체여야 한다")
    reqs, stages = value.get("requirements"), value.get("stages")
    if not isinstance(reqs, list) or not reqs or not isinstance(stages, list) or not stages:
        raise ValueError("`plan-outline` 에 요구사항과 단계가 있어야 한다")
    requirements = []
    for r in reqs:
        rid = words(r.get("id") if isinstance(r, dict) else None, "requirements.id")
        if not re.fullmatch(r"R[1-9]\d*", rid) or rid in {x["id"] for x in requirements}:
            raise ValueError(f"요구사항 id `{rid}` 는 겹치지 않는 `R<n>` 이어야 한다")
        requirements.append({"id": rid, "text": words(r.get("text"), "requirements.text")})
    known = {r["id"] for r in requirements}
    if requested and len(stages) != requested:
        raise ValueError(f"단계가 {len(stages)}개다 — 요청은 {requested}개")
    if len(stages) > MAX_STAGES:
        raise ValueError(f"단계는 {MAX_STAGES}개까지다")
    out = []
    for n, s in enumerate(stages, 1):
        if not isinstance(s, dict) or s.get("n") != n:
            raise ValueError(f"단계 번호는 1 부터 차례로다 — {n} 번째")
        slug = words(s.get("slug"), "stages.slug")
        if not NAME.fullmatch(f"{n}-{slug}.md"):
            raise ValueError(f"단계 {n} 의 slug `{slug}` 를 파일 이름으로 쓸 수 없다")
        deps = s.get("depends_on") or []
        if not isinstance(deps, list) or not all(type(d) is int and 1 <= d < n for d in deps):
            raise ValueError(f"단계 {n} 은 앞선 단계에만 기댈 수 있다")
        served = ids(s.get("requirement_ids"), f"stages[{n}].requirement_ids")
        if not served or set(served) - known:
            raise ValueError(f"단계 {n} 이 없는 요구사항을 적었거나 아무것도 맡지 않았다")
        out.append({"n": n, "slug": slug, "title": words(s.get("title"), "stages.title"), "depends_on": deps,
                    "requirement_ids": served})
    left = known - {r for s in out for r in s["requirement_ids"]}
    if left:
        raise ValueError(f"어느 단계도 맡지 않은 요구사항: {', '.join(sorted(left))}")
    return {"requirements": requirements, "stages": out}


def filed(value, allowed: set[str]) -> dict:
    """One `plan-file`, or `ValueError`. Its path must be one of `allowed`;
    whether it is safe to write is `target`'s, right before the write."""

    if not isinstance(value, dict):
        raise ValueError("`plan-file` 은 객체여야 한다")
    rel = value.get("path")
    if rel not in allowed:
        raise ValueError(f"받지 않는 경로다: {rel!r} — {', '.join(sorted(allowed))} 중 하나여야 한다")
    content = value.get("content")
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_FILE:
        raise ValueError(f"`{rel}` 의 내용이 비었거나 {MAX_FILE}자를 넘는다")
    # The text `atomic` writes, so every hash taken of it is the file's own.
    content = content.replace("\r\n", "\n")
    return {"path": rel, "content": content if content.endswith("\n") else content + "\n",
            "requirement_ids": ids(value.get("requirement_ids") or [], "requirement_ids"),
            "source_ids": ids(value.get("source_ids") or [], "source_ids")}


def file_of(root: str, stage: dict | None = None) -> str:
    return f"{root}/0-overview.md" if stage is None else f"{root}/{stage['n']}-{stage['slug']}.md"


# -- The boundary: only the new plan folder ------------------------------------------

def linked(path: Path) -> bool:
    """A symlink, a junction or another reparse point."""

    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def target(path: Path, root: str, rel) -> Path:
    """The file `rel` names directly inside the plan folder `root` of the
    worktree `path`, resolved now, or `ValueError`: no absolute path, no `..`,
    no other folder, no link on the way, nothing but a plan file's name."""

    if not isinstance(rel, str) or "\\" in rel or rel.startswith("/") or re.match(r"[A-Za-z]:", rel):
        raise ValueError(f"받지 않는 경로다: {rel!r}")
    parts = rel.split("/")
    if parts[:-1] != root.split("/") or not NAME.fullmatch(parts[-1]):
        raise ValueError(f"`{root}/` 바로 아래의 계획 파일이 아니다: {rel!r}")
    here = path
    for part in parts:
        here = here / part
        if linked(here):
            raise ValueError(f"링크를 거쳐 가는 경로다: {rel!r}")
    file, folder = (path / rel).resolve(), (path / root).resolve()
    if file.parent != folder or path.resolve() not in folder.parents:
        raise ValueError(f"작업트리의 `{root}/` 밖이다: {rel!r}")
    return file


def committed(path: Path, paths: list[str], message: str) -> None:
    """Commit exactly `paths`; nothing else staged goes with them."""

    for args in (["git", "add", "--", *paths], ["git", "commit", "-q", "-m", message, "--", *paths]):
        done = specs.sh(args, path)
        if done.returncode:
            raise RuntimeError(f"{args[1]} 실패 — {specs.said(done)}")


def head_of(path: Path) -> str:
    return specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()


def clean(path: Path) -> bool:
    done = specs.sh(["git", "status", "--porcelain"], path)
    return not done.returncode and not done.stdout.strip()


def leftover(path: Path, root: str, manifest: list[dict]) -> list[Path] | None:
    """When what is uncommitted is only an interrupted write of these
    documents, the temporary files it left; otherwise `None`. A file there is
    ours only if it is a finished document whose content is its draft's, or a
    temporary file `atomic` names — a swap is whole, so a document with other
    content was not written by us, and it is never overwritten."""

    folder = path / root
    if linked(folder) or not folder.is_dir():
        return None
    hashes = {Path(e["path"]).name: e["sha256"] for e in manifest}
    temporary: list[Path] = []
    for f in folder.iterdir():
        if linked(f) or not f.is_file():
            return None
        if f.name in hashes:
            try:
                if sha(f.read_text(encoding="utf-8")) == hashes[f.name]:
                    continue
            except (OSError, UnicodeDecodeError):
                pass
            return None
        swap = TEMPORARY.fullmatch(f.name)
        if not swap or swap[1] not in hashes:
            return None
        temporary.append(f)
    done = specs.sh(["git", "-c", "core.quotepath=off", "status", "--porcelain", "-uall"], path)
    changed = {line[3:].strip('"') for line in done.stdout.splitlines() if line.strip()}
    if done.returncode or not changed <= {f"{root}/{f.name}" for f in folder.iterdir()}:
        return None
    return temporary


# -- Mechanical checks -----------------------------------------------------------------

def tiered(text: str) -> dict:
    """A refactor stage's one `Tier: L0–L3` and one `Files: a, b` line, or `ValueError`.
    Files are distinct repository-relative paths; an L3 stage changes a contract,
    so it also needs `## Migration`."""

    tier, files = re.findall(r"^Tier:[ \t]*(.*?)[ \t]*$", text, re.M), re.findall(r"^Files:(.*)$", text, re.M)
    names = [f.strip().strip("`") for f in (files or [""])[0].split(",") if f.strip()]
    if tier[:1] not in (["L0"], ["L1"], ["L2"], ["L3"]) or len(tier) != 1 or len(files) != 1 or not names:
        raise ValueError("리펙터링 단계는 `Tier: L0–L3` 줄과 비지 않은 `Files:` 줄을 하나씩 적어야 한다")
    try:
        if len(set(names)) != len(names) or not all(improvement.relative(n) for n in names):
            raise improvement.Refused("a file is named twice")
    except improvement.Refused as exc:
        raise ValueError(f"`Files:` 는 저장소 기준 상대 경로를 한 번씩 적는다: {', '.join(names)}") from exc
    if tier[0] == "L3" and not re.search(r"^##[ \t]+Migration", text, re.M):
        raise ValueError("L3 단계에는 `## Migration` 절이 있어야 한다")
    return {"tier": tier[0], "files": names}


def problems(repo: str, sid: str, spec: dict, path: Path) -> list[tuple[str, str]]:
    """`(file, what)` for each mechanical failure: structure, coverage, links,
    ids and sources. Semantic validity is the reviewer's; a field being there
    does not prove it right."""

    p = spec["planning"]
    root, outline = p["artifact_root"], p["outline"]
    out: list[tuple[str, str]] = []
    limits = p["limits"]
    if not all(isinstance(limits[k], (int, float)) and math.isfinite(limits[k]) and limits[k] > 0
               for k in ("seconds", "calls", "tokens")):
        out.append(("", "한도가 유한한 양수가 아니다"))
    expected = [file_of(root)] + [file_of(root, s) for s in outline["stages"]]
    manifest = {e["path"]: e for e in p["artifact_manifest"] or []}
    sources = {s["id"]: s for s in p["source_manifest"] or []}
    requirements = {r["id"] for r in outline["requirements"]}
    names = {Path(e).name for e in expected}
    for rel in expected:
        entry = manifest.get(rel)
        text = draft(repo, sid, rel) if entry else None
        if entry is None or text is None or sha(text) != entry["sha256"]:
            out.append((rel, "초안이 없거나 기록과 다르다"))
            continue
        stage = next((s for s in outline["stages"] if file_of(root, s) == rel), None)
        headings = {h.strip().lower() for h in re.findall(r"^##[ \t]+(.+?)[ \t]*$", text, re.M)}
        for h in OVERVIEW if stage is None else STAGE:
            if not any(x.startswith(h.lower()) for x in headings):
                out.append((rel, f"`## {h}` 절이 없다"))
        if stage is not None and p["input"].get("refactor"):
            try:
                tiered(text)
            except ValueError as exc:
                out.append((rel, str(exc)))
        for rid in entry["requirement_ids"]:
            if rid not in requirements:
                out.append((rel, f"없는 요구사항 `{rid}` 을 적었다"))
            elif not re.search(rf"\b{rid}\b", text):
                out.append((rel, f"요구사항 `{rid}` 을 맡는다면서 본문에 적지 않았다"))
        if stage is not None and set(stage["requirement_ids"]) - set(entry["requirement_ids"]):
            out.append((rel, f"개요가 맡긴 요구사항을 빠뜨렸다: {', '.join(stage['requirement_ids'])}"))
        for source in entry["source_ids"]:
            if source not in sources:
                out.append((rel, f"없는 출처 `{source}` 를 적었다"))
            elif not re.search(rf"\b{source}\b", text):
                out.append((rel, f"출처 `{source}` 를 적는다면서 본문에 없다"))
        if stage is None:
            if not entry["source_ids"]:
                out.append((rel, "개요가 출처를 하나도 적지 않았다"))
            for s in sources.values():
                if s["kind"] == "web" and s["url"] not in text:
                    out.append((rel, f"아래 출처 목록에 `{s['id']}` 의 URL 이 없다"))
        for link in LINK.findall(text):
            if re.match(r"[a-z]+:", link) or link.startswith("#"):
                continue
            where = link.split("#", 1)[0].removeprefix("./")
            if "/" not in where and where in names:
                continue
            resolved = (path / root / where).resolve()
            if path.resolve() not in resolved.parents or not resolved.exists():
                out.append((rel, f"링크 `{link}` 가 가리키는 곳이 없다"))
    return out


# -- One model turn ------------------------------------------------------------------------

def opened(path: Path, chat: ChatSession) -> work.Run:
    """A run the screens can tail as this worktree's turn."""

    run = work.Run(chat)
    with _lock:
        work._runs[str(path)] = run
    work.feed.put({"kind": "turn", "path": str(path), "turn": run.turn, "session_id": chat.id})
    return run


def consume(path: Path, run: work.Run, text: str) -> tuple[str, str, dict, int, int]:
    """One turn of `run.chat` onto `run`, kept on the worktree's record:
    `(final, failed, meta, tools, web)`. The record leaves out the CLI's
    session id, so a later session in this worktree never resumes this one."""

    work.remember(path, "user", text)
    final, failed, meta, tools, web = "", "", {}, 0, 0
    try:
        for ev in run.chat.say(text, run.halt):
            if ev.kind == "tool":
                tools += 1
                web += (ev.meta or {}).get("tool") in WEB
            elif ev.kind == "done":
                final, meta = ev.text, dict(ev.meta)
                if meta.pop("error", False):
                    failed = final or "완료된 답이 없다"
            elif ev.kind == "error":
                failed = ev.text
            run.put({"kind": ev.kind, "text": ev.text, "meta": ev.meta, "session_id": ev.session_id,
                     "parent_id": ev.parent_id})
    except Exception as exc:  # a broken turn still owes the screen a reason
        failed = f"{type(exc).__name__}: {exc}"
        run.put({"kind": "error", "text": failed, "meta": {}, "session_id": run.chat.id, "parent_id": None})
    finally:
        with run.wake:
            made = work.steps(run.events)
        try:
            work.remember(path, "assistant", final, error=failed, steps=made,
                          turn=run.turn, cell=run.chat.id, started_at=run.started_at, cancelled=run.halt.is_set(),
                          provider="codex" if run.chat.is_codex else "claude",
                          **{k: v for k, v in meta.items() if k != "session_id"})
        except OSError as exc:  # a lost transcript row must not take the turn's usage with it
            note(run, f"대화 기록을 남기지 못했다 — {exc}")
    return final, failed, meta, tools, web


def note(run: work.Run, text: str) -> None:
    run.put({"kind": "tool", "text": text, "meta": {}, "session_id": run.chat.id, "parent_id": None})


class Worker:
    """One plan's run of phases, on its own thread. `halt` is its cancel; the
    running turn's own stop is `run.halt`, which `/api/work/stop` sets too."""

    def __init__(self, repo: str, sid: str, planning: dict) -> None:
        self.repo, self.sid = repo, sid
        self.halt = threading.Event()
        self.chat: ChatSession | None = None
        self.run: work.Run | None = None
        self.thread: threading.Thread | None = None
        self.base = dict(planning["spent"])
        limits = planning["limits"]
        self.budget = Budget(seconds=max(0.0, limits["seconds"] - self.base["seconds"]),
                             calls=max(0, limits["calls"] - self.base["calls"]), candidates=0,
                             tokens=max(0, limits["tokens"] - self.base["tokens"]), cancel=self.halt)
        self.started = time.monotonic()
        self.tools, self.web, self.unknown = 0, 0, False
        self.reason = "cancelled"   # what a halt is recorded as: a person's cancel, or the server going down

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
        self.halt.set()
        run = self.run
        if run is not None and not run.done:
            run.halt.set()
            run.chat.stop(run.halt)

    def spent(self) -> dict:
        used = self.budget.used
        return {"seconds": round(self.base["seconds"] + time.monotonic() - self.started, 1),
                "calls": self.base["calls"] + used["calls"], "tokens": self.base["tokens"] + used["tokens"],
                "tools": self.base.get("tools", 0) + self.tools, "unknown": self.base.get("unknown") or self.unknown}

    def stop(self, reason: str, detail: str = "") -> None:
        halted(self.repo, self.sid, reason, detail, spent=self.spent())

    def planner(self, spec: dict, path: Path) -> ChatSession:
        """A's session, made once per worker and closed when the worker ends."""

        if self.chat is None:
            role = spec["planning"]["roles"]["planner"]
            self.chat = ChatSession(path, tools=PLANNER_TOOLS, system=PROMPT, model=role["model"],
                                    effort=role["effort"])
            planned(self.repo, self.sid, sessions={**spec["planning"].get("sessions", {}), "planner": {
                "cell": self.chat.id, "model": role["model"], "effort": role["effort"], "tools": PLANNER_TOOLS}})
        return self.chat

    def turn(self, spec: dict, path: Path, text: str) -> str | None:
        """One dispatched request to A: its final answer, or `None` after
        stopping with the reason. The allowance is taken before the request,
        the reported usage charged after it; the wall deadline stops the turn."""

        if self.unknown:
            self.stop("budget_unknown", "호스트가 사용량을 알려 주지 않았다 — 0 으로 치지 않고 멈춘다")
            return None
        try:
            self.budget.call()
        except Exhausted as exc:
            self.stop(str(exc), "한도를 다 썼다 — 더 보내지 않는다")
            return None
        except Cancelled:
            self.stop(self.reason)
            return None
        # On disk before it is sent: a restart mid-turn keeps the call and marks its spend unknown (`recover`).
        planned(self.repo, self.sid, spent=self.spent(), inflight=True)
        chat = self.planner(spec, path)
        run = self.run = opened(path, chat)
        if self.halt.is_set():
            run.halt.set()
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            run.halt.set()
            chat.stop(run.halt)

        timer = threading.Timer(self.budget.left(), expire)
        timer.daemon = True
        timer.start()
        try:
            final, failed, meta, tools, web = consume(path, run, text)
        except BaseException:
            # Sent, and whatever broke after it, its usage was never charged: unknown, never zero.
            self.unknown = True
            planned(self.repo, self.sid, spent=self.spent(), inflight=False)
            raise
        finally:
            timer.cancel()
            run.finish()
            self.run = None
        self.tools += tools
        self.web += web
        tokens = meta.get("tokens") or {}
        if type(tokens.get("in")) is int and type(tokens.get("out")) is int:
            self.budget.charge({"input_tokens": tokens["in"], "output_tokens": tokens["out"]})
        else:
            # A failed or stopped turn may have spent tokens too: never zero. This worker sends
            # nothing more; a person's `[재개]` goes on, with the spend shown as a lower bound.
            self.unknown = True
        fields = {"spent": self.spent(), "inflight": False}
        if self.budget.used["tokens"] > self.budget.limits["tokens"]:
            # Usage comes after the answer: the ceiling was crossed, not kept.
            fields["overrun"] = {"tokens": fields["spent"]["tokens"], "limit": spec["planning"]["limits"]["tokens"]}
        if chat.session_id:
            sessions = (specs.load(self.repo, self.sid) or spec)["planning"].get("sessions", {})
            fields["sessions"] = {**sessions, "planner": {**sessions.get("planner", {}), "session_id": chat.session_id}}
        planned(self.repo, self.sid, **fields)
        if expired.is_set():
            self.stop("deadline", "시간 한도에 닿아 턴을 끊었다")
        elif run.halt.is_set() or self.halt.is_set():
            self.halt.set()
            self.stop(self.reason)
        elif failed:
            self.stop("host", failed)
        else:
            return final
        return None

    def ask(self, spec: dict, path: Path, text: str, read):
        """A turn whose answer `read` parses; one retry for an answer it
        cannot read, then `format`."""

        final = self.turn(spec, path, text)
        if final is None:
            return None
        try:
            return read(final)
        except ValueError as exc:
            why = str(exc)
        final = self.turn(spec, path, f"Your answer could not be read: {why}\n\nAnswer the same phase again, "
                                      "ending with exactly the blocks it asks for.")
        if final is None:
            return None
        try:
            return read(final)
        except ValueError as exc:
            self.stop("format", str(exc))
            return None


_workers: dict[tuple[str, str], Worker] = {}


# -- The phases ----------------------------------------------------------------------------

def common(spec: dict) -> list[str]:
    p = spec["planning"]
    given = p["input"]
    out = ["## Goal", "", given["goal"], ""]
    if given["context"]:
        out += ["## Context from the person", "", given["context"], ""]
    if p.get("answers"):
        out += ["## The person's answers", ""] + [f"- {a['question']} → {a['choice']}" for a in p["answers"]] + [""]
    out += [f"The plan folder is `{p['artifact_root']}/`. Only the server writes it.", ""]
    return out


def sources_part(spec: dict) -> list[str]:
    return ["## Sources kept by the research", "", "```json",
            json.dumps(spec["planning"]["source_manifest"], ensure_ascii=False, indent=1), "```", ""]


def collect(worker: Worker, spec: dict, path: Path, repo: Path) -> None:
    """The server's snapshot: plans, pull requests, decisions, warnings. No model."""

    p = spec["planning"]
    if (path / p["artifact_root"]).exists() or linked(path / p["artifact_root"]):
        return worker.stop("root_exists", f"`{p['artifact_root']}` 가 이미 있다 — 새 폴더만 쓴다")
    gathered = specs.materials(repo).split("\n\n", 1)[-1][:MAX_MATERIALS]
    folder = drafts(worker.repo, worker.sid)
    folder.mkdir(parents=True, exist_ok=True)
    atomic(folder / "materials.md", gathered)
    planned(worker.repo, worker.sid, phase="research", base_head=head_of(path), materials_sha256=sha(gathered))


def research(worker: Worker, spec: dict, path: Path) -> None:
    p = spec["planning"]
    try:
        gathered = (drafts(worker.repo, worker.sid) / "materials.md").read_text(encoding="utf-8")
    except OSError:
        gathered = "(the snapshot is gone)"
    may_ask = not p.get("questions")
    text = "\n".join(["Phase: research.", "", *common(spec), "## What the server gathered", "", gathered, "",
                      "## This phase", "",
                      "Read the plans and decisions the goal touches, then search the web for the evidence its "
                      "decisions need."
                      + (" If a decision the person owns changes the scope, return only a `plan-questions` block "
                         "now; the research waits for the answers." if may_ask else "")
                      + " Otherwise end with a `plan-sources` block."])
    worker.web = 0

    def read(final: str):
        blocks = found(final)
        if may_ask and blocks.get("plan-questions"):
            return ("ask", questioned(blocks["plan-questions"][-1]))
        if not worker.web:
            # No search ran, so whatever sources it lists or leaves out, the web research did not happen.
            return ("sources", None)
        if not blocks.get("plan-sources"):
            raise ValueError("`plan-sources` 블록이 없다")
        return ("sources", sourced(blocks["plan-sources"][-1], path))

    got = worker.ask(spec, path, text, read)
    if got is None:
        return None
    kind, value = got
    if kind == "ask":
        planned(worker.repo, worker.sid, phase="clarify", questions=value, questions_rev=p.get("questions_rev", 0) + 1)
        return None
    if not worker.web:
        # A source list without a search behind it is a local lookup, not web research.
        return worker.stop("web_unavailable", "이 턴에 웹 검색·가져오기가 한 번도 돌지 않았다 — 웹 도구가 없는 호스트다")
    planned(worker.repo, worker.sid, phase="outline", source_manifest=value, web={"observed": worker.web})


def outline(worker: Worker, spec: dict, path: Path) -> None:
    p = spec["planning"]
    root, wanted = p["artifact_root"], p["input"]["stages"]
    overview = file_of(root)
    text = "\n".join(["Phase: outline.", "", *common(spec), *sources_part(spec), "## This phase", "",
                      "Choose the requirements and the stage boundaries"
                      + (f" — exactly {wanted} stages, as the person asked" if wanted else "")
                      + f". End with a `plan-outline` block and a `plan-file` block for `{overview}`."])

    def read(final: str):
        blocks = found(final)
        if not blocks.get("plan-outline") or not blocks.get("plan-file"):
            raise ValueError("`plan-outline` 과 `plan-file` 블록이 모두 있어야 한다")
        return outlined(blocks["plan-outline"][-1], wanted), filed(blocks["plan-file"][-1], {overview})

    got = worker.ask(spec, path, text, read)
    if got is None:
        return None
    made, file = got
    planned(worker.repo, worker.sid, phase="stages", outline=made,
            artifact_manifest=[drafted(worker.repo, worker.sid, file)])


def stages(worker: Worker, spec: dict, path: Path) -> None:
    """One turn per stage not drafted yet; a stop keeps the ones done."""

    p = spec["planning"]
    root, plan = p["artifact_root"], p["outline"]
    done = {e["path"] for e in p["artifact_manifest"]}
    for stage in plan["stages"]:
        rel = file_of(root, stage)
        if rel in done:
            continue
        text = "\n".join([f"Phase: stage {stage['n']} of {len(plan['stages'])}.", "", *common(spec),
                          *sources_part(spec), "## The outline", "", "```json",
                          json.dumps(plan, ensure_ascii=False, indent=1), "```", "", "## The overview", "",
                          draft(worker.repo, worker.sid, file_of(root)) or "(missing)", "", "## This phase", "",
                          f"Write stage {stage['n']}, `{stage['title']}`, serving "
                          f"{', '.join(stage['requirement_ids'])}. End with one `plan-file` block for `{rel}`."])

        def read(final: str, rel=rel):
            files = found(final).get("plan-file")
            if not files:
                raise ValueError("`plan-file` 블록이 없다")
            return filed(files[-1], {rel})

        file = worker.ask(spec, path, text, read)
        if file is None:
            return None
        spec = planned(worker.repo, worker.sid,
                       artifact_manifest=[*(specs.load(worker.repo, worker.sid) or spec)["planning"]["artifact_manifest"],
                                          drafted(worker.repo, worker.sid, file)])
    planned(worker.repo, worker.sid, phase="validate")


def validate(worker: Worker, spec: dict, path: Path) -> None:
    """The mechanical checks; one bounded repair turn, then a stop."""

    p = spec["planning"]
    errors = problems(worker.repo, worker.sid, spec, path)
    if not errors:
        planned(worker.repo, worker.sid, phase="publish", errors=[])
        return None
    shown = [f"{rel or '(plan)'}: {what}" for rel, what in errors]
    if p.get("repaired"):
        return worker.stop("invalid", "\n".join(shown))
    planned(worker.repo, worker.sid, errors=shown, repaired=True)
    allowed = {e["path"] for e in p["artifact_manifest"]}
    named = sorted({rel for rel, _ in errors if rel in allowed} or {file_of(p["artifact_root"])})
    text = "\n".join(["Phase: repair.", "", *common(spec), *sources_part(spec),
                      "## What the server's checks found", "", *(f"- {s}" for s in shown), "",
                      *(part for rel in named for part in (f"## `{rel}` as drafted", "",
                                                           draft(worker.repo, worker.sid, rel) or "(missing)", "")),
                      "## This phase", "",
                      "Return a `plan-file` block, whole, for each file that needs a change. Paths: "
                      + ", ".join(f"`{a}`" for a in sorted(allowed)) + "."])

    def read(final: str):
        files = found(final).get("plan-file")
        if not files:
            raise ValueError("`plan-file` 블록이 없다")
        return [filed(f, allowed) for f in files]

    files = worker.ask(spec, path, text, read)
    if files is None:
        return None
    replaced = {f["path"]: drafted(worker.repo, worker.sid, f) for f in files}
    current = (specs.load(worker.repo, worker.sid) or spec)["planning"]["artifact_manifest"]
    planned(worker.repo, worker.sid, artifact_manifest=[replaced.get(e["path"], e) for e in current])


def body_of(spec: dict) -> str:
    """The pull request's body, read by a person. `변경 요약` is what
    `harvest.record` reads as the record's what."""

    p = spec["planning"]
    lines = ["## 변경 요약", "", spec["goal"], "", "## 계획 문서", ""]
    lines += [f"- `{e['path']}`" for e in p["artifact_manifest"]]
    lines += ["", "## 근거", ""]
    lines += [f"- {s['id']} [{s['title']}]({s['url']}) · {s['retrieved']}" if s["kind"] == "web"
              else f"- {s['id']} `{s['url']}`" for s in p["source_manifest"]]
    lines += ["", "## 명세", "", f"`raw/specs/{spec['repo']}/{spec['id']}.json` · 계획자가 올리고 수정자와 "
              "리뷰어에게 넘긴다. 리뷰가 허용해도 구현과 머지는 자동으로 하지 않는다.", ""]
    return "\n".join(lines)


def publish(worker: Worker, spec: dict, path: Path) -> None:
    """Write the drafts into the new folder, commit exactly them, push, and
    open or recover the pull request. Each step is checked against what an
    interrupted earlier attempt may have left, so a resume never publishes
    twice: HEAD is recorded before the push, the pull request right after."""

    p = spec["planning"]
    root = p["artifact_root"]
    paths = [e["path"] for e in p["artifact_manifest"]]
    publication = dict(p.get("publication") or {})
    head = head_of(path)
    if publication.get("head"):
        # Recorded, then stopped before the pull request: push only that very commit.
        if head != publication["head"] or not clean(path):
            return worker.stop("worktree_moved", f"작업트리가 기록한 커밋 {publication['head'][:7]} 에서 움직였다")
    else:
        folder = path / root
        ours = all(kept(worker.repo, worker.sid, e) and (path / e["path"]).is_file()
                   and sha((path / e["path"]).read_text(encoding="utf-8")) == e["sha256"] for e in p["artifact_manifest"]) \
            and folder.is_dir() and {f.name for f in folder.iterdir()} == {Path(r).name for r in paths}
        if head != p["base_head"]:
            # Committed by an attempt a restart cut before it was recorded — or someone else's commit.
            listed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", p["base_head"], head], path)
            if listed.returncode or set(listed.stdout.split()) != set(paths) or not ours or not clean(path):
                return worker.stop("worktree_moved", f"작업트리의 HEAD 가 {p['base_head'][:7]} 에서 움직였다")
        else:
            # An earlier attempt may have written some of the files before it was cut; they are written again.
            partial = leftover(path, root, p["artifact_manifest"])
            if not clean(path) and partial is None:
                return worker.stop("worktree_moved", "작업트리에 커밋 안 된 변경이 있다")
            if (folder.exists() or linked(folder)) and partial is None:
                return worker.stop("root_exists", f"`{root}` 가 이미 있다 — 새 폴더만 쓴다")
            try:
                for swap in partial or []:   # left by a process killed mid-write
                    swap.unlink()
                for e in p["artifact_manifest"]:
                    text = draft(worker.repo, worker.sid, e["path"])
                    if text is None or sha(text) != e["sha256"]:
                        raise ValueError(f"`{e['path']}` 의 초안이 기록과 다르다")
                    file = target(path, root, e["path"])   # resolved right before the write
                    file.parent.mkdir(parents=True, exist_ok=True)
                    atomic(file, text)
                committed(path, paths, f"docs: add the plan {worker.sid}")
            except (ValueError, RuntimeError, OSError) as exc:
                return worker.stop("publish_failed", str(exc))
            head = head_of(path)
            listed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", p["base_head"], head], path)
            if not clean(path) or set(listed.stdout.split()) != set(paths):
                return worker.stop("publish_failed", "커밋이 계획 폴더 밖을 건드렸거나 작업트리가 깨끗하지 않다")
        publication = {"head": head, "pr": None}
        planned(worker.repo, worker.sid, publication=publication)
    if worker.halt.is_set():
        return worker.stop(worker.reason, "커밋은 했고 push 하지 않았다")
    branch = specs.branch_of(spec)
    pushed = specs.sh(["git", "push", "-u", "origin", branch], path, 120)
    if pushed.returncode:
        return worker.stop("publish_failed", f"push 실패 — {specs.said(pushed)}")
    base = specs.base_of(spec, path)
    if base.returncode or not base.stdout.strip():
        return worker.stop("publish_failed", f"기본 브랜치를 모른다 — {specs.said(base)}")
    if worker.halt.is_set():
        return worker.stop(worker.reason, "push 는 했고 PR 은 만들지 않았다")
    base = base.stdout.strip()
    try:
        n, url = specs.pull_request(path, branch, base, spec["goal"], body_of(spec))
    except RuntimeError as exc:
        return worker.stop("publish_failed", str(exc))
    with specs._files:
        spec = specs.load(worker.repo, worker.sid)
        spec["planning"] = {**spec["planning"], "phase": "handoff",
                            "publication": {**publication, "pr": {"number": n, "url": url}}}
        specs.save(specs.moved(spec, f"PR #{n}", fault=None,
                               pr={"number": n, "url": url, "base": base, "head": publication["head"],
                                   "branch": branch}))
    specs.told(channels.repo_for(worker.repo), spec, f"계획 PR #{n} — {spec['goal']}")


PHASES = {"research": research, "outline": outline, "stages": stages, "validate": validate, "publish": publish}


def walk(worker: Worker, path: Path, repo: Path) -> None:
    while True:
        spec = specs.load(worker.repo, worker.sid)
        if spec is None or not spec.get("planning"):
            return
        phase = spec["planning"]["phase"]
        if phase not in RUNNING:
            return
        if worker.halt.is_set():
            return worker.stop(worker.reason)
        if worker.unknown:
            return worker.stop("budget_unknown", "호스트가 사용량을 알려 주지 않았다 — 0 으로 치지 않고 멈춘다")
        if phase == "collect":
            collect(worker, spec, path, repo)
        else:
            PHASES[phase](worker, spec, path)


def handoff(repo: str, sid: str, path: Path) -> None:
    """A lets go: its session is closed already, and a context row keeps any
    later session in this worktree from resuming it. The published PR waits
    for an explicit review request; its cells keep the roles the plan named."""

    work.remember(path, "context", "계획자 인계 — 이 뒤의 세션은 계획자의 대화를 잇지 않는다")
    specs.reviewed(specs.load(repo, sid))
    planned(repo, sid, handed={"ts": time.time()})


def drive(worker: Worker, path: Path, repo: Path, release) -> None:
    try:
        _drive(worker, path, repo, release)
    finally:
        with _lock:
            if _workers.get((worker.repo, worker.sid)) is worker:
                del _workers[(worker.repo, worker.sid)]


def _drive(worker: Worker, path: Path, repo: Path, release) -> None:
    try:
        walk(worker, path, repo)
    except Exception as exc:  # a broken worker still owes the plan a reason
        worker.stop("broken", f"{type(exc).__name__}: {exc}")
    finally:
        if worker.chat is not None:
            worker.chat.close()
        planned(worker.repo, worker.sid, spent=worker.spent())
        release()
    try:
        spec = specs.load(worker.repo, worker.sid)
        if spec and spec["planning"]["phase"] == "handoff" and not spec["planning"].get("handed"):
            if worker.halt.is_set():
                halted(worker.repo, worker.sid, worker.reason, "PR 은 올렸고 리뷰로 넘기지 않았다")
            else:
                handoff(worker.repo, worker.sid, path)
    except Exception as exc:  # after the worker let go: nobody else would say it failed
        halted(worker.repo, worker.sid, "broken", f"인계하지 못했다 — {type(exc).__name__}: {exc}")


def launch(repo: Path, spec: dict, release) -> None:
    """Start the worker on its own thread; it owns `release` from here."""

    worker = Worker(repo.name, spec["id"], spec["planning"])
    with _lock:
        if runtime.stopping.is_set():
            release()
            raise HTTPException(503, "서버가 종료 중이다. 다시 시작한 뒤 이어가라")
        if (repo.name, spec["id"]) in _workers:
            release()
            raise HTTPException(409, "이 계획은 이미 돌고 있다")
        _workers[(repo.name, spec["id"])] = worker
        try:
            worker.thread = threading.Thread(target=drive, args=(worker, Path(spec["worktree"]), repo, release),
                                             daemon=True)
            worker.thread.start()
        except BaseException:
            _workers.pop((repo.name, spec["id"]), None)
            release()
            raise


def held(path: Path):
    return hold(work._busy, _lock, str(path), "그 작업트리를 다른 요청이 쓰고 있다", kind="turn")


def close_all() -> None:
    """At shutdown: each worker stops its turn — the planner's process goes —
    and records the stop as a restart, which `[재개]` takes on."""

    with _lock:
        running = list(_workers.values())
    for worker in running:
        worker.cancel("restart")
    for worker in running:
        if worker.thread is not None:
            worker.thread.join()


def recover() -> None:
    """At start-up: a worker the server went down with stopped with it. No
    write or publication is replayed by itself; `[재개]` takes it on."""

    for folder in specs.SPECS.glob("*"):
        if folder.is_dir():
            for spec in specs.listing(folder.name):
                p = spec.get("planning") or {}
                if p.get("phase") in RUNNING:
                    # A turn cut in flight was sent and never charged: its spend is unknown, not zero.
                    unknown = {"spent": {**p["spent"], "unknown": True}} if p.get("inflight") else {}
                    halted(folder.name, spec["id"], "restart", "서버가 다시 켜졌다", inflight=False, **unknown)
                elif p.get("phase") == "handoff" and not p.get("handed"):
                    pr = ((p.get("publication") or {}).get("pr") or {}).get("number")
                    if spec["state"] == f"PR #{pr}":
                        # Published, and the server went down before the loop took the pull request.
                        halted(folder.name, spec["id"], "restart", "PR 은 올렸고 리뷰로 넘기기 전에 서버가 다시 켜졌다")
                    else:
                        # The loop took it (`kick` moved the state); only the mark was cut.
                        planned(folder.name, spec["id"], handed={"ts": time.time()})


# -- The document revision -----------------------------------------------------------------

def revise(lp, spec: dict, path: Path, text: str) -> str | None:
    """The reviser's turn for the review loop (`loop.told`): a read-only
    session returns whole replacement files, and the server writes those
    inside the plan's own folder and commits exactly them. Its final answer,
    which carries the disposition; `None` when the loop was stopped."""

    release = loop.wait_hold(lp, path)
    if release is None:
        return None
    role = spec["planning"]["roles"]["reviser"]
    chat = ChatSession(path, tools=REVISER_TOOLS, system=REVISER_PROMPT, model=role["model"], effort=role["effort"])
    try:
        run = lp.run = opened(path, chat)
        if lp.halt.is_set():
            run.halt.set()
        root = spec["artifact_root"]
        final, failed, _, _, _ = consume(path, run, f"{text}\n\nThe plan's documents are under `{root}/`.")
        if lp.halt.is_set():
            return None
        if failed or run.halt.is_set():
            # A turn stopped on its own (`/api/work/stop`) writes nothing, as a failed one.
            return ""
        try:
            # Any path is let through the shape check; `target` judges it before the write.
            files = [filed(f, {f.get("path")} if isinstance(f, dict) else set())
                     for f in found(final).get("plan-file", [])]
            # Every file checked before any is written: one bad path writes none.
            for f in files:
                target(path, root, f["path"])
            for f in files:
                atomic(target(path, root, f["path"]), f["content"])
            paths = [f["path"] for f in files]
            if paths and specs.sh(["git", "status", "--porcelain", "--", *paths], path).stdout.strip():
                committed(path, paths, "docs: revise the plan after review")
                note(run, f"계획 문서 {len(paths)}개를 고쳐 커밋했다")
        except (ValueError, RuntimeError, OSError) as exc:
            note(run, f"수정본을 쓰지 않았다 — {exc}")
        return final
    finally:
        chat.close()
        if lp.run is not None:
            lp.run.finish()
        lp.run = None
        release()


# -- The screen ----------------------------------------------------------------------------

def mine(repo: Path, sid: str) -> dict:
    spec = specs.load(repo.name, sid)
    if spec is None or not spec.get("planning"):
        raise HTTPException(404, "그런 계획이 없다")
    return spec


def shown(repo: Path, spec: dict) -> dict:
    with _lock:
        worker = _workers.get((spec["repo"], spec["id"]))
        run = work._runs.get(spec.get("worktree") or "")
    running = worker is not None and run is not None and not run.done
    return {**specs.view(repo, spec),
            "running": {"path": spec["worktree"], "turn": run.turn, "session_id": run.session_id} if running else None}


@router.post("/api/plans")
def start(body: Plan) -> dict:
    return begun(body)


def begun(body: Plan, create: bool = True) -> dict:
    """`[계획]`: the worktree and the spec, then the worker. The same key with
    the same input is the same plan; with other input, a conflict. With
    `create=False` it only finds: a key no spec carries is a 404, decided in
    the same lookup that would otherwise create."""

    checked(body)
    with _lock:
        repo = current_repo()
    gate = specs.gate_of(repo)
    if not gate:
        raise HTTPException(409, "연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
    given = normalized(body)
    revision = digest(given)
    with _creating:
        old = next((s for s in specs.listing(repo.name)
                    if (s.get("planning") or {}).get("request_id") == body.request_id), None)
        if old is not None:
            if old["planning"]["input_revision"] != revision:
                raise HTTPException(409, "같은 요청 키에 다른 입력이다 — 새 계획은 새 키로")
            return shown(repo, old)
        if not create:
            raise HTTPException(404, "이 요청 키의 계획이 없다")
        sid = specs.unique(repo, given["slug"] or f"plan-{revision[:8]}")
        root = f"docs/plans/{sid}"
        with _lock:
            specs.checkout_idle(repo)
            release = held(repo)
        try:
            try:
                path, before, previous = specs.fork(repo, sid)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
            now = time.time()
            work.forget(path)
            roles = given["roles"]
            spec = {"id": sid, "repo": repo.name, "rev": 1, "goal": given["goal"], "out": [], "done": [gate],
                    "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [],
                    "review_profile": "plan", "review_profile_version": specs.PROFILE_VERSION, "artifact_root": root,
                    "source": {"focus": "plan", "turn": now, "plan": None}, "state": "작업 중", "stopped": None,
                    "worktree": str(path), "workspace_mode": "branch", "branch": sid,
                    "start_head": before, "return_branch": previous,
                    "pr": None, "report": None, "gate": None, "fault": None,
                    # The reviser works the pull request, the reviewer checks it: never A.
                    "cell": roles["reviser"], "reviewer": roles["reviewer"],
                    "history": [{"ts": now, "state": "정리됨"}, {"ts": now, "state": "작업 중"}],
                    "planning": {"version": VERSION, "phase": "collect", "request_id": body.request_id,
                                 "input_revision": revision, "input": given, "artifact_root": root,
                                 "roles": roles, "limits": given["limits"],
                                 "spent": {"seconds": 0, "calls": 0, "tokens": 0, "tools": 0, "unknown": False},
                                 "questions": [], "questions_rev": 0, "answers": None, "source_manifest": None,
                                 "outline": None, "artifact_manifest": None, "sessions": {},
                                 "publication": {"head": None, "pr": None}, "stopped": None}}
            with specs._files:
                specs.save(spec)
        except BaseException:
            release()
            raise
    launch(repo, spec, release)
    return shown(repo, spec)


@router.get("/api/plans/{sid}")
def status(sid: str) -> dict:
    repo = current_repo()
    return shown(repo, mine(repo, sid))


class Answer(BaseModel):
    id: str
    choice: str


class Answers(BaseModel):
    revision: int
    answers: list[Answer]


@router.post("/api/plans/{sid}/answers")
def answer(sid: str, body: Answers) -> dict:
    """Answers to the questions standing now, all of them; the research goes on."""

    with _lock:
        repo = current_repo()
    spec = mine(repo, sid)
    with _lock:
        release = held(Path(spec["worktree"]))
    try:
        with specs._files:
            spec = mine(repo, sid)
            p = spec["planning"]
            if p["phase"] != "clarify":
                raise HTTPException(409, f"기다리는 질문이 없다 — {p['phase']}")
            if body.revision != p["questions_rev"]:
                raise HTTPException(409, "다른 판의 질문에 한 답이다")
            given = {a.id: a.choice.strip() for a in body.answers}
            asked = {q["id"]: q for q in p["questions"]}
            if set(given) != set(asked) or not all(given.values()):
                raise HTTPException(400, "질문마다 답이 하나씩 있어야 한다")
            spec["planning"] = {**p, "phase": "research",
                                "answers": [{"id": q, "question": asked[q]["question"], "choice": given[q]}
                                            for q in asked]}
            specs.save(spec)
    except BaseException:
        release()
        raise
    launch(repo, spec, release)
    return shown(repo, spec)


def resumed(repo: Path, spec: dict, path: Path) -> tuple[str, list[dict]]:
    """Where a stopped plan goes on, and the drafts that still stand. A draft
    changed or gone since it was recorded is dropped with what builds on it."""

    p = spec["planning"]
    back = p["stopped"]["phase"]
    publication = p.get("publication") or {}
    manifest = p.get("artifact_manifest") or []
    if publication.get("pr"):
        return "handoff", manifest
    if publication.get("head"):
        return "publish", manifest
    if p.get("base_head") and head_of(path) != p["base_head"]:
        if back == "publish":
            # Perhaps our own commit, cut before it was recorded; `publish` tells it from anyone else's.
            return "publish", manifest
        raise HTTPException(409, "작업트리의 HEAD 가 계획을 시작한 뒤 움직였다 — 새 계획으로")
    if back in ("collect", "clarify", "research", "outline"):
        return back, manifest
    standing = [e for e in manifest if kept(repo.name, spec["id"], e)]
    if not any(e["path"] == file_of(p["artifact_root"]) for e in standing):
        return "outline", []
    if len(standing) < len(manifest) or back == "stages":
        return "stages", standing
    return back, standing


@router.post("/api/plans/{sid}/resume")
def resume(sid: str) -> dict:
    """`[재개]` of a stopped plan, after its input and drafts are checked."""

    with _lock:
        repo = current_repo()
    spec = mine(repo, sid)
    path = work.ours(spec["worktree"] or "", repo)
    with _lock:
        release = held(path)
    try:
        with specs._files:
            spec = mine(repo, sid)
            p = spec["planning"]
            if p["phase"] != "stopped":
                raise HTTPException(409, f"멈춘 계획만 잇는다 — {p['phase']}")
            if digest(p["input"]) != p["input_revision"]:
                raise HTTPException(409, "입력이 기록과 다르다 — 새 계획으로")
            phase, manifest = resumed(repo, spec, path)
            spec["planning"] = {**p, "phase": phase, "stopped": None, "artifact_manifest": manifest or None,
                                "repaired": False}
            specs.save(spec)
    except BaseException:
        release()
        raise
    launch(repo, spec, release)
    return shown(repo, spec)


@router.post("/api/plans/{sid}/cancel")
def cancel(sid: str) -> dict:
    """Stop the running turn and the worker; what is drafted, committed or
    opened stays. A plan waiting on answers stops too."""

    repo = current_repo()
    spec = mine(repo, sid)
    with _lock:
        worker = _workers.get((repo.name, sid))
    if worker is not None:
        worker.cancel()
        if worker.thread is not None:
            worker.thread.join(work.HALT_WAIT)
    elif spec["planning"]["phase"] in ("clarify", *RUNNING):
        halted(repo.name, sid, "cancelled")
    return shown(repo, mine(repo, sid))
