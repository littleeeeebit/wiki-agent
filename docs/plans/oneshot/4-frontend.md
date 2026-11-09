# Step 4 — Feature split, frontend design, R2 and the self-reviewing build

The overall design is in the [overview](0-overview.md); this step starts at
`split`, where [step 3](3-spec-backend.md) leaves a reviewed backend.

Goal. Every user-facing action is placed by the owner into one of four
frequency classes; the agent designs screens that obey that placement and
shows them as screenshots (R2); after approval it builds the frontend and
improves it by looking at its own screenshots, judged by a separate read-only
critic, until a stop rule holds; R3 receives final screenshots and a recorded
walkthrough of each core scenario.

## Stage states

`split → frontend design → R2 → frontend build ⇄ critique → R3`. After R3
approval the run moves to `pull request` ([step 5](5-screen-pr.md)). Stops
follow step 2's table, plus `critique` (the round cap was reached with
high-severity findings left).

## S5 — Feature split

### The inventory comes from code

The list of actions is read from the built backend, not written by the model,
so nothing is forgotten and nothing invented: the OpenAPI document the
framework serves (FastAPI's `openapi.json`), or the CLI's command list for a
`cli` run. One model turn only names each action in the owner's words (from
the glossary), groups trivially related endpoints into one feature (list and
filter are one), and proposes a class with a one-line reason.

| Class | Placement rule |
| --- | --- |
| `every` | On the primary screen, visible without a click |
| `sometimes` | One click away: a menu, a drawer, a secondary tab |
| `rarely` | Settings or an admin area |
| `never` | Not in the UI; API or CLI only |

### The board

The owner gets a drag board: four columns, each feature a card placed where
the model proposed, its reason under it. One more field above the board: "the
first screen is for …", one line, prefilled from S0's `first_screen`.
Submitting the board writes `frequency: {feature_id: class}` and
`first_screen` to the spec. This is a conversation stage, not a review: it
asks a question the owner alone can answer, and only once.

Every feature gets a stable id here (`feature_id`), used from now on in the
design, in the code as `data-feature`, and in the checks.

## S6 — Frontend design

One model turn in the worktree that returns blocks and writes low-fidelity
mock pages; the server validates the blocks and shoots the mocks.

| Block or file | Shape | Becomes |
| --- | --- | --- |
| `oneshot-screens` | `[{route, purpose, features: [feature_id], primary: bool}]` | The screen map |
| `oneshot-states` | Per screen: what empty, loading, error and full look like, one line each | The state list R2 shows and S7 shoots |
| `oneshot-tokens` | Two or three palette proposals, a type scale, spacing | The palette choice on R2 |
| `design/mock/<route>.html` | Static grey-box pages, no real data | Screenshots on R2 |

Server checks, each a refusal sent back once:

- Exactly one screen is `primary`, and it carries every `every` feature.
- No `rarely` or `never` feature appears on the primary screen.
- Every `sometimes` feature is on a screen or element reachable in one step
  from the primary screen.
- Every feature except `never` appears on some screen.
- Each mock page exists and renders without console errors.

The server shoots each mock with Playwright at the profile's viewports
(desktop, and phone when responsive was chosen).

## R2 — the frontend sheet

Rendered by code like R1, in Korean, the same per-item approval and the same
translationese check on every person-facing string. Order:

1. The first-screen line and the primary screen's screenshot.
2. The frequency map as the owner submitted it, beside where each feature
   landed. Any placement the design could not honour is listed here.
3. The other screens' screenshots, one row per screen with its purpose.
4. The palette proposals as swatches on the primary screenshot; the owner
   picks one.
5. Folded: states per screen, the backend review verdict from S4.

An edit to a placement may reopen S5's board instead of S6, when the owner
says the class was wrong rather than the screen.

## S7 — Build, look, critique, fix

### Build

A write session receives the approved screen map, states, chosen palette and
the frequency map. Two rules it must follow, both checked by the server:

- Every control for a feature carries `data-feature="<feature_id>"`.
- The adapter's `live_cmd` starts the backend and the frontend together;
  the session fills it, since step 2 left it empty, and the gate gains the
  frontend's tests, in the existing `gate` shell form with its PowerShell 5
  variant.

### Look: the shooting harness

A server routine, not a model. With `live_cmd` running it drives Playwright
over every route, at each viewport, in each state:

| State | How it is produced without changing the app |
| --- | --- |
| empty | A fresh database |
| full | The fixture the contract tests use, loaded through the API |
| loading | Routes intercepted and delayed |
| error | Routes intercepted to return 500 |

and records, per shot:

- Console errors and unhandled rejections.
- Horizontal overflow (`scrollWidth` above the viewport width) and elements
  outside the viewport.
- An axe-core accessibility pass, contrast included.
- The placement check from the DOM: on the primary route every `every`
  feature's `data-feature` element is visible without interaction, no
  `rarely` or `never` one is present, and every `sometimes` one becomes
  visible after one click.
- The core scenarios run as end-to-end tests clicking through, recorded as
  video.

The mechanical findings are deterministic; a placement or console finding is
high severity by definition and never left to the critic's judgement.

### Critique: a separate read-only session

The owner chose a critic separate from the builder. It is a read-only
session that receives the screenshots as images, the mechanical findings, the
R2 sheet and the frequency map, and judges in the `design-pass` order:
purpose and layout, measured spacing and alignment, typography, colour,
leftover whitespace. It returns an `oneshot-critique` block:
`[{severity: "high"|"medium"|"low", route, viewport, state, what, fix}]`. It
cannot write files and never sees the builder's reasoning, only the result.

### Fix and repeat

The findings go to the builder as one turn, high severity first; the gate
runs; the harness shoots again; the critic judges again.

| Stop rule | Value |
| --- | --- |
| Done | No high-severity finding, mechanical or critic, in two rounds in a row |
| Cap | Six rounds; still high findings → `stopped` with `critique`, and R3 shows them |
| Budget | The run's ceiling, as everywhere |

Every round's shots are kept under the run's raw folder, so R3 can fold a
before and after per screen.

## R3 — the finished frontend

Rendered like R1 and R2. Order:

1. The primary screen, final, at each viewport.
2. One recorded walkthrough per core scenario.
3. The placement check's result: each frequency class and where its features
   ended up.
4. Remaining medium and low findings, if any, with why they were left.
5. Folded: every other screen and state, the per-round before and after, the
   mechanical findings history.

A local link to the running app sits at the top for the owner to click
around in. Per-item edits go back to the builder as one turn and the loop
runs again from shooting, with its stop rule; the re-shown sheet marks what
changed. Approval moves the run to `pull request`.

## Completion

- New tests in `tool/test_oneshot.py`: the inventory read from a fixture
  OpenAPI document; each S6 placement check refusing; the DOM placement check
  on fixture pages (an `every` feature hidden, a `rarely` feature on the
  primary route, a `sometimes` feature two clicks away); the stop rule (two
  clean rounds, the cap, a mechanical high finding overriding a clean
  critique); the critic's block refused outside its schema.
- The repository's gate including the web build, `tool/lint.py --check` and
  `tool/debt.py check`.
- One real run from step 3's throwaway repository through R3, with the R2 and
  R3 sheets and the round count in the pull request.

## Decided with the owner

Nothing yet for this step.
