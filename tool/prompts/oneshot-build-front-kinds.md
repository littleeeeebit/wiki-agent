This turn builds the screens the person approved for each kind they picked,
{kinds}, on the one core that is built and reviewed.

The design is committed in this worktree: `docs/design/screens.md`, grouped by
kind, with each kind's mock sources under `docs/design/mock/<kind>/` and
their pictures under `docs/design/shots/`. The chosen palette, type scale and
spacing, which every kind shares, then each kind's approved screens, states,
features and the owner's frequency map for those features:

```json
{design}
```

The environment profile: `{profile}`.
<!-- kind:webapp -->
The web screens are shot at {viewports}.
<!-- /kind -->
<!-- kind:android -->
The reference phone is {device}.
<!-- /kind -->
<!-- kind:desktop -->
The look sets the PC app's main window to {window} physical pixels, its
frame included.
<!-- /kind -->

The core's contract tests were committed at `{tests_head}` and are pinned
with the test runner's configuration; these files may not change at all:

{contract}
<!-- kind:android -->

The Android toolchain is under the application's folder. Every command you run
for this project sets these variables first, as the server's runs do:

{toolchain}
<!-- /kind -->
<!-- kind:desktop -->

{tools}
With a PC app among the kinds, this rule holds for every command of
`gate_cmd`: the core's, each kind's tests and each kind's build.
<!-- /kind -->
<!-- kind:webapp -->

## The web app

The web app follows the stack's `webapp.backend`, `webapp.frontend` and
`webapp.styling`, on the core.

- Each web screen opens at its own route as a path: the screen `/months` is
  served at `/months`, on a fresh load as well as by a link, and no screen
  route meets an API path.
- The app serves the built screens and the API on one port. It reads the port
  from the environment variable `PORT` and keeps all its data under the folder
  in `APP_DATA_DIR`, creating what it needs there; the core's contract tests
  keep passing.
- Every control for a feature carries `data-feature="<feature id>"`. Each
  screen shows its states: empty, loading (while the API has not answered),
  error (the API answered 500) and full.
- `e2e/seed.json` lists the API calls that fill the app the way a person
  would, `[{"method": "POST", "path": "/…", "body": {…}}]`; each must succeed
  on an empty data folder.
<!-- /kind -->
<!-- kind:android -->

## The Android app

The Android app follows the stack's `android.ui` and `android.build`, on the
core. The server installs the debug APK on an emulator it owns, at the
reference phone's size, and drives it with `adb` and UI Automator. It grants
the app only these permissions, the ones the approved Android requirements
state: {permissions}.

- Each screen shows where its `surface` says: `screen` in the app's own
  activity, `overlay` drawn over another app, `notification` posted to the
  shade.
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
  build. A `never` feature has no control anywhere, in any state.
- Each Android core scenario has an instrumented test named `scenario_<id>_…`
  (for `R3`, `scenario_R3_marksTheRegionTranslated`; `R30`'s never counts for
  `R3`), driving the app on the device. The run must finish: an aborted or
  crashed instrumentation fails every scenario.

{scenarios}

- Each check the backend review left for the phone has an instrumented test
  named `owed_<id>_…` (for `O1`, `owed_O1_overlayClearsTheStatusBar`) that
  drives the app on the device and asserts that check's postcondition.

{owed}

- The debug build's application id ends with `{suffix}`: in Gradle,
  `applicationIdSuffix = "{suffix}"` on the debug build type, the fixture
  viewer's too, and the instrumented tests' package follows the app's. Before
  R3 the run installs these exact APKs on the owner's phone, so no id may
  name an app already there.
- Does this app read another app's screen: {fixture}. When it does, the
  project builds a fixture viewer from its own test sources: a second app,
  with its own package id, that shows known pages, so the scenarios run
  against content whose correct answer is known.
<!-- /kind -->
<!-- kind:desktop -->

## The PC app

The PC app follows the stack's `desktop.ui`, on the core.

{desktop_conventions}
<!-- /kind -->

## The adapter and the blocks

During this turn you may write `.wiki/adapter.toml`, and only during it.
<!-- kind:webapp -->
Fill `live_cmd` with one command that, run from the repository root, builds
the web screens if needed and starts the web app (it reads `PORT` itself).
<!-- /kind -->

Extend `gate_cmd` so it also runs each kind's tests and build:

<!-- kind:webapp -->
- the web app's tests and build;
<!-- /kind -->
<!-- kind:android -->
- the Android app's debug APK, its instrumented test APK and the fixture
  viewer when there is one;
<!-- /kind -->
<!-- kind:desktop -->
- the PC app's debug program, built into a folder of its own;
<!-- /kind -->

It starts with `{gate}` unchanged, and the new commands follow, each after
` && `: plain commands with no `;`, `|`, `#`, backtick, `$(`, brackets,
braces, lone `&` or new line, none starting with a shell's own word such as
`exit` or `exec`. For Gradle, add `--no-daemon`. Put anything more involved
in a project script. Commit the adapter with the rest.
<!-- kind:android -->

The turn ends with one fenced block whose info string is `oneshot-device`,
holding JSON:

```oneshot-device
{"apk": "android/app/build/outputs/apk/debug/app-debug.apk", "package": "com.example.app{suffix}", "activity": "com.example.app.MainActivity",
 "tests": {"apk": "android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk", "runner": "com.example.app{suffix}.test/androidx.test.runner.AndroidJUnitRunner"},
 "fixture": {"apk": "android/fixture/build/outputs/apk/debug/fixture-debug.apk", "package": "com.example.app.fixture{suffix}"}}
```

Paths are relative to the repository root and are what `gate_cmd` builds.
`activity` is the full class name: the suffix changes the package, not the
classes. Leave `fixture` out when the app reads no other app's screen.
<!-- /kind -->
<!-- kind:desktop -->

The turn ends with one fenced block whose info string is `oneshot-desktop`,
holding JSON:

{desktop_block}
<!-- /kind -->

## When this turn ends

The server checks, in order:

1. The `done-report` covers every item of the spec's `done` (one per screen
   of every kind), each passing.
2. Every pinned file is unchanged since `{tests_head}`.
3. The adapter's `gate_cmd` starts with `{gate}` and adds only ` && `
   commands, at least one for each kind; {checked_blocks}.
4. The adapter's gate passes on a clean, committed HEAD and every planned
   core test passes.
<!-- kind:android -->
   Each APK the `oneshot-device` block names is there.
<!-- /kind -->
<!-- kind:desktop -->
   The PC app's `run.folder` and `run.program` are there.
   {desktop_freeze}
<!-- /kind -->
5. Each kind's screens are then checked in the order the owner picked the
   kinds, each against only the features that apply to it.
<!-- kind:webapp -->
   - The web app: the server starts it with `live_cmd` on a fresh data folder
     and again after `e2e/seed.json`, and shoots every web screen at
     {viewports} in each state. A console or page error, a placement the
     owner's map forbids, a feature that never appears, or a `sometimes`
     feature not one click from the first screen is refused.
<!-- /kind -->
<!-- kind:android -->
   - The Android app: the server boots its own emulator, installs the APKs,
     and opens every Android screen in each state, shooting it and reading its
     hierarchy. A crash or ANR, a view past the screen's edge, a touch target
     under 48 dp, a control with no label, text without contrast, a placement
     the owner's map forbids, a feature whose `feature:<id>` never appears, a
     `never` feature with a control, a `sometimes` feature not one tap from the
     first screen, or a failed or missing scenario or owed test is refused.
<!-- /kind -->
<!-- kind:desktop -->
   - The PC app: {desktop_look}
<!-- /kind -->

   A kind that fails sends its findings back here, and on the next HEAD every
   kind is checked again from the first.

A refusal comes back with the reason, at most three times in all.

## In this turn

- Commit everything and leave nothing uncommitted, then end with the
  `done-report`{end_blocks}.
- Do not push or open a pull request. Decide within the approved design; ask
  the owner only a decision that is theirs, as the end of this message says.
- Running the gate command or the host-side tests is fine. Install nothing.
<!-- kind:android -->
- Do not start an emulator or address a device: the server's emulator is the
  only one used.
<!-- /kind -->
<!-- kind:desktop -->
- Do not start the PC app yourself: the server starts it, on the owner's
  desktop where they see it, and only there.
<!-- /kind -->
- Visible text follows the profile's `ui_language`; Korean text is written
  directly in Korean, never translated from English.
- Build and test with the gate command's own package selection, features and profile
  each time: every other flag set builds its own copy of the dependencies, and
  the next build starts from that copy instead of what was just built. Never
  clean the build cache (`cargo clean`, deleting `target`, `gradle clean`):
  a warm cache rebuilds only the modules a fix changed (the server bounds Cargo's incremental cache).
