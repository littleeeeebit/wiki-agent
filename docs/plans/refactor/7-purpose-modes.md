# Stage 7 — purpose modes, a read-only survey, and specs that run as tasks

The refactoring focus stops being a cumulative risk ladder that drives its own
candidate pipeline. A person picks a repository, a model for each of the
survey, the implementation and the review, and one of three purpose modes,
presses Start, and gets a read-only survey: the repository's
pipelines explained one by one with their debt and evidence, then executable
specs at the bottom. A spec the person starts is an ordinary task: one
worktree, one Agent conversation, the gate, one pull request, the review loop.
Nothing merges by itself.

## Problem

The modes `cleanup`, `restructure` and `full` were tiers L0–L1, L0–L2 and
L0–L3 of one ladder, so choosing a mode chose how much risk to allow, not what
to change. Only `full` read the whole repository; the others started from a
line-count hotspot list, which calls long code debt and short tangled code
clean. No survey recorded its base commit, so a spec written against old code
ran unchanged after the code moved. A step became several pull requests
(characterization, steps, test cleanup, ratchet), and its candidate
competition and approval states were a second execution system beside
ordinary tasks.

## Modes

Each mode is a purpose, not a stage to pass through. A survey runs in exactly
one mode. A change another mode owns is never folded in; when one is a real
prerequisite it becomes a separate spec with a dependency and its reason. A
mode may find nothing to do.

| Id | Name | Purpose | Representative changes | Does not |
| --- | --- | --- | --- | --- |
| `preserve` | Behaviour-preserving structure cleanup | Split functions, classes and modules into clear responsibilities while observable behaviour stays identical | Characterization and regression tests for uncovered core behaviour first; split a large function along responsibilities; remove code the split left without a job, proven unused code, misplaced classes | Change behaviour, fix known bugs, pin a bug as a contract, slice code only to shorten it, add abstractions for imagined reuse |
| `simplify` | Unneeded complexity and contract cleanup | Remove duplicated checks, stale test contracts and unclear names | Drop a check repeated where the guarantee already holds; remove unused compatibility branches, fallbacks and wrappers; update stale test contracts against current requirements; rename to role and domain meaning | Remove required defence at auth, permission, input, integrity, concurrency or external boundaries; change expectations to make current code pass; drop real compatibility versions (API, schema, migration) |
| `rework` | Structural and performance root fixes | Fix dependency structure and measured bottlenecks the other two cannot | Break cycles; move a responsibility across a boundary; remove round trips, repeated computation, lookups or serialization; fix an algorithm, data structure, I/O or process use | Add caches, parallelism, layers or frameworks by default; claim an improvement without the same-condition measurement; change a contract or migrate data inside an unrelated spec |

The old ids remain only on records written before this stage.

## Decisions

Chosen by the owner on 2026-10-24 from offered options.

