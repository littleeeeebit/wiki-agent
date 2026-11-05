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
| 1 | Selection | Two repositories, measure size | Done — 2026-11-05, below |
| 2 | Wheel A | From connection with investigation on to second specification | In progress — the first run stopped on a caught bug (below); restarts after its fix |
| 3 | Wheel B | One wheel of repository with investigation off | Not started |
| 4 | Fix | What was caught | Not started |
| 5 | Gate and cleanup | All confirmations, move to `done/` | Not started |

## Runs

### Selection (2026-11-05)

| Repository | Tracked files | Lines | State |
| --- | --- | --- | --- |
| fresh-labeler | 44 | 7,200 | No remote; nearly every tracked file shows as modified |
| ml-interactive-lab | 40 | 10,636 | Remote `LittleBitAI/ml-interactive-lab`; one edited file (`how_to.md`) |

fresh-labeler is smaller, but with no remote it cannot reach the pull
request, loop and merge steps, so Wheel A uses ml-interactive-lab (the
user's choice). Wheel B uses `wiki-agent-refactor-scratch` instead of
`ai-generation`, whose checkout is on a feature branch with uncommitted
work, or `ai-nara-shop` (the user's choice).

### Wheel A, first run (2026-11-05)

| Step | Seen |
| --- | --- |
| 1 | There is no survey switch in settings any more; the [Connect] confirmation asks every time. Settings keep only the limits (2,000,000 tokens, 90 minutes, Opus) |
| 2 | A registered but unconnected repository never shows in the project list, so the confirmation exists only right after [Find folder]; closed, it cannot be reopened. The folder was added and the confirmation answered through the same requests from the page (`/api/folders`, `/api/connect/<name>/plan`, `/api/connect/<name>`) |
| 3 | The guessed adapter left `gate_cmd`, `live_cmd` and `server_stop` empty; the row listed `gate_cmd 빈 칸` |
| 4 | Both hosts' tests passed; only `gate_cmd 빈 칸` stayed missing |
| 5 | Estimate 6 turns, 121,234 tokens, 658 seconds. Turn 1 used 38,797 tokens. Stopped during turn 2 for the catch below |

Caught. The survey switches the original checkout to `wiki-bootstrap`, and
`survey.settle` committed the whole tree after turn 1: the person's edited
`how_to.md` (945 lines) and the untracked `.claude/settings.json` that
[Connect] had just written went into `wiki: 조사 턴이 남긴 것`, then
`wiki: 조사 범위 밖의 변경을 되돌림` put `how_to.md` back to `main` and
deleted `.claude/`. Pushed with the pull request, the person's edit would
have been published; the scratch repository's earlier survey had lost its
`.claude/` the same way. Nothing was pushed: the run was stopped, the
checkout returned to `main`, the edit reapplied from a saved diff, and the
local branch kept as a bundle.

Fixed. `survey.start` refuses while anything but the adapter, the wiring
[Connect] wrote (`.claude/settings.json`, `.codex/hooks.json`, only while
their text is exactly what [Connect] makes of the committed one) and
untracked `.wiki/` outputs is uncommitted, and names it. The wiring is committed on the
survey branch right after the adapter (`wiki: [연결] 배선`), so it goes up
with the wiki instead of being swept, stashed by a cut turn or read as a
dirty checkout by the report's gate (PR #296 round 1).