---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya joint: random listed-passage sets for distinct train problems"
pr: 169
merged: 2026-10-18
branch: "feat/laya-joint-subsets"
---

# Laya joint: random listed-passage sets for distinct train problems

What. `joint.py subsets <split>` → `<split>-subsets.candidates.jsonl`. For each coverage requirement it draws two random sets of the listed passages. Each set's size comes from the split's part-pick sizes (`picks-<split>.jsonl`). …

Why. The first joint run (20261018-044421) was weak: AUROC 0.67 and 63% right on the 105 questions support left undecided. Its labels agree with coverage 94% of the time. Train held only 725 distinct problems, and its fewer/padded views are variants of them. The owner chose a big augmentation on 2026-10-18 ("joint large augmentation"). On coverage train this gives 4,364 questions, median 884 tokens, 1 over 4,096.

Source. PR #169 · `feat/laya-joint-subsets`
