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

## What exists now

| Where | One kind, picked by the model |
| --- | --- |
| Start screen | The sentence and the three models; no kind |
| S0 | `oneshot-intent` returns `kind`; `intent_of` checks it is one of `KINDS` |
| Card | `종류` shown as text; the owner can only type a correction for the intake to restate |
| Record | `intent.kind`, one string; `kind_of(o)` reads it, 38 calls in 10 modules |
| Prompts | A web prompt and an `-android` one for each of 8 turns |
| S1 | `schema(kind)`: `responsive` for a web app, `device` for an Android app |
| S2 | `parts(kind)`: `PARTS` or `ANDROID_PARTS` |
| S3 | A web app's API table, or an Android app's modules (`M`), outside calls (`X`) and permissions |
| S5–S7 | Playwright on a web app's routes, or the run's emulator and the owner's phone for an Android app |
| `desktop` | In `KINDS` for the intake; refused at the card, with no stage that builds it |

## Design

### The kinds on the record

The run record holds `kinds`, the owner's list in the order they picked, one
or more of `SUPPORTED_KINDS`. The intent keeps everything else. A record or
spec from before this step has `intent.kind` and reads as `[kind]`.
`kind_of(o)` stays for the code that runs once per kind and is handed that
kind; `kinds_of(o)` gives the list.

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
saved profile still holds its own last answers.

### S2 — one conversation

One conversation covers every picked kind. A run of one kind keeps today's
parts. A run of several names the shared parts once (`core`, the language and
framework of the logic every kind uses, `data_store` and `tests`) and each
kind's own parts with that kind:

| Kind | Its own parts |
| --- | --- |
| `webapp` | `backend` (the server), `frontend`, `styling`, `deploy` |
| `android` | `ui`, `build` |
| `desktop` | `ui`, `build` |

A requirement may name the kinds it applies to; one that names none applies
to all. A platform capability (screen capture, drawing over other apps) names
its kind and carries its prompt and risk, as in step 7.

When the answers make the product something a picked kind cannot be, the
agent says so in the person's words, adds `"kinds_mismatch"` with its reason
to the `oneshot-spec` block and asks nothing more. The run stops with reason
`rekind`, which no `[재개]` takes on: the owner starts a new run with the
kinds they now pick. The agent never names the kinds the run should have.

### S3, R1 and S4 — one core

The core design is step 7's shape for every run of several kinds: modules
(`M`) with their contracts, outside calls (`X`), and each kind's permissions.
A web app among the kinds adds its server's API (`A`), which the other kinds
may call. The R1 sheet and its brief show, for each kind, the modules it uses
and the permissions it asks for. S4 builds the core contract-test first, in
the stack's host runner, once. The backend review reads the core once; a
finding only a device can show is deferred to that kind's S7 (step 9c).

### S5 to R2 — screens for each kind

The split reads features off the core once. The board is shown once, since
how often a feature is used does not depend on the kind, with a first screen
for each kind. S6 designs and previews each kind's screens, the way that kind
does. Each screen carries its `kind`, and R2 shows every kind's screens in
one sheet, grouped by kind, one confirmation line per screen.

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
- S2's bundles are, for example, Tauri 2, .NET with WinUI, or Electron.
- S6 previews the screens the way the stack's own preview tool renders them.
- S7 needs a session the run owns: it starts the built app, drives each core
  scenario and shoots each screen and state, then closes everything it
  started.

The first deliverable of 9d is that session's design: how the app is
started apart from the owner's own windows, and how its UI tree is read for
the mechanical and placement checks. It is written as a research note, with
its refusals and its cleanup proof, as 7d's emulator was.

## What does not change

- A run of one kind behaves as today: its prompts, parts, card and stages.
- Each review's brief (step 8), the stop rules and the caps.
- The live screen translator run is left as it is. It keeps its `webapp`
  record until the owner stops it.

## Steps

Each step is a separate PR, approved before the next starts. A run of several
kinds waits at the stage the next step adds, stopped with reason `kind`, as
step 7's did.

| # | Stage | Deliverable | Status |
| --- | --- | --- | --- |
| 9a | The owner picks | The start screen's kinds, the intake restating them and returning none, `kinds` on the record with old records read as one, the card showing them, S2's `rekind` stop; a run of one kind runs end to end as today | Planned |
| 9b | One core for several kinds | The union card, S2's shared and per-kind parts and per-kind requirements, the core design and R1 across kinds, S4 and the backend review once | Planned |
| 9c | Screens for each kind | The split and the board once, S6 and R2 across kinds, the build's per-kind gate, each kind's S7 loop in turn, R3 across kinds, S8 with every kind's artifacts | Planned |
| 9d | Desktop session | The run-owned Windows session for S7: the research note, starting the app apart from the owner's windows, reading its UI tree, refusals and cleanup proof | Planned |
| 9e | Desktop screens | `desktop` through S1, S2, S6, S7, R3 and S8, alone and with other kinds | Planned |
| 9f | Real run | The screen translator as `android` and `desktop`, from the start screen to the first pull request | Planned |
