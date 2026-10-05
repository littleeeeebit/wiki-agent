# Stage 4 — module restructure mode and task blocking

L0–L2 work on a module the person chooses, behind a short plan.

## Work

- Audit the chosen module in depth (read-only session): callers, state it
  owns, seams to split along. Output a one-to-three step plan whose first step
  is the characterization-test PR.
- L2 steps may move code across files and must keep `tool/lint.py` boundaries
  (or the target's own lint) passing; a new boundary is registered there.
- Blocking: starting an L2–L3 step is refused while the repository has another
  open task; while it runs, new tasks in that repository are refused with the
  refactor named as the reason. Ending, cancelling or stopping the step
  releases the block; a restart restores it from the spec.
- Every L2–L3 step waits for the person's approval before the next starts.

## Tests

Start refused with an open task; new task refused during the step and allowed
after cancel; restart keeps the block; the approval gate holds the next step.

## As built

- Mode `restructure` in `tool/main/refactor.py` needs the module's files. Its
  `audit` phase is a read-only turn that ends in a `refactor-plan` block of one
  to three steps. Each step has a tier within L0–L2, a goal and its files. The
  characterization-test PR still comes first, as in cleanup.
- The hold is `refactor.block` on the L2–L3 step's spec.
  `specs.checkout_idle`, which every task start passes through, refuses while
  another run's step holds the repository. Taking the hold refuses when any
  other task is open, meaning any state but `정리됨` and `머지됨`. Start makes
  the same check early.
- A stop or cancel releases the hold. A shutdown and `recover` keep it, and
  재개 takes it again.
- L2–L3 PRs are not sent to Review Loop. The person starts the review. Once it
  allows the PR, the step waits in `awaiting` until
  `POST /api/refactors/{id}/approve`.

Tests: `test_restructure_holds_the_repository_until_the_person_approves` in
`tool/test_refactor.py`.

## Rollback

Disable the mode; the block is derived from spec state and disappears with it.
