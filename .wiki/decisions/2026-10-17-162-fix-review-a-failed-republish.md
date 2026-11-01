---
scope: project
severity: preference
triggers: []
domain: ''
title: "Re-inserting a PR that stopped publishing back into the review loop"
pr: 162
merged: 2026-10-17
branch: "fix/review-a-failed-republish"
---

# Re-inserting a PR that stopped publishing back into the review loop

What. Allows a task that failed to publish while a PR was already open and stopped at `작업 중` to be re-selected as `리뷰 루프` in the PR list.

Why. After fixing the specification of rfg_rag PR #64 to `spec-update`, the app published it again. The gate returned 1 at the end due to a pytest temporary folder permission error (`PermissionError: [WinError 5]`, the test itself passed), and the specification remained as `작업 중` + `fault`. The PR list rejects `작업 중` as `이미 작업 중`. Since there was no path to change this state even after restarting the app, PR #64 could not be put into the review loop.

Source. PR #162 · `fix/review-a-failed-republish`
