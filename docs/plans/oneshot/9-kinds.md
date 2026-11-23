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
finding. For `desktop`, that means every `look` check. Its `owner` checks
close in the owner's desktop round, as "Owed checks for `desktop`" below
describes. R3 shows each owed item with the round that closed it. A run never
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

#### The card's window

`window` is the size S7 sets the app's main window to. It is the window's
frame in physical pixels, which the driver sets without activation and reads
back as `DWMWA_EXTENDED_FRAME_BOUNDS`. The options are `1280x800` (the
default), `1024x768`, `1440x900` and `1920x1080`, and nothing else is
accepted. The start screen shows `PC 앱` disabled on a host that is not
Windows, with that reason.

#### The toolchain, declared once

The stack is free, so the server knows a PC app's tools only from what S2
confirmed. The `build` part, or `desktop.build` in a run of several kinds,
carries a `tools` list beside its choice. Each entry has two fields:

- `name`: a host program found on `PATH`, such as `cargo`, `dotnet` or `npm`;
- `setup`: how a clean CI runner gets it, which is either a GitHub Actions
  step (an action pinned to a version, or a `run` command) or `preinstalled`,
  naming the `windows-latest` image.

`setup` is required when S1's `ci` is `github-actions`. The validator refuses
a `build` part with an empty or malformed `tools` list. The list is part of
what the owner confirms: a confirmation compares the choice and its tools, so
a changed list is a change the owner confirms again. It is saved in the spec
with the part.

A host tool is told apart from a project output. Every command a later turn
gives for `desktop` names its program first. This covers S4's runner, S6's
preview, the gates' builds and S8's packaging. That program is either a
declared tool, or a path inside the worktree that the run built.

There are two outcomes:

- A command whose program is neither is undeclared. It is refused, and the
  turn goes back to its author with that reason. Nothing runs.
- A declared tool that is not on `PATH` is missing. The run stops with reason
  `toolchain`, naming it in the owner's words. The agent installs nothing. A
  `[재개]` after the owner has installed it checks again.

The server resolves each declared tool once per stage. It does this at every
handoff, on every resume and in recovery, before that stage's first command
runs. It then executes the resolved path, so the program checked is the
program run.

#### The runnable program

The frontend build's `desktop` block names two outputs, kept apart:

- `run`: the program S7 starts. It is a worktree path to an unpackaged,
  non-elevated debug build, with its arguments, its working directory and the
  setting that points its data at the look's own data folder (`{data}`).
- `installer`: the file S8 attaches.

`run` names its program and the build folder that holds everything the launch
loads from the build: the program, its libraries and its resources. A `run`
is refused at the gate when:

- it is missing, or outside the worktree;
- it is packaged, as MSIX or as an installer;
- its manifest asks for elevation.

The gate freezes that folder into run-owned staging. It records a manifest of
every file's digest, and the launch settings:

- the program, as a path inside the frozen folder;
- its arguments;
- its working directory, also inside the frozen folder;
- the settings that point its data at `{data}`.

The build's identity is one digest over the manifest and the launch settings.
Each attempt gets a fresh, run-owned data folder for `{data}`. No setting may
point into the live worktree.

Every look round launches the frozen copy with those settings and records the
identity. The owner's desktop round launches the same identity, and R3
requires it. A change to any file, argument, working directory or data
setting, even under the same HEAD, is a new build. It clears the clean rounds
and any pending approval.

At launch, the research note's refusals apply as written. A main window
outside the app's job ends the round as failed, and so does a handoff to
another instance.

#### Owed checks for `desktop`

The backend review's `device` finding for `desktop` names its evidence
source, and the server assigns it:

- `look`, when the postcondition is a UI Automation state the driver reads: an
  element with a stable `AutomationId` and its name, value or toggle state
  after a scenario.
