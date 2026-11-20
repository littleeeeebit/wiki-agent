This turn builds the Windows program's screens the person approved, on the
core that is built and reviewed.

The design is committed in this worktree: `docs/design/screens.md`, with the
mock sources under `docs/design/mock/` and their pictures under
`docs/design/shots/`. The approved screens, states, type scale, spacing,
chosen palette, features and the owner's frequency map:

```json
{design}
```

The environment profile: `{profile}`. The look sets the program's main window
to {window} physical pixels, its frame included.

The core's contract tests were committed at `{tests_head}` and are pinned
with the test runner's configuration; these files may not change at all:

{contract}

{tools}

## Conventions the server relies on

The server copies the program the gate builds out of this worktree and starts
the copy unpackaged, without elevation, on a desktop of its own, out of the
owner's sight. It reads the program's controls through UI Automation and
acts only through automation patterns, never synthetic input.

- The screens follow the stack's `ui`. Each screen shows where its `surface`
  says: `window` is a window of the program's own, `dialog` is a dialog over
  one of them, `floating` is a window kept over another program's, and `tray`
  is the menu or flyout of its icon in the notification area.
- The debug build opens a screen in a state when it starts with two
  environment variables: `ONESHOT_SCREEN` (a screen id such as `S2`) and
  `ONESHOT_STATE` (`empty`, `full`, `loading` or `error`). `full` shows the
  program filled with the debug build's own sample data, `empty` with none,
  `loading` as it stays while the work has not finished, and `error` as it
  shows a failed load. A `floating` screen opens over a plain window of the
  program's own, and a `tray` screen opens its menu or flyout. A release
  build ignores both variables.
- Every control for a feature has the `AutomationId` `feature:<feature id>`:
  in a web view, the element's `id`; in XAML,
  `AutomationProperties.AutomationId`. A `never` feature has no control
  anywhere, in any state.
- The main window takes the size the look sets, and the content follows it.
- The program keeps all its data under the folder the look names, and never
  hands off to another instance: a second start opens its own window, and no
  session-wide lock or the owner's running copy keeps it from starting. The
  look also points `APPDATA`, `LOCALAPPDATA`, `TEMP` and `TMP` there; a web
  view stack reads its own setting as well, such as
  `WEBVIEW2_USER_DATA_FOLDER` for WebView2 or `--user-data-dir` for Electron.

## The adapter and the desktop block

During this turn you may write `.wiki/adapter.toml`, and only during it.
Extend `gate_cmd` so it also builds the debug program into a folder of its
own: it starts with `{gate}` unchanged, and the new commands follow, each
after ` && `: plain commands with no `;`, `|`, `#`, backtick, `$(`, brackets,
braces, lone `&` or new line, none starting with a shell's own word such as
`exit` or `exec`. Each command starts one of the host programs listed above,
or a file inside the worktree. Put anything more involved in a project
script. Commit the adapter with the rest.

End the turn with exactly one fenced block whose info string is
`oneshot-desktop`, holding JSON:

```oneshot-desktop
{"run": {"folder": "dist-pc", "program": "app.exe", "args": [], "cwd": ".",
         "env": {"WEBVIEW2_USER_DATA_FOLDER": "{data}/webview", "APP_DATA_DIR": "{data}"}},
 "installer": "src-tauri/target/release/bundle/nsis/app-setup.exe"}
```

- `run.folder` is the folder `gate_cmd` builds that holds everything the
  program loads from the build, its libraries and its resources, and nothing
  else: at most 5000 files and 1 GB, with no link inside it. Gather a
  stack's debug output there when its own build folder holds more.
- `run.program` is the debug `.exe` inside that folder, a real Windows
  program; `run.cwd` is its working directory, `.` or a folder inside it;
  `run.args` and `run.env` are its arguments and the environment it adds.
- `{data}` is the data folder the look gives each start. At least one
  argument or variable names it.
- An argument or variable names only the copy, by a path relative to
  `run.cwd`, `{data}`, or a remote URL such as `https://` or `ws://`. It holds
  no drive (`C:`), UNC or device path (`\\`), file URI, path from a drive's
  root (`/Users/...`), `..` step, or `%VAR%`; the server refuses one that
  does, before the gate runs.
- `installer` is the file a release build packages for S8 to attach. The
  final gate builds it, not this turn's `gate_cmd`; this turn only names it,
  and it is never the program `run` starts.

`run.folder` and `installer` are relative to the repository root;
`run.program` and `run.cwd` are relative to `run.folder`. Every path uses
`/`, with no `.` or `..` part, no part ending in `.`, and no Windows device
name such as `NUL` or `COM1`.

## When this turn ends

The server checks, in order:

1. The `done-report` covers every item of the spec's `done` (one per screen),
   each passing.
2. Every pinned file is unchanged since `{tests_head}`.
3. The adapter's `gate_cmd` starts with `{gate}` and adds only ` && `
   commands, each starting a listed host program or a worktree file, and the
   `oneshot-desktop` block has the shape above.
4. The adapter's gate passes on a clean, committed HEAD, every pinned core
   test passes by its class and name, and `run.folder` and `run.program` are
   there.
5. The server copies `run.folder` into a folder the run owns and checks the
   copy: a program Windows does not start as a 32- or 64-bit process (a
   library is not one), a packaged layout such as an MSIX folder around the
   program, or a manifest, embedded or beside the program, that asks for
   elevation is refused. `run.folder` is copied only under the name it is
   declared by, its case as stored on disk. The server then records every
   copied file's digest with the launch settings and names the build by
   them. A change to any file or setting is a new build.

A refusal comes back with the reason, at most three times in all.

## In this turn

- Commit everything and leave nothing uncommitted, then end with the
  `done-report` and the `oneshot-desktop` block.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Running the gate command or the host-side tests is fine. Install nothing,
  and do not start the program on the owner's desktop: the server's own
  desktop is the only one it runs on.
- Visible text follows the profile's `ui_language`; Korean text is written
  directly in Korean, never translated from English.
