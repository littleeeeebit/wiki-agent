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
from wiki import (
    LIMIT, REPO_BUDGET, RULE_BUDGET, budget, compose, label, match_pages, pages,
    remembered, repeatable, sent_whole, tag,
)

HANGUL = re.compile(r"[가-힣]")

# What a compact leaves in each host's transcript: Claude's `system` row with
# this subtype, Codex's rollout line of this type. Matched as bytes, compact
# JSON as both hosts write it, so a message that merely says "compact" does
# not count.
COMPACTED = (b'"subtype":"compact_boundary"', b'"type":"compacted"')

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


def compacted(previous: dict | None, transcript: Path, txp: str, size: int) -> bool:
    """Did the transcript compact since this session's previous turn?

    Only what was appended since then is read, so a transcript of tens of MB
    costs what one turn added. A different path, a transcript that shrank or
    a previous row with no offset all count as a compact: `/clear` and
    `--resume` land here, and so does anything this cannot explain.

    The previous row is this session's, not the file's last line. Two
    sessions write one trajectory in turns, and another session's larger
    offset would skip this one's compact — `trajectory.last_row` does not
    tell sessions apart, so it is not used here.
    """

    if previous is None:
        return False
    start = previous.get("tx")
    if previous.get("txp") != txp or not isinstance(start, int) or size < start:
        return True
    with transcript.open("rb") as handle:
        handle.seek(start)
        # Read once. Reading inside the loop hands every marker after the
        # first an exhausted stream, and Codex's compact is never seen.
        tail = handle.read()
    return any(marker in tail for marker in COMPACTED)


def recall(wiki: Path | None, session: str, transcript, host: str | None) -> tuple[set, dict]:
    """`(seen, fields)` — what this session already holds, and what to record.

    Anything missing or failing means nothing is seen and every page goes
    out in full, as it did before this existed. Wrong in that direction
    costs tokens. The other direction — counting as seen what was never read
    — is the failure this wiki exists to prevent, `craft/hooks-fail-open`.
    """

    limit = LIMIT.get(host or "")
    if wiki is None or not session or not transcript or limit is None:
        return set(), {}
    try:
        path = Path(str(transcript))
        size = path.stat().st_size
        # The path's tag, not the path: it holds a user name and a project.
        txp = tag(str(path))
        mine = trajectory.session_rows(trajectory.path_for(wiki), session)
        if compacted(mine[-1] if mine else None, path, txp, size):
            return set(), {"tx": size, "txp": txp, "reset": True}
        return remembered(mine, limit), {"tx": size, "txp": txp}
    except Exception:  # noqa: BLE001
        return set(), {}


def main() -> int:
    # The utterance coming in and the injection going out are both Korean. The
    # encoding is not left to the environment.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="발화에 맞는 위키 페이지를 넣는다")
    parser.add_argument("--adapter", default=None, help="adapters/<이름>.toml")
    parser.add_argument("--project", default=None, help="대상 저장소. `.wiki/` 를 읽는다")
    parser.add_argument("--host", default=None,
                        help="claude|codex. 없으면 세션 내 중복 제거를 안 한다")
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
    available = pages(args.adapter, args.project)
    matched = match_pages(prompt, available)
    english = rendering(prompt, deadline)
    matched = localised(matched, deadline)
    # Unlike reading, writing has to work before `.wiki/` exists.
    # `project_wiki` returns `None` when it does not, which would leave a
    # freshly attached repository silently recording nothing at all.
    wiki = Path(args.project).expanduser() / ".wiki" if args.project else None
    session = str(payload.get("session_id") or "")
    seen, where = recall(wiki, session, payload.get("transcript_path"), args.host)
    # Without a host the ceiling is unknown, so nothing is squeezed either.
    rules, decisions, rule_parts, repo_parts, trimmed, body, _squeezed = compose(
        matched,
        (budget(args.adapter, RULE_BUDGET, args.project),
         budget(args.adapter, REPO_BUDGET, args.project)),
        english, args.project, seen, repeatable(available), LIMIT.get(args.host or ""),
    )
    parts = rule_parts + repo_parts

    # Repository documents are not selected here. The whole listing goes in
    # once at session start and the choosing is done by whoever already holds
    # it — `tool/session_state.py`.

    loaded = [label(p) for _s, _b, p in rules + decisions]
    if body:
        # This one line lands on the person's screen as written. The rule
        # inverted and this stayed Korean, because the reader here is the
        # person. `operator/english-progress` holds that boundary.
        note = f"위키 주입: {', '.join(loaded[:6])}" if loaded else "위키: 걸린 규칙 없음"
        heads = [part.split("\n", 1)[0] for part in rule_parts]
        again = sum(", repeated) -->" in head for head in heads)
        squeezed = sum(", rule only) -->" in head for head in heads)
        if again:
            note += f" · 이미 실림 {again}장"
        if squeezed:
            note += f" · 한도로 규칙 문단만 {squeezed}장"
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
        sys.stdout.flush()

    # Recorded last, after the output is out. `sent` is only known once the
    # body is, and a row written before the output would keep `full` for a
    # turn that died writing stdout — pages the session never received.
    #
    # A turn that matched nothing is recorded too. What was not carried is as
    # much evidence about routing as what was, and reading only the utterances
    # that matched nothing is the one way to find a miss.
    failed = trajectory.record(
        wiki,
        prompt,
        loaded,
        sum(len(part) for part in parts),
        session,
        # The whole `additionalContext` in UTF-8 bytes — index, rendering,
        # source map and separators included. `cost` counts only the rule and
        # decision blocks; this is the number a host ceiling (`wiki.LIMIT`)
        # is compared with.
        sent=len(body.encode("utf-8")),
        # `[name, tag]` of each rule page that went out whole, not trimmed.
        # The next turn of this session reads it back as seen (`recall`).
        full=sent_whole(rules, rule_parts),
        # `tx` the transcript's size now, `txp` its path's tag, `reset` when
        # it compacted since the last turn — `compacted` reads them next turn.
        **where,
    )
    if failed:
        # The name and nothing else. Non-ASCII in the message kills this very
        # stderr write under a cp949 console, which is how the report of a
        # failure became a second failure.
        print(f"trajectory skipped: {failed}", file=sys.stderr)
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

