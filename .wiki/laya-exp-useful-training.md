---
scope: project
severity: preference
triggers: ["(?i)laya.*(train|학습|colab|epoch|에폭|zero.?shot|제로샷|결과)"]
reads: [docs/plans/laya/4-training.md, laya/train.py, laya/notebooks/train.ipynb]
slots: []
sources: []
links: [laya, laya-exp-useful-data, laya-insight-select-by-log-loss, laya-insight-temperature-ceiling, laya-insight-validation-size, laya-insight-seed-variance]
---

# Laya `useful` fine-tunes on Colab L4 — runs 1 to 5, 2026-10-14

Part of [laya](laya.md). Data in [laya-exp-useful-data](laya-exp-useful-data.md).

## Setup common to every run

`laya` 0.4.6, transformers 5.19, torch 2.11, NVIDIA L4 (22 GB; peak 7.8 GB).
Base `base` (the better zero-shot), `option_layout="parallel"`,
`max_len` 2,048 (no question was over it), micro-batch 4 × 16 accumulation,
`calib_frac=0`, seed 0. Train 500 labels. Training takes about 1 minute an
epoch. Metrics are on validation; confidence is `max(p, 1 - p)` after the
calibration split's temperature when one exists.

## Run 1 — 100 validation, 4 epochs, no calibration split

| | Accuracy | Mean confidence | ECE | Val loss |
| --- | --- | --- | --- | --- |
| before (`base`) | 0.50 | 0.70 | 0.21 | 0.83 |
| after 4 epochs | 0.79 | 0.98 | 0.21 | 1.47 |

Large gain, but strongly overconfident, so a calibration split was labelled.

## Run 2 — 100 validation, 300 calibration, 2 and 4 epochs (result 20261014-0354)

| Model | Accuracy | ECE | Conf. when wrong | AUROC | Log loss | Temperature |
| --- | --- | --- | --- | --- | --- | --- |
| `base` zero-shot | 0.52 | 0.14 | 0.65 | 0.56 | 0.740 | — |
| `typed` zero-shot | 0.49 | 0.05 | 0.54 | 0.57 | 0.695 | — |
| 2 epochs | 0.77 | 0.09 | 0.62 | 0.81 | 0.452 | 1.81 |
| 4 epochs | 0.78 | 0.07 | 0.75 | 0.79 | 0.467 | 5.0 (ceiling) |

The two differed by one question; see [laya-insight-validation-size](laya-insight-validation-size.md).

## Run 3 — 300 validation, 300 calibration, 2 and 4 epochs (result 20261014-0634)

| Model | Accuracy | ECE | Conf. when wrong | AUROC | Log loss | ≥ 0.8: n, acc. | ≥ 0.9: n, acc. |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `base` zero-shot | 0.55 | 0.10 | 0.65 | 0.52 | 0.725 | 15, 0.67 | 0 |
| `typed` zero-shot | 0.50 | 0.04 | 0.54 | 0.54 | 0.696 | 0 | 0 |
| 2 epochs | 0.74 | 0.08 | 0.66 | 0.78 | 0.491 | 113, 0.94 | 28, 0.93 |
| 4 epochs | 0.78 | 0.11 | 0.84 | 0.65 | 0.583 | 240, 0.82 | 150, 0.83 |

- Before temperatures, 4 epochs had mean confidence 0.99 and val loss 2.25;
  2 epochs 0.84 and 0.52. Temperatures 5.0 (ceiling) and 1.84.
- 4 epochs alone was right on 32 questions, 2 epochs alone on 19
  (McNemar p ≈ 0.07).
- The notebook chose 4 epochs by accuracy; this led to
  [laya-insight-select-by-log-loss](laya-insight-select-by-log-loss.md) and [laya-insight-temperature-ceiling](laya-insight-temperature-ceiling.md).

## Run 4 — 300 validation, 300 calibration, 2, 3 and 4 epochs, chosen by log loss (result 20261014-0701)

Notebook at PR #142 (d3d38d8). Chose 2 epochs; its checkpoint is
`내 드라이브/laya/useful-train-e2.zip`.

| Model | Accuracy | ECE | Conf. when wrong | AUROC | Log loss | Temperature | ≥ 0.8: n, acc. | ≥ 0.9: n, acc. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 epochs | 0.74 | 0.08 | 0.66 | 0.78 | 0.489 | 1.81 | 117, 0.93 | 33, 0.91 |
| 3 epochs | 0.75 | 0.07 | 0.68 | 0.77 | 0.492 | 3.88 | 105, 0.92 | 2, 1.00 |
| 4 epochs | 0.77 | 0.10 | 0.81 | 0.70 | 0.551 | 5.0 (ceiling) | 235, 0.85 | 131, 0.86 |

- Most questions answerable while the answered ones stay at least 0.90
  accurate, the threshold set on these same 300 (optimistic): 2 epochs 173,
  3 epochs 141, 4 epochs 83. At 0.95: 63, 27, 47.
