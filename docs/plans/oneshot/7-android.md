# Step 7 — Android apps

The overall design is in the [overview](0-overview.md). Steps 2 to 6 run one
kind, `webapp`, end to end. This step adds `android`: an app installed on an
Android phone, whose logic runs on the phone and which may call outside APIs.

Goal. A sentence that describes a phone app is restated as `android`, not
refused, and the run carries it through the same stages and the same three
reviews to a first pull request with a tested core, a built APK, and screens
the agent has looked at on the reference device.

## Why this step exists

On 2026-11-15 the owner asked for a real-time screen translator: it reads
Japanese manga on another app's screen, tracks scrolling and page swipes,
skips text it already translated and sound effects, and is tuned for a
Galaxy S21. The intake answered `desktop` and refused it with "지금은 웹앱만".

Two causes. The turn ran inside the hub checkout and took the hub's Tauri work
as the product's context; PR #350 fixes that. The second remains: the kind list
(`webapp`, `api`, `cli`, `desktop`, `data`) has no phone app, and only
`webapp` can run. A web page cannot read another app's screen or draw over it,
so this request cannot be rewritten as a web app either.

## Decided with the owner

2026-11-15, by options.

| Question | Decision |
| --- | --- |
| Platform | Android only. iOS forbids reading another app's screen and needs a Mac to build; a later plan |
| Where S7 looks at the app | A run-owned emulator for every round, plus one round on the owner's connected phone before R3 |
| S3 and S4 for an app with no server | Kept, as the core: S3 designs the core modules and their contracts, S4 builds them contract-test first; R1 stays |

## What exists now

Every stage after S1 assumes a web backend and a browser frontend.

| Stage | Web assumption | Where |
| --- | --- | --- |
| S0 | `kind` is one of five; only `webapp` continues | `oneshot_profile.KINDS`, `SUPPORTED_KINDS` |
| S1 | `responsive` picks desktop or desktop-and-mobile viewports | `oneshot_profile.SCHEMA` |
| S2 | Stack parts are `backend`, `frontend`, `styling`, `data_store`, `tests`, `deploy` | `oneshot_profile.PARTS` |
| S3 | The R1 sheet maps scenarios to endpoints in an API table | `oneshot-design.md`, `oneshot_sheet.py` |
| S4 | Contract tests call the API in the project's test tool | `oneshot_build.py` |
| S5 | Features are read off the built API's endpoints | `oneshot_front.py` (`split`) |
| S6 | Low-fidelity screens are HTML rendered in a browser | `oneshot_front.py`, `oneshot_screens.py` |
| S7 | `live_cmd` serves screens and API on one port; Playwright shoots each route at each viewport | `oneshot_look.py` |
| S8 | README for the chosen OS; the gate runs the backend and end-to-end tests | `oneshot_pr.py` |

This PC has no Android SDK, emulator or system image, and its JDK is 11.

## Design

### S0 — a seventh kind

`KINDS` gains `android`, described to the intake as an app installed on an
Android phone. `SUPPORTED_KINDS` becomes `webapp` and `android`. The intake
prompt says what each kind means, so a phone app is not pushed into
`desktop`. A sentence asking for iOS too is restated as `android`, and the
restatement says that iOS is out of this version.

### Toolchain preflight

Before S1 can be confirmed for `android`, the server checks the toolchain:
the Android SDK command-line tools, platform tools, emulator, one build-tools
and platform version, one `x86_64` system image, and JDK 17. Each one is
found by path and recorded with its version. A missing piece stops the card
with the exact install command.

Installing is a download of several gigabytes onto the owner's machine. The
run never does it unasked: the card offers it as an action the owner presses.
The run records the paths and versions it built with. The receipt names them,
so a later round on another SDK is visible.

The install writes only inside its own folder. Each write, replace and
delete checks right before it acts that its path is under that folder with
no link or junction on the way. A junction that already exists, or one that
appears during a network wait, is refused. A process of the owner's that
swaps folders in the moment between a check and its write is outside this
boundary, because it already holds the rights to the owner's SDK. The
licence answers reach `sdkmanager` from a file as bare `y` lines. Silence is
not a stop for the install: the three-hour limit and the server's stop flag
bound it.

### S1 — the card

