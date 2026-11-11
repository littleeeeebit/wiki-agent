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

`split → board → frontend → r2 → build-front ⇄ critique → r3`. After R3
approval the run moves to `pull request` ([step 5](5-screen-pr.md)). Stops
follow step 2's table, plus `critique` (the round cap was reached with
high-severity findings left). The backend review's approval starts `split`
on the same worker key; `board` and `r2` wait for the owner.

## Parts

| Part | Scope | Module |
| --- | --- | --- |
| 4a | S5, S6 and R2: the inventory, the board, the design turn, the mock shots, the R2 decisions and their commit | `tool/main/oneshot_front.py`, `tool/main/oneshot_screens.py` |
| 4b | S7's build turn: the adapter grant, `live_cmd`, the shooting harness and its mechanical findings | `tool/main/oneshot_front_build.py`, `tool/main/oneshot_look.py` |
| 4c | Image input in `ChatSession`, the critic, the stop rule and R3 | `tool/agent/chat_session.py`, `tool/main/oneshot_critique.py` |

The screens that show the board and the sheets belong to
[step 5](5-screen-pr.md); 4a serves their data over HTTP.

## S5 — Feature split

### The inventory comes from the approved API

The list of actions is read from the OpenAPI document the owner approved at
R1 (`docs/design/api.yaml`, the server's own copy), which the contract tests
exercise, not written by the model, so nothing is forgotten and nothing
invented. No stack's interpreter is needed to read it, and the build cannot
change the copy the server reads. One
read-only model turn only names each action in the owner's words (from the
glossary), groups trivially related endpoints into one feature (list and
filter are one), and proposes a class with a one-line reason, as an
`oneshot-features` block:
`[{id, name, endpoints: ["METHOD /path"], class, why}]`. Every served
operation belongs to exactly one feature and a feature names no other.

The backend review hands the worktree to the split only after letting it go,
and the split, S6 and an R2 edit turn each hold it as the review does
(`oneshot_review.holding`): from reading the app until the result is saved,
on the HEAD the build passed and a clean tree, checked before and after the
turn. A moved tree sends the run back to the build's checks (`build`, stopped
with `gate`), and the instruction the person queued for the worktree
meanwhile goes once the stage lets go. A split saved after such a rebuild
discards the screen design and R2 decisions left from the earlier split, so
the board is shown alone.

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

One read-only model turn in the worktree that returns four blocks; the
server validates them, writes each mock page into the run's folder and shoots
it.

| Block | Shape | Becomes |
| --- | --- | --- |
| `oneshot-screens` | `[{id: "S1", route, purpose, features: [feature_id], primary: bool, reached: "start"\|"one-click"\|"deeper"}]` | The screen map |
| `oneshot-states` | `[{screen, empty, loading, error, full}]`, one line each, one entry per screen | The state list R2 shows and S7 shoots |
| `oneshot-tokens` | `{palettes: [{id: "C1", name, colors: {primary, surface, text, accent}}], type: [...], spacing: [...]}`, two or three palettes | The palette choice on R2 |
| `oneshot-mocks` | `{screen_id: html}`: a self-contained grey-box page per screen, no real data, no external file | Screenshots on R2 |

Server checks, each a refusal sent back once:

- Exactly one screen is `primary`, reached at `start`, and it carries every
  `every` feature.
- No `rarely` or `never` feature appears on the primary screen.
- Every `sometimes` feature is on a screen reached at `start` or `one-click`.
- Every feature except `never` appears on some screen, and no `never`
  feature appears on any.
- Each mock carries `data-feature="<id>"` for every feature its screen lists.
- Each mock opens with no console error, no page error and no request
  leaving the page; the shooting browser lets through the mock's own file and
  `data:` URLs and aborts every other request, another local file included.
  The filter covers the whole browser context, so a popup's requests are
  aborted too; a WebSocket is never connected, and opening either is a
  refusal of its own.
- Translationese in a purpose, a state line or a palette name is sent back
  once and kept as a warning after that, as in R1.

The server shoots each mock with Playwright at the profile's viewports
(desktop, and phone when responsive was chosen), into the run's folder under
`front/r<n>/`, a new `n` for every accepted design so an earlier round's
shots stay.

