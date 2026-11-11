"""oneshot S2–R1: the spec conversation, the backend design and the R1 sheet, on the spec the bootstrap made.

`docs/plans/oneshot/3-spec-backend.md` is the design.
From the commit point on, the spec's `oneshot` field owns the run.
Its `stage` moves `spec → design → r1 → tests`; `tests` is where the backend build begins.
Requirements live in the spec's `requirements`, in the shape `oneshot_sheet.requirements` checks.
The glossary, the conversation, the design and the sheet's decisions live in `oneshot`.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import is_junction

from . import oneshot, oneshot_sheet as sheet, specs
from .oneshot_profile import current, environment, stacked, unsettled
from .query import ROOT

SPEC_PROMPT = (ROOT / "tool/prompts/oneshot-spec.md").read_text(encoding="utf-8")
DESIGN_PROMPT = (ROOT / "tool/prompts/oneshot-design.md").read_text(encoding="utf-8")
RUNNING = {"spec": "talk", "design": "design", "r1": "redesign"}   # a later stage's module adds its own
SHEETS = {"r1"}   # a stage whose sheet stands when its edit turn stopped
TURNS = {"tests", "build"}   # a stage run as a work turn (`oneshot_build.STAGES`), resumed by sending it again
VIEWS: list = []   # a later stage's fields for the screen, `fn(spec) -> dict`
MAX_TALK = 4_000

router = APIRouter()


def store(repo: str) -> oneshot.Store:
    def get() -> dict:
        return spec_of(repo)["oneshot"]

    def put(**fields) -> dict:
        return saved(repo, oneshot=fields)["oneshot"]

    return oneshot.Store(f"spec:{repo}", get, put, "stage", oneshot.RUNS / f"spec-{repo}", lambda o: True)


def spec_of(repo: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", repo):   # it came from the screen: a name, never a path
        raise HTTPException(404, "그런 저장소가 없다")
    spec = specs.load(repo, oneshot.BRANCH)
    if spec is None or "oneshot" not in spec:
        raise HTTPException(404, "원샷 명세가 없다")
    return spec


def saved(repo: str, **fields) -> dict:
    """Top-level spec fields and `oneshot` fields (`oneshot={...}` merges) written in one save."""

    with oneshot._lock:
        spec = spec_of(repo)
        merged = {**spec["oneshot"], **fields.pop("oneshot", {})}
        spec.update(fields, oneshot=merged)
        specs.save(spec)
        return spec


# -- S2 the spec conversation --------------------------------------------------------

def ended(reqs: list[dict], stack: list[dict] | None = None) -> str:
    """Empty when the conversation may end, or what is still open. The server decides, never the agent.
    `stack` is S2's; an R1 edit (`None`) cannot change it."""

    if not [r for r in reqs if r["source"]["kind"] == "original"]:
        return "주인의 말에서 온 요구(`original`)가 하나도 없다"
    if open_ := [r["id"] for r in reqs if r["status"] == "undecided"]:
        return f"아직 정하지 않은 요구가 있다: {', '.join(open_)}"
    covered = {d for s in sheet.scenarios(reqs) for d in [s["id"], *s.get("depends_on", [])]}
    if bare := [r["id"] for r in reqs if r["source"]["kind"] == "original" and r["id"] not in covered]:
        return f"시나리오가 없는 원래 요구가 있다: {', '.join(bare)}"
    if stack is not None and (open_ := unsettled(stack)):
        return f"주인이 아직 고르지 않은 스택: {', '.join(open_)}"
    return ""


def owner_words(o: dict) -> str:
    return "\n".join([o["sentence"], *[t["text"] for t in o.get("talk") or [] if t["role"] == "owner"]])


