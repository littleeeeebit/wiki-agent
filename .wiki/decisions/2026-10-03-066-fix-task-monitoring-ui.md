---
scope: project
severity: preference
triggers: []
domain: ''
title: "Task UI rearrangement and fixes for deletion, notification, and context compression defects"
pr: 66
merged: 2026-10-03
branch: "fix/task-monitoring-ui"
---

# Task UI rearrangement and fixes for deletion, notification, and context compression defects

What. Fixed the code change status above the task tab and hid the specification summary on the right. Specification data and the edit/plan functions for the next task are maintained. - Apply translation overlays to the specification goals, exclusion scope, completion criteria, decisions, and reports for the next task. Edit/submit preserves the original text. - Consolidated Claude/Codex usage limits at the bottom left, and distinguished between 5-hour/7-day windows and reset dates/times. Tokens are displayed next to the task model, and connection readiness information is hidden. …

Why. Passed frontend build/lint, Ruff, wiki lint, and checks for changed whitespace and UTF-8 BOM. Existing lint warnings and bundle size warnings remain. - Passed 116 agent, task, specification, and loop regressions, and 112 main, specification, session, and synchronization regressions. Passed 2 deletion regressions again after the final deletion reinforcement. - Passed task browser and mobile browser checks. Inspected scroll/tab switching change status, translation and original text editing, usage, notification IPC, compression status, and deletion after refresh. - Browser notification and compression checks use synthetic events and desktop IPC stubs. These are not results confirming actual Windows Notification Center reception or actual model compression execution. Independent GPT-5. …

Source. PR #66 · `fix/task-monitoring-ui`