| Question | Decision |
| --- | --- |
| Survey form | A persisted survey record that reuses the read-only refactoring turn; not a chat turn, not the planner |
| Survey output | Structured JSON blocks the server validates; a refused block goes back to the same model once with the reason (user's choice after a live 23-pipeline survey lost its integration turn to one 49-character key); a second refusal, or no block, stops the survey with what was wrong. An integration block names every refused spec at once, with the duplicate keys, unknown dependencies and cycles beside them, so one repair can fix them all (review round 1); a refused spec's text key and dependencies still make the graph, and a key that is not text is one more refusal (review round 2). A spec lists base-commit files it touches in `files` and files it creates in `creates` (absent at base, in a folder that exists): the live repair turn fixed the one new module named and stopped on the next |
| Large repositories | A map turn, one read-only turn per pipeline, then one integration turn; finished pipeline turns are kept and skipped on retry |
| Pipeline count | No ceiling; the map turn groups small helpers and says why |
| Spec unit | `preserve`: one spec for the whole repository (`pipeline`: `all`) holding every debt worth doing. `simplify`: one spec per pipeline (or `shared:<path>`) holding every debt worth doing there. `rework`: one spec per root cause, its files in whichever pipelines the fix touches and the others named in `impact`, so each measurement stands alone. User's choice on 2026-10-26, after a survey turned 19 pipelines and 4 shared modules into 46 cards of one function each, most under 70 lines of code; `preserve` narrowed to one spec on 2026-10-27, after the per-pipeline survey still gave 26 cards of one or two files each, because a behaviour-preserving debt stays inside one hotspot and one run, review and gate costs less than 26 |
| Survey tools | Read, Glob and Grep only; the server runs the gate once on the base commit for the baseline |
| Baseline gate | In a temporary detached linked worktree of the base commit, removed afterwards |
| `.omm` | Read in every mode when present and cross-checked against code: its node list and the files changed since its last commit go to the map turn; divergences are recorded; `.omm` is never written by a survey |
| Hotspot list | Removed from the screen; the scan goes to the map turn as reference measurements only; the ratchet stays in the final gate |
| Model | Only the model chosen in the focus runs the survey; each turn records the model the host reports and the survey stops on a mismatch; an unreported model is shown as unconfirmed |
| Roles | Start names three models — survey, implementation, review — none preset and none empty; they are fixed for every card that survey makes: its rechecks run on the survey model, its work cell (first turn, repairs, conflict fixes) on the implementation model whatever the screen sends, its review cell on the review model. The conversation that refines or splits the cards runs on the survey model too: the start writes it beside the mode on disk (kept over a restart), the focus refuses another model and its toolbar locks until the mode changes (review round 1). A stopped survey from before roles does not resume, and a card from before roles neither starts, rechecks nor is refined. Model names are stored stripped as checked, a `codex:` one with a model id after it. User's choice after a survey started on the chat toolbar's model, a model they had not meant. An unstarted card's implementation and review models can be chosen again on that card, one card at a time (`[모델 바꾸기]`, recorded in `refactor.role_changes`); its survey model stays, and a started card keeps its models to the end. User's choice when a quota ran low midway through a survey's cards; a same model for both only warns |
| Language | The model writes English; the screen reads it through the translation overlay; pull requests go up in Korean as today |
| Mode change | A new survey every time, nothing reused; unstarted specs of another mode or generation stay unstartable as today; the mode and generation are kept per repository on disk, so a restart changes neither; a stopped survey that has made no card yet stays resumable in its mode and resumes into the current generation (user's choice after a mode clicked away and back hid a 23-pipeline survey) |
| One survey per repository | A second start while one runs is refused; the request key deduplicates; a new survey's start deletes the unstarted specs of older surveys of its mode, not set aside (user's choice: the same debt never has two startable cards, even while the new survey runs or after it stops); a completed survey still drops what is left of older surveys; started specs stay |
| Repository switch | A survey keeps running on its own repository; the screen shows the selected repository's latest survey only |
| Spec execution | The ordinary task flow; one spec, one pull request; the execution model is the implementation role fixed at the survey's start, or chosen again on the card before it started |
| Concurrency | The ordinary task rules (`checkout_idle`); a second click on a started spec starts nothing |
| Dependencies | A spec whose prerequisite has not merged cannot start, and says which |
| Stale specs | At Start, the survey's base commit is compared with the current base branch: a change to the spec's target, created or evidence files refuses Start and offers a re-survey of that pipeline; other changes are listed on the task and Start proceeds |
| `preserve` characterization | Same pull request: the first turn writes or names tests only; the server checks that only test files changed and that they pass on the unchanged code, commits the freeze, and only then asks for the refactoring |
| Test contract | Each spec declares which tests it adds, which it may merge or delete (with reasons) and, in `simplify`, which contracts it updates; before the gate the server holds every other existing test file to the existing AST-equivalence check, additions allowed. A Python test file is compared as syntax, type comments included: every old statement stays unchanged and in order and every name it bound is bound as often, so a skip mark, a `pytestmark`, a shadowing redefinition, assignment, import or `del` is a change (review round 2); the count follows the body's scope through every `if`/`try`/`for`/`with`/`match`, with `except` and `match` targets, walruses, `import *` and nested `global`s, and through a definition's whole header — decorators, bases, defaults, annotations — but not a comprehension's or nested function's own names (review rounds 3–4); dynamic rebinding (`globals()`, `setattr`) stays outside a syntactic check. A declared `path` frees the file; a declared `path::name` frees only that test, which must exist at the start, and deleting the file needs the file declared. The contract holds on every head a round checks, review and conflict repairs included (review round 1) |
| Performance | A `rework` performance spec carries metric, representative task, measurement command and repeat count; the server measures before and after in the task's worktree, also where hosted CI owns the full suite; without a command it is a hypothesis and cannot start |
| Completion report | At the done report a survey spec runs the final command (`specs.local_required`) once instead of the narrow round checks and compares it with the survey's baseline by exit code and failure identifiers; when every failure was already in the baseline, both sides parsed and the exit code is the baseline's, the pull request opens with that stated; any unparsed or new failure, another exit code, or a verdict that never judged the head (no exit code, or 0 with a failed verdict: the tree, head or base moved) opens nothing (review round 1). A failing command that left the tree or head changed opens nothing whatever its exit code, and a name extracted is not a run explained: a failure-looking line (error, fatal, traceback…) no failure name covers and the baseline's tail did not print, digits and worktree paths blanked, opens nothing (review round 2). pytest's closing count line — exactly its comma-separated count list, time and `=` rule (none with `-q`), any other sentence with counts staying an ordinary line (review round 5) — is judged by its counts alone, before any digit-blind match: it stands explained when it counts no more failures and errors than the baseline's, whatever its passed, skipped and warning counts (review rounds 3–4). A pytest `FAILED`/`ERROR` line is read as a whole node id — spaces in its path or parameter id included — when exactly one of its prefixes before a ` - ` or its end reads as one; none, or several (a `] - ` inside a parameter id, or a parameterized test's message ending in `]`), reads as unparsed, so the pull request does not open (review rounds 5–6). Any collected file is a node (`docs/case1.txt::case1.txt`), an indented line is read as it stands stripped, and a `FAILED`/`ERROR` line that names no node is compared with the baseline as text, digits kept (review round 7). A pass stands as the final result for that head (`specs.proven`), so the pre-merge gate reruns only when review changes the head. The pre-merge gate itself is unchanged |
| Conversation | Answers questions with the latest survey as context and refines specs in place: the same spec id with a new revision; a split keeps the original id for one purpose and adds only the split-off part as a new spec of the same survey; no spec exists without a survey; a refinement passes the dependency-cycle check against every survey card of the repository — a recheck gives a card a new survey id, not new edges (review round 2) |
| Legacy runs | Shown read-only with their original mode names; no resume or approve; a restart releases a held repository |
| Legacy unstarted cards | Dropped (moved to `dropped/`) at server start |
| Legacy execution code | Deleted in pull request 3 (`refactor_finish.py`, `refactor_continuation.py` and the old phases); `refactor_profile.py` stays for the improvement runner |
| Hub | All three modes may survey; a hub spec runs only in its own linked worktree; merge and restart stay the person's |
| Label | 리펙터링 becomes 리팩터링 on screen and in messages; identifiers stay `refactor` |
| Delivery | This document, then three stacked pull requests merged in order |
| Verification | Fake-host automated tests; live: a scratch repository with and without `.omm` surveyed in all three modes and one spec run to a pull request; `ai-companion-voice-agent` surveyed only |

