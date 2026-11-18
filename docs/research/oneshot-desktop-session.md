# Oneshot desktop session — where S7 runs a Windows app

Plan 9 ([9-kinds](../plans/oneshot/9-kinds.md)) adds `desktop`, a program
installed on Windows. Its S7 loop starts the built app, drives each core
scenario, shoots each screen and state, reads the UI tree for the mechanical
and placement checks, and closes everything it started. It must do all of that
without touching the owner's windows, focus, mouse or keyboard. This note is
step 9d's first deliverable: the options on this PC, what was measured here on
2026-11-18, the design that follows, its refusals and its cleanup proof. 9d2
builds the design and measures what this note marks as not measured.

## Options on this PC

This PC runs Windows 11 Home (build 26200).

| Option | Verdict |
| --- | --- |
| Windows Sandbox | Not on Home; `WindowsSandbox.exe` is absent |
| A Hyper-V guest | Not on Home, and a guest image is one more thing to build, update and license |
| A second user session over RDP | Home has no RDP host |
| The owner's own desktop | Moves windows and focus under the owner's hands |
| A desktop the run owns, in the owner's session | Chosen |

A Win32 desktop is a separate surface inside the owner's window station.
Windows on a desktop that is not the input desktop are not shown on the
owner's screen, and Windows delivers mouse and keyboard input to the input
desktop only. The repository already has the pieces: `owned_desktop()` in
`tool/native_verification.py` creates a desktop and binds the calling thread
to it, and `launched()` in `tool/native_process.py` starts a process on it,
suspended, puts it in a kill-on-close job and resumes it.

The boundary is a surface, not an account. The app runs as the owner's user,
in the owner's session: it shares the owner's files, registry, known folders,
network, session-wide named objects and the window station's clipboard.

## What was measured

The experiment's driver (`exp.py`) and reader (`reader.ps1`) and their outputs
are kept outside the repository. Two fixtures:

- a tkinter window titled `fixture-tk`, with a line `ready` and a button `Go`
  that changes the line to `clicked`;
- Edge 157 in `--app` mode with a fresh `--user-data-dir`,
  `--force-renderer-accessibility` and `--window-size=800,600`, showing a
  page with a heading, a line `ready` and a button `Go` that changes the line
  to `clicked`. Chromium is the engine of WebView2, which Tauri uses, and of
  Electron.

The driver created the desktop with `owned_desktop()` and started the fixture
with `launched()`. It took the fixture's processes from one parent-pid
snapshot six seconds after the start. A reader then ran on the same desktop as
a Windows PowerShell 5.1 process, also through `launched()`. It listed the
visible windows of those processes, called `SetWindowPos` on the largest one
(800×600, no activation, no z-order change, its result not checked), shot it
with `PrintWindow` and `PW_RENDERFULLCONTENT`, walked the UI Automation control
view, invoked the first element named `Go` through its `InvokePattern`, then
looked for an element named `clicked` and shot again. The driver read the
owner's foreground window at three points: before the desktop was created,
after the snapshot and before the reader started, and after cleanup. At the
middle point it also listed, from a thread on the owner's desktop, every
window of the snapshot's processes.

| Check | tkinter (`tk.json`) | Edge (`edge.json`) |
| --- | --- | --- |
| Windows of the snapshot's processes on the owner's desktop, at the middle point | none | none |
| Owner's foreground window | same at all three points | same before and at the middle point; different after cleanup, cause not recorded |
| Shot size | 800×600, resized from 656×519 | 800×600, already that size from `--window-size` |
| `PrintWindow` with full content | rendered | rendered, page content included |
| UI Automation tree | the window `fixture-tk` and three unnamed panes; `Go` not found | 26 nodes: the document, the text `ready`, the button `Go` with `Invoke` |
| `Go` invoked, then read | — | `clicked` found in the tree and seen in the second shot |
| Foreground window on the owned desktop | none | none |
| Snapshot processes alive after cleanup | none | none |
| Desktop opened again by name after cleanup | `OpenDesktopW` returned null | `OpenDesktopW` returned null |
| The run's Edge profile folder | — | removed |

A later Edge attempt (`edge2.err`, `run-edge/report.json`) added a GDI copy of
the screen from the reader. The copy failed with an invalid-handle error. That
attempt then failed in cleanup: the launcher's 5-second wait for the root
process ran out after the job was ended, and the driver stopped before its
checks, so its files record no cleanup result. A process listing in the
session afterwards found no Edge process started in the previous ten minutes,
and the profile folder was removed by hand. Neither is in the saved files.

These measurements prove less than a cleanup proof needs. A null from
`OpenDesktopW` also means a failed probe, and the process check covered one
snapshot of pids, not the job.

