This turn builds the Android app's screens the person approved, on the core
that is built and reviewed.

The design is committed in this worktree: `docs/design/screens.md`, with the
mock sources under `docs/design/mock/` and their pictures under
`docs/design/shots/`. The approved screens, states, type scale, spacing,
chosen palette, features and the owner's frequency map:

```json
{design}
```

The environment profile: `{profile}`. The reference phone is {device}.

The core's contract tests were committed at `{tests_head}` and are pinned
with the test runner's configuration; these files may not change at all:

{contract}

The Android toolchain is under the application's folder. Every command you run
for this project sets these variables first, as the server's runs do:

{toolchain}

## Conventions the server relies on

The server installs the debug APK on an emulator it owns, at the reference
phone's size, and drives it with `adb` and UI Automator. It grants the app only
these permissions, the ones the approved requirements state: {permissions}.

- The screens follow the stack's `ui`. Each screen shows where its `surface`
  says: `screen` in the app's own activity, `overlay` drawn over another app,
  `notification` posted to the shade.
- The debug build opens a screen in a state when its launch activity is
  started with two string extras: `oneshot.screen` (a screen id such as `S2`)
  and `oneshot.state` (`empty`, `full`, `loading` or `error`). `full` shows the
  app filled with the debug build's own sample data, `empty` with none,
  `loading` as it stays while the work has not finished, and `error` as it
  shows a failed load. An `overlay` screen starts drawing its overlay and may
  leave its activity; a `notification` screen posts its notification. A
  release build ignores both extras.
- Every control for a feature is exported to the native hierarchy as
  `feature:<feature id>`: in Compose `Modifier.testTag("feature:<id>")` with
  `Modifier.semantics { testTagsAsResourceId = true }` on the hierarchy that
  holds it, in Flutter `Semantics(identifier: "feature:<id>")`. A
  notification's views carry it as their content description in the debug
  build. A `never` feature has no control anywhere.
- Each core scenario has an instrumented test named `scenario_<id>…` (for
  `R3`, `scenario_R3_marksTheRegionTranslated`), driving the app through UI
  Automator or the stack's test runner on the device:

{scenarios}

- Does this app read another app's screen: {fixture}. When it does, the
  project builds a fixture viewer from its own test sources: a second app,
  with its own package id, that shows known pages (vertical-scroll chapters,
  swipe-paged chapters, and pages with sound-effect lettering), so the
  scenarios run against content whose correct answer is known.

## The adapter and the device block

During this turn you may write `.wiki/adapter.toml`, and only during it.
Extend `gate_cmd` so it also builds the instrumented test APK, and the
fixture viewer when there is one: it starts with `{gate}` unchanged, and the
new commands follow, each after ` && `: plain commands with no `;`, `|`, `#`,
backtick, `$(`, brackets, braces, lone `&` or new line, none starting with a
shell's own word such as `exit` or `exec`. For Gradle, add `--no-daemon`.
Commit the adapter with the rest.

End the turn with exactly one fenced block whose info string is
`oneshot-device`, holding JSON:

```oneshot-device
{"apk": "app/build/outputs/apk/debug/app-debug.apk", "package": "com.example.app", "activity": ".MainActivity",
 "tests": {"apk": "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk", "runner": "com.example.app.test/androidx.test.runner.AndroidJUnitRunner"},
 "fixture": {"apk": "fixture/build/outputs/apk/debug/fixture-debug.apk", "package": "com.example.app.fixture"}}
```

Paths are relative to the repository root and are what `gate_cmd` builds.
Leave `fixture` out when the app reads no other app's screen.

## When this turn ends

The server checks, in order:

1. The `done-report` covers every item of the spec's `done` (one per screen),
   each passing.
2. Every pinned file is unchanged since `{tests_head}`.
3. The adapter's `gate_cmd` starts with `{gate}` and adds only ` && `
   commands, and the `oneshot-device` block names each APK.
4. The adapter's gate passes on a clean, committed HEAD, every pinned core
   test passes by its class and name, and each APK the block names is there.
5. The server boots its own emulator, installs the APKs, and opens every
   screen in each state, shooting it and reading its hierarchy. A crash or
   ANR, a view past the screen's edge, a touch target under 48 dp, a control
   with no label, text without contrast, a placement the owner's map forbids,
   a feature whose `feature:<id>` never appears, a `never` feature with a
   control, or a `sometimes` feature not one tap from the first screen is
   refused. Then it runs the instrumented tests; a failed or missing scenario
   test is refused too.

A refusal comes back with the reason, at most three times in all.

## In this turn

- Commit everything and leave nothing uncommitted, then end with the
  `done-report` and the `oneshot-device` block.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Running the gate command or the host-side tests is fine. Do not start an
  emulator or address a device: the server's emulator is the only one used.
- Visible text follows the profile's `ui_language`; Korean text is written
  directly in Korean, never translated from English.