## Survey

`POST /api/refactors` with a new mode creates a run record under
`raw/refactor/runs/<repo>/` with `kind: "survey"`, the requested repository,
`roles` (survey, worker, reviewer: model and effort each), `role` (the survey
one, which the turns run on) and mode. It reuses the run worker, cancellation, restart
recovery and resume of `tool/main/refactor.py`; the phases are new.

1. `baseline` — the base branch's commit is recorded as `base`. The gate runs
   once in a detached linked worktree of `base` through `specs.judge`; the
   exit code, tail and parsed failure identifiers are stored. A gate that
   cannot run is recorded as not run, never as passing.
2. `map` — a read-only turn reads entry points, routing, configuration,
   dynamic registration, tests and `.omm`. It answers a `refactor-map` block:
   pipelines (id, name, entry, files and directories, one-line role), shared
   modules, excluded paths with reasons, and `.omm` use and divergences.
3. `pipelines` — one read-only turn per pipeline answers a `refactor-pipeline`
   block: what it does; inputs and outputs; links to other pipelines; the
   responsibilities it should own and those it wrongly holds; debts by kind
   with exact file and symbol evidence, a countable scope, and separate size,
   difficulty and risk ratings each with its reason; what goes wrong if left;
   what the mode would change and the expected effect; checked and unchecked
   areas. Little debt needs a reason; unread is `unchecked`, never `none`.
4. `integrate` — one read-only turn answers a `refactor-survey` block: the
   coverage summary and the specs. A change to a shared module that several
   pipelines need becomes its own spec they depend on, unless splitting it
   would break the intermediate state, which the spec then says.
5. `done` — the server validates and writes each spec as an ordinary
   `정리됨` card with `source.focus = "refactor"` and a `refactor` block:
   `{mode, survey, base, pipeline, files, evidence, depends, tests, measure}`.

Validation refuses a spec that covers more than one pipeline, names a file
absent at `base`, has another mode, depends on an unknown or cyclic id,
declares a contract update outside `simplify` (or `rework` marked as a
separate contract change with migration and rollback), or only investigates or
documents structure. In `preserve` it also refuses a second spec, and in
`simplify` a second spec on the same pipeline or `shared:<path>`. Every turn checks that HEAD and the working tree did not
move. The turn's reported model is compared with the requested one.

