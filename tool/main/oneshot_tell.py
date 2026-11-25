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

from . import oneshot, oneshot_build, oneshot_sheet as sheet, oneshot_spec, work
from .oneshot_profile import STAGE_ORDER

router = APIRouter()
MAX_TELL = 4000
# From the first build turn until the run is done: before it, the design talk and R1 take the owner's words.
OPEN = STAGE_ORDER[STAGE_ORDER.index("tests"):STAGE_ORDER.index("done")]
# While the screens are designed with the owner (R2 and what leads to it) the words wait for the frontend build.
WAIT = STAGE_ORDER[STAGE_ORDER.index("split"):STAGE_ORDER.index("build-front")]
FRONT = STAGE_ORDER[STAGE_ORDER.index("build-front"):]
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
         "turn after you changes it. A planned test may pass already. Close with the same block as before, naming "
         "every contract file.\n\n")


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
        return told(spec["oneshot"], oneshot_front_build.build_front_text(spec))
    return oneshot_build.build_text(spec)


class Tell(BaseModel):
    text: str


@router.post("/api/oneshot/spec/{repo}/tell")
def tell(repo: str, body: Tell) -> dict:
    text = body.text.strip()
    if not text or len(text) > MAX_TELL:
        raise HTTPException(400, f"말은 1–{MAX_TELL}자로 적는다")
    with oneshot._lock:
        spec = oneshot_spec.spec_of(repo)
        o = spec["oneshot"]
        oneshot_spec.supported(o)
        if o.get("stage") not in OPEN:
            raise HTTPException(409, "설계를 승인한 뒤부터 실행이 끝나기 전까지 구현 에이전트에게 말할 수 있다")
        said = {"text": text, "ts": time.time(), "into_turn": steered(spec, text)}
        o = oneshot_spec.saved(repo, oneshot={"told": [*(o.get("told") or []), said],
                                              "tell_pending": [*(o.get("tell_pending") or []), text]})["oneshot"]
        if o.get("working") == "measure":   # the build goes back to work at once: the measurement stops
            from . import oneshot_measure   # it imports this module's neighbours
            if halt := oneshot_measure.HALTS.get(repo):
                halt.told = True   # `oneshot_measure.decided` takes the words in rather than stopping
                halt.set()
        idle = not o.get("working") and o["stage"] not in WAIT
    if idle:   # nothing is running to reach a start: the words are taken in now
        oneshot_spec.start(repo, "retell")
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


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
    o = store.get()
    if not o.get("tell_pending") or o.get("stage") not in OPEN or o["stage"] in WAIT:
        return False
    oneshot_spec.start(store.key.removeprefix("spec:"), "retell")
    return True


def retell(repo: str) -> None:
    """The design rewritten from the owner's words by the model that wrote it, and the build carried on."""

    spec = oneshot_spec.spec_of(repo)
    o, reqs = spec["oneshot"], spec.get("requirements") or []
    words = list(o.get("tell_pending") or [])
    if not words:
        oneshot_spec.saved(repo, oneshot={"working": None})
        return
    store, kind = oneshot_spec.store(repo), oneshot_spec.kinds_of(o)
    text = (oneshot_spec.design_text(spec) + f"\nThe current design:\n\n{json.dumps(o['design'], ensure_ascii=False)}"
            "\n\nWhile the product was being built, the owner said this, in their own words:\n\n"
            f"{json.dumps(words, ensure_ascii=False)}\n\nChange the design where these words change it: a requirement, "
            "a component, a choice such as the model a part runs, and every item built on what changed. Keep every "
            "other item exactly as it is. Return every design block in full, and an `oneshot-spec` block in full when "
            "a requirement changes.\n")
    every = set(sheet.every(o["design"], reqs))
    found = oneshot.turned(store, "design", oneshot_spec.design_prompt(kind), text,
                           sheet.blocks(kind) + ",oneshot-spec",
                           lambda final, attempt: oneshot_spec.design_turn(final, attempt, spec, every),
                           Path(spec["worktree"]))
    if found is None:   # stopped with its reason; the words still wait, so `[재개]` takes them in again
        return
    made, new_reqs, glossary, _ = found
    old, new = sheet.every(o["design"], reqs), sheet.every(made, new_reqs)
    moved = sorted(i for i in {*old, *new} if old.get(i) != new.get(i))
    retold = [*(o.get("retold") or []), {"words": words, "changed": moved, "ts": time.time()}]
    again = {"tell_pending": [], "retold": retold, "attempts": 0, "fix": None, "measure_fails": 0, "working": None}
    if moved:
        oneshot_spec.saved(repo, requirements=new_reqs, oneshot={"design": made, "glossary": glossary})
        if not (head := oneshot_spec.design_committed(repo, "Record the design the owner changed during the build")):
            return   # stopped with `folder`
        again["design_head"] = head
    # Past the backend, the frontend build takes the words; the backend's is not walked again. A changed design, or
    # contract tests not committed yet, has the tests turn first, which hands on to that build (`retell_to`).
    back = "build-front" if o["stage"] in FRONT else "build"
    stage = back if o.get("tests_head") and not moved else "tests"
    spec = oneshot_spec.saved(repo, oneshot={**again, "stage": stage, "retell_to": back})
    oneshot_build.send(repo, stage, oneshot_build.tests_text(spec) if stage == "tests" else next_build(spec))


oneshot.DIVERTS.append(diverted)
oneshot_spec.WORKERS["retell"] = retell