- `owner`, when the postcondition needs what the owned desktop cannot show: a
  capture of another program's window or the screen, or how a per-pixel-alpha
  overlay composes. Its postcondition is observed, never inferred from the
  app's own controls:
  - Every `owner` check runs against a fixture the round starts in its own
    job, whose content is known: for example a window showing a known page of
    text.
  - A capture check passes only when what the app reports it captured contains
    the fixture's expected content. That report is an element's value or a
    file in the app's `{data}` folder that the check names.
  - A composition check passes only when the round's own capture of the
    fixture's region shows the overlay's expected content over the fixture.

  The driver's UI Automation steps run the scenario. A "capture active"
  element or a drawn window of the app's own is not evidence. A run with valid
  controls whose capture is blank or wrong fails the check, and R3 stays
  closed.

  The review's `owner` deferral names the step that sets the capture going,
  `act`, as a `look` one does, and what the check observes, `observe`,
  exactly one of the three below. Each names `from`, what the app itself
  captures however the round observes it: `fixture`, the fixture's window, or
  `screen`, the region the fixture covers. An app that reads the screen reads
  a surface, so that check runs only when the approval allows it, as a
  `composition` does.

  - `report`: the app's own report of what it captured, the value of an
    element (`id`, with `in: owned` in the window the main window owns) or a
    UTF-8 file under `{data}` (`file`), which must contain the fixture's text.
    The report holds the page when its words appear in order, case and
    punctuation aside. A word is a run of letters, digits and combining
    marks of any script, read after canonical (NFC) normalization. Format
    characters such as a zero-width joiner or a direction mark are passed
    over: they neither part a word nor count in it. A zero-width space does
    part words. A misread word such as `appleé`, in either encoding, or words
    joined into one token, is no match. The driver reads a control's value
    without UI Automation's defaults, so a provider that does not supply it
    leaves the value unknown, never empty. So does a value that is not
    whole UTF-16. A toggle state is read the same way, so an unsupplied one
    is unknown, never `Indeterminate`. So is an expansion state. Only a
    supplied `Collapsed` or `LeafNode` skips a container's item audit. Any
    other state is audited, including one that is unsupplied or out of
    range. An element's id or name that is not
    whole UTF-16 leaves its window's read not whole. The driver reads every element's automation id again
    after its value and compares the two ordinally. One that changed (a
    control rebound meanwhile) leaves that window's read not whole. Such a
    read proves no value, no absence and no single holder of an id.
    A known limit, accepted by the owner: UI Automation offers no snapshot
    of a tree, so the driver reads the tree live. The checks above catch a
    change that is still there when it is read again. They cannot catch an
    element rebound and restored between two reads of it, or one moved
    within the tree into a part the walk already passed. A report file is read through one open that shares
    reading only, so it is one version of the file. A file the app still
    holds open for writing is no report.
    A known limit, accepted by the owner: the approval decides what the
    round itself captures and which checks run. A report check cannot tell
    whether the app read the fixture's window or the screen region it
    covers, since the fixture is visible either way. With the owner's
    approval, the app runs on the owner's desktop as built.
    The report must be the capture step's own, measured against what was
    proven just before that step. The driver reads it with the step's target
    found, just before the step acts: a whole state for an element, and the
    file's text for a file. After both reads it finds the target again, so
    the step acts on the control as it is then. A file read as text must be
    valid UTF-8, before the step and after it. Bytes that are not UTF-8
    leave the text unknown, and the report counts for nothing.
    - An element counts only when its window was read whole, without any
      unread items, both in the state after the step and in the one read
      just before it. There must be exactly one element by its id, offering
      a value that was neither unread nor cut.
    - Before the step, the element must be proven absent, or proven to hold
      a value without the page. An owned window counts as absent only when
      the main window owned none. An ambiguous window, two elements by the
      id, or a value unread, cut or not offered is unknown, and the report
      counts for nothing.
    - A file counts only when the driver proved it absent then, or read
      text without the page. Rewriting the page an earlier capture left is
      no capture of the step's.

    A known limit, accepted by the owner. What a report proves is that the
    app captured this check's fixture during this run. Each check draws its
    own random words and starts the app after the fixture is up, so no other
    run's capture can hold them. That the capture step itself made the
    capture is checked as far as the outside can see, and no further.
    Outside checks cannot tell a capture of the app's own earlier in the
    run from the step's, if the app shows or publishes it only when the step
    acts. That includes a capture finished in the milliseconds after the
    read, one kept in a window not shown before the step, and one cached
    and written out by the step. Proving the link would need the app to
    report a mark of the step's own.
  - `window`: the round's window-bound capture of the app's main window, or
    with `in: owned` the one it owns, which must show what `shows` says. A
    frame whose content size differs from the surface the capture was set
    up with is not read. A window resized meanwhile would be cut off or
    padded with pixels nobody drew.
  - `composition`: the round's surface capture of the fixture's region, which
    must show what `shows` says over the fixture.

  The fixture is the server's own, never the build's: a window the round
  starts in its own job, showing a page of English words the server draws at
  random for that round, so neither the builder nor the app knows them
  beforehand. The fixture reads its page from a file the server writes. The
  server deletes that file once the fixture's window is up, before the app
  starts, so no file the app can read holds the words. A report file is read
  through one open, at most its bound and a byte past it, so a file that
  grows past the bound is rejected. The independent critic judges a `window` or `composition`
  capture against its `shows` sentence and the fixture's words, as it judges
  a screenshot. The frontend build scripts each `owner` check as it scripts a
  `look` one, its last step the review's `act`.

