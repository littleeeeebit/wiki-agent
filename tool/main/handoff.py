"""handoff — what an implementer reads before anything else, and what it hands back instead of redesigning.

A spec's JSON gave a write session the goal, the done items and the grounds, all at the same weight. A
repository whose documents carry designs from different stages (`docs/design/backend.md` describing a capture
pipeline the app no longer starts, `.wiki/project.md` the in-app reader that replaced it) then left a light
implementer to pick the stronger-looking one, and it reshaped the old pipeline. Three fields settle that up
front, written by the model that wrote the spec: `task` (the single job and its kind), `precedence` (which
document, or which lines of it, govern and which are legacy or background) and `escalate` (when to stop and
hand back). `text` puts them at the very top of the write session's prompt.

When the implementer hits a stop condition it ends its answer with an `escalation` block. The server sends
the conflict to the spec's `author` model in a read-only turn, which keeps the spec with guidance, revises it,
or splits a prerequisite task off; after `ROUNDS` such answers, or with no author model on record, the person
decides. The implementer never redesigns on its own: its own `spec-update` may not move the goal or the scope
(`narrowed`).
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

KINDS = ("fix", "change", "feature", "refactor", "docs")
ROLES = {"current": "the contract for this task",
         "legacy": "an earlier design: context only; do not change what it describes unless the task says so",
         "reference": "background only, never a contract"}
# Always in force; a spec adds only what is particular to its task.
DEFAULT_ESCALATE = (
    "The code contradicts the spec or a `current` document.",
    "Meeting the task needs an architecture change the spec does not describe: a new component, a moved "
    "boundary, a changed data model, transaction boundary or public interface.",
    "The task requires changing code that only a `legacy` document describes.",
    "A done item cannot be met as the spec states it.",
)
ROUNDS = 2          # the author's answers per task before the person decides
TOOLS = "Read,Glob,Grep"   # the author reads the worktree; it never writes there
RANGE = re.compile(r"^(.+?):(\d+)[-–](\d+)$")
ANSWERS = ("spec-update", "guidance", "split")
PROMPT = (Path(__file__).resolve().parents[2] / "tool/prompts/escalation.md").read_text(encoding="utf-8")
NARROWED = ("Your `spec-update` changed the {what}. That is the spec author's decision, not the implementer's: "
            "the update was not applied. If the code or the documents make the spec impossible as written, end "
            "your answer with an `escalation` block as your instructions describe; otherwise carry on under "
            "revision {rev}.")


def lines(path: Path) -> int:
    with path.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def checked(repo: Path, block: dict, strict: bool = True, kept: list | tuple = ()) -> dict:
    """`task`, `precedence` and `escalate` of a spec block, validated against `repo`; `ValueError` names
    what is wrong. A reference names an existing file or folder inside the repository, a range lines that
    file has; not `strict`, only a reference inside the repository (one checked in another checkout). An
    entry of `kept`, the spec's standing ones, was checked when it came in: a file that changed under it
    since never blocks a revision of something else."""

    task = block.get("task")
    if not isinstance(task, dict) or task.get("kind") not in KINDS or not isinstance(task.get("what"), str) \
            or not task["what"].strip():
        raise ValueError(f"`task` 는 {{\"kind\": {'|'.join(KINDS)}, \"what\": 한 문장}} 이어야 한다")
    given = block.get("precedence")
    if not isinstance(given, list) or not given:
        raise ValueError("`precedence` 는 이 작업에 적용할 문서를 우선순위대로 담은 비지 않은 목록이어야 한다")
    # Standing means the same reference with the same authority: one promoted to `current` is checked again.
    root, ranked, standing = repo.resolve(), [], {(p.get("ref"), p.get("role")) for p in kept if isinstance(p, dict)}
    for p in given:
        if not isinstance(p, dict) or not isinstance(p.get("ref"), str) or not isinstance(p.get("role"), str) \
                or p["role"] not in ROLES:
            raise ValueError(f"`precedence` 항목마다 `ref` 와 `role`({'|'.join(ROLES)}) 이 있어야 한다")
        ref = p["ref"].strip()
        m = RANGE.match(ref)
        target = (root / (m.group(1) if m else ref)).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"`precedence` 의 `{ref}` 는 저장소 밖이다")
        fresh = strict and (ref, p["role"]) not in standing
        if fresh and not target.exists():
            raise ValueError(f"`precedence` 의 `{ref}` 가 저장소에 없다")
        if fresh and m:
            first, last = int(m.group(2)), int(m.group(3))
            if not target.is_file() or not 1 <= first <= last <= lines(target):
                raise ValueError(f"`precedence` 의 `{ref}` 줄 범위가 파일에 없다")
        ranked.append({"ref": ref, "role": p["role"], **({"why": " ".join(p["why"].split())}
                                                        if isinstance(p.get("why"), str) and p["why"].strip() else {})})
    escalate = block.get("escalate") or []
    if not isinstance(escalate, list) or not all(isinstance(e, str) for e in escalate):
        raise ValueError("`escalate` 는 글줄의 목록이어야 한다")
    return {"task": {"kind": task["kind"], "what": " ".join(task["what"].split())}, "precedence": ranked,
            "escalate": [e.strip() for e in escalate if e.strip() and e.strip() not in DEFAULT_ESCALATE]}


FIELDS = frozenset({"task", "precedence", "escalate"})


def given(block: dict) -> bool:
    return any(k in block for k in FIELDS)


def followed(spec: dict, change: dict) -> dict:
    """A rewritten goal without a task of its own carries the task with it: the old task would stay the first
    thing the implementer reads (the draft editor has no task field)."""

    goal = " ".join(str(change.get("goal") or "").split())
    if not spec.get("task") or "task" in change or not goal or goal == spec.get("goal"):
        return {}
    return {"task": {**spec["task"], "what": goal}}


def surveyed(s: dict) -> dict:
    """A refactoring survey card's fields: the survey's evidence is the card's contract. Its paths were
    checked in the survey's own checkout, which may stand on another commit (`checked`, not `strict`)."""

    return {"task": {"kind": "refactor", "what": f"{s['title']}. {s['change']}"},
            "precedence": [{"ref": p, "role": "current", "why": "survey evidence for this pipeline"}
                           for p in sorted({e["path"] for e in s["evidence"]})]}


