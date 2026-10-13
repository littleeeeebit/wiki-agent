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


def kind_of(name: str, decision_kind: str = "") -> str | None:
    """The kind of question `name`, or `None` for a name this table does not know.
    An action request's one question is the `action` kind whatever it is called."""

    if decision_kind == "action":
        return "action"
    if name in NAMED:
        return NAMED[name]
    m = STEM.match(name)
    return m.group(1) if m and m.group(1) in KINDS else None
