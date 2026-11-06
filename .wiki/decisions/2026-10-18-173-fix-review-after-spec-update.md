---
scope: project
severity: preference
triggers: []
domain: ''
title: "loop: Allow PRs that only receive spec-update and stop during work to be included in the review loop"
pr: 173
merged: 2026-10-18
branch: "fix/review-after-spec-update"
---

# loop: Allow PRs that only receive spec-update and stop during work to be included in the review loop

What. `loop.stalled` no longer requires `fault` (error). - If the status is `작업 중`, the PR is already open, and there is no turn holding the worktree, it can be selected from `리뷰 루프 (N)`. If selected, it goes to `리뷰 대기`. - If a turn is in progress, it remains `이미 작업 중`.

Why. The implementation session for rfg_rag #66 sent only `spec-update` without a completion report (`done-report`). `specs.revise` moves the spec to `작업 중` and waits for a completion report. Since no completion report arrived, nothing changed this state. #162 only solved cases with errors (`fault`), and since there was no error in #66, the review loop button was disabled.

Source. PR #173 · `fix/review-after-spec-update`