For `android`, `responsive` is replaced by `device`: the reference device
whose screen S7 shoots. The options are emulator hardware profiles with their
resolution and density. The default comes from the sentence when it names a
phone (a Galaxy S21 is 1080×2400 at 421 dpi), otherwise a common phone. `os_now`
stays: it is the build host. `auth`, `visibility`, `license`, `ci`,
`ui_language`, `docs_language`, `llm` and `github_owner` keep their meaning.

The saved profile is per kind, so `android.json` holds its own last answers.

### S2 — scenarios and the stack

The conversation is unchanged. The stack parts become `language`, `ui`,
`core`, `data_store`, `tests` and `build` for `android`. The agent offers whole
bundles with reasons, for example Kotlin with Jetpack Compose, Flutter, or
Tauri 2 mobile with a Kotlin plugin. The owner picks one, as in step 6.

Platform capabilities are requirements with the same `confirmed` and
`assumed` status as any other: screen capture (`MediaProjection`), drawing
over other apps, a foreground service, and any permission they need. Each one
carries the user-visible prompt it causes and its distribution risk. For
example, Google Play restricts accessibility services used for anything but
accessibility.

### S3 — core design and R1

S3 designs the core instead of an API. Its parts are the modules and their
contracts (inputs, outputs, errors), the state they keep, what is stored on
the phone, every outside call with its failure handling, and the test plan
from the scenarios. The R1 sheet replaces the API table with a module table
(module, contract in one line, what it calls). For the screen translator, that
is where the owner reads how scrolling is told from page swipes, how a region
already translated is recognised, and how sound effects are skipped. Those
three were the owner's own questions.

An app that never leaves the phone has no outside calls. A module past the
table's budget shows its contract and calls in the folded part, because the
owner approves it too. An R1 edit reaches along `calls` in both directions:
a changed contract moves its callers, and a changed caller may need its
callees changed.

### S4 — core build

Contract tests come first, in the chosen stack's own host-side test runner,
with no emulator, as step 6 decided for every stack: Gradle's JVM unit tests
for Kotlin, `flutter test` for Flutter, `cargo test` for a Tauri core. The
runner writes the same JUnit report, and the pinned cases are checked the same
way. The gate is that runner's test task, the stack's lint, and a debug APK
build: the tests turn names the lint and the APK command beside the test
command, and the server joins the three with ` && `. The independent
read-only review of the diff is unchanged, with a reviewer's brief for an
app's core.

The server runs the contract tests and the gate on the toolchain the card
found. The owner's `ANDROID_*` variables are left out. Gradle's caches and the
SDK tools' preferences, the debug signing key among them, live under the
toolchain's folder. The turns are told the same variables for their own runs.

S3 writes `docs/design/core.md` (the sheet) and `docs/design/core.json` (the
modules and the calls leaving the phone) where a web app gets `backend.md` and
`api.yaml`. The base commit's `.gitignore` adds `local.properties` and the
IDE's folders.

### S5 and S6

S5 reads the features off the core contracts and the platform surfaces: the
app's screens, the overlay's controls, the notification, the settings. The
four columns are unchanged, and so is the map's force: it constrains S6 and S7
as on the web.

On the web a feature is found by its `data-feature` attribute. A native
screen has no DOM, so each feature shown in the UI (`every`, `sometimes`,
`rarely`) carries `feature:<id>` on its control, exported to the native
hierarchy UI Automator reads. A tag alone is not exported, so the stack contract
names the export too:

- Compose: `testTag("feature:<id>")`, with `testTagsAsResourceId = true` on the
  containing hierarchy.
- Flutter: `Semantics(identifier: "feature:<id>")`. A widget `Key` is still
  allowed for the host-side preview tests, but it is not what S7 finds.

S6 finds controls through the stack's own test tree and S7 through the native
resource id; both name the same feature. A `never` feature has no control and
needs no identity.

S6 renders the low-fidelity screens as screenshots without an emulator, with
the stack's own preview tool (for Compose, a JVM screenshot library), at the
reference device's size. The R2 sheet is unchanged.

How 7c builds it:

- S5's inventory is the approved core: every module in the server's copy of
  the design, and every requirement with a `capability`. Each feature names
  its `parts` from that inventory, and every part sits in exactly one feature,
  as every served operation does on the web. A helper module the person never
  touches goes into a `never` feature.
- An S6 screen names its `surface` instead of a route: `screen`, `overlay`
  (drawn over another app) or `notification`. The placement rules are the
  web's.
