---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya support: train augmentation targeting 'partial' answers"
pr: 156
merged: 2026-10-16
branch: "feat/laya-support-part"
---

# Laya support: train augmentation targeting 'partial' answers

What. The first support training (20261016-094429) had an accuracy of 0.75, but it only got 1 out of 42 'partial' answers correct. Therefore, it judged 216 out of 219 questions for coverage, and the accuracy was 0.662 (AUROC 0.73), which was lower than the existing coverage model's 0.694. Following the Laya principle, we will focus on augmenting the weak 'partial' area.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #156 · `feat/laya-support-part`
