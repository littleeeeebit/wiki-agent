---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya policy: refit redirect after augmenting train with natural positives"
pr: 210
merged: 2026-10-22
branch: "laya-redirect-policy-full2"
---

# Laya policy: refit redirect after augmenting train with natural positives

What. `redirect` answered 'no' to 8 out of 18 correct answers in the calibration set. Upon diagnosis, 38% of the train set were correct answers, and one-third of those were blatantly inserted injection utterances, whereas validation and calibration only contained natural correct answers from actual documents.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #210 · `laya-redirect-policy-full2`
