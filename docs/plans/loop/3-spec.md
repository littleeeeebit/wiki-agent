# Step 3 — Specification and Handoff

The relationship between the overall design and the steps is in [Overview](0-overview.md)]. This step stands on top of the turn reconnection in [Step 2](2-agent.md)].

Goal. The conversation with the wiki ends with a single task specification, and when [Start] is pressed, a session that receives that specification as a system prompt in the task tree works. The server only believes it is finished after running the gate again to verify. Once verified, the server creates a PR, and the result returns to the original conversation.

The screen is attached only as much as needed for verification. Step 6 rebuilds it.

## Agreed with the user

2026-09-25.

| What | Agreed |
| --- | --- |
| Reducing focus | Per step. In step 3, `다음 작업` replaces progress, and the diagnosis header is merged into the wiki focus. The review focus goes to the review cell in step 4. The record of deleted focus (`raw/chat/<id>.jsonl`) is not deleted |
| Candidate materials | The server collects them and attaches them to the first turn |
| When to provide candidates | [Provide candidate] button. A turn is consumed only when pressed. You may also ask directly |
| Shape of the answer | Named blocks within the answer — `candidates`, `choices`, `spec` |
| Modifying specifications | Modify goals, exclusions, and completion conditions in the card. Modify evidence and decisions through conversation |
| Name of the specification | The agent provides `slug`. The server checks the shape and appends `-2` if it overlaps. Can be modified in the card |
| Completion judgment | The agent's `done-report` block, and the server runs `gate_cmd` once more in the task tree |
| PR | Once completion is verified, the server pushes with the person's `gh` and `gh pr create` |
| Result summary | Once when the PR is created, once when merged. The server writes it as a specification and report without calling the model |
| Split specifications | Multiple cards do not know each other. The order is determined by the person via [Start] |
| Plan line | Only that line in the document where the specification emerged is changed to `완료 — PR #n`. If there is no source line, no document is touched |

There is one difference from the overview. The overview stated "No `gate_cmd` in `done` cannot be a card." Here, the server always adds `gate_cmd` as the first item of `done`. Instead of checking what the agent might miss, it makes it impossible to miss. A specification for a repository without `gate_cmd` in the adapter does not become a card and shows "Connect first".

## Specification

### Shape

`raw/specs/<repo>/<id>.json`. Add an operation column to the overview column.

```json
{
  "id": "fix-login-redirect",
  "repo": "wiki-agent",
  "rev": 2,
  "goal": "로그인 뒤 원래 페이지로 돌아간다",
  "out": ["세션 만료 처리"],
  "done": ["python -m pytest tool", "로그인 뒤 /settings 로 돌아오는 테스트가 있다"],
  "grounds": {"pages": ["craft/gate-the-exit-not-the-callers"], "files": ["web/src/lib/api.ts:96"], "rules": []},
  "decisions": [{"what": "리다이렉트를 서버에서 한다", "why": "…", "rejected": "화면에서 history 로"}],
  "source": {"focus": "next", "turn": 1727240000.1, "plan": {"path": "docs/plans/loop/3-spec.md", "row": "2"}},
  "state": "정리됨",
  "stopped": null,
  "worktree": null,
  "pr": null,
  "report": null,
  "gate": null,
  "history": [{"ts": 1727240000.1, "state": "정리됨"}]
}
```

- `id` is the same as the task tree name and branch name. It is in the shape of `workspace.TASK`
- `rev` increases every time it is modified. Saving a card carries the `rev` it saw, and if different, it is 409
- The values of `state` are all in this list. This is the only place that determines the state of the specification, and the overview and other step documents point to this list (review round 6). `specs.py` receives all these values from the beginning. The step 3 code uses the first three and `머지됨`·`멈춤`, and the rest are used by the loop in step 4

  | Value | Meaning |
  | --- | --- |
  | `정리됨` | The card is created and has not started. The only state that can be modified |
  | `작업 중` | A work session is working in the task tree. [Re-PR] in step 4 also returns here |
  | `PR #n` | PR has been created. Before the plan line commit and re-push are finished |
  | `리뷰 대기` | Waiting for a concurrent execution slot in the loop |
  | `리뷰 Rn` | The review cell sees round n |
  | `고치는 중 Rn` | The work cell fixes the discovery of round n. Includes gate and push |
  | `머지 가능` | The last round was `머지 허용` |
  | `머지 대기` | After [Merge], it is in the merge queue or auto-merge, and the PR is still open |
  | `머지됨` | PR is `MERGED` |
  | `멈춤` | `stopped: {reason, detail}` is attached. The value of `reason` is all in the stop reason table of [Step 4](4-review.md)] |

  `waiting` (waiting for approval) is not a state but a separately attached mark. It can be true in any state
