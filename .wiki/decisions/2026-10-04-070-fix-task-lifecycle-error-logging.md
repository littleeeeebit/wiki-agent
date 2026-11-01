---
scope: project
severity: preference
triggers: []
domain: ''
title: "Error log and task/review loop lifecycle recovery"
pr: 70
merged: 2026-10-04
branch: "fix/task-lifecycle-error-logging"
---

# Error log and task/review loop lifecycle recovery

What. Record errors in raw/errors.jsonl. Include HTTP, browser, agent, review, and cleanup failures, mask secrets, and rotate files. - If a PR branch is already open from the original checkout, verify the clean state and correct PR HEAD, then reuse it. - Proceed with auto-merge and cleanup after review approval and final check pass. Revert the original to base and clean up reviewed local/remote branches and corresponding PR review artifacts. …

Why. Cause and verification scope: docs/research/task-lifecycle-recovery.md - Frontend build/lint, task/mobile browser check, Ruff, wiki lint, whitespace/UTF-8 without BOM check passed. - Full backend check: 1,436 passed, 2 skipped, 5 failed. Modified 2 expected values for existing branch maintenance, and 2 timeout exceeded cases and 1 translation status mismatch were not reproduced. All 5 cases passed in the current code re-check. - The re-check Python process remained in wait-to-terminate after outputting pass results, and was stopped after identification. Did not record the full check as a complete success. - Latest cleanup regression check and 16 existing worktree checks passed. …

Source. PR #70 · `fix/task-lifecycle-error-logging`
