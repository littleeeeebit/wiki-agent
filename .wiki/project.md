---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# Project — what wiki-agent is, how its parts are laid out and how they talk

wiki-agent is a desktop app plus a set of host hooks that sit on top of
Claude Code and Codex CLI. It keeps lessons as wiki pages, injects the
matching rules into an agent's session before it acts, and runs the
"decide the next task → delegate → review → merge → record why" cycle in one
window. People read Korean (screen, questions, options); agents write English
and people read it through a translation overlay (`README.md`).

This repository is also the hub wiki: the shared, repository-independent
rules live here and are injected into every connected repository.

## Top-level layout

| Path | What it is |
| --- | --- |
| `operator/`, `craft/` | The hub's shared rule pages (scope `operator` / `craft`), listed in `index.md`. Never copied into a target's `.wiki/` |
| `SCHEMA.md`, `ENFORCEMENT.md`, `MAINTENANCE.md`, `index.md` | How a page is shaped, the five enforcement layers, when and how to update the wiki, the rule index |
| `DESIGN.md` | Screen values (spacing, type, colour) for `web/` |
| `skills/` | Host skills: `review-loop`, `after-merge`, `retrospect`, `design-pass`, `write-markdown` |
| `tool/` | All Python: the hooks, the app server and its pipelines, checks and maintenance scripts, tests (`tool/test_*.py`) |
| `web/` | The screen (`web/src/`, React + Vite + TypeScript), the Tauri desktop window (`web/src-tauri/`), browser tests (`web/tests/`) |
| `android/` | The mobile APK shell: QR pairing, saved HTTPS origin, hardened WebView (`docs/mobile.md`) |
| `laya/` | Laya: fine-tuned per-kind decision models meant to replace Jev's typed decisions (`laya/README.md`, `docs/plans/laya/`) |
| `eval/` | Evaluation fixtures for Jev (`eval/jev/`) and Laya (`eval/laya/`); run results stay off git |
| `docs/` | Maintainer documents (architecture, development, setup, Jev, verification), `docs/plans/` (open and `done/` plans), `docs/research/` |
| `overview.html`, `overview/` | The Korean introduction site (GitHub Pages) |
| `adapters/` | Per-machine adapter files; only `adapters/example.toml` is tracked |
| `raw/`, `artifacts/` | Local state and scratch output, ignored by git: chat records `raw/chat/`, server lock, refactor runs, gate times |
| `.wiki/` | This repository's own project wiki: `adapter.toml`, `ratchet.json`, `decisions/`, Laya pages; everything else ignored unless force-added |
| `verification.toml` | Round command and complete local final command; hosted workflow `verify.yml` |

## The parts and who owns what

`docs/architecture.md` is the authority; in short:

- Pipelines under `tool/`, import-checked by `tool/lint.py` (`PIPELINES`):
  `tool/common/` (settings, budgets, process options; below everything),
  `tool/wiki/` (hub pages, front matter, matching), `tool/workspace/` (task
  branches, linked checkouts, host session records), `tool/translate/`
  (English normalization, Korean overlay), `tool/search/` (sources, chunks,
  evidence store, knowledge graph, retrieval, the search daemon),
  `tool/decision/` (Jev transport, typed decisions, policy, claims),
  `tool/agent/` (models and host sessions), and `tool/main/` which composes
  them into the query, work, specification, planning, review-loop, refactor
  and merge workflows.
- The hooks are separate from the program: `tool/inject.py` (SessionStart /
  UserPromptSubmit injection), `tool/hook.py` and the `PreToolUse` checks
  (`tool/deny.py`, `tool/edit_as_diff.py`, `tool/codex_pretool.py`,
  `tool/host_boundary.py`, ...). They match hub rules against the utterance
  without the search daemon or Jev. `tool/setup_agents.py` installs them
  into a target repository; `tool/apply.py` merges `enforce` blocks into
  host settings.
- Maintenance and checks at `tool/` top level: `tool/lint.py` (hub lint and
  pipeline imports), `tool/repo_lint.py` (a target's `.wiki/`),
  `tool/debt.py` (debt ratchet against `.wiki/ratchet.json`),
  `tool/verify.py` (the one final-verification owner), `tool/graph.py`
  (policy graph → root `graph.json`), `tool/repo_graph.py` (document map →
  `.wiki/graph.json`), `tool/omm_scan.py` (`.omm` architecture documents),
  `tool/improve.py` (self-improvement runner, `docs/self-improvement.md`).

## How the parts talk

- The screen talks to the app server over HTTP on `127.0.0.1:8787`
  (FastAPI, `tool/main/app.py`); a shared change feed invalidates records for
  every client. The mobile browser or APK reaches the same routes through a
  WebSocket bridge over an explicitly enabled `cloudflared` tunnel
  (`tool/main/mobile.py`, `tool/main/mobile_transport.py`).
- The app server drives host sessions (Claude Code, Codex) for work and
  review cells through `tool/agent/` and reads their events back; work
  sessions report with `done-report` / `spec-update` blocks parsed in
  `tool/main/specs.py` and `tool/main/work.py`; the review loop is
  `tool/main/loop.py`.
- Retrieval goes to the search daemon (`tool/search/daemon.py`), started by
  the first search that needs it and replaced when its code hash changes; its
  store lives in the user cache, per hub and project. Jev
  (`tool/decision/`) chooses among moves the code offers in `tool/main/knowledge.py`
  and `tool/main/loop.py`; it never executes.
- Hooks run per host event, outside the server, and write the target's
  `.wiki/trajectory.jsonl` and hook diagnostics.
- One server owns a hub's workflows, held by an OS lock on `raw/server.lock`;
  startup recovers interrupted tasks and resumes authorized loops
  (`tool/main/recovery.py`).
- Four different things are called a graph (policy, document map, evidence
  knowledge graph, workflow transition graph); the table in
  `docs/architecture.md` says which is which.

## Build, run and test

Setup and checks are in `docs/development.md`; install and connection in
`docs/chat-setup.md` and `docs/hooks-setup.md`.

- Install: `python tool/setup_chat.py install --agent codex|claude|both`
  (packages, the screen, CLI login). Requirements files:
  `requirements-chat.txt`, `requirements-hooks.txt`, `requirements-dev.txt`,
  `requirements-search.txt`. Needs Python 3.11+, Node 22.12+, Rust for the
  first window build.
- Run: `tool/app.cmd` (Windows) or `tool/app.command` opens the Tauri window;
  `python tool/main` serves the browser UI on port 8787. Stop by closing the
  window or Ctrl+C.
- Connect another repository: the app's 연결 flow, or
  `python tool/setup_agents.py --project <path> --agent both`.
- Frontend: `npm --prefix web ci`, `npm --prefix web run lint`,
  `npm --prefix web run build`. Android: `tool/build_android.ps1`.
- Tests: pytest under `tool/` (`pytest.ini`, root `conftest.py`, xdist) and
  `laya/`. During work run only the affected test files; whole-directory
  pytest is refused outside the final-verification owner.
- Round check (`verification.toml`): `python -m ruff check tool laya`.
- Final gate: `python tool/verify.py final --base main` picks hosted CI
  (`verify.yml`) for a public repository with an active workflow, otherwise
  one bounded local run of the `local` command in `verification.toml` (ruff,
  `tool/lint.py --check`, `tool/debt.py check`, pytest on `tool` and `laya`,
  web lint and build, `web/tests/task_browser.py`).
