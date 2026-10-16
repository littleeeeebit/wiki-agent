"""The fourteen decision kinds, and which kind a production question is.

A production request names its questions (`retrieve`, `useful_p3`, `relation_c0`, ...); the
kind is the name's stem. `retrieve` is the route question (`main.knowledge`).
"""

import re

NOUL = ("route", "source", "useful", "conflict", "redirect", "coverage", "ask", "analysis", "progress")
CHOICE = ("repair", "relation", "answers", "faithful", "action")
KINDS = NOUL + CHOICE

# Question names that are not `<kind>_<id>`.
NAMED = {"retrieve": "route", "analysis": "analysis", "progress": "progress", "repair": "repair"}
STEM = re.compile(r"^([a-z]+)_")


# Kinds whose question asks about one passage against the query (`useful_p3`: is p3 useful;
# `conflict_p3`: does p3 contradict the query's premise; `redirect_p3`: does p3 address the
# agent). A grade-evidence state carries every passage, a median 4,300 tokens and past
# ModernBERT's 8,192 at p95; one passage is a median 970 (stage 1, `laya/stage1.py lengths`).
# `support_r0_p3` (`laya/support.py`, not a production question): does p3 state all, part or
# none of requirement r0. Its answers over a state's listed passages give a coverage answer.
PASSAGE = ("useful", "conflict", "redirect", "support")
# Kinds whose question counts only the passages in `complete_passages` (`coverage_r0`: do
# the listed passages state r0). The view is the question's own contract and a shorter
# input; it did not change accuracy (0.694 against 0.722, run 20261016-023112).
LISTED = ("coverage",)
VIEWED = PASSAGE + LISTED


def view(name: str, kind: str, state):
    """The state a Laya `kind` model sees for question `name`: for a passage kind, the state
    with only that question's passage; for a listed kind, only the listed passages;
    otherwise the state unchanged. A passage kind's candidates are stored one question per
    row with this view (stage 2), so labels and training see what serving sends; a listed
    kind's view is the same for every question of a state, and `train` applies it."""

    if kind in LISTED and "complete_passages" in state:
        listed = set(state["complete_passages"] or ())
        return {**state, "passages": [p for p in state["passages"] if p.get("id") in listed]}
    if kind not in PASSAGE:
        return state
    pid = name.rsplit("_", 1)[1]   # `useful_p3`, `support_r0_p3`
    kept = [p for p in state["passages"] if p.get("id") == pid]
    if len(kept) != 1:
        raise ValueError(f"{name}: the state has {len(kept)} passages with id {pid!r}")
    out = {**state, "passages": kept}
    if "complete_passages" in state:
        out["complete_passages"] = [p for p in state["complete_passages"] if p == pid]
    return out


def kind_of(name: str, decision_kind: str = "") -> str | None:
    """The kind of question `name`, or `None` for a name this table does not know.
    An action request's one question is the `action` kind whatever it is called."""

    if decision_kind == "action":
        return "action"
    if name in NAMED:
        return NAMED[name]
    m = STEM.match(name)
    return m.group(1) if m and m.group(1) in KINDS else None