def text(path: Path, spec: dict) -> str:
    """The section a write session reads first; empty for a spec from before these fields."""

    if not spec.get("task"):
        return ""
    out = ["## Read this first", "", f"**Task ({spec['task']['kind']}):** {spec['task']['what']}",
           "**Done when:** every `done` item of the spec below holds. Do this one task; anything else is out.",
           "", "**Which documents govern this task, in order.** When two disagree, the earlier one wins.", ""]
    for n, p in enumerate(spec.get("precedence") or [], 1):
        m = RANGE.match(p["ref"])
        target = path / (m.group(1) if m else p["ref"])
        try:
            size = "folder" if target.is_dir() else f"{lines(target)} lines"
        except OSError:
            size = "missing in this checkout"
        where = f"`{m.group(1)}` lines {m.group(2)}–{m.group(3)} ({size})" if m else f"`{p['ref']}` ({size})"
        out.append(f"{n}. {where} — **{p['role']}**: {ROLES[p['role']]}." + (f" {p['why']}" if p.get("why") else ""))
    out += ["", "A document you loaded only partway is not read: before relying on a `current` document, read "
            "all of it, or all of the lines named above. A document not listed never outranks one that is; "
            "where it disagrees with a listed one, escalate.", "",
            "**Stop and hand back, do not redesign, when:**", "",
            *[f"- {e}" for e in [*DEFAULT_ESCALATE, *spec.get("escalate", [])]], "",
            "To hand back, end the answer with an `escalation` block as the rules below describe, and leave "
            "what the conflict touches unchanged.", "", ""]
    return "\n".join(out)


def brief(spec: dict) -> dict:
    """The fields as the spec JSON a reviewer reads carries them."""
    return {k: spec[k] for k in ("task", "precedence", "escalate") if spec.get(k) is not None}


# -- The implementer's own revision --------------------------------------------------------------------

def narrowed(spec: dict, change, person: bool) -> str:
    """Why an implementer's `spec-update` is not applied, or "" when it may be. A person's own turn may move
    anything; a server-sent one may reword `done` and carry a person's scope decision into `out` (a review
    repair does), but never move the goal or these fields."""

    if person or not isinstance(change, dict):
        return ""
    moved = [k for k in ("goal",) if k in change and " ".join(str(change[k]).split()) != spec.get("goal")]
    moved += [f"`{k}`" for k in sorted(FIELDS) if k in change]
    return NARROWED.format(what=" and ".join(moved), rev=spec["rev"]) if moved else ""


