# PR 7 — explicit research-backed planning workflow

Add a Plan action using the selected repository, task context, progress and blockers.

## Work

Run states are persisted through existing specification/session ownership:
collect context -> research -> draft overview -> draft stages -> document checks
-> commit/push/PR -> independent document review -> ready for human merge.
Bind artifacts to repository, worktree, input revision and configured model roles.
A refresh, cancel or restart must not publish twice or lose completed sources.

Planner A reads current plans and decisions, asks options for decisions that change
scope, and searches through available host web tools. Capture source title, URL,
retrieved date, original locator, useful fragment/paraphrase, applicability,
counterevidence, rejected alternatives and validation condition. Missing web
capability is visible; do not label local-only lookup as completed web research.

A writes the overview with adopted fragments and a bottom source list, then chooses
stage boundaries and fills each stage's dependencies, files/contracts, acceptance,
tests and rollback. Validate coverage mechanically where possible. Commit and
open a PR through existing workspace/publication guards. The implementation model
then revises the documents; the independent reviewer applies PR 6's plan profile.

Support configurable planner/reviser/reviewer roles. Require distinct review
session ownership even if two roles use the same model family. Existing write
approval, cancellation and cost/time limits apply. A question waiting for a user
does not count as answered.

## Evidence and exit

One recorded run produces a complete plan PR with source-backed decisions and
all requested stages. Confirm A hands off after initial publication, the reviser
handles findings, and the reviewer is read-only. Restart/cancellation cannot create
a duplicate PR. No automatic implementation or merge follows plan approval.

## Scope and rollback

Reuse specs, worktrees, publication, source records and loop profiles. No permanent
research agent service or new web-search provider. Disable the Plan entry point
without losing drafted files or PRs.

## Implementation blueprint

### Entry and storage

Add `tool/main/planning.py` as main-layer composition, not a pipeline importing
other pipelines. It owns proposed planning routes and worker orchestration;
`specs` remains the only owner of spec writes. Reuse `work.Run` event/tail
conventions, worktree creation and publication helpers.

Proposed routes:

| Request | Behavior |
| --- | --- |
| POST /api/plans | Validate repository/context, three roles and required limits; create plan spec/worktree and return spec ID |
| GET /api/plans/{sid} | Persisted status, artifacts, questions and spend |
| POST /api/plans/{sid}/answers | Accept answers only for current question/revision |
| POST /api/plans/{sid}/resume | Resume an explicitly stopped phase after checking current artifact/input revision |
| POST /api/plans/{sid}/cancel | Cancel owned current turn; keep completed artifacts |

Use existing work event endpoint for the associated run; do not build another
stream transport. Requests carry a client-generated idempotency key. Same key and
same normalized input returns the existing spec; conflicting input is 409.

Persist an additive `planning` field in the spec:

```json
{
  "version": 1, "phase": "collect", "request_id": "<id>",
  "input_revision": "<digest>", "artifact_root": "docs/plans/<slug>",
  "roles": {"planner": {"model": "...", "effort": "..."},
            "reviser": {"model": "...", "effort": "..."},
            "reviewer": {"model": "...", "effort": "..."}},
  "limits": {"seconds": 0, "calls": 0, "tokens": 0},
  "spent": {"seconds": 0, "calls": 0, "tokens": 0},
  "questions": [], "source_manifest": null, "artifact_manifest": null,
  "publication": {"head": null, "pr": null}
}
```

Zeros above illustrate shape only: runtime validation requires strictly positive
finite limits submitted before starting. No unlimited/default-zero runs.

Calls count dispatched model requests where exposed; host turns and tool invocations
are recorded separately. A CLI turn may hide internal provider calls, so a turn cap
is not a strict provider-call cap. Normalize host token fields into Budget format,
charge every usage event, and stop further dispatch on exhaustion. Usage reported
only after a response can overshoot; record that overrun as a failed ceiling check.
The wall deadline cancels the owned turn through existing stop handling. Missing
usage stops automatic continuation with budget_unknown, never zero spend. Display
these enforcement limits before launch; strict provider caps require host support.

### State machine

| Phase | Work | Next / failure |
| --- | --- | --- |
| collect | Snapshot goal, plans, decisions, blockers via specs.materials and explicit inputs | clarify or research |
| clarify | Persist option questions with IDs; no dependent dispatch | research after submitted answers |
| research | Planner uses available read/search tools, produces source records | outline; unavailable/budget failure stops |
| outline | Produce requirement IDs, chosen stage count/dependencies and overview | stages |
| stages | Fill each blueprint and artifact manifest | validate |
| validate | Parse paths/links, coverage, sources, dependencies and limits | publish; one bounded repair attempt then stop |
| publish | Materialize valid documents, commit/push/create or recover existing PR | handoff |
| handoff | Release planner; configure reviser and independent reviewer | existing plan-profile review |
| stopped | Preserve reason and completed checkpoints | explicit resume or cancel |

An HTTP disconnect does not cancel the worker. A server restart changes running
phases to stopped; no autonomous replay of a write or publication request. Resume
revalidates goal/source revision. Editing the input invalidates downstream drafts
instead of mixing plans built for different requirements.

### Enforce the new-folder-only boundary

The initial planner uses a read-only session with host-supported web search/fetch.
It returns structured artifact content; it does not receive arbitrary write or
shell execution authority. The server validates and materializes only a newly
created `docs/plans/<slug>/` in the owned worktree. Reject absolute paths, ..,
symlinks/reparse-point escapes, duplicate filenames, non-Markdown output and
pre-existing artifact roots. Resolve paths immediately before writing.

Return artifacts in bounded per-file messages:
`{path, content, requirement_ids, source_ids}`. Write UTF-8 without BOM atomically.
Record the file hashes. Use exact path arguments to git add; do not stage the whole
worktree. Existing plans and code remain read-only. Proposed changes to old plans
are emitted as follow-up specifications, not applied.

