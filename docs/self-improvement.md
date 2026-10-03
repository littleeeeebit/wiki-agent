# Self-improvement — two owners, measured candidate selection

Self-improvement has two independent scopes. The same deterministic runner
serves both; their evidence, budgets, hypotheses and adoption state never mix.

| Scope | Owner | What can improve | Adoption |
| --- | --- | --- | --- |
| `hub` | This Git repository | wiki-agent's harness, shared workflows and rules | A hub change enters the existing review and final gate; deployment remains explicit |
| `project` | One connected Git repository | Its code, harness, documentation and local workflows | A branch in that repository enters its existing review and gate |

A project result is not a shared rule. Promoting a lesson to the hub requires
a separate hub proposal, evidence of general applicability, and the admission
thresholds in [Schema](../SCHEMA.md). Project-private sources and slot values
are never automatically copied into `operator/`, `craft/`, or another project.

## What runs

`tool/improvement.py` owns a bounded experiment. `tool/improve.py` exposes it
as an explicit CLI; `GET /api/improvements` lists only the selected project's
status. Ordinary conversations, retrospectives and Stop hooks start no
experiment and spend no additional model calls.

1. Freeze the experiment contract, policy model, role commands, evaluator
   dependencies, task snapshots, domain guards and total allowance.
2. Evaluate the baseline in a detached checkout.
3. Propose independent candidate patches from the incumbent. Supply earlier
   hypotheses, measured results and owner-selected context to the proposer.
4. Check edit ownership, protected paths and declared leakage patterns. Run
   the independent critic before evaluating accepted patches.
5. Measure every expected evolve trial. Select the highest-scoring admissible
   candidate, retaining the incumbent if no candidate qualifies.
6. Repeat within the recorded round and time/call/token limits.
7. Reveal the separate held-out tasks once, compare the original baseline and
   selected incumbent, and prepare a local adoption branch only if guards and
   the held-out score floor pass. Review and the repository gate still follow.

Changes use UTF-8 without BOM. Candidate source edits must be tracked text
files; ignored local wiki pages may supply frozen context but are not silently
force-added to a publishable branch. A candidate may not edit its evaluator,
task snapshots, supervisor, or `.wiki/adapter.toml` gate contract.