def held_back(path: Path, run, spec: dict, change):
    """`specs._check`'s step for a `spec-update` `narrowed` refuses: `None` when it may be applied; else
    the turn telling the implementer to escalate instead, once, and after a second try a stop."""

    from . import loop, specs
    if not (why := narrowed(spec, change, getattr(run, "person", False))):
        return None
    if loop.LOOPING.fullmatch(spec["state"]):
        # The review loop sends this repair's retries: a second sender would race it for the worktree.
        why = f"리뷰 수정 턴이 목표·작업 지시를 바꾸려 했다 — 반영하지 않았다. 사람이 판단한다:\n{json.dumps(change, ensure_ascii=False)[:6000]}"
        loop.asked(spec["repo"], spec["id"], why)
        specs.failed(run, spec, why)
        return lambda: None
    if spec.get("narrow_asked"):
        specs.update(spec["repo"], spec["id"], narrow_asked=False)
        specs.failed(run, spec, "구현 모델이 목표·작업 지시를 다시 바꾸려 했다 — 상위 판단이 필요하다")
        return lambda: None
    specs.update(spec["repo"], spec["id"], narrow_asked=True)
    specs.note(run, "구현 모델의 명세 변경이 목표·작업 지시를 건드려 반영하지 않았다 — 상위 모델로 넘기라고 알린다")
    return lambda: None if run.halt.is_set() else specs.again(path, run.chat, why, spec)


# -- Escalation -----------------------------------------------------------------------------------------

def shaped(value) -> bool:
    return isinstance(value, dict) and all(isinstance(value.get(k), str) and value[k].strip()
                                           for k in ("conflict", "question")) \
        and isinstance(value.get("where"), list) and bool(value["where"]) \
        and all(isinstance(w, str) and w.strip() for w in value["where"])


def found(blocks: list[dict]) -> list[dict]:
    return [b for b in blocks if b["name"] == "escalation"]


def author_of(spec: dict) -> dict:
    return spec.get("author") or (spec.get("source") or {}).get("author") or {}


def question(spec: dict, escalation: dict) -> str:
    """The author's turn: the spec as the implementer has it, and what it ran into."""

    from . import specs
    return (f"The task spec, revision {spec['rev']}:\n\n{text(Path(spec['worktree']), spec)}{specs.brief(spec)}\n"
            f"The implementer stopped and handed back:\n\n```json\n{json.dumps(escalation, ensure_ascii=False, indent=2)}"
            f"\n```\n\nRead the code and documents it cites in this checkout, then answer with exactly one of "
            f"{', '.join(f'`{a}`' for a in ANSWERS)} as your instructions describe.")


def ask(path: Path, author: dict, system: str, text_: str, run) -> str:
    """One read-only turn of `author` in `path`; its final answer. Patched in tests.

    Whatever stops the work turn — its Stop, the review loop's, a worktree removal, the server — sets the
    run's halt, and the author's turn ends with it: the worktree is held until this returns."""

    from agent import ChatSession
    chat = ChatSession(path, tools=TOOLS, system=system, model=author.get("model") or "",
                       effort=author.get("effort", ""), isolated=not str(author.get("model")).startswith("codex:"))
    over = threading.Event()

    def watch():
        while not over.wait(0.1):
            if run.halt.is_set():
                chat.stop(run.halt)
                return
    threading.Thread(target=watch, daemon=True, name="author-stop").start()
    final, failed = "", ""
    try:
        for ev in chat.say(text_, run.halt):
            if ev.kind == "done":
                final = ev.text
                failed = (final or "완료된 답이 없다") if (ev.meta or {}).get("error") else ""
            elif ev.kind == "error":
                failed = ev.text
    finally:
        over.set()
        chat.close()
    if failed:
        raise RuntimeError(failed)
    return final


