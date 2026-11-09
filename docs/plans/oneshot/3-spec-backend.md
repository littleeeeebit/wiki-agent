# Step 3 — Spec conversation, backend design, R1 and the backend build

The overall design is in the [overview](0-overview.md); this step starts
where [step 2](2-bootstrap.md) leaves the run: a spec in state `spec` with a
worktree on its branch.

Goal. A few turns of options turn the owner's sentence into scenarios and a
glossary; the agent designs the backend alone and shows it as one Korean sheet
(R1); after approval it writes the contract tests first, implements against
them, passes the gate and an independent read-only review, and hands a working
backend to step 4 without asking anything and without publishing anything:
nothing in this step pushes the branch or opens a pull request.

## What already exists and is reused

| Need | Existing piece | Where |
| --- | --- | --- |
| Requirements with status and grounds | `requirements` items: `id`, `text`, `source`, `status` (`confirmed`, `assumed`, `undecided`, `not_applicable`), conditions, `verify` | `tool/main/spec_views.py` |
| A requirement the run cannot pass | `spec_views.blocked` names a dependency on an undecided item | `tool/main/spec_views.py` |
| Asking with options inside an answer | The `choices` block and its buttons | `tool/main/specs.py` (`blocks`, `answered`) |
| A write session on a spec | The work session with the spec as system prompt, `done-report`, and the server rerunning `gate_cmd` | `tool/main/work.py`, `tool/prompts/work-spec.md` |
| Leaving the done-report before the pull request | The refactor branch: a spec with its own completion returns before `pr_turn` (`refactor_run.published`) | `tool/main/specs.py:1773` |
| Read-only sessions | `agent.read_tools`, as the review and prerequisite readings use | `tool/agent` |
| Syntax comparison of test files | `refactor_run.equivalent`, the whole-file AST comparison the refactor contract uses | `tool/main/refactor_run.py:473` |
| Blocks returned and written by the server | Planner pattern: the model returns named JSON blocks, the server validates and writes only inside a named folder | `tool/main/planning.py` |

Two existing pieces are deliberately not reused as they are.
`prerequisite_review` runs only inside a published pull request's Review Loop
and, once it passes, tells the session to implement everything and push; S4
has no pull request yet and must not push. The refactor test contract
(`refactor_run.contract`) reads `spec["refactor"]` and compares against
`start_head`, where the oneshot's contract tests do not exist yet, so it would
skip them as new files. S4 below reuses only their parts.

`SPEC_VIEWS` in the hub's `.env` stays off. The oneshot run uses the
requirements shape and its own renderer; it does not switch on the experiment's
agent view, which is still under evaluation.

## Stage states

`spec → design → r1 → tests → build → review → split`, held in the spec's
`oneshot.stage`. `split` is where step 4 begins. Stops follow step 2's
table, plus `tests` (the tests-first turn was refused three times), `gate`
(the build could not pass its checks within its attempts) and `review` (the
read-only review refused twice).

The step ships as three pull requests, in order:

| Part | Holds |
| --- | --- |
| 3a | S2, S3 and R1: `tool/main/oneshot_spec.py` (stages and HTTP), `tool/main/oneshot_sheet.py` (checks, sheet, translationese) |
| 3b | S4 tests first and the build, with the contract-test protection: `tool/main/oneshot_build.py` |
| 3c | The backend review: `tool/main/oneshot_review.py` |

The bootstrap's last act starts the first S2 turn. Each stage's worker hands
over to the next one itself; a hand-over refused because the server is going
down leaves the new stage stopped with `restart`. At start-up, a spec whose
run has written its commit point but whose first S2 turn never ran is stopped
with `restart` too, so `[재개]` starts it. A worker that fails in a way no
check foresaw stops with `block` and the exception named.

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
3. The glossary is a list of `{term, meaning, avoid}` taken from the owner's
   words; `avoid` lists the words not to use for the same thing. The agent
   may add terms but may not rename or drop one the owner used: a term of the
   previous glossary that appears in the owner's sentence or answers must
   still be there.
4. The stage ends when the server finds no `undecided` item and every
   `original` item has at least one scenario (a requirement with
   `postconditions` that is the item or names it in `depends_on`). The server
   judges the requirements as it will store them: a kept requirement keeps
   its stored `source` whatever the turn claims, an `original` item may not
   be dropped, and a cycle among the requirements' `depends_on` is refused
   here, where the conversation can still fix it. The agent cannot end it by saying so; a `choices` block in
   the turn that ends it is dropped. Until then the server records what is
   still open. The conversation keeps the agent's prose without its blocks.