- The S6 turn stays read-only. Its `oneshot-mocks` block holds the mock
  sources as files, the file that draws each screen, a preview command and
  the folder it writes `<screen id>.png` to. The command follows the same
  plain-chain rule as S4's commands. The server unpacks the reviewed HEAD's
  files, with no Git folder, into the run's folder, adds the mock files, runs
  the command on the card's toolchain and keeps only the pictures.
- A screen's source carries `feature:<id>` for each feature it shows. Each
  picture is a complete PNG (checksums, every row of pixels) at the reference
  device's size, within 3%. It is kept rebuilt from the chunks those checks
  read, its transparency (`tRNS`) included and its other ancillary metadata
  left out.
- The preview command is code the model wrote, and it runs with the server
  account's rights, as S4's contract tests and gate already do for every kind.
  Unpacking into the run's folder keeps the worktree out of the command's own
  folder, but it is not a sandbox: a command that writes elsewhere on purpose
  is not stopped. The owner decided on 2026-11-15 to keep this boundary for
  7c. Isolating model-written code covers S4, S6 and S7 together and is its own
  later step, not part of 7c.
- The command's deadline is 30 minutes, and a quiet spell does not cut it
  sooner: a first run downloads its Gradle dependencies.
- An earlier version's `kind` stop at a stage this version runs becomes a
  resumable `restart` stop when the server starts.
- An edit turn keeps the shared preview files and the command as they were,
  as it keeps the tokens.
- R2's approval commits `docs/design/screens.md`, the mock files under
  `docs/design/mock/` and the pictures under `docs/design/shots/`, and starts
  the screen build.

### S7 — the emulator loop

Each round installs the debug APK on an emulator the run owns. Isolation is
the Android collector's from the native WebView plan
([native-webview](../native-webview/0-overview.md), step 3). It covers an AVD under
an attempt-owned home, the run's own ADB server with discovery off, ports
outside the default scan, and cleanup proven on every outcome. That collector
is on hold. This step resumes it as shared infrastructure instead of building
a second one.

How 7d builds it:

- The owned emulator is one module, `tool/android_owned.py`. Its `boot()`
  yields a booted serial and cleans on every exit. The WebView driver uses
  it, and S7 will use it in 7e. The DevTools proxy, the head-built APK and the
  receipt redesign from the native WebView plan's step 3 stay on hold with
  that plan.
- The driver no longer starts an emulator or calls `adb` itself. Every call
  goes through the module's command line. The module's `adb` client connects
  only to a relay the module holds, and the relay joins a connection to the
  server only after the TCP table shows the recorded server answering it.
  A check made before the call could not stop a request from reaching a
  server that took the port in between. An approval names the system image
  folder instead of an AVD template.
- The owned server's discovery is closed by `ADB_EMU=0`,
  `ADB_LOCAL_TRANSPORT_MAX_PORT=5554`, `ADB_USB=0` and `ADB_MDNS=0`. These are
  read in adb's own source (`client/main.cpp`, `client/mdns_utils.cpp`),
  though adb's built-in help lists only the scan port. The floor is the release
  verified here, 37.0.1, so an older `adb` is refused rather than trusted.
- The orchestrator cleans from the record after every desktop flow, whatever
  its outcome, and holds the flow at preparation while anything is left.
- Cleanup differs from the native WebView plan's wording on one point. It
  sends no `kill-server` or `forward --remove`, since either reaches whatever
  holds the port when the client connects. It kills the recorded server by
  identity, and its forwards close with it.
- One real run on this PC used another project's SDK, read-only: platform tools
  37.0.1, emulator 37.2.13 and an `android-34` Google APIs x86_64 image on
  WHPX. It booted in 79 seconds. The owned server listed only `emulator-5586`.
  A server started mid-run with ordinary discovery listed no device. Cleanup
  left no recorded process, listener or folder. The default server it found
  on 5037 kept its identity and its port.
- The emulator cannot create its own home folder, so the module creates it.
  Without it, the emulator retried its lock file forever and wrote gigabytes
  of log before the boot deadline.

1. Boot the AVD with the reference device's profile, install the APK, grant
   only the permissions the scenarios state, start the app.
2. Drive each core scenario through the platform's UI automation (UI Automator
   or the stack's test runner) and screenshot each state.
3. Mechanical checks, all high when they fail: a crash or ANR in the app's
   logcat, a view outside the screen, touch targets under 48 dp, text cut off,
   contrast, and the accessibility scanner's findings.