- Specifications are only modified in `정리됨`. If the goal of a started specification changes, it is a new specification

### Server

`tool/main/specs.py` is the sole owner of the specification file. Reading, checking, saving, and changing states are done only here. Saving is written to a temporary file and `replace`.

| Path | Task |
| --- | --- |
| `GET /api/specs` | List of specifications for the selected project |
| `PUT /api/specs/{id}` | Modification of the card. `{rev, goal, out, done}`. The first line of `done` (gate) is re-inserted by the server |
| `POST /api/specs/{id}/start` | [Start]. Handoff below |
| `POST /api/specs/{id}/drop` | Discard. The file is moved to `raw/specs/<repo>/dropped/` |

Check.

- If `goal` is empty, reject
- The first item of `done` is the `gate_cmd` of the adapter. The same line inserted by the agent is left only once
- If `slug` is not in the shape of `TASK`, change to lowercase and change disallowed characters to `-`. If it is still empty, reject
- If there is a specification with the same `id` or a task tree with the same name, append `-2`, `-3`
- The path of `grounds.files` must be within that repository. Paths that do not exist are not deleted but marked as "non-existent path" on the card

## Next work focus

### Changing focus

`channels.CHANNELS`.

| Now | After step 3 |
| --- | --- |
| `progress` progress | Delete. The next work of `next` replaces it |
| `diagnose` diagnosis | Delete. Merge "Look at the record first" in the header into one paragraph in the header of `wiki` |
| `retro` retrospect | As is |
| `review` review | As is until step 4 |
| `wiki` wiki | Header with diagnosis merged |

The header of `next` is kept in English in `tool/prompts/next-task.md`. The shape of the block and the order of follow-up questions are included.

### [Provide candidate]

Add `propose: true` to the body of `POST /api/say/next`. The server creates the words.

| Material | Where | Max |
| --- | --- | --- |
| Remaining lines of plan | `session_state.plans(repo)`, `active_page(repo)` | Plan set, line column |
| Open PR | `gh pr list --state open --json number,title,headRefName,isDraft,reviewDecision,url` | Column |
| Recent decisions | `session_state.decisions(repo)` | Current `MAX_DECISIONS` |
| Warning | `lint --check` if hub, discovery of `repo_lint` if target repository | Column |
| Remaining P2 | `p2` of the specification file. Step 4 fills it. Empty before that | |

If one material cannot be read, write one line of reason in that section and move to the rest ("gh is not logged in"). The user line of the record contains the entire material. The `--resume` CLI must be seen as having seen the same material. The screen is folded into one line "(Candidate request)".

### Block

Place at the end of the answer. The server extracts the block from `done`, checks it, and sends it separately as an `blocks` event. Delete the block in the body of the answer so that the translation overlay does not move the JSON.

| Block | Shape | Screen |
| --- | --- | --- |
| `candidates` | `[{title, why, source}]`, three to five | Button list. If pressed, that `title` goes to the next word |
| `choices` | `{question, options: [{label, note}], multi}` | Button or checkbox and [Send]. The selected one goes to the next word |
| `spec` | Add fields filled by the person in the specification above `slug`. If multiple, split specifications | Specification card |

If the block is broken (not JSON, missing fields), an error line and [Re-request] are shown instead of the card. [Re-request] sends "The `spec` block failed: <reason>. Emit it again."

If the `spec` of the same `slug` appears again in the same conversation, and that specification is still `정리됨`, update it by increasing `rev`. The card shows the new version and attaches "Version 2".

### Place where the result returns

Write the `role: "result"` line in `raw/chat/next.jsonl`. `{spec, text}`. The screen draws that line as one line between conversations. When a person speaks from that focus next, the server attaches the result line that the CLI has not yet seen before the speech ("Since your last turn: …"). The next conversation knows the result without calling the model separately.

