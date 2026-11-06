---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Measurement accuracy: Proportion gate Wilson interval, original text-based duplicate judgment, v4 re-judgment"
pr: 105
merged: 2026-10-10
branch: "fix/audit-measurement"
---

# Measurement accuracy: Proportion gate Wilson interval, original text-based duplicate judgment, v4 re-judgment

What. `tool/eval/report.py`: Changed the proportion interval to a 95% Wilson interval. The lower bound of 6/6 is 0.61, and even if all succeed, about 35 cases are needed to exceed 0.90. Difference metrics like improvement margin remain as the existing bootstrap. - `tool/search/retrieval.py`: `text_key` judges as the original text. Only ignores line ends, trailing spaces, and leading/trailing blank lines. Indentation of the first line is also kept as is (pointed out in review round 1). …

Why. Two items from the external audit (2026-10-10) regarding measurement accuracy. Q1: The proportion gate (analysis/fact routing, task start/check/review modification) used percentile bootstrap intervals. If all succeed, the interval collapses to [1, 1], so it passed the lower bound 0.90 gate with only 6 out of 6 cases. K2: Duplicate judgment of search candidates (`text_key`) ignored case and spaces. In code or configuration, if `production` and `PRODUCTION` or indentation differ, the meaning is different, but they were grouped as the same candidate.

Source. PR #105 · `fix/audit-measurement`
