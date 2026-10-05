# Stage 3 — quick cleanup mode and the 리펙터링 tab

The first mode end to end: scan, pick L0–L1 steps, run the profile, open PRs.

## Work

- `tool/main/refactor.py` (main layer, under the 800-line cap): `POST
  /api/refactors` with mode, scope (`project` or `hub`) and limits;
  `GET /api/refactors/{id}`; cancel and resume like `/api/plans`.
- Quick cleanup takes the top hotspots from `debt.scan`, asks the model for
  L0–L1 steps only (dead code, renames, helper extraction, duplicate merge
  inside a module; public signatures unchanged), and runs each through the
  stage 2 profile into its own spec and PR.
- The run state is an additive `refactor` field on an ordinary spec, as
  planning does; the spec stays the only writer of task state.
- After review passes, the next L0–L1 step starts on top of the previous
  branch. Whether the refactor request itself authorizes Review Loop for its
  steps, or the person still starts each loop, is confirmed with the owner
  before this stage is implemented — today a person starts every loop.
- The tab: hotspot table, mode picker with default budgets, step progress,
  ratchet entries. Steps also appear in the task rail and Suite.

## Tests

Fake host: a scan with two hotspots yields two L0–L1 steps, each a spec and a
PR in order; an L2 proposal is refused in this mode; cancel keeps finished
PRs; restart does not republish.

## Rollback

Hide the tab and route; specs already created stay ordinary tasks.
