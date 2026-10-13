# Replacing Jev with fine-tuned Laya models

The owner asked (2026-10-13) to replace the hosted Jev decision model with
Laya, trained per purpose and routed by code. This page is the planning
research for [the Laya plan](../plans/laya/0-overview.md). It records what
Laya is, what the current Jev traffic looks like, and which training and
labelling methods the plan adopts.

## Problem

Every Jev decision is a hosted call to `api.typesafe.ai`
(`tool/decision/__init__.py`, `HOST`, model `jev-1.13.0`). The model is fixed;
the repository can only fit thresholds around it (`eval/jev/policy.json`).
A wrong answer for this repository's states cannot be trained away. Laya is
an open model with the same question contract, so it can be fine-tuned on
this repository's own decisions.

This is not the cause of reliability PR 9's open review findings. Those come
from the set's substring label checker (`tool/eval/korean.py`), which calls
no model.

## Sources

| Id | Source | Read | Used for |
| --- | --- | --- | --- |
| L1 | [Laya model card](https://huggingface.co/convaiinnovations/laya), retrieved 2026-10-13 | Page read | Architecture, contract, limits, benchmarks |
| L2 | [Laya repository](https://github.com/NandhaKishorM/laya), v0.4.6, retrieved 2026-10-13 | README read | Serving, fine-tuning entry points, calibration |
| L3 | Guo et al. 2017, [On Calibration of Modern Neural Networks](https://arxiv.org/abs/1706.04599) | Known paper, not reread | Temperature scaling on held-out data |
| L4 | Nahum et al. 2024, [Are LLMs Better than Reported?](https://arxiv.org/abs/2410.18889) | Search summary | LLM ensembles flag label errors for expert review |
| L5 | Lee et al. 2024, [LLM2LLM](https://arxiv.org/abs/2403.15042) | Search summary | Teacher augmentation targeted at the student's errors |
| L6 | Clark et al. 2019, [Born-Again Multi-Task Networks](https://arxiv.org/abs/1907.04829) | Search summary | Multi-task BERT often fails to match single-task models |
| L7 | Liu, Liang and Gitter 2019, [Loss-Balanced Task Weighting](https://ojs.aaai.org/index.php/AAAI/article/view/5125) | Search summary | Per-task negative transfer under multi-task training |
| L8 | Warner et al. 2024, [ModernBERT](https://arxiv.org/abs/2412.13663) | Known paper, not reread | Encoder with native 8,192-token context |

## What Laya is (L1, L2)

- A non-autoregressive decision model. A state plus typed questions
  (`noul`, `choice`, `score`) returns per-question probabilities. It never
  generates text.
- `laya-serve` exposes "the same POST /v1/systemone request and response
  shape as TypeSafe Jev" (L1). A batch endpoint takes up to 64 states (L2).
- English root checkpoint: ModernBERT-large encoder (395M) with a decision
  head, 421M in all; default `max_len` 512. `laya-typed-decisions`: 1,024.
  The multilingual checkpoint reads up to 8,192 tokens, and accuracy "varies"
  past about 4,000 (L2).
- Each option is scored at its own `[MASK]` and softmaxed over the question's
  options, so a new question schema needs no new head.
- Zero-shot it is near chance on typed decisions: 0.362 against 0.318 random
  and 0.461 majority. Fine-tuning on the benchmark's 6,000 training decisions
  gave 0.766 (L1, L2).
- It ships over-confident. Refitting one temperature per (type, option count)
  on held-out data moved mean ECE from 0.466 to 0.081 (L2). The notebook's
  calibration samples are training items, so it must be refitted on separate
  data (L2, L3).
- Weak spots: many options (Banking77, 77 labels: 0.425), ordinal `score`,
  and `noul` answers that follow the option labels rather than the state
  (issue #156).
- Fine-tuning: `laya.train.finetune(train.jsonl, base, out, TrainConfig)`,
  trained with RLCD (proper-scoring-rule rewards). About 4–5 hours for 4
  epochs over ~30k questions on 2×T4. `option_layout="parallel"` makes option
  order irrelevant (L2).
- Apache-2.0. The dataset's field format is in the repository's fine-tuning
  notes, which this research did not read; stage 2 reads it before building.

## Current Jev traffic (measured)

Jev's recorded `input_tokens` under `raw/eval/jev` (Jev's tokenizer; Laya's
will differ):

| Request | Count | Median | Max |
| --- | --- | --- | --- |
| route | 180 | 522 | 671 |
| judge (grade, coverage, repair in one request) | 164 | 2,205 | 2,378 |
| review routing | 520+ | 2,470 | 78,850 |
| action (stage 8) | 50 | ~560 | 4,556 |

Route fits Laya's 1,024 default. Judge and review routing do not. The
repository has a few hundred real requests in all (`raw/actions` holds 273
rows), far below 3,000 per decision kind.

Fourteen kinds exist (`tool/decision/policy.py`): Noul `route`, `source`,
`useful`, `conflict`, `redirect`, `coverage`, `ask`, `analysis`, `progress`;
Choice `repair`, `relation`, `answers`, `faithful`, `action`.

The machine has an RTX 3080 with 10 GB. Fourteen 421M-parameter models in
fp16 are about 0.84 GB each, 11.8 GB together, so all fourteen cannot stay
resident.

## Core theory adopted

1. One model per kind. Multi-task fine-tuning of BERT-class encoders often
   loses to single-task models on some tasks (L6, L7). The owner chose
   fourteen models (2026-10-13); routing is code's, since every request
   already names its kind.
2. Labels by agreement. Two strong LLMs label independently; agreed labels
   train, disagreements go to a person. LLM ensembles find label errors and
   their confidence predicts precision (L4). The ensemble is a filter, not
   ground truth.
3. Error-driven augmentation. After training, cluster validation errors into
   slices and have a teacher write new states for the weakest slice, labelled
   by the same agreement rule (L5). Validation, never test, drives it.
4. Calibration on held-out data. One temperature per kind on a calibration
   split that neither trained nor selected the model (L3), then the existing
   threshold fit per model version (`eval/jev/policy.json`).
5. Longer inputs by encoder length, then by splitting. ModernBERT's native
   context is 8,192 (L8). Train judge-type kinds at the measured length;
   where a state still exceeds it, ask per passage through the batch path.

## Applicability and counterevidence

- Laya's published results are on ticket and intent workflows, not on
  retrieval grading. Accuracy on this repository's kinds is unknown until
  stage 1's zero-shot baseline and stage 4's training.
- Two LLM labellers share failure modes; agreement does not prove a label.
  L4 measured error detection of 6–21%, not label correctness. The frozen
  test sets stay human-labelled or come from existing reviewed sets.
- Synthetic states dominate training, because real traffic is small. A
  model can fit the generator's style. The test sets therefore use real
  logged states and existing evaluation sets only.
- Fourteen models multiply training, calibration and storage by fourteen,
  and the GPU cannot hold all of them. Stage 1 measures swap latency before
  any training is paid for.
- Laya's issue #156 (`noul` following option labels) would hit nine of the
  fourteen kinds; stage 1 measures it with label-swapped probes.

## Alternatives rejected

- One shared model with per-kind temperatures: fewer artifacts, but the
  owner chose fourteen models.
- Distilling Jev's answers: cheapest labels, but it copies Jev's errors,
  which is what the replacement is for.
- Human labels only: about 42,000 labels is not feasible; humans adjudicate
  disagreements and spot-check.
- Laya's Router: it routes by script and language, not by purpose.

## Validation conditions

- Stage 1: the local server answers the current request shape, every kind's
  zero-shot accuracy and the label-swap effect are recorded, and swap latency
  fits the per-question budget.
- Stage 3: a pilot measures agreement and the human queue before the full
  labelling is approved.
- Stage 6: per kind, Laya is non-inferior to `jev-1.13.0` on the frozen test
  and the reliability v8 gates do not regress.
