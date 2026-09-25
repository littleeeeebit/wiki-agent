"""match — which wiki pages an utterance pulls in, and how they are rendered.

The rules come from the shared wiki, the knowledge from the target repository's
`.wiki/`. Nothing here translates: the hook entry point (`tool/inject.py`)
matches on the Korean the person typed, then weaves in `translate` itself.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .wikilib import WIKI, front_matter, rule_paragraph

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

# Each host's ceiling on `additionalContext`, in UTF-8 bytes. Past it the host
# stores the injection in a file and hands the session a 2 KB preview, so a
# page carried on that turn was not read. Bytes, because a byte-level BPE
# never makes more tokens than bytes: under the ceiling in bytes is under it in
# tokens. Characters give no such bound — one Hangul syllable can be several
# tokens.
#
# Codex: the install sets `additionalContextLimit = 12000`, in tokens
# (`tool/apply.py`), so 12,000 bytes is on the safe side. Claude: no published
# unit, so it was read off the transcripts on 2026-09-25 — 3,784 injections,
# the largest kept whole 9,896 characters, the smallest sent to a file 10,015.
# Claude counts characters, and characters never exceed bytes, so 9,800 bytes
# is under it. `trigger_audit replay` prints the same two numbers for the
# sessions it reads.
LIMIT = {"codex": 12000, "claude": 9800}


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


def render_parts(matched: list, rule_limit: int | None, repo_limit: int | None,
                 seen: set = frozenset(), repeatable: set = frozenset(),
                 squeeze: bool = False) -> tuple:
    """The two axes as actually sent. The audit counts the same thing — slots
    filled, summaries, the name list — rather than a tidier version of it.

    `seen` holds `(name, tag(body))` for pages this session already received
    in full; `repeatable` the names that declare `repeat: rule`. A page in
    both goes out as `repeated`. The key carries the body's tag, so a page
    edited mid-session is not seen and its new text goes out once in full.
    `squeeze` sends every declaring page as its rule paragraph — see `compose`.
    """
    decisions = sorted(
        (m for m in matched if m[2].parent.name == "decisions"),
        key=lambda m: m[2].name, reverse=True,
    )
    rules = [m for m in matched if m[2].parent.name != "decisions"]
    rules.sort(key=lambda item: 0 if item[0] == "landmine" else 1)
    parts = []
    for s, b, p in rules:
        known = (label(p), tag(b)) in seen
        if label(p) in repeatable and (known or squeeze) and rule_paragraph(b):
            parts.append(repeated(b, p, s, seen=known))
        else:
            parts.append(whole(s, b, p))
    parts, trimmed = fit(parts, rules, rule_limit)
    return rules, decisions, parts, knowledge(decisions, repo_limit), trimmed


def whole(severity: str, body: str, path: Path) -> str:
    return f"<!-- wiki:{label(path)} ({severity}) -->\n{body}"


def title_of(body: str, path: Path) -> str:
    return next((x[2:].strip() for x in body.splitlines() if x.startswith("# ")), path.stem)


def repeated(body: str, path: Path, severity: str, seen: bool = True) -> str:
    """What a page declaring `repeat: rule` carries when not in full.

    The title, the rule paragraph whole, and the path. The declaration says
    every clause that must hold on every turn sits inside that paragraph; a
    one-sentence form loses exactly those clauses.

    Two occasions. The session has already seen the page in full, or this
    turn is too large for the host to show whole (`compose`). The tail says
    which, because "loaded earlier" would be false on the second.
    """

    tail = (f"Loaded in full earlier this session: `{label(path)}.md`" if seen else
            f"Full page, left out because this turn is over the host's ceiling: `{label(path)}.md`")
    return (
        f"<!-- wiki:{label(path)} ({severity}, {'repeated' if seen else 'rule only'}) -->\n"
        f"# {title_of(body, path)}\n\n{rule_paragraph(body)}\n\n{tail}"
    )


def repeatable(available: list) -> set[str]:
    """The pages that declared `repeat: rule`. Any other page goes out in full
    on every turn — the default is the safe side."""

    return {label(p) for meta, _b, p in available if meta.get("repeat") == "rule"}


def remembered(rows: list[dict], limit: int) -> set[tuple[str, str]]:
    """What a session has seen in full, read off its own trajectory rows.

    A page counts as seen when it went out in full, on a turn whose `sent`
    was within the host's ceiling, after the last reset. Past the ceiling
    the host put the injection in a file and showed a 2 KB preview, so the
    full text on that turn was never read. A row with no `sent` — written
    before this existed — counts for nothing.
    """

    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row.get("reset"):
            seen = set()
        sent = row.get("sent")
        if isinstance(sent, int) and sent <= limit:
            seen |= {tuple(x) for x in row.get("full") or [] if len(x) == 2}
    return seen


def tag(text: str) -> str:
    """A short fingerprint. Recorded in place of the text it stands for."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def sent_whole(rules: list, parts: list[str]) -> list[list[str]]:
    """`[name, tag]` of each page that went out in full — tagged after the
    budget trimmed and the text was translated, so it is what actually left."""

    return [[label(p), tag(b)] for (s, b, p), part in zip(rules, parts)
            if part == whole(s, b, p)]


