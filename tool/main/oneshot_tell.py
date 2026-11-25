"""The owner speaks to a run while it is built: any time from the approved design to the end, the build agent's
own turn included. `docs/plans/oneshot/14-owner-speaks-mid-build.md` is the design.

A word said while a build turn runs goes into that turn at once (`work.steer`'s path). Every word also waits in
`tell_pending` until the next thing the run would start: a turn (`oneshot_build.send`) or a stage's worker
(`oneshot.start_worker`), both asking `oneshot.diverted` first. There the run turns aside into `retell`: the model
that wrote the design rewrites what the words change, a requirement, a component, the model a part runs, and keeps
the rest; the design files are committed; and the build carries on from the code it has, through the tests of what
changed, the build, the measurement and the review. Nothing starts over, and nothing waits for a second approval:
the words are the owner's own.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import oneshot, oneshot_build, oneshot_sheet as sheet, oneshot_spec, runtime, work
from .oneshot_profile import STAGE_ORDER
from .query import _lock as work_lock, hold

router = APIRouter()
MAX_TELL = 4000
MAX_ASKED = 20000   # the questions a work-log answer carries, with every option the owner saw
# From the first build turn to the owner's last round (R3): before it the design talk and R1 take the owner's
# words, after it the pull request is published. Words said by then and still waiting turn the run back first.
OPEN = STAGE_ORDER[STAGE_ORDER.index("tests"):STAGE_ORDER.index("pr")]
# While the screens are designed with the owner (R2 and what leads to it) the words wait for the frontend build.
WAIT = STAGE_ORDER[STAGE_ORDER.index("split"):STAGE_ORDER.index("build-front")]
FRONT = STAGE_ORDER[STAGE_ORDER.index("build-front"):]
# Stages a run rests at with no worker, waiting for the owner: the measurement, the conflicts, the rounds, R3.
PARKED = ("measure", "conflict", "phone", "pc", "r3")
HARD = ("kind", "rekind", "fake")   # stops `[재개]` refuses (`oneshot_spec.resume`): the owner's words never clear them
STEERED = ("The owner says this to you now, while you work, in their own words:\n\n{text}\n\nAct on it in this "
           "turn. Where it changes a requirement or the design (a behaviour, a component, the model a part runs), "
           "build toward what they said rather than the design you were given: when this turn ends the server "
           "has the design rewritten from their words, and the contract tests follow it.")


# Every work turn: the owner can speak into it, and the decisions only they can make are asked, not guessed.
OWNER = ("\n\nThe owner watches this turn and can speak into it; what they say comes in as their own words, and it "
         "governs where it differs from the design. When a decision is the owner's to make (two requirements pull "
         "apart, or the design leaves a choice open that changes what they get), ask them with your question tool, "
         "the recommendation first with what each option costs, and carry on with their answer.\n")
AGAIN = ("The owner changed the design while it was being built, and the design files are committed again. The "
         "contract tests and the application already exist. Change the contract tests the changed items touch and add "
         "the new planned ones, keep every other test as it is, and leave the application's code alone: the build "
         "turn after you changes it. This time a planned test may pass already, since the code exists: where the "
         "instructions below say every planned test must fail now, read that as every planned test must run, "
         "unskipped. Close with the same block as before, naming every contract file.\n\n")


def told(o: dict, text: str) -> str:
    """A work turn's `text` with what the owner said during the build, which the design now follows, and the line
    that they watch and can be asked."""

    words = [w for r in o.get("retold") or [] for w in r["words"]]
    return text + ("\n\nDuring the build the owner said this, in their own words, and the design was rewritten to "
                   f"follow it:\n\n{json.dumps(words, ensure_ascii=False, indent=2)}\n" if words else "") + OWNER


def tests_told(o: dict, text: str) -> str:
    """The tests turn's: after a design changed mid-build, the tests and the code exist already."""

    return told(o, (AGAIN if o.get("tests_head") else "") + text)


