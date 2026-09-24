"""match — which wiki pages an utterance pulls in, and how they are rendered.

The rules come from the shared wiki, the knowledge from the target repository's
`.wiki/`. Nothing here translates: the hook entry point (`tool/inject.py`)
matches on the Korean the person typed, then weaves in `translate` itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from .wikilib import WIKI, front_matter

INJECTABLE = {"landmine", "contract"}
SLOT = re.compile(r"\{([a-z][a-z0-9_]*)\}")

# No budget by default. Every rule that matched is carried.
#
# Because nothing here may be dropped. What would be dropped is a rule the
# triggers selected — the one judged necessary for this utterance — and a rule
# broken because it was never carried is the failure this wiki exists to
# prevent. A ceiling must not manufacture that failure itself.
#
# Where a budget is set, it trims rather than cuts. The lowest severity goes
# from full text to its one rule line first, and past that to a title and a
# path. No page disappears.
#
# The project sets the budget through its adapter. It has nothing to do with
# the target repository's prompt limit: a hook's `additionalContext` does not
# travel that path, so borrowing that number here imposes someone else's
# ceiling.
#
# One budget per axis, deliberately. Sharing one means the thing that shrinks
# under pressure is always the rules, because `fit` trims rules and does not
# touch a decision record. The heaviest turn actually measured held
# 20,620 characters of rules against 2,173 of decisions: knowledge is 9% of
# it and carries 0% of the trimming. The side that grows must not push out
# the side that has to hold.
RULE_BUDGET = "rule_budget"
REPO_BUDGET = "repo_budget"

# How many decision records go in as full text in one turn. What is over that
# is not dropped; its name stays.
MAX_DECISIONS = 3


def budget(adapter: str | None, slot: str, project: str | Path | None = None) -> int | None:
    """This project's budget for that axis. `None` when unset — no ceiling."""

    raw = slots_for(adapter, project).get(slot)
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def shrink(body: str, path: Path, severity: str, hard: bool) -> str:
    """Shorten the full text. Never remove it.

    `hard` leaves a title and a path, otherwise the rule paragraph survives.
    Either way the fact that this page matched, and where to open it, are
    still there — which is the whole difference from dropping it.
    """

    title = next((x[2:].strip() for x in body.splitlines() if x.startswith("# ")), path.stem)
    head = f"<!-- wiki:{label(path)} ({severity}, shortened) -->\n# {title}"
    if hard:
        return head + f"\n\nFull page: `{label(path)}.md`"
    # Both spellings. Page bodies turn English in stage 2, and a shrink that
    # only knows `규칙.` would leave the title with no rule under it exactly
    # when the budget is tight -- the turn where the rule matters most.
    rule = next(
        (x for x in body.splitlines() if x.startswith(("규칙.", "Rule."))), ""
    )
    return head + (f"\n\n{rule}" if rule else "") + f"\n\nFull page: `{label(path)}.md`"


def rule_index(rules: list) -> str:
    """One sentence per loaded rule, meant to sit near the top of the injection.

    A host that receives more than about 12 KB stores the injection in a file
    and gives the session only the first 2 KB of it. With a repository's
    28,000-character `plan-active` in the mix, that happens on most turns.
    Measured on 2026-09-23 in ai-nara-shop: two turns sent 42 KB and 35 KB,
    and the session's preview ended partway through the first rule. Every
    rule after that one was never shown to the session. The full pages still
    go out below this index. The index only makes sure each rule's opening
    sentence is inside the part the host keeps.

    A page with no `Rule.` paragraph, which is repository knowledge, is left
    out of the index.
    """

    lines = []
    for _s, body, path in rules:
        para = re.search(r"^(?:Rule|규칙)\.\s+(.+?)(?:\n\s*\n|\Z)", body, re.M | re.S)
        if para:
            first = re.split(r"(?<=\.)\s", " ".join(para.group(1).split()), maxsplit=1)[0]
            lines.append(f"- `{label(path)}` — {first}")
    if not lines:
        return ""
    return (
        "<!-- wiki:rule-index -->\n"
        "Rules loaded this turn, one sentence each. The full pages follow below.\n"
        + "\n".join(lines)
    )


