---
scope: project
severity: preference
triggers: []
domain: ''
title: "Collection of registered runtime evidence and Windows native verification connection"
pr: 95
merged: 2026-10-07
branch: "feat/review-routing-evidence-handoff"
---

# Collection of registered runtime evidence and Windows native verification connection

What. Execution evidence of registered API, browser, and Windows native flows is automatically collected even in general local and external implementation tasks before review. Only approved settings and selected flows are executed, and general reviewers maintain read-only access. Failures are returned to the corresponding implementation path.

Why. Windows native verification executes isolated apps on a task-dedicated desktop and checks executables, builds, windows, focus, controls, events, and cleanup results. If source, specification, environment, or evidence changes, review preparation and merge eligibility are withheld. Existing Cloud verification and review loops are maintained. Verification: - Total Python regression tests: 1,743 passed, 4 skipped. After full collection, added environment ownership regression and native verification were executed separately from the final source, passing 4. - Actual loopback API, Chromium save/refresh, Windows Win32 window/event, and invalid focus/selector verification passed. GitHub, reviewer, and implementation task transmission are test bands. …

Source. PR #95 · `feat/review-routing-evidence-handoff`
