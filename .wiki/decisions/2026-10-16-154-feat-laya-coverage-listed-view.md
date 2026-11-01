---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya coverage: Show only list paragraphs to the model"
pr: 154
merged: 2026-10-16
branch: "feat/laya-coverage-listed-view"
---

# Laya coverage: Show only list paragraphs to the model

What. `coverage` Make the model see only the paragraphs contained in `complete_passages`.

Why. We performed a free check of 61 coverage validation errors (run 20261015-214208, 2-epoch model). There are 37 cases where the label is correct but the model is wrong, 1 label error, and 23 cases where the requirement itself is ambiguous. The question states "count only the paragraphs in the list," but the model answers 'yes' based on paragraphs outside the list. The following is the validation error rate. | | Correct answer 'No' | Correct answer 'Yes' | | --- | --- | --- | | All paragraphs are in the list | 7/34 (0.21) | 3/24 (0.12) | | There are paragraphs outside the list | 33/82 (0.40) | 18/79 (0.23) | Since the label question itself does not count paragraphs outside the list, the existing labels remain valid. There is no additional cost.

Source. PR #154 · `feat/laya-coverage-listed-view`
