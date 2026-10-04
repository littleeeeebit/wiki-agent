---
name: review-loop
description: >-
  Run an independent review through wiki-agent's Review Loop control, which
  owns PR association, reviewer dispatch and persisted results. Other hosts
  use only an explicitly supplied native review cell.
---

# One round of the review loop

This skill exists for the ordering. When arming the watch and sending are two
separate steps, the order gets it wrong. Here they are one procedure, so there
is nowhere to get it wrong.

The rule behind it is `operator/codex-review-loop`. That page carries this
repository's values — the review folder (`review_dir`, `artifacts/review/` in
most repositories), the offline gate and the live run. A skill is the same
file in every repository, so those values are read off the injected page, not
written here.

## In an app-managed wiki-agent session

Use the application's Review Loop control for the selected pull request.
The server attaches a manually published PR to its existing task by verified
branch identity, preserves the specification and writes review instructions.
It dispatches an independent read-only reviewer, persists each round's result,
and returns valid serious findings to the implementation session.

Start the control once for a PR. The server continues review and repair rounds
after verified completion and publication, and resumes active loops after a
server restart. Do not tell the person to click it for every round or save that
advice as a standing instruction. A stopped task has a persisted reason; read
that reason instead of describing a stop as normal next-round waiting. Explicit
user stops and genuine blockers require resolution before continuing.

The implementation agent reports completion through `done-report` and records
requirements changes through `spec-update`. It never edits `raw/specs` to force
a state, discovers external terminals, or starts a second review workflow.
The server owns metadata and dispatch; unavailable desktop CLIs are irrelevant.

A round names its number, PR and exact head. Findings carry location, severity,
reason and runnable evidence. P0 is a crash, corruption or lost flow; serious
P1 changes requested behavior; P2 is a nonblocking suggestion. The result ends
with `머지 허용` or `머지 불가 — <reason>`. An agent's own checks are not an
independent review. Repair rounds run scoped checks, and the final gate runs
on the head the reviewer allowed before a requested merge.

## In another host

Only use a manual review cell when that host explicitly supplies one. Use its
native tools to send one instruction file, arm the result watch before dispatch,
and read the result file after it arrives. Never install or invoke another
desktop application's transport as a fallback. If no reviewer is available,
report that specific limitation while continuing the authorized implementation.

The repository directory does not identify the runtime. When maintaining
wiki-agent from another host, reuse the review cell the person supplied there.
An optional current-host transport is not a dependency of the managed app.
