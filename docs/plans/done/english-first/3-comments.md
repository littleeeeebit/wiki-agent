# Phase 3 — Code Comments and Web UI

The overall design and the relationship between the three phases are in [Overview](0-overview.md)].

Goal. The comments and docstrings of `tool/*.py` become English, and a Korean overlay is added to the web chat.

Prerequisites. The default order is after Phase 2 is complete. If the Phase 2 mirror is deferred, comment and chat UI development can proceed first with the Phase 1 translation engine and fake English responses. Document/map display and translation APIs are the responsibility of Phase 2 and are not redundantly implemented in this phase. Actual English output activation is done only on paths where the necessary amount of host injection and web overlay verification is complete.

## To be fixed — Code comments

`tool/*.py` 8,699 lines. The rules of `craft/comments-carry-why` apply as is —
Comments provide reasons and do not provide history. Do not violate those rules while translating.

### Places that are rewrites, not translations

These comments have the fact that they are in Korean as part of their content. If moved, they lose their meaning.

| Place | What it says now | How |
| --- | --- | --- |
| `hooks-fail-open` series comments — "If Korean is mixed in the message, that stderr write dies again" | cp949 stderr dies because of Korean | Write in English but keep the reason: non-ASCII in the message kills the stderr write under a cp949 console |
| All comments in `korean_progress.py` | Reason for Korean enforcement | Already rewritten because the rules were reversed in Phase 2 |
| Comments in `ko.toml` | How to maintain the Korean label list | Leave in Korean. The content of this file is Korean, and those who maintain it read Korean labels |
| Comment next to `[가-힣]{2,}` in `census.py:277` | Korean word extraction | Write in English but keep why the Korean class is needed |
| `harvest.py:147` "Because Korean labels have no word boundaries" | Korean label matching | Write in English but explain that inclusion checks are used because labels with compound words/particles are not caught by regex word boundaries alone. Avoid the generalization that Korean itself has no word boundaries |
| Korean test function names like `test_harvest.py::test_한글_표지는_...` | | See below |
| `"한글 project"` path in `test_codex_hooks.py`, `"adapter 한글 "` in `test_local_adapter.py` | Data to reproduce when there is Korean in the path | Leave as is. This is test input |

### Korean test function names

There are names like `test_harvest.py::test_한글_표지는_아직_합성어_안에서도_걸린다`.
Change them to English. However, transcribe the regression cause that the test protects into a comment —
This is the place where `comments-carry-why` nailed down that "test comments judge the opposite".
The context that the name held must not disappear with the name.

### Order

Because there are many files, review is impossible if done all at once. Submit in bundles.

| Bundle | Files |
| --- | --- |
| hook | `inject.py` `session_state.py` `sync.py` `declared_continuation.py` `edit_as_diff.py` `english_progress.py` `codex_pretool.py` `hook_diagnostics.py` |
| check | `lint.py` `repo_lint.py` `apply.py` `setup_agents.py` `setup_chat.py` |
| analysis | `census.py` `harvest.py` `corpus.py` `graph.py` `repo_graph.py` `intersect.py` `transcript.py` `trajectory.py` `trigger_audit.py` `wikilib.py` `declared_continuation.py` |
| chat | `chat.py` `chat_channels.py` `chat_local.py` `chat_post.py` `chat_session.py` `slack_brief.py` `translate.py` `mirror.py` |
| test | `test_*.py` all |

## To be fixed — Web chat overlay

### `tool/chat.py`

- Reuse `POST /api/translate` from Phase 2 for chat display as well. Do not create new translation logic
- The responsibility for input translation lies with `inject.py` of Phase 1 `UserPromptSubmit`. `chat.py` delivers the Korean original text to the host as is and does not pre-translate. The web manager verifies that hook injection also runs in the actual chat sessions of both hosts. If it does not run, defer input switching for that host and repair the wiring. Do not bypass by adding direct double translation
- `raw/chat/progress.jsonl` is currently stacked in Korean. From now on, English will be stacked.
  Do not touch existing lines. Let the reading side judge by whether it is Korean or not

### `web/src`

- `한국어/English` toggle in the toolbar. Default is Korean
- `Stream.tsx`·`Answer.tsx` run `/api/translate` just before render. Cache the response
- If the toggle is off, do not call it at all
- Translation failure shows the original text. English is better than a blank screen
- **Utterances typed by a person are not translated in the overlay.** Do not judge by language, judge by speaker — users type both Korean and English. Input translation is what 1st phase `inject.py` gives to the agent, and the overlay is a place for the user to check "how my words were delivered". If the sentence they wrote comes back re-translated, that check becomes meaningless. The mirror already does this with the `SELF` label (`test_the_persons_own_words_are_never_translated_in_either_language` of `tool/test_mirror.py`), and web chat/`Handoff.tsx` follow the same rule

