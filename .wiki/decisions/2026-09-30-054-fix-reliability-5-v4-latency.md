---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "fix: Passed delay gate by fixing draft requirement ID — Reliability PR 5 v4"
pr: 54
merged: 2026-09-30
branch: "fix/reliability-5-v4-latency"
---

# fix: Passed delay gate by fixing draft requirement ID — Reliability PR 5 v4

What. Reliability PR 5 v4. Resolved the delay gate (10.76s, threshold 10s) that was the only failure in v3. All required gates passed on the new held-out half: additional delay 5.48s.

Why. The draft prompt stated that "all claims include the entire question `r0`." - If the line pasted before the question is omitted as material, `r0` disappears from the requirements and the remaining requirements are after `r1`. - Even so, the draft author wrote `r0`, so all claims in the first draft were rejected as `unknown_requirement` and the answer was written twice. - The reason for rejection was confirmed via temporary diagnosis (recording rejected claims per draft by wrapping `knowledge.grounded`), and that record was not preserved. What the preserved calibration run shows: rows written twice in D: 8 (v3, 6 of which were pasted) → 3 (v4). - `tool/prompts/answer-draft. …

Source. PR #54 · `fix/reliability-5-v4-latency`