## Handoff

### [Start]

1. Grab the specification. If not `정리됨`, 409
2. `workspace.create(repo, id)`. If it fails, the specification remains `정리됨`
3. Write the `worktree` of the specification and change it to `작업 중`
4. Make the work session the system prompt for the specification. Expand `work.session` to receive `system`. The current write session has no system prompt (`tool/main/work.py:202`)
5. Launch the first turn "Start." with the `Run` of step 2 and return the turn id. The screen selects and attaches to that task tree

Reason for this order. If the specification becomes `작업 중` first, it must be reverted if task tree creation fails. If the task tree is first, nothing changes for a failed specification.

### System prompt for work session

Keep in English in `tool/prompts/work-spec.md`. Load the specification as is and attach rules.

- Work only within this task tree. Do not do what is in `out`
- Commit what you have changed. Commit messages follow the conventions of this repository
- Do not do push and PR. The server does it
- To say it is finished, place the `done-report` block at the end of the answer. For each item in `done`, `{item, pass, evidence}`. `evidence` is the command run and the last line of its output
- If even one item does not pass, do not say it is finished. Write down what is blocked and ask

### Completion judgment

Every time a work turn ends, `specs` looks at it.

1. If there is no `done-report` in the answer, do nothing. The conversation continues
2. If there is a block and all `pass` are true, the server verifies
   - `git status --porcelain` is empty. Otherwise, judgment fails as "uncommitted changes"
   - Run `gate_cmd` in the task tree. Since it is a shell string, run it as a shell. Deadline is 20 minutes. Leave the last 80 lines of output in `gate` of the specification
3. If judgment fails, the specification remains at `작업 중` and the reason and output tail are visible on the card. The person decides what to send to the work session. The loop in step 4 automatically connects this spot
4. If judgment passes, create a PR

This is the first time the server runs a command in the task tree in this step. It is not an original checkout, and it is the same as the command the agent already ran. Add this line to "Where the screen writes to the server" in the overview.

### PR

1. `git push -u origin <id>`
2. Create the body. Use the section name read by `harvest.record` as is

   ```markdown
   ## 변경 요약
   <goal>

   ## 변경 이유
   - <what> — <why> (버린 것: <rejected>)

   ## 확인
   - [x] <item> — <evidence>

   ## 명세
   `raw/specs/<repo>/<id>.json` · 근거: <pages>, <files>
   ```

3. `gh pr create --base <기본 브랜치> --head <id> --title <goal> --body-file <임시 파일>`
4. Change the specification to `PR #n` and write the result line — "PR #n — <goal>. Completion condition k passed"
5. If the specification came from a plan line, send one turn to the work session. "PR #n has been created. Change the status column of the `<row>` line of `<path>` to `완료 — PR #n` and commit." When that turn ends, the server pushes again

Reason for changing the line after the PR. It was decided to write the PR number in the status column, and the number exists only after the PR is created. Since this commit is also inside the PR, the review in step 4 sees it.

### Merged

There is no [Merge] in step 3. When a person merges on GitHub, when reading the specification list (when the window receives focus), it recognizes it as `gh pr view <n> --json state,mergedAt` and changes it to `머지됨`. Write the result line "PR #n merged". If [Merge] in step 4 is created, leave this path as is, but [Merge] changes it first.

## Screen

Temporary. Step 6 rebuilds it.

- `next` focus on the query side. [Provide candidate] in an empty conversation. Candidate buttons, options, specification card below the answer
- Specification card. Goal, exclusion, completion condition input fields (gate line is locked), list of evidence and decisions, version number, [Save] [Start] [Discard]. Started cards show only status, task tree name, and PR link
- Attach the specification status as one word to the task tree line of the rail
- Keep the old "→ Work" draft (`/api/draft`) as is. Remove in step 6

## Test

Place `test_specs.py` newly. The proxy CLI uses the method of `test_agent.py`.

