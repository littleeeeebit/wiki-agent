---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya joint: picks from several support runs for hard problems"
pr: 170
merged: 2026-10-18
branch: "feat/laya-joint-more-picks"
---

# Laya joint: picks from several support runs for hard problems

What. `train.py pack --picks a.zip,b.zip`: `packed.json` `picks` is now `{"checkpoints": [...], "splits": [...]}`. - The notebook picks cell reads coverage train and calibration with each checkpoint in turn. …

Why. `joint subsets` (PR #169) came back 93% "no": 1,333 of 1,434 labels after 12 of 29 rounds. 545 of those are on covered requirements. Random sets rarely hold the relevant passages, so they are easy problems, and the run was stopped. Owner (2026-10-18): the training data must span difficulty, weighted hard, so the decision model works in hard situations. …

Source. PR #170 · `feat/laya-joint-more-picks`
