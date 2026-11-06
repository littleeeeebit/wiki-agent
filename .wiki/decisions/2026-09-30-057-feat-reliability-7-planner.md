---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: evidence-based planning flow — reliability PR 7"
pr: 57
merged: 2026-09-30
branch: "feat/reliability-7-planner"
---

# feat: evidence-based planning flow — reliability PR 7

What. Reliability PR 7. Added an explicit [planning] flow that receives a repository and goal, finds evidence, and writes a plan document. Planner A only receives read and web tools, and files are written only by the server to a new `docs/plans/<slug>/` folder. After publication, it is passed to the revision model and an independent reviewer (based on the plan from PR 6).

Why. Path (`tool/main/planning.py`): `POST /api/plans`, `GET /api/plans/{sid}`, `…/answers`, `…/resume`, `…/cancel`. There is a key created by the client for each request; the same key and same input return the existing specification, while different inputs return 409. - Status: A plan is a normal specification with its own worktree, and progress remains in the appended `planning` field. The stages are collect → clarify → research → outline → stages → validate → publish → handoff, and stopped. If the server restarts, the running stage becomes stopped(restart) and does not republish itself. …

Source. PR #57 · `feat/reliability-7-planner`
