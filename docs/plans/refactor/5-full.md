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

## Rollback

Disable the mode; plan documents and PRs already written remain.
