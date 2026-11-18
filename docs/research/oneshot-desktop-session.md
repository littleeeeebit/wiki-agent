# Oneshot desktop session — where S7 runs a Windows app

Plan 9 ([9-kinds](../plans/oneshot/9-kinds.md)) adds `desktop`, a program
installed on Windows. Its S7 loop starts the built app, drives each core
scenario, shoots each screen and state, reads the UI tree for the mechanical
and placement checks, and closes everything it started. It must do all of that
without touching the owner's windows, focus, mouse or keyboard. This note is
step 9d's first deliverable: the options on this PC, what was measured here on
2026-11-18, the design that follows, its refusals and its cleanup proof. 9d2
builds the design; "Next" names what it measures.

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
with `launched()`. It took the fixture's processes from a parent-pid snapshot
six seconds after the start. A reader then ran on the same desktop as
a Windows PowerShell 5.1 process, also through `launched()`, and after it
finished the driver added a second snapshot. The reader listed the
visible windows of the first snapshot's processes, called `SetWindowPos` on the largest one
(800×600, no activation, no z-order change, its result not checked), shot it
with `PrintWindow` and `PW_RENDERFULLCONTENT`, walked the UI Automation control
view, invoked the first element named `Go` through its `InvokePattern`, then
looked for an element named `clicked` and shot again. The driver read the
owner's foreground window at three points: before the desktop was created,
after the snapshot and before the reader started, and after cleanup. At the
middle point, once, it also listed from a thread on the owner's desktop every
window of the first snapshot's processes.

| Check | tkinter (`tk.json`) | Edge (`edge.json`) |
| --- | --- | --- |
| Windows of the first snapshot's processes on the owner's desktop, in the one sample | none | none |
| Owner's foreground window | same at all three points | same before and at the middle point; different after cleanup, cause not recorded |
| Shot size | 800×600, resized from 656×519 | 800×600, already that size from `--window-size` |
| `PrintWindow` with full content | rendered | rendered, page content included |
| UI Automation tree | the window `fixture-tk` and three unnamed panes; `Go` not found | 26 nodes: the document, the text `ready`, the button `Go` with `Invoke` |
| `Go` invoked, then read | — | `clicked` found in the tree and seen in the second shot |
| Foreground window on the owned desktop | none | none |
| Identities from the two snapshots read alive after cleanup | none | none |
| Desktop opened again by name after cleanup | `OpenDesktopW` returned null | `OpenDesktopW` returned null |
| The run's Edge profile folder | — | removed |

A later Edge attempt (`edge2.err`, `run-edge/report.json`) added a GDI copy of
the screen from the reader. The copy failed with an invalid-handle error. That
attempt then failed in cleanup: the launcher's 5-second wait for the root
process ran out after the job was ended, and the driver stopped before its
checks, so its files record no cleanup result. A process listing in the
session afterwards found no Edge process started in the previous ten minutes,
and the profile folder was removed by hand. Neither is in the saved files.

These are observations, not a cleanup proof. A null from `OpenDesktopW` also
means a failed probe. The process check read the union of two parent-pid
snapshots, not the job; it skipped a pid whose identity could not be read at
the snapshot, and it counted an unreadable identity after cleanup the same as
a gone one.

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

- The job is created first, with a unique name the record holds before the
  launch and a handle that is not inheritable. It is given to `CreateProcessW`
  through `PROC_THREAD_ATTRIBUTE_JOB_LIST`, so the process is in the job from
  its creation, and every process it starts is too. A process that cannot be
  created in its job is a refusal before anything is driven. So is
  `ERROR_ELEVATION_REQUIRED`, with no retry through the shell. The process
  inherits only its own input and log, named through
  `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`, never another inheritable handle of the
  server.
- The caller gets the job handle, records the root's identity (pid, creation
  time, image) while it is still suspended, and resumes it. A root whose
  identity cannot be read, or whose record fails, is ended before it runs.
- Membership is followed as it happens. The job is tied to an I/O completion
  port before its first process is created, and the port posts `JOB_OBJECT_MSG_NEW_PROCESS` for every process that joins
  and `JOB_OBJECT_MSG_EXIT_PROCESS` or `JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS`
  for every one that leaves. On each join the look opens the pid at once and
  keeps the handle only if `IsProcessInJob` confirms it on that same handle,
  so a reused pid is never held.
