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
| An input the script calls wrong (2026-11-25) | The owner's input stands. The build agent gets it as a failure and fixes the program, unless a check outside the program shows the input wrong; only then is the owner asked, in Korean written by the server. One pull request (13e) |

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

- It launches the frozen program on the owner's desktop, so the owner sees
  the window the measurement drives and hears its sound, and only that
  window: every console child the server, the script and the tests start
  runs without a window (`process.hide_child_consoles`). The clips also
  hold what the program said.
- A metric is read from what the program put out (the sound, the screen,
  the file), never from its own account of what it did, and a metric with
  nothing to measure fails.
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

The server's pass stands only for what it measured. An input changed since,
or a frozen copy no longer as it passed, sets the pass aside, and the server
measures again. A result from before this step, still waiting for a verdict,
has no mode and never had the server's pass, so the server measures that HEAD
first too. An approval the owner gave before this step stands: the owner
already measured that HEAD in full and judged it, and the review reads it.

### An input the script calls wrong is the build's to prove

The `vtuber-ko-dub` run's third measurement (2026-11-25) stopped for the
owner on `M5`, a live broadcast that was on air. Twitch now names the
broadcast in `#EXT-X-SESSION-DATA` instead of `#EXT-X-TWITCH-INFO`, and the
program read an empty id, refused every live, and its script reported that
as a wrong input, in English. Each new address the owner gave stopped the
same way.

The owner gave the input, so the script's `input_error` is not the owner's
stop by itself. The rules:

- A report with an `input_error` that names no input the plan declares, or
  a blank reason, is a failed report, the build's like any other. Its error
  is a fixed sentence: nothing the script wrote is repeated.
- Otherwise the server saves a failed measurement whose `error` is a Korean
  sentence of its own. The card and every stop show only server sentences;
  the script's reason stays in the record for the build turn. The build turn
  is told the input stands, to check it against the service by a call
  outside the program and to fix the program.
- `input_sent` lists the inputs whose dispute a build turn was actually sent,
  each by id, `env`, `what` and `kind`: the same id in a rebuilt plan is
  another input. A dispute stopped at the round cap joins it only when the
  owner's retry sends it to the build, and only if no input changed since
  that measurement (`input_since`): a report of an earlier value still goes
  to the build, but hands nothing over.
- `input_fails` counts every input's disputes. A machine pass leaves it, so
  inputs only the owner's measurement uses still reach the round cap; the
  cap's retry resets it with `measure_fails`, and an owner's measurement that
  ran through every input clears it.
- The owner is stopped only when a report on an input in `input_sent`
  carries `checked`: `how`, the check made outside the program, and
  `answer`, what the service answered, each at least four Hangul syllables,
  Hangul at least half of its letters in any script, and at most 600
  characters. A name or an address inside it passes; an account in another
  language, or a longer one, goes back to the build. The owner reads both
  parts whole, in a sentence the server writes around them. Every sentence
  that carries text from the report (the card's `error`, the owner's stop,
  the round cap's stop) is masked as a whole with the report's mask, and so
  are the worker's own halt and set-aside sentences where it holds that mask.
- A fixed sentence the server writes with no text from the report, such as
  a result set aside by a later input change, is shown as written. A secret
  that is itself a phrase of such a sentence is an accepted limit: it was
  never the report's, and the sentence says nothing of it.
- The owner's stop also resets `measure_fails` and sets `input_owed`: their
  press sends the next failed report of any kind, a malformed one included,
  to the build even at a round cap of one, before the cap applies again. An
  owner's measurement that runs through every input clears `input_owed`
  with the rest of the dispute.
- The server reads `checked` by its shape and language, never by whether it
  is true. That is an accepted limit: the owner reads the account, and a
  press with the same value sends the next report to the build again.
- Any change of an input's value, and the owner's stop itself, clear
  `input_sent` and `input_fails`, and a value change sets `input_since`: the
  next such report goes to the build first. Pressing `[실측 시작]` again with the same value is how the owner
  says it stands.

A program that leaves the owner's screen empty, opens no window of its own,
or plays no sound the owner can hear is the owner's to refuse; the note says
which.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 13a | `.wiki/measure.json` takes `app` for a `desktop` run, frozen and health-checked like the PC app's `run`; metrics carry `person`; the build prompt asks for the first screen with the backend | In this pull request |
| 13b | The server measures by itself after each passing build turn, on the owner's screen, with sound, with one data folder per run; only failed metrics go back | In this pull request |
| 13c | The owner's one measurement on their own screen, with sound, after every machine metric passed | In this pull request |
| 13d | The `vtuber-ko-dub` run goes through 13a–13c: its build turn writes the first screen, the server measures until it passes, then the owner measures once | After the merge |
| 13e | An input the script calls wrong goes to the build agent; the owner is asked only once a check outside the program shows it wrong | In this pull request |
