# Step 12 — The backend review converges on what was measured

The overall design is in the [overview](0-overview.md). This step stops the
backend review from refusing without end. It does that in three ways. The
builder's answers reach the reviewer. A conflict the two cannot settle goes to
the owner. And before any review, the built backend runs once against the real
services.

Goal. A backend build opens its pull request as soon as its gate passes. It
then runs on the owner's PC against real outside services, for example a real
Google sign-in and a real live broadcast. The owner follows steps that
wiki-agent shows, one window at a time, to get each account and address. The
review reads that pull request together with the measurement. Each round
either converges or reaches the owner with a decision only they can make. All
of it ships in one pull request, so a run resumed after the merge goes through
it with no other change.

## Why this step exists

The `vtuber-ko-dub` run's backend review refused 46 times between 2026-11-20
09:24 and 2026-11-22 15:55. It stopped at `cc333ede`. The record is in
`raw/specs/vtuber-ko-dub/oneshot-initial.json` under `oneshot.reviews`, and the
build transcript is in the builder's Claude session for
`vtuber-ko-dub-worktrees/oneshot-initial`.

| Cause | What the record shows |
| --- | --- |
| The builder's answers never reach the reviewer | From round 33 on, each build turn answers four to six findings "with evidence instead of a change". `earlier_text` gives the reviewer only the refusals. The finding in `session file cleanup` at round 46 repeats round 21's finding, repro included |
| Requirements conflict and nothing asks the owner | The builder answered: "every change I could find breaks another requirement the design approved". Here is one case. A body that ends when its connection closes reads the same to ffmpeg as one cut short. Accepting it would bring back round 23's P0 |
| Findings outside this stage block it | `Android service integration` (16 rounds) and `desktop command integration` (15 rounds) need the Gradle project and the `src-tauri` crate. The build turn's own text says those belong to each kind's later build |
| An unchanged HEAD is reviewed again | Rounds 40 to 43 read `db40989d` four times, ten minutes apart, and refused it with the same six findings |
| "Replace the mechanism" fires every round | Once every area had three refusals (`STUCK`), every build turn rewrote mechanisms. Every commit from round 29 on is titled "Replace the mechanisms…", and each rewrite brought new cases |
| Nothing was measured | 245 tests passed on fakes. The crate has no `fn main`, so the backend cannot run. Findings about how a real replay ends were reasoned from code, round after round |

`[재개]` resetting `refused` is intended. A resumed review still reads every
earlier refusal (`earlier_text` reads all `reviews`), and a resumed build turn
gets the standing refusal and its areas' history (`build_turn`, `recurring`).

## Decided with the owner (2026-11-22)

| Question | Decision |
| --- | --- |
| The builder's answers | Each answer "with evidence instead of a change" goes into the next review's prompt. The reviewer leaves the finding out, or raises it again with a `rebuttal` saying why the evidence does not hold |
| A build turn that answers a finding without a change | It commits a pinning test of the behaviour it defends, and names it in its answer. A build turn that ends without a commit is a bug of the turn: it goes back to the builder to commit, never to the owner and never to a stop |
| An unchanged HEAD | It is never reviewed again |
| A conflict the builder and the review cannot settle | It goes to the owner. This applies when the builder answered an area in two rounds, naming a requirement a change would break, or when an area is refused again after its one mechanism replacement. The owner keeps the review's side, the builder's side, or writes their own decision |
| Findings that belong to a later stage | They are not blocking findings of the backend. Each goes to that kind's own build turn |
| "Replace the mechanism" | Once per area |
| The round cap on `[재개]` | It resets, as before |
| Order after the build | Build gate → pull request #1 (a draft) → real measurement → review. Fix commits stack on the same pull request, and the last stage makes it ready |
| What the measurement runs | The backend ships a runnable headless server, with its API, its workers and the real sign-in, as a required product of the build. The measurement starts it with real accounts and real addresses |
| Who passes the measurement | The server measures automatically on the owner's PC. Then the owner listens to the converted samples and approves or refuses |
| Where the owner learns what to give | In wiki-agent's measurement screen. Every step names the window, what to press or type, what then appears, and what to copy into which box |
| Who writes those steps | The build agent, in the same build turn that writes the backend, through the existing agent turn. `[재개]` on a stopped run sends that turn |
| How it ships | One pull request. Once it is merged, a resumed run reaches the measurement with no other change |

## Design

### The builder's answers reach the review

A build turn sent back by a refusal answers the findings it does not change
in one `oneshot-answers` block. Each entry names the finding's `area`, the
`location`, the evidence, the requirements a change would break (`breaks`),
and the test that pins the defended behaviour, as `path::name`. The build
check (`oneshot_measure.answered`) refuses an entry whose area the refusal
did not name, whose `breaks` are not design items or requirements, or whose
test file did not change since the refused HEAD. It saves the entries on the
spec under `answers`, with the round they answer.

