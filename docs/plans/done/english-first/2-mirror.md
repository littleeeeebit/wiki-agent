# Phase 2 — Mirror and wiki prose

The overall design and the relationship between the three phases are in [Overview](0-overview.md)].

Goal. What appears on the user screen becomes English, and a Korean mirror stands next to it.

Prerequisites. Phase 1 must be finished and `tool/translate.py` must exist.

Sequence rule. `mirror.py` must run first to turn on `english_progress.py`. Doing it in reverse creates a section where the user sees only English without a means to read it.

## What to build

### `tool/mirror.py`

Translate the output of Claude Code and Codex into Korean. The mirror person in charge first confirms the collection path, session identification, and samples for each host and distinguishes them with `--host claude|codex`. The JSONL specification below is for Claude. For Codex, define the reading implementation and inspection with actual samples, and if there is no collection path, hold mirror-live. Do not read and pass records from other cells instead. The second cell of Orca is opened by a human, and the execution method is written in `docs/mirror-setup.md`.

- Log location: `~/.claude/projects/<저장소 슬러그>/*.jsonl`.
  `tool/sessions.py` already knows the same path (`SESSIONS`, `sessions.folder`) —
  do not write it anew, call it. At the time it was `census.transcript_dir`,
  and it was a place where the mirror was made to import diagnostic tools, so it was removed later.
- If run without arguments, it grabs the most recent jsonl in this repository slug for `mtime`.
  It can be specified with `--session <경로>`.
- Translation targets:

| Target | Translate |
| --- | --- |
| `text` block of `assistant` | Translate |
| `description` field among `tool_use` of `assistant` | Translate |
| Remaining input of `tool_use` (commands, file contents, patches) | Do not translate. It is code |
| `tool_result` | Do not translate. The volume is large and most of it is code |
| `user` (typed by a human) | Print as is. It is already Korean |
| `<system-reminder>`/injection statements | Do not print. Filter with `census.INJECTED` label |

- Poll the end of the file. If the file is truncated or disappears, find it again (`/clear` creates a new session file). Also re-search if a new file is created while the existing file remains. Do not discard the last incomplete line being written; keep it until the next read. If there are multiple cells, mtime alone does not determine the target, so specify `--session`.
- For translation failures, print the original text (English). It is better to see at least English than for the mirror to be empty.
- `stdout` UTF-8 fixed. This is not a pipe but a terminal, but `craft/hooks-fail-open` has nailed down the range to all of `tool/*.py`.

Execution method is manual. Cell creation automation is not included in this plan.

### What changed through actual measurement — Mirror is a screen, not a terminal

As the plan wrote, it was first made as terminal output, and that path runs as is without `--web`. On top of that, a local screen was added at the user's request. One sheet of `tool/mirror.html` is displayed on stdlib `http.server` at `127.0.0.1:8788` (wiki chat `8787` next number, if blocked, pick a higher one) and SSE is streamed with `/stream`. 0 new dependencies, 0 build steps.

It is two-sided. The left is translated conversation, the right is untranslated commands and patches. Prose and code have fixed required widths that are opposite, so if put in one column, both lose. Values and evidence are held by root `DESIGN.md`.

Six things that were actually wrong in the plan specification:

- Since there are only three kinds in manifest, there was no place to write documents intentionally left in Korean. So those documents were entirely omitted from the manifest, and the gate cannot distinguish between omitted and forgotten. In fact, 13 files were written as "gate green" without ever undergoing protection zone verification. `kept` was added and `why` was made mandatory.

- **Codex sessions are not in one place `~/.codex/sessions`.** `CODEX_HOME` moves that location, and Orca gives it separately for each account — the actual path is `%APPDATA%/orca/codex-accounts/<id>/home/sessions`. A mirror that only reads the default path says "no session" without ever seeing the session the user is actually using. This, not the parser, was the reason the Codex side of step 2 was blocked. `codex_homes()` collects all three. The repository list went from 3 to 11.

- Claude's intermediate utterance is not a `user` record. The original text remains only in `content` of `queue-operation`/`enqueue`. If this is not read, only 1 out of 11 human utterances appears.
- `HTTPServer.allow_reuse_address` steals others' ports on Windows. If not turned off, the port forwarding loop does not run and both mirrors serve the same port.
- `timestamp` of both hosts is UTC. If cut and used like `transcript.py`, it is off by 9 hours.
- Codex's tool description is `McpToolCall.arguments.title`, patch is `unified_diff` of `FileChange`, and command is `CommandExecution.command`.

### `tool/test_mirror.py`

- Create a fake jsonl to see if `tool_result` and injection statements are not printed
- Change the translation function to fake to see if only `assistant` text and `description` pass through
- See if it does not die when the file is truncated in the middle

## What to fix

### Enforcement reversal — `korean_progress.py` → `english_progress.py`

Do this after confirming that `mirror.py` actually runs.