A finding with neither is not deferrable and stays a blocker. A `desktop`
clean round counts only when every `look` check passed on that round's HEAD.
A failed or unread observation is a failure, never a skip. `owner` checks
stay pending through the owned rounds. R3 opens only when every pending
`owner` check has passing evidence on the current HEAD and the build's
identity: its files and its launch settings, above. The sheet shows the round that closed each owed ID.

#### Screens

As for an Android app, S6's mocks come with the stack's preview command.
Each native kind has its own preview contract. Its sources are staged in
`previews/<kind>/` and approved into `docs/design/mock/<kind>/`, and its
pictures are rendered into a folder of its own. So an Android app and a PC
app in one run keep both previews, and R2's edits and approval carry each.

A PC preview writes `<screen id>.png` no larger than the `window` and at least
90% of each side. The title bar and borders are the stack's, so the exact
frame is checked in S7, where the driver reads it back. Every `desktop`
preview command runs inside a look on an owned desktop, whatever its
descriptor says, so one that opens a window never opens it on the owner's.

S7's coverage reads the R2-approved screen map. It applies the research
note's placement rules by each feature's `AutomationId`:

- an `every` feature is on the first surface;
- a `sometimes` feature is one action away;
- a `rarely` feature is absent from the primary surface but present in some
  approved state;
- a `never` feature has no control in any approved screen, state or window.

Absence counts only once every approved state has been reached and read. A
driver report that misses an approved state, or stops early, is not a clean
round. A `never` feature found anywhere is a placement finding.

#### The look loop

The frontend build's gate runs the core tests and builds `run`. S7 starts it
through the look's session (`tool/desktop_session.py`) on the run's owned
desktop. It then:

- drives each screen's scenarios with the driver (`tool/desktop_driver.py`);
- shoots each state;
- reads the mechanical and coverage findings off the driver's report;
- runs the `look` owed checks.

The critic judges those shots as it does each kind's. The look's cleanup proof
gates every round. A round whose session did not prove its cleanup counts as
failed.

#### The owner's desktop round

After two clean rounds, a run with a pending `owner` check waits for the owner,
as the phone round does. The approval is one-time. It is recorded with:

- the run and the HEAD;
- the build's identity;
- the check IDs;
- what the round will start: the app and each check's fixture, by name;
- each capture operation and the surface it reads.

The server checks all of these again before anything starts on the owner's
desktop.

The windows do not exist yet when the owner approves. Once the round has
started them, it binds each approved source to the concrete job, process and
window handle and class it created. Before every read, it proves that binding
again. A window that closed or was replaced is never read: the capture call is
not made.

What is read is limited where the frame is sampled, not afterwards. A capture
operation is one of two kinds:

- Window-bound: it reads one bound window's own content through a source bound
  to that window instance, never through a bare handle. Nothing outside the
  window can enter the read, whatever moves over it. Windows can recycle a
  destroyed window's handle, so a handle proven a moment ago may name another
  app's window by the time a call like `PrintWindow` uses it. The round
  therefore does not use `PrintWindow` on the owner's desktop. It works in
  this order:
  1. It creates a Windows Graphics Capture item for the handle. The item is
     tied to one window instance and closes when that window is destroyed.
  2. It proves the binding again. When the proof fails, it closes the item
     before any frame is read.
  3. Only then does it start the frame pool. An item that closed delivers no
     frame, and the check fails without reading anything.

  Where no such item can be made, the check stays pending, unless an approved
  surface can supply its evidence.
- Surface: it reads a region of the screen, which is the only way to observe
  how an overlay composes, or what the app itself captures from the screen.
  Whatever is on that region at the moment of sampling is read, including a
  notification that arrives meanwhile. A surface operation runs only when the
  approval names that region or monitor as the surface the owner allows to be
  read. Before sampling, the round places its opaque fixture over the whole
  region, so that what lies beneath is the round's own.

A check whose evidence needs a surface the approval does not name is not run.
It stays pending, and R3 stays closed.

The approval starts the round at once. For each check it runs, in this
order:

1. The fixture (`tool/oneshot_fixture.py`) in a job of its own, opaque and
   topmost, placed at a fixed corner of the primary monitor. Windows shows
   it without activation (`SetWindowPos` with `SWP_NOACTIVATE`), never
   through Tk, whose `deiconify` asks for focus. It reports its handle and
   its client area on screen. Its binding holds its title, its window
   rectangle and its process. It says it is ready only once it is shown,
   it has read where it is, its window region is set and its guard is
   installed on that same frame, all read again once the guard holds it.
   A show, read, region or guard that fails, or a state that changed
   before the guard held it, ends it first, so the round stops on its own
   setup and fails no app. Once the fixture is up and bound, the check
   ends its job, its exit proven, before the next check starts. One round
   runs on the owner's desktop at a time, whichever repository it is for,
   from its look's start through its cleanup. A look whose cleanup was not
   proven leaves its record held on disk. Before any round starts, every
   repository's look records are read through the looks' own recovery,
   whichever run of the server wrote them, under the lock a stage
   folder's move takes, so no record moves past the read. A held one, or
   a folder or record that cannot be listed or read, keeps every round off the desktop
   until that recovery settles it, which takes a completed proof, a new
   boot or the end of its logon session. Before each fixture
   starts, no top-level window on the desktop may have the fixture's
   title. Once it is bound, its own must be the only one. The round
   confirms the approved HEAD and the frozen build once it has the
   desktop, before each fixture starts, and with each app's process made
   but not yet run. That last check reads HEAD again after the frozen
   files. The last stop check and the resume run under the lock a stop is
   accepted under, so a stop accepted first keeps the process from
   running. A moved HEAD goes back to the build. A changed freeze with the
   worktree unchanged goes back to approval. A known limit, accepted by
   the owner: a change to the frozen copy or HEAD in the instant between
   that last check and the resume is not seen before the app runs. The
   check after the app's run finds a changed freeze and sends it back to
   the build, and the worktree check finds a moved HEAD, so such a round
   never passes. The frozen copy is read once more as a pass is taken,
   after the critic's verdicts, and HEAD after it; a copy changed since,
   or a moved HEAD, sends it back to the build. Each read of the copy
   lists it without passing over a folder it cannot list, and opens the
   manifest and every file for reading only. Windows then refuses anyone
   a write, a rename or a delete of them until the read ends. A file
   added meanwhile is found by listing again once all are read. At a pass
   they stay held through the HEAD read and until the pass is saved. A
   file another process holds open for writing cannot be opened so, and
   that read sends the round back to the build, after the app's run as at
   the pass. A second known limit, accepted by the owner: an app that changes
   its own frozen files while it runs and puts them back before the check
   after its run is not seen. Out of scope, by the owner's decision: another
   process of the owner's that races the server's own files during these
   checks, such as replacing the copy's folder, adding a file to a folder
   already listed, or moving HEAD while a pass is saved. Such a process
   runs as the same user and could as well write the server's records
   directly, so closing those instants would protect nothing. The checks
   are there for an app or a build that changed its copy, not for a local
   process working against the server. For the same reason, also by the
   owner's decision, an app under test that deliberately deceives the round
   is out of scope. Examples are reading the fixture process's memory, or
   swapping its report path for a link while the server opens it. The app
   runs as the same user too. The round finds an honest app that is broken
   or unfinished, not one built to cheat it.