Each turn replaces the requirements and the glossary whole in an
`oneshot-spec` block and may ask at most three questions in one `choices`
block. The owner answers through `POST /api/oneshot/spec/{repo}/say`.

The owner's turns are the only human input until R1, so S2 asks everything
the design depends on. What the agent still has to guess later becomes an
`assumed` item, which R1 lists first.

## S3 — Backend design

One model turn in the worktree, read-only, returning blocks. The server
validates them and writes the files.

| Block | Shape | Becomes |
| --- | --- | --- |
| `oneshot-entities` | `[{id: E1…, name, fields: [{name, type, rule, key}], relations, depends_on}]` | An entity diagram (Mermaid) and the storage keys |
| `oneshot-api` | `[{id: P1…, method, path, does, request, response, errors, depends_on}]` | The API table and the OpenAPI document |
| `oneshot-flows` | `[{id: F1…, name, steps: [{from, to, say, api}], depends_on}]` for the main scenarios | Sequence diagrams |
| `oneshot-assumptions` | `[{id: A1…, what, why, rejected, depends_on}]` | The sheet's first section, and part of the approved design the build receives |
| `oneshot-tests` | `[{id: T1…, test_name, given, when, then, depends_on}]` | The contract test plan |

Every item has a stable `id`, and `depends_on` lists the ids of the items it
was derived from: requirements, assumptions, entities and endpoints. The
server checks the references: every id exists, there is no cycle through the
requirements and the items together, an endpoint depends on at least one
requirement, a test on its requirement and
on every endpoint it calls, a flow on the endpoints in its steps, and
anything shaped by an assumption names it (the prompt asks for this; no code
can tell what an assumption shaped). A missing or dangling reference
refuses the block like any other S3 check.

Server checks before R1 is shown, each a refusal sent back to the model once:

- Every scenario requirement is covered by an endpoint and a test.
- Every endpoint names the requirements it serves; one serving nothing is
  refused as out of scope. No two endpoints share a method and path, so the
  OpenAPI document holds every approved row.
- A path uses letters, digits and `-_.{}/` only; nested lists (`relations`,
  `errors`, `avoid`) are lists of strings, and a field's `key` is a boolean. A wrong shape is a refusal, never
  a failed worker.
- No entity name, field, path or `does` uses a word the glossary's `avoid`
  lists.
- The profile is respected. Authentication is checked on whole path
  segments (`/login`, `/sessions`, `/auth`; not `/authors`): with `none`, no
  such endpoint, and with `password` or `oauth`, at least one. The data store and the framework are stated on the sheet and
  in the documents from the profile, not read from the blocks.

These refusals go back to the model once and stop the run with `block` on the
second. The length budget and the translationese check below also go back
once, but a second answer still over them is shown with the warnings listed.

When R1 is approved, the server writes into the worktree and commits:
`docs/design/backend.md` (the sheet) and `docs/design/api.yaml` (an OpenAPI
3.1 document written as JSON, which is valid YAML). It first checks that the
worktree is still the Git root on the oneshot branch and that the output
folder and both files resolve inside it, neither file a link, and the commit names the two files, so nothing
else staged meanwhile goes in. It records the commit as
`oneshot.design_head`. They are the new project's own documents.

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

Deterministic, in `tool/main/oneshot_sheet.py`, run on every person-facing
string before the sheet is shown.

| Pattern | Example |
| --- | --- |
| Double passive | `~되어지다`, `~되어진다` |
| Noun chains | three or more `~의` in one clause |
| Calque connectives | `~에 대해`, `~에 있어서`, `~를 통해` used for "via" |
| Nominalised endings | `~하는 것이다`, `~할 것이다` closing a plain statement |
| Plural marker on things | `~들` on non-human nouns |
| English parentheses | an English word in parentheses after a Korean word that already says it |

A string over the threshold goes back to the model once with the matched spans
named; the check counts and names, it never rewrites. The threshold starts at
one match per string. Plural `~들` skips a list of words for people, and `~의`
chains skip nouns that end in `의` (`회의`, `동의`, …). The threshold, the
pattern list and the skip lists are the knobs; all are tuned on real sheets
from the first runs, not guessed further now.

