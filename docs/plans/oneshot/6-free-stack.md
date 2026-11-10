# Step 6 — The stack is the owner's, not the card's

The overall design is in the [overview](0-overview.md). Steps 2–5 run one
stack end to end: the environment card fixes FastAPI, Vite with React,
Tailwind, SQLite and npm, and the pipeline reads the backend through FastAPI
and pytest. This step takes the stack off the card and out of the pipeline.

Goal. The owner picks the stack in the spec conversation from the agent's
options, and every stage after it works with that stack: the contract tests
run in the project's own test tool, the feature split reads the approved API
document, and the frontend build follows the chosen stack's conventions.

## Decided with the owner

2026-11-11, by options.

| Question | Decision |
| --- | --- |
| Which card fields go | Backend, frontend, styling, data store, deployment, JavaScript package manager, Python environment and its conda name, and both budgets |
| Who picks the stack, when | The owner, in S2: the agent offers options with reasons, the owner chooses |
| Where the contract tests run | The project's own test tool, whatever the stack is |
| Budgets | No ceiling. The ledger is still charged and shown |

## The card after this step

| Field | Stays because |
| --- | --- |
| `os_now`, `os_later` | Scripts, paths and line endings, for any stack |
| `auth` | Whole screens and endpoints; the design checks it |
| `visibility`, `license`, `ci`, `github_owner` | The repository, before any code |
| `ui_language`, `docs_language` | Copy and documents |
| `responsive` | S7's viewports |
| `llm` | `.env.example` and key handling |

The saved profile per kind keeps only these. A saved profile holding a removed
field loads with that field dropped.

## Budgets

`oneshot_profile.allowed` and its two ceilings go. Every turn is still charged
to the run's ledger (`charged`, `cut`), and the screen still shows money spent,
time spent and calls. A run no longer stops with the reason `budget`.

## S2 — the stack in the conversation

The `oneshot-spec` block gains a `stack` list beside the requirements:

```json
{"stack": [{"part": "backend", "choice": "FastAPI (Python, uv)", "why": "…", "status": "confirmed"}]}
```

| Part | What it names |
| --- | --- |
| `backend` | Language, framework and its environment or package manager |
| `frontend` | Framework and build tool, or server-rendered pages |
| `styling` | How screens are styled |
| `data_store` | Where data lives |
| `tests` | The test tool for the contract tests and the frontend's tests |
| `deploy` | How the app is run beyond this machine, or local only |

- The first stack question offers two or three whole bundles, each naming
  every part with one reason, so one answer settles the stack. A later
  question changes one part only when the owner asks for it.
- `status` is `confirmed` only from the owner's answer. The server ends S2
  when every requirement is settled and every part is `confirmed`; an
  `assumed` part keeps the conversation open.
- The stack is shown at the top of the R1 sheet, read-only: changing it after
  R1 means a new run.

## Bootstrap — nothing stack-shaped before S2

The base commit holds `.gitignore` (a list wide enough for the common stacks),
the README stub without setup commands, `.gitattributes` and `LICENSE` as
before, and `.wiki/adapter.toml` with an empty `gate_cmd`. The spec starts
with an empty `done`; the gate enters it at S4, when the tests turn declares
the command.

## S4 — contract tests in the project's own tool

### What the tests turn returns

Besides the test files and whatever scaffold the tool needs, the turn ends
with one block:

```oneshot-runner
{"command": "npx vitest run --reporter=junit --outputFile={report}",
 "contract": ["tests/contract.test.ts"], "config": ["vitest.config.ts"]}
```

- `command` runs every test from the repository root and writes a JUnit XML
  report to `{report}`. JUnit is the one report format nearly every tool
  writes: pytest, Vitest, Jest, Go through `gotestsum`, Cargo through
  `nextest`, .NET, Playwright.
- `contract` lists the files holding the planned tests; `config` lists the
  files that change how the tool runs them.
- Each planned test's own name is exactly its `test_name`, not nested in a
  suite name, so the report names it.

### What the server checks

| Check | When | Refused if |
| --- | --- | --- |
| Red | The tests turn ends | A planned name is missing from the report, or passed, or was skipped |
| Pin | The server commits the tests | — it stores `command`, `contract` and `config` with `tests_head` |
| Pinned | Every later turn ends | A `contract` or `config` file differs from `tests_head` |
| Green | The build turn ends | A planned name did not pass, or was skipped, in a run of the stored command |

The server substitutes `{report}` with a path in the run's folder, outside the
worktree, and reads only that file. The adapter's `gate_cmd` becomes the
stored command with `{report}` set to `artifacts/junit.xml`.

### What this gives up

pytest's probe (`oneshot_probe`) proved three more things: that each test's
body ran (`entered`), that pytest's runner was not patched (`intact`), and
that a red test failed only for want of the application (`missing_app`). No
generic report carries them, so they go:

- A red test failing for another reason, a broken import in the test itself
  say, is not refused at red. It cannot turn green without editing a pinned
  file, so the build is refused at green instead, later than today.
- A configuration file the turn did not list, or application code that
  tampers with the tool's reporting, is not caught by the server. The
  backend's independent review (3c) reads the diff for it.

## S5 — operations from the approved API document

`served()` read FastAPI's `app.openapi()` through the project's interpreter.
The operations now come from `docs/design/api.yaml`, the OpenAPI document the
owner approved at R1 and the contract tests exercise. The split turn and the
board are unchanged.

## S7 — conventions without a stack

| Kept | Why |
| --- | --- |
| One port, read from `PORT` | The shots and the end-to-end checks open one address |
| Data under `APP_DATA_DIR` | A fresh data folder per shot |
| `data-feature` on every feature control | The placement checks |
| `e2e/seed.json` | The full state |
| `live_cmd` in the adapter | The server starts the app |

| Dropped | Instead |
| --- | --- |
| The frontend lives in `web/` | Wherever the chosen stack puts it |
| Hash routes | The build writes `e2e/screens.json`, `{"S1": "/months"}`, and the shots open those paths |
| `gate_cmd` keeps the backend's pytest command | It keeps the stored contract command |

## Runs started before this step

A run whose spec carries `oneshot.version` 1 stops with the reason `version`
on its next step: its profile and its pins have the old shape. It is kept for
reading; a new run starts over.

## Parts

| Part | Scope | Status |
| --- | --- | --- |
| 6a | This document; budgets removed from the card, the ceilings and the screen | In progress |
| 6b | S4 on the runner block and JUnit, S5 from `api.yaml`, S7's conventions | Not started |
| 6c | The stack in S2 and on R1, the card's stack fields removed, the bootstrap without them, the prompts reading the stack, version 2 | Not started |

6b goes before 6c so every merged state still runs: with the old card the
tests turn can declare a pytest command, and 6c then moves the choice into S2.