2. The frozen app from a fresh start of the check's scenario, driven without
   a shot (`shoot: false`), so the driver reads and acts but never calls
   `PrintWindow`. Every step's declared postcondition must hold on the one
   element it names, in the whole read of the state that step left. Until
   it does, the app is not in the state the scenario sets up, and nothing
   observed counts.
3. While both still run, the fixture's binding is proven, then what the
   check observes is read: the app's report, or a capture by the helper
   (`tool/owner_capture.cs`). The app's main window as the driver last read
   it must still be bound to a live process of the app's job. That process
   is whichever member owns the window. It is checked after a report is
   read, and before and after a window or composition capture. The
   fixture's binding is proven again with it: after a report is read,
   before the helper's `go` for a window capture, as it already is for a
   composition, and after any capture is read. An app that
   crashed meanwhile is no pass, even if a child of it still draws.

The server builds the helper with the .NET Framework's `csc` against the
system's WinMetadata, which every Windows 10 and 11 carries, and starts it in
a job of its own. Each read has two phases. The helper says it is ready, the
server proves the binding again, and only a `go` written after that proof,
with no stop asked meanwhile, lets it sample.

- A window capture hands the helper only a handle, bound against the window
  exactly as the driver read it. The helper keeps a frame only while the
  item stays open through the copy.
- A composition hands it the fixture's client area on screen, which the
  fixture paints whole and opaque (never its frame, whose borders DWM
  draws partly invisible), with its handle and process, and the fixture's
  marks. The marks are cells of random colours the server draws for the
  check, which the fixture paints along its client area's edges, 8 pixels
  deep. No two colours, and no colour and the page's white or black, lie
  within twice the helper's tolerance of each other. The fixture gives
  itself a window region of exactly its client area. To keep the page
  steady for the reading, its own window procedure turns requests to
  move, resize, restack, hide, retitle, restyle or close it into none. It
  puts back at once a minimize, and a move, hide or lost topmost flag that
  came without that request. It does not keep its order among other
  topmost windows; the marks cover that. The helper checks the fixture
  right before and after the blit: the same window, visible, not
  minimized, its client area exactly that region, and its window region
  one rectangle of exactly that client area (`GetWindowRgnBox` answering
  `SIMPLEREGION` with those bounds; `ERROR`, which a failed query also
  gives, never passes). It keeps the frame only when every pixel of every
  mark is in it. A fixture displaced by even a pixel shows a neighbour's
  colour or the page at some mark's edge, so this proves from the sampled
  pixels themselves that the fixture covered the region in that frame. A
  frame without them leaves the check pending. A failed check of the
  window fails it. A window region replaced after the first check, still
  in place during the blit, and put back exactly before the second check
  would escape both. That needs an app that sets regions on the server's
  own window with that timing.
- A report passes only when it holds the fixture's page as it reads: its
  words in order, one after another. A fixed text naming every word the
  server draws from is no capture of this round's page.

Each capture is copied out before the round's look is cleaned, and the critic
judges it against `shows` and the fixture's words only after every window the
round started is gone. A check stays pending when its helper cannot be built,
or when the helper cannot read there: Windows refuses or empties the item,
the desktop gives the blit no pixels, the fixture's marks are not in the
sampled frame, or the helper says nothing in time.

A failed check sends its high finding to the build, as the phone round's do.
Pending checks send the run back to waiting with a new attempt, naming them.
A stop, an unproven cleanup, a helper that could not let go of what it made,
or a fixture that never showed passes nothing. A stop is accepted under the
lock each `go` and each outcome is decided under, the server's shutdown
included. A stop accepted first reaches the helper as `stop` and turns any
outcome into a stop, a tree moved meanwhile included.