def escalated(repo: Path, path: Path, run, spec: dict, final: str):
    """`specs._check`'s step for an answer ending in an `escalation` block: `None` when there is none (the
    check goes on), otherwise what `_check` returns — the next turn to send, or nothing after a stop."""

    from . import specs
    blocks = found(specs.blocks(final)[1])
    if not blocks:
        return None
    value = blocks[0].get("value")
    if len(blocks) != 1 or "error" in blocks[0] or not shaped(value):
        return ended(spec, run, "`escalation` 블록은 conflict·where·question 을 담은 JSON 객체 하나여야 한다")
    author, past = author_of(spec), spec.get("escalations") or []
    entry = {"ts": time.time(), "rev": spec["rev"], "escalation": value}
    # A record with an empty model is the CLI's default the spec was written on; no record is an older card.
    if not author or len(past) >= ROUNDS:
        why = "명세를 쓴 모델이 기록돼 있지 않다" if not author else f"상위 모델 판단이 {ROUNDS}번 넘었다"
        return person(spec, run, [*past, {**entry, "answer": {"kind": "person"}}],
                      f"상위 판단 필요 — {why}. 구현 모델의 충돌: {value['conflict']} / 질문: {value['question']}")

    # Run here, still holding the worktree as the gate does: the author reads the tree the implementer left,
    # and its lines land in this turn's record.
    specs.note(run, f"충돌을 명세를 쓴 모델 `{author.get('model') or '기본 모델'}` 에게 넘긴다 ({len(past) + 1}/{ROUNDS})")
    from common import masked

    hide = getattr(getattr(run, "chat", None), "hide", None)   # a oneshot's secrets: never saved or sent on
    try:
        answer = ask(path, author, PROMPT, question(spec, value), run)
    except Exception as exc:   # an unanswered round trip is the person's to settle, never a silent retry
        if run.halt.is_set():
            return stopped(spec, run, [*past, {**entry, "answer": {"kind": "stopped"}}])
        error = masked(str(exc), hide)
        return person(spec, run, [*past, {**entry, "answer": {"kind": "failed", "error": error[:500]}}],
                      f"상위 모델 왕복이 실패했다 — {error}")
    from . import oneshot_needs

    if hide and oneshot_needs.held_in(answer, hide):   # nothing of it is used, nor named
        return person(spec, run, [*past, {**entry, "answer": {"kind": "refused"}}],
                      "상위 모델의 답에 이 세션의 비밀 값이 있어 쓰지 않는다")
    if run.halt.is_set():   # a Stop pressed while the author answered: nothing of the answer takes effect
        return stopped(spec, run, [*past, {**entry, "answer": {"kind": "stopped", "text": answer[-2000:]}}])
    return applied(repo, path, run, specs.load(spec["repo"], spec["id"]) or spec, past, entry, answer)


def safe(run, reason: str) -> str:
    """`reason` masked with the turn's secrets: every exit of the handoff keeps or shows it."""
    from common import masked

    return masked(reason, getattr(getattr(run, "chat", None), "hide", None))


def ended(spec: dict, run, reason: str):
    """The task stops with `reason`. An unfinished oneshot's stage stops with it, where `[재개]` picks it up,
    and its finalizer records no stop of its own over it."""

    from . import oneshot_spec, specs

    reason = safe(run, reason)
    if (spec.get("oneshot") or {}).get("stage") not in (None, "done"):
        oneshot_spec.store(spec["repo"]).stop("block", reason)
        run.checked = True
    return specs.failed(run, spec, reason)


def stopped(spec: dict, run, history: list[dict]):
    from . import specs
    spec = specs.update(spec["repo"], spec["id"], escalations=history) or spec
    return ended(spec, run, "사람이 멈춤 — 상위 모델의 답을 적용하지 않았다")


def person(spec: dict, run, history: list[dict], reason: str):
    """The person decides: the reason on the card, and a running review loop halted on it, the same reason
    on its pause."""

    from . import loop, specs

    reason = safe(run, reason)   # before the loop's pause and the stage's stop keep it
    spec = specs.update(spec["repo"], spec["id"], escalations=history) or spec
    if loop.LOOPING.fullmatch(spec["state"]):
        loop.asked(spec["repo"], spec["id"], reason)
    return ended(spec, run, reason)