def kept(spec: dict, value, where: str) -> tuple[list[dict], list[dict]]:
    """An `oneshot-spec` block as the spec will store it: each kept requirement's stored `source`,
    a new one counted as a decision outside S2, no original requirement
    dropped, and no term the owner used renamed or removed."""

    reqs, glossary = sheet.requirement_block(value)
    previous, o = spec.get("requirements") or [], spec["oneshot"]
    sources = {r["id"]: r["source"] for r in previous}
    if gone := [r["id"] for r in previous if r["source"]["kind"] == "original" and r["id"] not in {x["id"] for x in reqs}]:
        raise ValueError(f"주인의 말에서 온 요구를 지웠다: {', '.join(gone)}")
    reqs = [{**r, "source": sources.get(r["id"]) or (r["source"] if where == "S2" else
                                                      {"kind": "decision", "locator": where})} for r in reqs]
    if found := sheet.cycle({r["id"]: r for r in reqs}):   # refused here, where the requirements can still change
        raise ValueError(f"요구의 `depends_on` 이 돌고 돈다 — {' → '.join(found)}")
    words = owner_words(o)
    if renamed := [g["term"] for g in o.get("glossary") or []
                   if g["term"] in words and g["term"] not in {x["term"] for x in glossary}]:
        raise ValueError(f"주인이 쓴 용어를 바꾸거나 지웠다: {', '.join(renamed)}")
    return reqs, glossary


def unanswered(o: dict, stack: list[dict]) -> list[str]:
    """The parts `stack` marks `confirmed` that no owner's answer stands behind: confirmed already with the same
    choice, or offered in the stack before the question the owner has just answered, and nothing else.
    ponytail: that the answered question was about the stack is the prompt's to keep; the owner then sees the
    stack again on R1."""

    talk, before = o.get("talk") or [], {s["part"]: s for s in o.get("stack") or []}
    answered = len(talk) >= 2 and talk[-1]["role"] == "owner" and bool(talk[-2].get("choices"))
    return [s["part"] for s in stack if s["status"] == "confirmed"
            and not (before.get(s["part"], {}).get("status") == "confirmed" and before[s["part"]]["choice"] == s["choice"])
            and not (answered and s["part"] in before)]


def spec_turn(final: str, spec: dict):
    found = {m.group(1): m.group(2) for m in oneshot.BLOCK.finditer(final)}
    if "oneshot-spec" not in found:
        raise ValueError("`oneshot-spec` 블록이 하나 있어야 한다")
    value = json.loads(found["oneshot-spec"])
    reqs, glossary = kept(spec, value, "S2")   # refuses anything but an object first
    stack = stacked(value.get("stack") or [])
    if early := unanswered(spec["oneshot"], stack):
        raise ValueError("주인이 답하기 전에 `confirmed` 로 둔 스택 부분이 있다 — 묶음을 묻고, 주인의 답을 받은 다음 턴에 "
                         f"확정한다: {', '.join(early)}")
    choices = None
    if "choices" in found:
        choices = json.loads(found["choices"])
        specs._shape("choices", choices)
        if len(choices.get("questions", [choices])) > 3:
            raise ValueError("한 번에 묻는 `choices` 는 세 개까지다")
    said = oneshot.BLOCK.sub("", final).strip()
    return reqs, glossary, stack, choices, said


def talk(repo: str) -> None:
    """One S2 turn: the requirements and glossary replaced whole, at most three questions asked."""

    spec = spec_of(repo)
    o = spec["oneshot"]
    text = (f"The owner's sentence:\n\n{o['sentence']}\n\nThe confirmed restatement:\n\n"
            f"{json.dumps(o['intent'], ensure_ascii=False)}\n\nThe environment profile:\n\n"
            f"{json.dumps(current(o['profile']), ensure_ascii=False)}\n\nThe stack so far:\n\n"
            f"{json.dumps(o.get('stack') or [], ensure_ascii=False)}\n\nThe requirements so far:\n\n"
            f"{json.dumps(spec.get('requirements') or [], ensure_ascii=False)}\n\nThe glossary so far:\n\n"
            f"{json.dumps(o.get('glossary') or [], ensure_ascii=False)}\n\nThe conversation so far:\n\n"
            + ("\n\n".join(f"[{t['role']}] {t['text']}" for t in o.get("talk") or []) or "(none yet: this is the first turn)"))
    found = oneshot.turned(store(repo), "spec", SPEC_PROMPT, text, "oneshot-spec,choices",
                           lambda final, _: spec_turn(final, spec))
    if found is None:
        return
    reqs, glossary, stack, choices, said = found
    # A question asked in this turn waits for its answer, whatever else is settled.
    why = ended(reqs, stack) or ("주인의 답을 기다리는 질문이 있다" if choices else "")
    turns = [*(o.get("talk") or []), {"role": "agent", "text": said, "choices": choices, "ts": time.time()}]
    # The stack now lives in this spec, in version 3's shape, whatever version the run started at.
    fields = {"glossary": glossary, "stack": stack, "talk": turns, "version": max(o.get("version", 1), 3)}
    if not why:
        saved(repo, requirements=reqs, oneshot={**fields, "open": ""})
        handoff(repo, "design", stage="design")
    else:
        saved(repo, requirements=reqs, oneshot={**fields, "open": why, "working": None})


