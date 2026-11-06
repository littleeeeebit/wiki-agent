---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya gate: useful and redirect on their reserved gate states"
pr: 214
merged: 2026-10-22
branch: "laya-gate-passage-kinds"
---

# Laya gate: useful and redirect on their reserved gate states

What. `laya/jev_gate.py` gates `useful` and `redirect` (`--checkpoint`), beside `coverage`. - `--candidates` writes a passage kind's gate candidates: one row per question, the reserved state with only that question's passage (`kinds. …

Why. `useful` and `redirect` each have 104 units reserved on 2026-10-13 that nothing has read. Jev stage 11's arm B did not reword either kind, so production's recorded Jev answers use the current wording. Production's passage views are longer than the generated ones these kinds trained on. Gate views: median 880 tokens, against train's 520. 36% of `redirect`'s are past its trained 1,024. …

Source. PR #214 · `laya-gate-passage-kinds`
