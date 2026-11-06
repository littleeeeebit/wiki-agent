---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya support: 'All vs. Part' augmentation and validation expansion"
pr: 160
merged: 2026-10-17
branch: "feat/laya-support-wide"
---

# Laya support: 'All vs. Part' augmentation and validation expansion

What. In the retraining with aug3 (20261017-013631), the coverage created with support surpassed the existing coverage model for the first time. The score threshold is 0.744 (existing 0.694), and AUROC is 0.80 (existing 0.77). However, 11 out of 17 incorrect answers viewing 'all' as 'part' remain, and since there are only 17 'all' in the validation, the measurement noise is high. Therefore, we will significantly augment 'All vs. Part' and expand the validation as well.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #160 · `feat/laya-support-wide`