def begin(repo: str) -> None:
    """At the commit point: the first S2 turn."""

    handoff(repo, "talk")


def handoff(repo: str, work: str, **fields) -> None:
    """The next stage started by the stage before it. A refusal (the server going down) leaves the
    new stage stopped with `restart`, so `[재개]` takes it on."""

    try:
        start(repo, work, **fields)
    except HTTPException:
        saved(repo, oneshot={**fields, "working": None})
        store(repo).stop("restart", "서버가 꺼지는 중이라 다음 단계를 시작하지 못했다")


# -- S3 the backend design -------------------------------------------------------------

def blocks(final: str) -> dict:
    """Every fenced block of an answer, each once and each JSON."""

    found = {}
    for m in oneshot.BLOCK.finditer(final):
        if m.group(1) in found:
            raise ValueError(f"`{m.group(1)}` 블록이 둘 이상이다")
        try:
            found[m.group(1)] = json.loads(m.group(2))
        except ValueError as exc:
            raise ValueError(f"`{m.group(1)}` 블록이 JSON 이 아니다 — {exc}") from exc
    return found


def marked(items: list[str], comments: dict, reach: list[str], old: dict, new: dict, decisions: dict) -> tuple[dict, dict]:
    """After an edit turn, `(marks, decisions kept)`: an edited, a changed or a re-shown item loses its approval."""

    marks = {}
    for item in items:
        if item in comments:
            marks[item] = "edited"
        elif old.get(item) != new.get(item):
            marks[item] = "consequence"
        elif item in reach:
            marks[item] = "again"
    return marks, {k: v for k, v in decisions.items() if k not in marks and v.get("approve")}


def design_turn(final: str, attempt: int, spec: dict, partial: set[str] | None = None):
    found = blocks(final)
    reqs, glossary = spec.get("requirements") or [], spec["oneshot"].get("glossary") or []
    if partial is not None and "oneshot-spec" in found:
        reqs, glossary = kept(spec, found["oneshot-spec"], f"R1 {spec['oneshot']['r1']['round']}회차 고침")
        if why := ended(reqs):
            raise ValueError(why)
    design = sheet.parsed(found)
    if partial is not None:   # an approval outside the reach survives, so nothing outside it may move
        old, new = sheet.every(spec["oneshot"]["design"], spec.get("requirements") or []), sheet.every(design, reqs)
        if moved := sorted(i for i in old if i not in partial and old[i] != new.get(i)):
            raise ValueError(f"고치라고 한 범위 밖 항목을 바꾸거나 지웠다: {', '.join(moved)} — 그대로 두어라")
    sheet.checked(design, reqs, glossary, spec["oneshot"]["profile"])
    warnings = sheet.soft(design, reqs, glossary)
    if warnings and attempt == 0:
        raise ValueError("다음을 고쳐 다시 내라 — " + " / ".join(warnings[:12]))
    return design, reqs, glossary, warnings


def design_text(spec: dict) -> str:
    o = spec["oneshot"]
    return (f"The owner's sentence:\n\n{o['sentence']}\n\nThe confirmed restatement:\n\n"
            f"{json.dumps(o['intent'], ensure_ascii=False)}\n\nThe environment profile and the stack:\n\n"
            f"{environment(o)}\n\nThe requirements:\n\n"
            f"{json.dumps(spec.get('requirements') or [], ensure_ascii=False)}\n\nThe glossary:\n\n"
            f"{json.dumps(o.get('glossary') or [], ensure_ascii=False)}\n")


def design(repo: str) -> None:
    """One S3 turn in the worktree, read-only; the server checks the blocks and shows the sheet."""

    spec = spec_of(repo)
    found = oneshot.turned(store(repo), "design", DESIGN_PROMPT, design_text(spec), sheet.BLOCKS,
                           lambda final, attempt: design_turn(final, attempt, spec), Path(spec["worktree"]))
    if found is None:
        return
    made, _, _, warnings = found
    saved(repo, oneshot={"design": made, "stage": "r1", "working": None,
                         "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": warnings}})


# -- R1 the sheet and its decisions ------------------------------------------------------

