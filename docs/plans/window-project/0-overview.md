# Window project — each window works in its own repository

Purpose. Let two windows show and drive two repositories at once, wiki-agent
in one and rfg_rag in the other, without either refusing the other or writing
into the other's project. Today the server holds one selected project for every
window, so working in two repositories means switching back and forth.

## Why this plan exists

The project picker in the top left was disabled while anything ran in the
selected project, so no other repository could be worked on during a task.
PR #178 fixes the symptom: the picker now waits only for what the server
waits for, and a running turn or loop keeps its own repository after a switch.

What #178 does not change is the model underneath:

- `tool/main/query.py` keeps one `_project` for the whole server, persisted in
  `raw/chat/project.json`.
- Every request carries the screen's project in `X-Project`. `query.project()`
  refuses a request whose screen shows another project with 409 and
  `X-Project-Moved`, and the screen then follows the server.
- A switch closes the live sessions of every other project
  (`query.configure`, the `inactive` list). Their `session_id` survives, so
  coming back resumes them, but two projects cannot be live together.
- A focus is held by its id alone (`hold(_busy, _lock, cid, ...)`), so the
  same focus in two projects would refuse each other.

So one window can now move between repositories while work continues, but a
second window either follows the first or is refused.

## Target

The project is a property of the window, not the server.

- A request's project is the `X-Project` it names, checked against the known
  repositories. `query.project()` returns it instead of comparing it with a
  global.
- `raw/chat/project.json` stays as the project a new window opens on, the
  last one picked. Picking a project in a window does not move other windows.
- Sessions, focus holds and model settings are keyed by repository and focus.
  `_sessions` and `_config` already are; `_busy` becomes `(repo, cid)`.
- A switch closes nothing that another window may be using. Idle sessions are
  closed by an idle limit instead, so memory stays bounded.
- Background work names its repository explicitly and never reads a selection.

## What has to change

| Area | Current | Change |
| --- | --- | --- |
| `query.project()` | Global `_project`, 409 on mismatch | Return the request's `X-Project` when it names a known repository; the global only as the default for a request without one |
| `query.configure` | Writes the global, closes other projects' sessions, refuses while any focus answers | Records the window's choice and the default for new windows; closes nothing; refuses only while this project's focus answers |
| Focus holds | `_busy` keyed by `cid` | Keyed by `(repo, cid)` |
| `app.only_this_screen` | Refuses a write that names no project | Unchanged; a write still has to name its project |
| `refactor.charted` | Stops a run with `moved` when the selection changed, and calls `planning.begun`, which reads the selection | Passes the worker's repository to planning; the `moved` stop goes away |
| `work.ours` and `loop.mine` | Default to the selected repository | Callers pass the request's repository; check no thread calls them without one |
| Screen (`web/src/lib/api.ts`, `App.tsx`) | Follows `X-Project-Moved` and the server's choice | Keeps its own project per window (session storage), opens on the default, and treats 409 as an error, not a move |
| Tauri (`web/src-tauri/src/main.rs`) | One window labelled `main` | A command that opens another window with its own label |
| Feed events | One stream for all | Unchanged; each window refetches with its own `X-Project`, and events already carry their path or repository |

About 54 call sites read `project()` or `current_repo()`. Nearly all are route
handlers, where the request's header answers them. The ones above are the
exceptions found so far; the first step is to confirm that list.

## Risks

- A background thread that still reads the selection would silently act on
  whichever repository a window picked last. The first step lists every call
  outside a request and a test pins each one.
- Closing idle sessions by time instead of by switch changes when a CLI process
  ends. The limit needs a test that a busy or waiting session is never closed.
- The mobile companion shows one project. It keeps using the default until it
  gets its own picker.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 0 | Picker follows the server | #178: the picker no longer locks while a turn or loop runs | In review |
| 1 | Audit | Every `project()` and `current_repo()` call classified as request or background, with background ones passing their repository | Not started |
| 2 | Server per request | `project()` from `X-Project`, holds keyed by repository, switch closes nothing, idle limit | Not started |
| 3 | Screen per window | Window-owned project, no following on 409, a second window from the desktop app | Not started |
| 4 | Verification | Two windows on two repositories: a task in each, a review loop in one, a switch in the other, nothing refused or crossed | Not started |