### Approval

Per item: each assumption, endpoint row and scenario has approve or edit with a
comment, and the server moves the run on only when every item on the sheet,
folded or not, has a recorded decision (step 5 gives folded sections an
explicit approve-all).

Items depend on each other through `depends_on` (S3 above), so an edit to a
requirement, an assumption, an entity or an endpoint reaches the tests and
flows built on it. An edit therefore reaches further than the edited item:

1. The server collects the edited items and every item whose `depends_on`
   reaches one of them, transitively.
2. One turn receives the owner's comments and that whole set, and returns
   replacements for all of it; the S3 checks run again on the full design.
   An item outside the set that changed or disappeared refuses the answer,
   because its approval survives. Changed requirements follow S2's rules:
   stored sources, no original dropped, no owner's term renamed; a new one
   counts as a decision.
3. Every item in the set loses its approval, whether or not its text changed.
4. The sheet is shown again with the set marked: edited by the owner, changed
   as a consequence, or unchanged but needing approval again.

The number of rounds is recorded on the spec. The decisions arrive in one
`POST /api/oneshot/spec/{repo}/r1`, each item `{approve: true}` or `{edit:
comment}`; the server refuses a submission that leaves any item without a
decision. The items needing a decision are every requirement, a scenario or
not (a condition without `postconditions` is listed under the scenarios), and
every entity, endpoint, flow, assumption and test. An approval survives a
regeneration only when its item is outside the reached set and unchanged. An
edit turn that stops leaves the sheet as it was, and `[재개]` returns to it.

## S4 — Backend build

No human input. Three parts, each checked by the server rather than by the
agent's word.

A oneshot spec's turn never reaches `pr_turn` before S8. `specs._check` hands
every turn in a oneshot spec's worktree to `oneshot_build.checked` before any
general branch (a `spec-update`, the `done-report` check, the round gate, the
pull request). Success in `tests` means the planned tests fail, which the
general gate would count as a failure. In `build` the general round gate is
scoped (`git diff --check` for an unmapped change), so the stage reads its
own report and runs the full gate itself. A person's turn in the worktree,
or a turn in a stage S4 does not own, changes nothing. The branch stays
local until S8.

The turns are the ordinary work session's (`work.session` on the role model
`next`), sent as an `oneshot_build.Turn`. Each is checked against the run's
ledger before it is sent and charged when it ends, its cost unknown when the
CLI reported none. A turn that ends without reaching its check (stopped,
failed, cut by a shutdown) stops the stage with `block` or `restart` and the
reason. A refusal goes back to the session as the next turn with the reason;
the third refusal of a stage stops it (`tests` or `gate`). `[재개]` sends the
stage's turn again with fresh attempts, and start-up stops a stage whose turn
was running with `restart`.

Each turn names itself in `oneshot.inflight` with a token. A turn whose end
arrives after a newer turn was sent is still charged, but it clears and stops
nothing: the in-flight entry is the newer turn's. A turn `work.begin` refuses
before its thread starts ran no model, so it is not charged and leaves
nothing in flight, including when `work.begin` ends the turn itself before
refusing it: that end arrives on the thread that sent the turn, never on the
turn's own. `[재개]` is refused (409) while a stopped turn is still in
flight, because the next turn would lose that turn's charge. The next turn
a check hands back is sent only if no stop arrived by the time
`work.run_turn` sends it; a stop during the check or while the turn is
recorded leaves the stage stopped and resumable.

### Tests first

After R1's approval, a write session receives the planned tests
(`oneshot-tests.md`), reads the committed design, and writes only tests.
The server then checks:

- Since the design commit, only test paths (`tests/`, `test_*.py`,
  `*_test.py`, `conftest.py`) and the test configuration (`pyproject.toml`,
  `uv.lock`, `pytest.ini`) changed, committed or not.
- Collection succeeds and names every planned test in the changed test
  files. Matching is by the function's own name, but what is kept is each
  node id pytest itself gave, parameter cases and inherited classes
  included; a same-named test outside those files is not a planned test. The
  prompt asks for the application to be imported in the test body, so the
  tests collect before it exists.
