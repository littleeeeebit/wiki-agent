---
scope: project
severity: contract
triggers: ["(?i)laya.*(select|choose|best|고르|선택|epoch|에폭|checkpoint|체크포인트)"]
reads: [laya/notebooks/train.ipynb, docs/plans/laya/4-training.md]
slots: []
sources: []
links: [laya, laya-exp-useful-training, laya-insight-temperature-ceiling]
---

# Choose a Laya checkpoint by calibrated log loss, not by accuracy

Part of [laya](laya.md).

Rule. Among fine-tunes of one kind, keep the one with the lowest validation
log loss after the calibration split's temperatures. Report accuracy, ECE,
AUROC of confidence and the accuracy above 0.8 and 0.9 confidence beside it.

Why. A Laya decision answers only when its confidence clears the policy's
threshold and defers otherwise, so what matters is accuracy where it is
confident. In run 3 ([laya-exp-useful-training](laya-exp-useful-training.md)) 4 epochs was more
accurate (0.78 vs 0.74) and won on accuracy, but its confidence barely told
right from wrong (AUROC 0.65): even at 0.9 or above it was right 0.83 of the
time. 2 epochs was right 0.94 of the time on the 113 questions at 0.8 or
above. Log loss ranked them correctly (0.491 vs 0.583).

How to apply. The notebook's `log_loss` is exact only for noul questions
(the truth's probability is `confidence` when right, `1 - confidence` when
wrong). For choice kinds it must read the expected option's probability
directly: Laya's choice `confidence` is entropy-based, not the top
probability (PR #142 review).
