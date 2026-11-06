---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring 3/6: Quick Cleanup Mode and Refactoring Tab"
pr: 79
merged: 2026-10-05
branch: "refactor/3-cleanup"
---

# Refactoring 3/6: Quick Cleanup Mode and Refactoring Tab

What. This is step 3 of the refactoring series. We add Quick Cleanup Mode and the Refactoring Tab. Review this in conjunction with step 2.

Why. `tool/main/refactor.py`: Execution proceeds in the order of scan → property test PR → stacked step PRs, one for each hotspot. Each step's patch is selected by the step 2 runner. All PRs are standard specifications, so they are handled as-is by the task list, suite, and review loop. - As determined by the owner, the refactoring request approves the property test PR and the start of the review loop for L0–L1 step PRs. Merging is done by a human. - Both model turns and runner calls are charged against the execution limit. If usage is unknown, it stops. Restarting stops execution at `restart`, and [Resume] continues without reopening the PR. - Screen: Center tab `리펙터링` (top debt files, mode/limit, execution list, stop/resume). - `web/src/App. …

Source. PR #79 · `refactor/3-cleanup`