| What | Case of becoming red |
| --- | --- |
| Reject empty `goal`, gate is always the first item, reject repository without adapter | If you delete checks one by one |
| `-2` when `slug` cleanup and overlap | If you delete overlap check |
| Saving old `rev` is 409 | If you delete `rev` comparison |
| Extracting block. Broken JSON is error, block is missing from body | |
| [Start] creates task tree first, if it fails, specification remains `정리됨` | If you change the order |
| Work session receives specification as system prompt | See as argument received by proxy |
| Even if `done-report` passes, if gate fails, there is no PR. Uncommitted changes are the same | If you delete server's verification |
| If PR body is put in `harvest.record`, `무엇`·`왜` are not empty | If you change section name |
| Result line is attached only once before the next word | |

Replace `gh` and `git push` with proxy. The actual PR is created once in the verification step.

## What we do not do

| What | Why |
| --- | --- |
| Order between specifications (`after`) | User's decision. Person decides with [Start] |
| Automatically resend after gate failure | Loop in step 4 does it |
| [Merge] | Step 4 |
| Deleting "→ Work" draft | Step 6. If deleted now, there is no way to assign work without a specification |
| Model writing result summary | User's decision. Specification and report are enough |

## Agreed in implementation

Filled the spots left empty by the plan like this.

| What | Agreed |
| --- | --- |
| Plan line source | Agent puts `plan: {path, row}` in `spec` block. Server accepts as `source.plan` only when that file is under `docs/plans/` or `.wiki/plan-active.md` and there is a table row where the first column is `row`. Otherwise, it is a specification without a source line |
| Same conversation | When the `source.session` of the specification is the same as the session id of the CLI. If you delete the context, it is a new conversation, so the same `slug` becomes `-2` |
| Overlap | Specification with the same name, task tree folder, branch. If there is a branch, `git worktree add -b` fails late in [Start] |
| Path | `grounds.files` outside the repository is not accepted. Only paths that are inside but do not exist are left and shown as "non-existent path" |
| Where judgment runs | In the thread of the work turn, while holding the task tree. The gate line remains in the tool line and record of that turn. [Stop] of the turn also stops the gate. Read stop even after the gate is finished, and read once more right before push and PR creation — even if the gate passed, it is not exported after stop |
| Completion report | If the number of items is less than `done` or if even one `pass` is false, do not judge. Do not compare the text of the items — even if the agent writes the line slightly differently, the server's gate does the judgment |
| push·PR failure | Specification remains `작업 중`, reason in `fault`. If reported again, judge again |
| Plan line turn | Sent after the turn that created the PR releases the task tree. If a person sent it first in the meantime, do not send it and write in `fault`. After that, whenever any turn ends, push and close only when that line of the committed HEAD is `완료 — PR #n`. Otherwise, write in `fault` and wait for the next turn |
| Discard | Only `정리됨` and `머지됨` |
| System prompt of session | Read from the specification file every time a session is created. The first instruction after restarting the server also receives the specification |

## Verification

- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- Once to this repository in the window. [Provide candidate] → one candidate → two follow-up questions → specification card → modify one completion condition → [Start] → work session works according to specification and `done-report` → server's gate → PR is created and sections in the body are correct → result line in conversation → plan line commit in PR → merge on GitHub → `머지됨` and result line
- After merging that PR, run `harvest` once to see if `왜` of the decision record is `decisions` of the specification

## What I saw in window verification

2026-09-25, one round to this repository. Three candidates in [Provide candidate], three follow-up questions, specification card `readme-next-task-checks`, one line of completion condition modified to version 2, [Start], 7 items of `done-report` in work session, server's gate 332 passed, PR #15 (four sections in body), result line, plan line commit and re-push, GitHub merge, `머지됨` and result line, `왜` of `harvest` preview is the first decision of the specification. One specification (`loop-4-review-cell`) was left as `정리됨`.

The base of PR #15 was `main` as determined by the server. Since the task tree branched from this PR's branch and carried the commits of this PR, I manually changed the base to this PR's branch and merged.

Two empty spots on the screen. Both are spots where the server causes work itself, so they become more frequent in the 4-step loop. Close them together when step 4 sets up the path for the screen to listen to server changes, and step 6 rebuilds them.

| What | Now |
| --- | --- |
| Turn launched by server | Turns sent by the server like the plan line turn do not attach to the screen of the already opened task tree. Attach when refreshed |
| Result line generated while reading list | The result line of `머지됨` noticed while reading the specification list is visible when reading the conversation again |

