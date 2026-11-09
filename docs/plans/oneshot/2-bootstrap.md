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
| A linked task worktree, the original left on `main` | `workspace.create(repo, task, linked=True, base=...)` | `tool/workspace/worktrees.py:44` |
| Adapter text and its hash record | `connect.adapter_text`, `connect.keep` | `tool/main/connect.py:484`, `:82` |
| A picked folder outside the workspace | `connect.pick_folder`, `connect.added`, and the same-name refusal of folder registration | `tool/main/connect.py:149`, `:117`, `:710` |
| Which folders are projects | `channels.projects` lists every workspace or picked folder that is not `미연결` | `tool/main/channels.py:102` |
| Blocks inside an answer | `specs.blocks` extracts fenced JSON blocks and removes them from the text | `tool/main/specs.py:556` |
| Cost of a model turn | The turn's `cost_usd` from the CLI's `total_cost_usd` | `tool/agent/chat_session.py:1321` |

`specs.fork` is not used: it switches the original checkout to the task
branch (`workspace_mode` `branch`), and the oneshot run keeps the original on
`main` so the owner can open it at any time.

## Why the run has two records

A spec belongs to a repository (`raw/specs/<repo>/<id>.json`), and during S0
and S1 there is no repository yet. So the run lives in two places, each owning
one stretch and never both at once:

| Stretch | Owner | What it holds |
| --- | --- | --- |
| S0–S1 | `raw/oneshot/<run>.json` | The owner's sentence verbatim, the confirmed restatement, the profile, the target folder, bootstrap progress |
| From the base commit on | The spec `raw/specs/<repo>/<id>.json`, field `oneshot` | Stage state for S2–S8 and the run id pointing back |

The hand-over has one commit point: writing `spec: {repo, id}` into the run
record. Before it, the run record owns the run, and a resume finishes
bootstrap's last sub-step idempotently (below). After it, the run record is
never written again and the spec's `oneshot.run` points back. A reader starts
from either and finds the other.

## Identity fixed before anything is written

When the owner confirms the intake, the run fixes and records, under the
oneshot lock, everything later sub-steps will name: the folder path, the
repository name, the GitHub owner and name, the spec id and the branch
(`oneshot-initial`, a valid task name). The name is then checked against, and
reserved over:

- existing folders in the workspace and picked folders (`connect.added_names`);
- connection records (`raw/connect/<name>.json`), whose hash a later handover
  trusts, so a same-name record is never merged into;
- other runs that have not reached their commit point;
- the GitHub owner's repositories.

Any conflict refuses the name before a file, a record or a remote exists, and
the owner picks another.

## Spending

