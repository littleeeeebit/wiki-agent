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

## Rollback

Disable the mode; the block is derived from spec state and disappears with it.
