---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# tool/ — all Python: the hooks, the app server and its pipelines, checks and tests

## What it is for

`tool/` holds every piece of Python except `laya/`:

- the host hooks that inject and enforce the wiki's rules;
- the app server (`tool/main/`) and the pipelines it composes;
- the maintenance and check scripts;
- the tests.

`docs/architecture.md` is the authority for how the parts fit, and
`docs/development.md` for the checks.

## Layout and entry points

- Pipelines (packages): `common`, `wiki`, `translate`, `agent`,
  `workspace`, `search` and `decision`. `tool/lint.py:496` lists them as
  `PIPELINES`. A pipeline may import only itself and `common`
  (`tool/lint.py:538-551`). Each package's role is in the table at
  `docs/architecture.md` "Pipelines".
- `tool/main/` is the program, the one package allowed to compose the
  pipelines.
  - `python tool/main` puts `tool/` on `sys.path` and calls `main.app.main`
    (`tool/main/__main__.py:1-10`).
  - The FastAPI app mounts one router per workflow: query, architecture,
    suite, work, specs, loop, planning, refactor, improvements, connect,
    verification, mobile and update (`tool/main/app.py:135-148`).
  - The default port is 8787 (`tool/main/app.py:556`).
  - On startup the server takes the lock and recovers loops, plans and
    refactors (`tool/main/app.py:60-77`).
- Hooks are separate from the program (`docs/architecture.md`, "The hooks…").
  - `tool/hook.py` is the single entry the user-level host settings call, as
    `hook.py <host> <script>`. It works out the project per call
    (`tool/hook.py:1-16`).
  - `tool/apply.py` wires them. The UserPromptSubmit hook is `inject.py`, the
    SessionStart hook is `session_state.py`, and the Stop hooks are `sync.py`
    and `declared_continuation.py` (`tool/apply.py:35-38`, `tool/apply.py:409-417`).
  - Each page's `enforce.pretooluse` script becomes a PreToolUse entry
    (`tool/apply.py:175`, `tool/apply.py:411`).
  - `tool/setup_agents.py` installs the hooks for a project
    (`tool/setup_agents.py:104`, `tool/setup_agents.py:253-254`).
- Checks and maintenance at the top level:
  - `tool/lint.py`: the hub wiki and pipeline imports;
  - `tool/repo_lint.py`: a target's `.wiki/`;
  - `tool/debt.py`: the debt ratchet against `.wiki/ratchet.json`;
  - `tool/verify.py`: the final-verification owner;
  - `tool/graph.py` and `tool/repo_graph.py`: the maps;
  - `tool/omm_scan.py`: architecture documents;
  - `tool/improve.py`: self-improvement.
- Data folders:
  - `tool/prompts/`: model prompts, for example `work-spec.md`,
    `review-round.md` and `pr-write.md`;
  - `tool/markers/`: the Korean glossary and markers;
  - `tool/fixtures/`: test fixtures;
  - `tool/eval/`: the evaluation scripts over `eval/`.
- Launchers: `tool/app.cmd` (Windows) and `tool/app.command`. They build and
  start the Tauri window (`tool/app.cmd:7-9`, `tool/app.cmd:33-35`).
- Tests: flat `tool/test_*.py` files. `pytest.ini:2` sets
  `testpaths = tool`, and `tool/conftest.py` gives every test an empty home.

## Depends on, depended on by

- It depends on the Python packages in `requirements-*.txt`, on `web/dist`
  (the server serves the built screen, `tool/main/app.py:52`,
  `tool/main/app.py:449`), on the host CLIs, and on optional keys in `.env`
  (Gemini, TypeSafe, Langfuse; `tool/conftest.py:35-50`).
- It is depended on by `web/`: the screen calls `/api/*` and the Tauri shell
  starts `python tool/main` (`web/src-tauri/src/main.rs:1-4`,
  `web/src-tauri/src/main.rs:59-78`).
- Every Claude Code and Codex session on the machine depends on it, through
  the user-level hooks.
- `laya/` imports from it through `sys.path` (`laya/generate.py:57-59`).

## What trips a newcomer

- A change here is live at once. The hooks are wired at user level and run
  in every directory on the machine (`tool/hook.py:9-16`). An edit to
  `inject.py` or a PreToolUse script reaches every open session's next
  event. A hook must fail open: `inject.py` returns 0 on a payload it cannot
  read (`tool/inject.py:169-172`).
- Imports are bare (`from main import …`, `import decision`), because
  `tool/` is on the path (`tool/main/__main__.py:6`). Lint counts all three
  spellings of an import, so `tool.translate` does not slip through
  (`tool/lint.py:499-508`).
- Do not run whole-directory pytest. The root `conftest.py` refuses
  `pytest tool`, `pytest laya` or `pytest .` outside the final owner
  (`conftest.py:8-13`, `tool/verify.py:60-65`). Run the affected
  `tool/test_*.py` files or use `-k`.
- Tests switch off the search daemon, Gemini, TypeSafe, Langfuse and the
  Jev host fallback (`tool/conftest.py:31-53`). A test that needs one has to
  set it up itself.
- The debt ratchet caps a new file at 800 lines and a new test file at 1,500
  (`.wiki/ratchet.json:315-317`). Existing large files may not grow past
  their entries. The largest are `tool/main/knowledge.py` (about 3,900
  lines) and `tool/main/loop.py` (about 2,900).
- The server holds one OS lock per hub (`tool/main/runtime.py:159-170`). A
  second `python tool/main` against the same checkout cannot take over a
  live server's tasks.
- The selected project is one per server. A screen that sends a stale
  `X-Project` gets a 409 (`tool/main/query.py:75-97`). The plan to make the
  project per window, `docs/plans/window-project/0-overview.md`, has not
  started.
- `tool/app.cmd` must stay ASCII: cmd.exe reads it as CP949 (`tool/app.cmd:4-5`).
- `tool/main/app.py` is not launched with `uvicorn` from outside. Use
  `python tool/main` or the window, so `tool/` is on the path.
