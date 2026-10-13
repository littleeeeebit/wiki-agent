# Stage 3 — labels by two-labeller agreement

See the [overview](0-overview.md). After [stage 2](2-data.md).

Purpose. Give every train, validation and calibration state a label two
independent LLMs agree on, and send the rest to a person, until each kind
has 3,000 usable train labels, 300 validation and 300 calibration. The gate
test is labelled by a person only (stage 2).

## Rule (all three splits)

- Two labellers from different model families each answer the kind's
  question for the state, with the same instructions Jev receives, and give
  a probability.
- Agreement: same answer, both probabilities at least 0.7. An agreed state
  is usable with that answer.
- Disagreement, or agreement below 0.7: the human queue. A person's label
  makes it usable; a state the person marks ambiguous is dropped.
- Validation and calibration take the same rule. The same two labellers are
  used for all three splits, so their cost is in the budget below.
- Each label records labeller models, prompts, probabilities and, for a
  person, who and when.

## Completion and refill

- A split is complete when it holds its target of usable labels (train
  3,000, validation 300, calibration 300).
- A dropped state, or one beyond the kind's human cap, is replaced from
  stage 2's 20% surplus, labelled under the same rule.
- If the surplus runs out before a split is complete, the kind stops at
  stage 3 and is reported with its counts. It does not train on fewer.
  Generating more needs the owner's approval as a new budget.

## Budget

Per kind, labels for 3,600 states at most (the targets plus surplus) × two
labellers, plus the human queue. With about 2k input tokens a label, that is
about 14.4M input tokens a kind and about 200M for fourteen, before
augmentation. The real figure comes from the pilot.

## Pilot, then the full run

1. Pilot: 200 states of the kind, with the owner's approval of its
   budget. Measure agreement rate, human queue size, cost per label, and
   agreement with human labels on development cases.
2. The owner approves the full run with those numbers, a total spend limit
   and a per-kind human cap. The tool stops at the limit; spend already made
   is reported, not undone.

## Tool

`tool/eval/laya_label.py`: `label <kind>` runs both labellers within a
budget (calls, tokens, seconds) and resumes without paying twice;
`review <kind>` shows the queue one state at a time in the terminal.

## Acceptance

- Each kind's three splits are complete with usable labels and
  provenance, or the kind is stopped and reported.
- Reported per kind and split: agreement rate, human count, drop count,
  refills, spend.

## Tests

`tool/test_laya_label.py` with stubbed labellers: agreement threshold;
validation and calibration labelled under the same rule; refill from
surplus after drops; stop when the surplus runs out; resume without paying
twice; budget stop; queue routing; gate test states never reaching any
split.

## Rollback

Labels are files beside the data; delete them. No product code changes.