- Every collected node runs and fails, in its own body, because the
  application is not there: the exception, or one it was raised from, is a
  `ModuleNotFoundError` for `app` or a module under it. A node that passes
  checks nothing, a skipped one neither, an error in setup or teardown is
  not a red test, and a failure for any other reason, an `ImportError` from
  another module included, means the test does not wait for the
  application.

Every pytest run of the contract loads `tool/main/oneshot_probe.py` by name
(`-p oneshot_probe`), copied to a folder of its own under the run's raw
folder, which is the only path it adds. The probe is what the server reads,
never a report rebuilt afterwards: pytest's own node id of every collected
test, and for each phase its outcome, whether the test's own function was
entered, whether anything replaced how pytest runs a test, and whether the
failure was the missing application. The probe keeps an `async def` test
a coroutine function, so the plugin that runs it still does, and counts it
entered only when its body starts. Each item type is compared with its own
`runtest` as collection left it, so a `unittest.TestCase` keeps pytest's
runner for it. pytest is given the test modules alone (`test_*.py`,
`*_test.py`); a data file the tests read is pinned with them but never
collected. A run of chosen nodes is given the modules of those nodes and has
the probe deselect every other test, so no node id passes through the
command line. A phase that is skipped, the teardown included, makes the
whole node skipped. Plugin autoloading is off
(`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`), so a plugin the tests need is named in
the configuration by its plugin name (`-p anyio`), which the gate, run with
autoloading on, accepts too; `PYTEST_ADDOPTS` is empty and no bytecode is
written. The changed files, HEAD and the set of changed paths are read before
the runs and again after them; any difference is a refusal, so a file the
run itself rewrote is never committed.

When all three hold, the server commits those paths alone, naming them to
the commit so nothing else staged rides along, checks that none of them is
left changed, and records the commit as `oneshot.tests_head`, the test files
as `oneshot.contract` and the node ids as `oneshot.nodes`. That commit, not
the base commit, is what later changes are compared against.
The spec's `done` becomes the gate and one line per scenario
(`R1: <text>`), and the build turn follows.

### Implementation

The build turn (`oneshot-build.md`) names the pinned files and the checks.
Its `done-report` must cover every `done` item (`specs.shortfall`); the
server then runs `gate_cmd` itself on the clean, committed HEAD
(`specs.judge`). The contract tests are protected against `tests_head` in
two ways, because a file that is unchanged is not yet a test that ran:

1. Unchanged. Before each gate, `oneshot_build` reads each pinned file at
   `tests_head` and in the worktree and compares them with
   `refactor_run.equivalent`, the whole-file AST comparison: nothing may be
   added to a pinned file either, so a module-level `pytest.skip(...,
   allow_module_level=True)` or a new `pytestmark` appended after the tests
   is a refusal. (`_kept` is not enough here: it allows statements added
   after the old ones.) A deleted, unreadable or unparsable pinned file is a
   refusal. New tests go in new files. The pytest configuration is pinned
   with them: a `pytest.ini`, `.pytest.ini`, `tox.ini`, `setup.cfg` or
   `conftest.py` added or changed since `tests_head` (other than a pinned
   file that is still equivalent) is a refusal, and so is a package's
   `__init__.py` in or above a contract file's folder, which runs before its
   tests do, and a `[tool.pytest]` table in `pyproject.toml` that differs
   from the one at `tests_head`.
2. Ran and passed. After the gate passes, `oneshot_build` runs exactly the
   node ids in `oneshot.nodes` through the probe. Each pinned node must be
   there, passed, with its own body entered and pytest's way of running a
   test untouched. Skipped, xfailed, deselected or missing is a failure,
   whatever the gate's exit code said; a same-named test in another file
   stands in for nothing, and an application that replaces how pytest runs
   tests while it is imported is caught here.

Up to three attempts, then `stopped` with `gate`. When all checks hold, the
stage becomes `review` with the HEAD as `oneshot.built_head`; that is where
the backend review (3c) begins.

### Backend review

A oneshot reading of its own (`tool/main/oneshot_review.py`), between
agents and before any pull request. The build's success hands over to it as
a worker of the spec's store, like S2 and S3:

1. The server freezes the review input: the approved design, the
   requirements, and the diff from the base commit (`start_head`) to the
   worktree's HEAD, with that HEAD named. The HEAD must be the
   `built_head` the build checks passed; any other stops with `review`. A
   copy of the diff is kept in the run's folder as the record, but the
   reviewer never reads it there: the read-only tools reach only tracked
   files of the worktree (`agent/read_tools.py`, `git ls-files`).