- Reverse the judgment: deny if there is Korean outside the protection zone of the description. Allow `keep_korean` in the glossary and commands/paths inside backticks.
- Keep the blocking reason and `systemMessage` visible to the user in Korean. `WATCHED = {"Bash", "Agent", "Task"}` remains as is.
- Exceptions pass through as is — entry point guard, `stdin`/`stdout` UTF-8 fixed.
- Since the file name changes, the wiring must follow. The `PreToolUse` item of `.claude/settings.json` is generated by `tool/apply.py` from `enforce: pretooluse:` of the page front matter. Move the page and run `apply --write`.
- For Codex, `codex_pretool.py` directly imports and calls `korean_progress`. Change the import and call together. Since Claude's `apply.merge/put_hook` does not delete the old hook, add a migration that removes only the existing `korean_progress.py` entries it owns. Preserve user hooks. Include an upgrade with existing installation as input and a double-application check.

### Moving pages — `operator/korean-progress.md` → `operator/english-progress.md`

- Rewrite the body in English. Since the rules are reversed, it is a rewrite, not a translation.
- Leave `triggers` as the Korean regex, but re-select it to fit the reversed rules.
- Fix all `links` and the `[[korean-progress]]` of other pages pointing to this page. `operator/ask-with-arrow-key-options.md`, `operator/report-without-stopping.md`, links in `craft/hooks-fail-open.md`, and Markdown links in `index.md` also point to it. Also check all old name references in code, tests, and documents with `rg`, and do not postpone behavior test changes to phase 3.
- The remaining two that actually came out in the full investigation: `korean_progress` listed in the `tool/` list of `README.md:24`, and `SCRIPTS = {"korean_progress.py": ...}` of `tool/test_apply.py:43`. For the latter, if the name is not fixed, the test verifies the wiring with the old name and turns green — the test does not see that the wiring has changed.
- Project intellectuals `.wiki/graph.json`/`.wiki/corpus.json` and the hub policy map root `graph.json` are separate. The former is regenerated with `python tool/sync.py --project .`, the latter with `python tool/graph.py`, and verify that there is no old page ID in `/api/graph`.

### Web document/map display — Pre-work before pages

The phase 2 web display person in charge implements the Korean document display for `POST /api/translate` and `Peek.tsx`. The API calls the phase 1 translator, verifies direction/length, and maintains localhost binding. Preserve the original path/line number and the actual original text, and display the translation separately. The title and rule description of map `web/src/graph/force.ts` also show Korean with the same API. Do not translate identifiers, links, and setting values. For failures, show the original text and a Korean failure notice. Verify first with fake English document/map responses, then change the actual pages. This minimal display path ships in phase 2 without waiting for phase 3.

Also make `graph.load_pages()` recognize the `Rule.` paragraph since it only reads `규칙.`. Include a check that rule is not empty in Korean/English samples and a map Korean display check in this phase.

### Wiki prose Anglicization — 32 files

`operator/` 9 · `craft/` 12 · `docs/` 6 · root 5 (`README`·`SCHEMA`·`ENFORCEMENT`·`MAINTENANCE`·`index`). Add new `docs/mirror-setup.md` separately to these 32. Keep the four documents of `docs/plans/` in Korean.

#### What increased by actual measurement, and what the user removed

The target was not 32, but 24 chapters of rules + 5 chapters of root + 5 chapters of `docs/` = 34 chapters. After writing the plan, `craft/` increased from 12 to 15, and `DESIGN.md` appeared in the root.

**Keep `docs/chat-setup.md` and `docs/mirror-setup.md` in Korean.** The user decided so on 2026-09-22, and the evidence is the standard of plan 0 itself — what goes to English is the "surface the agent reaches," but these two are documents that team members read before installation. Before installation, neither the mirror nor the citation drawer runs, so there is no path to read in Korean, and that would directly violate the sequence rule of this plan ("do not change the user screen to English before the path to read in Korean stands"). The two files are on the "what humans read" side that `operator/english-progress` talks about.

What not to touch in each file:

- The entire front matter. Especially `triggers` — matches user utterances. However, record the enforce/links/trigger changes of the page rewrite above as exact manifest allowance differences.
- Inside backticks, inside code blocks.
- `[[링크]]` slug. If you are going to change the slug to English, all at once, and up to `graph.json` regeneration is one set. The current slug is already English — do not change it.
- Actual Korean strings that the page cites — e.g., label words of `ko.toml`, ending `-겠습니다` that `declared_continuation.py` sees. This is data, not prose.

While rewriting in English, also follow `craft/emphasis-is-scarce`. The existing files that the hook currently blocks are 15 chapters — `craft/` 8, `operator/` 3, root 4. All are targets for rewriting in this phase, so do not organize them separately and pass them all at once here. Whether they pass is confirmed by `Write` actually not being blocked.