4. Placement, also high, with the web's rules read off the UI hierarchy: an
   `every` feature is visible on the first surface, a `sometimes` one is
   reached in one action, a `rarely` one is absent from the primary surface,
   and a `never` one has no control anywhere in the UI (its logic stays). An
   `every`, `sometimes` or `rarely` feature whose identity is missing from the
   native hierarchy is a finding too. A `never` feature with no control
   passes. A round with any finding here is not clean.
5. The separate critic reads the screenshots in the `design-pass` order, as on
   the web.
6. Fix, then go to 1. The stop rule and the round cap are the web's.

An app that reads another app's screen needs that other app. The run installs
a fixture viewer from its own test sources, which shows known pages:
vertical-scroll chapters, swipe-paged chapters, and pages with sound-effect
lettering. The scenarios then run against content whose correct answer is
known.

How 7e builds it:

- The build turn is the web's `build-front` turn with its own prompt. An app
  on a phone has no `live_cmd`. Instead, the turn ends with an
  `oneshot-device` block naming the debug APK, its package and launch
  activity, the instrumented test APK with its runner, and the fixture viewer
  when the app reads another app's screen. A capability whose permission names
  MediaProjection or an accessibility service counts as reading another app's
  screen. The gate it publishes starts with S4's gate and adds the test APK's
  build. After the gate, every APK the block names must be a plain file in
  the tree.
- The server, not a model, drives the emulator (`tool/main/oneshot_device.py`).
  Each look boots a fresh `android_owned` emulator at the reference phone's
  size. It first cleans every earlier look's record, so a look a server crash
  cut short is cleaned before the next boots, and one that cannot be cleaned
  stops the look. Then it installs the APKs. It grants only the
  `android.permission.*` names the approved requirements state: overlay
  drawing goes through `appops`, the others through `pm grant`, and screen
  capture consent goes through `PROJECT_MEDIA` for a screen-reading app.
- A screen opens in a state the way a web route opens with a seeded or held
  API: the debug build reads the launch extras `oneshot.screen` and
  `oneshot.state` and draws that screen in that state from its own sample
  data. An overlay is shot over the fixture viewer, or the launcher when
  there is none. A notification is shot with the shade expanded. Each state
  is saved as `<screen>-phone-<state>.png`, the name the critic already reads.
- The hierarchy is UI Automator's dump. A feature is found by `feature:<id>`
  as a resource id (Compose's exported test tag, Flutter's identifier), or as
  a content description on a notification's views, which carry no tag.
- The mechanical checks and the placement read the app's own nodes, all
  high. Those are its package's, and every view under a `feature:<id>` that
  System UI draws, which is how a notification's controls are found in the
  shade. The fixture viewer's views stay out, tagged or not:
  - a crash or ANR in the event log since the screen opened;
  - a view past the screen's edge;
  - a clickable node under 48 dp;
  - a clickable node with no text or description in it;
  - text whose ink and ground contrast less than 3:1. That last one compares
    the darkest and lightest twentieth of the raw frame's pixels inside the
    text's box.

  Text cut off is the critic's: the hierarchy carries the whole string
  whether or not it is ellipsized.
- The placement rules are the web's, read off the first screen's hierarchy
  in the `full` state. One action is one tap on a control in view, after
  relaunching the first screen. A feature that never appears in any `full`
  hierarchy is a finding, and so is a `never` feature in any state's.
- The scenarios run once per look as `am instrument -w -r` on the test APK.
  Each scenario requirement needs a test named `scenario_<id>` or
  `scenario_<id>_…`, so `R10`'s test never stands in for `R1`'s. A failed,
  skipped or missing one is high. So is a run that did not end with
  `INSTRUMENTATION_CODE: -1`, or one with a line that starts an abort, a
  failure or a crash record. A test whose name holds those words is not one.
- The critic names an Android screen by its id where a web screen has its
  route. Two clean rounds open the phone round (below). A run an earlier
  version stopped at `build-front` becomes
  resumable when the server starts.
- A real emulator on this PC checked the harness's commands, driving the
  Settings app with the other project's SDK, read-only:
  - the launch and its `Status: timeout` through a trampoline activity;
  - the hierarchy dump, a tap, the raw frame's header and the PNG;
  - the event log, and the non-zero exit of a missing instrumentation.

  Cleanup left nothing, and the 5037 server kept its identity.

### Before R3 — one round on the owner's phone

