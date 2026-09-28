# PR 10 — close the live acceptance gaps

Verify the completed experience and update original plans with current evidence.

## Work

Run the original loop's two full desktop cycles: a new connected repository with
investigation on, and a repository with investigation off. Verify real SessionStart
events for both hosts separately from installation checks and direct hook calls.

Exercise an English question, graph evidence/health, explicit planning with source
records, a document-review correction, implementation using wiki context, a code
review, a forced recurrence research cycle, and final gate/merge eligibility.
Verify a Korean paired scenario only against the PR 9 baseline.

Check refresh/reconnect, cancel, provider failure, budget exhaustion and server
restart at meaningful boundaries. Confirm model roles, runtime ownership and the
absence of flashing windows in cold and warm desktop questions. Observe cleanup
of processes and worktrees the run owns.

Evidence must identify commit, settings, model/policy versions, host and graph
generation. Record screenshots or event artifacts where appropriate without
credentials. Reuse already valid evidence instead of rerunning unrelated live
scenarios. Distinguish current measured results from historical plan records.

## Evidence and exit

Finish the remaining loop steps 4–7 and Jev stage 10 only when their actual criteria
pass. If a host or interaction cannot be verified, leave the corresponding status
open with the exact blocking condition. Do not mark the whole original plan done
because this new roadmap has reached its last row.

Publish reproduction commands, cost/latency/test comparisons, coverage limitations,
and rollback instructions. The final reviewed commit has the full gate required by
PR 2. No autonomous merge; preserve the existing explicit Merge action.

## Scope and rollback

This is acceptance and only necessary integration fixes, not a final batch of
unrelated features. Return a material failure to its owning stage and rerun only
invalidated evidence. Preserve the previous known-good settings and artifacts.

## Implementation blueprint

### Evidence record

Create `raw/verification/reliability/<run-id>/` with a manifest and per-scenario
records. Proposed record fields:
`{case_id, commit, repo_id, host, model_roles, settings_hash, behavior_manifest,
graph_generation, started_at, result, expected, observed, artifact_paths, limits}`.
Redact credentials and private query content from export; local artifacts retain
only what diagnosis requires. No screenshot or synthetic result substitutes for
an actual host event.

The committed acceptance report links reproducible commands and non-sensitive
summaries. Large traces/screenshots remain raw artifacts with hashes and locators.

### Scenario matrix

| ID | Setup and action | Required observation |
| --- | --- | --- |
| L01 | Cold desktop start; first and warm English question | No extra console, correct repository, visible current Jev mode |
| L02 | Connect new fixture repo with investigation on | Adapter is scoped, both real host starts observed, investigation remains in owned worktree |
| L03 | Connect another fixture repo with investigation off | Same subsequent sync/harvest path; no forced investigation |
| L04 | Ask a bridge-evidence question | Trace shows selected retrieval/expansion, citations resolve, graph snapshot identified |
| L05 | Start Plan with three roles and limits | New folder only, source fragments and full stages, one PR |
| L06 | Reviewer rejects a missing plan acceptance criterion | Reviser fixes document; planner does not take over review |
| L07 | Implement a fixture task requiring a prior decision | Jev retrieves the relevant wiki record before implementation |
| L08 | Feed repeated P1/oscillation to review | Research precedes code edit; source/theory page shares fix PR |
| L09 | Refuse both researched fixes for one issue | Stops after cycle 2 with alternatives; no cycle 3 |
| L10 | Allow review then run final gate; push another commit | Old gate/approval cannot merge new HEAD |
| L11 | Korean paired question and Korean evidence | Outcome matches the PR 9 report; original citations intact |
| L12 | Refresh, cancel, restart at phase boundaries | No duplicate dispatch/PR, no lost sources, explicit resume |
| L13 | Provider outage, no search capability, exhausted limit | Correct distinct stop/fallback; no false completion |
| L14 | Explicit merge and cleanup on disposable task | Review/gate binding honored; owned processes/worktrees cleaned; unrelated work intact |

Use disposable repositories and prearranged fixture findings where destructive
behavior is exercised. Synthetic recurrence verifies the trigger path; report it
as synthetic and separately record any naturally occurring issue. Do not manufacture
a real production defect to make the scenario happen.

### Test and run order

1. Verify installed prerequisites and current settings without altering global
   hooks silently. Read connection checks and distinguish wiring from real events.
2. Run L01–L04 to establish the window, hosts and evidence path.
3. Run L05–L10 in the same disposable task where practical, retaining head identities.
4. Run L11 with frozen paired inputs; do not tune while recording acceptance.
5. Run L12–L14 and collect cleanup/rollback evidence.
6. Fix only observed integration defects; invalidate and rerun affected cases.
7. Run the final full gate for the reviewed commit, plus web build when frontend
   changed. Record exact commands from the current package/adapter, not guessed scripts.

Use `python tool/lint.py --check` for wiki validation. For frontend and host
commands, read current `web/package.json` and setup instructions at implementation
time. Required command absence is a failed prerequisite, not a skipped pass.

### Original-plan reconciliation

| Original record | Evidence needed before changing status |
| --- | --- |
| loop/4-review | Review, cap, concurrency, receiving existing PR, Merge and stale HEAD scenarios |
| loop/5-connect | Both real host events, migration/connection and investigation on/off |
| loop/6-screen | Real Tauri layout and interaction, not just server/API tests |
| loop/7-verify | Both complete repository cycles |
| jev/10-evaluation-rollout | Current English evaluation, product parity/window checks, rollback evidence |
| Archived English-first/wiki-agent | Add current supersession/verification references only where misleading; do not overwrite historical test counts |

L01–L14 are the minimum new-path cases, not substitutes for any additional exit
criteria in those source plans. Maintain a criterion-to-case ledger; an uncovered
criterion stays open. Final report separates passed, failed, blocked and not run.
Rollback uses existing settings and prior policy/index generation where applicable;
never deletes source documents or failed evidence.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 1 | Run desktop and real host acceptance matrix | Not started |
| 2 | Fix integration failures and verify affected paths | Not started |
| 3 | Reconcile original plan statuses with evidence | Not started |