Three observations shaped the design:

- The reader's window list held two input-indicator windows
  (`UAC_InputIndicatorOverlayWnd`, `UAC Input Indicator`) besides each
  fixture's window. The saved rows do not record which process owned them.
- One Edge run outlived the launcher's fixed 5-second wait.
- In an earlier Edge attempt, the parent-pid walk counted the ADB server on
  port 5037 among the fixture's processes: that server's recorded parent pid
  had since been reused by the fixture. This was seen in the session, by
  reading the leftover pid's command line, and is not in the saved files. The
  experiment only read that process's identity and signalled nothing. A
  cleanup that killed by such a list would have killed it.

## Design

### The launcher 9d2 needs

`launched()` cannot carry the design as it is. It keeps its job handle
private, so a caller can neither list the job's processes nor wait on it. It
resumes the root before the caller can record it. It ends the job, then waits
five seconds on the root alone. And it assigns the job after creating the
process, so a server that dies in between leaves a suspended process outside
any job and outside the record. 9d2 changes it:

- The job is created first and given to `CreateProcessW` through
  `PROC_THREAD_ATTRIBUTE_JOB_LIST`, so the process is in the job from its
  creation. A process that cannot be created in its job is a refusal before
  anything is driven.
- The caller gets the job handle, records the root's identity (pid, creation
  time, image) while it is still suspended, and resumes it.
- Membership is read from the held job. `JobObjectBasicProcessIdList` gives
  pids. Each pid is opened, and the process counts only if `IsProcessInJob`
  confirms it on that same handle, so a pid that exits and is reused between
  the list and the open is never recorded.
- Ending is `TerminateJobObject`, then a wait of up to 30 seconds until the
  job's `ActiveProcesses` count, from `JobObjectBasicAccountingInformation`, is
  zero.

`owned_desktop()` binds the calling thread with `SetThreadDesktop`, which fails
on a thread that already has windows or hooks, and it restores and closes on
exit. 9d2 enters it on a dedicated thread that creates no windows, and it
treats a failed bind, restore or close as a held look.

### Starting the app

1. Each look writes its record before anything starts: the desktop name, the
   folders it creates, and each job's identity. The record and the evidence
   live in the run's folder, outside the folders the look removes.
2. The built output is started unpackaged, from the tree, in its job, on that
   desktop. The build turn must give a debug output that keeps its data in a
   folder the run names and that never hands off to another instance. The
   look sets `APPDATA`, `LOCALAPPDATA`, `TEMP` and `TMP` into its folder, plus
   the stack's own setting where it has one (`WEBVIEW2_USER_DATA_FOLDER` for
   WebView2, `--user-data-dir` for Electron). Environment variables do not
   bind an app that reads known folders through the shell API, or one that
   finds an owner's instance through a session-wide mutex. So an app that
   exits without a window on the owned desktop is a finding.
3. The app's window is the visible top-level window of a job process that is
   not an input indicator. More than one candidate is a refusal. The window is
   resized to S1's `window` with `SetWindowPos` and no activation, and the
   result is read back from the window.

### Reading and driving it

The driver is a process on the owned desktop, in a job of its own. It reads
the UI Automation control view and acts only through patterns: `Invoke`,
`Value.SetValue`, `Toggle`, `SelectionItem.Select`, `ExpandCollapse`,
`Scroll` and `ScrollItem`. It sends no synthetic input (`SendInput`,
`SetCursorPos`, window messages for keys or clicks) and uses no clipboard.
A step names its control by `AutomationId`, scoped to its screen. A name is
used only when no id exists, and it must match exactly one element. Each step
ends with its postcondition read from the tree.

Every call into the app has a deadline: a shot, a tree read, an action and a
postcondition each get 10 seconds, and the look as a whole has S7's limit.
`PrintWindow` asks the app to render synchronously, and a provider can block
while a modal dialog opens. So the driver runs those calls in its own process,
and the server ends that process's job when a deadline passes.

Each screen and state is shot with `PrintWindow` and `PW_RENDERFULLCONTENT`,
per visible top-level window of the app's job.

The checks read the tree as the Android loop reads its hierarchy:

- Mechanical: an interactive control without a name, a control outside its
  window, a target under 24×24 physical pixels, a crash or an exit of the app.
  Geometry is UI Automation's bounding rectangle, in physical pixels, and the
  window's rectangle is read in the same space (`DwmGetWindowAttribute` with
  `DWMWA_EXTENDED_FRAME_BOUNDS`), not from `GetWindowRect`, which a
  DPI-unaware caller sees scaled.
