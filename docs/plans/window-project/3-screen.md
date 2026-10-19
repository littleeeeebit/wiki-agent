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
   label (`main-2`, …) on the same server page. The capability's `windows`
   list becomes a pattern that covers those labels, and the command is added
   to `generate_handler!` and the capability's permissions.
3. Closing one window closes only that window. Restarting (the update card,
   `restart`) still ends the shared server, so its question counts the work of
   every window: the server reports running turns and loops for all projects,
   not only those the asking window has seen.
4. A running turn's project comes from its session or its task record, never
   from its path. A row of another project shows the approvals the screen
   already knows for that path.

## Done when

- A browser test with two pages on one fixture server: page A on project
  `fixture-a`, page B picks `fixture-b`; page A keeps `fixture-a` and its next
  request is answered for it.
- The rail test covers a shared checkout path and a waiting approval in another
  project.
- The restart question lists work from both windows.
