---
scope: project
severity: preference
triggers: []
domain: ''
title: "Answer path design: Verification after #59"
pr: 111
merged: 2026-10-10
branch: "docs/answer-path-design"
---

# Answer path design: Verification after #59

What. | | D Factual claim | No evidence | Ratio | Factual questions with no answer | | --- | --- | --- | --- | --- | | v4 (Verification on) | 132 | 7 | 0.053 | 5 | | v5 (Synthesis) | 401 | 85 | 0.212 | 1 | | v5 A (No Jev) | 403 | 88 | 0.218 | 0 |

Why. In v5, the answer gate decision was postponed. After #59, general answers do not go through the claim verifier. As a result, the ratio of claims without evidence for answers that went through Jev (D) in calibration became the same as the side that answered without Jev (A). This PR organizes the resolution direction into a design document. The code is not changed.

Source. PR #111 · `docs/answer-path-design`
