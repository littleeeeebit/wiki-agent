---
scope: project
severity: preference
triggers: []
domain: ''
title: "Reliability v5 result record"
pr: 110
merged: 2026-10-10
branch: "docs/reliability-v5-results"
---

# Reliability v5 result record

What. | gate | value | judgment | | --- | --- | --- | | Analysis recall | 1.0 [0.934, 1.0], n=54 | pass | | Fact specificity | 0.889 [0.796, 0.943], n=72 | inconclusive | | Material classification | 0.973 [0.892, 0.994], n=56 | inconclusive | | Request-only multipart | 1.0 [0. …

Why. Record the v5 held-out results in the reliability plan document. It is in the same format as the v4 results. The answer held-out was not executed according to the owner's decision. This is because it already appeared in the calibration that the answer gate would fail, and the problem is not this set, but the basic answer path (#59).

Source. PR #110 · `docs/reliability-v5-results`
