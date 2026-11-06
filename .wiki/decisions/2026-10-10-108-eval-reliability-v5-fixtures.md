---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Reliability v5 Draft: New task fixtures, held-out intent, routing gate by classification"
pr: 108
merged: 2026-10-10
branch: "eval/reliability-v5-fixtures"
---

# Reliability v5 Draft: New task fixtures, held-out intent, routing gate by classification

What. `eval/jev/reliability-v5/actions.json`: There are 199 task fixtures. They were rewritten without referencing the previous set. - 12 calibration cases per point, 54 held-out cases (`specs.check` is 55 cases). - held-out label distribution: `work.start` 18/18/18, `specs.check` 11×5, `loop.fix` 27/27. - Difficult cases were also included. …

Why. Create and freeze an evaluation set of decidable cases according to the v5 design (#107). Ensure that routing and task gates, which were pending in the v4 re-evaluation, can exceed a Wilson lower bound of 0.90 even with one failure allowed. As decided by the owner, all gates will be re-measured in v5. Frozen on 2026-10-10 after two rounds of label review (r5). Held-out execution has not been performed yet.

Source. PR #108 · `eval/reliability-v5-fixtures`