`review_text` shows the next reviewer every answer (`answers_text`). In an
area answered in the last round, a finding needs a `rebuttal`; without one
the verdict is refused and the reviewer is asked again (`held`).

### A turn without a commit goes back

The build check reads HEAD first (`oneshot_measure.unchanged`). When the
turn was sent back by a refusal, or by a failed measurement, and HEAD is
still the HEAD refused or measured, the turn is refused and sent back to the
build agent with that reason. It counts against `MAX_ATTEMPTS`, as a failed
gate does. `[재개]` at the round cap marks the turn as sent back by the
refusal (`fix`), so the same check holds there.

### A conflict goes to the owner

After a refusal, `conflicted` looks at each blocking area the owner has not
ruled on. It is a conflict when the builder answered it in two rounds with a
broken requirement, or when its mechanism was already replaced once
(`replaced`, recorded the turn it is replaced). A refusal with a conflict
does not go to the build or count towards the cap: the run waits at the
`conflict` stage. The owner's board shows the review's findings beside the
builder's answers, and for each area offers three rulings: the review's side,
the builder's side, or the owner's own words. The rulings are saved on the
spec (`rulings`) and shown to every later review and build turn. A P1 in an
area ruled for the builder is refused unless it is a P0. When nothing is left
to fix, the same HEAD is reviewed again under the rulings; otherwise the
build turn gets the remaining findings and the rulings.

### Later stages' findings wait

A P1 may carry `later: {kind, what}` for a kind the run builds. It blocks
nothing now. It is saved on the spec (`later`), and that kind's front-end
build turn receives it (`later_text`). A `later` for a kind the run does not
build is refused.

### The backend runs, and is measured before the review

Every build turn ends on the measurement contract
(`tool/prompts/oneshot-measure-contract.md`). The build agent commits
`.wiki/measure.json` with five parts.

- `serve`: a command that starts the backend headless, and its local health
  address.
- `measure`: a committed script that drives the running backend through the
  requirements against the real services, and writes `{out}/report.json` and
  a few clips.
- `inputs`: what only the owner can give, each a secret or a plain value,
  with its guide.
- `during`: what the owner does while it runs, such as finishing a Google
  sign-in.
- `metrics`: each with its requirements, the design's outside calls it
  reaches, and its threshold.

A guide is at least three Korean steps, each naming the window, what to do
there and what then appears; the last names what to copy.

The build check reads the plan (`oneshot_measure.checked`). It refuses a
guide step without its window, action or result, and a guide whose last step
copies nothing. It refuses an outside call of the design that no metric
reaches, and a command a PC app's confirmed tools do not cover. It then
starts `serve` in a job of its own, waits for its health address to answer
200, and stops it with everything it started. A tree the backend wrote into
is refused.

After the check passes, the run is at `measure`. The server pushes the HEAD
and opens pull request #1 as a draft, or finds and updates it
(`published`). The measurement screen shows each input's steps, a box for
each value (a secret goes to Windows Credential Manager and never comes
back), the owner's steps for the run, and the metrics with their thresholds.
[실측 시작] holds the worktree, starts `serve`, runs the script with the
inputs in its environment and every secret masked from its output, and keeps
the report and the clips. A failed metric, a missing report or a backend
that does not come up sends the result back to the build agent. An input the
script calls wrong stops for the owner, who fixes it and presses
[실측 시작] again. A run of passed metrics waits for the owner. They listen to
the clips, then approve, which comments the record on the pull request and
starts the review, or refuse with what they heard, which goes to the build
agent. The review reads the approved measurement of its HEAD
(`measured_text`). A finding the measurement contradicts is P2.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 12a | The builder's answers reach the review with pinning tests; a turn without a commit goes back to the builder; an unchanged HEAD is never reviewed twice | In this pull request |
| 12b | Conflicts go to the owner's board; "replace the mechanism" once per area; later stages' findings go to that kind's build | In this pull request |
| 12c | The build agent's `.wiki/measure.json` and its check, the headless backend started at the gate, and pull request #1 right after it | In this pull request |
| 12d | The real measurement before the review: the owner's step-by-step inputs, the automatic metrics and clips, the owner's verdict, the record on the pull request and in the review | In this pull request |
| 12e | The stopped `vtuber-ko-dub` run: `[재개]` sends its build agent, which commits the headless server, the measurement and its guides; then measure against a real Google sign-in and a real Japanese VTuber live | After the merge |
