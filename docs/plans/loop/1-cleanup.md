# Phase 1 — Resolving Discrepancies

The relationship between the overall design and the phases is in [Overview](0-overview.md)].

Goal. Eliminate places where the plan document, comments, and code say different things. When a later phase writes a new plan document, SessionStart shows the remaining lines to the agent. If that judgment is incorrect, the next task focus (Phase 3) selects candidates from where it was wrong.

On 2026-09-25, 11 plan documents and `docs/verification.md`·`docs/quality.md` were thoroughly examined and cross-checked with the code.

## To Fix

### Plan Status Judgment

`session_state.open_steps` considers a line finished only when the status column is exactly `완료`·`취소` (`tool/session_state.py:98`). Lines with an extra line appended, like `완료 — 양 호스트 실측`, are counted as remaining work. On 2026-09-25, line 3 of english-first-2 and line 8 of english-first-3 appeared in SessionStart that way. That series is no longer read as it is under `done/`, but the same shape of status column is used in future plans.

Fix. Judge by the first word of the status column. If it starts with `완료`·`취소`, it is a finished line. `미완료` is not finished — this is why it must be a word comparison, not a prefix comparison.

Test. Provide a table of four lines: `완료 — …`, `취소 — …`, `미완료 — 외부 조건으로 차단됨`, `미착수`, and check if only the last two remain. It must be red in the code before the fix.

### Outdated Documents

| Where | Discrepancy | Fix |
| --- | --- | --- |
| `docs/verification.md` | It is exactly the record of the public copy. "Five channels", Slack executor appear. There is no verification for wiki-agent phases 2–7 | State in the title and the first section that this file is a record from the public copy era, and that current verification points to `docs/plans/done/wiki-agent/7-verify.md`. Do not delete old records |
| "Things not included" in `docs/plans/done/wiki-agent/0-overview.md` | Review cell remains as "Later" | Point to `loop` Phase 4 |

### Outdated Comments and Unused Names

Slack, mirror, and `chat.handoff` were deleted in Phase 6, but comments still cite them as reasons.

| Where | Fix |
| --- | --- |
| `tool/session_state.py:53` | Instead of `chat.handoff`, cite the place that currently calls this function (`/api/draft`) as the reason |
| `tool/workspace/sessions.py:207` | Remove "Slack brief" |
| `tool/test_sessions.py:188,216` | Cite the ownership rule that protects it, not the mirror, as the reason |

`claude_session`·`codex_session`·`FINDERS` had the mirror as the sole caller. The current calling place is only the test. `sessions.py:7-8` notes that it is kept "because the test pins the ownership rule". If that rule belongs to `checkouts()`, move the test to `checkouts()` and delete the three. If there is a rule that cannot be moved, keep it and write the rule name as the reason. Decide which is the case by reading the test.

### Small Postponements

| What | Where Postponed | Fix |
| --- | --- | --- |
| `wiki-agent` is translated as `위키-에이전트` | Phase 7 | Put it in `fixed` of `glossary.toml`. The cache is invalidated once. Phase 7 said "when fixing for other reasons", but there is no other reason to wait |
| No project mark in queries sent before the first channel list arrives | Phase 6 Review 11 | If `claimed` is empty, do not send requests that are not `/api/channels` and wait (`web/src/lib/api.ts:96`). Also, reject requests that use the server if `X-Project` is missing (`tool/main/app.py:86`) |
| Translation request prepayment deduction is not the output token limit | Phase 3 PR #4 | Apply `generationConfig.maxOutputTokens` proportional to the input length (`tool/translate/__init__.py:371`). The deducted amount becomes the actual limit |
| haiku ignores the English instruction in `chat-answer.md` and answers in Korean | Phase 7 | Look at the cause first. Check if it conflicts with the language instruction of user unit `CLAUDE.md`, or if it is a location issue of `--append-system-prompt`. If the cause is outside this repository, note it and do not fix it |

Terminal changes appearing late in the work tree list (Phase 7) is passed to Phase 6. As the rail changes to work units, the time to re-read the list changes as well.

## Things Not Doing

- Keeping text inside quotes in translation. Leave as decided in Phase 7
- Including severity and body in answer event `hits`. Phase 4 document said "when necessary" and there is no place where it is needed yet
- Account selection screen. Leave as decided in Phase 5

## Verification

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- `session_state.plans()` does not read the document of `done/`, and outputs lines 2–7 of `loop/0-overview.md`

## Phases

| # | Phase | What | Status |
| --- | --- | --- | --- |
| 1 | Judgment | `open_steps` first word judgment and test | Done — confirmed red before fix |
| 2 | Document | `verification.md`, "Things not included" in wiki-agent overview | Done |
| 3 | Comments and Names | Old comments, `claude_session`·`codex_session`·`FINDERS` cleanup | Done — all three deleted |
| 4 | Small Postponements | Glossary, requests without project marks, translation output limit, haiku language cause | Done — haiku cause is inside the repository |
| 5 | Gate | All verifications above green | Done |

## Work Done

- Judgment. Lines where the first word is `완료`·`취소` are considered finished lines. `test_a_status_is_judged_by_its_first_word`
- `branch_line`. The plan said to cite `/api/draft` as the reason, but `/api/draft` does not call this function. The only calling place is `session_state.report` and it was always called in English. So, the Korean branch and `english` argument were deleted. What `/api/draft` uses is `active_page`·`decisions`, and the module header notes that
- `claude_session`·`codex_session`·`FINDERS`. Three tests called them. The rule separating rollout by cwd and the flattening collision rule are already protected by the tests of `checkouts()`·`logs()`. The remaining one was a test measuring the cost the mirror called every second, so it was deleted along with the function
- Project mark. The screen waits until the first `claim`, not `/api/channels`. The first load receives the channel list and translation switch together as `Promise.all`, so if left as is, they wait for each other — receive the switch after the list. The server rejects with 400 if `X-Project` is missing in requests that are not `GET`. `test_a_write_that_names_no_project_is_refused`. Re-read in the window and saw that the channel goes first and all POSTs are 200
- Translation limit. `maxOutputTokens = 2 × 입력 글자 수 + 64`. Prepayment deduction counts sent bytes as input tokens and the limit as output tokens. `MODEL` has no thought tokens. `test_the_hold_is_the_most_the_request_can_cost`
- Glossary. `"wiki-agent" = "wiki-agent"`. Saw that `wiki-agent는 …` comes out as an actual request
- haiku language. The cause is inside this repository. There is no user unit `CLAUDE.md` and no `language` setting. Sent the same question twice with hook on, and twice off (`--setting-sources ""`, `disableAllHooks`), and English was only once when it was on — hook and user settings are not the cause. Changing to `--system-prompt` resulted in English both times, but lost the default prompt of Claude Code. Adding one line at the end of `chat-answer.md`, "Questions usually come in Korean, answer in English anyway", resulted in English all three times in the actual combination where the hook is on (answer prompt + wiki focus header). The "write in English" at the head did not anticipate Korean questions, and haiku followed the language of the question