Not every provider reports money: Claude's result carries `total_cost_usd` (`tool/agent/chat_session.py:1321`),
while Codex runs on the subscription and reports tokens only (`:1095`,
`:1124`). So the run keeps its own ledger in the record that currently owns
it (`spend` in the run record, then in the spec's `oneshot` field), and each
role of the run — intake, spec, design, builder, critic, reviewer, sheet
rewrites — is metered by what its provider reports:

| Role's provider | Metered by | First turn, no sample yet | Turn ended without a cost |
| --- | --- | --- | --- |
| Reports USD (Claude) | The USD ceiling | Counted at the role's estimate, a setting per role with one default | Counted at the larger of the estimate and the role's largest seen cost, marked unknown |
| Subscription (Codex) | The hours ceiling only; never converted to USD | — | — |

- Before a USD-metered turn starts, the run refuses it when the spent total
  plus the role's largest seen cost, or its estimate before any sample, would
  pass the ceiling, and stops with `budget`.
- Every turn, of either kind, adds its elapsed seconds and one call to the
  ledger. The hours ceiling is checked against the ledger's seconds before
  each turn; calls are recorded for the card, with no ceiling of their own.
- The ledger is written after each turn, so a restart resumes with the spent
  totals; a turn cut by the restart counts as unknown at the rule above.
  A ledger whose USD entries are all estimates still enforces: the estimates
  are what was charged.

The card shows which roles fall under the USD ceiling and which only under
the hours ceiling, and that the ceilings are enforced between turns, never
inside one: a single turn may overshoot by its own cost.

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

   `kind` is one of `webapp`, `api`, `cli`, `desktop`, `data`; only `webapp`
   can run in this version (see "Supported combinations"), and any other kind
   ends the intake with that said. `name` is the repository and folder name,
   checked like a task slug (`specs.slugged`) and then reserved as in
   "Identity fixed before anything is written".
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

### Supported combinations

Every later stage assumes a web backend and a browser frontend: S3 maps
scenarios to endpoints, S6 needs a primary screen, R3 needs screenshots and
walkthroughs, and the gate is built from a Python environment. So this version
offers exactly one stack, and the card shows every other option disabled with
"later":

| Field | Selectable now |
| --- | --- |
| Kind | `webapp` |
| Backend | Python FastAPI |
| Frontend | Vite and React |
| Python environment | `uv` `.venv`, stdlib `venv`, conda |
| Everything else in the schema | Every option listed |

A new kind or stack becomes selectable only together with its own stage
branches, gate and review deliverables, in a later plan.

### Validity rules

Checked by the server before the card can be confirmed; each shows as a line
on the card, not as a refusal.

| Rule | Why |
| --- | --- |
| Stdlib `venv` with "OS later" planned warns and suggests `uv` | The venv interpreter path differs between Windows and macOS, so a committed `gate_cmd` cannot name it on both |
| Conda needs an environment name | The README carries `conda activate <name>`, which is also what `connect.conda_python` reads |
| Public needs a license | The overview's choice |
| Private forbids a CI workflow file | The owner's rule: private means no hosted CI |
| Budget must be filled | The run's ledger and `Budget` are built from it |

### The card

One card, every field visible, prefilled from the kind's profile. Changed
fields carry the model's one-line `why`. Confirming the card is also the
approval for the two outward actions bootstrap takes, creating the GitHub
repository and pushing `main`, so the card states both in a line above the
confirm button with the repository's owner and visibility.

## Bootstrap

A server routine, no model. Every sub-step is one effect, and it first checks
whether its effect already exists with the identity the run fixed; only a
missing effect is performed, and the sub-step is recorded done before the next
starts. A resume therefore repeats nothing, also when the server stopped
after an effect and before its record.

| # | Sub-step | Effect already there when | Then |
| --- | --- | --- | --- |
| 1 | Re-check the name | — | The reservation checks run again; a conflict that appeared meanwhile stops with `folder` |
| 2 | Folder | The fixed path exists and holds only files this run wrote | Create it, or re-check the picked one is still empty |
| 3 | Files | Each file exists with the content the run would write | `.gitignore` from the profile's languages; `.gitattributes` with `* text=auto eol=lf` when another OS is planned; a README stub with the restatement, the owner's sentence and the setup command; `LICENSE` when public; `.wiki/adapter.toml` from the profile through `connect.adapter_text` |
| 4 | Base commit | `main` exists and its one commit holds exactly those files | Initialise with `main` and commit |
| 5 | Register | The folder is listed by `channels.projects` under the fixed name | Register a picked folder as `[폴더 찾기]` does, with its same-name refusal; then write the adapter's hash through `connect.keep` |
| 6 | Remote repository | The run recorded the repository's node id | Create it through `gh` with the chosen visibility and owner, and record its node id at once |
| 7 | Origin | `origin` points at that repository | Add it |
| 8 | Push | The remote `main` is the base commit | Push `main` with upstream |
| 9 | Spec and worktree | A spec with the fixed id whose `oneshot.run` is this run; a linked worktree on `oneshot-initial` whose HEAD is the base commit | Create whichever is missing (`workspace.create(..., linked=True)`), then write the commit point |

Two cases are not adopted silently:

- At sub-step 6, a repository with the fixed name exists but its node id is
  not recorded (the server stopped between creation and record, or someone
  else made it). The run stops with `github` and shows the repository; the
  owner either adopts it, allowed only when it has no commits, or picks
  another name.
- At sub-step 9, a spec or branch with the fixed id that does not belong to
  this run stops with `folder`; it is never overwritten.

`workspace.create(..., linked=True)` branches from the original checkout's
current HEAD and ignores `base`, so sub-step 9 first checks that the original
is on `main` at the base commit, on a fresh run and on a resume alike, and
stops with `folder` otherwise.

`connect.keep` moved after registration on purpose: the connection record is
keyed by folder name, and a record written before the same-name check would
already have changed another repository's record.

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
  blocks refused outside the schema; every validity rule; disabled kinds and
  stacks refused by the server, not only by the card; bootstrap on a
  temporary folder with `gh` stubbed, interrupted after each sub-step's effect
  and before its record, and resumed without repeating an effect; an
  unrecorded same-name GitHub repository stopping instead of being adopted; a
  spec created and the server stopped before the commit point, resumed into
  one spec and one worktree; a same-name folder or connection record refused
  with the existing `raw/connect/<name>.json` byte-identical and no `gh`
  call; a non-empty picked folder refused; the original checkout still on
  `main` and unchanged after sub-step 9; the `gate_cmd` for each environment;
  the ledger refusing a turn that would pass the ceiling, charging a USD
  role's first turn at its estimate, counting an unreported cost as unknown,
  metering Codex-only roles by hours without inventing USD across
  several turns, and restoring every total after a restart, including a
  ledger of estimates only; a
  restart turning each running state into `stopped` with `restart`.
- The repository's gate, `tool/lint.py --check` and `tool/debt.py check`.
- One real run on this machine with a throwaway private repository, recorded
  in the pull request: the card, the created repository, and the spec's
  worktree.

## Decided with the owner

Nothing yet for this step.
