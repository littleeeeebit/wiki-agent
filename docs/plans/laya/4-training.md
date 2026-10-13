# Stage 4 — fourteen fine-tunes, calibration and policy

See the [overview](0-overview.md). After [stage 3](3-labels.md).

Purpose. Train one Laya model per kind on the RTX 3080, calibrate it, and
fit its thresholds.

## Training

- Base: the checkpoint stage 1 found stronger on that kind (the English
  root or `laya-typed-decisions`).
- `laya.train.finetune` with `option_layout="parallel"`, so option order
  does not matter (L2); fp16, gradient checkpointing, micro-batch and
  accumulation set to fit 10 GB.
- `max_len` per kind from stage 1's p95 length, up to 4,096. A state
  longer than that is asked per passage through the batch path, and the
  per-passage answers are combined by the rule the kind already uses in
  code (for example, any `useful` passage).
- Early stopping on validation. Seed, data hashes and config recorded with
  each checkpoint as `laya-<kind>-v<N>`.
- Route first, end to end through stage 6, before the other thirteen.

## Calibration and policy

- One temperature per kind fitted on the calibration split (never on
  training items, unlike the notebook default; L2, L3). Report ECE before
  and after.
- Then the existing threshold fit (`eval/jev/policy.json`, keyed by model and
  prompt version) on the same split, so policy rules belong to that
  checkpoint.

## Acceptance

- Each kind's checkpoint, temperature and policy rules exist with their
  provenance.
- Validation accuracy and ECE per kind against stage 1's zero-shot and
  Jev's recorded answers.

## Tests

Existing policy and replay tests run with a Laya model version; a new test
loads a checkpoint record and refuses one whose data hash or temperature is
missing.

## Rollback

A kind's provider goes back to `typesafe`; checkpoints are kept.
