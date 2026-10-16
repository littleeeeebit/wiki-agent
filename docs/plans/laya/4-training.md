# Stage 4 — fourteen fine-tunes, calibration and policy

See the [overview](0-overview.md). After [stage 3](3-labels.md).

Purpose. Train one Laya model per kind on Colab's L4 (24 GB; owner,
2026-10-14), calibrate it, and fit its thresholds. Serving stays on the
RTX 3080.

## Training

- Base: the checkpoint (the English root or `laya-typed-decisions`) with
  the higher accuracy on that kind's validation split. The gate test is
  never read.
- `laya.train.finetune` with `option_layout="parallel"`, so option order
  does not matter (L2), if stage 1 found its requirements met; otherwise
  the default layout with options shuffled per example. fp16, gradient
  checkpointing, micro-batch 4 with 16 accumulation steps (an effective
  batch of 64) to fit the L4; `coverage` takes 1 with 64 (`MICRO_BATCH`).
  `finetune` scores `eval_data` 16 questions a batch in FP32 whatever the
  micro-batch, so a one-question kind's validation is scored only by
  `score` (`Agent.predict`), not inside `finetune`.
- `max_len` per kind: the smallest of 1,024, 2,048 or 4,096 that holds 95%
  of the kind's state-plus-head lengths from stage 1 (`useful`: 2,048, p95
  1,396). On the labelled splits (2026-10-15) the other retrieval kinds are
  277–684 at p95, so 1,024. `coverage` reads every passage: 4,844 at p95,
  40% over 4,096, so it takes ModernBERT's whole 8,192 (owner, 2026-10-15).
  Longer states follow stage 1's length rule (TypeSafe, else
  `unavailable`); no state is cut, so a question over it is left out of
  training and scoring alike.
- Colab. `python laya/train.py pack <kind>...` zips `laya/`'s code with the
  kinds' labelled splits as `laya/data/colab.zip`, and `laya/packed.json`
  with the kinds in training order, the commit and the splits' hashes;
  `laya/notebooks/train.ipynb` (Korean, for the owner) unpacks it from
  Drive, installs `laya` 0.4.6 and takes each kind in turn: it scores both
  published checkpoints zero-shot at the reviewed commit, fine-tunes the
  better one, and saves the chosen checkpoint (with `laya-run.json`) back
  to Drive. A kind that fails leaves its error in its result zip and the
  next kind starts. After every step it rewrites the kind's small result
  zip on Drive, so a failed run
  still leaves one: `log.txt` (every step's output and traceback),
  `results.json` (environment, `packed.json`, data counts, accuracies,
  seconds per step), `predictions.jsonl` (each validation question's
  expected answer and every model's, no state text), `laya-run.json` and
  `train_report.json`. The owner brings that zip back to the PC for the
  comparison. No paid call runs there; the improvement loop stays on the
  PC.
- Early stopping on validation. Seed, data hashes and config recorded with
  each checkpoint as `laya-<kind>-v<N>`. Laya 0.4.6's `finetune` has no
  early stopping: it scores validation (`eval_data`) before and after a
  fixed number of epochs, so the notebook trains once per entry of `RUNS`
  (epoch count and other `TrainConfig` values, or a seeded subset of
  train) and keeps the lowest validation log loss after
  the temperatures, since the policy answers only when confident and needs
  confidence that separates right from wrong, not accuracy alone. The
  first `useful` run (500 train, 4 epochs, no calibration split) went from
  0.50 zero-shot (`base`) to 0.79 on 100 validation questions, at a mean
  confidence of 0.98 (ECE 0.21), hence the calibration split and the
  shorter run. On 300 validation questions with 300 calibration, 4 epochs
  reached 0.78 against 0.74 for 2, but its temperature hit Laya's 5.0
  ceiling and it was 0.83 accurate even at confidence 0.9 or above (AUROC
  0.65, log loss 0.58); 2 epochs fitted 1.84 and was 0.94 accurate on the
  113 questions at 0.8 or above (AUROC 0.78, log loss 0.49). With 2, 3
  and 4 epochs chosen by log loss, 2 won again (0.489; 3 epochs 0.492,
  temperature 3.88). The sweep after it chose 3 epochs with label smoothing
  0.1 (log loss 0.471), with 2 epochs at 0.1 next (0.484), so the
  nine-kind run trains those two per kind. The experiments are recorded in
  the local wiki's `laya` pages.
- `useful` first, end to end through stage 6, before the other kinds; each
  of those starts once it has at least 100 production states.
- Code: `laya/train.py` and `laya/notebooks/train.ipynb`.

### Coverage by passage (owner, 2026-10-16)

`coverage` stayed near 0.70 accuracy and 0.77 class AUROC over three
retrains: more "no" data, more epochs, and the listed view. Two thirds of
its validation errors had a correct label. Its question asks whether several
passages together state a requirement. The owner chose to ask one passage at
a time instead, the shape `useful` learned.

- Kind `support` (`laya/support.py`, not a production question): does
  passage p state all, part or none of requirement r. Its view is the state
  with that one passage. It trains at 1,024 tokens, since a view is at most
  2,624 characters.
- The production `coverage` question is unchanged. Its answer is made from
  the answers over the state's listed passages:
  - yes when some passage states all of the requirement;
  - no when every passage states none of it;
  - undecided otherwise, which leaves it to the LLM.
- Candidates are derived from `coverage`'s labelled splits. Each question
  gives its listed passage sharing most words with the requirement, and one
  other listed passage at random; no label or verifier turn is read. A train
  row near a held view (gate, validation, calibration) is skipped. Planned:
  1,600 train, 300 validation and 300 calibration questions, labelled by
  guided labelling. The cap is $40.
- The notebook chooses the `support` run by its own validation log loss, then
  scores the chosen run on `coverage`'s 219 validation questions
  (`support.evaluate`): the decided rate, accuracy on the decided ones, and
  AUROC of the chance that some passage states all of it.
- First run (20261016-094429): `support` 0.75, but coverage made from it 0.662
  (AUROC 0.73), below the coverage model. It learned none and all, never part
  (1 of 42), so coverage decided 216 of 219 and one false all among a dozen
  passages made a false yes.
- Augmentation aimed at part (owner, 2026-10-16; cap raised to $42):
  `support.augment` makes about 800 train candidates. A trimmed candidate is a
  passage that stated all of a requirement, less the sentences a verifier
  quoted for that all. The quotes come only from train's own labelling turns.
  A runner candidate is a covered requirement's second or third passage by
  shared words. Both are labelled like any other candidate, and train then
  holds full1 plus that split.
- aug1 (800) and aug2 (3,410, the whole remaining pool: the owner asked for
  one large batch, not small ones) raised part to 20 of 42. all fell to 2 of
  17, and coverage made from that model never says yes. Train held 953 part
  against 472 all.
- Augmentation aimed at all (owner, 2026-10-17; cap $48): `support restate`
  asks every labelled all of the train splits again, of the same passage told
  another way:
  - its verifier-quoted evidence alone;
  - another listed passage of its coverage state before it;
  - another listed passage after it;
  - the evidence with another passage after it.

  That gives about 1,600 candidates, each still labelled.

## Calibration and policy

- One temperature per kind fitted on the calibration split (never on
  training items, unlike the notebook default; L2, L3). Report ECE before
  and after: `train_report.json` holds it before the temperatures, the
  notebook's `results.json` after (`train.score` hands back each answer's
  confidence).
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
