---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Fix the three intermittent CI failures"
pr: 203
merged: 2026-10-21
branch: "LittleBitAI/fix-ci-flakes"
---

# Fix the three intermittent CI failures

What. We conducted a full investigation of 18 CI failures since 10-17. 14 cases were actual defects fixed in the same branch, and we identified and fixed the causes of three types of intermittent failures that passed upon re-running the same commit.

Why. A — `web/tests/task_browser.py` second `머지 ▸` click 30-second timeout (run 39342372292, 39361138526, 39534422793) - Cause: If `/api/specs` arrives before `/api/worktrees` immediately after a refresh, the rail item key is `spec:<id>`, and if the work tree list arrives after clicking it, the key changes to the work tree path, causing the selection to be cleared (the screen returns to "Select Work"). This is an actual screen defect, not just a test issue. - Fix: The selection follows the work tree key in `App.tsx`. The test forces that sequence to verify (failed before the fix, passed after the fix). B — `tool/test_connect. …

Source. PR #203 · `LittleBitAI/fix-ci-flakes`