The [RRSI method](https://github.com/google-research/rrsi) supplies the search
principles: annealed independent-edit budgets, history-aware proposals,
exploration of untried components during stalls, leakage screening,
noise-aware selection, cost constraints and removal experiments. This runner
uses the same score-floor and cost/novelty selection structure. Its stricter
usage handling rejects unknown costs rather than treating them as zero.

Each edit in a bundled candidate shares that candidate's measured result.
It does not establish the causal contribution of each edit. Prune directives
ask for a measured removal experiment; they do not delete machinery themselves.
Critic judgments and component declarations still require review.

## Configure an experiment

The owner supplies a JSON file with schema `wiki-improvement/1`. Required fields:

| Field | Contract |
| --- | --- |
| `model` | Fixed policy model and version passed to every evaluation |
| `rounds`, `candidates`, `trials` | Positive counts; trials are repetitions per task |
| `edits_min`, `edits_max` | Positive independent-edit bounds, minimum no greater than maximum |
| `stall_window`, `prune_window` | Positive windows for exploration and removal suggestions |
| `delta` | Measured or explicitly selected noise band, finite and nonnegative |
| `beta0`, `beta1` | Relative token-cost allowance for gains exceeding `delta` |
| `w_score`, `w_cost`, `w_novelty` | Finite nonnegative weights within the noise band |
| `limits` | Positive finite `seconds`, and positive integer `calls` and `tokens`, shared across the whole experiment |
| `caps` | `propose`, `critic`, `evaluate`: each maps to nonnegative integer `{calls, tokens}` upper bounds enforced by its trusted adapter; each bound must fit the total allowance |
| `tasks` | Disjoint nonempty `evolve` and `held_out` task-ID lists |
| `components` | Component names mapped to editable relative paths or directory prefixes ending in `/` |
| `guards` | Mandatory boolean guard names; missing or false guards fail |
| `commands` | `propose`, `critic`, `evaluate`: argument arrays, executed without a shell |
| `controller_files` | All evaluator/adapter dependencies, frozen fixtures and grading inputs |

Optional `protected` contains additional path prefixes. `leakage_patterns`
contains owner-selected regular expressions screened against candidate patches.
`context_files` names repository-relative sources, including ignored local wiki
pages, to snapshot for proposals; their combined text is bounded to 60,000
characters. Paths escaping the selected repository are refused.

Command executables, existing file arguments, the manifest and declared
dependencies are hashed. Relative command files and `controller_files` resolve
beside the manifest. Include transitive dependencies and immutable grading
inputs explicitly. The inherited environment is fingerprinted without recording
its values or secrets. Changing it or a pinned input requires a new experiment.
The evaluator must grade against these frozen inputs, not candidate-authored
expected answers.

The existing Jev comparison runner remains available. Its report intentionally
requires identical code/behavior manifests within one comparison. A domain
adapter for these experiments must evaluate each candidate separately and export
comparable per-task trial records under the shared frozen conditions; it must
not bypass the existing report's compatibility checks.

`tool/improvement_host.py --model <role-model> --effort <effort>` refuses both
native roles before opening a session, with zero calls and tokens. The current
native `ChatSession` hosts cannot enforce a total call/token ceiling. Their
ambient settings, hooks and persisted sessions therefore never enter an
experiment through this adapter. Native model-backed proposal and criticism
remain unsupported; this runner requires owner-supplied, isolated domain
adapters with real provider-level enforcement. It does not launch a new API
provider or infer a project's success criteria from its name.

`tool/improvement_evaluate.py --tasks <frozen-tasks.json>` supplies a task-command
evaluator for both scopes. Its task manifest uses schema `wiki-improvement-tasks/1`:

```json
{
  "schema": "wiki-improvement-tasks/1",
  "tasks": {
    "case-a": {"argv": ["python", "-m", "pytest", "C:/frozen/test_case_a.py"], "inference": false, "seconds": 30},
    "case-b": {"argv": ["python", "-m", "pytest", "C:/frozen/test_case_b.py"], "inference": false, "seconds": 30}
  },
  "guards": {"evolve": {"integrity": []}, "held_out": {"integrity": []}}
}
```

This is a shape example, not approved experiment thresholds or a ready dataset.
Declare the manifest, external test files and their dependencies in
`controller_files`. Include separate task IDs and fixtures for each split.
`inference: false` explicitly declares offline commands: exit zero scores one,
other exits score zero, and model cost is zero. An inference command instead
requires positive `max_usage: {calls, tokens}` bounds enforced by that command
and returns JSON with `reward` and known `usage: {calls, tokens}`. Before
each trial, the evaluator checks that the entire bound fits its unused allowance.
If not, it returns an error with known usage without starting that trial.
The task receives JSON on stdin with `stage: "task"`, `id`, `trial`,
`root`, `model`, `split`, owner fields and `limits`; those limits contain
its own call/token ceiling and bounded timeout. The pinned policy model also
reaches it through `WIKI_IMPROVEMENT_MODEL`. Guard values list the tasks
that must pass every repetition; an empty integrity guard relies on the
controller's unchanged-checkout check. Other mandatory guards must have real
task checks. Each command receives a separate cache per task and repetition.
`{root}` in command arguments substitutes the candidate checkout.

## Domain adapter protocol

Every command reads one JSON object on stdin and writes one JSON object to
stdout. Diagnostics go to stderr. Requests include `stage`, `scope`,
`repo_key`, candidate `root`, pinned policy `model`, and allocated `limits`.
The controller admits a whole operation only if its frozen `caps` fit the
persisted remaining budget; the request's call/token limits are those caps,
not the experiment's entire remainder. Zero ceilings are for genuinely
offline commands only; offline work can continue with no inference allowance.
For example, `caps.evaluate: {calls: 4, tokens: 4000}` covers one complete
evaluation, including every task repetition and any grading overhead, not one
trial. A generic evaluator's per-trial `max_usage` must fit within that allocation.
Every result includes `usage: {calls, tokens}` with known nonnegative integers.
The evaluator's total reported tokens must include all measured policy tokens
and any grading overhead. Unknown usage stops the experiment; restarting does
not grant a new allowance. Adapters must enforce allocated ceilings before
making inference: bound input plus output tokens, retries, tool/agent calls
and grading overhead, or refuse without inference. An output-token option alone
does not cap total tokens. Declaring a number is not enforcement: the owner must
verify the frozen adapter and provider actually implement the cap. A host lacking
that capability must not be used. Returned cap violations preserve actual usage
and stop the experiment without another operation or adoption; that check detects
a broken trusted adapter, it does not make an uncapped one safe.

Adapters must use
the supplied `WIKI_IMPROVEMENT_CACHE` root for their state. Namespace trial
memory and caches by split, task, repetition and candidate; never retain a task
solution for another trial. This directory is separate for every candidate
operation and repository.
All model/role inputs must be frozen or supplied in the request. Disable ambient
instructions, settings, hooks and persistent sessions; never fall back to an
ordinary interactive host. Include any explicit settings in the frozen inputs.

Proposal requests contain evolve results, history, context, component mappings
and directives. Their response has this form:

```json
{
  "edits": [{"component": "context_mgmt", "hypothesis": "Remove irrelevant context", "paths": ["agent/context.py"]}],
  "patch": "diff --git a/agent/context.py b/agent/context.py\n...",
  "usage": {"calls": 1, "tokens": 1200}
}
```

Critic requests contain the patch, declared edits and evolve task IDs. Return
`verdict: "accept" | "reject"`, `reasons: [...]`, and usage. Screen for task
specialization, memory leakage, rubric manipulation, missing safety replacements,
unbounded work and undeclared bundled mechanisms.

Evaluation requests contain `commit`, `split`, `ids` and `trials`. Return:

```json
{
  "trials": [{"id": "task-a", "trial": 0, "reward": 0.8, "tokens": 900}],
  "guards": {"integrity": true},
  "usage": {"calls": 1, "tokens": 900}
}
```

Rewards are in `[0, 1]`. Return one row per task and zero-based repetition;
duplicate or unknown rows are refused. Missing trials contribute zero to the
full score denominator and make the candidate inadmissible. Unknown token
counts cannot pass the cost rule. Mandatory guards are noncompensatory.

## Run and adopt

Use separate names and configurations for each owner. These commands perform
local experiments; only `handoff` creates a local branch, and none publishes,
merges, deploys hooks or changes the source checkout's current branch.

```powershell
python tool/improve.py --repo C:/path/to/wiki-agent --scope hub --name context-v1 init --config C:/experiments/hub.json
python tool/improve.py --repo C:/path/to/wiki-agent --scope hub --name context-v1 round
python tool/improve.py --repo C:/path/to/wiki-agent --scope hub --name context-v1 status
python tool/improve.py --repo C:/path/to/wiki-agent --scope hub --name context-v1 handoff

python tool/improve.py --repo C:/path/to/project --scope project --name project-v1 init --config C:/experiments/project.json
python tool/improve.py --repo C:/path/to/project --scope project --name project-v1 run
python tool/improve.py --repo C:/path/to/project --scope project --name project-v1 handoff
```

`run` resumes only between completed rounds. Process interruption or unknown
usage stops that experiment with its evidence intact; it is not redrawn with
a fresh budget. OS locks prevent concurrent operations. Held-out reveal ends
search whether it passes or fails. Further tuning requires new unseen tasks.
Handoff includes immutable commit and contract IDs plus the evidence location.
Open the branch's PR through the normal workflow and use the current host's
independent review loop and repository gate. An app-managed session uses its
Review Loop control; an Orca-hosted session reuses its supplied native review cell.
Keep private trial content out of public PR bodies.

Records live under `raw/improvement/<scope>/<git-identity>/<experiment>/` on
the host, outside the shared knowledge corpus. Git identity uses the canonical
common Git directory, so same-named repositories stay distinct and linked
checkouts retain the same owner. These are private operating records, not
adopted hub knowledge. Candidates remain in detached checkouts for inspection.
Document listing and retrieval exclude these private checkouts, even during
Git discovery failure. Explicit evaluation inside a candidate can still retrieve
that candidate's own documents.

Source checkouts and evaluator hashes provide experiment integrity checks;
they do not sandbox arbitrary domain commands at the OS level. Use trusted
adapters. Scope and cache fields are adapter contracts, not permission to read
other repositories. The [general recurrence recovery plan](plans/reliability/8-recovery.md)
remains a separate workflow; this implementation does not mark it complete.

## Verification

`python -m pytest -q tool/test_improvement.py tool/test_improvement_evaluate.py tool/test_improvement_sources.py` exercises both scopes with real
Git and subprocess adapters over synthetic repositories. It checks selection,
critic ordering, repository separation, immutable inputs, budgets, interruption,
held-out reveal, review handoff and source preservation without model calls.
Live model quality and project-specific generalization require separately
configured, budgeted experiments; synthetic tests do not establish them.