There are places where rewriting, not translation, is correct. The "line breaks are done in meaning units" section of `craft/comments-carry-why.md` is about Korean adnominal forms/dependent nouns. Since that problem does not exist in English, rewrite it based on English standards (articles/prepositions/conjunctions left at the end of lines).

### `tool/lint.py` — Replace line break check for English

| What to delete | Instead |
| --- | --- |
| `ends_adnominal` | Remove |
| `splits_a_phrase` | `orphan_tail(word)` — articles/prepositions/conjunctions/auxiliary verbs left at the end of lines |
| `prose_lines` | Use as is. Filtering code/tables/lists/front matter is independent of language |
| `broken_wraps` | Keep name, replace only judgment |

- Change the corresponding test of `test_lint.py` to an English sample. The point is to see if the check actually turns red — the test is written that way now.
- Delete the Korean display width annotation (61%·54% statistics) of `lint.py:260` because the evidence has disappeared.

### Maintain Korean in status/blocking UI

Keep the blocking/error messages for users in `statusMessage` and hooks in Korean. In this phase, do not add English mapping or status-only update logic. The status person in charge checks the normal status of both hosts and the Korean description blocking screen. If only English is seen, restore the corresponding message to Korean, re-verify, and hold the enforce/gate completion.

## Phases

| # | Name | What | Status |
| --- | --- | --- | --- |
| 1 | mirror | Both host output path investigation·`mirror.py`·`test_mirror.py`·manual execution document | Done |
| 2 | mirror-live | Claude·Codex each actual session and Korean display range verification | Done — actual measurement of both hosts. Codex was visible only after finding `CODEX_HOME` |
| 3 | web-docs | Translation API·citation drawer·map Korean display and graph's Rule parser verification | Done |
| 4 | enforce | English progress·English Stop judgment·page rewrite·both host wiring switch | Done |
| 5 | pages | Git-based manifest confirmation·32 prose Anglicization·allowance protection zone difference record | Done — 37 manifest items. 24 rule chapters + 10 root/document chapters Anglicized, 3 left in Korean declared as `kept` |
| 6 | lint-index | English line break check·direct execution check·sync and graph each regeneration | Done |
| 7 | status | Both host status·blocking·question UI Korean maintenance check | Done |
| 8 | gate | `--check` green + sample back-translation human check | Done — `translate --check` 37/37 exit code 0, 3 back-translations read by human and "nothing breaks significantly" |

## Verification

- `python tool/translate.py --check --manifest docs/translation-baseline.json operator/ craft/ docs/ *.md` — exit code 0. Exclude the plan and compare rewrite exceptions with manifest allowance differences.
- A human reads the sample back-translation of `raw/translate-review.md`. 3 pages per phase.
- `python tool/lint.py --check` · `python tool/apply.py --project . --agent claude --check` · `python tool/apply.py --project . --agent codex --check` · `pytest tool/`
- Check the changed paths during the direct execution check of `docs/development.md`: `python tool/test_apply.py`, `python tool/test_lint.py`, `python tool/test_inject.py`, `python tool/test_declared_continuation.py`. Especially, the main check of apply·lint·declared_continuation is not executed just by pytest collection.
- `npm --prefix web run lint` · `npm --prefix web run build` and document/map Korean display actual measurement.
- `python tool/trigger_audit.py "raw/census-*.jsonl" --project .` — replay the same census input that actually exists before and after to compare the hit list per page. The gate person in charge secures the same input before pages and records the baseline. If there is no input, run synthetic regression separately, but leave actual utterance hit verification as pending, and do not mark it as having replaced actual measurement. This tool is not an automatic hit rate regression gate. Compare progress page name changes by mapping them, and distinguish between cost changes and trigger changes due to translation.
- Actual measurement: With one Korean utterance, (a) is the page injected in English (b) does Korean appear in the mirror cell (c) am I blocked if I use Korean `description`?

## How to revert

Revert pages·lint·hooks·Codex wrapper·generator in the same transition unit, remove the owned English hook entries, and then regenerate both host settings and graph/corpus. After confirming that the existing Korean enforcement and Stop check return, close the mirror. Removing only settings is not enough.

## Review reflection — Empty seats to close before transition

- The current mirror specification supports only Claude. Support it after confirming Codex's output collection path·record format·session identification on the actual host and securing test samples. Before that, do not switch common pages and Codex enforcement to English. Leave question options and hook status/errors as Korean exceptions.
- `declared_continuation.py` checks the Korean ending of the assistant response, not the user. Along with the English progress transition, add judgments for English promises·question promises·grace periods·actual questions and check Korean/English regression samples. Maintain the original Korean pattern for the failure original text path.
- Match the Korean display rules of `ask-with-arrow-key-options` and the exceptions of `english-progress`. Web conversation answers maintain Korean output until phase 3. The web document/map Korean display is finished by the phase 2 web-docs person in charge before pages. If it fails, hold the pages deployment and gate.
