---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya: restore label smoothing 0.1 and record why useful's policy stays"
pr: 209
merged: 2026-10-21
branch: "laya-notebook-ls01"
---

# Laya: restore label smoothing 0.1 and record why useful's policy stays

What. Label smoothing 0 changed in #208 was worse in `useful`. The results of run 20261021-214310 are validation 0.805, ECE 0.110, and policy cost 264. The run using smoothing 0.1 on the same data was 0.849, 0.016, and 176.5.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #209 · `laya-notebook-ls01`
