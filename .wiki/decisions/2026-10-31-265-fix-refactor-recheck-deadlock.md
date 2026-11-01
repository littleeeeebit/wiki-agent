---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring re-verification is not blocked by test modifications, fixed test approval cleans up build artifacts on its own, and progress and the selected model are maintained."
pr: 265
merged: 2026-10-31
branch: "fix/refactor-recheck-deadlock"
---

# Refactoring re-verification is not blocked by test modifications, fixed test approval cleans up build artifacts on its own, and progress and the selected model are maintained.

What. The investigation worktree disappears from the rails. `refactor-survey-<id>` The read-only worktree appeared in "Waiting to start" as a "No specification" task, so that window looked like "Claude Default" and sending instructions allowed it to run with the default model. Remove it from the worktree list (`tool/workspace/worktrees.py`). Re-verification itself originally ran with the card's investigation model (`codex:gpt-6-astra`). …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #265 · `fix/refactor-recheck-deadlock`
