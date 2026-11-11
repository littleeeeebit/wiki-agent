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
- Where the tool can, `command` makes each testcase carry a `file`
  attribute: pytest with `-o junit_family=xunit1`, jest-junit with
  `addFileAttribute`. The server pins it with the case.

### What the server checks

| Check | When | Refused if |
| --- | --- | --- |
| Named | The tests turn ends | A listed file is missing or outside the worktree, or a file the turn changed names a planned test but is not in `contract` |
| Red | The tests turn ends | A planned name is missing from the report, or passed, or was skipped, or two of its testcases carry the same `[classname, name, file]`, or the report names a file for one that is not in `contract` |
| Pin | The server commits the tests | — it stores `command`, `contract`, `config` and each planned case as `[classname, name, file]` from the report (`file` empty when the report has none), with `tests_head` |
| Pinned | Every later turn ends | A `contract` or `config` file differs from `tests_head` |
| Green | The build turn and the final gate (S8) end | A pinned case is absent, skipped, failing or reported more than once in a run of the stored command |

S7 and S8 run the stored command themselves before the adapter's gate. They
also refuse an adapter whose `gate_cmd` does not start with it, or adds
anything after it other than ` && ` commands: no `||`, `;` or `#`, which
could swallow its failure or hide it in a comment. The published gate, which
CI and later sessions run, then runs the contract first.

A planned name matches a testcase whose name equals it in any case, or that
name followed by a parameter in brackets (`name[…]`) or in parentheses after
a space (`name (…)`), so Go's `Test_name` and pytest's parameter cases match;
any other text after the name is another test. A case is pinned by its classname, name and
file together: a test of the same name in another class, or in another file
the report names, never stands in for it, and a configuration that deselects
or skips a pinned case leaves it absent or skipped at green.

A report without `file` identifies a case by classname and name only, and
those do not always name one file: pytest turns `tests/a.b/test_total.py` and
`tests/a/b/test_total.py` into the same classname `tests.a.b.test_total`. If
the pinned case stops running and a new file reporting the same classname
and name passes, green reads one passing case and accepts it. Green refuses
the case only when both run, because the pinned identity is then reported
twice.

The server substitutes `{report}` with a path in the run's folder, outside the
worktree, single-quoted so neither PowerShell nor bash expands anything in it
(PowerShell's typographic single quotes are doubled like the plain one), and
reads only that file. A report's `file` is read as a worktree path when the
worktree holds a file there, and otherwise as the end of a `contract` path,
since a runner started in a folder below the root names files from there. A run that reaches the tests turn records version 2,
whatever version it started at, since its record now has version 2's shape. The adapter's `gate_cmd` becomes the
stored command with `{report}` set to `artifacts/junit.xml`.

### What this gives up

pytest's probe (`oneshot_probe`) proved three more things: that each test's
body ran (`entered`), that pytest's runner was not patched (`intact`), and
that a red test failed only for want of the application (`missing_app`). No
generic report carries them, so they go, and nothing at green brings them
back:

- Red no longer says why a test failed. A test that fails for want of a
  helper module unrelated to the application, and checks nothing about it,
  passes once the build adds that module. A later pass proves the case ran
  and passed, not that it exercised the application.
- A body that never ran is not caught: a runner patched to report a pass, by
  a configuration file the turn did not list or by application code, reads as
  a pass in the report.
- Without a `file` attribute, the file a case came from is not checked: a
  new file whose case reports the pinned classname and name stands in for a
  pinned case that stopped running.

The planned tests' `given`, `when` and `then` were approved at R1, and the
backend's independent review (3c) reads the contract tests and the diff
against them. That review, not the server, is what stands between these
cases and a green build.

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
| Hash routes | Each screen opens at its own route as a path (`/months`), which server-rendered pages and history-routed apps both serve; S6 refuses a route that an API path also matches |
| `gate_cmd` keeps the backend's pytest command | It keeps the stored contract command |

## Runs started before this step

Each part that changes the shape of what a run stores raises `VERSION` in
that part. The run record and the spec it hands over to both carry it. One
check refuses, with HTTP 409, every owner action on a run of an older
version that is already past the stage whose record changed. The refusal
tells the owner to start a new run, and the run stays readable.

The check stands in front of every way forward, not only the spec's
`[재개]`. A run waiting for the owner is not running, so a server restart
leaves it as it was, and its next step is a submission, not a resume. The
actions it guards are the run's resume (`/api/oneshot/{rid}/resume`), the
spec's `[재개]`, and the submissions `say`, `r1`, `board`, `r2` and `r3`.

| Part | Version | Refused |
| --- | --- | --- |
| 6b | 2 | A version 1 spec past the tests turn: it pinned pytest node ids and no command |
| 6c | 3 | A version 2 run whose bootstrap wrote its files: its README and adapter carry the card's stack, and the bootstrap's commit compares them with what 6c writes |
| 6c | 3 | A version 2 spec past S2: its stack is in the card's old fields, not in `stack` |

A run still before the changed stage carries on: the new code writes the new
record when it reaches that stage.

Each part's tests include its own cases: for 6b, a version 1 spec waiting at
R2 whose `r2` submission is refused; for 6c, a version 2 spec waiting at R1
whose `r1` submission is refused, and a version 2 run stopped after the
bootstrap's `files` step and before its `commit` whose resume is refused.

## Parts

| Part | Scope | Status |
| --- | --- | --- |
| 6a | This document; budgets removed from the card, the ceilings and the screen | Done (#331) |
| 6b | S4 on the runner block and JUnit, S5 from `api.yaml`, S7's conventions, version 2 | In progress |
| 6c | The stack in S2 and on R1, the card's stack fields removed, the bootstrap without them, the prompts reading the stack, version 3 | Not started |

6b goes before 6c so every merged state still runs. After 6b a new run's
tests turn declares a pytest command from the old card, and a run already
past its tests turn is refused on resume and on every submission (version 1). 6c then moves the
choice into S2.

What each merged state is checked against:

| After | A new run | A run from before the part |
| --- | --- | --- |
| 6a | Runs to the end on the card's stack, with no ceiling | Carries on; a card saved with the budget fields shows and saves without them |
| 6b | Declares its runner and runs to the end | Before the tests turn: carries on. Past it: refused on resume and on every submission |
| 6c | Picks its stack in S2 and runs to the end | Before the bootstrap's files: carries on. Files written, or past S2: refused on resume and on every submission |