After verifying the web overlay, switch the Korean response instructions of `chat-answer.md` to English.
Just re-translating a prompt that is already in English does not change the output language.
The easy Korean explanation of `chat-explain.md` is a different function from translation, so keep it and do not translate redundantly. The review preamble of `chat_channels.py` has separate Korean result instructions, so change them to English together. The `retro-candidates` fence of retro is data that uses Korean categories and fixed formats, so preserve it and check for UI parsing regressions. Continue to maintain the Korean display of documents/maps from Phase 2. Do not re-translate the entire partial answer for every render, but translate completed segments, and ensure that even if a previous request finishes late, it does not overwrite the new answer/new toggle state. Verify this behavior with fake translation responses.

Preserve the input original text until trigger matching of `remember`·`hits_for`·host hook.
The recent chat shown by `Handoff.tsx` will also contain English logs, so include it in the display overlay.
Distinguish between the original prompt to copy and the Korean preview, and do not overwrite the original log with the translation.

## Things not to touch

Labels and comments in `tool/markers/ko.toml` · Korean input data in tests ·
`triggers` in page front matter · existing logs in `raw/` · existing commits and decision records

## Phases

| # | Name | What | Status |
| --- | --- | --- | --- |
| 1 | hooks | Englishize hook bundle comments | Complete — lines with Korean 285 → 83 |
| 2 | checks | Englishize check bundle comments | Complete — 320 → 172 |
| 3 | analysis | Englishize analysis bundle comments | Complete — 361 → 180 |
| 4 | chat-tools | Englishize chat bundle comments | Complete — 315 → 178. `translate`·`mirror` already English in Phase 2 |
| 5 | tests | Englishize test comments/function names, preserve regression causes | Complete — Korean function names 65 → 0, collection count 244 maintained |
| 6 | input | Reuse existing translation API · measure inject input translation on both web hosts | Complete — measurement caused a defect. See below |
| 7 | web | Chat/handover toggle/overlay · switch output instructions per answer and channel | Complete — `useOverlay`, toggle, `chat-answer.md` English switch |
| 8 | gate | `pytest tool/` + `lint --check` + web build | Complete — 245 passed, 7 direct executions, tsc·lint·build |

All remaining Korean is intentional. Strings read by humans (rejection messages · `systemMessage` ·
argparse help · screen labels · Slack body), data matched by parsers (`## 단계` ·
`완료` · `왜.` · Korean promise patterns), test inputs, and Korean examples cited within English comments.

## What 6 steps produced — Injection order

Went to measure and found a defect. Sent Korean to both hosts in web chat and opened the session
log, and found that the rule bundle was included but **there was no English version of the utterance.** No one told me that.

Hosts extract to a file if injection exceeds about 12KB and only give a 2KB preview to the session.
Just the rules for a normal turn are 12,205 characters. Since the English version was attached at the very end, the part that got cut off was always that. Moved the English version to the very front — it is hundreds of characters long, and it is the very block the user comes to check "how my words were delivered".

| | Before fix | After fix |
| --- | --- | --- |
| Claude web session | Rules O · English version X | Both O |
| Codex web session | Rules O · English version X | Both O |

## 7 steps measurement — `#위키` channel

| What is measured | Result |
| --- | --- |
| Toggle default | Korean |
| If turned off with English | 0 `/api/translate` requests |
| One completed answer | 1 request. It was 3 when `!m.pending` was absent |
| Question typed by user in English | As is. Judge by speaker, not language |
| English answer | Appeared in Korean |

## Verification

- `pytest tool/` all green — since function names were changed, count if the collection count is the same as before
- Also run the 7 direct execution checks of `docs/development.md`: `test_lint.py`,
  `test_apply.py`, `test_inject.py`, `test_declared_continuation.py`, `test_repo_lint.py`,
  `test_slack_brief.py`, `test_trajectory.py`, each executed with `python tool/<파일>`.
  Do not say that main-based checks were verified just by preserving pytest collection counts
- `python tool/lint.py --check` — especially whether the replaced `broken_wraps` gives false positives against English comments
- `cd web && npm run build` green
- Measurement: Verify Korean input/English injection on both hosts, and verify Korean display of accurate answers and English original text toggle. Easy explanation indicates it is a Korean function regardless of the toggle. Also verify review channel, retro candidate buttons, handover preview, and original text copy.