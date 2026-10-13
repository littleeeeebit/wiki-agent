# Stage 4 — fourteen fine-tunes, calibration and policy

See the [overview](0-overview.md). After [stage 3](3-labels.md).

Purpose. Train one Laya model per kind on the RTX 3080, calibrate it, and
fit its thresholds.

## Training

- Base: the checkpoint (the English root or `laya-typed-decisions`) with
  the higher accuracy on that kind's validation split. The gate test is
  never read.
- `laya.train.finetune` with `option_layout="parallel"`, so option order
  does not matter (L2), if stage 1 found its requirements met; otherwise
  the default layout with options shuffled per example. fp16, gradient
  checkpointing, micro-batch and accumulation set to fit 10 GB.
- `max_len` per kind: the smallest of 1,024, 2,048 or 4,096 that holds 95%
  of the kind's state-plus-head lengths from stage 1. Longer states follow
  stage 1's length rule (TypeSafe, else `unavailable`); no state is cut.
- Early stopping on validation. Seed, data hashes and config recorded with
  each checkpoint as `laya-<kind>-v<N>`.
- `useful` first, end to end through stage 6, before the other kinds; each
  of those starts once it has at least 100 production states.
- Code: `laya/train.py` and `laya/notebooks/train.ipynb`.

## Calibration and policy

- One temperature per kind fitted on the calibration split (never on
  training items, unlike the notebook default; L2, L3). Report ECE before
  and after.
- Then the existing threshold fit (`tool/eval/policy.py`) on the same
  split, written to `eval/laya/policy/<kind>.json` bound to the checkpoint,
  its version and temperature (stage 1's composed policy). TypeSafe's
  `eval/jev/policy.json` is not touched.

## Acceptance

- Each kind's checkpoint, temperature and policy rules exist with their
  provenance.
- Validation accuracy and ECE per kind against stage 1's zero-shot and
  Jev's recorded answers on the same development cases.

## Tests

- `tool/test_laya_checkpoint.py`: a checkpoint record without its data
  hashes, temperature or policy file is refused; a policy file bound to
  another checkpoint or version leaves the kind provisional; the base choice
  reads validation only.
- `tool/test_decision.py` and `tool/test_decision_flow.py` (existing)
  run once with a composed policy where `route` is a Laya kind.

## Rollback

A kind's provider goes back to `typesafe`; checkpoints are kept.
