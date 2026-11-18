# Step 9 — Kinds the owner picks, several in one run

The overall design is in the [overview](0-overview.md). Steps 2 to 7 run one
kind per run, and the intake's model picks it. This step lets the owner pick
the kinds, one or several, and carries a run of several kinds through one
repository: one conversation and one core, then each kind's screens and
checks. It also adds `desktop`, a program installed on Windows.

Goal. The owner picks every kind on the start screen. No agent picks, guesses
or changes one. A run of several kinds reaches one first pull request with a
shared, tested core and, for each kind, its screens checked the way that kind
is checked.

## Why this step exists

On 2026-11-16 the owner started the screen translator: it reads Japanese
manga on another app's screen on a Galaxy S21, and the owner also wants it on
a PC. The intake prompt already says a sentence naming a phone model, or
reading another app's screen, is `android`. The model still answered
`webapp`. It also restated the app as one "used in a web browser on mobile
and PC", and the card showed the kind only as text. The owner confirmed it.

Then S2 turned the app into an installed Tauri 2 app for the Galaxy and
Windows, with a Kotlin plugin that captures the screen and draws over it.
Nothing changed the kind. The run went on as `webapp`:

- the backend review refused it 25 times in 17 hours, on behaviour only a
  phone can show (scroll tracking, the overlay's insets);
- it spent USD 1,128 by the 25th refusal;
- the stages after the review will check a Svelte settings page in a browser,
  and no stage will ever see the overlay.

Two causes. The kind is the model's to pick, and a run has exactly one. The
translator is two kinds at once, and the owner knew which.

## Decided with the owner

2026-11-17, by options.

| Question | Decision |
| --- | --- |
| Who picks the kind | The owner, on the start screen with the sentence. Nothing is preselected, and no agent sets or changes it |
| Several kinds | In this plan |
| How a run of several kinds is built | One repository: S2 and the core (S3, S4, R1) once for every kind, then the screens and their checks for each kind |
| `desktop` | In this plan, so the translator's Windows side can be built |
| A PC app's stack (2026-11-18) | Free, picked in S2 like a web app's: Tauri 2, .NET with WinUI, Electron or another. The run checks the tools the picked stack names and installs none |
| What the owned desktop cannot show (2026-11-18) | A round on the owner's own desktop before R3, approved by the owner at that time, as the Android phone round is. Plan 9's gate is unchanged |

## What exists now

| Where | One kind, picked by the model |
| --- | --- |
| Start screen | The sentence and the three models; no kind |
| S0 | `oneshot-intent` returns `kind`; `intent_of` checks it is one of `KINDS` |
| Card | `종류` shown as text; the owner can only type a correction for the intake to restate |
| Record | `intent.kind`, one string; `kind_of(o)` reads it in 9 modules, and `oneshot.py` reads `intent["kind"]` directly for the card, the profile and the bootstrap |
| Prompts | A web prompt and an `-android` one for each of 8 turns |
| S1 | `schema(kind)`: `responsive` for a web app, `device` for an Android app |
| S2 | `parts(kind)`: `PARTS` or `ANDROID_PARTS` |
| S3 | A web app's API table, or an Android app's modules (`M`), outside calls (`X`) and permissions |
| S5–S7 | Playwright on a web app's routes, or the run's emulator and the owner's phone for an Android app |
| `desktop` | In `KINDS` for the intake, which marks it unsupported; the restatement cannot be confirmed, so the run never reaches S1, and no stage builds it. Every non-Android kind would get the web parts, prompts, API sheet and API-based split |

## Design

### The kinds on the record

The run record holds `kinds`, the owner's list in the order they picked, one
or more of `SUPPORTED_KINDS`. The server writes them into the intent as
`kinds`, and the intent's `kind` is the first of them, so every stage of one
kind reads it through `kind_of(o)` unchanged. A record or spec from before
this step has only `intent.kind` and reads as `[kind]`.

### S0 — the start screen and the intake

The start screen asks what is being made, beside the sentence, as one
checkbox per kind: `웹앱` (in a browser), `Android 앱` (installed on the
phone), `PC 앱` (installed on Windows). None is checked, and the start button waits
for at least one. A kind this version cannot build yet is shown disabled,
with the step that adds it.

The intake receives the kinds with the sentence. It restates the product as
those kinds and returns no `kind`. It may say, in its answer, that the
sentence asks for something the picked kinds cannot do. For example, a web
app cannot draw over another app. The owner then goes back and picks again;
the agent changes nothing. The card shows the kinds as the owner picked them.

### S1 — the card

The card's fields are the union of every picked kind's: `responsive` for a
web app, `device` for an Android app, and for `desktop` the window size S7
shoots at. Shared fields (`visibility`, `license`, `ci`, `ui_language`,
`docs_language`, `llm`, `github_owner`, `auth`) appear once. Each kind's
saved profile still holds its own last answers. A shared field is prefilled
only when every picked kind's saved profile holds the same value; otherwise
it starts empty and the owner fills it. Once the card is confirmed, each
picked kind's saved profile is written with the shared fields and that
kind's own fields.

### S2 — one conversation

One conversation covers every picked kind. A run of `webapp` alone or
`android` alone keeps today's parts. A run of `desktop` alone takes
Android's six parts (`language`, `ui`, `core`, `data_store`, `tests`,
`build`) and its core path (below). A run of several names the shared parts once (`core`, the language and
framework of the logic every kind uses, `data_store` and `tests`) and each
kind's own parts with that kind:

