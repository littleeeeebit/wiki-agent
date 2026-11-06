---
scope: project
severity: preference
triggers: []
domain: ''
title: "Review loop also accepts PR branches held by other worktrees"
pr: 188
merged: 2026-10-19
branch: "fix/adopt-held-branch"
---

# Review loop also accepts PR branches held by other worktrees

What. If a PR branch is checked out by another worktree (e.g., Orca workspace), and that worktree is clean and at the same commit as the PR head, have it drop the branch to a detached state at the same commit, and the review loop creates its own worktree as usual.

Why. rfg_rag #75: After the #74 review loop finished, pressing the #75 review loop failed to even start due to `fatal: 'retrieval-port/usage-effort' is already checked out at 'C:/Users/dasdk/orca/workspaces/rfg_rag/barracuda'`. The Orca workspace that submitted the PR was still holding the branch. The previous fix only reused the selected main checkout if it held the branch. User decision: Release that branch. If files remain the same, and there are uncommitted changes or it is a different commit, do not touch it and stop by noting that folder in the evidence.

Source. PR #188 · `fix/adopt-held-branch`