- Placement, by each feature's id within its screen. An `every` feature is
  visible on the first surface. A `sometimes` one is reached in one action. A
  `rarely` one is absent from the primary surface but present in some state
  the scenarios reach. A `never` one has no control in any state or window
  the scenarios reach, and the look counts that absence only after every one
  of those states was reached and read. A virtualized list is scrolled through
  `ScrollItem` before an absence in it counts.

### Cleanup and its proof

Cleanup runs on every outcome, from the record, as the Android look's does. It
ends the driver's job and the app's job, then closes the desktop. The proof
holds only when every check reads a definite answer:

- each job's `ActiveProcesses` count is zero, read on the job handle the look
  holds;
- no window remains on the desktop, enumerated through the desktop handle the
  look holds, before `CloseDesktop` succeeds on it;
- each recorded process identity is gone. `OpenProcess` fails because no such
  process exists, or opens a process whose creation time differs. A failure
  for any other reason, such as access denied, is not an answer;
- no recorded process holds a listening TCP port, read from the TCP table
  before the jobs end and again after;
- the look's disposable folders, which the record lists, are gone.

An inconclusive check holds the look, as a leftover does, and the look names
it. The next look cleans every earlier record before it starts, so a look a
server crash cut short is cleaned first. For a record whose server died, the
job handle died too. A kill-on-close job ends its processes when its last
handle closes, so the next look checks that the recorded identities are gone
and the desktop has no windows left.

## Refusals and findings

| Case | What the look does |
| --- | --- |
| The output is packaged only (MSIX, Store) and needs installing into the owner's account | Refused at preflight; the build must give an unpackaged output |
| The app asks for elevation in its manifest | Refused at preflight: it cannot start without a consent prompt to the owner |
| The process cannot be created inside its job | Refused before anything is driven |
| No app window within 30 seconds, or the app exits, including a handoff to another instance | A finding, with the app's log |
| More than one candidate window | Refused as ambiguous |
| A shot, tree read, action or postcondition passes its deadline | The jobs are ended, the cleanup proof runs, and the step is a finding |
| A shot fails or is a single colour | A finding |
| Controls the scenarios name are missing from the tree (the tkinter case) | A finding: the app cannot be checked. S2 should prefer a stack that exposes UI Automation |
| A step's control has no pattern for its action | A finding; the driver never falls back to input |
| A name matches more than one element | A finding against the step's id; the driver never takes the first match |
| A window on the owned desktop belongs to a process outside the jobs | Refused: something left the job and the run cannot prove it owns it. A broker that starts a process with no window there is not detected; the build turn's no-handoff, no-helper requirement covers it |
| Any cleanup check is left or inconclusive | The look is held and names what was left |

## What the owned desktop can and cannot show

Measured: windows on the owned desktop do not appear on the owner's desktop,
and a GDI copy of the screen from the owned desktop fails. An overlay drawn on
the owned desktop cannot sit over a window on the owner's desktop.

Not measured: capturing a window on the same owned desktop with Windows
Graphics Capture's window API, and an overlay window over a fixture window on
the same owned desktop. Either would let the screen translator's capture and
overlay checks run on the owned desktop against a fixture viewer, as Android's
look installs one. 9d2 measures both.

Plan 9 requires each owed `device` check to pass in its kind's S7 loop on the
current HEAD before R3. If the owned desktop cannot run the capture and
overlay checks, 9e chooses with the owner between two ways, and either keeps
that gate:

- a round on the owner's own desktop before R3, approved at that time, as the
  Android phone round is, which runs those checks and records their evidence
  in the run;
- an explicit change to plan 9's gate.

## Not measured

- WinUI 3, WPF, WinForms, Electron and a Tauri build. No build of theirs ran
  here, and Edge stands in only for Chromium's rendering and tree.
- Edge without `--force-renderer-accessibility`, so the tree a WebView2 app
  gives by default.
- Any pattern other than `Invoke`, and any app with more than one window.
- Whether the shots were drawn with hardware acceleration.
- Display scale. The windows reported 96 DPI, but Windows reports 96 for a
  DPI-unaware window at any scale.
- The data-folder variables listed above, and an owner's instance running
  beside the look.
- Windows Graphics Capture, and an overlay, on the owned desktop.

The saved files do not record the runtime versions. Edge 157 and Tk 8.6 were
read from this PC in the session.

## Next

9d2 builds the session as one module on the launcher above: the record,
start, window choice, pattern-only driver with its deadlines, shots, cleanup
and proof. It carries a test for each refusal and finding above, and one real
run of the Edge fixture whose cleanup proof is saved with its evidence. It
also measures the capture and overlay questions above, so 9e starts from
answers.