A restart, a cancellation, a new HEAD or a new build identity sends the run
back to waiting for approval. An earlier approval is never applied to
a different launch.

The round:

1. Records its ownership before launch.
2. Starts the program in an owned job.
3. Acts only on windows of that job, through automation patterns. It does not
   activate them or send input.
4. Runs only the pending `owner` checks.
5. Keeps only evidence within the approved scope.
6. Closes what it started, and proves its cleanup on every outcome, failure
   included.

A failed check goes to the builder as a finding. A rebuild clears every
earlier device pass: the phone round's, the owned rounds' and this one's.
Each accepted frontend build has its own generation id, and a pass counts
only when it names the current one, so a rebuild clears passes even when
its HEAD and APK digests are the same. A build an earlier version accepted
gets its id at start-up, before any device round's recovery, so no earlier
pass counts for it, its device rounds run again in the guard's order, phone
first, and their passes name it. Before recovery reopens a held
R3, and before the owner's approval is recorded, the worktree must be read
at the build's HEAD. A HEAD that cannot be read confirms nothing.

R3 has one guarded entry for every device round. When a run has both a phone
round and an owner's desktop round due, R3 opens only when both passed on the
current HEAD and build. Neither round's pass opens it alone. A run with no
pending `owner` check skips the round. R3's approval asks the same guard
again: a sheet that opened before a pass was cleared sends the run to that
round, never to the pull request.

#### R3 and S8

The sheet has a `PC 앱` section read off `desktop` alone, with the owner's
desktop round when one ran. S8's README has a section for it.

The final gate builds the installer and freezes it with its HEAD and digest,
as it does an APK. The draft prerelease carries the run's assets: the
installer, the APK, or both, each reused only for the same HEAD and digest.
A failed copy or upload never blocks the pull request; its body says why.

S1's `ci` choice is kept. When CI is chosen and `desktop` is among the kinds,
the workflow job runs on `windows-latest`:

1. It takes each declared tool's confirmed `setup` step, as written. A
   `preinstalled` tool has no step and goes straight to the check below.
2. With `android` among the kinds, it also takes step 7's JDK and Android SDK
   setup.
3. A step then checks that each declared tool is on `PATH` before the one
   shared gate runs.

S8 refuses a workflow that lacks any of these steps.

#### Steps and their stops

Each sub-step is its own PR, tested on fakes alone. Until the next one lands,
the server stops a `desktop` run, alone or among other kinds, with reason
`kind`, at the boundary that sub-step names. The stop holds at every automatic
handoff, on `[재개]` and in recovery. A stopped run never calls the next stage,
and resumes into it only once that stage has landed.

| Step | What | A `desktop` run stops |
| --- | --- | --- |
| 9e1 | `PC 앱` on the start screen; the intake's definition; the `window` field and the `desktop.json` profile; S2's parts and prompts; the `build` part's `tools` | After S2, before the core design |
| 9e2 | The toolchain check and undeclared-command refusal; the S3 module design and the R1 table; S4's contract tests and gate; the backend review's `desktop` deferral with its `look` and `owner` sources; the split off modules | After the split, before S6 |
| 9e3 | S6's per-kind previews and R2; the frontend build's `run` and `installer`, and its gate building `run` | After the build gate, before the look |
| 9e4a | The look: the frozen build launched for each screen and state with the research note's refusals, its shots, the mechanical, placement and coverage findings, the cleanup proof | After every kind's look passed, before the critique |
| 9e4b | The critic on a PC app's shots, the `look` owed checks with their driver scenarios, the two clean rounds | After two clean rounds, before the owner's round or R3 |
| 9e5a | The `owner` check's `act` and `observe` and its scenario; the owner's approval with what it records, checked again, and what clears it; the one guarded R3 entry for every device round | After the owner approved, before the round starts |
| 9e5b | The owner's desktop round: its fixtures and owned job, the bindings proven before every read, the window-bound and surface captures, the critic's judgment, the cleanup proof | Before R3 |
| 9e6 | R3's `PC 앱` section; S8's README, the frozen installer on the prerelease, and the CI job | Runs to its first pull request |