## R2 — the frontend sheet

Rendered by code like R1, in Korean, the same per-item approval and the same
translationese check on every person-facing string. Order:

1. The first-screen line and the primary screen's screenshot.
2. The frequency map as the owner submitted it, beside where each feature
   landed. A design that cannot honour a placement is refused before R2, so
   every row matches the owner's class.
3. The other screens' screenshots, one row per screen with its purpose.
4. The palette proposals, each with its four colours; the owner picks one.
5. Folded: states per screen, the backend review verdict from S4.

The items are each screen (`S1`, with its mock and screenshot) and each
screen's states (`S1.states`); the palette is a choice beside them, needed
before approval. Edits follow R1's dependency rule: a screen's states, its
mock and its screenshot depend on the screen, and every item in the set loses
its approval and is shown again. No item holds the tokens, so an edit turn
that changes a palette, the type scale or the spacing is sent back, and the
owner's palette choice stands as chosen. Every item, folded or not, needs a
decision before the run moves on. Approval writes `docs/design/frontend.md`,
the mocks under `docs/design/mock/` and the shots under `docs/design/shots/`
into the worktree as one commit, and the run moves to `build-front`. The
commit holds the worktree as a turn does: while another turn holds it, the
approval is refused and the decisions are kept for a second try; a HEAD that
is not the one the build passed, or a dirty tree, sends the run back to the
build's checks. A symbolic link or a junction on the way to any of those files, or a
place that resolves outside the worktree, stops the approval before anything
is written.

An edit to a placement may reopen S5's board instead of S6, when the owner
says the class was wrong rather than the screen. Reopening S5 invalidates
everything S6 produced from the old map: the screen map, states, mocks,
screenshots, the palette choice and every R2 approval are discarded, S6 runs
again from the new map, and R2 is shown whole, not as a difference.

## S7 — Build, look, critique, fix

### Build

R2's approval sends one write turn (stage `build-front`, run like the
backend's through `oneshot_build.send`) the approved screen map, states,
chosen palette and the frequency map; the spec's `done` becomes one item per
screen in the same save that commits the approval, so a turn resumed after a
restart reports against the screens too. A turn that cannot be sent (a
failed start) lets the instruction the person queued meanwhile go,
as a finished turn does. The rules it must follow, all checked by the server:

- Every control for a feature carries `data-feature="<feature_id>"`.
- The adapter's `live_cmd` starts the backend and the frontend together;
  the session fills it, since step 2 left it empty, and the gate gains the
  frontend's tests, in the existing `gate` shell form with its PowerShell 5
  variant, keeping the backend's gate command in it.
- The app serves its screens and its API on one port, read from `PORT`, and
  keeps its data under `APP_DATA_DIR`. Each screen opens at its own route as
  a path (`/months`), and S6 refuses a screen route that an API path also
  matches.
- `e2e/seed.json` lists the API calls, `[{method, path, body}]`, that fill
  an empty app the way a person would.

The adapter is guarded (`tool/guard_grant.py`): a session may write the task
checkout's `.wiki/adapter.toml` only while its server spec carries
`adapter: true`, and never the original's. The guard trusts the stored flag
alone, so its lifetime is the run's job:

- Set when an S7 build turn actually starts, including a resumed one.
- Cleared when that turn ends by any path: accepted `done-report`, a refused
  one, a gate failure, a model error, the owner's stop. The clear
  sits in the turn's release of the worktree, which runs for every ending and
  before the worktree is let go, so the next turn there never finds it set.
- Cleared by the run's `recover()` at server start for every oneshot spec,
  since a restart can cut a turn before its end handler runs.

So the grant exists only while a build turn is running, and only in the
linked worktree. The server then reads the committed adapter back and
refuses the build if `gate_cmd` lost the backend's tests or `live_cmd` is
empty. It runs the backend's gate command as the server knows it and then
the adapter's gate, on a clean HEAD, keeps the contract tests pinned and
every pinned backend case passing in the stored runner's report, as in S4,
and then runs the
harness below. After the harness the HEAD must still be the one the gate
passed, with a clean tree; a look the owner's stop or a server shutdown cut
short passes nothing and leaves the stage at `build-front`. A high finding
goes back to the turn with the rest of S4's refusal rule (three in all, then
`gate`); a pass moves the run to `critique`.