## Requirements and two views

Added 2026-11-08 (`baseline-spec-two-views`). A spec record may carry a
versioned requirements section. Code renders a human review document and the
write session's input from the same frozen snapshot. A setting keeps that
input out of sessions until an experiment turns it on.
`tool/main/spec_views.py` owns the section; `specs.py` still owns the file.

### Data model

| Field | Meaning |
| --- | --- |
| `original` | The person's own words in the `next` conversation that produced the card, verbatim (`said`, else `text`, of each user row since the last clear, read whole rather than through the screen's 200-row replay). Set once when the card is made |
| `requirements[]` | `id` (`R1`, `R2`, …, unique), `text`, `source` `{kind, locator}` with `kind` in `original`, `policy`, `repository`, `decision`, `status` in `confirmed`, `assumed`, `undecided`, `not_applicable`. Optional: `preconditions`, `postconditions`, `invariants`, `failures`, `depends_on` (ids in the same list), `context` (paths), `verify` (`{item, expected}`), `global: true` |
| `spec_version` | An integer that moves on whenever the requirements change |
| `spec_digest` | sha256 of the canonical requirements JSON (sorted keys, no whitespace) |

`specs.fields` validates the list (`spec_views.requirements`). An unknown key,
a repeated id or a `depends_on` that names no requirement is refused, and the
error names the item and the key. `tool/prompts/spec-requirements.md`
documents the field for the planner. `channels.preamble` appends it to the
`next` and `refactor` focus introductions only while the setting is on;
`tool/prompts/next-task.md` itself is unchanged.

The record's guarantees sit in `save`, the one exit every path passes
(`spec_views.stamped`). A stored `original` and each stored requirement's
`source` are restored before writing, whatever the caller changed. The version
and digest are recomputed there too. A goal edit leaves both as they were.
`REVISABLE` accepts `requirements` but never `original` or a verification
result. A requirement first added by a `spec-update` is recorded as
`decision`, with the revision and reason as its locator, whatever source it
claims. A revision's history entry keeps the earlier requirements.

### Two renderers

Both are pure functions over the record. No model call takes part.

- `specs.human(spec)` is Markdown with these parts: purpose (the goal);
  behaviour (each requirement with its id, status, source, conditions,
  dependencies, context and verify items); constraints (`out` and the global
  requirements); undecided items; conflicts; the original request quoted
  unchanged. Inside the behaviour section, every value sits on its own line
  behind a fixed label (`- Requirement:`, `- Verify:` with an indented
  `- Expected:` under it, and so on). Any accepted one-line value therefore
  reads back unambiguously, including values that look like the document's
  own structure. Free text outside it (the goal, `out` lines, undecided
  items) is put on one line, and the original request is quoted line by
  line. No free text can therefore start a heading the parser reads.
- `specs.agent(spec, task=None)` is a dict holding the version, digest,
  selected requirements, `contracts` and `omitted`. The contracts are
  id, status, text, postconditions and invariants for each `depends_on` target
  reached outside the selection, followed transitively. `omitted` lists the
  remaining ids, available on request. `task=None` selects all.
- `specs.equivalent(human_md, agent_json)` reads the human document's
  behaviour section back. It compares facts per requirement: id, status, text,
  each condition, each dependency, each verify item, negation words and
  number-with-unit tokens. Contracts count as facts too. What a selection
  owes comes from the human document, never from the agent rendering's own
  `contracts` and `omitted` lists. Owed in full: the selected requirements
  and every global one. Owed as contract fields: every dependency the full
  ones reach. Only the rest may appear by id alone. A contract or global
  declared as omitted therefore shows as missing facts. It returns what each
  rendering lacks.

### Setting

`SPEC_VIEWS=on` in the hub `.env`, read through `common.settings.pick`, the
same reader as `decision.env_file()`. It defaults to off. Off, every agent
input is what main produced: the write session's system prompt and the `next`
and `refactor` introductions. A test compares them with sha256 digests taken
from main at `fc886a3`. The write-session comparison covers the ten records
saved when this spec was made, whose prompt fields are committed in
`tool/fixtures/spec_views_baseline.json`. On, a record with `requirements`
gets one more section after the spec. That section
is the agent view as JSON with `spec_version` and `spec_digest`, and a note
that a `spec-update` may carry `requirements`.

