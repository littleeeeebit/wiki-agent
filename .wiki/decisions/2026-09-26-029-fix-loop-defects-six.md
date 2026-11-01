---
scope: project
severity: preference
triggers: []
domain: ''
title: "fix: loop defect six — reduced specification, PR language, wiki language, review button, deleted worktree"
pr: 29
merged: 2026-09-26
branch: "fix/loop-defects-six"
---

# fix: loop defect six — reduced specification, PR language, wiki language, review button, deleted worktree

What. Fix six loop defects found while using it directly. The specification secretly reduced the request, causing half-finished work to go to review, PRs being uploaded in English, wiki documents being written in Korean (both existing and new ones), reviews not being possible even after work was finished, and deleted worktrees remaining in the list.

Why. Half-finished work goes to review pending (#27) — The specification author reduced the request to 1 line of 7 steps, claiming "a person cannot do the work in the window." I added a rule to `next-task.md` to include the entire request in the specification: do not put parts of the request in `out`, and leave parts that the work session cannot finish alone in `done` so the session stops and asks before reporting completion. I also removed "You decide what to exclude" from the [New Task] template (Discarded: server machine-checking the specification scope — there is no standard to distinguish the scope of the request and the specification). PR is in English — Since the agent reads the specification, keep it in English, and translate it to Korean only at the final stage that goes out to GitHub (`opened`, P2 comment). Do not translate commands and their output (evidence). …

Source. PR #29 · `fix/loop-defects-six`