| Kind | Its own parts |
| --- | --- |
| `webapp` | `backend` (the server), `frontend`, `styling`, `deploy` |
| `android` | `ui`, `build` |
| `desktop` | `ui`, `build` |

A requirement may name the kinds it applies to; one that names none applies
to all. A platform capability (screen capture, drawing over other apps) names
its kind and carries its prompt and risk, as in step 7. Which picked kinds a
requirement applies to is the owner's. Every kind a requirement applies to
must be able to meet it. When one cannot, the agent asks, as one `choices`
question, whether to narrow the requirement to the picked kinds that can
meet it, or to change the requirement. A mixed run whose platform
capabilities each name a capable kind is valid. A web app and an Android app
that capture the screen only on Android is one.

The run cannot go on when the owner keeps a requirement applying to a kind
that cannot meet it, by answering neither narrow nor change. It also cannot
go on when none of the picked kinds can meet a confirmed requirement. The
agent then says so in the person's words, adds `"kinds_mismatch"` with the
requirement and its reason to the `oneshot-spec` block and asks nothing more. The run stops with reason
`rekind`, which no `[재개]` takes on: the owner starts a new run with the
kinds they now pick. The agent never names the kinds the run should have.

### S3, R1 and S4 — one core

The core design is step 7's shape for every run of several kinds and for
`desktop` alone: modules (`M`) with their contracts, outside calls (`X`),
assumptions (`A`) and each kind's permissions. A web app among the kinds adds
its server's API as endpoints (`P`), the prefix the web sheet already uses,
which the other kinds may call. The R1 sheet and its brief show, for each
kind, the modules it uses and the permissions it asks for. S4 builds the core
contract-test first, in the stack's host runner, once.

### The backend review and what only a device can show

The backend review reads the core once. A finding may be deferred to a
device only when the evidence it asks for is the app running on a phone or
in a Windows session, which the host's contract tests cannot run: scroll
tracking in another app, an overlay's insets, a permission prompt. The
reviewer marks it `device`, naming the kind, the requirement and the check
that would show it: a scenario test, run in that kind's S7 loop, whose
postcondition is the finding's. Anything the code or the host tests can show
wrong stays a blocker, as today: wrong logic, a missing or wrong contract
test, a contract the code breaks.

A review whose only P0 and P1 findings are `device` findings approves. Each
deferral is saved in the spec's `owed` list with its kind, requirement,
finding and check, and survives a restart. The kind's S7 loop runs each owed
check on the device. A clean round counts only when every owed check for
that kind passed on that round's HEAD; a failing one goes to the builder as a
finding. R3 shows each owed item with the round that closed it. A run never
reaches R3 with an owed check unrun.

### S5 to R2 — screens for each kind

The split reads features off the core once. Each feature carries the kinds
it applies to: the kinds every one of its parts applies to. A part that
applies to all kinds does not narrow it, and an Android-only capability
makes the feature Android's. A feature whose parts leave no kind is refused
at the split. The board is shown once, since how often a feature is used
does not depend on the kind, with a first screen for each kind. S6 designs
and previews each kind's screens, the way that kind does. Each screen
carries its `kind`, and R2 shows every kind's screens in one sheet, grouped
by kind, one confirmation line per screen. Every placement, coverage and
scenario check in a kind's S6 and S7 reads only the features that apply to
that kind.

### S7 to R3 — each kind's loop, one R3