After the emulator loop stops, the run asks the owner to connect the reference
phone over USB with debugging on. This is one prompt, not a fourth review.

The run waits for the phone. The state is saved and resumable: no timeout
drops the round, and a restart returns to the same prompt. R3 opens only on a
phone round that passed on the exact APK and head R3 shows. A failed scenario,
crash or high finding on the phone goes back into the emulator loop as that
round's findings, then to the phone again. A new head makes an earlier phone
result stale. The owner can still stop the run, as at any stage.

This is a separate physical-device path. The emulator collector keeps
refusing physical devices, and nothing here weakens that.

- Which phone. The prompt lists the connected devices with their serial and
  model, and the owner picks one. Every ADB command names that serial
  explicitly, and so does every retry and cleanup. A phone with another serial is never
  addressed, even when it is the only one connected.
- Which packages. The app and the fixture viewer install under run-specific
  package ids (an application id suffix carrying the run id). Before any
  install the run checks that neither id exists on the phone; if one does, it
  stops and says so instead of reinstalling over it.
- What the run owns. Before installing, it saves an ownership record: the
  serial, both package ids and the APK's hash. Cleanup uninstalls only what
  that record names, then checks both packages are gone. It runs on every
  outcome, pass, fail, cancel or disconnect. A cleanup that could not finish
  stays pending across restarts, is retried when the same serial reconnects,
  and is shown on the run until it is done. When the owner keeps the app, the
  record is released instead.

On that phone the run does only three things: install the two packages, run
the same scenarios with screenshots and the placement check, and read the
app's own frame timing (`dumpsys gfxinfo` for its package) and memory. It
touches no other app or data.

How 7f1 builds it (`tool/android_phone.py`, `tool/main/oneshot_phone.py`):

- The phone is reached through the run's own ADB server, decided with the
  owner on 2026-11-16: `android_owned`'s server with its USB backend on and
  emulator scan and mDNS off, on its own port, cleaned by the same record.
  Port 5037 is never used. Windows lets one ADB server hold a USB phone, so
  when another server holds it the list is empty, and the prompt says to close
  that server. One phone server runs at a time.
- The server is started per action: listing the phones on `[휴대폰 찾기]`,
  and the round itself. Every device command waits until `bind` names the
  picked serial, and passes it with `-s`.
- The suffix is `.oneshot<run id>`. The build-front turn sets it as the debug
  build's `applicationIdSuffix`, on the fixture viewer too, and names the
  activity by its full class. The block's packages must end with it, and the
  test package follows the app's. So the emulator's look already installs
  the ids the phone gets.
- The APKs are build outputs Git does not track, so a build outside the run
  can change them. Both devices install copies in the run's folder. The
  emulator's look copies the APKs after the gate, installs the copies and
  saves their SHA-256s. The round holds the worktree on the head that look
  passed, copies the APKs again, and checks each copy against the saved
  digest. It installs from those copies. A moved head, or an APK that is
  missing or changed, goes back to the build.
- Before installing, the round checks:
  - the picked serial is listed as `device`;
  - the screen is not locked (after a wake key);
  - no package exists, read exactly off `pm list packages`. An existing one
    stops the round with no record and nothing installed or removed.
- Installs pass `-R`. Android's package manager replaces an existing
  installation by default and ignores `-r`. So an id another installer takes
  after the check fails the install instead of being replaced. After each
  install, the digest of the `base.apk` that `pm path` names must match the
  passed one. That path must lie under `/data/app` or an adopted volume's
  `/mnt/expand/<uuid>/app` before it is sent to the phone's shell.
- The record goes in `oneshot.phone_owned`, one entry per round. It names each
  package with the digest it must hold, and is saved before the first install.
  A package joins its `installed` map once its install command succeeds,
  which under `-R` proves the installation is the run's. Right after, the map
  records which installation it is. That is the `base.apk` path, which the
  package manager draws afresh on each install since Android 11, with the
  file's inode and modification time in epoch seconds from `stat`. A
  reinstall writes them anew, and a time zone change does not touch them.
  The record is settled on every exit:
  - `cleaned`;
  - `pending`, with what is left;
  - `unsure`, for a package on the phone that cannot be tied to the run's
    installation. Examples are a crash between the record and the install, or
    the owner reinstalling the same APK while the cleanup waited. The same APK
    the owner installed is not the run's, so it is shown and never removed;
  - `released`, when the owner kept a passing app.
