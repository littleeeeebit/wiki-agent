# Step 13 — The measurement runs the product, and the owner sees and hears it

The overall design is in the [overview](0-overview.md). This step changes
what step 12's measurement runs and who runs it. For a run that builds a
program installed on Windows, the measurement launches that program. The
server measures by itself, over and over, sending the build agent only what
failed. The owner measures once, at the end, watching the program's window
and hearing its sound.

Goal. The owner is called for the measurement only when everything a machine
can check has passed. Then the owner sees the product they asked for running
on their own screen, with the real broadcast and the real sound.

## Why this step exists

The `vtuber-ko-dub` run's second measurement (`m2`, 2026-11-24) is recorded
in `raw/specs/vtuber-ko-dub/oneshot-initial.json` under `oneshot.measures`.

| What happened | What the record shows |
| --- | --- |
| The owner did the whole measurement and saw nothing | `serve` is `cargo run -p vtuber-core --bin vtuber-serve`, a headless server. The product is a Windows program (`.exe`), and no window of it ever opened. The run had no screen yet: `src-tauri` held only its configuration |
| The live broadcast never played | `live_delay_s`, `live_playlist_gaps` and `live_clip` stayed at `99999` or `false`: the live never reached playback. The build turn that followed checked a recorded video only and ended saying "A real live stream (R8, R14)" was not verified |
| A machine's failure cost the owner a full measurement | 11 of 14 metrics failed, and none of the 11 needs a person: the setup, the Gemini key, the video and the live. The two that need a person, the Google sign-in and the browser link, passed. Every repair still asked the owner to measure everything again, sign-in included |
| A failure no person was needed to find | The note says the TTS worker never loaded its models. The build agent found and fixed it on the real video by itself, in the turn that followed |

## Decided with the owner (2026-11-24)

| Question | Decision |
| --- | --- |
| What the measurement runs | For a run with `desktop`, the program the owner will install, never a headless server. The build turn that writes the backend also writes the program's first screen, so the measurement has something to open. A run without `desktop` keeps step 12's `serve` |
| Who measures until it passes | The server, by itself, after every build turn whose gate passes. Only the metrics that failed go back to the build agent, with their values and notes; it fixes them and the server measures again |
| When the owner measures | Once, after every metric that needs no person has passed on one build. The program opens on the owner's own screen, plays the real broadcast and speaks Korean out loud |
| A metric that never ran | A failure, like any other. A build turn cannot hand the owner a measurement whose live part never played |
| How it ships | One pull request: this plan and all of 13a to 13c, never split. Once it is merged, the stopped `vtuber-ko-dub` run goes through it with no other change |
| An input the script calls wrong (2026-11-25) | The owner gave it, so the owner is asked first, on the measurement screen, with the script's reason. A new value is measured again at once, with no build between. Pressing `[실측 시작]` with the same value says it stands, and the build agent fixes the program. The rule holds for any input, never for one kind of failure (13e) |
| Where a repair happens (2026-11-25) | Inside the measurement. Once the run reaches it, the stage and the screen never go back before the owner approves: a failed metric, a refusal, a plan that no longer holds and a moved tree are all repaired by a build turn while the run stays at the measurement (13f) |
| What the owner can see (2026-11-25) | Everything a oneshot runs. The server's own measurement, the screen looks, the browser looks and the emulator open on the owner's screen; no desktop of its own, no headless browser, no hidden window. A pass the owner could not have watched is a judgement only the server saw (13f) |

## Design

### The first screen ships with the backend

In a run whose kinds include `desktop`, the build turn that writes the
backend also writes the program's first screen. It is the screen a person
uses for the measured flow, nothing more: an address box, start and stop,
what plays with the converted sound, and the speakers found. The later
stages (S5 to S7) build the rest of the screens on top of it and review it
like any other screen; this step adds nothing to them.

`.wiki/measure.json` names the program in `app` instead of `serve`:

- `run`: the program, its arguments, its working directory and the setting
  that points its data at `{data}`, as the PC app's `run` block (9e, "The
  runnable program");
- `health`: the local address the program's own process answers once it is
  ready, for example its link server.

