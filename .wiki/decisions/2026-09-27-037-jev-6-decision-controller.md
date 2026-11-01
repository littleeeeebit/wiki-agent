---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev stage 6 — typed decisions and the retrieval state machine"
pr: 37
merged: 2026-09-27
branch: "jev-6-decision-controller"
---

# feat: Jev stage 6 — typed decisions and the retrieval state machine

What. Stage 6 of `docs/plans/jev/`: Jev's judgments now control bounded retrieval transitions, with validated inputs, explicit uncertainty and fitted per-decision policies.

Why. Typed decisions (`tool/decision/contract.py`): `DecisionRequest`/`DecisionResult` with six statuses (decided, uncertain, unavailable, invalid, cancelled, exhausted). A failure is never read as a negative; unoffered candidates, malformed distributions and Score legends out of order are rejected. - Policy (`tool/decision/policy.py`): a rule per decision kind. The 0.2/0. …

Source. PR #37 · `jev-6-decision-controller`
