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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
