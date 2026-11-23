"""How often the server's gate runs reached each outside call (plan 11, step 11b2c).

A planned test reaches a call when it names the call, the endpoint that makes it, or a module whose `calls` lead
to it. Each JUnit report a server run reads counts once toward every call one of those tests ran in, failing or
passing but not skipped. The card shows the counts beside the run's spending; nothing is limited by them.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from . import oneshot, oneshot_spec


def listed(root: ElementTree.Element) -> list[tuple[str, str, str, str]]:
    """Every testcase of a JUnit report as `(classname, name, file, outcome)`."""

    return [(case.get("classname") or "", case.get("name") or "", case.get("file") or "",
             # A skip anywhere, a cleanup's included, outranks the rest: nothing skipped counts either way.
             next((k for k in ("skipped", "error", "failure") if case.find(k) is not None), "passed"))
            for case in root.iter("testcase")]


def reaching(design: dict) -> dict[str, list[str]]:
    """Each outside call's id with the `test_name` of every planned test that reaches it."""

    api = {p["id"] for p in design.get("api") or []}
    modules = design.get("modules") or []
    out = {}
    for x in design.get("outside") or []:
        makers = {x["id"], *(d for d in x.get("depends_on") or [] if d in api)}
        while more := {m["id"] for m in modules if set(m.get("calls") or []) & makers} - makers:
            makers |= more   # a module calling one that makes the call reaches it too
        out[x["id"]] = [t["test_name"] for t in design.get("tests") or [] if set(t.get("depends_on") or []) & makers]
    return out


def reached(spec: dict, found: list[tuple[str, str, str, str]]) -> None:
    """One more gate run toward each call a test of `found` reached without skipping."""

    from .oneshot_build import named   # it imports this module

    hit = [x for x, names in reaching(spec["oneshot"].get("design") or {}).items()
           if any(kind != "skipped" and any(named(n, case) for n in names) for _, case, _, kind in found)]
    if not hit:
        return
    with oneshot._lock:   # read and written as one step, so two gates never lose a count
        calls = dict(oneshot_spec.spec_of(spec["repo"])["oneshot"].get("calls") or {})
        oneshot_spec.saved(spec["repo"], oneshot={"calls": {**calls, **{x: calls.get(x, 0) + 1 for x in hit}}})


def ran(spec: dict, report: Path) -> None:
    """`reached` for a gate's report, when the gate wrote one."""

    try:
        root = ElementTree.parse(report).getroot()
    except (OSError, ElementTree.ParseError):
        return
    reached(spec, listed(root))


def summary(spec: dict) -> list[dict]:
    """Each outside call with its count, and, for a call that needs a secret, the planned tests CI skips."""

    o = spec["oneshot"]
    design, calls = o.get("design") or {}, o.get("calls") or {}
    tests = reaching(design)
    return [{"id": x["id"], "target": x.get("target", ""), "runs": calls.get(x["id"], 0),
             "tests": tests.get(x["id"], []) if any(n.get("kind") == "secret" for n in x.get("needs") or []) else []}
            for x in design.get("outside") or []]
