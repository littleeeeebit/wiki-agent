---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya: Create coverage answers by paragraph unit"
pr: 155
merged: 2026-10-16
branch: "feat/laya-support-kind"
---

# Laya: Create coverage answers by paragraph unit

What. Split coverage by paragraph unit. The method chosen by the user is 3-choice (all/partial/none) and the budget cap is $40.

Why. Coverage remained near 0.70 accuracy and 0.77 AUROC during three retrainings. We sequentially attempted "no" data augmentation, epoch adjustment, and showing only list paragraphs. 2/3 of the validation errors were cases where the label was correct. The form of asking about a single paragraph has already been well learned by `useful` (0.83, AUROC 0.92).

Source. PR #155 · `feat/laya-support-kind`