The build check freezes `run` the way the PC app's rounds do
(`oneshot_desktop.frozen`), launches the frozen copy on the owner's desktop,
and waits for `health` to answer 200 from a socket the program's
job owns, as step 12 checks `serve`. A plan with `serve` in a `desktop` run,
or with `app` in a run without `desktop`, is refused, and so is a `run` the
PC app's gate would refuse.

The measurement script drives the running program through that same local
API, so the window shows what the script does. It never drives the window
itself.

### Metrics that need a person

A metric carries `person: true` when one of the `during` steps is needed to
reach it, such as a Google sign-in. Every `during` step names the metrics it
serves, and a metric with `person: true` must be named by one. The build
check refuses either missing.

The script is told which measurement it is in, through `ONESHOT_MEASURE`:
`auto` or `owner`. In `auto` it waits for no person: it uses the sign-in the
run's measurement data already holds, and reports a person metric it cannot
reach as not reached, never as passed.

### The server measures until it passes

After every build turn whose gate passes, the server measures by itself.

- It launches the frozen program on the owner's desktop, where the owner
  sees its window while the script drives it. With `ONESHOT_MEASURE=auto` the
  program plays no sound through the speakers, and the clips hold what it
  said. A program that plays sound anyway is heard: an accepted limit, since
  the server sets no per-program volume.
- `{data}` is one folder kept for the whole run, not a fresh one per launch,
  so a sign-in the owner gave once is still there for the next build.
- The program gets only what it needs to run, the measurement's inputs, the
  design's secrets and its own launch settings. The script keeps the gate's
  environment, the toolchain it runs with, as in step 12. A value of eight
  characters or more under a credential's name is masked in what the script
  reports. A shorter one is not, since masking a short value would garble the
  text, and nor is a credential under another name: accepted limits.
- The program's output is read off a pipe into memory and reaches the disk
  only masked, so no leftover file holds a secret it printed.
- A failed metric that needs no person goes to the build agent
  (`fix: measure`) with those metrics only: their values, thresholds, the
  report's notes and the end of the program's log. The build agent may run
  the same script itself while it fixes them. The run stays at the
  measurement while it does (13f).
- A person metric is not judged in the server's own measurement. Once
  every other metric passed, the owner's one measurement covers every
  `during` step and every person metric.
- The round cap (`oneshot_review.cap()`) still stops the run for the owner
  after that many failed measurements in a row.

### The owner measures once, and sees and hears it

When every metric that needs no person has passed on the built HEAD, the
card says the program is ready to be measured. The owner's `[실측 시작]`
launches the same frozen program on the owner's desktop with its sound on,
and `ONESHOT_MEASURE=owner`. The owner follows the `during` steps, watches
the program play the video and the live broadcast, and hears it speak
Korean. The clips stay for a second listen. The owner approves, or refuses
with a note that goes to the build agent like a failed metric.

The server's pass stands only for what it measured. An input changed since,
or a frozen copy no longer as it passed, sets the pass aside, and the server
measures again. A result from before this step, still waiting for a verdict,
has no mode and never had the server's pass, so the server measures that HEAD
first too. An approval the owner gave before this step stands: the owner
already measured that HEAD in full and judged it, and the review reads it.

### An input the script calls wrong is the owner's to answer

The `vtuber-ko-dub` run's measurements on 2026-11-25 reported its live
broadcast input `M5` as wrong twice: once for a parsing fault in the program,
and once because the broadcast had ended. Both times the run went back to the
build, and the screen to the build stage, while the owner could have given a
live address at once.

The owner gave the input, so the owner answers. The rules hold for every
input, whatever the script says is wrong with it:

- A report with an `input_error` that names no input the plan declares, or
  a blank reason, is a failed report, the build's like any other. Its error
  is a fixed sentence: nothing the script wrote is repeated.
- Otherwise the measurement is saved with status `input` and stops on its
  own screen (`measure-input`). No build turn starts and no failure is
  counted. The stop asks the owner in a sentence the server writes: which
  input, the script's reason when it is said in Korean, the build's check
  when the report carries one, and what each answer does.
- A new value for any input, plain or secret, sets `input_since`, and the
  measurement starts again at once, with no press and no build between.
