---
scope: project
severity: preference
triggers: []
domain: ''
title: "Implementation of evidence context, interruption logging, and replay evaluation for Jev shadow recommendation"
pr: 92
merged: 2026-10-07
branch: "feat/review-routing-jev-shadow"
---

# Implementation of evidence context, interruption logging, and replay evaluation for Jev shadow recommendation

What. Following the 2-stage review routing design, I implemented context preparation for Jev shadow recommendation, evidence/registration flow selection, cancellation/stale logging, and offline replay/evaluation. Shadow recommendation does not affect enforcement contract, readiness state, execution permissions, and review approval.

Why. Reuse existing repository scope search and provide actual diff, spec, verified manifest, and receipt identity as limited context. - Perform search, normalization, and typed decision within a single shared budget, and log interruptions, errors, budget exhaustion, and identity changes via the spec owner. - Replay fixed requests/results without external calls, and allow actual evaluation requests only for explicit `--live`. - Can be disabled separately from existing global Jev settings via `WIKI_REVIEW_SHADOW=off`. - Integration of contract, agent decisions, review loop, and local verification: 238 passed. - Focused inspection of contract, evaluation, and search: 55 passed. - Inspection of evaluation, normalization, and search: 66 passed. …

Source. PR #92 · `feat/review-routing-jev-shadow`