def compose(matched: list, limits: tuple, english: str, project: str | None,
            seen: set = frozenset(), repeat: set = frozenset(),
            limit: int | None = None) -> tuple:
    """`render_parts` and `assemble` together, fitted to the host's ceiling.

    Here rather than in the hook so `trigger_audit replay` measures the same
    bytes the hook sends. `limits` is `(rule_limit, repo_limit)`, `limit` the
    host's ceiling (`LIMIT`), `None` when the host is not known.

    Past the ceiling the host puts the whole injection in a file and shows the
    session a 2 KB preview, so a full page sent on that turn is not read — it
    only pushes everything behind it out of view. On such a turn every page
    that declared `repeat: rule` goes as its rule paragraph, seen or not: the
    declaration says the binding clauses are all there, and the rest was not
    going to be read. Pages that did not declare it still go in full. Without
    this, a Claude turn is over the ceiling so often that no page is ever
    seen and there is nothing to deduplicate.

    The rule index rides only on a turn that is still over the ceiling. It
    exists for the 2 KB preview, and a turn under the ceiling has none —
    there it is a second copy of every paragraph's first sentence. With no
    known ceiling the index stays, as before.

    Returns `render_parts`'s five, the body, and whether it was squeezed.
    """

    def fits(body: str) -> bool:
        return limit is not None and len(body.encode("utf-8")) <= limit

    out = render_parts(matched, *limits, seen, repeat)
    body = assemble(out[0], out[2] + out[3], english, project, index=False)
    if fits(body):
        return (*out, body, False)
    squeezed = limit is not None
    if squeezed:
        out = render_parts(matched, *limits, seen, repeat, squeeze=True)
        body = assemble(out[0], out[2] + out[3], english, project, index=False)
        if fits(body):
            return (*out, body, True)
    return (*out, assemble(out[0], out[2] + out[3], english, project), squeezed)


def assemble(rules: list, parts: list[str], english: str, project: str | None,
             index: bool = True) -> str:
    """The whole `additionalContext`, or `""` when there is nothing to send.

    `english` is the rendering block, already labelled.
    """

    # The rule index goes first. It is a few hundred characters, and the
    # rendering in front of it could reach 4,000 (`inject.MAX_RENDERED`) and
    # push every rule sentence out of the 2 KB preview. See `rule_index`.
    blocks = []
    listing = rule_index(rules) if index else ""
    if listing:
        blocks.append(listing)
    # Before the pages, not after them. The rendering is carried even when no
    # page matched: the utterance is agent input on every turn, and tying it
    # to a trigger would drop it on exactly the turns no rule covers.
    #
    # Position is the other half of that. A host persists an injection past
    # about 12 KB and hands the session a 2 KB preview instead; the rules
    # alone reach 12,205 characters on an ordinary turn, so anything after
    # them is cut. Measured on 2026-09-22 in a web chat session: the rules
    # arrived, this block did not, and nothing said so. Behind the short
    # index it still starts inside the preview.
    if english:
        blocks.append(english)
    # The header and the source map stay on a turn where every rule was
    # already seen. They are a few hundred characters, and a branch that
    # drops them is a branch that can drop the repeated forms with them.
    if parts:
        blocks.append(
            "Below is what the wiki loaded for this utterance. A rule marks a "
            "place where something actually went wrong before; knowledge is "
            "something already decided.\n\n"
            + source_map(rules, project)
            + "\n\n"
            + "\n\n---\n\n".join(parts)
        )
    return "\n\n---\n\n".join(blocks)