def fit(parts: list[str], rules: list, limit: int | None) -> tuple[list[str], int]:
    """Trim to the budget. Over it, still nothing is thrown away.

    Lowest severity first, down to the one rule line, then to the title alone.
    With no budget this does nothing, which is the default.
    """

    if not limit or sum(len(p) for p in parts) <= limit:
        return parts, 0

    trimmed = 0
    for hard in (False, True):
        for i in range(len(rules) - 1, -1, -1):
            if sum(len(p) for p in parts) <= limit:
                return parts, trimmed
            severity, body, path = rules[i]
            small = shrink(body, path, severity, hard)
            if len(small) < len(parts[i]):
                parts[i] = small
                trimmed += 1
    return parts, trimmed


def digest(body: str, path: Path) -> str:
    """Reduce a decision record to two lines.

    The full text must not go in. One repository holds 88 of them at about
    700 characters each, so three matches alone push the rules out. Carrying
    them whole was measured: the heaviest turn reached 13,241 characters, over
    the ceiling, at a 67% hit rate — and this wiki's own standard is that past
    40% you are carrying everything, which reads the same as carrying nothing.

    What is needed at injection time is "this was already decided, and here is
    why", not the record. The record is at the end of the path.
    """

    title = next((x[2:].strip() for x in body.splitlines() if x.startswith("# ")), path.stem)
    # Both spellings, for the same reason `shrink` takes both. The body is
    # translated before this runs, so a parser that only knows `왜.` finds
    # nothing and the agent gets a decision title with no reason under it —
    # which reads as a decision made for no reason.
    why = next(
        (x.split(".", 1)[1].strip() for x in body.splitlines()
         if x.startswith(("왜.", "Why."))),
        "",
    )
    head = re.split(r"(?<=다\.)\s", why, maxsplit=1)[0][:180] if why else ""
    return f"- {title}\n  {head}\n  Full record: `.wiki/decisions/{path.stem}.md`"


def knowledge(decisions: list, limit: int | None) -> list[str]:
    """The decision-record block. A different budget from the rules, on purpose.

    Knowledge must not eat the rules' place. A rule broken because it was
    never carried is the failure this wiki exists to prevent, and knowledge
    must not be what creates it.

    Over the budget, full texts drop to names one at a time. Nothing vanishes
    here either: what matched stays, and so does where to open it.
    """

    if not decisions:
        return []

    keep = MAX_DECISIONS
    while True:
        briefs = [digest(b, p) for _s, b, p in decisions[:keep]]
        rest = [p.stem for _s, _b, p in decisions[keep:]]
        block = (
            "<!-- wiki:decisions -->\n"
            "This has been decided before. Read the reason before reversing it."
        )
        if briefs:
            block += "\n\n" + "\n".join(briefs)
        if rest:
            more = " more" if briefs else ""
            block += (
                f"\n\n{len(rest)}{more} decision(s) on this: "
                + ", ".join(f"`{n}`" for n in rest[:8])
                + (" …" if len(rest) > 8 else "")
            )
        if limit is None or len(block) <= limit or keep == 0:
            return [block]
        keep -= 1


def label(path: Path) -> str:
    """What a page is called: `scope/name` in the shared wiki, `.wiki/name` in a project."""

    if path.parent.name == "decisions":
        return f".wiki/decisions/{path.stem}"
    if path.parent.name == ".wiki":
        return f".wiki/{path.stem}"
    return f"{path.parent.name}/{path.stem}"


def source_map(matched: list, project: str | None) -> str:
    """Which repository the injected pages came from, written as absolute paths.

    Without this line a session read "record it in the wiki" as the current
    repository's `.wiki/`. Most of the injected pages had come from the hub,
    the header said "this repository's wiki", and the hub's path appeared
    nowhere. A name cannot decide it — both places are called "the wiki".

    Which scope holds what is stated alongside it. `operator` and `craft`
    name no repository, so they live in the hub; a repository's gates,
    launchers, ports and invariants live in that repository's `.wiki/`.
    """

    hub = Path(__file__).resolve().parents[2]
    from_hub = sorted(
        {label(p) for _s, _b, p in matched if not str(label(p)).startswith(".wiki/")}
    )
    from_repo = sorted(
        {label(p) for _s, _b, p in matched if str(label(p)).startswith(".wiki/")}
    )

    lines = ["**Where these came from.** Two places. Decide here which one to edit."]
    lines.append(f"- Hub wiki `{hub}` — `operator/` `craft/`. Rules that name no repo")
    if project:
        repo_wiki = Path(project).expanduser() / ".wiki"
        lines.append(f"- This repo `{repo_wiki}` — gates, launchers, ports, invariants")
    else:
        lines.append("- The target repo's `.wiki/` — gates, launchers, ports, invariants")
    if from_hub:
        lines.append(f"- From the hub this time: {', '.join(from_hub)}")
    if from_repo:
        lines.append(f"- From this repo this time: {', '.join(from_repo)}")
    return "\n".join(lines)


