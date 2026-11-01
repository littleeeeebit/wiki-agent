---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Reliability v5 Design: Number of Decidable Cases and Routing Gate by Classification"
pr: 107
merged: 2026-10-10
branch: "plan/reliability-v5"
---

# Reliability v5 Design: Number of Decidable Cases and Routing Gate by Classification

What. Change only the design document. There is no execution or fixture. I put the following in `docs/plans/reliability/5-english-baseline.md`.

Why. Let's re-determine the ratio gate with the Wilson interval in #105; the routing gate and task gate of v4 became pending due to a lack of cases, not failure. And the v4 routing bundle has 3 analysis cases and 63 fact cases out of 66 held-out cases, so even if it always answers "fact", it results in 0.955. Accuracy alone cannot show whether it recognizes analysis.

Source. PR #107 · `plan/reliability-v5`
