# Refactor — paying down vibe-coding debt with measured, behaviour-preserving steps

Six stacked PRs add a refactoring workflow: find the debt, freeze today's
behaviour, let candidate patches compete under that freeze, and stop the debt
from growing back. Stage 1 lands with this overview.

## Problem

Fast agent-written changes left the core too large to review in one sitting.
At adoption `tool/main/knowledge.py` was 3,800 lines, `loop.py` 2,560,
`specs.py` 1,435, `planning.py` 1,321 and `work.py` 1,057; 60 files sat over
the caps or held duplicated blocks. Nothing stopped a change from making them
larger, and connected repositories have the same pattern with less test cover.

## Constraints

- Behaviour is preserved. Structure and behaviour never change in one step.
- Existing ownership stays: specs own task state, the review loop owns review,
  the final gate and a person own merge.
- Metrics are language-agnostic; a connected repository may be Python,
  TypeScript, Swift or a mix, and installs nothing.
- Hub and project scopes stay separate, as in [self-improvement](../../self-improvement.md).

## Decisions

Chosen by the owner on 2026-10-05 from offered options.

| Question | Decision |
| --- | --- |
| Scope | Hub and connected projects, separated like self-improvement |
| Form | A dedicated `[리펙터링]` workflow; `tool/improvement.py` selects among candidates |
| Proposer | A native write session inside the runner, for the refactor profile only; its token ceiling is soft (kept between turns, crossing recorded) |
| Modes | Quick cleanup (metric scan, L0–L1, no plan), module restructure (audit of a chosen module, L0–L2, short plan), full (whole-repo audit, L0–L3, plan series) |
| Risk tiers | L0 mechanical, L1 inside a module, L2 across module boundaries, L3 contract change |
| Detection | Lines, duplicated blocks, longest block and git churn (`tool/debt.py`) |
| Preservation | Characterization tests are written first, must pass on today's code, then frozen |
| Selection | Preservation is mandatory; among survivors the largest metric improvement wins |
| Candidates | L0 one, L1 two, L2–L3 three |
| Failure | One retry with the failing tests as feedback, then stop and propose a smaller step |
| Continuation | L0–L1 continue after review passes; L2–L3 wait for approval; merge is always a person's |
| Concurrency | L2–L3 refuse to start beside an open task and block new tasks in that repository while running |
| Budget | Per-mode defaults shown before launch and editable |
| Recurrence | A per-file ratchet with an 800-line cap for new files, checked by every final gate |
| Screen | A new 리펙터링 tab |
| Hub | Runs in a worktree only; the server restarts by hand after merge |

## Stages

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | [Scanner and ratchet](1-scanner.md) | `tool/debt.py`, final-gate check, merge-time tightening, hub baseline | Done |
| 2 | [Runner profile](2-runner.md) | Characterization freeze and metric scoring in `improvement.py` | Done — `tool/refactor_profile.py`, native proposer with a soft ceiling |
| 3 | [Quick cleanup](3-cleanup.md) | First mode end to end and a minimal 리펙터링 tab | Done — `tool/main/refactor.py`, `web/src/components/Refactor.tsx` |
| 4 | [Module restructure](4-restructure.md) | Module audit, short plan, task blocking | Done — `refactor.block` on the step's spec, approve route |
| 5 | [Full refactor](5-full.md) | Whole-repo audit feeding the planner's series | Not started |
| 6 | [Hub and first use](6-hub.md) | Hub scope and a real split of `knowledge.py` | Not started |

Each stage is a PR stacked on the previous stage's branch and merged in order
by a person. Review rounds: 1 alone; 2 and 3 together, because the profile has
no caller without the mode; then 4, 5 and 6 each alone.

## Sources

Background the decisions lean on, recalled rather than re-fetched in this session:

- Michael Feathers, *Working Effectively with Legacy Code* — characterization tests pin current behaviour before a change.
- Adam Tornhill, *Your Code as a Crime Scene* — hotspots are where size meets change frequency.
- The ratchet pattern used by tools such as Betterer — a metric may improve and may never regress.