## Execution

Start on a survey spec goes through `specs.start` as an ordinary task after
these checks: mode and generation still current; the survey is the
repository's latest and complete; prerequisites merged; target and evidence
files unchanged since `base`; a performance spec has its measurement. A hub
spec gets a linked worktree. The work prompt adds the mode's rules from
`tool/prompts/refactor-run.md` and the spec's `refactor` block.

In `_check`, before the existing report, cleanup and gate steps:

- `preserve` waits for `refactor-tests` first, checks it as the old
  characterization step did, writes the frozen file the existing `preserve`
  command reads, and sends the refactoring turn. The final gate then reruns
  those tests through `review_contract.preservation_command`.
- The test contract check compares every test file between `start_head` and
  HEAD with `refactor_run.equivalent` (moved from the deleted `refactor_finish`), allowing pure additions and the
  spec's declared merges, deletions and contract updates.
- The final command replaces the round checks and its failures are compared
  with the survey baseline. Only pre-existing, parsed failures let the pull
  request open; a pass is kept as the head's final result.
- A performance spec is measured once the gate passes: the task's start commit
  and its final commit, back to back in the same worktree, with the same
  command and repeat count; the median of each is reported.

The pull request body gains a `리팩터링 검증` section: mode, survey base,
changed files since base, characterization, test contract, baseline
comparison and measurements.

The frozen file records where the tests were fixed as `head`; a legacy file's
`baseline` is a debt count, so review preservation reads `head` and falls back
to `start_head`.

A stale card is rechecked with `POST /api/refactors/specs/{sid}/recheck`: a
survey record with `recheck: <sid>` runs the baseline and one read-only turn
over that card's pipeline on the current commit. A spec with the same key
rewrites the card in place (a new revision, the recheck's base and survey); no
spec sets the card aside with the turn's reason. It retires no other card.

The conversation turn reads the latest survey of the selected mode and its
cards first (`refactor_run.context`). A `spec` block with `id` refines that
card in place; one with `split_from` adds the split-off part to the same
survey. Both pass the same validation as the survey's own specs.

## Screen

The 리팩터링 focus shows three mode cards: name, purpose, representative
changes and what each mode does not do. Above 리팩터링 시작 sit three
model pickers — 조사, 구현, 리뷰 — empty until picked; the button stays
disabled until all three name a model. It sends the repository, the three
roles and the mode; the chat toolbar's model is not used. The selected mode's latest survey
follows. It shows its phase (pipelines counted), Stop and Resume, the base
commit and its gate with the failures already there, the reported models, and
`.omm` use. Then come its coverage, unchecked and excluded paths, and one
collapsible report per pipeline. The model's English reads through the
overlay. A running recheck shows the same way.

The survey cards of the selected mode sit below the conversation. An
unstarted card has no free-text edit (`PUT /api/specs/{id}` refuses it). It
shows the spec's own fields and why it cannot start yet: another mode, an
unmerged prerequisite, an unmeasured hypothesis. It shows its fixed roles
(`refactor.roles`) in place of a picker; the card's `cell` and `reviewer`
carry them, the task pane's model picker is locked on its worktree and the
review pane says 리팩터링에서 지정. 다시 확인 starts the recheck. A started
card shows the 리팩터링 검증 table. Unstarted cards stay out of the task rail.
Runs of the earlier workflow are listed read-only.

## Pull requests

| # | Scope |
| --- | --- |
| 1 | Survey: modes, record and phases, prompts, validation, baseline gate, `.omm` input, model check, spec cards, one live survey per repository |
| 2 | Execution: Start checks, linked hub worktree, run prompt, characterization freeze, test contract, baseline comparison, measurement, PR section, conversation refinement in place, recheck |
| 3 | Screen: mode cards, Start, progress, report, specs at the bottom; legacy runs read-only and their pipeline removed; legacy cards dropped; label |

## Tests

Fake hosts drive each phase: a malformed block stops with its reason; a
cancelled survey resumes skipping finished pipelines; a second start is
refused; a survey never changes HEAD or the tree; a survey-only spec, a
two-pipeline spec and a cyclic dependency are refused; a stale target file
refuses Start; a prerequisite blocks Start; `preserve` refuses a
characterization turn that touched code; a changed existing test outside the
contract fails before the gate; a new failure blocks the pull request while a
pre-existing one does not; legacy runs read but cannot resume.

## Rollback

Revert the three pull requests. Survey records are new files beside the old
runs, and survey specs are ordinary cards that can be dropped.
