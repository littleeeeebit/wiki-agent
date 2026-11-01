---
scope: project
severity: contract
triggers: ["(?i)laya.*(temperature|온도|calibrat|보정|overconfiden|과신|overfit|과적합)"]
reads: [laya/train.py, docs/plans/laya/4-training.md]
slots: []
sources: []
links: [laya, laya-exp-useful-training, laya-insight-select-by-log-loss]
---

# A fitted temperature at Laya's 5.0 ceiling means the checkpoint overfit

Part of [laya](laya.md).

Rule. When `calibration.json` holds a temperature of 5.0 for a question
type, treat that checkpoint's confidence for the type as uncalibrated: do
not fit thresholds on it; train fewer epochs instead.

Why. `laya.common` clamps fitted temperatures to [0.5, 5.0]. In runs 2 and 3
([laya-exp-useful-training](laya-exp-useful-training.md)) 4 epochs had raw mean confidence 0.98–0.99
and validation loss 1.5–2.25 while training loss kept falling (0.59 → 0.20):
memorised training labels. Its fitted noul temperature was 5.0 both times,
and after it the wrong answers still carried 0.75–0.84 confidence. 2 epochs
fitted 1.81–1.84 and its confidence separated right from wrong (AUROC
0.78–0.81). Run 4 showed the slope: 2, 3 and 4 epochs fitted 1.81, 3.88
and 5.0, with AUROC 0.78, 0.77 and 0.70. A temperature well above 2 is
already a warning.

How to apply. Read `e<N>-calibration.json`'s `temperature` (order: choice,
score, noul) in the result zip before anything else. Raw validation loss
rising while accuracy holds is the same signal before calibration.
