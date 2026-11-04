# Stage 3 — each window keeps its own project

Purpose. A window remembers which repository it shows, keeps showing it when
another window picks something else, and a second window can be opened from
the desktop app.

## Current

- `web/src/lib/api.ts` sends `claimed` as `X-Project` and, on a 409 with
  `X-Project-Moved`, raises `project-moved`; `App.tsx` then follows the server.
- The desktop shell (`web/src-tauri/src/main.rs`) builds one window labelled
  `main`. `capabilities/default.json` grants IPC to `["main"]` only, and the
  `restart` command exits the whole app.
- `web/src/lib/leave.ts` counts the turns and loops this window shows, and asks
  before closing or restarting.
- The rail lists other projects' running work (`others`, `elsewhere` in
  `web/src/lib/tasks.ts`). The review of step 0 found two faults there:
  - A running turn's project is guessed from its path's parent folder
    (`tool/main/loop.py` `/api/loops`), so a shared checkout such as
    `C:/repos/a` is named `repos` and also lands in the current project's group.
  - A plain turn's row is fixed at `waiting: false`, so a waiting approval in
    another project does not show.

## Change

1. The window's project lives in `sessionStorage`, set when the person picks
   one. A new window starts from the server's default. The `project-moved`
   listener goes; a 409 is shown as an error.
2. A `new_window` Tauri command opens another `WebviewWindow` with a unique
   label (`main-2`, …) on the same server page. The command is declared in
   three places, all needed for a remote page to call it: `COMMANDS` in
   `web/src-tauri/build.rs` (which generates its permission),
   `generate_handler!` in `main.rs`, and the capability's permissions. The
   capability's `windows` list becomes a pattern that covers the new labels.
3. A terminal belongs to its window. `Ptys` records the opening window's
   label with each id, and the window's destroy event ends that window's
   terminals only. Today they are ended by `pty_close` from the page or by
   the app's `RunEvent::Exit`, and neither runs when one of two windows
   closes, so the closed window's shell would keep its worktree open.
4. Closing one window closes only that window. Restarting (the update card,
   `restart`) still ends the shared server, so its question counts the work of
   every window: running work turns and review loops (`/api/loops`, which
   reads `work._runs`) and answers being generated in a wiki, next-task or
   retrospective focus (`knowledge.LIVE`, which `/api/loops` does not read).
5. A running turn's project comes from its session or its task record, never
   from its path. A row of another project shows the approvals the screen
   already knows for that path.

## Done when

- A browser test with two pages on one fixture server: page A on project
  `fixture-a`, page B picks `fixture-b`; page A keeps `fixture-a` and its next
  request is answered for it.
- The rail test covers a shared checkout path and a waiting approval in another
  project.
- The restart question lists work from both windows, including the case where
  the other window has only a wiki answer being generated; cancelling it
  leaves that answer running.
- On the built app, `invoke('new_window')` from the page opens a window whose
  terminal IPC is allowed. Closing that window ends its shell, and the first
  window's shell and the server's work go on.

## As built (2026-11-04)

- `web/src/lib/api.ts` keeps `claimed` in `sessionStorage` under `project`. A
  stored one releases the request wait at once and goes with the first
  `/api/channels`; a new window has none and takes the default that list names.
- The `project-moved` listener stays. Since stage 2 the server sends
  `X-Project-Moved` only for a repository gone from disk, naming the hub, and
  #264's recovery needs the screen to follow it. No other 409 moves the screen.
- `new_window` opens `main-2`, `main-3`, … on the calling window's origin. It
  is async, because a window built in a synchronous command deadlocks on
  Windows. The capability's `windows` is `["main", "main-*"]`. The rail header
  has its button, in the desktop app only.
- `Pty` records its window's label. Its output goes to that window alone
  (`emit_to`), and `WindowEvent::Destroyed` kills that window's shells.
- `leave(closing)` asks nothing when another window is open
  (`getAllWindows`, granted by `core:default`). Otherwise it reads
  `/api/loops`: every running turn, the looping specs, and the new `answers`
  (the `knowledge.LIVE` runs not done). A failed read still asks.
- `/api/loops` names a turn by its task's `repo`, else by the main clone git
  reports (`workspace.sessions.checkout`). `elsewhere` takes the screen's
  approval count per path, so a row shows `승인 N`.
- Checks: `test_a_running_turn_is_named_by_its_repository_and_every_live_answer_is_counted`
  in `tool/test_window_project.py`, and `web/tests/window_project_browser.py`
  (in CI), which drives two pages and a stand-in for the shell's IPC. Each
  fails with its fix reverted. The built app's checks (a second window's
  terminal IPC, a closed window ending its shell) are stage 4's scenario.