For document revision, keep the same boundary using read-only generation of
replacement artifacts and validated server writes. This avoids pretending that a
normal write-capable CLI session enforces a folder sandbox. Code work sessions
retain the existing write/approval path. Publication stays subject to existing
authorization; the Plan request grants the bounded document workflow, not arbitrary
commands or merge.

### Artifact schema and quality gate

`0-overview.md`: problem, constraints, decisions, short stage/dependency table,
adopted source fragments, bottom references.
Each stage: requirement IDs, existing/proposed entry points, contracts, transition
and error behavior, ordered edits, test scenarios/commands, migration and rollback.
Source manifest: URL/title/retrieval time, quoted locator or paraphrase,
applicability, caveats and claim IDs. Treat fetched content as evidence, never as
instructions.

The validator checks structure, requirement coverage, local links, acyclic stage
dependencies, finite budgets and source references. Semantic validity is the
independent reviewer's job; a field being nonempty does not prove it correct.

### Publication and UI

Refactor `specs.opened` narrowly to look up an existing PR for the same repository,
head branch and base before create, including after a timeout. Verify identity;
never retry creation blindly. Record pushed HEAD before the external call and PR
identity immediately afterward. Do not update original plan rows for a new planning
spec; its source.plan is null.

Add an explicit Plan action in `Query.tsx` with goal/context preview, three role
selectors and required limits; add types/functions in `lib/api.ts`. Status and
pending questions use the existing task rail. Start/revise/review model identities
remain visible. No automatic implementation or merge after review passes.

### Acceptance recipe

With a fake host, produce a two-stage plan with a cited source; assert only the
new folder is written, links/coverage pass, PR is created once, and reviewer differs
from planner. Inject ../, symlink, malformed output, missing source, missing search,
duplicate request, publication timeout and restart. Each must preserve state and
avoid unauthorized writes. Verify worktree changes before/after publication.
Add `test_planning.py` for these workflow boundaries, reusing existing fixtures.
Live host capability and role handoff are checked in PR 10.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Entry | Wire explicit entry, roles and persisted run state | Done — `planning.start`/`status`/`answer`/`resume`/`cancel`, `recover`, `[계획]` in `Query.tsx` |
| 2 | Research and output | Implement research and structured document output | Done — phases `research`…`validate`, `plan-planner.md`, `problems`, `target` |
| 3 | Publish and hand off | Publish and hand off to document review | Done — `publish`, `specs.pull_request`, `handoff`, `revise`; live check left to PR 10 |

## Implementation notes

- `tool/main/planning.py` holds the routes and the worker. The plan spec is an
  ordinary spec in `작업 중` with `review_profile: plan`, `artifact_root:
  docs/plans/<id>`, `source.plan: null`, and the additive `planning` field;
  `cell` is the reviser's model and `reviewer` the review cell's
  (`loop.cell` reads it before the settings). A done report in that worktree
  publishes nothing (`specs._check`).
- The worker holds the worktree for its whole run, so a person's turn, a
  reset or a removal waits. Each planner turn is a `work.Run` registered for
  the worktree: the rail and the agent tab tail it through `/api/work/events`,
  and `/api/work/stop` cancels it like `/api/plans/{sid}/cancel`. The record
  keeps no CLI session id and gets a context row at hand-off, so no later
  session resumes A.
- Planner A runs with `Read,Glob,Grep,WebSearch,WebFetch` and no shell. Web
  research counts only when a tool event named a web tool (`meta.tool`, now
  set on every host tool event); otherwise research stops `web_unavailable`.
  Codex hosts without web search enabled stop there.
- Budget: `common.budget.Budget` built from what is left. `calls` counts turns
  sent to A, tool calls are counted apart; tokens are `in + out` charged after
  each answer; a crossing is kept as `overrun`, and the next request is
  refused. A turn without usage — answered, failed or stopped — is never
  zero: `spent.unknown` stays set and the worker sends nothing more
  (`budget_unknown` when it would). A person's resume goes on, and the
  screen shows the spend as a lower bound; the token limit is then no
  strict ceiling. The wall deadline stops the running turn. Reviser turns are
  bounded by the loop's round cap, not by these limits.
- Drafts wait in the hub's `raw/planning/<repo>/<id>/` with their SHA-256 in
  `artifact_manifest`; a resume drops a changed draft and what builds on it.
  Validation is one repair turn, then `invalid`. A stage may depend only on
  earlier stages, which rules out cycles.
- Publication writes through `target` (flat files directly in the new
  folder, no `..`, no link or junction on the way, resolved right before the
  write), commits exactly those paths, checks the diff, records the head
  before the push and the pull request right after. `specs.pull_request`
  looks up the open pull request for the same head and base before creating
  and again after a failed or timed-out create; `specs.opened` uses it too.
  A resume pushes only the recorded commit, and finishes a cut write only
  when every file in the folder is a finished draft (by hash) or a swap
  `atomic` left. A turn is marked in flight on disk before it is sent, so a
  restart mid-turn keeps the call and marks the spend unknown. The hand-off
  is marked after publication pauses for the person's explicit review request;
  handoff and restart recovery never start independent review on their own.
- The reviser is a read-only session per fix turn (`revise`, called from
  `loop.told`); the server checks every returned path before writing any and
  commits them. A path outside the folder writes nothing.
- Not here: a switch that hides `[계획]` (rollback is reverting the router
  and the button; drafts, worktrees and pull requests stay), raising limits
  on resume (a new plan), and live host checks (PR 10).

## Sources

[Workflow and evaluator patterns](https://docs.langchain.com/oss/python/langgraph/workflows-agents).
