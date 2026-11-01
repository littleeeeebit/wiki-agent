---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya instruction labeling: Model refusal is not a failure but an empty answer"
pr: 148
merged: 2026-10-15
branch: "fix/laya-refusal"
---

# Laya instruction labeling: Model refusal is not a failure but an empty answer

What. In Laya instruction labeling(`laya/guided.py`), treat the model's usage policy refusal ("can't help with this") as an empty answer rather than a failed turn.

Why. During the full execution of training data, Haiku refused one state of `analysis` every time. Currently, a refusal is treated as a failed turn, so it retries 3 times before stopping the round and waiting for a re-execution. However, since the same refusal occurs even after re-execution, that type of labeling never ends.

Source. PR #148 · `fix/laya-refusal`
