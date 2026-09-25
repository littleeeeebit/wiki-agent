"""Whether the two axes really do have separate budgets."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

WORD = "예산시험어"
MARK = "<!-- wiki:decisions -->"

RULE = f"""---
scope: craft
severity: contract
triggers: ["{WORD}"]
slots: []
sources: []
links: []
---

# 규칙 제목

규칙. {"버" * 120}

왜. {"근" * 120}
"""

DECISION = """---
scope: project
severity: contract
triggers: ["{word}"]
---

# 결정 {n} 의 제목

왜. {why} 그리고 여기부터는 요약에 안 들어간다.
"""


def seed_cache(path: Path, korean: str, english: str) -> None:
    """Put one translation in a throwaway cache, so the hook needs no network.

    The cache is consulted before the key is, so a seeded row makes the real
    `translate` path produce a rendering with no request. That is the only way
    to measure what the hook assembles *with* a rendering without either
    calling Gemini or faking the function the hook does not import from here.
    """

    import sqlite3

    import translate

    path.parent.mkdir(parents=True, exist_ok=True)
    _keep, _fixed, version = translate.glossary()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS shots (k TEXT PRIMARY KEY, v TEXT)")
    db.execute(
        "INSERT OR REPLACE INTO shots (k, v) VALUES (?, ?)",
        (translate._key(translate.KO_EN, version, korean), english),
    )
    db.commit()
    db.close()


def build(decisions: int, rule_budget: int | None, repo_budget: int | None,
          rendered: str | None = None) -> str:
    """Stand up a throwaway wiki and repository, run the hook once, return what
    it injected."""

    root = Path(tempfile.mkdtemp())
    wiki, project = root / "wiki", root / "project"
    (wiki / "craft").mkdir(parents=True)
    (wiki / "craft" / "big.md").write_text(RULE, encoding="utf-8")

    (wiki / "adapters").mkdir()
    slots = ["[slots]"]
    if rule_budget:
        slots.append(f"rule_budget = {rule_budget}")
    if repo_budget:
        slots.append(f"repo_budget = {repo_budget}")
    (wiki / "adapters" / "x.toml").write_text("\n".join(slots) + "\n", encoding="utf-8")

    out = project / ".wiki" / "decisions"
    out.mkdir(parents=True)
    for n in range(decisions):
        (out / f"2026-01-{n + 1:02d}-{n}.md").write_text(
            DECISION.format(word=WORD, n=n, why="왜" * 60), encoding="utf-8"
        )

    if rendered:
        seed_cache(root / "translate-cache.sqlite3", f"{WORD} 를 쓴다", rendered)

    done = subprocess.run(
        [sys.executable, str(HERE / "inject.py"), "--adapter", "x",
         "--project", str(project)],
        input=json.dumps({"prompt": f"{WORD} 를 쓴다", "session_id": "t"}),
        capture_output=True, text=True, encoding="utf-8",
        # The key is emptied so the translation fails open. Left in, every
        # call to this helper makes a real Gemini round trip: the suite gets
        # slow, flaky and expensive. The English rendering itself is measured
        # below, by the dedicated tests, with a fake translator.
        #
        # `TRANSLATE_ENV` is what actually disarms it. The `.env` beside the
        # repository outranks the variable, so once a real one exists in the
        # checkout, emptying the variable suppresses nothing and this helper
        # goes back to spending money. The variable is emptied as well, for
        # the fallback the file no longer covers.
        #
        # The cache is redirected to a temporary one too. Emptying the key is
        # not enough on its own: the cache answers before the key is
        # consulted, so an earlier run having cached this utterance makes the
        # translation succeed without one. That is how this test went red once.
        env=dict(os.environ) | {
            "WIKI_ROOT": str(wiki),
            "PYTHONIOENCODING": "utf-8",
            "GEMINI_API_KEY": "",
            "TRANSLATE_ENV": str(root / "absent.env"),
            "TRANSLATE_CACHE": str(root / "translate-cache.sqlite3"),
        },
    )
    payload = json.loads(done.stdout or "{}")
    return payload.get("hookSpecificOutput", {}).get("additionalContext", "")


def rule_half(text: str) -> str:
    """Only the rules half of the injection, with the `---` separators removed.

    With no decisions there is no separator either. Left in, the presence or
    absence of decisions registers as the difference and the thing actually
    being measured never gets measured.

    The header goes too. The source table carries the target repository's
    absolute path and `build` makes a fresh temporary folder on every call,
    so leaving it in reports a difference every time because the path
    differs. The one thing this function measures is whether the rules shrank.
    """

    head = text.split(MARK)[0]
    # Past the rule index, which sits before the header and its path.
    first = head.find("<!-- wiki:", head.find("Below is what the wiki"))
    body = head[first:] if first >= 0 else head
    return body.strip().rstrip("-").strip()


def test_more_decisions_shrink_the_rules_by_not_one_character():
    # The budget is set so the rules alone fit and the rules plus the
    # decisions do not. Under the single budget this would be where the rules
    # collapse to their one rule line.
    alone = build(decisions=0, rule_budget=700, repo_budget=None)
    crowded = build(decisions=10, rule_budget=700, repo_budget=None)
    assert MARK in crowded, "결정이 실리지 않았다. 시험이 아무것도 안 재고 있다"
    assert rule_half(alone) == rule_half(crowded)
    assert "shortened" not in rule_half(crowded), rule_half(crowded)[:200]


def test_a_tight_rule_budget_trims_only_the_rules():
    text = build(decisions=10, rule_budget=200, repo_budget=None)
    assert "shortened" in rule_half(text), rule_half(text)[:200]
    # Trimming the rules does not make a decision disappear.
    assert MARK in text


def test_the_knowledge_budget_trims_only_the_decisions():
    wide = build(decisions=10, rule_budget=None, repo_budget=None)
    tight = build(decisions=10, rule_budget=None, repo_budget=300)
    assert rule_half(wide) == rule_half(tight)
    assert len(tight.split(MARK)[1]) < len(wide.split(MARK)[1])


def test_however_tight_the_knowledge_budget_the_names_survive():
    text = build(decisions=10, rule_budget=None, repo_budget=1)
    block = text.split(MARK)[1]
    # At most eight names are shown, newest first. The newest never
    # disappears, at any budget.
    assert "2026-01-10-9" in block, block
    assert "10 decision(s) on this" in block, block


def test_under_one_budget_knowledge_would_have_pushed_the_rules_out():
    """Why the two were separated, written down as code.

    `fit` trims rules and nothing else. Put both under one budget and the
    overflow comes off the rules, so the rules shrink by exactly as much as
    the knowledge grew. The whole trimming burden lands on the side that did
    not grow.

    This test measures what would happen without the split rather than the
    split code as it stands. That is what keeps one green from erasing what
    this design is preventing.
    """

    from wiki.match import fit, knowledge

    body = RULE.split("---", 2)[-1].lstrip("\n")
    page = Path("craft") / "big.md"
    rules = [("contract", body, page)]
    rule_parts = [f"<!-- wiki:craft/big (contract) -->\n{body}"]

    decisions = [
        ("contract", DECISION.format(word=WORD, n=n, why="왜" * 60), Path(f"d{n}.md"))
        for n in range(3)
    ]
    repo_parts = knowledge(decisions, None)

    # A budget the rules alone fit inside and the rules plus the knowledge do
    # not. The same value is given to both arrangements.
    limit = len(rule_parts[0]) + 40
    assert len(rule_parts[0]) <= limit < len(rule_parts[0]) + len(repo_parts[0])

    old, squeezed = fit(list(rule_parts) + list(repo_parts), rules, limit)
    new, untouched = fit(list(rule_parts), rules, limit)

    assert untouched == 0 and new == rule_parts, "갈라 놓으면 규칙은 안 줄어든다"
    assert squeezed and old[0] != rule_parts[0], "한 예산이면 규칙이 줄어든다"
    assert old[1] == repo_parts[0], "그런데 지식은 한 글자도 안 줄었다"


def test_with_no_budget_nothing_is_trimmed():
    text = build(decisions=10, rule_budget=None, repo_budget=None)
    assert "shortened" not in text


# ---- The English rendering of the utterance --------------------------------
#
# Measured with a fake translator. Calling the real one ties these tests to a
# network and a bill, and worse, a run where the translation failed and one
# where it succeeded look like the same green.
#
# No `monkeypatch` fixture. The direct-run runner at the bottom of this file
# calls these with no arguments, so taking a fixture would make them checks
# that only run under pytest — and `docs/development.md` says to run this file
# directly.


def _rendering(prompt: str, answer: str | None) -> str:
    import inject
    import translate

    was = translate.translate
    translate.translate = lambda texts, direction, deadline: [answer or t for t in texts]
    try:
        return inject.rendering(prompt, time.monotonic() + 5)
    finally:
        translate.translate = was


def test_the_rendering_is_attached_only_when_the_utterance_is_korean():
    korean = _rendering("규칙을 지켜라", "Follow the rule")
    assert "Follow the rule" in korean
    assert "English rendering" in korean

    assert _rendering("just plain english", "SHOULD NOT BE CALLED") == ""


def test_a_failed_translation_gets_no_rendering_label():
    """Labelling the original as a rendering is the worst failure here.

    The reading side has no way to check whether something was translated, so
    mislabelled Korean gets read as English and trusted. When the translator
    returns the original unchanged — that is, when it failed — the block is
    not built at all.
    """

    assert _rendering("규칙을 지켜라", None) == ""


def test_triggers_match_against_the_korean_original():
    """The order is the whole of this change.

    Hand `match_pages` the translation and the Korean regexes scan an English
    sentence, match nothing — and nothing matching is indistinguishable from
    nothing applying. The injection disappears without a word.
    """

    from wiki.match import match_pages

    meta = {"severity": "contract", "triggers": [WORD]}
    available = [(meta, "규칙. 본문", Path("craft") / "x.md")]

    assert match_pages(f"{WORD} 를 쓴다", available), "the original has to match"
    assert not match_pages("writes the budget test word", available), (
        "matching the translation would leave this test blind to a swapped order"
    )


def test_only_project_pages_and_decision_summaries_are_translated():
    """What the plan set down for phase 1, and a place this once missed.

    A `.wiki/` page body and a decision summary are agent input, so they are
    translated. The hub's `operator/` and `craft/` have their originals
    rewritten in English in phase 2, so translating them here pays for the
    same words twice and throws the second one away.
    """

    import time

    import inject
    import translate

    was = translate.translate
    translate.translate = lambda texts, direction=None, deadline=None: [
        "EN:" + t for t in texts
    ]
    try:
        matched = [("contract", "규칙. 허브", Path("craft/x.md")),
                   ("contract", "규칙. 저장소", Path(".wiki/y.md")),
                   ("contract", "왜. 한국어 이유", Path(".wiki/decisions/001.md"))]
        out = inject.localised(matched, time.monotonic() + 5)
    finally:
        translate.translate = was

    assert out[0][1] == "규칙. 허브", "허브 페이지는 이 단계에서 안 옮긴다"
    assert out[1][1].startswith("EN:"), "저장소 페이지가 번역을 안 거쳤다"
    assert out[2][1].startswith("EN:"), "결정 본문이 번역을 안 거쳤다"


def test_the_budget_and_the_record_measure_the_translated_length():
    """Put the translation after `render_parts` and both go wrong together.

    `fit` trims against the Korean length while English is usually longer, so
    a block that was just fitted to the budget overflows again once
    translated. `trajectory.cost` would record the pre-translation number too,
    and `trigger_audit` reads that value as the real injected size.
    """

    import time

    import inject
    import translate
    from wiki import match

    was = translate.translate
    translate.translate = lambda texts, direction=None, deadline=None: [
        t + "x" * 100 for t in texts
    ]
    try:
        matched = [("contract", "규칙. 짧다", Path(".wiki/y.md"))]
        grown = inject.localised(matched, time.monotonic() + 5)
        _rules, _dec, parts, _repo, _trimmed = match.render_parts(grown, None, None)
    finally:
        translate.translate = was

    assert len(parts[0]) > 100, "렌더링이 번역된 본문을 안 썼다"


def test_the_rendering_comes_before_the_rules():
    """A host persists a large injection and hands the session a preview.

    The rules alone reach 12,205 characters on an ordinary turn, past the
    roughly 12 KB where that happens, so whatever sits after them is cut.
    Measured on 2026-09-22 in a web chat session on both hosts: the rules
    arrived, the rendering did not, and nothing reported it. Position is the
    fix — this block is a few hundred characters and it is the one the person
    reads to check what was understood.
    """

    context = build(decisions=1, rule_budget=None, repo_budget=None,
                    rendered="writes the budget test word")

    assert "wiki:english-rendering" in context, context[:200]
    assert context.index("wiki:english-rendering") < context.index("Below is what the wiki"), (
        "the rendering has to precede the rules, or a preview drops it"
    )


def test_every_rule_sentence_lands_inside_the_2kb_preview():
    """A host that receives more than about 12 KB keeps only the first 2 KB.

    Measured on 2026-09-23 in ai-nara-shop: 42 KB went in, and the preview
    ended partway through the first rule. The index puts each rule's opening
    sentence ahead of the full pages. Twenty decisions stand in for the bulk
    of a repository's pages.
    """

    from wiki import match

    context = build(decisions=20, rule_budget=None, repo_budget=None,
                    rendered="writes the budget test word")
    head = context[:2000]

    assert "wiki:rule-index" in head, head
    assert "`craft/big` — " + "버" * 120 in head, head
    # A long utterance renders to more than the preview by itself. The index
    # has to come before it, or no rule sentence survives the cut.
    context = build(decisions=0, rule_budget=None, repo_budget=None,
                    rendered="long rendering " * 200)
    assert "`craft/big` — " in context[:2000], context[:2000]
    assert context.index("wiki:english-rendering") < context.index("Below is what the wiki")

    page = "# T\n\nRule. First sentence here. Second one\nwraps here.\n\nWhy. x\n"
    assert match.rule_index([("landmine", page, Path("operator/t.md"))]).endswith(
        "- `operator/t` — First sentence here."
    )
    assert match.rule_index([("contract", "# plan\n\nno rule\n", Path(".wiki/p.md"))]) == ""


def test_the_rendering_goes_out_even_when_no_rule_matched():
    """`if not parts: return 0` used to swallow the utterance translation here.

    The utterance is agent input on every turn. Hanging it off the triggers
    makes the translation disappear on exactly the turns where no rule applies
    — the turns where the wiki has nothing else to offer.
    """

    assert _rendering("규칙을 지켜라", "EN").endswith("EN")


def test_a_page_that_eats_the_deadline_does_not_starve_the_rendering():
    """The utterance is translated before the pages, not after.

    Measured on 2026-09-23 in ai-nara-shop: `plan-active`, 28,000 characters,
    held the page request past the whole 8-second deadline, and the rendering
    behind it got nothing. The fake below plays that page: whichever call
    comes second finds the budget already spent.
    """

    import io

    import inject
    import translate

    root = Path(tempfile.mkdtemp())
    (root / ".wiki").mkdir()
    (root / ".wiki" / "big.md").write_text(
        f'---\nseverity: contract\ntriggers: ["{WORD}"]\n---\n\n규칙. 긴 계획\n',
        encoding="utf-8",
    )

    spent = []

    def fake(texts, direction=None, deadline=None):
        out = texts if spent else ["EN:" + t for t in texts]
        spent.append(texts)
        return list(out)

    was = translate.translate, sys.stdin, sys.stdout, sys.argv
    translate.translate = fake
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(
        {"prompt": f"{WORD} 를 쓴다"}, ensure_ascii=False).encode("utf-8")))
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.argv = ["inject.py", "--project", str(root)]
    try:
        inject.main()
        sys.stdout.seek(0)
        payload = json.loads(sys.stdout.read())
    finally:
        translate.translate, sys.stdin, sys.stdout, sys.argv = was

    assert "wiki:english-rendering" in payload["hookSpecificOutput"]["additionalContext"], (
        "the page translation ran first and left the rendering no time"
    )


def test_the_record_carries_what_was_sent_and_a_quiet_turn_too():
    """`sent` is the bytes of the whole `additionalContext`, `full` the pages
    that went out untrimmed. A turn that matched nothing and sent nothing is
    still a row — that turn is where a miss hides."""

    import trajectory
    from wiki import match

    root = Path(tempfile.mkdtemp())
    wiki, project = root / "wiki", root / "project"
    (wiki / "craft").mkdir(parents=True)
    (wiki / "craft" / "big.md").write_text(RULE, encoding="utf-8")
    (project / ".wiki").mkdir(parents=True)
    env = dict(os.environ) | {
        "WIKI_ROOT": str(wiki), "PYTHONIOENCODING": "utf-8", "GEMINI_API_KEY": "",
        "TRANSLATE_ENV": str(root / "absent.env"), "TRANSLATE_CACHE": str(root / "cache.sqlite3"),
    }
    sent = []
    for prompt in (f"{WORD} 를 쓴다", "아무 규칙도 안 걸리는 말"):
        done = subprocess.run(
            [sys.executable, str(HERE / "inject.py"), "--project", str(project)],
            input=json.dumps({"prompt": prompt, "session_id": "t"}),
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        context = json.loads(done.stdout or "{}").get("hookSpecificOutput", {}).get("additionalContext", "")
        sent.append(len(context.encode("utf-8")))

    rows = trajectory.rows(project / ".wiki")
    assert [r["sent"] for r in rows] == sent and sent[0] > 0 and sent[1] == 0, (rows, sent)
    assert [name for name, _tag in rows[0]["full"]] == ["craft/big"], rows[0]
    assert rows[1]["full"] == [] and rows[1]["injected"] == [], rows[1]

    page = ("contract", "# T\n\nRule. x\n", Path("craft/t.md"))
    assert match.sent_whole([page], [match.whole(*page)]) == [["craft/t", match.tag(page[1])]]
    assert match.sent_whole([page], [match.shrink(page[1], page[2], "contract", True)]) == [], (
        "a page the budget trimmed did not go out whole"
    )


# ---- Deduplication within a session ----------------------------------------
#
# Run in-process with translation replaced by the identity — a failed
# translation hands the original back, so this is the translation-off path.

REPEAT = f"""---
scope: operator
severity: contract
repeat: rule
triggers: ["{WORD}"]
---

