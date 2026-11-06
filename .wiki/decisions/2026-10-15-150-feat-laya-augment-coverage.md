---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya augmentation: Create 'no' candidates by removing the source paragraph from the 'yes' state of coverage"
pr: 150
merged: 2026-10-15
branch: "feat/laya-augment-coverage"
---

# Laya augmentation: Create 'no' candidates by removing the source paragraph from the 'yes' state of coverage

What. Add `laya/augment.py` that generates 'no' (evidence does not cover the requirement) training candidates for `coverage` from the existing 'yes' state.

Why. As a result of training 9 types of searches in Colab (20261015-053449), `coverage` collapsed to answer 'yes' to all 127 validation questions. It failed to find a single one of the 24 'no' answers. The training data is skewed with 946 'yes' and 158 'no' instances. Laya's principle is to intensively augment the parts that are not well learned (Owner, 2026-10-15). Even if the remaining candidates are labeled, 'no' is rare. It is only 40% even in question types without answers. Therefore, we deliberately create states that will become 'no'.

Source. PR #150 · `feat/laya-augment-coverage`