2. Delivery: the design, the requirements, the profile and the pinned files
   go into the review turn's prompt itself; the diff is written with
   `read_tools.save_diff` to the fixed path inside the worktree's Git
   directory that the existing `pr_diff` tool reads, as Review Loop does (a
   Claude reviewer is told that file's path). The source files are tracked,
   so `repo_read` reaches them. No read outside the repository is added.
3. A read-only session (`Read,Glob,Grep`, on the model and effort the Review
   Loop settings name, through `oneshot.ROLES["review"]`) returns an
   `oneshot-review` block (`oneshot-review.md`): `{verdict, head,
   findings: [{location, severity, reason, evidence}]}`. The server refuses
   the block, and asks again once, when `head` is not the HEAD it was given
   (a verdict on another commit counts as none), when an approval carries a
   P0 or P1 finding, or when a refusal carries none. The turn is metered as
   the role `review`.
4. On a refusal, the P0 and P1 findings go to the work session as one build
   turn with fresh attempts; the contract-test check and the gate run
   again; the review runs once more on the new HEAD with a new frozen input.
   A second refusal stops the run with `review`; `[재개]` counts refusals
   afresh and reads the current HEAD again.
5. An approval of the HEAD the input named moves the run to `split`. Every
   verdict is kept in `oneshot.reviews`.

Nothing in this reading pushes or opens a pull request. The owner sees its
verdict folded under R2.

## Prompts

`tool/prompts/oneshot-spec.md` (S2), `oneshot-design.md` (S3),
`oneshot-tests.md` (S4 tests first), `oneshot-build.md` (S4 build) and
`oneshot-review.md` (the backend review). The two S4 prompts are the turn's
text, after the work session's own system prompt; they forbid pushing,
opening a pull request and asking the person, which that prompt allows. English instructions, with a section of Korean style rules and the
requirement that person-facing strings are written in Korean directly.

## Completion

- New tests in `tool/test_oneshot.py`: S2 refusing to end with an undecided
  item or an uncovered original item; each S3 check refusing; the sheet's
  order and length budget; each translationese pattern counted and named on
  fixtures, and clean Korean passing; a submission with any item undecided,
  folded or not, refused by the server; dangling, missing and cyclic
  `depends_on` refused; an edited scenario, an edited endpoint response (a
  field renamed) and an edited assumption each regenerating the tests and
  flows that depend on them and resetting their approvals; a `tests` turn
  whose planned tests fail reaching `build` without being counted as a gate
  failure or an incomplete spec; the tests-first checks (non-test path
  refused, passing planned test reported, a setup error and a failure that is
  not the missing application refused, each parameter case pinned, nothing
  staged outside the test files committed, a file changed during the run
  refused, a stop during the check sending no build turn); a late end of an
  earlier turn leaving the newer turn's in-flight entry alone, a turn
  `work.begin` refused leaving nothing in flight, and a resume refused while
  a turn is in flight, and a stop after the check sending no next turn; a
  case pinned by its own node id through an inherited class and a parameter
  id holding `::`; an `ImportError` from another module refused as red; a
  new `conftest.py`, `.pytest.ini` or package `__init__.py`, a replaced test
  body and a `[tool.pytest]` change refused in the build, and an application
  that replaces pytest's test runner caught by the probe; a same-named passing test in
  another file not standing in for a failing contract test; a contract test created after the
  base commit then weakened
  (an expected value changed, a skip mark, a deleted test) refused against
  `tests_head`, and a deleted or unparsable pinned file refused; a
  module-level skip appended to an otherwise unchanged pinned file refused;
  a planned test skipped, xfailed or not collected counted as failing while
  the gate itself is green; review
  refusal looping once then stopping; a verdict on an older HEAD ignored; the
  review turn, run with the real Codex read profile, receiving the design
  and requirements in its prompt and the diff through `pr_diff`, and
  returning a verdict on the current HEAD; a full S4 run that ends in `split` with no push, no
  pull request and no Review Loop.
- The repository's gate, `tool/lint.py --check` and `tool/debt.py check`.
- One real run from step 2's throwaway repository through `split`, with the
  R1 sheet's screenshot and the review verdict in the pull request.

## Decided with the owner

Nothing yet for this step.
