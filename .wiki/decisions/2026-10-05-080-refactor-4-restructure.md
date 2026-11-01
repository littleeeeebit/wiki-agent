---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring 4/6: Module Reconfiguration Mode and Repository Locking"
pr: 80
merged: 2026-10-05
branch: "refactor/4-restructure"
---

# Refactoring 4/6: Module Reconfiguration Mode and Repository Locking

What. This is step 4 of the refactoring series. We add module reconfiguration mode (L0–L2) and repository locking.

Why. Audit the selected modules in a read-only turn to create a 1–3 step plan (`refactor-plan`). Property test PR comes first. - L2–L3 steps lock the repository with a `refactor.block` of the self-specification. Reject new tasks while a `specs.checkout_idle` that passes all task starts is locked. Do not start if there are other open tasks when locking. - Stopping or canceling releases it, while termination/restart leaves it locked, and [Resume] locks it again. - L2–L3 PRs do not start the review loop on their own. A human starts the review, and after the review approves, you must `[승인]`(`POST /api/refactors/{id}/approve`) to proceed to the next step. - `tool/main/specs. …

Source. PR #80 · `refactor/4-restructure`
