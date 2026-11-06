---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya augmentation: Create 'no' candidates for coverage by removing the paragraph cited by the verifier as evidence for 'yes'"
pr: 153
merged: 2026-10-15
branch: "feat/laya-augment-unsupported"
---

# Laya augmentation: Create 'no' candidates for coverage by removing the paragraph cited by the verifier as evidence for 'yes'

What. Add a second method `augment.unsupported` for creating 'no' training candidates for `coverage`. It is created by removing the paragraph containing the passage cited by the verifier as evidence for 'yes'.

Why. In the conflict·coverage retraining results (20261015-102046), coverage was still weak. Accuracy is 0.703, TNR is 0.63, and class AUROC is 0.76. Out of 65 incorrect cases, 43 were instances of saying 'yes' to an uncovered requirement. Error rates by question type are high for bridging at 0.36, partial at 0.35, and unanswered at 0.33. Candidates created with the previous method `uncovered` (removing source document paragraphs) had only 23% 'no' labels. This is because paragraphs from other documents often cover the same requirement as well. Following Laya's principle (focus augmentation on poorly trained parts, owner 2026-10-15), we focus on these three types by removing paragraphs that actually cover the requirement.

Source. PR #153 · `feat/laya-augment-unsupported`
