This turn builds the frontend the person approved, on the backend that is
built and reviewed.

The design is committed in this worktree: `docs/design/frontend.md`, with the
mock pages under `docs/design/mock/` and their screenshots under
`docs/design/shots/`. The approved screens, states, type scale, spacing,
chosen palette, features and the owner's frequency map:

```json
{design}
```

The environment profile: `{profile}`.

The backend's contract tests were committed at `{tests_head}` and are
pinned; these files may not change at all:

{contract}

## Conventions the server relies on

- The frontend lives in `web/` and follows the profile's `frontend`,
  `styling` and `js_pm`. Screens use hash routes: the screen `/months` is
  opened at `/#/months`, so no screen route meets an API path.
- The app serves the built screens and the API on one port. It reads the port
  from the environment variable `PORT` and keeps all its data under the folder
  in `APP_DATA_DIR`, creating what it needs there; the backend's contract
  tests keep passing.
- Every control for a feature carries `data-feature="<feature id>"`. Each
  screen shows its states: empty, loading (while the API has not answered),
  error (the API answered 500) and full.
- `e2e/seed.json` lists the API calls that fill the app the way a person
  would, `[{"method": "POST", "path": "/…", "body": {…}}]`; each must succeed
  on an empty data folder.

## The adapter

During this turn you may write `.wiki/adapter.toml`, and only during it.
Fill `live_cmd` with one command that, run from the repository root, builds
the screens if needed and starts the app (it reads `PORT` itself). Extend
`gate_cmd` so it also runs the frontend's tests and build, keeping
`{gate}` in it unchanged. Commit the adapter with the rest.

## When this turn ends

The server checks, in order:

1. The `done-report` covers every item of the spec's `done` (one per screen),
   each passing.
2. Every pinned file is unchanged since `{tests_head}`.
3. The adapter's `gate_cmd` still runs `{gate}`, and `live_cmd` is filled.
4. The adapter's gate passes on a clean, committed HEAD, and every planned
   backend test passes by node id.
5. The server starts the app with `live_cmd` on a fresh data folder and again
   after `e2e/seed.json`, and shoots every screen at {viewports} in each
   state. A console or page error, a placement the owner's map forbids, a
   feature that never appears, or a `sometimes` feature not one click from
   the first screen is refused; a horizontal overflow is recorded.

A refusal comes back with the reason, at most three times in all.

## In this turn

- Commit everything and leave nothing uncommitted, then end with the
  `done-report`.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Visible text follows the profile's `ui_language`; Korean text is written
  directly in Korean, never translated from English.
