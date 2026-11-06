---
scope: project
severity: preference
triggers: ["할 (게|거|일) 없", "유지보수", "loop\\.py", "테스트.*느"]
reads: [docs/plans/reliability/2-tests.md, tool/main/loop.py]
slots: []
sources: []
links: []
---

# A loop round is paid in Git and `gh` processes — where to cut them

Maintenance backlog, not urgent. Pick it up when nothing else is waiting.

## What was measured

PR 2 of the reliability plan set 72 s as the target for `tool/test_loop.py`
plus `tool/test_specs.py`, and ended at 102.74 s (`2-tests.md`, Comparison).
The tests are not what is left. Their setup went from 20.1 s to 4.8 s, and the
rest is processes the product starts inside the loop thread. A `cProfile` of
`test_four_loops_with_three_seats_leave_the_fourth_waiting` counted 178
process starts at about 100–150 ms each on Windows. A real round pays the same,
plus the network for every `gh` and `fetch`.

## Candidates, in the order to try

| Where | What it does now | Cheaper shape |
| --- | --- | --- |
| `loop.step` → `worktrees(repo)` | Only checks that the spec's worktree is listed. `worktrees()` also runs `git status --porcelain`, `merge-base` and three `ls-tree` per worktree for the screen's `dirty` and `merged` columns: 2 + 5n processes every round, n the worktrees of the project | A listing-only helper over `git worktree list --porcelain`; the screen keeps the full rows |
| `loop.step` → `pr_head` | `gh pr view` before `shipped` and again after it | Ask again only when `shipped` pushed or fast-forwarded |
| `loop.shortstat` | `git fetch` of the base branch for every instruction, then `diff --shortstat` | Measure first; skip the fetch when `origin/<base>` already holds the base `pr_head` answered with |

## How to do it without losing the witness

Keep `test_loop` on real Git and the `Hub` stand-in. The integration is what
those tests prove, so a mocked `git` that makes them fast proves nothing about
the loop. Profile one test before and after, as above, and compare the
loop/spec file time against the 102.74 s in `2-tests.md`. `worktrees()` also
feeds `work.ours` and `/api/worktrees`; changing it, rather than adding a
helper, runs `test_main` and `test_worktrees` too.
