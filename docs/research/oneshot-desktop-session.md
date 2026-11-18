# Oneshot desktop session — where S7 runs a Windows app

Plan 9 ([9-kinds](../plans/oneshot/9-kinds.md)) adds `desktop`, a program
installed on Windows. Its S7 loop starts the built app, drives each core
scenario, shoots each screen and state, reads the UI tree for the mechanical
and placement checks, and closes everything it started. It must do all of that
without touching the owner's windows, focus, mouse or keyboard. This note is
step 9d's first deliverable: the options on this PC, what was measured here on
2026-11-18, the design that follows, its refusals and its cleanup proof.

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
Windows on it are never shown on the owner's screen and never take the owner's
focus, and Windows routes mouse and keyboard input to the input desktop only.
The repository already creates one: `owned_desktop()` in
`tool/native_verification.py`, used by the Win32 collector, and
`launched()` in `tool/native_process.py` starts a process on it, suspended,
inside a kill-on-close job.

## What was measured

Two fixtures, each with a heading, a line reading `ready` and a button `Go`
that changes the line to `clicked`:

- a tkinter window (Python 3, Tk 8.6);
- Edge 157 in `--app` mode with a fresh `--user-data-dir`. Chromium is the
  engine of WebView2, which Tauri uses, and of Electron, so this stands in for
  the stacks S2 offers that draw on the GPU.

The driver created the desktop with `owned_desktop()` and started the fixture
with `launched()`. A reader then ran on the same desktop as a Windows
PowerShell 5.1 process, also through `launched()`. It resized the window with
`SetWindowPos` (no activation, no z-order change), shot it with `PrintWindow`
and `PW_RENDERFULLCONTENT`, walked the UI Automation control view, invoked
`Go` through its `InvokePattern`, then read and shot again. From a thread on
the owner's desktop, the driver listed every window of the fixture's processes
and read the foreground window before, during and after the run.

| Check | tkinter | Edge |
| --- | --- | --- |
| Windows of the app on the owner's desktop | none | none |
| Owner's foreground window during the run | unchanged | unchanged |
| Resized to the card's 800×600 | yes | yes |
| `PrintWindow` with full content | rendered | rendered, page content included |
| UI Automation tree | 4 nodes, none named; `Go` not found | 26 nodes: the document, the text `ready`, the button `Go` with `Invoke` |
| `Go` invoked, then read | — | `clicked` in the tree and in the second shot |
| Foreground window on the owned desktop | none | none |
| GDI copy of the screen from the owned desktop | not tried | fails: invalid handle |
| Processes after cleanup | none | none |
| Desktop opened again by name after cleanup | fails: gone | fails: gone |
| The run's Edge profile folder | — | removed |

Three observations shaped the design:

- Each fixture process also owned two input-indicator windows
  (`UAC_InputIndicatorOverlayWnd`, `UAC Input Indicator`) on the owned
  desktop. The app's window is the largest visible one that is neither.
- In one Edge run, ending the job took longer than the launcher's 5-second wait,
  which raised. No Edge process remained afterwards. The wait needs a longer
  bound, and the proof must check again after it.
- Finding the app's processes by walking recorded parent pids adopted an
  unrelated process: the ADB server on port 5037, whose recorded parent pid
  had since been reused by the fixture. The experiment only read its
  identity and signalled nothing, but a cleanup that killed by that list would
  have killed it. Ownership is the job's process list, never a parent pid.

## Design

### Starting the app

1. Each look creates one owned desktop with a fresh name and writes the run's
   record before anything starts: the desktop name, the folders it creates,
   and then each process's identity (pid, creation time, image) as the job
   reports it. The desktop is never switched to.
2. The built output is started from the tree, unpackaged, suspended, inside a
   kill-on-close job, on that desktop. Its data goes to run-owned folders:
   `APPDATA`, `LOCALAPPDATA`, `TEMP` and `TMP` point into the look's folder,
   and the stack's own setting where it has one (`WEBVIEW2_USER_DATA_FOLDER`
   for WebView2, `--user-data-dir` for Electron). A fresh data folder also
   keeps a single-instance app from handing off to a copy the owner has open,
   as Edge showed.