### Look: the shooting harness

A server routine, not a model. It starts `live_cmd` on a free port inside a
process tree it owns (a job on Windows, a process group elsewhere), never
starts it when that tree cannot be owned, and kills the tree on every way
out. On Windows the tree is also followed by parent process, since a
packaged launcher such as the Store's `pwsh` starts its children outside the
job. A watcher opens a handle to each new child as it appears and holds it
until the end, so a child whose parent exits first is still the app's and
is still killed. Before any request goes to the port, every process listening
on it must be one of the app's; another program there, a listener whose owner
cannot be read, a table that cannot be read, or a launcher that exited refuses
the look, so the seed's writes never reach another program. With the
app running it drives Playwright over every route, at each viewport, in each
state:

| State | How it is produced without changing the app |
| --- | --- |
| empty | The app started on a fresh `APP_DATA_DIR` |
| full | The app started again on another fresh folder, then `e2e/seed.json` replayed through the API |
| loading | The API's requests intercepted and never answered while the shot is taken |
| error | The API's requests intercepted to return 500 |

A request counts as the API's when it is a fetch whose path matches a served
operation from the S5 inventory (`{id}` matches one segment). In `error`, the
failed request's own console line is expected, so only uncaught page errors
count there.

and records, per shot:

- Console errors and unhandled rejections (high).
- Horizontal overflow, `scrollWidth` above the viewport width (medium).
- The placement check from the DOM in the `full` state: on the primary route
  every `every` feature's `data-feature` element is visible inside the
  viewport without interaction or scrolling, no `rarely` or `never` one is
  present, and every `sometimes` one is visible there or becomes visible in
  the viewport after one click on one of the first fifteen controls that is
  itself in view (high).
- Across every screen, no `never` feature is present and every other feature
  is present somewhere (high).

Each shot is `<screen>-<viewport>-<state>.png` under the run's folder,
`build/r<n>/`, one `n` per checked build. The mechanical findings are
deterministic; a placement or console finding is high severity by definition
and never left to the critic's judgement.

