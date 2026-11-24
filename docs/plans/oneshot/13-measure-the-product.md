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
(`oneshot_desktop.frozen`), launches the frozen copy on the run-owned
desktop, and waits for `health` to answer 200 from a socket the program's
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

- It launches the frozen program on the run-owned desktop, so no window
  covers the owner's screen. With `ONESHOT_MEASURE=auto` the program plays
  no sound through the speakers, and the clips hold what it said. A program
  that plays sound anyway is heard: an accepted limit, since the server sets
  no per-program volume.
- `{data}` is one folder kept for the whole run, not a fresh one per launch,
  so a sign-in the owner gave once is still there for the next build.
- A failed metric that needs no person sends the run back to the build agent
  (`fix: measure`) with those metrics only: their values, thresholds, the
  report's notes and the end of the program's log. The build agent may run
  the same script itself while it fixes them.
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

A program that leaves the owner's screen empty, opens no window of its own,
or plays no sound the owner can hear is the owner's to refuse; the note says
which.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 13a | `.wiki/measure.json` takes `app` for a `desktop` run, frozen and health-checked like the PC app's `run`; metrics carry `person`; the build prompt asks for the first screen with the backend | In this pull request |
| 13b | The server measures by itself after each passing build turn, on the run-owned desktop, without sound, with one data folder per run; only failed metrics go back | In this pull request |
| 13c | The owner's one measurement on their own screen, with sound, after every machine metric passed | In this pull request |
| 13d | The `vtuber-ko-dub` run goes through 13a–13c: its build turn writes the first screen, the server measures until it passes, then the owner measures once | After the merge |
