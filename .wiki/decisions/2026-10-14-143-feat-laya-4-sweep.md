---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya Stage 4: Comparing seeds, data volume, and settings with a list of training runs (RUNS)"
pr: 143
merged: 2026-10-14
branch: "feat/laya-4-sweep"
---

# Laya Stage 4: Comparing seeds, data volume, and settings with a list of training runs (RUNS)

What. The Colab notebook trains and compares not just by the number of epochs, but for each run in the `RUNS` list. A run consists of a name and `TrainConfig` values, and if you provide `train`, it selects only that much from the train set with seed 0 (learning curve).

Why. In run 4 (2, 3, 4 epochs, log loss selection), 2 epochs were selected (accuracy 0.74, log loss 0.489). To further increase accuracy, you must first know what is effective. Before buying more paid labels, perform free experiments: seed 1, 2: differences occurring under the same settings (improvements smaller than this are noise), train 250: how much worse it gets if data is halved (whether it improves if increased), label smoothing 0.1 (2, 3 epochs): suppressing overconfidence, encoder learning rate 1.5e-5 (2, 3 epochs). The selection criterion remains log loss after calibration.

Source. PR #143 · `feat/laya-4-sweep`
