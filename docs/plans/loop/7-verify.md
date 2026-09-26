# Step 7 — Verification

The relationship between the overall design and the steps is in [Overview](0-overview.md)].

Goal. The one-line purpose of the overview stands twice in the window. Once for a new repository with investigation turned on, and once for a repository with investigation turned off.
What is caught is fixed in this step, and for each fix, record what was caught.

## Agreements with the User

2026-09-25.

| What | Agreement |
| --- | --- |
| Side with investigation on | One small actual repository. When starting, measure the sizes of `fresh-labeler` and `ml-interactive-lab` and choose the smaller one |
| Side with investigation off | An already connected repository. `ai-generation` or `ai-nara-shop`. Also check here if "automatically follows" is correct after hub migration |
| Host | Default for both wheels. Task Claude, Review Codex |

## Two Wheels

### Wheel A — New repository with investigation on

1. Turn on investigation in settings and set limits to default
2. [Connect] in the project list. If the hub migration confirmation window has already moved, it does not appear
3. Look at the adapter estimation slot. Empty slots appear in the row and the next task candidate material
4. Actual tests of both hosts pass
5. Estimate confirmation → Investigation session. Watch tokens and time accumulate per turn
6. `wiki-bootstrap` PR → Loop → [Merge] → Hand over. The original's adapter becomes a tracking file and `.git/wiki-connect/` becomes empty
7. Documents and module pages for that repository are visible on the map
8. [Propose candidate] in the next task focus. Empty slots appear as candidates. Run one more wheel with that specification

### Wheel B — Connected repository with investigation off

1. The status of that repository in the list is correct. If an old project hook remains, `일부`, after [Connect] `연결 완료`
2. [Propose candidate] → Ask back → Specification → [Start] in the next task focus
3. Refresh the window during the task. Re-attach to the running turn and approval card
4. Approve the same write only once with [During session]
5. `done-report` → Server gate → PR → Result row
6. The loop starts by itself. [Stop] and [Continue] during a round. Leave the window when waiting for approval to receive OS notifications
7. `머지 가능` → P2 comment draft → [Merge] → `MERGED` confirmation → Cleanup. If the original is clean, ff, otherwise "Original is behind".
   If that repository has a merge queue or mandatory checks, `머지 대기` appears first, and check if the worktree and branch remain the same during that time, and if it is cleaned up only after `MERGED`. If not, this step is immediately `MERGED` — record which one it was
8. A decision record is created after the next `sync` of that repository and the specification card changes to a link
9. If the specification came from a plan row, that row is `완료 — PR #n`
10. If there is a running turn when closing the app, a question appears

## Recording

Follow the format of `docs/plans/done/wiki-agent/7-verify.md`. One line per step — what was done, what was seen, and if something was caught, what was fixed. For what was caught, record the reproduction before fixing. The fixed commit goes into the PR of this step.

Measure for each wheel.

| Measured | Why |
| --- | --- |
| Number of turns and time to specification | Whether asking back gets longer |
| Number of rounds and discoveries per round | "Number of rounds should decrease" in overview |
| Number of human clicks (approval, [Continue], [Merge]) | Whether automation actually reduced manual effort |
| Investigation estimate vs actual | Match the step 5 estimate coefficient |

## What Not to Do

- Wheel with translation turned off. Step 7 of wiki-agent did it and this plan did not touch translation
- Codex task cell. The user decided to look only at default combinations. The actual Codex check in step 2 runs a write session once

## Confirmation

- Tables for both wheels are in this document
- `pytest tool/`, `python tool/lint.py --check`, `ruff check tool/`, `npm run build`
- All step tables in the overview are `완료`, and move this series to `docs/plans/done/loop/`

## Steps

| # | Step | What | Status |
| --- | --- | --- | --- |
| 1 | Selection | Two repositories, measure size | Not started |
| 2 | Wheel A | From connection with investigation on to second specification | Not started |
| 3 | Wheel B | One wheel of repository with investigation off | Not started |
| 4 | Fix | What was caught | Not started |
| 5 | Gate and cleanup | All confirmations, move to `done/` | Not started |