def next_build(spec: dict) -> str:
    """The build turn's text for the stage the run is at: the backend's, or the frontend's after a design the
    owner changed past it."""

    if spec["oneshot"]["stage"] == "build-front":
        from . import oneshot_front_build   # it imports `oneshot_build`, which imports this module late
        return oneshot_front_build.build_front_text(spec)   # told of the owner there, as every frontend build is
    return oneshot_build.build_text(spec)


class Option(BaseModel):
    label: str
    note: str = ""


class Asked(BaseModel):
    """A question the build agent asked in its work log, as the owner saw it, with the answer they gave."""

    header: str = ""
    question: str
    options: list[Option] = []
    answer: str


class Tell(BaseModel):
    text: str
    asked: list[Asked] = []   # the words answer these: the design model never saw them


@router.post("/api/oneshot/spec/{repo}/tell")
def tell(repo: str, body: Tell) -> dict:
    text = body.text.strip()
    if not text or len(text) > MAX_TELL:
        raise HTTPException(400, f"말은 1–{MAX_TELL}자로 적는다")
    asked = [a.model_dump() for a in body.asked]
    if len(json.dumps(asked, ensure_ascii=False)) > MAX_ASKED:   # bounded on its own: the answer keeps its budget
        raise HTTPException(400, f"답한 질문은 {MAX_ASKED}자까지 싣는다")
    with oneshot._lock:   # held through the start, so two requests never both find the run idle
        spec = oneshot_spec.spec_of(repo)
        o = spec["oneshot"]
        oneshot_spec.supported(o)
        if o.get("stage") not in OPEN:
            raise HTTPException(409, "설계를 승인한 뒤부터 주인 화면 회차(R3)까지 구현 에이전트에게 말할 수 있다")
        stage = o["stage"]
        said = {"text": text, "ts": time.time(), "stage": stage, "into_turn": steered(spec, text)}
        o = oneshot_spec.saved(repo, oneshot={"told": [*(o.get("told") or []), said], "tell_pending": [
            *(o.get("tell_pending") or []), {"text": text, "stage": stage, "ts": said["ts"], **({"asked": asked} if asked else {})}]})["oneshot"]
        if o.get("working") == "measure":   # the build goes back to work at once: the measurement stops
            from . import oneshot_measure   # it imports this module's neighbours
            if halt := oneshot_measure.HALTS.get(repo):
                halt.told = True   # the measurement hands the words on rather than stopping (`oneshot_measure`)
                halt.set()
        # Only a parked run takes the words in now. Anywhere else a step is between its turns, and its next start
        # meets them (`diverted`); a run that parks with words waiting shows them, and `[지금 반영]` takes them in.
        if due(o) and not parked(spec):
            start(oneshot_spec.store(repo))
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/tell/apply")
def apply(repo: str) -> dict:
    """`[지금 반영]`: words a parked run holds, taken in now."""

    with oneshot._lock:
        spec = oneshot_spec.spec_of(repo)
        oneshot_spec.supported(spec["oneshot"])
        if not due(spec["oneshot"]):
            raise HTTPException(409, "지금 반영할 말이 없다 — 화면 설계 중에 한 말은 프런트엔드 빌드에서 반영된다")
        if why := parked(spec):
            raise HTTPException(409, why)
        start(oneshot_spec.store(repo))
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def parked(spec: dict) -> str:
    """"" when the run rests with nothing running and nothing about to: stopped with a stop the owner may resume, or
    resting at a screen that waits for the owner; otherwise why the words wait for the run's next start."""

    o = spec["oneshot"]
    if o.get("working") or o.get("inflight") or any(Path(k) == Path(spec["worktree"]) for k in list(work._busy)):
        return "구현 에이전트가 일하는 중이다 — 다음 단계가 시작될 때 반영된다"
    if (o.get("stopped") or {}).get("reason") in HARD:
        return o["stopped"]["detail"]
    return "" if o.get("stopped") or o.get("stage") in PARKED else "다음 단계가 시작될 때 반영된다"


