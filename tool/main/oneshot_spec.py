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
import stat
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import is_junction

from . import oneshot, oneshot_brief as brief, oneshot_sheet as sheet, specs, work
from .oneshot_profile import STAGE_ORDER, current, environment, held, kind_of, kinds_of, off_host, stacked, tooled, unsettled

RUNNING = {"spec": "talk", "design": "design", "r1": "redesign"}   # a later stage's module adds its own
SHEETS = {"r1"}   # a stage whose sheet stands when its edit turn stopped
TURNS = {"tests", "build"}   # a stage run as a work turn (`oneshot_build.STAGES`), resumed by sending it again
VIEWS: list = []   # a later stage's fields for the screen, `fn(spec) -> dict`
RECOVERS: list = []   # a later stage's start-up repair of a run an earlier version left, `fn(repo, o)`
MAX_TALK = 4_000

router = APIRouter()


def store(repo: str) -> oneshot.Store:
    def get() -> dict:
        return spec_of(repo)["oneshot"]

    def put(**fields) -> dict:
        return saved(repo, oneshot=fields)["oneshot"]

    return oneshot.Store(f"spec:{repo}", get, put, "stage", oneshot.folder(f"spec-{repo}"), lambda o: True)


def spec_of(repo: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", repo):   # it came from the screen: a name, never a path
        raise HTTPException(404, "그런 저장소가 없다")
    spec = specs.load(repo, oneshot.BRANCH)
    if spec is None or "oneshot" not in spec:
        raise HTTPException(404, "원샷 명세가 없다")
    return spec


def saved(repo: str, **fields) -> dict:
    """Top-level spec fields and `oneshot` fields (`oneshot={...}` merges) written in one save. Going back from the
    critique or past it, or dropping the frozen PC build, retires the look: no critique reads its shots again, so
    their secrets leave memory (`oneshot_front_build.MASKS`)."""

    with oneshot._lock:
        spec = spec_of(repo)
        before, changes = spec["oneshot"].get("stage"), fields.pop("oneshot", {})
        merged = {**spec["oneshot"], **changes}
        spec.update(fields, oneshot=merged)
        specs.save(spec)
    at = [STAGE_ORDER.index(s) if s in STAGE_ORDER else -1 for s in (before, merged.get("stage"))]
    if 0 <= at[1] < STAGE_ORDER.index("critique") <= at[0] or changes.get("desktop_build", ()) is None:
        from . import oneshot_front_build   # it imports this module
        oneshot_front_build.MASKS.pop(repo, None)
    return spec


# -- S2 the spec conversation --------------------------------------------------------

def ended(reqs: list[dict], stack: list[dict] | None = None, kind: str = "webapp") -> str:
    """Empty when the conversation may end, or what is still open. The server decides, never the agent.
    `stack` is S2's, with the run's `kind`'s parts; an R1 edit (`None`) cannot change it."""

    if not [r for r in reqs if r["source"]["kind"] == "original"]:
        return "주인의 말에서 온 요구(`original`)가 하나도 없다"
    if open_ := [r["id"] for r in reqs if r["status"] == "undecided"]:
        return f"아직 정하지 않은 요구가 있다: {', '.join(open_)}"
    covered = {d for s in sheet.scenarios(reqs) for d in [s["id"], *s.get("depends_on", [])]}
    if bare := [r["id"] for r in reqs if r["source"]["kind"] == "original" and r["id"] not in covered]:
        return f"시나리오가 없는 원래 요구가 있다: {', '.join(bare)}"
    if stack is not None and (open_ := unsettled(stack, kind)):
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
    if where != "S2":   # which kinds a requirement applies to is the owner's, settled in S2: an edit keeps it
        scopes = {r["id"]: r.get("kinds") for r in previous}
        reqs = [{**{k: v for k, v in r.items() if k != "kinds"},
                 **({"kinds": scopes[r["id"]]} if scopes[r["id"]] else {})} if r["id"] in scopes else r for r in reqs]
    if found := sheet.cycle({r["id"]: r for r in reqs}):   # refused here, where the requirements can still change
        raise ValueError(f"요구의 `depends_on` 이 돌고 돈다 — {' → '.join(found)}")
    picked = kinds_of(o)   # an S2 turn and an R1 edit alike: a requirement names only the owner's kinds
    if beyond := [r["id"] for r in reqs if "kinds" in r and not (r["kinds"] and set(r["kinds"]) <= set(picked))]:
        raise ValueError(f"요구의 `kinds` 는 주인이 고른 종류({', '.join(picked)}) 가운데 하나 이상이다: {', '.join(beyond)}")
    words = owner_words(o)
    if renamed := [g["term"] for g in o.get("glossary") or []
                   if g["term"] in words and g["term"] not in {x["term"] for x in glossary}]:
        raise ValueError(f"주인이 쓴 용어를 바꾸거나 지웠다: {', '.join(renamed)}")
    return reqs, glossary


def same(a, b) -> bool:
    """Equal values, types kept: `True` is not `1`, nor `"1"` (Python's `==` takes the first pair as one)."""

    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def unanswered(o: dict, stack: list[dict]) -> list[str]:
    """The parts `stack` marks `confirmed` that no owner's answer stands behind: confirmed already with the same
    choice, or offered in the stack before the question the owner has just answered, and nothing else. A PC app's
    build part, whose `tools` run on this PC, is confirmed only as the owner confirmed it with its own button
    (`confirm_build`): that exact choice and tool list, setups included, and no answer to any question.
    ponytail: that the answered question was about the stack is the prompt's to keep; the owner then sees the
    stack again on R1."""

    talk, before = o.get("talk") or [], {s["part"]: s for s in o.get("stack") or []}
    answered = len(talk) >= 2 and talk[-1]["role"] == "owner" and bool(talk[-2].get("choices"))
    build = o.get("confirmed_build")
    return [s["part"] for s in stack if s["status"] == "confirmed" and (
        not same(build, {"choice": s["choice"], "tools": s.get("tools")}) if tooled(s["part"], kinds_of(o)) else
        not (before.get(s["part"], {}).get("status") == "confirmed" and before[s["part"]]["choice"] == s["choice"])
        and not (answered and s["part"] in before))]


def spec_turn(final: str, spec: dict):
    found = {m.group(1): m.group(2) for m in oneshot.BLOCK.finditer(final)}
    if "oneshot-spec" not in found:
        raise ValueError("`oneshot-spec` 블록이 하나 있어야 한다")
    value = json.loads(found["oneshot-spec"])
    reqs, glossary = kept(spec, value, "S2")   # refuses anything but an object first
    stack = stacked(value.get("stack") or [], kinds_of(spec["oneshot"]),
                    (current(spec["oneshot"].get("profile")) or {}).get("ci") == "github-actions")
    if early := unanswered(spec["oneshot"], stack):
        raise ValueError("주인이 답하기 전에 `confirmed` 로 둔 스택 부분이 있다 — 묶음을 묻고, 주인의 답을 받은 다음 턴에 "
                         f"확정한다: {', '.join(early)}")
    choices = None
    if "choices" in found:
        choices = json.loads(found["choices"])
        specs._shape("choices", choices)
        if len(choices.get("questions", [choices])) > 3:
            raise ValueError("한 번에 묻는 `choices` 는 세 개까지다")
        for q in choices.get("questions", [choices]):   # the answer is the picked labels, several joined with ", "
            labels = [o["label"].strip() for o in q["options"]]
            if len(set(labels)) != len(labels) or q.get("multi") and any("," in x for x in labels):
                raise ValueError("한 질문의 선택지는 `label` 이 서로 달라야 하고, 여럿을 고르는 질문의 `label` 에는 "
                                 "쉼표가 없어야 한다 — 답으로 고른 `label` 이 돌아온다")
    if (mismatch := value.get("kinds_mismatch")) is not None:
        if (not isinstance(mismatch, dict) or mismatch.get("requirement") not in {r["id"] for r in reqs}
                or not isinstance(mismatch.get("reason"), str) or not mismatch["reason"].strip()):
            raise ValueError("`kinds_mismatch` 는 `requirement`(요구 id)와 `reason` 을 가진 객체다")
        if choices:
            raise ValueError("`kinds_mismatch` 를 내는 턴은 아무것도 묻지 않는다")
        mismatch = {"requirement": mismatch["requirement"], "reason": mismatch["reason"].strip()}
    said = oneshot.BLOCK.sub("", final).strip()
    return reqs, glossary, stack, choices, said, mismatch


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
    kinds = kinds_of(o)
    system = oneshot.prompt("spec-kinds") if len(kinds) > 1 else oneshot.prompt("spec", kinds[0])
    found = oneshot.turned(store(repo), "spec", system, text, "oneshot-spec,choices", lambda final, _: spec_turn(final, spec))
    if found is None:
        return
    reqs, glossary, stack, choices, said, mismatch = found
    # A question asked in this turn waits for its answer, whatever else is settled.
    why = ended(reqs, stack, kinds) or ("주인의 답을 기다리는 질문이 있다" if choices else "")
    turns = [*(o.get("talk") or []), {"role": "agent", "text": said, "choices": choices, "ts": time.time()}]
    # The stack now lives in this spec, in version 3's shape, whatever version the run started at.
    fields = {"glossary": glossary, "stack": stack, "talk": turns, "version": max(o.get("version", 1), 3)}
    # A PC app's pressed build holds only while the stack still offers that build: replaced or dropped, it is gone.
    build = next(({"choice": s["choice"], "tools": s.get("tools")} for s in stack if tooled(s["part"], kinds)), None)
    fields["confirmed_build"] = o.get("confirmed_build") if same(o.get("confirmed_build"), build) else None
    if mismatch:   # the owner kept a requirement the run's kind cannot meet: every later stage builds that kind
        # One save: a server that dies after it never leaves the conversation open again.
        detail = f"`{mismatch['requirement']}` 는 이 실행의 종류로 만들 수 없다 — {mismatch['reason']}. 새 실행을 시작해 종류를 다시 골라라"
        with oneshot._lock:
            saved(repo, requirements=reqs, oneshot={**fields, "open": "", "working": None, "mismatch": mismatch,
                                                    "stopped": {"reason": "rekind", "detail": detail, "state": o["stage"]}})
        return
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
    kind = kinds_of(spec["oneshot"])
    design = sheet.parsed(found, kind)
    if partial is not None:   # an approval outside the reach survives, so nothing outside it may move
        old, new = sheet.every(spec["oneshot"]["design"], spec.get("requirements") or []), sheet.every(design, reqs)
        if moved := sorted(i for i in old if i not in partial and old[i] != new.get(i)):
            raise ValueError(f"고치라고 한 범위 밖 항목을 바꾸거나 지웠다: {', '.join(moved)} — 그대로 두어라")
    sheet.checked(design, reqs, glossary, spec["oneshot"]["profile"], kind)
    warnings = sheet.soft(design, reqs, glossary)
    if warnings and attempt == 0:
        raise ValueError("다음을 고쳐 다시 내라 — " + " / ".join(warnings[:12]))
    return design, reqs, glossary, warnings


def design_prompt(kinds: list[str]) -> str:
    """S3's instructions: one kind's, or the core several kinds share."""

    return oneshot.prompt("design-kinds") if len(kinds) > 1 else oneshot.prompt("design", kinds[0])


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
    kind = kinds_of(spec["oneshot"])
    found = oneshot.turned(store(repo), "design", design_prompt(kind), design_text(spec), sheet.blocks(kind),
                           lambda final, attempt: design_turn(final, attempt, spec), Path(spec["worktree"]))
    if found is None:
        return
    made, _, _, warnings = found
    saved(repo, oneshot={"design": made, "design_author": store(repo).get()["answering"]["design"],
                         "stage": "r1", "working": "brief",
                         "r1": {"round": 1, "decisions": {}, "marks": {}, "warnings": warnings}})
    briefed(repo)


def briefed(repo: str, gate: str = "r1") -> None:
    """A review's brief for what was just saved (`oneshot_brief`); it clears `working` whatever happens."""

    brief.write(store(repo), lambda: spec_of(repo), lambda **f: saved(repo, oneshot=f), gate)


def briefing(repo: str, gate: str, fields: dict):
    """A review opened from a stage that holds the worktree (`oneshot_review.holding`): saved under
    `working: brief`, and the brief written by what this returns, once the tree is let go: it reads none."""

    o = {**spec_of(repo)["oneshot"], **fields}
    if held(o):   # this version ends before the review it would open: the run waits there, its rounds kept
        saved(repo, oneshot={**{k: v for k, v in fields.items() if k != gate}, "working": None,
                             "stopped": kind_stop(o["stage"], o)})   # the review's opening is never written
        return None
    saved(repo, oneshot={**fields, "working": "brief"})
    return lambda: briefed(repo, gate)


# -- R1 the sheet and its decisions ------------------------------------------------------

def redesign(repo: str, comments: dict[str, str], reach: list[str]) -> None:
    """The owner's edits and everything built on them, regenerated in one turn and checked whole."""

    spec = spec_of(repo)
    text = (design_text(spec) + f"\nThe current design:\n\n{json.dumps(spec['oneshot']['design'], ensure_ascii=False)}"
            f"\n\nThe owner edited these items, with these comments:\n\n{json.dumps(comments, ensure_ascii=False)}"
            f"\n\nReplace every item in this set and keep the rest as it is: {', '.join(reach)}. Return every "
            "design block in full, and an `oneshot-spec` block in full when a requirement changes.\n")
    kind = kinds_of(spec["oneshot"])
    found = oneshot.turned(store(repo), "design", design_prompt(kind), text, sheet.blocks(kind) + ",oneshot-spec",
                           lambda final, attempt: design_turn(final, attempt, spec, set(reach)), Path(spec["worktree"]))
    if found is None:
        return
    made, reqs, glossary, warnings = found
    old = sheet.every(spec["oneshot"]["design"], spec.get("requirements") or [])
    new = sheet.every(made, reqs)
    r1 = spec["oneshot"]["r1"]
    marks, decisions = marked(sheet.decidable(made, reqs), comments, reach, old, new, r1["decisions"])
    saved(repo, requirements=reqs, oneshot={
        "design": made, "design_author": store(repo).get()["answering"]["design"], "glossary": glossary, "working": "brief",
        "r1": {"round": r1["round"] + 1, "decisions": decisions, "marks": marks, "warnings": warnings}})
    briefed(repo)


def approved(repo: str) -> None:
    """Every item approved: the design files written into the worktree and committed, and the build may begin."""

    spec = spec_of(repo)
    tree, o = own_tree(spec), spec["oneshot"]
    docs = tree and (tree / "docs" / "design").resolve()
    if not tree or not docs.is_relative_to(tree):
        store(repo).stop("folder", f"작업트리가 `{oneshot.BRANCH}` 브랜치의 그 작업트리가 아니다: {spec['worktree']}")
        return
    # A core (an Android app's, or the one several kinds share): the sheet and the module contracts the tests are
    # written from, and a web app's endpoints among several kinds as its API.
    cored = "modules" in o["design"]
    written = ({"core.md": sheet.sheet, "core.json": sheet.core, **({"api.yaml": sheet.openapi} if "api" in o["design"]
                                                                   else {})} if cored else
               {"backend.md": sheet.sheet, "api.yaml": sheet.openapi})
    files = [f"docs/design/{name}" for name in written]
    if linked := [f for f in files if (tree / f).is_symlink() or not (tree / f).resolve().is_relative_to(tree)]:
        store(repo).stop("folder", f"설계 문서 자리가 작업트리 밖을 가리킨다: {', '.join(linked)}")
        return
    docs.mkdir(parents=True, exist_ok=True)
    for name, render in written.items():
        (docs / name).write_text(render(spec), encoding="utf-8", newline="\n")
    head = commit(repo, tree, files, f"Record the approved {'core' if cored else 'backend'} design "
                                     f"(R1 round {o['r1']['round']})")
    if head:
        # What the build reads first (`handoff`): the design just committed governs, and the spec model that
        # wrote it answers the builder's escalations.
        saved(repo, task={"kind": "feature", "what": spec["goal"]},
              precedence=[{"ref": f, "role": "current", "why": "the approved design"} for f in files], escalate=[],
              author=o.get("design_author") or oneshot.role_config("design", store(repo)),
              oneshot={"stage": "tests", "design_head": head, "working": "tests"})   # the turn comes next


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
    """Starts a stage's worker. A run held at its kind's stop (`held`), or moving into it with `fields`, starts
    nothing past S2 whatever its saved stop says: the stop is put back and the start refused. A held run moved
    back to the frontend build or later is refused too: its frozen build and looks are what it waits with."""

    o = specs.load(repo, oneshot.BRANCH)["oneshot"]
    stage = fields.get("stage") or o.get("stage")
    back = held(o) and stage in STAGE_ORDER and STAGE_ORDER.index(stage) >= STAGE_ORDER.index("build-front")
    if work != "talk" and (back or held({**o, "stage": stage})):
        stop = waiting(repo, o).get("stopped") or {}
        raise HTTPException(409, stop.get("detail") or "이 실행의 다음 단계는 다음 버전에서 이어진다")
    oneshot.start_worker(store(repo), work, WORKERS[work], (repo, *args), fields)


def recover() -> None:
    """A stage running when the server stopped, and a first S2 turn the bootstrap's hand-over never
    started, are both left stopped with `restart`, so `[재개]` takes them on. So is a stage an earlier
    version stopped at for its kind and this version runs."""

    for folder in specs.SPECS.glob("*"):
        spec = specs.load(folder.name, oneshot.BRANCH) if folder.is_dir() else None
        o = (spec or {}).get("oneshot") or {}
        if o and spec.get("adapter"):   # a restart can cut a turn before its end clears the adapter grant
            saved(folder.name, adapter=False)
        for repair in RECOVERS if o else ():
            repair(folder.name, o)
            o = specs.load(folder.name, oneshot.BRANCH)["oneshot"]
        if o and (repair := brief.recovered(o)):   # a brief the server went down writing, or its orphaned turn
            o = saved(folder.name, oneshot=repair)["oneshot"]
        o = waiting(folder.name, o) if o else o
        stop = o.get("stopped") or {}
        if stop.get("reason") == "kind" and not held(o):
            # The owner's board needs no worker: released, it is theirs to submit again (a PC app's, from 9e2c).
            saved(folder.name, oneshot={"stopped": None if o.get("stage") == "board" else {
                **stop, "reason": "restart", "detail": "이 버전은 이 단계를 이어 간다 — [재개]를 누르면 시작한다"}})
        if o.get("working") or (o and o.get("stage") == "spec" and not o.get("talk") and not o.get("stopped")
                                and handed(o)):
            oneshot.restarted(store(folder.name))
            # A held run cut mid-turn (an older version's `working` at its kind's stop): its stop is the kind's,
            # never a `restart` the owner's `[재개]` would only be refused on.
            waiting(folder.name, specs.load(folder.name, oneshot.BRANCH)["oneshot"])
        o = specs.load(folder.name, oneshot.BRANCH)["oneshot"] if o else o
        if o and o.get("stage") in TURNS and (o.get("stopped") or {}).get("reason") == "restart" and not held(o):
            from . import oneshot_build   # it imports this module
            oneshot_build.resolve_tools(folder.name, o["stage"])   # a PC app's tool gone meanwhile: `toolchain`


def waiting(repo: str, o: dict) -> dict:
    """A run at the stage this version waits at keeps its `kind` stop: a stop or a shutdown that landed between
    that stage's save and its hand-off replaced it, and must not make the stage resumable. A PC app's frozen build
    whose copy is gone, or whose HEAD the worktree has left, holds nothing: it is dropped, a run past the frontend
    build goes back to it, and `[재개]` builds again. An earlier version's run held at `build-front` with its build
    frozen and its stop lost is left resumable too: this version looks at that build in the same check."""

    front = STAGE_ORDER.index("build-front")
    if (build := o.get("desktop_build")) and not o.get("working") and not standing(repo, build):
        stage = "build-front" if o.get("stage") in STAGE_ORDER and STAGE_ORDER.index(o["stage"]) > front \
            else o.get("stage")
        o = saved(repo, oneshot={"desktop_build": None, "stage": stage, "stopped": {
            "reason": "restart", "state": stage,
            "detail": "얼린 PC 앱 빌드가 없거나 작업트리의 HEAD 가 바뀌었다 — [재개]를 누르면 다시 빌드한다"}})["oneshot"]
    elif build and o.get("stage") == "build-front" and not o.get("working") and not o.get("stopped"):
        o = saved(repo, oneshot={"stopped": {"reason": "restart", "state": "build-front",
                                             "detail": "얼린 PC 앱 빌드에서 멈췄다 — [재개]를 누르면 다시 빌드하고 검사한다"}})[
            "oneshot"]
    if held(o) and not o.get("working") and (o.get("stopped") or {}).get("reason") != "kind":
        return saved(repo, oneshot={"stopped": kind_stop(o["stage"], o)})["oneshot"]
    return o


def standing(repo: str, build: dict) -> bool:
    """A frozen build still stands unless it is shown not to: its copy, program or manifest is gone, the copy is no
    longer what its manifest and saved identity record (`oneshot_desktop.altered`), or the worktree is read at
    another HEAD than the one its gate passed. A file or HEAD that cannot be read keeps it, never a startup."""

    try:   # a record missing what a frozen build always carries holds nothing to start
        app, head = Path(build["folder"]), str(build["head"])
        needed = (app / build["launch"]["program"], app.parent / "manifest.json")
    except (KeyError, TypeError):
        return False
    unread = False
    for path in needed:   # each read, so one unreadable file never hides another shown missing
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                return False
        except (FileNotFoundError, NotADirectoryError, ValueError):   # gone, or a path no file can have
            return False
        except OSError:   # there, but unreadable now: not shown gone
            unread = True
    try:   # read whatever else is unreadable: a moved HEAD shows the build stale on its own
        done = specs.sh(["git", "rev-parse", "HEAD"], Path(spec_of(repo)["worktree"]))
        if done.returncode == 0 and done.stdout.strip() != head:
            return False
    except Exception:   # a timeout, a missing Git or worktree: unread, so not shown changed
        pass
    if unread:
        return True
    from . import oneshot_desktop   # the copy against its manifest and the saved identity
    try:
        return not oneshot_desktop.altered(build)
    except ValueError:   # a manifest that is no JSON: damaged
        return False
    except OSError:   # unreadable now: not shown changed
        return True


def kind_stop(stage: str, o: dict | None = None) -> dict:
    """The `kind` stop at `stage`, where this version's run ends."""

    return {"reason": "kind", "state": stage, "detail": {"r3": "화면 비평과 기기 회차를 마쳤다 — R3 는 다음 버전에서 이어진다"}.get(
        stage, "이 버전은 여기까지 이어 간다 — 다음 단계는 다음 버전에서 이어진다")}


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
    source: str = ""   # the gate the owner saw (`oneshot_brief.shown`); any other is refused with 409
    lines: dict[str, dict] = {}   # the brief: one decision per confirmation line, by key
    rest: bool = False   # the brief: approve every item no line or sheet decision covers


def on_brief(spec: dict, gate: str, items: list[str], body) -> tuple[dict, bool]:
    """A review's submission as item decisions for `decided`, and whether it was made on the brief. It must
    name the gate the owner saw (`source`, 409 otherwise). On the brief (`lines` or `rest`), every line is
    checked first and then reduced (`oneshot_brief.reduced`); an edit saved before an edit turn that failed
    is still pending, and `rest` never approves it away."""

    gate_now = brief.shown(spec, gate)
    if body.source != gate_now["source"]:
        raise HTTPException(409, "시트가 그 사이 바뀌었다 — 다시 불러와 새 시트로 정하라")
    if not (body.lines or body.rest):
        return body.decisions, False
    if "lines" not in gate_now:
        raise HTTPException(409, "이 시트에는 요약이 없다 — 항목마다 정하라")
    pending = {i for i, d in spec["oneshot"][gate]["decisions"].items() if (d or {}).get("edit")}
    try:
        return brief.reduced(gate_now["lines"], body.lines, body.decisions, body.rest,
                             [i for i in items if i not in pending]), True
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


WAITS = {"r1", "board", "r2", "r3"}   # stages that wait for the owner; `spec` waits on the agent's question


def read(o: dict, gate: str, given: dict, edited, briefed: bool = False) -> dict:
    """`oneshot.reading` with one more submission of `gate`: the round, the seconds the sheet was open,
    the folds opened, the items edited with their comments (`edited`, item → comment) and whether it was
    decided on the brief. The sheet's decisions drop a comment once it is applied, so this is where S8
    finds what the owner changed."""

    seconds = given.get("seconds")
    try:   # `1e309` arrives as inf and `10**400` overflows a float: neither could be sent back as JSON
        seconds = float(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None
    except OverflowError:
        seconds = None
    opened = given.get("opened")
    entry = {"round": (o.get(gate) or {}).get("round", 1),
             "seconds": round(seconds, 1) if seconds is not None and math.isfinite(seconds) and seconds >= 0 else None,
             "opened": sorted({x for x in opened if isinstance(x, str)})[:20] if isinstance(opened, list) else [],
             "edited": sorted(edited), "edits": {i: edited[i] for i in sorted(edited)}, "brief": briefed}
    earlier = o.get("reading") or {}
    return {**earlier, gate: [*(earlier.get(gate) or []), entry]}


def summary(spec: dict) -> dict:
    o = spec["oneshot"]
    asked = o["stage"] == "spec" and (o.get("talk") or [{}])[-1].get("role") == "agent"
    return {"repo": spec["repo"], "stage": o["stage"], "working": o.get("working"), "stopped": o.get("stopped"),
            "kind": kind_of(o), "kinds": kinds_of(o), "waits": not o.get("working") and not o.get("stopped") and (o["stage"] in WAITS or asked)}


@router.get("/api/oneshot-specs")
def listing() -> dict:
    """Every run past S1, the rail's rows: its stage, and whether it waits for the owner."""

    found = []
    for run in oneshot.runs():
        if run.get("spec") and (spec := specs.load(run["spec"]["repo"], oneshot.BRANCH)) and "oneshot" in spec:
            found.append(summary(spec))
    return {"specs": found}


def recorded(path: str) -> bool:
    """A worktree a run's spec records: readable whatever project maintenance has selected, so the oneshot
    window reads another project's run after a restart (`work.known`)."""

    return any(run.get("spec") and (spec := specs.load(run["spec"]["repo"], oneshot.BRANCH))
               and spec.get("worktree") == path for run in oneshot.runs())


work.READABLE.append(recorded)


def view(spec: dict) -> dict:
    o = spec["oneshot"]
    # `worktree`: where the run's work turns go, so the screen follows them live as maintenance does.
    out = {"repo": spec["repo"], "oneshot": o, "requirements": spec.get("requirements") or [],
           "worktree": spec.get("worktree") or "", **summary(spec)}
    if o.get("design"):
        out["sheet"] = sheet.sheet(spec)
        out["items"] = sheet.decidable(o["design"], spec.get("requirements") or [])
        out["brief"] = brief.shown(spec)
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
    if why := off_host(o):   # a saved PC app run, opened on another host: no turn of it runs here
        raise HTTPException(409, why)


def idle(o: dict, stage: str) -> None:
    supported(o)
    if held(o):   # at or past its kind's stop, with the stop saved or lost: nothing past S2 runs in this version
        raise HTTPException(409, "이 실행의 다음 단계는 다음 버전에서 이어진다")
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


class Build(BaseModel):
    part: str
    choice: str
    tools: list


@router.post("/api/oneshot/spec/{repo}/build")
def confirm_build(repo: str, body: Build) -> dict:
    """The owner confirms a PC app's build part as the stack offers it: that choice and its tools, each setup
    included. Only this confirms it (`unanswered`); the next S2 turn then writes it `confirmed`."""

    with oneshot._lock:
        o = spec_of(repo)["oneshot"]
        idle(o, "spec")
        talk = o.get("talk") or []
        if talk and talk[-1]["role"] == "agent" and talk[-1].get("choices"):   # never taken as that question's answer
            raise HTTPException(409, "아직 답하지 않은 질문이 있다 — 그 질문에 먼저 답하라")
        offered = next((s for s in o.get("stack") or [] if s["part"] == body.part and tooled(s["part"], kinds_of(o))),
                       None)
        if not offered or offered["status"] == "confirmed" or not same(
                {"choice": offered["choice"], "tools": offered.get("tools")}, {"choice": body.choice, "tools": body.tools}):
            raise HTTPException(409, "확정하려는 빌드와 도구가 지금 제시된 것과 다르다 — 화면을 새로 고쳐라")
        names = ", ".join(t["name"] for t in offered["tools"])   # the saved offer, never the request's copy
        start(repo, "talk", confirmed_build={"choice": offered["choice"], "tools": offered["tools"]}, talk=[
            *talk, {"role": "owner", "text": f"빌드를 이대로 확정한다: {offered['choice']} — 도구 {names}",
                    "ts": time.time()}])
    return view(spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/r1")
def decide(repo: str, body: Decisions) -> dict:
    """Every item on the sheet, folded or not, decided at once: approve, or edit with a comment. On the brief,
    each confirmation line is decided and `rest` approves the others (`oneshot_brief.reduced`). Either way the
    submission names the gate it was made on, its `source`."""

    with oneshot._lock:
        spec = spec_of(repo)
        o = spec["oneshot"]
        idle(o, "r1")
        items = sheet.decidable(o["design"], spec.get("requirements") or [])
        given, briefed = on_brief(spec, "r1", items, body)
        decisions, comments = decided(items, o["r1"]["decisions"], given)
        if comments:
            reach = sorted(sheet.dependents(o["design"], spec.get("requirements") or [], set(comments)))
            start(repo, "redesign", comments, reach, r1={**o["r1"], "decisions": decisions},
                  reading=read(o, "r1", body.reading, comments, briefed))
            return view(spec_of(repo))
        from . import oneshot_calls, oneshot_needs   # they import this module
        if why := oneshot_needs.unready(spec) or oneshot_calls.held(o["design"]):   # plan 11: no stand-in, no shared name
            raise HTTPException(409, why)
        saved(repo, oneshot={"r1": {**o["r1"], "decisions": decisions},
                             "reading": read(o, "r1", body.reading, (), briefed)})
        approved(repo)
    from . import oneshot_build   # it imports this module
    after = spec_of(repo)["oneshot"]
    if after["stage"] == "tests" and not after.get("stopped"):   # outside the lock: the turn takes the worktree's hold
        oneshot_build.begin(repo)
    return view(spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/resume")
def resume(repo: str) -> dict:
    with oneshot._lock:
        o = spec_of(repo)["oneshot"]
        supported(o)
        if not o.get("stopped") or o.get("working"):
            raise HTTPException(409, "멈춘 단계가 아니다")
        o = waiting(repo, o)
        # The kind's next stage is not in this version, or the owner kept a requirement the kind cannot meet.
        if o["stopped"]["reason"] in ("kind", "rekind"):
            raise HTTPException(409, o["stopped"]["detail"])
        thread = oneshot._workers.get(store(repo).key)
        if (settle := brief.orphaned(o)) and not (thread and thread.is_alive()):   # no turn left to charge it
            o = saved(repo, oneshot=settle)["oneshot"]
        if o.get("inflight"):   # its `Turn.finish` has not run: the next turn would lose that turn's charge
            raise HTTPException(409, "앞 턴의 비용을 아직 매기는 중이다 — 잠시 뒤 다시 하라")
        if o["stage"] not in (*RUNNING, *TURNS, *SHEETS, "review", "board"):
            raise HTTPException(409, f"`{o['stage']}` 단계는 다음 단계에서 이어진다")
        # An edit turn that stopped: the sheet stands, the owner decides again. A board's stop likewise.
        if o["stage"] in SHEETS or o["stage"] == "board":
            return view(saved(repo, oneshot={"stopped": None}))
        if o["stage"] in RUNNING:
            start(repo, RUNNING[o["stage"]])
            return view(spec_of(repo))
        if o["stage"] == "review":   # a worker, started under the lock like the stages above
            from . import oneshot_review   # it imports this module
            if not oneshot_review.capped(o):
                oneshot_review.resume(repo)
                return view(spec_of(repo))
            # The cap stopped it on a refused HEAD: the findings go to the build, the refusals counted afresh.
            o = saved(repo, oneshot={"stage": "build", "refused": 0})["oneshot"]
        saved(repo, oneshot={"working": o["stage"], "stopped": None})   # a second resume meanwhile is refused
    from . import oneshot_build   # it imports this module
    oneshot_build.resume(repo)   # outside the lock, as after the approval
    return view(spec_of(repo))
