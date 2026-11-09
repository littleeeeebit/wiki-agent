# Step 3 — Spec conversation, backend design, R1 and the backend build

The overall design is in the [overview](0-overview.md); this step starts
where [step 2](2-bootstrap.md) leaves the run: a spec in state `spec` with a
worktree on its branch.

Goal. A few turns of options turn the owner's sentence into scenarios and a
glossary; the agent designs the backend alone and shows it as one Korean sheet
(R1); after approval it writes the contract tests first, implements against
them, passes the gate and an independent read-only review, and hands a working
backend to step 4 without asking anything.

## What already exists and is reused

| Need | Existing piece | Where |
| --- | --- | --- |
| Requirements with status and grounds | `requirements` items: `id`, `text`, `source`, `status` (`confirmed`, `assumed`, `undecided`, `not_applicable`), conditions, `verify` | `tool/main/spec_views.py` |
| A requirement the run cannot pass | `spec_views.blocked` names a dependency on an undecided item | `tool/main/spec_views.py` |
| Asking with options inside an answer | The `choices` block and its buttons | `tool/main/specs.py` (`blocks`, `answered`) |
| A write session on a spec | The work session with the spec as system prompt, `done-report`, and the server rerunning `gate_cmd` | `tool/main/work.py`, `tool/prompts/work-spec.md` |
| An independent reading before the rest | `prerequisite_review`: committed instruction, declared inputs, JSON verdict path; read-only | `tool/main/prerequisite_review.py` |
| Blocks returned and written by the server | Planner pattern: the model returns named JSON blocks, the server validates and writes only inside a named folder | `tool/main/planning.py` |

`SPEC_VIEWS` in the hub's `.env` stays off. The oneshot run uses the
requirements shape and its own renderer; it does not switch on the experiment's
agent view, which is still under evaluation.

## Stage states

`spec → design → R1 → tests → build → backend review → split`. `split` is
where step 4 begins. Stops follow step 2's table, plus `gate` (the build could
not get the gate green within its attempts) and `review` (the read-only review
refused twice).

## S2 — Spec conversation

Runs in the run's own chat, with the `next`-style blocks.

1. The first turn reads the owner's sentence, the restatement and the profile,
   and asks at most three `choices` at a time: who uses it, the core
   scenarios, and what is out.
2. Each answer is folded into `requirements`. A scenario becomes one item
   whose `text` is written as "~하면 ~된다" and whose `postconditions` carry
   the observable result; `source.kind` is `original` when it comes from the
   owner's words, `decision` when from an answer, `policy` when the agent
   added it.
3. The glossary is a list of `{term, meaning}` taken from the owner's words.
   The agent may add terms but may not rename one the owner used.
4. The stage ends when the server finds no `undecided` item and every
   `original` item has at least one scenario. The agent cannot end it by
   saying so.

The owner's turns are the only human input until R1, so S2 asks everything
the design depends on. What the agent still has to guess later becomes an
`assumed` item, which R1 lists first.

## S3 — Backend design

One model turn in the worktree, read-only, returning blocks. The server
validates them and writes the files.

| Block | Shape | Becomes |
| --- | --- | --- |
| `oneshot-entities` | `[{name, fields: [{name, type, rule}], relations}]` | An entity diagram (Mermaid) and the storage schema |
| `oneshot-api` | `[{method, path, does, request, response, errors, requirement_ids}]` | The API table and the OpenAPI document |
| `oneshot-flows` | `[{name, steps}]` for the main scenarios | Sequence diagrams |
| `oneshot-assumptions` | `[{id, what, why, rejected}]` | `assumed` requirements |
| `oneshot-tests` | `[{requirement_id, test_name, given, when, then}]` | The contract test plan |

Server checks before R1 is shown, each a refusal sent back to the model once:

- Every scenario requirement is covered by an endpoint and a test.
- Every endpoint names the requirements it serves; one serving nothing is
  refused as out of scope.
- No entity field or endpoint uses a word that contradicts the glossary.
- The profile is respected: the chosen data store, authentication and
  framework.

