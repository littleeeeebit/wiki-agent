# Step 2 — Run record, intake, environment and the empty-folder bootstrap

The overall design is in the [overview](0-overview.md). This step covers S0
and S1 and ends with a connected repository on GitHub and a spec that S2 can
start from. Nothing in it writes product code.

Goal. The owner types one or two sentences, confirms a restatement, confirms
one environment card, and the app turns an empty folder into a repository with
an adapter, a base commit on `main` pushed to GitHub, and a spec whose worktree
is ready for S2.

## What already exists and is reused

| Need | Existing piece | Where |
| --- | --- | --- |
| A run that survives restarts | Run state in an additive spec field, a worker thread per run, `recover()` at start turns running phases into `stopped`, `[재개]` continues | `tool/main/planning.py` (`planned`, `halted`, `recover`, `RUNNING`) |
| The only writer of spec files | `specs.load`, `specs.save`, `specs.update` under the files' lock | `tool/main/specs.py` |
| A task worktree off the base branch | `specs.fork` | `tool/main/specs.py:295` |
| Adapter text and its hash record | `connect.adapter_text`, `connect.keep` | `tool/main/connect.py:484`, `:82` |
| A picked folder outside the workspace | `connect.pick_folder`, `connect.added` | `tool/main/connect.py:149`, `:117` |
| Which folders are projects | `channels.projects` lists every workspace or picked folder that is not `미연결` | `tool/main/channels.py:102` |
| Blocks inside an answer | `specs.blocks` extracts fenced JSON blocks and removes them from the text | `tool/main/specs.py:556` |
| Spending ceiling | `common.budget.Budget` | `tool/common/budget.py` |

## Why the run has two records

A spec belongs to a repository (`raw/specs/<repo>/<id>.json`), and during S0
and S1 there is no repository yet. So the run lives in two places, each owning
one stretch and never both at once:

| Stretch | Owner | What it holds |
| --- | --- | --- |
| S0–S1 | `raw/oneshot/<run>.json` | The owner's sentence verbatim, the confirmed restatement, the profile, the target folder, bootstrap progress |
| From the base commit on | The spec `raw/specs/<repo>/<id>.json`, field `oneshot` | Stage state for S2–S8 and the run id pointing back |

When bootstrap finishes, the run record gets `spec: {repo, id}` and is never
written again; the spec's `oneshot.run` points back. A reader starts from
either and finds the other.

## Run states in this step

`intake → environment → bootstrap → spec`. `spec` is where step 3 begins.
`stopped` is reachable from each with a reason.

| Reason | When | `[재개]` does |
| --- | --- | --- |
| `restart` | The server stopped mid-stage | Reruns the stage from its last recorded sub-step |
| `budget` | The run's ceiling was reached | Asks for a new ceiling, then continues |
| `folder` | The target folder is not empty, or vanished | Re-checks the folder |
| `github` | `gh` is not logged in, the name is taken, or the push was refused | Re-runs the failed sub-step only |
| `block` | The model's block failed validation twice | Sends the stage turn again |

## S0 — Intake

1. The owner enters the sentence and a target: a new folder name in the
   workspace, or an existing folder picked with `[폴더 찾기]`. A picked folder
   must be empty — no files at all, hidden ones included — or the run refuses
   it before any model turn.
2. One model turn returns an `oneshot-intent` block:

   ```json
   {"restated": "…", "kind": "webapp", "name": "budget-tracker",
    "users": "…", "first_screen": "…"}
   ```

   `kind` is one of `webapp`, `api`, `cli`, `desktop`, `data`. `name` is the
   repository and folder name, checked like a task slug (`specs.slugged`).
3. The screen shows the restatement in Korean beside the owner's own sentence.
   The owner confirms or edits it as text; an edit goes back to the model once
   more. The owner's sentence is stored verbatim and never rewritten: later
   stages quote it, and the glossary in S2 takes its terms from it.

## S1 — Environment card

### The profile schema belongs to the server

The list of choices, their options and their validity rules are code, not
prompt text, so a model cannot invent a choice or an option. The model only
proposes values, and only where the intent gives a reason to differ from the
saved profile.

- Schema: the table in the overview's "Environment choices", one entry per
  row, each with its options and default.
- Saved profiles: `raw/oneshot/profiles/<kind>.json`, one per kind. A run
  reaching bootstrap writes its final profile back to its kind's file.