- The cleanup removes a package only when it is still the installation in
  `installed` and its `base.apk` holds the recorded digest. Another APK is
  listed as `foreign` and left alone.
- The pick clears the stop before the round is published, so a stop that
  arrives before the worker runs holds. The round checks for a stop after the
  scenarios and after the measurements. At its end, under one lock with the
  stop handler, it settles what stays on the phone. A stop accepted before
  that point means the round never passes and its apps come off, even when
  the owner chose to keep them. After that point a stop is refused. A later listing that shows the serial settles every `owning`,
  `pending` or `unsure` entry for it again. The record also covers a round a
  restart cut.
- The listing runs at any stage except during the round itself. The run's
  notice of an unfinished cleanup carries `[휴대폰 찾기]`, so a passed round's
  cleanup left pending at R3 can still be finished.
- The screens use the shared inspection with 7e. Mechanical checks use the
  phone's own `wm size` and `wm density`. A crash is read from the event log
  since the screen opened, by the device's clock, never by clearing the log.
  The emulator does the same.
- Frame timing comes from `dumpsys gfxinfo` after a reset and four swipes on
  the first screen in `full`: frames, janky share, the 50th, 90th and 99th
  percentiles. Memory is the total PSS from `dumpsys meminfo`. Neither is a
  finding; R3 shows them.
- The outcome decides what follows:
  - a pass stops with reason `kind` at `r3` until 7f2 brings R3's sheet;
  - a high finding goes to the build-front turn as the round's fix, and the
    critic's stop rule counts its two clean rounds afresh;
  - a phone missing, locked, holding an id, stopped or failing returns to the
    prompt with the reason.
- A restart mid-round returns to the prompt on `[재개]`. A run an earlier
  version stopped at `r3` for its kind waits for the phone instead.
- The spine shows the round for an Android run only.

### R3 and S8

R3 shows the emulator's final screenshots, the phone's screenshots and frame
timing, and one walkthrough per core scenario. S8 writes a README with the
build and install commands, attaches the debug APK to the pull request's
summary, and adds an Android build workflow only when the repository is
public. The review loop runs on the pull request as for every kind.

## What does not change

- The three reviews, the decide-and-flag rule between them, and the stop list.
- Korean gate sheets with the translationese check.
- Bootstrap, the base commit and the `oneshot-initial` worktree.
- `webapp` runs exactly as before.

## Steps

Each step is a separate PR, approved before the next starts.

| # | Stage | Deliverable | Status |
| --- | --- | --- | --- |
| 7a | Kind and toolchain | `android` in the kinds with the intake's definitions, the toolchain preflight with the owner-pressed install, the `device` field and `android.json` profile | Done |
| 7b | Core path | S2 stack parts and capability requirements for `android`, the S3 module design and R1 module table, S4 contract tests and gate in the stack's own host-side runner | Done |
| 7c | Screens without an emulator | S5 from contracts and platform surfaces, S6 rendered by the stack's preview tool at the device size | Done |
| 7d | Owned emulator | The native-webview step 3 collector resumed as shared infrastructure, with its refusals and cleanup proof | Done |
| 7e | Emulator loop | S7 on the owned emulator: install, scenarios, screenshots, mechanical and placement checks resolved from the real native hierarchy (cases: a build that moves a feature from its approved place, a correctly absent `never` feature, a leaked `never` control), critic, fixture viewer | Done |
| 7f1 | Phone round | The connected-phone round before R3: the resumable wait, the bound serial, run-specific packages, the ownership record and its cleanup | Done |
| 7f2 | R3 and S8 | The R3 sheet with the phone's results, S8's README, APK and optional workflow | Planned |
| 7g | Real run | The screen translator sentence from intake to the first pull request | Planned |

7a alone stops the refusal: the run continues to S1, and later stages wait
behind a stated reason until their step lands. With 7b the run goes through
the bootstrap and the core build. With 7c it goes on through the split, the
board, the screens and R2. Once R2 approves, it stops with reason `kind` before
the frontend build. A card that 7a stopped opens again on
resume, so the owner can confirm it into the bootstrap. The wait is enforced where a
stage starts, at the automatic handoffs (S2 to design, R1 to tests, R2 to the
build) and the owner's resume routes alike, not only by hiding a button. 7b and 7c need no emulator.
7d to 7f2 need the toolchain on this PC. On 2026-11-16 the owner split 7f
into 7f1 and 7f2, so each PR's review stays short.
