# Stage 5 — augmentation from validation errors

See the [overview](0-overview.md). After [stage 4](4-training.md).

Purpose. Add training states where a kind's model is weakest, as the owner
asked: train, find what validation gets most wrong, add data on that
subject, train again.

## Loop, per kind

1. Errors. Run the model on validation. Group the errors into slices by
   features the state already carries: the kind's answer, the source
   family, the option count, state length, negation or numbers in the text,
   and the generator's topic.
2. Pick. The slice with the most errors that holds at least 10 of them.
3. Generate. A teacher LLM writes new states for that slice (L5), through
   stage 2's builders: 300 per round by default.
4. Label. Stage 3's agreement rule; the human cap applies.
5. Retrain from the base on all data, recalibrate on the same calibration
   split, and evaluate on validation.

Stop when validation accuracy gains less than one percentage point, or after
three rounds. Test never chooses a slice or a stopping point.

## Acceptance

Per kind and round: the slice chosen, states added, spend, and validation
accuracy before and after. A round that lowers validation is reverted.

## Tests

`tool/test_laya_augment.py`: slice grouping, minimum size, stop rules,
revert on regression, test states never used.

## Rollback

Drop the round's states and keep the previous checkpoint.
