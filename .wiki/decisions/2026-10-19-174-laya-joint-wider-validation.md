---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya joint: Read coverage validation in all picks executions"
pr: 174
merged: 2026-10-19
branch: "laya-joint-wider-validation"
---

# Laya joint: Read coverage validation in all picks executions

What. The picks stage of `train.py pack --picks` reads validation in addition to coverage's train and calibration. - Aligned notebook picks cell comments, `support.evaluate`·`joint`·`pack` docstrings, and the planning document (4-training). - Test: Verify that the picks pack's splits and coverage's three splits are each included exactly once.

Why. In the joint secondary training (4,417 questions, focusing on difficult problems), the AUROC for the 105 questions that support could not determine was 0.673→0.680, which was almost the same. However, with 72 joint validation questions (21 yes), the AUROC 95% interval is 0.52–0.82, so the two executions cannot be distinguished. Owner decision (2026-10-19): Expand evaluation and remove the paragraph count shortcut. Use the picks from other support executions (121439, 094429) for validation and calibration as well to increase the number of joint verification and calibration questions. `joint.evaluate` continues to score coverage with `picks-validation.jsonl` of the serving execution (211003).

Source. PR #174 · `laya-joint-wider-validation`
