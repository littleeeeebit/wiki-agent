# Step 14 — The owner speaks to the build, and the design follows

The overall design is in the [overview](0-overview.md). Until this step the
owner shaped a run only at its gates: the design talk, R1, R2 and R3. Once
the build began, the design was fixed. This step lets the owner speak to the
build agent at any time from the first contract-test turn to the pull
request, and has the design follow what they say.

Goal. A small bug found while trying the app, a change of mind, or a
different model for one part reaches the build in the owner's own words.
The design is rewritten where those words change it, and the build carries
on from the code it has. Nothing starts over.

## Why this step exists

| What happened | What it showed |
| --- | --- |
| `vtuber-ko-dub`, 2026-11-25: the owner listened to the dub and found the speakers mixed later in the video, clicks at cuts, Japanese overlapping the Korean, and the wrong speed | Each is a change to the design (diarize the whole video first, fade at cuts, GPU only, a newer diarization model), not a bug the gate can see. The run had no way to take them in |
| However well the oneshot is designed, small bugs remain once the owner tries the app | The owner has to be able to change the spec after the backend and after the frontend, including switching the model a part runs |
| The build agent's own questions had nowhere to go | The work-record panel showed a Codex choices block but could not answer it |

## Decided with the owner (2026-11-25)

| Question | Decision |
| --- | --- |
| When the words apply | At once, then carry on. The model that wrote the design rewrites only what the words change; the server commits the design files; the build continues from the current code through the changed tests, the build, the measurement and the review. No second approval: the words are the owner's own. The card shows what changed |
| A turn that is running | The words go into it at once, as a steer framed as the owner's, and the design is rewritten when the turn ends. A running measurement stops and the run goes back to the build |
| Where the owner speaks | In the implementation agent's panel (작업 기록), which also answers the choices the agent offers |
| How it ships | One pull request |

## Design

### The words

`POST /api/oneshot/spec/{repo}/tell {text}` takes 1 to 4000 characters, at
any stage from `tests` through R3 (`oneshot_tell.OPEN`). Before `tests` the
design talk and R1 take the owner's words; from `pr` on the pull request is
being published. The server refuses both with 409. Each set of words is
appended to `told` with its time, its stage and whether it reached a
running turn, and to `tell_pending` with its stage.

- A oneshot work turn running in the run's worktree gets the words at once,
  through the same path a person's steer takes (`work.steer`), framed as the
  owner's and telling the agent the design will follow them.
- A running measurement is halted with a mark (`told`); wherever the
  measurement notices the halt, it hands the words on to the rewrite once it
  lets go of the worktree, rather than stopping the run.
- A parked run starts the rewrite at once, under the same lock as the
  check, so two requests never both start it. Parked means nothing runs, no
  turn holds the worktree, and the run is either stopped with a stop the
  owner may resume or resting at a screen that waits for the owner (the
  measurement, the conflicts, the phone and PC rounds, R3). A stop [재개]
  refuses (`kind`, `rekind`, `fake`) is never parked: the owner's words
  never clear it. Anywhere else a step is between its turns, and its next
  start meets the words. A run that parks with words still waiting shows
  them, and [지금 반영] takes them in (`POST …/tell/apply`).
- Words said while the screens are designed with the owner (`split` to `r2`,
  `oneshot_tell.WAIT`) wait until the frontend build begins, across any
  backend rewrite before it; R2 is a conversation of its own. Words said
  before it, during the backend's review for instance, are taken in before
  the screens are designed past them.

### Turning aside

Every step of a run starts through one of two calls: `oneshot.start_worker`
for a stage's worker and `oneshot_build.send` for a work turn. Both ask
`oneshot.diverted` first. With words waiting, `oneshot_tell.diverted` starts
the `retell` worker in that step's place, and the step's own text is
dropped: the rewritten design leads to a fresh build turn, and a review or
measurement runs again after it. A step whose run a rewrite has already
taken yields to it, since the rewrite sends the next step itself. A work
turn asks again once it holds the worktree, and lets the tree go before it
hands the step over. The measurement's start checks the request, then asks,
then reads its old plan, so a word that drops an input is never held behind
that input. The rewrite holds the worktree itself through its design turn,
its commit and its save, as a work turn does; when another turn has it, the
words stay waiting. The publication step asks too, so words
said by R3 are never published past. A start that meets the rewrite
already running (a turn's handoff after a word said at its very end) leaves
it as it is; one the server refuses otherwise (it is going down) stops the
run with its reason, so no `working` is left without a worker. A
measurement hands the words on only once it has let go of the worktree.

### The rewrite

`retell` runs one design turn with the model and effort that wrote the
design (`design_author`, whatever the window's model is now). It gets the
current design and the owner's words, may change any item, and must keep
every item the words do not touch. The same check as R1's (`design_turn`)
accepts it.

- When an item changed, the design files are written from the new design
  and committed by `oneshot_spec.design_committed`, the helper R1's approval
  now uses too, and only then does the run's record take the new design and
  requirements. A commit that fails leaves the record as the files are, and
  the words still waiting. The run goes to a tests turn.
- When nothing changed, the run goes straight to its build turn.
- A run still before its first contract-test commit, or in the middle of a
  tests turn after an earlier rewrite, goes to the tests turn, keeping where
  that turn hands on to.

Either way `retold` records the words and the item ids that changed, the
card lists them, and `attempts`, `fix` and `measure_fails` restart. The
design, its requirements and head, the queue and the next stage are recorded
in one save, so they stand or fall together. A result waiting for the
owner's verdict is set aside (`oneshot_measure.retire`): it no longer
stands for what is built next. Only the words taken in leave
`tell_pending`: words said while the design turn ran wait for the next one,
and a rewrite during the screen design takes only the words said before it.

### The tests turn after a rewrite

A run with `tests_head` already has its contract tests and its application.
The tests turn is told to change only the tests the changed items touch, add
the newly planned ones and leave the application alone. The server's check
(`oneshot_build.tested`) then:

- allows a planned test to pass, since the code exists; none may be skipped;
- commits only the contract and configuration files and the adapter, leaving
  any other change for the build turn;
- re-pins `tests_head` and `cases`, as the first tests turn does, and keeps
  the screens R2 approved as the spec's `done` when it hands back to the
  frontend build.

It hands on to `retell_to`: the backend build, or, for a run already at or
past the frontend build, the frontend build. The backend's later stages are
not walked again.

### What every work turn is told

Every tests and build turn, the frontend's and its repairs included, says
the owner watches it and can speak into it, that their words govern where
they differ from the design, and that a decision only the owner can make is
asked with the agent's question tool, recommendation first; the prompts'
old "ask the person nothing" line now says the same. The words the design
was rewritten from go with every turn after. The tests turn after a rewrite
is told that, this time, a planned test may already pass.

### The screen

The work-record panel (`OneshotTrack`) gets a box to speak to the build
agent, the count of words waiting, and the list of rewrites with what
changed. The same box shows on every screen that waits on the owner in
those stages, a measurement to judge or R3's sheet among them, and posts
through the screen's own action so the rewrite shows at once. A choice the
build agent offers in its latest turn goes back through the same endpoint.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 14a | `tell`, the divert at both start points, `retell`, the retold tests turn, the measurement halt, the prompt lines and the panel | In this pull request |
| 14b | The `vtuber-ko-dub` run takes the owner's 2026-11-25 decisions through `tell` and carries on | After the merge |
