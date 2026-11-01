---
scope: project
severity: preference
triggers: ["(?i)laya.*(validation|검증).*(size|크기|늘리|개수|몇)"]
reads: [docs/plans/laya/2-data.md]
slots: []
sources: []
links: [laya, laya-exp-useful-training, laya-exp-useful-data]
---

# 100 validation questions cannot rank Laya epoch counts — use 300

Part of [laya](laya.md).

Rule. Compare fine-tunes of a kind on at least 300 validation questions;
treat a difference of a few questions on 100 as noise.

Why. On 100 questions (run 2, [laya-exp-useful-training](laya-exp-useful-training.md)) 2 and 4 epochs
differed by one question (0.77 vs 0.78) and their log loss and AUROC were
close, so the comparison said nothing. On 300 (run 3) they split clearly:
AUROC 0.78 vs 0.65, log loss 0.491 vs 0.583, and an accuracy gap with
McNemar p ≈ 0.07. Accuracy's standard error is about ±4 points at 100 and
±2.5 at 300.

How to apply. 200 more validation labels cost $2.14 for `useful`
([laya-exp-useful-data](laya-exp-useful-data.md)); budget that before a kind's first epoch
comparison.
