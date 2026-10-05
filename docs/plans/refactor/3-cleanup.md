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
  branch. The owner decided that the refactor request itself authorizes
  Review Loop for its characterization-test PR and its L0–L1 step PRs; L2–L3
  steps still wait for the person.
- The tab: hotspot table, mode picker with default budgets, step progress,
  ratchet entries. Steps also appear in the task rail and Suite.

## Tests

Fake host: a scan with two hotspots yields two L0–L1 steps, each a spec and a
PR in order; an L2 proposal is refused in this mode; cancel keeps finished
PRs; restart does not republish.

## As built

- The run is its own record under `raw/refactor/runs/<repo>/`, moved through
  the phases `scan` → `tests` → `steps`. Each PR is an ordinary spec carrying
  `refactor: {run}`, so the task rail, Suite and Review Loop treat it as any
  task. There is no `GET /api/refactors/{id}`; the list route carries every
  run.
- Quick cleanup turns each of the top `top` hotspots into one L1 step with a
  goal built from its measurements. It does not ask the model to plan, so no
  L2 proposal can arise in this mode.
- The characterization tests come first, on their own branch and PR against
  the original base. The host may only add or edit test files, which must
  pass before they are committed. Each step branch stacks on the previous
  one, and its PR targets that branch.
- Every model turn and every runner call is charged to the run's limits.
  Unknown usage stops the run. A server restart stops a running run with
  `restart`, and 재개 continues from the recorded phase without reopening a
  PR that exists.
- The ratchet holds a raised `web/src/App.tsx` entry for the tab's two lines.
  Splitting that file is work for a refactor run, not for this feature.

Tests: `tool/test_refactor.py` drives a full cleanup with a fake host, fake
runner and fake GitHub, plus the restart and code-edit refusal paths.
`tool/test_refactor_profile.py` drives the real runner.

## Rollback

Hide the tab and route; specs already created stay ordinary tasks.