def taken(o: dict) -> list[dict]:
    """The waiting words a rewrite takes in now. Words said while the screens are designed with the owner wait for
    the frontend build, across any backend rewrite before it; every other word is taken at once (a backend
    review's, which the screens must not be designed past, included)."""

    return [w for w in o.get("tell_pending") or [] if w["stage"] not in WAIT or o.get("stage") in FRONT]


def due(o: dict) -> bool:
    return o.get("stage") != "done" and bool(taken(o))


def start(store: oneshot.Store) -> None:
    """`retell` started under `oneshot._lock`. A worker already running (the rewrite, which a turn's handoff met)
    is left as it is; a start refused otherwise (the server going down) stops the run with its reason, so no
    `working` is left without a worker."""

    try:
        oneshot_spec.start(store.key.removeprefix("spec:"), "retell")
    except HTTPException as exc:
        if exc.status_code != 409:
            store.stop("restart" if runtime.stopping.is_set() else "block", str(exc.detail))


def handoff(store: oneshot.Store) -> bool:
    """A stage that held the worktree passes the run to the rewrite once it let the tree go: true when the rewrite
    took it, so the instruction queued for the worktree waits for the rewrite's own turn (`oneshot_review.holding`)."""

    with oneshot._lock:
        start(store)
        return store.get().get("working") == "retell"


def steered(spec: dict, text: str) -> bool:
    """`text` said into the build agent's running oneshot turn, as a person's steer is; false when none runs."""

    try:
        run = work.attached(spec["worktree"])
    except HTTPException:
        return False
    if not isinstance(run, oneshot_build.Turn) or run.repo != spec["repo"]:
        return False
    with run.wake:
        if run.done or not run.chat.steer(STEERED.format(text=text)):
            return False
        run.put({"kind": "said", "text": text, "meta": {}, "session_id": run.chat.id, "parent_id": run.chat.parent_id})
    return True


def diverted(store: oneshot.Store, what: str) -> bool:
    """Before a spec run starts `what`: with the owner's words waiting, the run rewrites its design first."""

    if what == "retell" or not store.key.startswith("spec:"):
        return False
    with oneshot._lock:   # the check and the start as one step
        o = store.get()
        if o.get("working") == "retell":   # a rewrite took the run: it sends the next step itself
            return True
        if not due(o):
            return False
        start(store)
    return True


def authored(store: oneshot.Store) -> oneshot.Store:
    """`store` as the design turn reads it: the model and effort that wrote the design, whatever the window's
    model is now (`oneshot.role_config` reads a run's pinned `models`)."""

    if not (author := store.get().get("design_author")):
        return store
    group = oneshot.GROUPS["design"]
    return oneshot.Store(store.key, lambda: {**store.get(), "models": {**(store.get().get("models") or {}), group: {
        "model": author.get("model") or "", "effort": author.get("effort") or ""}}}, store.put, store.state,
        store.folder, store.active)


def retell(repo: str) -> None:
    """The design rewritten from the owner's words by the model that wrote it, and the build carried on. The worktree
    is held through the design turn, its commit and the save, as a work turn holds it, so no other turn writes it."""

    store = oneshot_spec.store(repo)
    try:
        release = hold(work._busy, work_lock, str(Path(oneshot_spec.spec_of(repo)["worktree"])),
                       "작업트리를 다른 턴이 쓰고 있다", kind="turn")
    except HTTPException:   # another turn has it: the run stops parked, so `[지금 반영]` or `[재개]` tries again
        with oneshot._lock:
            if store.get().get("working") == "retell":
                store.stop("block", "다른 턴이 작업트리를 쓰고 있어 주인의 말을 아직 반영하지 못했다 — 그 턴이 끝나면 [지금 반영]")
        return
    try:
        sent = rewritten(repo)
    finally:
        release()
    if sent:
        oneshot_build.send(repo, *sent)


