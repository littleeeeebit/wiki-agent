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
- A read-only turn (the audit) gets `Read,Glob,Grep` only, so Claude has no
  shell and Codex runs source-only. It must leave HEAD and the working tree as
  it found them, or the run stops with `read_only_wrote`.
- The hold is `refactor.block` on the L2–L3 step's spec.
  `specs.checkout_idle`, which every task start passes through, refuses while
  another run's step holds the repository. A run checks it before its own fork
  as well as before every switch. Taking the hold refuses when any
  other task is open, meaning any state but `정리됨` and `머지됨`. Start makes
  the same check early.
- A stop or cancel releases the hold. A shutdown and `recover` keep it, and
  재개 takes it again. Cancel on a run with no worker, as after a restart,
  releases its holds directly; the tab offers it as 저장소 놓기. That lookup
  and release hold `_launching`, which `launch` takes too, so a resume never
  lands between them and loses its hold.
- L2–L3 PRs are not sent to Review Loop. The person starts the review. Once it
  allows the PR, the step waits in `awaiting` until
  `POST /api/refactors/{id}/approve`. Approval is refused unless the step's
  spec is in a reviewed state and its branch head is the head the counted
  review round allowed (`specs.approved`). It is bound to that revision and
  head: a revision or a new commit afterwards needs review and approval again
  before the next step starts. A step merged before approval, whose branch
  merge cleanup pruned, stands for its allowed head only when GitHub reports
  the merged PR's `headRefOid` as exactly that head; the next step stacks on
  that commit. Resume never remakes a published step's branch, including
  one whose PR the spec already holds while the run still says `adopted`;
  an L0–L1 step stopped before its review request is sent to review then, if
  its spec is still at `PR #n` with no rounds. The runner's handoff branch is
  deleted only after the `adopted` checkpoint, so a resume from `pending` can
  re-adopt.

Tests: `test_restructure_holds_the_repository_until_the_person_approves` in
`tool/test_refactor.py`.

## Rollback

Disable the mode; the block is derived from spec state and disappears with it.