def redesign(repo: str, comments: dict[str, str], reach: list[str]) -> None:
    """The owner's edits and everything built on them, regenerated in one turn and checked whole."""

    spec = spec_of(repo)
    text = (design_text(spec) + f"\nThe current design:\n\n{json.dumps(spec['oneshot']['design'], ensure_ascii=False)}"
            f"\n\nThe owner edited these items, with these comments:\n\n{json.dumps(comments, ensure_ascii=False)}"
            f"\n\nReplace every item in this set and keep the rest as it is: {', '.join(reach)}. Return all five "
            "design blocks in full, and an `oneshot-spec` block in full when a requirement changes.\n")
    found = oneshot.turned(store(repo), "design", DESIGN_PROMPT, text, sheet.BLOCKS + ",oneshot-spec",
                           lambda final, attempt: design_turn(final, attempt, spec, set(reach)), Path(spec["worktree"]))
    if found is None:
        return
    made, reqs, glossary, warnings = found
    old = sheet.every(spec["oneshot"]["design"], spec.get("requirements") or [])
    new = sheet.every(made, reqs)
    r1 = spec["oneshot"]["r1"]
    marks, decisions = marked(sheet.decidable(made, reqs), comments, reach, old, new, r1["decisions"])
    saved(repo, requirements=reqs, oneshot={
        "design": made, "glossary": glossary, "working": None,
        "r1": {"round": r1["round"] + 1, "decisions": decisions, "marks": marks, "warnings": warnings}})


def approved(repo: str) -> None:
    """Every item approved: the design files written into the worktree and committed, and the build may begin."""

    spec = spec_of(repo)
    tree, o = own_tree(spec), spec["oneshot"]
    docs = tree and (tree / "docs" / "design").resolve()
    if not tree or not docs.is_relative_to(tree):
        store(repo).stop("folder", f"작업트리가 `{oneshot.BRANCH}` 브랜치의 그 작업트리가 아니다: {spec['worktree']}")
        return
    files = ["docs/design/backend.md", "docs/design/api.yaml"]
    if linked := [f for f in files if (tree / f).is_symlink() or not (tree / f).resolve().is_relative_to(tree)]:
        store(repo).stop("folder", f"설계 문서 자리가 작업트리 밖을 가리킨다: {', '.join(linked)}")
        return
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "backend.md").write_text(sheet.sheet(spec), encoding="utf-8", newline="\n")
    (docs / "api.yaml").write_text(sheet.openapi(spec), encoding="utf-8", newline="\n")
    head = commit(repo, tree, files, f"Record the approved backend design (R1 round {o['r1']['round']})")
    if head:
        saved(repo, oneshot={"stage": "tests", "design_head": head, "working": "tests"})   # the turn comes next