def applied(repo: Path, path: Path, run, spec: dict, past: list[dict], entry: dict, answer: str):
    """The author's answer put into effect, and the implementer's next turn."""

    from . import specs
    got = [b for b in specs.blocks(answer)[1] if b["name"] in ANSWERS]
    if len(got) != 1 or "error" in got[0]:
        return person(spec, run, [*past, {**entry, "answer": {"kind": "refused", "text": answer[-2000:]}}],
                      f"상위 모델이 {', '.join(ANSWERS)} 중 하나로 답하지 않았다")
    name, value = got[0]["name"], got[0]["value"]
    history = [*past, {**entry, "answer": {"kind": name, "value": value}}]
    oneshot = bool((spec.get("oneshot") or {}).get("stage") not in (None, "done"))
    from . import loop
    if loop.LOOPING.fullmatch(spec["state"]):
        # The review loop owns this task's turns and its spec: nothing is applied before the person decides.
        # The whole answer goes on the card and the pause: the person resumes from what was proposed.
        said = value["text"] if name == "guidance" and isinstance(value, dict) and isinstance(value.get("text"), str) \
            else json.dumps(value, ensure_ascii=False, indent=1)
        return person(spec, run, history, f"상위 모델이 `{name}` 로 답했다 — 반영하지 않고 리뷰 루프를 멈췄다. 답을 보고 "
                                          f"에이전트 칸에서 이어 가라:\n{said[:6000]}")
    try:
        if name == "guidance":
            if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not value["text"].strip():
                raise ValueError("`guidance` 는 {\"text\": ...} 여야 한다")
            specs.update(spec["repo"], spec["id"], escalations=history)
            told = (f"The spec stands at revision {spec['rev']}. Its author answered your escalation:\n\n"
                    f"{value['text'].strip()}\n\nCarry on under that answer.")
        elif oneshot:   # a oneshot's contract is its committed design: only the person changes it mid-build
            return person(spec, run, history, f"상위 모델이 원샷 설계를 바꾸자고 답했다 ({name}) — 사람이 정한다")
        elif name == "spec-update":
            # A Stop sets the halt, then waits on `run.saving`: one before this check owns the result, one after
            # it answers once the revision, validation included, is saved, and no follow-up starts.
            with specs._files, run.saving:
                current = specs.load(spec["repo"], spec["id"])
                if run.halt.is_set():
                    return stopped(spec, run, [*past, {**entry, "answer": {"kind": "stopped", "value": value}}])
                specs.revise(repo, current, value, author=True, checkout=path)   # the tree the author read
            spec = specs.update(spec["repo"], spec["id"], escalations=history)
            # A running session keeps the system prompt it started with: the revision goes in the turn.
            told = (f"The spec's author revised it to revision {spec['rev']} in answer to your escalation. It "
                    f"replaces the spec in your instructions:\n\n{text(path, spec)}{specs.brief(spec)}\n"
                    "Continue the task under this revision.")
        else:
            if not isinstance(value, dict) or not isinstance(value.get("spec"), dict):
                raise ValueError("`split` 은 {\"spec\": 선행 작업 명세, \"reason\": ...} 이어야 한다")
            gate = specs.gate_of(repo)
            if not gate:
                raise ValueError("저장소의 gate_cmd 가 없어 선행 작업 카드를 만들 수 없다")
            with specs._files, run.saving:   # as the revision, in its order: the card is made whole, or not at all
                if run.halt.is_set():
                    return stopped(spec, run, [*past, {**entry, "answer": {"kind": "stopped", "value": value}}])
                sid = specs.card(repo, value["spec"], gate, {"focus": "escalation", "turn": time.time(),
                                                             "from": spec["id"], "author": author_of(spec)})
            return person(spec, run, history, f"선행 작업 `{sid}` 을 먼저 한다 — {value.get('reason') or '상위 모델이 나눴다'}. "
                                              "그 PR 이 머지된 뒤 이 작업을 이어 간다")
    except (ValueError, OSError) as exc:
        return person(spec, run, [*history[:-1], {**entry, "answer": {"kind": "refused", "error": str(exc)}}],
                      f"상위 모델의 답을 적용하지 못했다 — {exc}")
    specs.note(run, f"상위 모델이 `{name}` 로 답했다 — 구현을 이어 간다")
    if oneshot:   # a Stop pressed while this turn is recorded leaves the stage resumable instead
        from . import oneshot_build
        run.checked = True   # handled: the stage's finalizer records no unchecked turn
        return oneshot_build.unless_stopped(run, spec["repo"],
                                            lambda: oneshot_build.send(spec["repo"], spec["oneshot"]["stage"], told))
    return lambda: None if run.halt.is_set() else specs.again(path, run.chat, told, spec)
