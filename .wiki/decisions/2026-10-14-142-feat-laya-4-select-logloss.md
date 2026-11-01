---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya Stage 4: Select epochs by post-calibration log loss, add 3 epochs"
pr: 142
merged: 2026-10-14
branch: "feat/laya-4-select-logloss"
---

# Laya Stage 4: Select epochs by post-calibration log loss, add 3 epochs

What. Change the Colab notebook to select the number of epochs based on post-calibration validation log loss instead of accuracy, and increase training epochs from (2, 4) to (2, 3, 4).

Why. Validation 300-question execution (train 500, calibration 300) results: | Model | Accuracy | ECE | AUROC | Log Loss | Confidence ≥ 0.8 Questions · Accuracy | |---|---|---|---|---|---| | 2 epochs | 0.74 | 0.08 | 0.78 | 0.491 | 113 · 0.94 | | 4 epochs | 0.78 | 0.11 | 0.65 | 0.583 | 240 · 0.82 | 4 epochs has higher accuracy but greater overconfidence, so the temperature was capped at the Laya upper limit of 5.0, and the accuracy is 0.83 even at confidence 0.9 or higher. Since this model is used as a threshold policy that answers only when confident, the existing criteria based solely on accuracy are not appropriate. …

Source. PR #142 · `feat/laya-4-select-logloss`