### Version and undecided at the first turn

With the setting on, `spec_views.first_turn` runs on the first work turn
(`work.run_turn`, `decide`), before Jev's start decision.

- If a requirement's `depends_on` target is `undecided`, the turn holds. The
  agent is sent nothing; the reply asks the person to settle the named ids, and
  `fault` says the same. Undecided items nothing depends on do not hold.
- If the session prompt's `spec_version` differs from the saved record's, the
  session is rebuilt through `work.session`. That function already replaces a
  session whose spec-derived prompt changed. An equal version reuses the
  session.

### Verification results

`raw/specs/<repo>/<id>.verify.json` sits beside the record. Only the round gate
in `specs._check` writes it (`spec_views.verified`). A verify item is recorded
only when its text is one of the commands the gate ran. The entry is keyed by
requirement id, `spec_version`, `spec_digest` and the gate's commit (`head`),
and holds `ok`, `head` and the time per item. A gate at another commit starts
the entry over. A passing gate passes the matched items. A failing gate fails
them all, since it does not say which command stopped it. `spec_views.results`
reads a requirement as `pass` only when every verify item ran and passed under
the current version at one commit. Passes from different commits never add up. It reads `fail` when one failed, `stale` when the entry
belongs to another version or digest, and `missing` otherwise; a stale entry
never counts as a pass. `spec-update` cannot reach the file.

`GET /api/specs/{id}/views` returns `{human, agent, spec_version,
spec_digest}`. A card whose record has requirements shows a collapsible
`요구사항 문서` section, read on first open (`web/src/components/SpecViews.tsx`).
The section is keyed by the record's revision and requirements version. Any
revision, a goal-only one included, retires the shown document and any reply
still in flight. `web/tests/spec_views_browser.py` checks this.

### Assumptions and undecided design points

| Point | Status |
| --- | --- |
| `original` is every person row of the conversation since its last clear, so it can include requests that went into other cards | Assumed |
| The planner writes `requirements`; nothing derives them from `goal` and `done` for older records, which keep no section | Assumed |
| Conflicts are dependencies on an `undecided` or `not_applicable` requirement; contradictions between texts are not detected | Assumed |
| Negations and units are matched by fixed word and unit lists in English; other languages compare only by text equality | Assumed |
| Only the round gate records results, and only for verify items that are literally one of its commands. The final gate, browser or desktop checks and the done report's evidence are not mapped to requirements, so such items stay `missing` | Undecided |
| A held first turn waits for the person's instruction; there is no screen control to change a requirement's status | Undecided |
| The reviewer and the post-compaction restatement still read `brief` only | Undecided |
| Which selection a task passes to `agent` is left to the experiment harness, which is the next task | Decided in the pilot: one model call per task picks the ids, code adds every global one (below) |

### The comparison harness

Added 2026-11-08 (`spec-views-pilot`). `tool/eval/spec_views.py` compares four
inputs to one write session. Each is rendered from the same frozen record.

| Condition | System prompt after the work-spec rules |
| --- | --- |
| A | `specs.brief` of the spec the `next` generator makes with the setting off |
| B | `specs.human` of that spec, enriched once with the setting on |
| C | id, rev, goal, out, original and `specs.agent(spec)` as JSON |
| D | id, rev, goal, out and `specs.agent(spec, select)`; the rest by id from `.spec-views/spec.json` |

Tasks replay merged pull requests (`eval/spec-views/tasks.json`). Their own
tests are the hidden check. A person vets each one as asserting observable
behaviour and records it in `vetted_by`. Tasks whose requirements conflict sit
in a separate `undecided` list and never enter the success rate.

`prepare` runs once per task. It extracts the hidden tests to
`raw/eval/spec-views/hidden/` and checks them against the reference: they pass
on the merge commit and fail on its parent. It then makes three model calls:
the generator with the setting off, the enrichment with it on, and D's
selection. Their results are frozen in `eval/spec-views/specs/<task>.json`.
B and C are then checked with `specs.equivalent`, and the goal, scope and
original must appear in B too. Each call's cost and the check go to the journal.
Preparation spends from the same ceilings as the run, and its elapsed time
counts toward the same hours.