The frontend build covers every kind. Its gate runs the core tests once and
each kind's build. S7 then runs each kind's loop in the order the owner
picked the kinds:

- a web app: Playwright on its routes;
- an Android app: the run's emulator, then the owner's phone;
- a PC app: the run's own Windows session (below).

A loop that fails sends its findings to the build, and every loop that
already passed runs again on the new HEAD. R3 lists every kind's findings,
each line naming its kind and screen. S8 opens one pull request, with a
README section and the release artifacts for each kind (an APK, a Windows
installer).

### `desktop` — a program installed on Windows

`desktop` is built on Windows only in this plan; macOS is a later plan.

- S1's `window` field is the size S7 shoots at.
- S2's stack is the owner's: for example Tauri 2, .NET with WinUI, or Electron.
- S6 previews the screens the way the stack's own preview tool renders them.
- S7 needs a session the run owns: it starts the built app, drives each core
  scenario and shoots each screen and state, then closes everything it
  started.

The session is a Win32 desktop the run owns inside the owner's session: the
app starts on it in a kill-on-close job, a driver on the same desktop reads
its UI Automation tree and acts only through automation patterns, and
`PrintWindow` shoots it. The design, what was measured on this PC, the
refusals and the cleanup proof are in the research note
[oneshot-desktop-session](../../research/oneshot-desktop-session.md). On the
owned desktop, a GDI copy of the screen fails, and Windows Graphics Capture
gave an empty item for a fixture window. An overlay's constant-alpha
composition can be reconstructed from `PrintWindow` shots. A check that needs
more, such as a screen capture or a per-pixel-alpha overlay, runs in the
owner's desktop round below.

### How 9e builds `desktop`

The stack is free, so the server knows a PC app's tools only from what S2
confirmed. The `build` part names each command the stack needs:

- its toolchain, as programs to find on `PATH`, such as `cargo`, `dotnet` or
  `npm`;
- the commands that build the app and its installer;
- the built program S7 starts;
- the installer S8 attaches.

Before S4, the server looks for each named program. When one is missing, the
run stops with reason `toolchain` and names it in the owner's words. The
agent installs nothing. A `[재개]` after the owner has installed it looks
again.

- S3 and R1. The core design is step 7's shape: modules, outside calls,
  assumptions and the app's Windows permissions or capabilities, such as
  screen capture or an always-on-top window. S4 builds the core
  contract-test first, in the stack's own host-side runner.
- The backend review. A `device` finding for `desktop` names a check that the
  run's look can run: a driver scenario whose postcondition is the finding's.
- S6. As for an Android app, the mocks come with the stack's preview command.
  The command writes `<screen id>.png` at the `window` size, and the server
  checks each picture's size.
- S7. The frontend build's gate runs the core tests and builds the program.
  S7 starts the program through the look's session (`tool/desktop_session.py`)
  on the run's owned desktop. It then drives each screen's scenarios with the
  driver (`tool/desktop_driver.py`), shoots each state and reads the
  mechanical findings off the driver's report. The critic judges those shots
  as it does each kind's. The look's cleanup proof gates every round. A round
  whose session did not prove its cleanup counts as failed.
- The owner's desktop round. After two clean rounds, a `desktop` run whose
  owed checks include one the owned desktop cannot show waits for the owner,
  as the phone round does. Approved at that time, the round starts the exact
  program R3 will show on the owner's desktop, in an owned job. It runs only
  those checks and records their evidence. It closes what it started and
  proves it. A run with no such check skips the round.
- R3 and S8. The sheet has a `PC 앱` section read off `desktop` alone, with
  the owner's desktop round when one ran. S8's README has a section for it.
  The installer is attached to the draft prerelease beside an APK, on the
  head and digest the final gate froze. The CI job runs on `windows-latest`.

The sub-steps, each its own PR:

| Step | What |
| --- | --- |
| 9e1 | `PC 앱` on the start screen. The intake's definition, the `window` card field and the `desktop.json` profile. S2's parts, prompts and the `build` part's named commands. A `desktop` run waits after R1 |
| 9e2 | The core path. The toolchain check, the S3 module design and the R1 table for `desktop`, S4's contract tests and gate, the backend review's `desktop` deferral, and the split off modules. It waits at the frontend |
| 9e3 | Screens. S6's mocks with the stack's preview at the `window` size, R2, the frontend build and its gate building the program |
| 9e4 | The look loop. S7 on the owned desktop, with launch, driver scenarios, shots, mechanical findings, the critic, owed checks and the cleanup proof |
| 9e5 | The owner's desktop round. The resumable wait, the owned job on the owner's desktop, the owed checks only, and their evidence and cleanup |
| 9e6 | R3 and S8. The `PC 앱` sheet section, the README section, the installer on the draft prerelease and the Windows CI job. `desktop` alone and among other kinds runs to its first pull request |