- Raw validation loss before temperatures: 0.52, 0.81, 2.04; raw mean
  confidence 0.84, 0.95, 0.98. Training loss in the last epoch: 0.32,
  0.17, 0.17.
- 2 epochs repeated run 3 (0.74, log loss 0.491 → 0.489): seed 0 and the
  same data give nearly the same model.
- 3 epochs sits between on every measure; it did not keep 4 epochs'
  accuracy with 2 epochs' confidence.

## Run 5 — eight-run sweep: seeds, half the data, label smoothing, encoder learning rate (result 20261014-0827)

PR #143 notebook (`RUNS`; the uploaded zip was packed at b767ad0, whose
training code equals the reviewed head). 2 epochs at seeds 0–2; 2 epochs on
a 250-record subset; label smoothing 0.1 at 2 and 3 epochs; encoder
learning rate 1.5e-5 at 2 and 3 epochs. About 2.5 minutes per 2-epoch run,
3.3 per 3-epoch run, 26 minutes in all. Chose `e3-ls0.1` by log loss; its
checkpoint is `내 드라이브/laya/useful-train-e3-ls0.1.zip`.

AUROC is the wiki's column (confidence separating right from wrong);
"class AUROC" is p(true) against the label. Coverage is the most questions
answerable while the answered stay at the target accuracy, threshold set on
these same 300 (optimistic).

| Run | Accuracy | ECE | Log loss | Temperature | AUROC | Class AUROC | Cov. 0.90 | Cov. 0.95 | ≥ 0.8: n, acc. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| e2 (seed 0) | 0.753 | 0.05 | 0.487 | 1.58 | 0.76 | 0.850 | 166 | 65 | 114, 0.94 |
| e2 seed 1 | 0.763 | 0.06 | 0.504 | 0.51 | 0.72 | 0.848 | 121 | 41 | 85, 0.92 |
| e2 seed 2 | 0.740 | 0.06 | 0.528 | 1.85 | 0.68 | 0.838 | 106 | 64 | 91, 0.90 |
| e2 train 250 | 0.740 | 0.06 | 0.535 | 1.13 | 0.69 | 0.853 | 90 | 43 | 60, 0.93 |
| e2 ls 0.1 | 0.753 | 0.05 | 0.484 | 1.48 | 0.77 | 0.853 | 168 | 84 | 116, 0.93 |
| e3 ls 0.1 (chosen) | 0.780 | 0.04 | 0.471 | 2.00 | 0.75 | 0.865 | 181 | 74 | 137, 0.90 |
| e2 lr 1.5e-5 | 0.743 | 0.04 | 0.486 | 1.20 | 0.77 | 0.846 | 155 | 84 | 112, 0.94 |
| e3 lr 1.5e-5 | 0.740 | 0.08 | 0.493 | 2.72 | 0.78 | 0.864 | 165 | 62 | 106, 0.93 |

- Seeds alone move accuracy 0.74–0.76, log loss 0.487–0.528, coverage at
  0.90 from 106 to 166; the three seeds disagree on 62 of 300 questions.
  See [laya-insight-seed-variance](laya-insight-seed-variance.md).
- Every other run sits inside or just past that spread. `e3-ls0.1` is the
  only one past it on log loss and accuracy: against seed 0 it was alone
  right on 21 questions, seed 0 alone on 13; paired bootstrap 95% interval
  for the accuracy gain −0.010 to +0.063.
- Label smoothing let 3 epochs train without the overconfidence 3 and 4
  epochs showed in run 4 (temperature 2.00, not 3.88 or the 5.0 ceiling),
  but it caps confidence: only 2 questions reach 0.9.
- Half the data (250) kept class AUROC (0.853) but lost log loss (0.535)
  and selective quality (coverage 90 vs 106–166). More labels help
  ranking-by-confidence more than accuracy; the gain from 250 to 500 is
  about the size of the seed spread.
- Averaging the three seeds: accuracy 0.763, log loss 0.496, coverage 153;
  averaging all eight: 0.777, 0.485, 162. No better than the single
  `e3-ls0.1`.
- Seed 0 2-epoch repeated runs 3 and 4 closely (0.74–0.75, log loss
  0.487–0.491) but its temperature moved 1.81 → 1.58: GPU training is not
  bit-for-bit repeatable.

## Next

Threshold policy on the calibration split for the chosen checkpoint
(plan stage 4, `tool/eval/policy.py`), served on the RTX 3080. Settling
between `e3-ls0.1` and `e2` needs seeds for `e3-ls0.1`; more train labels
are the other lever.

## Raw files (local)

`Downloads/result-useful-<stamp>.zip`: `results.json`, `log.txt`,
`predictions.jsonl` (per question: expected, each model's answer and
confidence), `e<N>-train_report.json`, `e<N>-calibration.json`.
