---
scope: project
severity: preference
triggers: ["(?i)laya.*(seed|시드|variance|분산|noise|노이즈|sweep|스윕)"]
reads: [laya/notebooks/train.ipynb, docs/plans/laya/4-training.md]
slots: []
sources: []
links: [laya, laya-exp-useful-training, laya-insight-select-by-log-loss, laya-insight-validation-size]
---

# One Laya fine-tune per setting cannot rank settings — the seed moves it as much

Part of [laya](laya.md).

Rule. Before calling one training setting better than another for a kind,
compare it with the spread of the same setting across seeds. A single run
that beats another by less than that spread is noise.

Why. In run 5 ([laya-exp-useful-training](laya-exp-useful-training.md)) the same `useful` 2-epoch
setting at seeds 0, 1 and 2 gave accuracy 0.740–0.763, log loss
0.487–0.528, confidence AUROC 0.68–0.76, and coverage at 0.90 accuracy
from 106 to 166 questions of 300; the seeds disagreed on 62 questions.
Label smoothing, a lower encoder learning rate and half the data all
landed inside that range. Only 3 epochs with label smoothing 0.1 sat
outside it, and its paired bootstrap interval for accuracy still crossed
zero (−0.010 to +0.063).

Selective numbers (coverage, confidence AUROC) vary most across seeds,
more than accuracy and log loss, so a threshold policy tuned on one seed's
checkpoint should be checked on another seed's before it is trusted.

How to apply. A sweep that compares settings gives each candidate at least
three seeds (about 2.5 minutes each on the L4) and compares means against
the seed spread; select within a setting by log loss
([laya-insight-select-by-log-loss](laya-insight-select-by-log-loss.md)), on 300 validation questions
([laya-insight-validation-size](laya-insight-validation-size.md)).