`run` refuses a config that differs from the committed file. It also refuses
before the first cell when a frozen record is not the one committed with the
config, its requirements no longer hash to the configured digest, its selection
is not the configured one, its reference check did not separate the answer, or
B and C were not equivalent. It runs every
(task, condition, repetition) in a seeded shuffled order. Each cell starts in a
fresh one-commit repository made from an archive of the merge base. A worktree
would let the session's history reach the merge commit, which is the answer. One
isolated Claude Code session runs with the condition as its whole system prompt
and `Start.` as its only turn. Then the hidden tests are copied in under names
the session never saw, and their digests are checked. A hidden test passes only
when every one of its cases passed and pytest exited cleanly. Next come the
affected test files as the merge base wrote them, and a check for forbidden
paths that also sees files hidden behind ignore rules. Verification stops at the
shared wall deadline; a cell cut there is a ceiling failure, not a verdict.
Finally the workspace is removed.

The session's USD cap is the room left under the ceiling, so the ceiling holds
while cells run in parallel. A cost the CLI never reported is charged at the
cap. A cell stopped by the wall-time ceiling is journalled as a failure. The
production-runner baseline runs last, as condition A with the app's session
flags plus the merge base's Python gate. `report` reads the journal and writes
`raw/eval/spec-views/<experiment>.report.json` and the Markdown report.
`tool/test_spec_views.py` drives `run` with a scripted stub and sends no
model requests.

```bash
python tool/eval/spec_views.py prepare --config eval/spec-views/pilot-1.json   # before the config is committed
python tool/eval/spec_views.py run --config eval/spec-views/pilot-1.json       # also resumes
python tool/eval/spec_views.py report --config eval/spec-views/pilot-1.json
```

### The main experiment

Added 2026-11-08 (`spec-views-main-experiment`). A config whose `kind` is
`main` (`eval/spec-views/main-1.json`) fixes before any run what the pilot
report decided:

- the candidate: D;
- the primary metric: verified task success;
- the pair comparisons;
- the adoption paths;
- the critical-regression rule, relative to A;
- the sample size derived from the pilot's variance;
- the owner's USD and hour ceiling, split between the two arms.

Its tasks come from `eval/spec-views/main-tasks.json`. Every command refuses
the config when one of its tasks is a pilot task or one tuned on
(`held_out`). `run` and `pipeline` also refuse when the first task's tests
(`tool/test_spec_views.py`) fail at the commit the cells would run on, and
they journal that check.

`pipeline` (`tool/eval/spec_views_pipeline.py`) is the full-pipeline arm,
journalled under its own experiment id. Each (task, repetition) starts from the
original text. It generates a fresh spec with the `next` prompt, enriches it
and selects D's requirements. A and D then each run as one cell on that
repetition's spec. A failed step fails only the conditions that needed it.
Every repetition's spec calls count toward its conditions' cost per success.

`report` (`tool/eval/spec_views_main.py`) reads both journals apart. It covers:

- paired success differences;
- cost-per-success ratios, both with task-resampled 95% intervals;
- the secondary measures the config defines.

It judges implementation pass, experiment validity and adoption separately,
by the config's rules. Adoption reads adopt, do not adopt, or further
verification needed. A notes block in the Markdown file survives
regeneration.

```bash
python tool/eval/spec_views.py prepare --config eval/spec-views/main-1.json --tasks eval/spec-views/main-tasks.json
python tool/eval/spec_views.py run --config eval/spec-views/main-1.json        # fixed specs, also resumes
python tool/eval/spec_views.py pipeline --config eval/spec-views/main-1.json   # full pipeline, also resumes
python tool/eval/spec_views.py report --config eval/spec-views/main-1.json     # docs/research/spec-views-main.md
```

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Specification | Shape·check·save of `specs.py`, test | Done |
| 2 | Focus | `next` focus, progress·diagnosis cleanup, collecting materials, extracting blocks | Done |
| 3 | Handoff | [Start], system prompt, completion judgment, gate execution | Done |
| 4 | PR | push, body, `gh pr create`, plan line, result line, noticing `머지됨` | Done |
| 5 | Screen | Focus, card, status of rail | Done |
| 6 | Gate | All of the above verification | Done — PR #15 |