def adapter_path(adapter=None, project=None, *, wiki=None) -> Path | None:
    """The checkout's own file first. Only an older install with none falls back
    to looking the name up in the hub."""
    if project:
        local = Path(project).expanduser().resolve() / ".wiki/adapter.toml"
        if local.exists():
            return local
    return (wiki or WIKI) / "adapters" / f"{adapter}.toml" if adapter else None


def slots_for(adapter: str | None, project: str | Path | None = None) -> dict[str, str]:
    """The chosen checkout's slot values. No adapter means an empty table."""

    path = adapter_path(adapter, project)
    if path is None or not path.exists():
        return {}
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {k: str(v) for k, v in (data.get("slots") or {}).items()}


def fill(body: str, values: dict[str, str]) -> str:
    """Replace `{slot}` and nothing else.

    `str.format` would also reach a trigger's `{0,10}` and every brace in a
    code block. Only known names are swapped; an unknown one is left standing,
    and `apply` points at whatever is still unfilled.
    """

    return SLOT.sub(lambda m: values.get(m.group(1), m.group(0)), body)


def project_wiki(project: str | Path | None) -> Path | None:
    """The target repository's `.wiki/`, or `None`."""

    if not project:
        return None
    directory = Path(project).expanduser() / ".wiki"
    return directory if directory.is_dir() else None


def pages(
    adapter: str | None = None,
    project: str | Path | None = None,
) -> list[tuple[dict[str, object], str, Path]]:
    """The shared wiki's rules plus that project's knowledge.

    Project pages include `decisions/`. There are many of those — 90 in one
    repository — so carrying them all would blow the budget, and the selecting
    side keeps a few. This function only reads.
    """

    values = slots_for(adapter, project)
    found = []
    for scope in ("operator", "craft"):
        directory = WIKI / scope
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            meta, body = front_matter(path.read_text(encoding="utf-8"))
            found.append((meta, fill(body, values) if values else body, path))

    local = project_wiki(project)
    if local:
        for path in sorted(local.glob("*.md")) + sorted(local.glob("decisions/*.md")):
            meta, body = front_matter(path.read_text(encoding="utf-8"))
            found.append((meta, fill(body, values) if values else body, path))
    return found


def match_pages(prompt: str, available: list) -> list:
    """Injection and the audit share one severity and regex judgement."""
    matched = []
    for meta, body, path in available:
        severity = str(meta.get("severity") or "")
        triggers = meta.get("triggers")
        if severity not in INJECTABLE or not isinstance(triggers, list):
            continue
        for pattern in triggers:
            try:
                if re.search(str(pattern), prompt, re.IGNORECASE):
                    matched.append((severity, body, path))
                    break
            except re.error:
                continue
    return matched


def render_parts(matched: list, rule_limit: int | None, repo_limit: int | None) -> tuple:
    """The two axes as actually sent. The audit counts the same thing — slots
    filled, summaries, the name list — rather than a tidier version of it."""
    decisions = sorted(
        (m for m in matched if m[2].parent.name == "decisions"),
        key=lambda m: m[2].name, reverse=True,
    )
    rules = [m for m in matched if m[2].parent.name != "decisions"]
    rules.sort(key=lambda item: 0 if item[0] == "landmine" else 1)
    parts = [f"<!-- wiki:{label(p)} ({s}) -->\n{b}" for s, b, p in rules]
    parts, trimmed = fit(parts, rules, rule_limit)
    return rules, decisions, parts, knowledge(decisions, repo_limit), trimmed
