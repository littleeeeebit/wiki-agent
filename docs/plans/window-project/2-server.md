# Stage 2 — the server takes the project from the request

Purpose. Two windows on two repositories send requests the server answers
for each one's own repository, without refusing either and without closing
the other's sessions.

## Current

- `query._project` is the one selection, persisted in `raw/chat/project.json`.
- `query.project()` compares the request's `X-Project` with it and refuses a
  mismatch with 409 and `X-Project-Moved`.
- `query.configure` moves the selection, refuses while any focus answers
  (`cid in _busy or (switched and _busy)`) and closes every session of the
  other projects (`inactive`).
- `query.hold(_busy, _lock, cid, ...)` keys a running answer by focus only.
- `app.only_this_screen` refuses a write that names no project.

## Change

1. `project()` returns the request's `X-Project` when it names a repository
   `channels.repo_for` knows; an unknown name is 400. A request without the
   header (only `/api/channels` and reads before the screen knows its project)
   gets the default, `raw/chat/project.json`.
2. `configure` with a new `repo` records it as the default for the next new
   window and answers with the project's settings. It no longer moves anything
   for other windows, refuses only while this project's focus answers, and
   closes no session.
3. `_busy` is keyed by `(repo, cid)`. A conversation record already carries
   `repo` (`raw/chat/<cid>.jsonl`), so records need no migration.
4. Sessions of a project nobody has asked for are closed after an idle limit
   (30 minutes, a setting), never while a turn runs, waits on an approval or a
   review loop holds its cell. Their `session_id` stays, so the next question
   resumes them, as a switch does today.
5. `X-Project-Moved` and the 409 for a mismatch go away. The refusal of a
   write without a project stays.

## Risks

- A write with a stale or forged `X-Project` acts on that repository. The
  server is local and pairs the mobile companion, and the header must name a
  known repository, so this is the same trust as any request today.
- Two windows on the same repository share its sessions and focus holds, as
  two tabs would. That is wanted: the second window sees the first one's
  answer running and waits for it.
- The idle limit changes when a CLI process ends. A test pins that a busy,
  waiting or loop-held session is never closed.

## Done when

- A test drives two clients with different `X-Project` headers: each asks the
  wiki focus at the same time, each starts a task, and neither gets 409 or the
  other's records.
- Switching in one client leaves the other client's session alive.
- The idle test above passes.
