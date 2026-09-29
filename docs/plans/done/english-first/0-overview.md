# English First + Korean Overlay — Overall Design

This document binds the three plans in this folder. Step-by-step details are in `1-engine.md`,
`2-mirror.md`, and `3-comments.md`.

## What and Why to Change

Problem. Wiki pages, hook prompts, and progress reports are all in Korean, so the agent's intelligence drops while thinking and outputting in Korean. This was observed in both hooks and wiki.

Solution. Write everything the agent touches (documents, prompts, progress reports, code comments) in English, and place a Gemini translation overlay on top so humans can read and write in Korean.

Absolute Condition. Meaning must not leak during translation. This applies both when moving Korean originals to English and when returning English outputs to Korean. Legal terms and proper nouns remain in Korean.

## Four Paths

| Path | Where | Direction | Step |
| --- | --- | --- | --- |
| User Input | `tool/inject.py` (UserPromptSubmit) | ko→en | 1 |
| Session Start Context | `tool/session_state.py::report()` | ko→en | 1 |
| Terminal Output | `tool/mirror.py` → Orca second cell | en→ko | 2 |
| Web Chat | `tool/chat.py` + `web/src` | Bidirectional | Document/map display 2, conversation 3 |

## The Single Rule for Ordering

**Do not switch the user screen to English before the path for reading in Korean is established.**

This defines the step boundaries. `english_progress.py` (blocking Korean in progress explanations outside protected zones) is turned on after the mirror verification of both hosts in step 2, not step 1. If turned on in step 1, the user would see only English without a mirror, which exactly reverses the purpose of this work.

The `statusMessage` of `settings.json` and the user-facing blocking/error messages of hooks remain in Korean. This plan does not include English localization of the status UI.

Transitions are verified per host. You cannot switch to Codex with a mirror that only reads Claude logs. Do not flip common progress rules to English before verifying the actual output path of Codex and verifying the Korean mirror. Web chat output maintains Korean until step 3 overlay verification. User UI that logs cannot capture, such as question tool options, hook status, and errors, remains in Korean. This exception is also specified in the English progress rules and `ask-with-arrow-key-options`.

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Translation Engine and Input Path | `translate.py`, glossary, `--check`, `inject.py` ko→en, `session_state` translation, English localization of agent-only prompts | Complete |
| 2 | Mirror and Wiki Prose | Both host mirrors, Korean display of documents/maps, flipping enforcement, English localization of 32 pages, lint replacement | Complete |
| 3 | Code Comments and Web UI | `tool/*.py` comments, web chat bidirectional overlay | Complete |

## What Remains in Korean Until the End — Touching Breaks It

| What | Why |
| --- | --- |
| `triggers` regex in page front matter | Matches user utterance. Utterance remains in Korean. Changing to English kills the entire injection |
| `tool/markers/ko.toml` | `census` and `harvest` are markers that categorize user utterances. Same reason |
| `[가-힣]{2,}` word extraction in `census.py:277` | Extracts words from utterance |
| Commit messages, PR bodies, `.wiki/decisions/` | Humans read these on GitHub. Translate only when assembling into agent context from `session_state.report` and `inject` |
| Legal terms and proper nouns in glossary `keep_korean` | Translating them makes them point to something else |

## Responsibility for Prior Verification and Exit Conditions

The implementer is one agent in this session. The mirror, web display, status, gate, and check “person in charge” in the four plans are all names of steps performed sequentially by this agent, and do not imply separate personnel or delegation. Perform implementation and the verifications below directly and record the results in the step logs. The user is only responsible for opening manual mirror cells and verifying specified meanings. Unverified conditions are not considered passed.

| When / In Charge | Verification | When failed or unverifiable |
| --- | --- | --- |
| Step 1 implementation lead | Both host input injection, no keys, total time budget | Keep original injection without activating translation wiring. Record failed items and reproduction methods, and hold that row |
| Step 2 mirror lead, first task | Collection of actual output from Claude/Codex and session identification | Step 2 can proceed up to investigation/implementation. If collection is impossible, hold mirror-live and keep common rules/hooks in Korean. Re-verify after securing support path |
| Step 2 web display lead, before pages | Korean display of English documents in citation drawers/maps | Hold distribution of English-localized pages. Implement minimum display functionality at this step without waiting for step 3 outputs |
| Step 2 implementation lead → user | Comparison before/after identical census, difference between back-translation and rewriting of 3 pages | Hold gate if there are no input/verification results. Do not mark as distributed/complete before user confirmation |
| Step 3 web lead | Both host conversation, toggle, translation failure, handover of original text | Hold English conversion of answers and maintain existing Korean output. Comment work and UI implementation can continue |

Even if the step 2 mirror is blocked, the step 3 conversation UI can be developed first with the step 1 engine and fake English answers. In that case, step 2 is on hold, not complete. Marking an unsupported host as supported or shipping common English enforcement without a mirror is not used as an exit.

Even while on hold, complete implementations/verifications that do not depend on it. If those are finished but the host collection path or user verification is not secured, report the entire task as finished with `미완료 — 외부 조건으로 차단됨`. Leave the corresponding step row as on hold, and record the confirmed failure evidence together with the conditions required for resumption. Do not repeatedly wait/retry under the same conditions or change to overall complete. Once the collection path is secured, the same agent re-verifies both host mirror-live, and when user verification arrives, re-adjudicate the corresponding gate. The hold is lifted only when those conditions are met. Do not reduce scope or cancel without the user's separate decision.

## How Plans in This Folder Are Loaded into SessionStart

`session_state.plans()` scans `docs/plans/*.md` and loads incomplete rows from the `## 단계` table. Up to 2 files in reverse alphabetical order — if all three are incomplete, only 3 and 2 are loaded, and 1 is not. When a step is finished, change the status cell of that file to `완료`. Files without incomplete rows are omitted.

Historical, as this plan ran. `session_state.plans()` now scans `docs/plans/`
recursively, skips `done/`, reads plan folders in reverse name order and the
lowest number first within one, and still shows two plans at most. A plan hidden by
that cap is still open; the newest overview links to it.