Files written into the worktree and committed: `docs/design/backend.md` (the
sheet's long form), `docs/design/api.yaml`. These are the new project's own
documents and follow its docs-language choice from S1.

## R1 — the backend sheet

### Language

The decision is Korean written directly, not translated. So every
person-facing string in the S2 and S3 blocks (`text` of requirements,
`does`, `what`, `why`, the glossary) is written in Korean by the model in the
same turn, under the style rules in the prompt. Identifiers, paths, types and
status values stay as they are. This is the new project's content, read by its
owner, like a pull request body, so it is not hub text that
`operator/english-progress` governs.

### Rendering

Code renders the sheet from the stored blocks; no model takes part, so the
sheet and the record cannot disagree. Order, top to bottom:

1. One line: what will be built, quoting the owner's sentence.
2. Assumptions the agent made, each with what it rejected. This is the part to
   read if nothing else is read.
3. What cannot easily be undone later: the storage schema's keys, the
   authentication choice, anything the profile marked public.
4. The entity diagram and the API table (method, path, one line).
5. Scenarios as "~하면 ~된다", each with the test that will check it.
6. Folded: flows, full request and response shapes, the glossary.

Length budget: sections 1–5 fit one screen at the app's default size, at most
seven assumptions and at most twelve API rows unfolded. Over the budget, the
server sends the blocks back once asking to merge or defer items; still over,
the extra rows are folded and the sheet says how many.

### The translationese check

Deterministic, in `tool/main/oneshot.py`, run on every person-facing string
before the sheet is shown.

| Pattern | Example |
| --- | --- |
| Double passive | `~되어지다`, `~되어진다` |
| Noun chains | three or more `~의` in one clause |
| Calque connectives | `~에 대해`, `~에 있어서`, `~를 통해` used for "via" |
| Nominalised endings | `~하는 것이다`, `~할 것이다` closing a plain statement |
| Plural marker on things | `~들` on non-human nouns |
| English parentheses | an English word in parentheses after a Korean word that already says it |

A string over the threshold goes back to the model once with the matched spans
named; the check counts and names, it never rewrites. The threshold and the
pattern list are the knobs; both are tuned on real sheets from the first
runs, not guessed further now.

### Approval

Per item: each assumption, endpoint row and scenario has approve or edit with a
comment. Edited items go back in one turn with only their ids; the returned
items replace them and the sheet is shown again with the changed items marked
and the rest folded. Approving everything moves the run to `tests`. The
number of rounds is recorded on the spec.

## S4 — Backend build

No human input. Three parts, each checked by the server rather than by the
agent's word.

### Tests first

A write session receives the approved design and the test plan and writes
only tests. The server then checks:

- The diff touches only test paths and the test configuration.
- Collection succeeds: the tests are importable and name the planned tests.
- The planned tests fail, because nothing is implemented yet. A planned test
  that already passes is reported back as not testing anything.

### Implementation

The same work session machinery as any spec: the spec's `done` lists the
gate and the scenarios; the session writes `done-report`; the server reruns
`gate_cmd` in the worktree. The session may not edit the contract tests from
the first part; the server holds those files to the existing test-contract
check (`docs/plans/refactor/7-purpose-modes.md`, "Test contract"), so a test
can be added but a planned one cannot be weakened. Up to three gate attempts,
then `stopped` with `gate`.

### Backend review

A `prerequisite_review` with a committed instruction under
`docs/design/review-backend.md`, the design files and the diff since the base
commit as inputs, and its verdict at `artifacts/review/backend.json`. A
refusal goes back to the work session as one turn of findings, the gate runs
again, and the review runs once more. A second refusal stops the run with
`review`. An approval moves the run to `split`.

This is a reading between agents, not a human review; the owner sees its
verdict folded under R2.

## Prompts

`tool/prompts/oneshot-spec.md` (S2), `oneshot-design.md` (S3),
`oneshot-tests.md` (S4 tests first), and the instruction for the backend
review. English instructions, with a section of Korean style rules and the
requirement that person-facing strings are written in Korean directly.

## Completion

- New tests in `tool/test_oneshot.py`: S2 refusing to end with an undecided
  item or an uncovered original item; each S3 check refusing; the sheet's
  order and length budget; each translationese pattern counted and named on
  fixtures, and clean Korean passing; per-item edits replacing only those
  items; the tests-first checks (non-test path refused, passing planned test
  reported); a weakened contract test refused; review refusal looping once
  then stopping.
- The repository's gate, `tool/lint.py --check` and `tool/debt.py check`.
- One real run from step 2's throwaway repository through `split`, with the
  R1 sheet's screenshot and the review verdict in the pull request.

## Decided with the owner

Nothing yet for this step.
