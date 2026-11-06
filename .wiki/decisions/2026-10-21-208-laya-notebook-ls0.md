---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya notebook: run label smoothing 0 to test useful's probability floor"
pr: 208
merged: 2026-10-21
branch: "laya-notebook-ls0"
---

# Laya notebook: run label smoothing 0 to test useful's probability floor

What. In the run (20261021-114803) where the training data for `useful` was doubled (1,524 → 3,171), the validation accuracy improved from 0.817 to 0.849, and the ECE improved from 0.068 to 0.016. However, the P(yes) floor of the calibration was concentrated at 0.10–0.15, so the policy could not draw a 'no' baseline. The cost increased from 139 to 176.5.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #208 · `laya-notebook-ls0`