- Ending is `TerminateJobObject`, then a wait of up to 30 seconds until every
  member that joined has a proven exit. A held member is proven only by its
  signalled handle, which is bound to that process; an exit message names a
  pid, which a later member may carry. A member that exits before the look can
  open it is proven by its exit message. A failed `TerminateJobObject` is no
  proof, and late messages are waited for until the 30 seconds run out. `ActiveProcesses` reaching zero is recorded as an
  accounting observation, never as the proof: Windows can report zero before a
  terminated process has finished exiting. A member with neither proof leaves
  the proof inconclusive.
- Windows does not guarantee those messages, so a member whose join and exit
  were both lost would be missing from every rule above. Once the job has
  ended and can admit no new member, its cumulative `TotalProcesses` count,
  from `JobObjectBasicAccountingInformation`, must equal the number of
  distinct members the look verified and held. Any difference leaves the proof
  inconclusive. The count proves coverage; the signalled handles prove exit.
  9d2 tests a windowless child, with no listener, whose two messages are both
  dropped.

`owned_desktop()` binds the calling thread with `SetThreadDesktop`, which fails
on a thread that already has windows or hooks, and it restores and closes on
exit. It yields only the name. 9d2 has it yield the handle too, so cleanup can
enumerate the desktop's windows before the handle closes. 9d2 enters it on a
dedicated thread that creates no windows, and it treats a failed bind, restore
or close as a held look.

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
3. The app's main window is chosen once, at startup: the visible top-level
   window of an app-job process that is not an input indicator. More than one
   candidate then is a refusal. Top-level windows the app opens later, such
   as a dialog or an overlay, belong to the state that opened them: they are
   shot and read with it, and they are not candidates. The driver resizes the
   main window to S1's `window` with `SetWindowPos` and no activation, under
   the driver's deadline, and reads the size back. A wrong size is a finding.
4. A look that needs another app, such as the screen translator's fixture
   viewer, starts it in a third job the look owns, recorded and cleaned like
   the others.

### Reading and driving it

The driver is a process on the owned desktop, in a job of its own. It reads
the UI Automation control view and acts only through patterns: `Invoke`,
`Value.SetValue`, `Toggle`, `SelectionItem.Select`, `ExpandCollapse`,
`Scroll` and `ScrollItem`. It sends no synthetic input (`SendInput`,
`SetCursorPos`, window messages for keys or clicks) and uses no clipboard.
A step names its control by `AutomationId`, scoped to its screen. A name is
used only when no id exists, and it must match exactly one element. Each step
ends with its postcondition read from the tree.

Every call into the app has a deadline: a resize, a shot, a tree read, an
action and a postcondition each get 10 seconds, and the look as a whole has
S7's limit.
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
  `rarely` one is absent from the primary surface but present in some
  approved state. A `never` one has no control in any approved screen, state
  or window. The features' frequencies come from the S5 board. The coverage
  set is the desktop kind's screen map and state list that R2 approved from
  S6's blocks. Absence counts only after every screen and state in that set
  was reached and read; one the look could not reach holds the check rather
  than passing it.
- A virtualized container is read to its verified end before an absence in it
  counts: through `ItemContainer.FindItemByProperty` and
  `VirtualizedItem.Realize` where the container supports them, otherwise by its
  `Scroll` pattern until the scroll position reports the end, reading each
  viewport. A container whose end cannot be verified holds the check.

### Cleanup and its proof

Cleanup runs on every outcome, from the record, as the Android look's does. It
ends every job the look owns, then closes the desktop. The proof holds only
when every check reads a definite answer:

- every process that joined a job has exited, by the launcher's rule above:
  each held member's handle signalled, and an exit message for each member
  never held;
- no window remains on the desktop, enumerated through the desktop handle the
  look holds. `CloseDesktop` then succeeds on that handle, and afterwards
  `OpenDesktopW` by the recorded name fails with not-found;
- each recorded process has exited: its handle is signalled, or `OpenProcess`
  fails with not-found, or it opens a process whose creation time differs. A
  process object another holder keeps open after exit still reads as exited,
  through its signalled handle;
- before the jobs end, the look reads which listening TCP ports the recorded
  processes hold. After, a listener counts against the look only if its
  owner's current identity matches a recorded one, so a reused pid does not
  hold the look;
- the look's disposable folders, which the record lists, are gone.

Any other error from these calls, such as access denied, is not an answer.
An inconclusive check holds the look, as a leftover does, and the look names
it.

The next look checks every earlier record before it starts. A server crash
takes the look's job handles and completion port with it. Kill-on-close then
asks Windows to end the jobs' processes, but nothing left proves they finished.
A job's name can vanish while a process still holds the job, and a process
that joined after the last recorded join has no identity to test. So recovery
does not try to prove from names that the processes ended. It accepts one of
three proofs:

- the look's record shows its proof completed before the crash;
- the PC restarted after the record was written. The record holds the uptime,
  `GetTickCount64`, read just before the launch. An uptime now lower than that
  proves a new boot. Wall-clock times prove nothing here, since the clock can
  be set back and corrected within one boot; 9d2 tests that case;
- the original logon session ended. The record holds that session's logon id
  (its LUID) and logon time. The proof is that no logon session with that id
  and time exists any more. A newer logon, which can run beside the old one
  under fast user switching, proves nothing by itself.

The last two prove only that the processes ended. Recovery then removes the
record's disposable folders and checks they are gone; a run-owned profile or
temp folder survives a restart. The record is cleared only after both. Without
a process proof, recovery kills what it can still identify: a recorded identity
that reads the same, by its handle, and the job, if it still opens by the
recorded name in the record's own session namespace. It then holds the run and
names the record. The owner releases the hold by restarting or signing out,
and the next look applies the proofs above. A folder that cannot be removed
keeps the hold. 9d2 tests a crashed look that leaves its profile behind and
stays held while removing it fails. Name probes for jobs and the desktop run
only in the record's session and window station; elsewhere they are not an
answer.

## Refusals and findings

| Case | What the look does |
| --- | --- |
| The output is packaged only (MSIX, Store) and needs installing into the owner's account | Refused at preflight; the build must give an unpackaged output |
| The app asks for elevation in its manifest | Refused at preflight: it cannot start without a consent prompt to the owner |
| The process cannot be created inside its job | Refused before anything is driven |
| No app window within 30 seconds, or the app exits, including a handoff to another instance | A finding, with the app's log |
| More than one candidate window | Refused as ambiguous |
| A shot, tree read, action or postcondition passes its deadline | The jobs are ended, the cleanup proof runs, and the step is a finding |
| A shot fails, or, in a state whose approved block shows content, its content area (the bounds of the screen's content element in the tree) is a single colour | A finding |
| A tree read fails, or lacks an id the screen's scenarios name | A finding when the app answered; held when the read itself failed |
| An action's postcondition reads false | A finding |
| The resize ends at another size | A finding |
| Controls the scenarios name are missing from the tree (the tkinter case) | A finding: the app cannot be checked. S2 should prefer a stack that exposes UI Automation |
| A step's control has no pattern for its action | A finding; the driver never falls back to input |
| A name matches more than one element | A finding against the step's id; the driver never takes the first match |
| A window on the owned desktop belongs to a process outside the look's jobs | Refused: something left the job and the run cannot prove it owns it. A broker that starts a process with no window there is not detected; the build turn's no-handoff, no-helper requirement covers it |
| Any cleanup check is left or inconclusive | The look is held and names what was left |

## What the owned desktop can and cannot show

By Windows' design, windows on a desktop that is not the input desktop are not
shown on the owner's screen, and an overlay drawn there cannot sit over a
window on the owner's desktop. The experiment's one sample agreed: the owner's
desktop listed no fixture window. Measured: in the later Edge attempt, the
one GDI copy of the screen from the owned desktop failed.

Not measured, and separate questions:

- capture: Windows Graphics Capture's window API on a fixture viewer window on
  the same owned desktop;
- overlay: an overlay window over that viewer on the same desktop. Both
  windows' frame bounds and their z-order are geometry evidence only: a
  layered window at zero opacity can sit exactly over the viewer and show
  nothing. A check that the overlay shows its content needs a composed picture
  of the overlay over the viewer. If 9d2 finds no way to take one on the owned
  desktop, that check stays open for the round below.

The screen translator's combined scenario needs both. Each one the owned
desktop cannot show needs its evidence from elsewhere. Plan 9 requires each
owed `device` check to pass in its kind's S7 loop on the current HEAD before
R3. A round on the owner's own desktop before R3 keeps that gate: approved at
that time, as the Android phone round is, it runs the missing checks and
records their evidence in the run. The other way is an explicit change to plan
9's gate. 9e chooses with the owner.

## Not measured

- WinUI 3, WPF, WinForms, Electron and a Tauri build. No build of theirs ran
  here, and Edge stands in only for Chromium's rendering and tree.
- Edge without `--force-renderer-accessibility`.
- The tree a WebView2 app gives by default, a separate question from Edge's.
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
start, window choice, pattern-only driver with its deadlines, shots, cleanup,
recovery and proof. It carries a test for each refusal and finding above, and
one real run of the Edge fixture whose cleanup proof is saved with its
evidence. It measures two things on the owned desktop: window capture and an
overlay over a fixture viewer, so 9e starts from answers. The other items
under "Not measured" stay open for 9e, which runs the stack S2 picks.
