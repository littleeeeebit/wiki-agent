# PR 2 — reduce test and gate burden

Reduce runtime, maintenance duplication, and repeated full-suite runs while
retaining meaningful behavior coverage.

## Work

Measure collection, fixtures and calls separately; record Python, pytest, plugins,
hardware and warm/cold conditions. Compare plugin autoload on/off with required
plugins explicitly preserved. Run the full timing baseline once at this stage,
not during every repair. The audit's 93-test sample took 125.55 s.

Classify expensive tests by contract, real Git/process integration, waiting, and
duplicate setup. Retain real integration witnesses for process and Git behavior.
Parameterize equivalent cases only when it reduces duplication; remove redundant
tests with a mapping to the surviving behavior check. Do not replace pytest or
mock away integration boundaries solely to make the count smaller.

Update the loop gate contract: affected checks run during repair; the final full
gate runs after review allows the exact commit, before merge becomes available.
Store targeted and full results separately. New commits invalidate the final
result. Shared-file changes include every consumer; uncertain impact or unexplained
failures widen checks. A failed final gate returns to repair and review.

Document the intentional change from the original loop's per-round repository
gate. Keep review approval and test approval bound to the same HEAD.

## Evidence and exit

Compare like-for-like runtime and maintenance diff, with retained contract coverage.
A focused loop check must reject merge when the final gate is missing, failed,
cancelled or attached to a different commit. Do not choose an arbitrary test-count
reduction target. Set the timing target after profiling and before refactoring.

## Scope and rollback

Keep pytest and current dependencies. Parallel test execution is a later option
only after shared state is isolated. Roll back gate scheduling independently from
safe test deduplication.

## Implementation blueprint

The performance change and gate behavior are separate measurements in one PR.
Keep the required full command in adapter `slots.gate_cmd`.

### Gate contract

Add a proposed `validation` object to specifications; `specs` is its only writer:

```json
{
  "version": 1,
  "round": {
    "head": "<oid>", "base_oid": "<oid>", "commands": ["<registered command>"],
    "selection": "mapped", "ok": true, "finished_at": 0
  },
  "final": null
}
```

A final result uses the same identity plus `command`, `environment_digest`,
`ok`, `code`, `reason` and `finished_at`. The digest includes the actual
registered command, Python/runtime version and available dependency-lock/config
hashes. Store no credentials. Do not claim the digest captures every machine
property; environmental changes known to the application invalidate reuse.

Keep legacy `gate` as the latest check display during migration, but never use a
targeted result as proof of final validation. Old specifications without
`validation.final` need a new final gate before merge. Do not retroactively treat
their latest result as final.

### Selection and execution

Use adapter `[checks]` records already read by `decisions.registered`; add optional
`paths` glob lists. Example proposed adapter shape:

```toml
[checks.loop]
cmd = "python -m pytest -q tool/test_loop.py tool/test_specs.py"
about = "Review state, publication and merge invariants"
paths = ["tool/main/loop.py", "tool/main/specs.py", "tool/prompts/review-*.md"]

[checks.agent]
cmd = "python -m pytest -q tool/test_agent.py tool/test_main.py"
about = "Agent lifecycle and application integration"
paths = ["tool/agent/**", "tool/main/work.py"]
```

Compute changed paths from merge base to HEAD, including both rename names and
deletions. Union all matching checks. Each changed production path must be covered;
an unmapped path, malformed map, missing merge base, shared configuration change,
or unexplained failure selects the full gate. Registered commands remain trusted
adapter input; Jev does not invent them or remove a required consumer check.
Documentation-only changes use registered document/link/lint checks when mapped.

After a valid passing result, reuse it only for the same identity. Do not run it
again merely because a screen refreshed. Optional Jev-selected checks remain
additive and cannot replace required mapping checks.

### Integration points and state transitions

| Owner | Required change |
| --- | --- |
| `specs._check` | For a new PR, run selected round checks, not unconditional full gate |
| `specs.judge` | Capture HEAD, base and clean status before AND after running; reject a command that changes files or HEAD |
| `loop.shipped` | Reuse or run round checks for changed HEAD, then push |
| `loop.step`, allow branch | Persist review allow, run full final gate before state becomes mergeable |
| `loop.merge` | Require current successful final result matching allowed HEAD/base/config, plus existing GitHub atomic match |
| `specs.view/summary`, web API/types and Review | Expose targeted versus final pending/pass/fail; never show merge enabled while pending |
| `loop.recover` | Interrupted final gate becomes incomplete; resume revalidates identity before retry |

Use a `validation.phase = "final_running"` substate while the existing review
state remains non-mergeable. On success set mergeable. On failure send gate findings
through repair, invalidate old review/final records for eligibility, then review the
new commit. Cancellation stops the child and retains an incomplete result, never
an `ok` result. Keep existing human Merge action.

### Profiling and refactoring procedure

Capture `python -m pytest --collect-only -q tool` and one full
`python -m pytest -q tool --durations=25` baseline. Inspect setup durations as
well as call durations. Compare plugin loading using
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` in a child environment only. Preserve required
plugins explicitly; do not modify the user's global shell.

Start with `test_loop.py` and its real Git setup. Move only immutable expensive
setup to broader fixtures. Each test gets its own mutable worktree/spec storage.
Replace sleeps used solely for synchronization with observable events; keep actual
timeout behavior tests. Consolidate repeated cases only when their assertions
exercise the same contract. Leave a table of removed cases and surviving witnesses.

### Acceptance matrix

Test: targeted pass cannot merge; final fail cannot merge; final pass on HEAD A
cannot merge B; gate-mutated worktree fails; restart during final run stays blocked;
unknown path runs full; shared path selects all consumers; same unchanged identity
reuses results. Use existing `test_specs.py`/`test_loop.py` rather than a new
test framework. Compare full elapsed time after the last change. A reduction in
collected count alone is not success.

Rollback can restore full checks each round without relaxing final merge guards.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 1 | Profile and map redundant/expensive checks | Not started |
| 2 | Refactor measured hotspots and final-gate scheduling | Not started |
| 3 | Compare runtime and verify merge binding | Not started |

## Sources

[pytest duration profiling](https://docs.pytest.org/en/stable/how-to/usage.html)
and [plugin loading](https://pytest.org/en/stable/how-to/plugins.html).