Two records the harness does not take yet: an axe-core accessibility pass
(axe-core is not installed in this repository's environment) and the core
scenarios run as recorded end-to-end walkthroughs. Both wait for a later
part; until then R3 shows screenshots only.

### Critique: a separate read-only session

The owner chose a critic separate from the builder. It is a read-only
session that must see the screenshots, and the shots live under the run's
raw folder, which the read-only tools cannot reach (Codex's source-only read
profile also turns off `view_image`). So the screenshots travel in the turn
itself, which the app's sessions cannot do yet: `ChatSession.say` takes text
only, and every provider path serializes one text block
(`tool/agent/chat_session.py:872`, `:913`, `:935`;
`tool/agent/chat_events.py:103`). This step therefore extends the session
first:

- `say` gains an optional list of image files. The Claude path serializes
  them as base64 image content blocks in the same stream-json user message as
  the text; that is the one provider supported for the critic. A session on a
  provider without image input refuses a turn with images instead of dropping
  them.
- The images belong to the turn, so a retry of that turn (the session's
  overflow or transport retries) resends the same round's images.
- The critic runs on Claude (role `critic`: `opus` at `high` effort). A
  Codex critic, or a refused or failed image turn, stops the run with
  `critique`; it is never treated as a critique that saw nothing wrong.
- The turn carries the latest round's shots, every screen's `full` state
  first, then `empty`, `error` and `loading`, at most 24 images, each named
  in the text in the order attached. A round with one of those shots missing,
  or reached through a link or a junction, is never judged: the run goes
  back to `build-front`, and `[재개]` shoots again.
- The critique holds the worktree as the backend review does, on the HEAD the
  build and its shots passed (`front_built_head`), clean. A tree that moved
  goes back to `build-front`'s checks.

The mechanical findings, the R2 sheet and the frequency map go in the
critic's prompt; the critic reads nothing from disk outside the worktree's
tracked files. It judges in the `design-pass` order:
purpose and layout, measured spacing and alignment, typography, colour,
leftover whitespace. It returns an `oneshot-critique` block:
`[{severity: "high"|"medium"|"low", route, viewport, state, what, fix}]`. It
cannot write files and never sees the builder's reasoning, only the result.

### Fix and repeat

The findings go to the builder as one turn, high severity first, with the
round's mechanical high findings added; the gate runs; the harness shoots
again; the critic judges again. A round is clean when neither the critic nor
the harness found anything of high severity. A clean round that still has
medium or low findings sends them to the builder once; a clean round with no
finding at all has the critic look at the same shots again, so two clean
rounds are two independent readings.

Each hand-over saves the next stage's `working` with its result, before that
stage starts: the build that passed names the critique, a round with findings
names the build. A server that dies in between leaves the stage for
`recover`, so `[재개]` takes it on. The findings a build turn is sent with are
kept as the run's `fix` until a build of them passes the harness, so a resumed
turn, after a restart or a failed start, carries the same findings.

| Stop rule | Value |
| --- | --- |
| Done | No high-severity finding, mechanical or critic, in two rounds in a row |
| Cap | Six rounds; still high findings → `stopped` with `critique`, the findings in its detail. `[재개]` counts six rounds afresh |

Every round's shots are kept under the run's raw folder, so R3 can fold a
before and after per screen.

## R3 — the finished frontend

Rendered like R1 and R2. Order:

1. The primary screen, final, at each viewport.
2. The placement check's result: each frequency class and where its features
   ended up.
3. Remaining medium and low findings, if any, with why they were left.
4. Folded: every screen in every state, the first and last shot of each
   screen, the mechanical findings history.

The items are the screens (`S1`, `S2`, ...), each approved or edited. The
screenshots are served at `GET /api/oneshot/spec/<repo>/look/<n>/<name>`. A
local link to the running app, for the owner to click around in, is part of
the screens in [step 5](5-screen-pr.md). Per-item edits go back to the
builder as one turn and the loop runs again from shooting, with its stop rule
counted afresh; the re-shown sheet marks the edited screens. Approval of
every screen moves the run to `pull request`. The approval holds the worktree
and checks it is still `front_built_head`, clean; a tree another turn holds
refuses it (409, the decisions kept), and a moved one goes back to
`build-front`'s checks. A worktree that is not the run's checkout stops R3
with `folder`; `[재개]` puts the sheet back with its decisions, and the next
approval checks the tree again. A screenshot path through a link or a
junction is never served.

## Completion

- 4a's tests in `tool/test_oneshot_front.py`: the inventory read from a
  served fixture app; a feature list that invents, drops or shares an
  operation refused; each S6 placement check refusing; a mock with a console
  error, a page error or an outside request sent back; R2 needing every item
  and a palette, committing the design, an edit reaching the screen's states
  and keeping the rest approved, and a reopened board discarding the design.
- 4b's tests in `tool/test_oneshot_look.py` and
  `tool/test_oneshot_front_build.py`: on a real fixture app started by its
  `live_cmd`, every screen shot in every state, and the DOM placement check
  (an `every` feature hidden, a `rarely` feature on the primary route, a
  `sometimes` feature no single click reaches, a `never` feature on a
  screen) and a console error each high; an app the command cannot start
  refused with its output; the adapter grant set only during the build turn
  and refused on the original checkout, through the real guard, and the real
  guard refusing an adapter write after the turn ended by a model error, by
  the owner's stop, by a refused check, and after a restart right after the
  flag was stored; a build whose adapter dropped the backend tests or left
  `live_cmd` empty refused; a high finding from the shots sent back; a clean
  build moving to `critique`.
- 4c's tests: the stop rule (two clean rounds, the cap, a mechanical high
  finding overriding a clean critique); the critic's block refused outside
  its schema; with the provider transport mocked (no external call), the
  request the real `ChatSession` sends for a critic turn containing each
  screenshot as an image block beside the text, a retried turn resending the
  same round's images, and a session on a provider without image input
  refusing the turn so the run stops with `critique`.
- The repository's gate including the web build, `tool/lint.py --check` and
  `tool/debt.py check`.
- One real run from step 3's throwaway repository through R3, with the R2 and
  R3 sheets and the round count in the pull request.

## Decided with the owner

Nothing yet for this step.