- The model's turn returns an `oneshot-profile` block: `{"<field>": {"value":
  "…", "why": "…"}}`, only for fields it changes. A field or option outside the
  schema refuses the block.
- Fields the intent decides (for example "uses an LLM", or conda because the
  intent names GPU work) are proposed; fields only the owner can know (budget,
  visibility) are always shown empty on a first run.

### Validity rules

Checked by the server before the card can be confirmed; each shows as a line
on the card, not as a refusal.

| Rule | Why |
| --- | --- |
| Stdlib `venv` with "OS later" planned warns and suggests `uv` | The venv interpreter path differs between Windows and macOS, so a committed `gate_cmd` cannot name it on both |
| Conda needs an environment name | The README carries `conda activate <name>`, which is also what `connect.conda_python` reads |
| Public needs a license | The overview's choice |
| Private forbids a CI workflow file | The owner's rule: private means no hosted CI |
| Budget must be filled | The run's `Budget` is built from it |

### The card

One card, every field visible, prefilled from the kind's profile. Changed
fields carry the model's one-line `why`. Confirming the card is also the
approval for the two outward actions bootstrap takes, creating the GitHub
repository and pushing `main`, so the card states both in a line above the
confirm button with the repository's owner and visibility.

## Bootstrap

A server routine, no model. Each sub-step is recorded in the run record
before the next starts, so `restart` continues from the first unfinished one
and nothing is done twice.

| # | Sub-step | Detail |
| --- | --- | --- |
| 1 | Folder | Create it in the workspace, or re-check the picked one is still empty |
| 2 | Files | `.gitignore` from the profile's languages; `.gitattributes` with `* text=auto eol=lf` when another OS is planned; a README stub with the restatement, the owner's sentence and the setup command for the chosen environment; `LICENSE` when public |
| 3 | Adapter | `.wiki/adapter.toml` from the profile through `connect.adapter_text`; its hash through `connect.keep`, the same record `[연결]` leaves |
| 4 | Git | Initialise with `main`, commit the files above as the base commit |
| 5 | GitHub | Create the repository through `gh` with the chosen visibility and owner, add it as `origin`, push `main` with upstream |
| 6 | Register | A picked folder is registered as `[폴더 찾기]` does, so `channels.projects` lists it |
| 7 | Spec | Create the run's spec (`goal` from the restatement, `oneshot` field with the run id and stage `spec`) and its worktree with `specs.fork` |

### The adapter's `gate_cmd`

Written from the profile, never from `connect.guess`. `guess` would see a
`pyproject.toml` later and pick the server's own `python -m pytest`, which is
the interpreter without the project's packages; a connected repository's
final gate once failed on 88 collection errors that way.

| Environment | `gate_cmd` at bootstrap |
| --- | --- |
| `uv` `.venv` | `uv run pytest` |
| conda | the `conda run --no-capture-output -n <name> python -m pytest` form `connect.conda_python` builds |
| stdlib `venv` | the interpreter path for the current OS under `.venv` |

The frontend half of the gate is added in step 4, once there is a frontend to
test. The gate is not run at bootstrap: there are no tests yet, and pytest
exits non-zero on an empty collection. The first gate run is S4's.

### What is never done on failure

Bootstrap deletes nothing. A failed sub-step leaves the folder, the local
repository and any created GitHub repository as they are and stops with the
reason; `[재개]` continues from that sub-step. A half-made GitHub repository
is the owner's to delete, never the app's.

## Server and screen

| Path | Does |
| --- | --- |
| `POST /api/oneshot` | Starts a run: sentence and target; refuses a non-empty folder |
| `GET /api/oneshot` | Runs in progress, for the rail |
| `POST /api/oneshot/{run}/intent` | Confirms or edits the restatement |
| `POST /api/oneshot/{run}/profile` | Saves card edits, or confirms the card and starts bootstrap |
| `POST /api/oneshot/{run}/resume` | `[재개]` after a stop |

Module: `tool/main/oneshot.py`, composed like `planning.py`. Prompts:
`tool/prompts/oneshot-intent.md` and `tool/prompts/oneshot-profile.md`, in
English, with the instruction that every string meant for the screen is
written in Korean directly.

The screen in this step is the minimum to drive it: a `새 프로젝트` entry on
the project rail, the intake form, the restatement panel and the environment
card. Gate sheets and the drag board are step 5.

## Completion

- New tests in `tool/test_oneshot.py`, covering: the intent and profile
  blocks refused outside the schema; every validity rule; bootstrap on a
  temporary folder with `gh` stubbed, interrupted after each sub-step and
  resumed without repeating one; a non-empty picked folder refused; the
  `gate_cmd` for each environment; a restart turning each running state into
  `stopped` with `restart`.
- The repository's gate, `tool/lint.py --check` and `tool/debt.py check`.
- One real run on this machine with a throwaway private repository, recorded
  in the pull request: the card, the created repository, and the spec's
  worktree.

## Decided with the owner

Nothing yet for this step.
