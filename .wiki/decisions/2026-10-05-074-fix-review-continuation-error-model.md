---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fix review loop stop, missing error logs, and empty agent model selection"
pr: 74
merged: 2026-10-05
branch: "fix/review-continuation-error-model"
---

# Fix review loop stop, missing error logs, and empty agent model selection

What. Fixed a defect where the review loop stops because the completion notification of a foreground command is mistaken for a separate background response. Tracking `is_backgrounded` and subsequent transitions.

Why. Logs review interruptions, task inspection exceptions, corrupted task files, and failures to send wait instructions to error logs. If file logging fails, it leaves a JSON with masked secrets to stderr and can recover logging afterward. - Enabled model selection even on the agent screen where no task is selected. Mobile task options also open. - Full backend gate for approval commits: 1536 passed, 2 skipped, 0 failed (28 minutes 37 seconds). The 2 skipped are due to Windows symbolic link permission restrictions. - Fixed an optional `turn` information regression in the research material publication path found in the full gate, and 20 related checks and 3 independent re-verifications by the reviewer passed. …

Source. PR #74 · `fix/review-continuation-error-model`
