---
scope: project
severity: preference
triggers: []
domain: ''
title: "Create the repository graph based on the repository, not the checkout"
pr: 177
merged: 2026-10-19
branch: "fix/repo-graph-canonical"
---

# Create the repository graph based on the repository, not the checkout

What. Merge preparation(`tool/main/maintenance.py:71`) recreates `.wiki/graph.json` in the PR worktree. Until now, the graph builder used the following two based on that worktree.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #177 · `fix/repo-graph-canonical`
