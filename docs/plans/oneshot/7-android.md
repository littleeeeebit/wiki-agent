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

### S4 — core build

Contract tests come first, in the project's own test tool, and run on the JVM
without an emulator. The gate is that tool's test task, its lint, and a debug
APK build. The independent read-only review of the diff is unchanged.

### S5 and S6

S5 reads the features off the core contracts and the platform surfaces: the
app's screens, the overlay's controls, the notification, the settings. The
four columns are unchanged.

S6 renders the low-fidelity screens as screenshots without an emulator, with
the stack's own preview tool (for Compose, a JVM screenshot library), at the
reference device's size. The R2 sheet is unchanged.

### S7 — the emulator loop

Each round installs the debug APK on an emulator the run owns. Isolation is
the Android collector's from the native WebView plan
([native-webview](../native-webview/0-overview.md), step 3). It covers an AVD under
an attempt-owned home, the run's own ADB server with discovery off, ports
outside the default scan, and cleanup proven on every outcome. That collector
is on hold. This step resumes it as shared infrastructure instead of building
a second one.

1. Boot the AVD with the reference device's profile, install the APK, grant
   only the permissions the scenarios state, start the app.
2. Drive each core scenario through the platform's UI automation (UI Automator
   or the stack's test runner) and screenshot each state.
3. Mechanical checks, all high when they fail: a crash or ANR in the app's
   logcat, a view outside the screen, touch targets under 48 dp, text cut off,
   contrast, and the accessibility scanner's findings.
4. The separate critic reads the screenshots in the `design-pass` order, as on
   the web.
5. Fix, then go to 1. The stop rule and the round cap are the web's.

An app that reads another app's screen needs that other app. The run installs
a fixture viewer from its own test sources, which shows known pages:
vertical-scroll chapters, swipe-paged chapters, and pages with sound-effect
lettering. The scenarios then run against content whose correct answer is
known.

### Before R3 — one round on the owner's phone

After the emulator loop stops, the run asks the owner to connect the reference
phone over USB with debugging on. This is one prompt, not a fourth review. On
that phone the run only does three things:

- Install the debug APK under its own package, and the fixture viewer.
- Run the same scenarios and take screenshots.
- Read the app's own frame timing (`dumpsys gfxinfo` for its package) and its
  memory.

It touches no other app or data, and it uninstalls both packages afterwards
unless the owner keeps them. A phone that is not connected within the wait
skips this round, and the R3 sheet says so at the top.

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
| 7a | Kind and toolchain | `android` in the kinds with the intake's definitions, the toolchain preflight with the owner-pressed install, the `device` field and `android.json` profile | Planned |
| 7b | Core path | S2 stack parts and capability requirements for `android`, the S3 module design and R1 module table, S4 contract tests and gate in the project's tool | Planned |
| 7c | Screens without an emulator | S5 from contracts and platform surfaces, S6 rendered by the stack's preview tool at the device size | Planned |
| 7d | Owned emulator | The native-webview step 3 collector resumed as shared infrastructure, with its refusals and cleanup proof | Planned |
| 7e | Emulator loop | S7 on the owned emulator: install, scenarios, screenshots, mechanical checks, critic, fixture viewer | Planned |
| 7f | Phone round and S8 | The connected-phone round before R3, the R3 sheet, S8's README, APK and optional workflow | Planned |
| 7g | Real run | The screen translator sentence from intake to the first pull request | Planned |

7a alone stops the refusal: the run continues to S1, and later stages wait
behind a stated reason until their step lands. 7b and 7c need no emulator.
7d to 7f need the toolchain on this PC.
