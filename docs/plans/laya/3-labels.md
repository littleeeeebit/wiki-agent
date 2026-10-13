# Stage 3 — labels by two-labeller agreement

See the [overview](0-overview.md). After [stage 2](2-data.md).

Purpose. Give every training state a label two independent LLMs agree on,
and send the rest to a person.

## Rule

- Two labellers from different model families each answer the kind's
  question for the state, with the same instructions Jev receives, and give
  a probability.
- Agreement: same answer, both probabilities at least 0.7. Agreed states
  train with that answer.
- Disagreement, or agreement below 0.7: the human queue. A person's label
  trains; a state the person marks ambiguous is dropped.
- Human labels on real states (stage 2) go to test only.
- Each label records labeller models, prompts, probabilities and, for a
  person, who and when.

## Pilot, then the full run

1. Pilot: 200 states per kind (2,800). Measure agreement rate, human queue
   size, cost, and agreement with the existing labelled sets where they
   overlap.
2. The owner approves the full run with those numbers and a per-kind human
   cap. Beyond the cap, disagreed states are dropped, and the drop rate per
   kind is reported.

## Tool

`tool/eval/laya_label.py`: `label <kind>` runs both labellers within a
budget (calls, tokens, seconds) and resumes without paying twice;
`review <kind>` shows the queue one state at a time in the terminal.

## Acceptance

- Every training state is agreed, human-labelled or dropped, with
  provenance.
- Reported per kind: agreement rate, human count, drop count, spend.

## Tests

`tool/test_laya_label.py` with stubbed labellers: agreement threshold,
resume without paying twice, budget stop, queue routing, test states never
reaching the training output.

## Rollback

Labels are files beside the data; delete them. No product code changes.