3. The app's window is the largest visible top-level window of a job process,
   other than the input indicators. It is resized to S1's `window` with
   `SetWindowPos` and no activation.

### Reading and driving it

The driver is a process on the owned desktop, in a job of its own. It reads
the UI Automation control view and acts only through patterns: `Invoke`,
`Value.SetValue`, `Toggle`, `SelectionItem.Select`, `ExpandCollapse`,
`Scroll` and `ScrollItem`. It sends no synthetic input (`SendInput`,
`SetCursorPos`, window messages for keys or clicks) and uses no clipboard,
which the owned desktop shares with the owner's window station. A step whose
control has no pattern for its action is a finding against the app, never a
reason to fall back to input.

Each screen and state is shot with `PrintWindow` and `PW_RENDERFULLCONTENT`,
per top-level window of the job.

The checks read the tree as the Android loop reads its hierarchy:

- Mechanical: an interactive control without a name, a control outside its
  window, a target under 24×24 pixels, a crash or an exit of the app.
- Placement, by each feature's `AutomationId` or name: an `every` feature is
  visible on the first surface, a `sometimes` one is reached in one action, a
  `rarely` one is absent from the primary surface, and a `never` one has no
  control anywhere.

### Cleanup and its proof

Cleanup runs on every outcome, from the record, as the Android look's does. It
ends the driver's job and the app's job, then waits up to 30 seconds for every
process the record names. The proof holds only when all of these are true:

- no process the record names is alive, compared by pid, creation time and
  image, so a reused pid is neither counted nor touched;
- the desktop name can no longer be opened;
- the look's folders are gone;
- no recorded process holds a listening port.

Anything left holds the look and names what was left. The next look cleans
every earlier record before it starts, so a look a server crash cut short is
cleaned first.

## Refusals

| Case | What the look does |
| --- | --- |
| The output is packaged only (MSIX, Store) and needs installing into the owner's account | Refused at preflight; the build must give an unpackaged output |
| The app asks for elevation in its manifest | Refused at preflight: it cannot start without a consent prompt to the owner |
| No app window within 30 seconds, or the app exits | A finding, with the app's log |
| Two candidate windows of the same size | Refused as ambiguous |
| Controls the scenarios name are missing from the tree (the tkinter case) | A finding: the app cannot be checked; S2 should prefer a stack that exposes UI Automation |
| A window on the owned desktop belongs to a process outside the jobs | Refused: something left the job, for example through a broker or a Store-installed shell, and the run cannot prove it owns it |
| Cleanup leaves anything | The look is held and names what was left |

## What the owned desktop cannot show

The screen translator's Windows side captures another app's screen and draws
over it. The GDI screen copy fails on the owned desktop, because only the input
desktop has a screen to copy. Windows Graphics Capture was not tried. So the
owned desktop can show the translator's own windows and controls, but not
capture or overlay against another app.

This parallels the Android phone round before R3. Two ways remain, and 9e
decides between them with the owner:

- one round on the owner's own desktop before R3, approved at that time, with
  the fixture viewer and the app shown on the owner's screen;
- the backend review's `device` deferral, with the capture and overlay checks
  left to the owner at R3.

## Not measured

- WinUI 3, WPF and WinForms. Their UI Automation support is native, but no
  build of theirs ran here.
- A Tauri build. Its WebView2 shares Edge's engine, measured above, but the
  WebView2 data-folder variable and a Tauri window's tree were not run.
- Display scale: every window on the owned desktop reported 96 DPI, so a
  look at another scale needs its own measurement.
- Windows Graphics Capture from the owned desktop.

## Next

9d2 builds the session as one module: the record, start, window choice,
pattern-only driver, shots, cleanup and proof, reusing `owned_desktop()` and
`launched()` and reading ownership from the job's process list. It carries a
test for each refusal above and one real run of the Edge fixture with its
cleanup proof.
