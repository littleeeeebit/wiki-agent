---
scope: project
severity: preference
triggers: []
domain: ''
title: "Re-issue to the base of the PR attached to the task specification"
pr: 161
merged: 2026-10-17
branch: "fix/republish-keeps-pr-base"
---

# Re-issue to the base of the PR attached to the task specification

What. When re-issuing a task specification attached to a manually issued PR, send it to the base of that PR instead of the default branch (main).

Why. rfg_rag PR #64 was manually issued as `dev`, and the review loop attached that PR to the specification (`pr.base = dev`, the `base` of the specification is empty). When I sent `spec-update` to fix the review settings of the specification, the app re-issued it and `base_of` chose the default branch `main`. `existing_pr` rejected the second PR because a PR for the same branch was already in `dev`. Therefore, the task stopped at `작업 중`, and the review loop button (visible only in `머지 가능` or `PR #…` status) did not appear.

Source. PR #161 · `fix/republish-keeps-pr-base`