Each step's fakes include at least these:

| Step | Fakes |
| --- | --- |
| Every step | A stopped run that tries to call the next stage, which must fail |
| 9e1 | A `window` outside the options; a `build` part without `tools`; a tool without `setup` while CI is on; a changed `tools` list under the same choice, which needs a new confirmation |
| 9e2 | A missing tool at the first stage; a tool removed between two normal stages, so the second stops with `toolchain` before it runs; a resume after a tool disappeared; an undeclared command, which goes back to its author and runs nothing. None may call an installer |
| 9e3 | Two native previews with overlapping relative paths, and a web preview, through one R2 approval; a preview whose descriptor says nothing of a window, whose process must still start on the owned desktop; a packaged-only `run`; a companion file changed under the same HEAD, and a launch flag changed alone, each a new build that clears the earlier passes and approval; a working directory that points into the live worktree |
| 9e4a | A driver report that misses an approved state; a complete report with a `never` feature; a failed cleanup proof; a dialog screen that opens with its main window, owned by it |
| 9e4b | An `owner` check still pending after both owned rounds; a `look` check whose postcondition is not read |
| 9e5a | An absent, a stale and a duplicate approval; an `owner` deferral with no `act` or `observe`, which stays a blocker; a restart and a new build after the approval, each back to waiting; a surface check without its surface in the approval, which stays pending with R3 closed; a mixed run in which the phone passes first |
| 9e5b | Valid app controls with a blank or wrong capture; a first binding after approval that succeeds, then the same window replaced, which is never read; a handle recycled to another app's window after the proof but before the capture, which is never read; a window that moves over a window-bound capture between the check and the sampling, which does not enter the read; a cleanup failure; a check the critic finds the capture does not show |
| 9e6 | A desktop-only asset; both assets on one prerelease; a resumed upload; a run with CI off; a CI-enabled mixed run with a PC tool the image lacks, whose workflow must carry its `setup` step and the check that it is on `PATH`; a `preinstalled` tool, whose workflow carries only that check |

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
| 9e | Desktop | `desktop` alone and with other kinds: its card, S2 parts and prompts, the core path (S3, R1, S4, the backend review, the split off modules), S6, S7 with its owed checks, R3 and S8 | In progress — 9e1: `PC 앱` on the start screen (disabled off Windows), the intake's definition, the `window` field and `desktop.json`, S2's six parts with its own prompt and the `build` part's confirmed `tools`. 9e2, in three PRs: 9e2a (#394), a PC app's core design (modules, outside calls, assumptions and tests, with its own prompt) and its R1 sheet and brief. 9e2b: S4's tests and core build with their own prompts; each send resolves the declared tools on `PATH` before the stage's turn, stops with `toolchain` naming a missing one and installs nothing, and the gates run each tool from the path found; a command whose program is neither a declared tool nor a worktree file goes back to its turn and runs nothing; 9e2c: the backend review with its own prompt, whose `desktop` deferral names its check's `source`, `look` or `owner`, kept on the `owed` item, and a finding with neither stays a blocker; the split off the core's modules with its own prompt; a `desktop` run, alone or among other kinds, stops at the board, before S6, and stays held there on `[재개]` and in recovery. 9e3, in two PRs: 9e3a, S6 with a PC app's own prompt: its screens' surfaces (`window`, `dialog`, `floating`, `tray`), its preview run in a look on a desktop the run owns with only its declared tools, each picture no larger than the card's `window` and at least 90% of each side, and each native kind's preview kept by kind (`previews`), unpacked in its own folder and approved into `docs/design/mock/<kind>/`; R2 shows the PC app's window shots beside every other kind's, and its approval leaves a `desktop` run stopped at the frontend build. 9e3b, in two PRs: 9e3b1, a PC app alone: the frontend build with its own prompt, whose `oneshot-desktop` block names `run` (a build folder, the debug `.exe` in it, its arguments, working directory and environment, with `{data}` named and nothing naming the worktree) apart from the `installer` S8 attaches; the gate runs with only the declared tools, then refuses a missing, linked or packaged `run`, a working directory outside its folder and a manifest asking for elevation, and freezes the folder into staging the run owns with a manifest of digests and the launch settings, one identity over both; the run waits there with reason `kind`, before the look. The debug build opens a screen in a state from `ONESHOT_SCREEN` and `ONESHOT_STATE`. 9e3b2: several kinds with `desktop` run that build in one turn, whose prompt keeps only the picked kinds' sections and takes the PC app's conventions, block and freeze from its own prompt; a hostname read as a number must be a valid IP address, and only `RUST_LOG` may name a module with `::`. 9e4, in two PRs: 9e4a, the look (`tool/main/oneshot_desktop_look.py`): once the gate passed and the PC app is frozen, every kind is looked at in the owner's order in the same check, the PC app by starting the frozen copy once for each screen and state on a look's owned desktop, `{data}` a fresh folder the look reserved and `ONESHOT_SCREEN`/`ONESHOT_STATE` naming what to open; the driver chooses the main window as the one no other window owns, so a dialog, floating window or flyout the main window owns is shot with the state, and its findings, the owner's map read off the first screen's `full` state (a `sometimes` feature one invocation away) and the coverage over every state are high; placement reads only the screen's own window, a `sometimes` feature counts only when the invocation showed it from a fresh start on a complete read, and absence counts only off reads that completed, a virtualized list making it unproven; a start that changes the frozen copy or that Windows refuses goes back to the build, and one whose end, or a look whose cleanup, is not proven stops the stage; one whose frozen build is gone goes back to the build, resumable. 9e4b: the critic judges a PC app's window shots as it does each kind's; the backend review gives each `look` deferral the step that must bring it about (`act`: a driver action on a control's `AutomationId`) and its postcondition as the driver reads it (`expect`: an element's `AutomationId` with its name, value or toggle state), each in the main window or the one it owns, and an earlier version's `look` check without them goes to the owner; the build's block scripts each as a driver scenario (`checks`: a screen and state, then the driver's steps by `AutomationId`, the last the review's `act` with its `expect`), and the look runs each from a fresh start, ending on the review's step, passing only when the state before that step proves its postcondition false (the one element its id names in that window reading another value, or a complete read holding none) and the state after it holds it, each step's state read after the one before; a result the app brings about by itself after the step before (a debounce, a startup load) is not told apart from the step's own: a control run that repeats the lead-in without the step is a follow-up, its runs diverging on background work and on state no element id carries; a failed or unread one is a high finding; a look that did not pass the PC app, ran none of them as an earlier version's, or is not of the run's current freeze proves none, and the critic or the phone round sends it back to the build; two clean rounds count only on the PC app's current freeze, for every kind, and a clean round at the cap earns its confirming round on that freeze, and the run then waits before R3, its `owner` checks pending, with an Android app's phone round first among several kinds; a whole look has its own deadline over its operations'. 9e5a: the review's `owner` deferral names its `act` and what it `observe`s (the app's `report`, the round's `window` or `composition` capture with its `shows`), and the frontend build scripts it, its last step the review's `act`; after two clean rounds a run with a pending `owner` check waits at `pc` for the owner's one-time approval of its HEAD, build, checks, what it starts and each capture with its surface, checked again before it is recorded, refused when stale or a second time, and cleared by a restart or a new build; a surface the owner does not allow leaves its check pending; R3 opens through one guarded entry, the phone round and this one each due until it passed on the current HEAD and build. 9e5b: the approval starts the round on the owner's desktop, in a look with no desktop of its own: for each check the server's fixture with a fresh page of words, the frozen app from a fresh start driven without a shot, then the app's report or a window-bound or surface capture by a helper the server builds from the system's metadata, each source proven again before it is read; the critic judges each capture once the round's windows are gone; a pass goes on to R3, where the run waits with reason `kind`, a failure goes to the build as findings, a check that cannot be read here stays pending, and a stop or an unproven cleanup passes nothing |
| 9f | Real run | The screen translator as `android` and `desktop`, from the start screen to the first pull request | Planned |
