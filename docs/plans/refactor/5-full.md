# Stage 5 — full refactor mode

A whole-repository audit becomes a `docs/plans/<slug>/` series, and each of
its stages becomes a refactor step.

## Work

- The audit reads the scan, the architecture documents and `.omm/` when
  present, and proposes target structure and stage order.
- The planner (`tool/main/planning.py`) receives the audit as context and a
  refactor goal; its validator additionally requires each stage to name its
  tier and, for L3, a migration and rollback section.
- After the plan PR merges, stages run in order through the stage 2 profile
  with stage 4's blocking and approval gates.

## Tests

A fake audit produces a two-stage plan through the planner; an L3 stage
without migration fails validation; stages start only after plan merge.

## As built

- Mode `full` measures the top 20 hotspots, then runs a read-only audit turn
  that answers in prose. The audit and `PLAN_RULES` become the planner's
  context. `planning.start` is called with `refactor: true` and the request
  key `refactor-<run>-plan`, so a resume finds the same plan. The planner's
  limits are what is left of the run's.
- `planning.tiered` checks a stage file's `Tier: L0–L3` and `Files:` lines,
  and requires `## Migration` for L3. The validator's `problems` uses it for
  refactor plans, so its single repair turn can fix a stage missing them.
- The run waits in phase `plan` until the plan spec is `머지됨` with its
  cleanup done. It then reads each stage file at the merge commit and turns it
  into a step, and charges the planner's spend to the run. A stopped plan
  stops the run. From there the characterization PR and the steps run as in
  stages 3 and 4, with stage 4's hold and approval for L2–L3.

Tests: `test_full_plans_first_and_steps_only_after_the_plan_merges` and
`test_an_l3_stage_must_say_how_it_migrates` in `tool/test_refactor.py`.

## Rollback

Disable the mode; plan documents and PRs already written remain.