## What does not change

- A run of `webapp` alone or `android` alone behaves as today: its prompts,
  parts, card and stages, apart from the device deferral in its backend
  review.
- Each review's brief (step 8), the stop rules and the caps.
- The live screen translator run is left as it is. It keeps its `webapp`
  record until the owner stops it.

## Steps

Each step is a separate PR, approved before the next starts. A run of several
kinds waits at the stage the next step adds, stopped with reason `kind`, as
step 7's did.

| # | Stage | Deliverable | Status |
| --- | --- | --- | --- |
| 9a | The owner picks | The start screen's kinds, the intake restating them and returning none, `kinds` on the record with old records read as one, the card showing them, S2's `rekind` stop; a run of one kind runs end to end as today | Done — several kinds stop with `kinds` at the restatement until 9b |
| 9b | One core for several kinds | The union card with its prefill and write-back, S2's shared and per-kind parts and per-kind requirements, the core design and R1 across kinds, S4 and the backend review once; the `device` deferral and `owed` list, closed in Android's S7 loop | Done — 9b1 (#367): the union card and S2 across kinds. 9b2 (#368): the core design and R1 across kinds. 9b3 (#369): S4 and the backend review once, with the core's tests as the gate; several kinds wait at the split (9c). 9b4: the `device` deferral and `owed` list, each an `owed_<id>_…` instrumented test in Android's S7 look, listed in R3 |
| 9c | Screens for each kind | Each feature's kinds, the split and the board once, S6 and R2 across kinds, the build's per-kind gate, each kind's S7 loop in turn with its owed checks, R3 across kinds, S8 with every kind's artifacts | Done — 9c1 (#371): each feature's kinds at the split, the board once with a first screen for each kind. 9c2 (#373): S6 designs and shoots each kind's screens its own way, each kind checked on its own features, and R2 groups them by kind. 9c3a (#374): one build turn for every kind, one gate with the core's tests and each kind's build, then each kind's look in the owner's order, each reading only what applies to it; a kind that fails sends the build back and the next HEAD looks at every kind again. 9c3b (#376): the critic judges one kind a round in the owner's order, on that kind's shots alone, each kind needing two clean rounds on the HEAD its look passed, so a fix judges every kind again; the phone round then runs on the Android app alone. 9c4a (#377): R3 as one sheet with a section for each kind in the owner's order, each read off that kind alone; the brief's lines name each finding's kind and screen, and an edit reaches the builder with its kind. 9c4b: S8's README turn asks for a section for each kind, the final gate runs on the Android toolchain, and an Android app among the kinds gets its CI job's SDK and its APK on a draft prerelease; several kinds without `desktop` run to their first pull request |
| 9d | Desktop session | The run-owned Windows session for S7: the research note, starting the app apart from the owner's windows, reading its UI tree, refusals and cleanup proof | Done — 9d1 (#381): the research note, with a measured run of a tkinter and an Edge fixture on an owned desktop; 9d2a (#382): the owned job (`tool/owned_job.py`), created before its process and followed to every member's proven exit; 9d2b1 (#385): the look's session (`tool/desktop_session.py`): its record before anything starts, apps in owned jobs with disposable data folders, the cleanup proof, and recovery of an earlier record; 9d2b2 (#386): the main window's choice, the pattern-only driver with its deadlines and shots (`tool/desktop_driver.py`), and one real Edge fixture run with its cleanup proof (`docs/research/oneshot-desktop-edge-run/`); 9d2b3: measurements on the owned desktop (`docs/research/oneshot-desktop-capture/`): Windows Graphics Capture gave an empty item for both fixture windows there, and an overlay over a viewer is read and its constant-alpha composition reconstructed from PrintWindow shots |
| 9e | Desktop | `desktop` alone and with other kinds: its card, S2 parts and prompts, the core path (S3, R1, S4, the backend review, the split off modules), S6, S7 with its owed checks, R3 and S8 | Planned |
| 9f | Real run | The screen translator as `android` and `desktop`, from the start screen to the first pull request | Planned |
