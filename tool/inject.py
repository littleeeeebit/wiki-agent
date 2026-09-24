"""UserPromptSubmit hook — read the utterance, put the matching page in context.

What this injects is agent input, so it goes out in English. The pages and
decision records it reads are Korean, and the translation happens on the way
out rather than in the files.
"""

from __future__ import annotations

# First import of the entry point: it keeps the stack from before whatever
# time limit kills this.
import hook_diagnostics  # noqa: F401
import argparse
import json
import re
import sys
import time
from pathlib import Path

import trajectory
import translate
from wiki.match import (
    REPO_BUDGET, RULE_BUDGET, budget, label, match_pages, pages, render_parts,
    rule_index, source_map,
)

HANGUL = re.compile(r"[가-힣]")

# The budget for the whole translation, kept under the hook's own 15 seconds.
# Going over does not cost the translation, it costs the entire injection.
# Whatever is not done by then goes out as the Korean original.
BUDGET = 8.0

# The length past which no English rendering of the utterance is attached.
# Unlike the rule budget, this block cannot be trimmed: a cut translation
# reads exactly like a whole one. So it is a threshold, not a ceiling.
MAX_RENDERED = 4000


def localised(matched: list, deadline: float) -> list:
    """Translate the repo's own pages before anything is measured.

    Only `.wiki/` pages. The hub's `operator/` and `craft/` prose is rewritten
    in English at the source in stage 2, so translating it here would pay for
    the same words twice and throw the second copy away.

    Runs before `render_parts`, which is the part that matters. Translating
    afterwards saved a few tokens on pages the budget would have shortened, and
    cost correctness everywhere else: `fit` had already trimmed to the Korean
    length, and English is usually longer, so a block could come back over the
    budget it was just fitted to — and `trajectory.cost` recorded the number
    from before, which `trigger_audit` reads as the size of what was injected.
    """

    mine = [i for i, (_s, _b, path) in enumerate(matched)
            if str(label(path)).startswith(".wiki/")]
    if not mine:
        return matched
    done = translate.translate(
        [matched[i][1] for i in mine], translate.KO_EN, deadline
    )
    out = list(matched)
    for body, i in zip(done, mine):
        severity, _was, path = out[i]
        out[i] = (severity, body, path)
    return out


def rendering(prompt: str, deadline: float) -> str:
    """The English rendering of the utterance, or `""` if there is none.

    **This must run after `match_pages`, never before.** Triggers are Korean
    regexes held against what the person actually typed. Hand `match_pages` a
    translation and nothing matches, and nothing matching is indistinguishable
    from nothing applying — the injection disappears without a word.

    `craft/hooks-fail-open` names that shape: not running is bad, believing it
    ran is worse.
    """

    if not HANGUL.search(prompt) or len(prompt) > MAX_RENDERED:
        # A long utterance is usually pasted material, and the rendering would
        # double it in a context that already holds the original. Skipping
        # beats truncating: half a translation reads as a whole one.
        return ""
    english = translate.translate([prompt], translate.KO_EN, deadline)[0]
    if english == prompt:
        # Unchanged means the translation failed. Labelling the Korean as an
        # English rendering would be a lie the reader cannot check.
        return ""
    return (
        "<!-- wiki:english-rendering -->\n"
        "English rendering of the user's message (Gemini). The Korean above is "
        "authoritative — go back to it wherever this reads oddly.\n\n" + english
    )


def main() -> int:
    # The utterance coming in and the injection going out are both Korean. The
    # encoding is not left to the environment.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="발화에 맞는 위키 페이지를 넣는다")
    parser.add_argument("--adapter", default=None, help="adapters/<이름>.toml")
    parser.add_argument("--project", default=None, help="대상 저장소. `.wiki/` 를 읽는다")
    args = parser.parse_args()

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
    if not prompt:
        return 0

    # Triggers are matched on the Korean the person typed, then the bodies are
    # translated, then everything downstream measures the English that will
    # actually go out. One deadline covers this and the utterance rendering,
    # so the hook's budget bounds the pair rather than each separately.
    #
    # The utterance is translated before the pages. It is a few hundred
    # characters; a repository page can be 28,000 — ai-nara-shop's
    # `plan-active` on 2026-09-23 held the request past the whole deadline,
    # and the rendering behind it got zero seconds and was dropped. The block
    # the person checks goes first; the pages take what is left.
    deadline = time.monotonic() + BUDGET
    matched = match_pages(prompt, pages(args.adapter, args.project))
    english = rendering(prompt, deadline)
    matched = localised(matched, deadline)
    rules, decisions, rule_parts, repo_parts, trimmed = render_parts(
        matched, budget(args.adapter, RULE_BUDGET, args.project),
        budget(args.adapter, REPO_BUDGET, args.project),
    )
    parts = rule_parts + repo_parts

    # Repository documents are not selected here. The whole listing goes in
    # once at session start and the choosing is done by whoever already holds
    # it — `tool/session_state.py`.

    loaded = [label(p) for _s, _b, p in rules + decisions]

    # A turn that matched nothing is recorded too. What was not carried is as
    # much evidence about routing as what was, and reading only the utterances
    # that matched nothing is the one way to find a miss.
    #
    # Unlike reading, writing has to work before `.wiki/` exists.
    # `project_wiki` returns `None` when it does not, which would leave a
    # freshly attached repository silently recording nothing at all.
    failed = trajectory.record(
        Path(args.project).expanduser() / ".wiki" if args.project else None,
        prompt,
        loaded,
        sum(len(part) for part in parts),
        str(payload.get("session_id") or ""),
    )
    if failed:
        # The name and nothing else. Non-ASCII in the message kills this very
        # stderr write under a cp949 console, which is how the report of a
        # failure became a second failure.
        print(f"trajectory skipped: {failed}", file=sys.stderr)

    if not parts and not english:
        return 0

    # The rule index goes first. It is a few hundred characters, and the
    # rendering in front of it could reach 4,000 (`MAX_RENDERED`) and push
    # every rule sentence out of the 2 KB preview. See `rule_index`.
    blocks = []
    index = rule_index(rules)
    if index:
        blocks.append(index)
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
    if parts:
        blocks.append(
            "Below is what the wiki loaded for this utterance. A rule marks a "
            "place where something actually went wrong before; knowledge is "
            "something already decided.\n\n"
            + source_map(rules, args.project)
            + "\n\n"
            + "\n\n---\n\n".join(parts)
        )
    body = "\n\n---\n\n".join(blocks)
    # This one line lands on the person's screen as written. The rule inverted
    # and this stayed Korean, because the reader here is the person.
    # `operator/english-progress` holds that boundary.
    note = f"위키 주입: {', '.join(loaded[:6])}" if loaded else "위키: 걸린 규칙 없음"
    if trimmed:
        note += f" · 줄임 {trimmed}장"
    if english:
        note += " · 영어본 첨부"

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": body,
            },
            "systemMessage": note,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    # Whatever happens, a hook does not stop the session. Leave the name of
    # what went wrong and pass.
    try:
        _code = main()
    except Exception as _error:  # noqa: BLE001
        print(f"hook skipped: {type(_error).__name__}", file=sys.stderr)
        _code = 0
    raise SystemExit(_code)