- `[실측 시작]` with no input changed since that measurement (`input_since`
  below its number) says the value stands. The report then goes to the build
  agent as a failed measurement, told the owner kept the input: it checks it
  against the service outside the code that refused it and fixes the
  program. The press names the measurement whose result the screen showed
  (`Start.n`). It sends a repair, of a kept input or a capped failure, only
  for that measurement, and a screen that never showed it is refused with
  409.
- `checked` is optional: `how`, the check made outside the program, and
  `answer`, what the service answered. `why`, `how` and `answer` are shown
  only when each has at least four Hangul syllables, Hangul at least half of
  its letters in any script, and at most 600 characters; a name or an address
  inside passes. The server reads them by shape and language, never by
  whether they are true: the owner reads them and decides.
- Every sentence that carries text from the report (the card's `error`, the
  owner's stop, the round cap's stop) is masked as a whole with the report's
  mask, and so are the worker's own halt and set-aside sentences where it
  holds that mask. A fixed sentence the server writes with no text from the
  report is shown as written; a secret that is itself a phrase of such a
  sentence is an accepted limit.

### The run never leaves the measurement, and nothing runs out of sight

Once the run reaches the measurement, its stage stays `measure` until the
owner approves. A repair is a build turn sent from there: `fix` names why
(`measure` for a failed metric or a refusal, `plan` for a plan that no longer
holds, `moved` for a tree that moved before, during or after a measurement,
its reason kept in `moved`), and `oneshot_profile.turn_stage` routes that
turn's end to the build's own check. The check's pass clears `fix` and
measures the new HEAD. A stop pressed before the moved check, or while it
runs, holds: no repair turn starts after it. A repair of an input the owner
kept as it was needs a new commit, as one of a failed metric does.
`[재개]` after a stopped repair sends the same build
turn with the same reason, and an escalation from it reaches the spec's author
and comes back as a build turn, as any build turn's does. The screen shows the
repair on the measurement page, and a repair stopped by a restart shows the
stop with its `[재개]`.

A new value given at a `measure-input` stop waits for the measurement that
asked to let the tree go, then measures; a second answer that waited too
finds that measurement running and adds nothing. Only a value the measurement
reads counts as a change, during a measurement or after one: writing or
forgetting a credential neither the design nor the plan names leaves the
question and a running result as they are. An input an earlier version
disputed at its round cap is measured again first, its count cleared, so the
owner is asked about it.

Whatever a oneshot runs opens where the owner can see it. A desktop look has
no desktop of its own (`desktop_session.look`), so the server's own
measurement, the screen looks and the owner's round all run on the owner's
desktop. The driver keeps a window's shot only while that window is still the
app's after it is drawn, since another program's window can reuse a handle
there. The browser looks run a headed browser, and the emulator opens its
window. A PC screen preview shows each screen in a window on the owner's
display for at least a second while it is shot. The server samples the job's
windows every 0.2 s (`desktop_session.Look.shown`); one visible, not
minimized, not cloaked and on a display counts. Pictures drawn with none, by
a headless browser, an offscreen render or a window placed off every display,
are sent back to the designer. Accepted limit: no picture is matched to the
window it was shot from. The guard catches a preview that forgot its window,
not one that opens a blank window on purpose; the owner sees the pictures at
R2. The cost, accepted: windows open on the owner's screen while the
server works, and its test runs open them too.

A program that leaves the owner's screen empty, opens no window of its own,
or plays no sound the owner can hear is the owner's to refuse; the note says
which.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 13a | `.wiki/measure.json` takes `app` for a `desktop` run, frozen and health-checked like the PC app's `run`; metrics carry `person`; the build prompt asks for the first screen with the backend | In this pull request |
| 13b | The server measures by itself after each passing build turn, on the owner's desktop, without sound, with one data folder per run; only failed metrics go to the build | In this pull request |
| 13c | The owner's one measurement on their own screen, with sound, after every machine metric passed | In this pull request |
| 13d | The `vtuber-ko-dub` run goes through 13a–13c: its build turn writes the first screen, the server measures until it passes, then the owner measures once | After the merge |
| 13e | An input the script calls wrong stops for the owner on the measurement screen: a new value measures again at once, the same value kept goes to the build | In this pull request |
| 13f | The run stays at the measurement through every repair; nothing a oneshot runs is out of the owner's sight | In this pull request |