def rewritten(repo: str) -> tuple[str, str] | None:
    """`retell`'s rewrite: the stage and text of the turn that follows, or `None` when it stopped or had nothing."""

    spec = oneshot_spec.spec_of(repo)
    o, reqs = spec["oneshot"], spec.get("requirements") or []
    pending = taken(o)
    store, kind = oneshot_spec.store(repo), oneshot_spec.kinds_of(o)
    if not pending:   # nothing to take in: only this worker's own mark is let go
        with oneshot._lock:
            if store.get().get("working") == "retell":
                store.put(working=None)
        return None
    words = [w["text"] for w in pending]
    said = [{"said": w["text"], "answering": w["asked"]} if w.get("asked") else w["text"] for w in pending]
    text = (oneshot_spec.design_text(spec) + f"\nThe current design:\n\n{json.dumps(o['design'], ensure_ascii=False)}"
            "\n\nWhile the product was being built, the owner said this, in their own words. An entry with `answering` "
            "answers the build agent's questions: each with its chapter, its options as the owner saw them, and the "
            f"answer chosen.\n\n{json.dumps(said, ensure_ascii=False)}\n\nChange the design where these words change it: a requirement, "
            "a component, a choice such as the model a part runs, and every item built on what changed. Keep every "
            "other item exactly as it is. Return every design block in full, and an `oneshot-spec` block in full when "
            "a requirement changes.\n")
    every = set(sheet.every(o["design"], reqs))
    found = oneshot.turned(authored(store), "design", oneshot_spec.design_prompt(kind), text,
                           sheet.blocks(kind) + ",oneshot-spec",
                           lambda final, attempt: oneshot_spec.design_turn(final, attempt, spec, every),
                           Path(spec["worktree"]))
    if found is None:   # stopped with its reason; the words still wait, so `[재개]` takes them in again
        return None
    made, new_reqs, glossary, _ = found
    old, new = sheet.every(o["design"], reqs), sheet.every(made, new_reqs)
    moved = sorted(i for i in {*old, *new} if old.get(i) != new.get(i))
    again, changes = {"attempts": 0, "fix": None, "measure_fails": 0, "working": None}, {}
    if moved:   # committed before the run's record holds it: a failed commit leaves the record as the files are
        candidate = {**spec, "requirements": new_reqs, "oneshot": {**o, "design": made, "glossary": glossary}}
        if not (head := oneshot_spec.design_committed(repo, "Record the design the owner changed during the build",
                                                      candidate)):
            return None   # stopped with `folder`; the words still wait
        again |= {"design": made, "glossary": glossary, "design_head": head}
        changes["requirements"] = new_reqs
    # Past the backend, the frontend build takes the words; the backend's is not walked again, nor is it left for a
    # frontend whose tests are being rewritten (`retell_to`). A changed design, or contract tests not committed yet,
    # has the tests turn first, which hands on to that build.
    back = o.get("retell_to") or ("build-front" if o["stage"] in FRONT else "build")
    stage = back if o.get("tests_head") and not moved and o["stage"] != "tests" else "tests"
    # One save: the design, its requirements and head, the queue and the stage stand or fall together. Only the words
    # taken in leave the queue; one said during the turn, or kept for the frontend build, waits. A result that
    # waited for the owner's verdict no longer stands for what is built next.
    from . import oneshot_measure   # it imports this module's neighbours
    with oneshot._lock:
        now = oneshot_spec.spec_of(repo)
        spec = oneshot_measure.retire(now, "주인의 말로 설계를 다시 썼다 — 다시 빌드한 뒤 다시 잰다", changes, **again,
            tell_pending=[w for w in now["oneshot"].get("tell_pending") or [] if w not in pending], stage=stage,
            retell_to=back if stage == "tests" else None,
            retold=[*(o.get("retold") or []), {"words": words, "changed": moved, "ts": time.time()}])
    return stage, oneshot_build.tests_text(spec) if stage == "tests" else next_build(spec)


oneshot.DIVERTS.append(diverted)
oneshot_spec.WORKERS["retell"] = retell
