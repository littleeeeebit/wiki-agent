---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# web/ — the React screen, the Tauri desktop window around it, and browser tests

## What it is for

`web/src/` is the app's screen, a React 19 + TypeScript + Vite single page
styled with Tailwind 4 and shadcn/base-ui (`web/package.json:12-41`).
`web/src-tauri/` is the desktop window. Rust does three things there: it
starts the Python server as a sidecar, opens a window on it and holds the
terminals. Everything else comes from that server, so the window shows the
same page a browser gets (`web/src-tauri/src/main.rs:1-4`). `web/tests/`
holds Playwright browser tests against fake servers.

## Entry points

- Screen: `web/src/main.tsx` → `web/src/App.tsx`.
  - Feature components: `web/src/components/`, for example `Query.tsx`,
    `TaskRail.tsx`, `Review.tsx`, `Refactor.tsx`, `Suite.tsx`,
    `MobileAccess.tsx`.
  - The map: `web/src/graph/`.
  - Shared logic: `web/src/lib/`.
  - All HTTP goes through `web/src/lib/api.ts`. `get` attaches the
    `X-Project` header (`web/src/lib/api.ts:197-210`).
  - Streams are read as SSE over `fetch`, because `EventSource` is GET only
    (`web/src/lib/api.ts:326-330`).
- Scripts:
  - `npm --prefix web run dev` runs Vite with `/api` proxied to
    `127.0.0.1:8787` (`web/vite.config.ts:12-13`).
  - `build` runs `tsc -b && vite build`.
  - `lint` runs `oxlint` (`web/package.json:6-11`).
- Window: `main` in `web/src-tauri/src/main.rs:270-296`.
  - It starts `python tool/main --port <free port> --exit-with-stdin`, using
    `.venv`'s Python when there is one (`web/src-tauri/src/main.rs:46-78`).
  - It logs to `raw/main.log` (`web/src-tauri/src/main.rs:272`).
  - It exposes `pty_*`, `open_url` and `restart` to the page
    (`web/src-tauri/src/main.rs:279`).
- Browser tests: `web/tests/*.py`. For example, `task_browser.py` starts a
  fixture FastAPI app and drives it with Playwright
  (`web/tests/task_browser.py:1-20`).

## Depends on, depended on by

- It depends on the server's `/api/*` routes (`tool/main/app.py:135-148`).
  The server serves `web/dist/index.html` and its assets
  (`tool/main/app.py:52`, `tool/main/app.py:449-456`).
- It depends on `DESIGN.md` for screen values and on the `design-pass`
  skill for polish order.
- It depends on the Android shell's one-way mode hint, read in
  `web/src/lib/mobile.ts:22-23`.
- It is depended on by `tool/app.cmd`, which refuses to start without
  `web/dist/index.html` and rebuilds the Tauri exe on each start
  (`tool/app.cmd:20-35`).
- It is depended on by the final gate: `npm --prefix web run lint`, `build`
  and `python web/tests/task_browser.py` (`verification.toml`, local
  command; `.github/workflows/verify.yml:147`).

## What trips a newcomer

- The screen does nothing without the Python server. In development, run
  `python tool/main` and `npm --prefix web run dev` side by side
  (`web/vite.config.ts:12-13`).
- The window does not use port 8787. It picks a free port per start
  (`web/src-tauri/src/main.rs:60`). Only the browser path and Vite's proxy
  assume 8787.
- `vite build` keeps old hashed chunks (`emptyOutDir: false`), because open
  windows may still import the previous build (`web/vite.config.ts:9-11`).
  `web/dist` grows until it is pruned by hand.
- Every request but `/api/channels` waits until the first channel list names
  the project. A request without `X-Project` would land in whatever project
  the server is on (`web/src/lib/api.ts:181-197`). A 409 with
  `X-Project-Moved` makes the screen follow another window's switch
  (`web/src/lib/api.ts:203-208`, `tool/main/query.py:94-96`).
- `tauri.conf.json` declares no windows and no CSP. The window is created in
  code (`web/src-tauri/tauri.conf.json:7-10`), and bundling is off
  (`web/src-tauri/tauri.conf.json:11-14`).
- `web/README.md` is the untouched Vite template (`web/README.md:1-5`) and
  says nothing about this app.
- Of the browser tests, only `task_browser.py` runs in the gate
  (`.github/workflows/verify.yml:147`). The others (`mobile_browser.py`,
  `refactor_browser.py`, `desktop_browser.py`, …) are run by hand when their
  area changes.
- UI labels are Korean and stay Korean. They are names of Korean things and
  are not translated in prose either (`operator/english-progress.md`).