def within(base: Path, *parts: str) -> Path | None:
    """`base / parts`, or `None` when a link or a junction on the way, or where it resolves, leaves `base`."""

    steps = [base.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
    if any(p.is_symlink() or is_junction(p) for p in steps):
        return None
    return steps[-1] if steps[-1].resolve().is_relative_to(base.resolve()) else None


def own_tree(spec: dict) -> Path | None:
    """The run's worktree, resolved, when it is the top of a checkout on the run's branch."""

    tree = Path(spec["worktree"]).resolve()
    top = specs.sh(["git", "rev-parse", "--show-toplevel"], tree, 30).stdout.strip()
    branch = specs.sh(["git", "branch", "--show-current"], tree, 30).stdout.strip()
    return tree if top and Path(top).resolve() == tree and branch == oneshot.BRANCH else None


def commit(repo: str, tree: Path, files: list[str], message: str) -> str | None:
    """Exactly `files` committed, whatever else is staged: the new HEAD, or `None` after stopping with `folder`."""

    for args in (["add", "--", *files], ["commit", "-m", message, "--", *files]):
        done = specs.sh(["git", *args], tree, 60)
        if done.returncode and not re.search("nothing to commit|no changes added", done.stdout + done.stderr):
            store(repo).stop("folder", f"설계 문서를 커밋하지 못했다 — {(done.stderr or done.stdout).strip()[-300:]}")
            return None
    return specs.sh(["git", "rev-parse", "HEAD"], tree, 30).stdout.strip()


def decided(items: list[str], earlier: dict, given: dict) -> tuple[dict, dict[str, str]]:
    """A sheet's decisions merged over the earlier ones, `(decisions, edit comments)`; every item needs one."""

    if unknown := sorted(set(given) - set(items)):
        raise HTTPException(400, f"시트에 없는 항목이다: {', '.join(unknown)}")
    decisions = {**earlier, **given}
    if missing := [i for i in items if i not in decisions]:
        raise HTTPException(400, f"아직 정하지 않은 항목이 있다: {', '.join(missing)}")
    comments = {}
    for item, d in decisions.items():
        if d.get("approve") is True and not d.get("edit"):
            continue
        if not isinstance(d.get("edit"), str) or not d["edit"].strip():
            raise HTTPException(400, f"{item}: 승인하거나 고칠 내용을 적어라")
        comments[item] = d["edit"].strip()
    return decisions, comments


WORKERS = {"talk": talk, "design": design, "redesign": redesign}   # a later stage's module adds its own


def start(repo: str, work: str, *args, **fields) -> None:
    oneshot.start_worker(store(repo), work, WORKERS[work], (repo, *args), fields)


def recover() -> None:
    """A stage running when the server stopped, and a first S2 turn the bootstrap's hand-over never
    started, are both left stopped with `restart`, so `[재개]` takes them on."""

    for folder in specs.SPECS.glob("*"):
        spec = specs.load(folder.name, oneshot.BRANCH) if folder.is_dir() else None
        o = (spec or {}).get("oneshot") or {}
        if o and spec.get("adapter"):   # a restart can cut a turn before its end clears the adapter grant
            saved(folder.name, adapter=False)
        if o.get("working") or (o and o.get("stage") == "spec" and not o.get("talk") and not o.get("stopped")
                                and handed(o)):
            oneshot.restarted(store(folder.name))


def handed(o: dict) -> bool:
    """The run's commit point is written: the spec, not the bootstrap, owns what comes next."""

    try:
        return bool(oneshot.load(o.get("run") or "").get("committed"))
    except HTTPException:
        return False


# -- HTTP ---------------------------------------------------------------------------------

class Say(BaseModel):
    text: str


class Decisions(BaseModel):
    decisions: dict[str, dict]
    reading: dict = {}   # how the sheet was read: `seconds` open and the folds `opened`


WAITS = {"r1", "board", "r2", "r3"}   # stages that wait for the owner; `spec` waits on the agent's question


def read(o: dict, gate: str, given: dict, edited) -> dict:
    """`oneshot.reading` with one more submission of `gate`: the round, the seconds the sheet was open,
    the folds opened and the items edited with their comments (`edited`, item → comment). The sheet's
    decisions drop a comment once it is applied, so this is where S8 finds what the owner changed."""

    seconds = given.get("seconds")
    try:   # `1e309` arrives as inf and `10**400` overflows a float: neither could be sent back as JSON
        seconds = float(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None
    except OverflowError:
        seconds = None
    opened = given.get("opened")
    entry = {"round": (o.get(gate) or {}).get("round", 1),
             "seconds": round(seconds, 1) if seconds is not None and math.isfinite(seconds) and seconds >= 0 else None,
             "opened": sorted({x for x in opened if isinstance(x, str)})[:20] if isinstance(opened, list) else [],
             "edited": sorted(edited), "edits": {i: edited[i] for i in sorted(edited)}}
    earlier = o.get("reading") or {}
    return {**earlier, gate: [*(earlier.get(gate) or []), entry]}


def summary(spec: dict) -> dict:
    o = spec["oneshot"]
    asked = o["stage"] == "spec" and (o.get("talk") or [{}])[-1].get("role") == "agent"
    return {"repo": spec["repo"], "stage": o["stage"], "working": o.get("working"), "stopped": o.get("stopped"),
            "waits": not o.get("working") and not o.get("stopped") and (o["stage"] in WAITS or asked)}


@router.get("/api/oneshot-specs")
def listing() -> dict:
    """Every run past S1, the rail's rows: its stage, and whether it waits for the owner."""

    found = []
    for run in oneshot.runs():
        if run.get("spec") and (spec := specs.load(run["spec"]["repo"], oneshot.BRANCH)) and "oneshot" in spec:
            found.append(summary(spec))
    return {"specs": found}


def view(spec: dict) -> dict:
    o = spec["oneshot"]
    out = {"repo": spec["repo"], "oneshot": o, "requirements": spec.get("requirements") or [], **summary(spec)}
    if o.get("design"):
        out["sheet"] = sheet.sheet(spec)
        out["items"] = sheet.decidable(o["design"], spec.get("requirements") or [])
    for more in VIEWS:
        out.update(more(spec))
    return out


def supported(o: dict) -> None:
    """Refuses any step forward on a run of an older version already past the stage whose record changed:
    version 1 pinned pytest node ids at the tests turn, and no command; version 2 left S2 with its stack
    in the card's old fields, not in `stack`."""

    if o.get("version", 1) < 2 and o.get("tests_head"):
        raise HTTPException(409, "예전 형식으로 계약 테스트를 고정한 실행이라 이어 갈 수 없다 — 새 실행을 시작하라")
    if o.get("version", 1) < 3 and o.get("stage") != "spec":
        raise HTTPException(409, "스택을 환경 카드에서 정한 예전 실행이라 이어 갈 수 없다 — 새 실행을 시작하라")


def idle(o: dict, stage: str) -> None:
    supported(o)
    if o.get("stage") != stage or o.get("working") or o.get("stopped"):
        raise HTTPException(409, f"지금 할 수 없다 — {o.get('stage')}{' 진행 중' if o.get('working') else ''}")


@router.get("/api/oneshot/spec/{repo}")
def status(repo: str) -> dict:
    return view(spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/say")
def say(repo: str, body: Say) -> dict:
    text = body.text.strip()
    if not text or len(text) > MAX_TALK:
        raise HTTPException(400, f"답을 1–{MAX_TALK}자로 적어라")
    with oneshot._lock:
        o = spec_of(repo)["oneshot"]
        idle(o, "spec")
        start(repo, "talk", talk=[*(o.get("talk") or []), {"role": "owner", "text": text, "ts": time.time()}])
    return view(spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/r1")
def decide(repo: str, body: Decisions) -> dict:
    """Every item on the sheet, folded or not, decided at once: approve, or edit with a comment."""

    with oneshot._lock:
        spec = spec_of(repo)
        o = spec["oneshot"]
        idle(o, "r1")
        items = sheet.decidable(o["design"], spec.get("requirements") or [])
        decisions, comments = decided(items, o["r1"]["decisions"], body.decisions)
        if comments:
            reach = sorted(sheet.dependents(o["design"], spec.get("requirements") or [], set(comments)))
            start(repo, "redesign", comments, reach, r1={**o["r1"], "decisions": decisions},
                  reading=read(o, "r1", body.reading, comments))
            return view(spec_of(repo))
        saved(repo, oneshot={"r1": {**o["r1"], "decisions": decisions}, "reading": read(o, "r1", body.reading, ())})
        approved(repo)
    from . import oneshot_build   # it imports this module
    if spec_of(repo)["oneshot"]["stage"] == "tests":   # outside the lock: the turn takes the worktree's hold
        oneshot_build.begin(repo)
    return view(spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/resume")
def resume(repo: str) -> dict:
    with oneshot._lock:
        o = spec_of(repo)["oneshot"]
        supported(o)
        if not o.get("stopped") or o.get("working"):
            raise HTTPException(409, "멈춘 단계가 아니다")
        if o.get("inflight"):   # its `Turn.finish` has not run: the next turn would lose that turn's charge
            raise HTTPException(409, "앞 턴의 비용을 아직 매기는 중이다 — 잠시 뒤 다시 하라")
        if o["stage"] not in (*RUNNING, *TURNS, *SHEETS, "review"):
            raise HTTPException(409, f"`{o['stage']}` 단계는 다음 단계에서 이어진다")
        if o["stage"] in SHEETS:   # an edit turn that stopped: the sheet stands, the owner decides again
            return view(saved(repo, oneshot={"stopped": None}))
        if o["stage"] in RUNNING:
            start(repo, RUNNING[o["stage"]])
            return view(spec_of(repo))
        if o["stage"] == "review":   # a worker, started under the lock like the stages above
            from . import oneshot_review   # it imports this module
            oneshot_review.resume(repo)
            return view(spec_of(repo))
        saved(repo, oneshot={"working": o["stage"], "stopped": None})   # a second resume meanwhile is refused
    from . import oneshot_build   # it imports this module
    oneshot_build.resume(repo)   # outside the lock, as after the approval
    return view(spec_of(repo))
