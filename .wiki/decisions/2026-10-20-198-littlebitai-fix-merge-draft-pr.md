---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fix draft PRs to be merged via the merge button"
pr: 198
merged: 2026-10-20
branch: "LittleBitAI/fix-merge-draft-pr"
---

# Fix draft PRs to be merged via the merge button

What. Check if the PR is a draft right before merging; if it is a draft, change it to ready status with `gh pr ready` and then merge. The merge button pressed by a human is a request to release the draft. Since both published PRs and PRs attached via the Review Loop pass through this single point, I fixed it only in the merge path.

Why. ai-coding-wiki #153 was approved for merge in the review, but it stopped like this when the merge button was pressed (P0). `요청한 머지 중단 — 409: 머지하지 못했다 — GraphQL: Pull Request is still a draft (mergePullRequest)` - The task agent (Codex) opened the PR directly as `gh pr create --draft` during implementation. The task instruction (`work-spec.md`) allows the task agent to open a PR. The server's publishing stage (`pull_request`) reused the already opened PR to update only the title and body, leaving the draft status as is. …

Source. PR #198 · `LittleBitAI/fix-merge-draft-pr`