# Repeated page

Rule. The first binding sentence. The second binding sentence
wraps onto this line and must survive whole.

What goes wrong. {{why}}
"""

PLAIN = f"""---
scope: craft
severity: contract
triggers: ["{WORD}"]
---

# Plain page

Rule. Declares nothing, so it goes out in full every time.
"""

PARAGRAPH = "Rule. The first binding sentence. The second binding sentence\nwraps onto this line and must survive whole."


def stage(why: str = "short") -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "wiki" / "operator").mkdir(parents=True)
    (root / "wiki" / "craft").mkdir()
    (root / "wiki" / "operator" / "rep.md").write_text(REPEAT.format(why=why), encoding="utf-8")
    (root / "wiki" / "craft" / "plain.md").write_text(PLAIN, encoding="utf-8")
    (root / "project" / ".wiki").mkdir(parents=True)
    (root / "t1.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    (root / "t2.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    return root


def turn(root: Path, session: str = "s1", transcript: str | None = "t1.jsonl",
         project: bool = True, stdout=None, prompt: str = f"{WORD} 를 쓴다") -> str:
    """One hook call on Claude. Returns the injection, `""` when nothing went out."""

    import io

    import inject
    import translate
    from wiki import match

    payload = {"prompt": prompt, "session_id": session}
    if transcript:
        payload["transcript_path"] = str(root / transcript)
    was = match.WIKI, translate.translate, sys.stdin, sys.stdout, sys.argv
    match.WIKI = root / "wiki"
    translate.translate = lambda texts, direction=None, deadline=None: list(texts)
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
    sys.stdout = stdout or io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.argv = ["inject.py", "--host", "claude"] + (
        ["--project", str(root / "project")] if project else [])
    try:
        inject.main()
        sys.stdout.seek(0)
        text = sys.stdout.read()
    finally:
        match.WIKI, translate.translate, sys.stdin, sys.stdout, sys.argv = was
    return json.loads(text)["hookSpecificOutput"]["additionalContext"] if text else ""


def is_repeated(context: str) -> bool:
    return "operator/rep (contract, repeated)" in context


def test_the_second_turn_carries_the_rule_paragraph_whole_instead_of_the_page():
    root = stage()
    first, second = turn(root), turn(root)
    assert not is_repeated(first) and "What goes wrong. short" in first
    assert is_repeated(second), second
    assert PARAGRAPH in second, "the repeated form lost part of the rule paragraph"
    assert "What goes wrong. short" not in second
    assert "Loaded in full earlier this session: `operator/rep.md`" in second


def test_a_page_without_the_declaration_goes_out_in_full_every_turn():
    root = stage()
    for _ in range(3):
        context = turn(root)
    assert "Declares nothing, so it goes out in full every time." in context
    assert "craft/plain (contract, repeated)" not in context


def test_every_rule_already_seen_still_sends_each_rule_paragraph():
    root = stage()
    turn(root)
    context = turn(root)
    assert PARAGRAPH in context
    assert "Below is what the wiki loaded" in context, "the header must not go with the pages"


def test_a_turn_over_the_host_ceiling_sends_rule_paragraphs_and_sees_nothing():
    """Past the ceiling the host shows a 2 KB preview, so a full page is not
    read there. Declaring pages go as their paragraph; the rest stays whole;
    and nothing counts as seen, so the tail must not claim it was."""

    root = stage(why="x" * 12000)
    first = turn(root)
    assert "operator/rep (contract, rule only)" in first, first[:600]
    assert PARAGRAPH in first and "x" * 100 not in first
    assert "Declares nothing, so it goes out in full every time." in first
    assert "Loaded in full earlier" not in first
    assert not is_repeated(turn(root)), "a squeezed turn delivered no full text"

    # A row the hook before `--host` wrote: over the ceiling with the page
    # listed whole. The host filed that injection, so it was never read.
    from wiki import remembered

    assert remembered([{"sent": 20000, "full": [["operator/rep", "t"]]}], 9800) == set()
    assert remembered([{"sent": 9000, "full": [["operator/rep", "t"]]}], 9800) == {("operator/rep", "t")}


def test_a_turn_under_the_ceiling_is_not_squeezed():
    root = stage()
    assert "rule only" not in turn(root)


def test_the_rule_index_rides_only_on_a_turn_still_over_the_ceiling():
    """The index is for the 2 KB preview; under the ceiling there is none."""

    root = stage()
    assert "wiki:rule-index" not in turn(root)

    # A page that did not declare `repeat` cannot be squeezed, so this turn
    # stays over the ceiling and the preview needs the index first.
    (root / "wiki" / "craft" / "huge.md").write_text(
        PLAIN.replace("Plain page", "Huge page") + "y" * 12000 + "\n", encoding="utf-8")
    context = turn(root, session="s2", transcript="t2.jsonl")
    assert context.startswith("<!-- wiki:rule-index -->"), context[:200]
    assert "operator/rep (contract, rule only)" in context


def test_an_edited_page_goes_out_in_full_once_more():
    root = stage()
    turn(root)
    page = root / "wiki" / "operator" / "rep.md"
    page.write_text(REPEAT.format(why="edited mid-session"), encoding="utf-8")
    edited = turn(root)
    assert not is_repeated(edited) and "edited mid-session" in edited
    assert is_repeated(turn(root))


def test_turns_without_a_session_id_are_never_joined():
    root = stage()
    turn(root, session="")
    assert not is_repeated(turn(root, session=""))


def test_a_compact_in_one_session_resets_only_that_session():
    root = stage()
    for session, transcript in (("s1", "t1.jsonl"), ("s2", "t2.jsonl")) * 2:
        turn(root, session, transcript)
    with (root / "t1.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"type":"system","subtype":"compact_boundary"}\n')
    assert not is_repeated(turn(root, "s1", "t1.jsonl")), "s1 compacted and must reload"
    assert is_repeated(turn(root, "s2", "t2.jsonl")), "s2 did not compact"
    assert is_repeated(turn(root, "s1", "t1.jsonl")), "after the reload, s1 has seen it again"


def test_what_was_seen_before_a_compact_stays_forgotten_after_it():
    """The turn right after a compact may not match the page. The one after
    that reads every row of the session, and the rows from before the
    compact must not count — the reset row is where they stop."""

    root = stage()
    turn(root)
    with (root / "t1.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"type":"system","subtype":"compact_boundary"}\n')
    turn(root, prompt="아무 규칙도 안 걸리는 말")
    assert not is_repeated(turn(root)), "the compact wiped the page from context"


def test_a_codex_compact_resets_too():
    """The copy's review round 1: the transcript tail was read once per
    marker, so the second marker — Codex's — only ever saw empty bytes. A
    test that wrote Claude's marker alone could not see it."""

    root = stage()
    turn(root)
    with (root / "t1.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"timestamp":"2026-09-25T00:00:00Z","type":"compacted","payload":{}}\n')
    assert not is_repeated(turn(root)), "Codex compacted and must reload"


def test_a_compact_word_inside_a_message_is_not_a_compact():
    root = stage()
    turn(root)
    with (root / "t1.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "text": '"subtype":"compact_boundary"'}) + "\n")
    assert is_repeated(turn(root))


def test_a_new_transcript_path_or_a_shrunk_transcript_resets():
    root = stage()
    turn(root)
    assert not is_repeated(turn(root, transcript="t2.jsonl")), "the path changed"
    assert is_repeated(turn(root, transcript="t2.jsonl"))
    (root / "t2.jsonl").write_text("", encoding="utf-8")
    assert not is_repeated(turn(root, transcript="t2.jsonl")), "the transcript shrank"


def test_a_turn_that_died_writing_its_output_is_not_recorded_as_seen():
    import io

    class Dead(io.TextIOWrapper):
        def write(self, _text):
            raise OSError("pipe closed")

    root = stage()
    try:
        turn(root, stdout=Dead(io.BytesIO(), encoding="utf-8"))
    except OSError:
        pass
    assert not (root / "project" / ".wiki" / "trajectory.jsonl").exists()
    assert not is_repeated(turn(root)), "the failed turn delivered nothing"


def test_no_project_no_transcript_or_a_broken_row_sends_everything():
    root = stage()
    turn(root, project=False)
    assert not is_repeated(turn(root, project=False))

    root = stage()
    turn(root, transcript=None)
    assert not is_repeated(turn(root, transcript=None))

    root = stage()
    turn(root)
    with (root / "project" / ".wiki" / "trajectory.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"session": "s1", "full": [["operator/rep"\n')
    assert not is_repeated(turn(root))


def test_a_korean_repo_page_repeats_its_korean_rule_paragraph():
    root = stage()
    (root / "project" / ".wiki" / "gate.md").write_text(
        f'---\nseverity: contract\nrepeat: rule\ntriggers: ["{WORD}"]\n---\n\n'
        "# 게이트\n\n규칙. 머지 전에 게이트를 돌린다. 둘째 문장도\n문단 안이다.\n\n왜. 길다.\n",
        encoding="utf-8",
    )
    turn(root)
    second = turn(root)
    assert ".wiki/gate (contract, repeated)" in second
    assert "규칙. 머지 전에 게이트를 돌린다. 둘째 문장도\n문단 안이다." in second
    assert "왜. 길다." not in second


def test_the_recall_invariant_holds_over_the_fixed_sample():
    """The pytest half of the gate: the same judgement `replay` makes, over a
    committed sample of utterances, against today's pages.

    Transcripts are not looked up — the sample's sessions exist on no machine.
    """

    import contextlib
    import io

    import trigger_audit

    sample = HERE / "fixtures" / "recall-trajectory.jsonl"
    was = trigger_audit.transcripts
    trigger_audit.transcripts = lambda home=None: {}
    report = io.StringIO()
    try:
        with contextlib.redirect_stdout(report):
            code = trigger_audit.replay([str(sample), "--project", str(HERE.parent)])
    finally:
        trigger_audit.transcripts = was
    assert code == 0, report.getvalue()[-2000:]
    assert "빠진 곳 없음" in report.getvalue()
    assert "operator/codex-review-loop" in report.getvalue(), "the declarations were not read"


def test_the_invariant_goes_red_when_a_clause_is_missing():
    from trigger_audit import recall_misses
    from wiki import tag

    body = PARAGRAPH + "\n\nWhy. more\n"
    matched = [("contract", body, Path("operator") / "rep.md")]
    seen = {("operator/rep", tag(body))}
    assert recall_misses(matched, seen, {"operator/rep"}, PARAGRAPH) == []
    assert recall_misses(matched, seen, {"operator/rep"}, PARAGRAPH.splitlines()[0]) == ["operator/rep"]
    assert recall_misses(matched, set(), {"operator/rep"}, PARAGRAPH) == ["operator/rep"], (
        "unseen, the whole body is owed"
    )
    assert recall_misses(matched, set(), {"operator/rep"}, PARAGRAPH, squeezed=True) == []
    assert recall_misses(matched, set(), set(), PARAGRAPH, squeezed=True) == ["operator/rep"], (
        "a page that did not declare it is owed in full even on a squeezed turn"
    )


def test_a_repeat_declaration_owes_a_short_rule_paragraph():
    """Lint's half: a declaring page with no paragraph, or one past
    `REPEAT_MAX`, would repeat nothing or repeat too much."""

    from wiki.wikilib import REPEAT_MAX, repeat_errors

    assert repeat_errors({}, "no rule") == []
    assert repeat_errors({"repeat": "rule"}, "# T\n\nRule. Short.\n") == []
    assert repeat_errors({"repeat": "all"}, "Rule. x\n")
    assert repeat_errors({"repeat": "rule"}, "# T\n\nWhy. no rule here\n")
    assert repeat_errors({"repeat": "rule"}, "Rule. " + "x" * REPEAT_MAX + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
