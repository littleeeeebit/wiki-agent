# Stage 5 — augmentation from validation errors

See the [overview](0-overview.md). After [stage 4](4-training.md).

Purpose. Add training states where a kind's model is weakest, as the owner
asked: train, find what validation gets most wrong, add data on that
subject, train again.

## Loop, per kind (`laya/loop.py`)

The loop follows RRSI (Regularized Recursive Self-Improvement,
google-research/rrsi): every attempt is recorded and read back, a candidate
is adopted only when its gain clears the evaluation's noise, a falsified
hypothesis is never retried, a stall turns the search to what has not been
tried, components that stop helping are removed, and held-out data never
enters selection.

1. Errors. Run the incumbent on validation; each error is a
   `student_wrong` line of the error notebook ([stage 3](3-labels.md)).
   Group the errors into slices by features the state already carries: the
   kind's answer, the option count, state length, negation or numbers in
   the text, and the generator's topic.
2. Pick. The slice with the most errors (at least 10) that the notebook
   does not mark `falsified` or `ambiguous`; after a stall, a feature not
   tried since, when one qualifies.
3. Generate. A teacher LLM writes new states for that slice (L5), through
   stage 2's builders, with each question's intended answer: 300 per round
   by default. It reads the whole history first (every notebook page
   whatever its status, every cluster's size, every round's record, the
   slices never to retry) and records `off_slice` and `leak` drops.
4. Label. Stage 3's agreement rule. If the labellers agree on under half
   the slice's questions, it is `ambiguous`: not adopted, a stall.
5. Retrain from the base on the incumbent's data and this round,
   recalibrate on the same calibration split, and score validation.
6. Adopt when the paired accuracy gain over the incumbent is above the
   noise floor (two bootstrap standard deviations of the paired
   difference, resampling whole states, since one state's questions move
   together). Otherwise the slice is `falsified`: not adopted, a stall,
   and the loop goes on to the next round.
7. Prune. After the last round, retrain without each adopted round; drop it
   when keeping it gains no more than the noise floor.

At most three rounds; the loop stops early when no slice qualifies. The gate
test never chooses a slice, an adoption or a stopping point. Validation now
chooses the base, the stopping epoch, the slices and the adoptions, so its
accuracy is optimistic; only the gate test is reported as the result.

## Budget

Each round is paid twice: teacher generation for 300 states and two labels
for each. At most three rounds × fourteen kinds. Before the first round the
owner approves one total augmentation limit (calls, tokens, spend) from the
pilot's cost per state and per label. Each round is checked against what is
left before it sends anything; a round that does not fit is not started.
Spend is recorded per round. A round not adopted or pruned leaves its data
and checkpoint unused, not its spend. Notebook pages are diagnosed by one
host turn each, within the same limit. In `laya/loop.py` the limit is
`max_turns` host turns shared by generation, labelling and note writing; a
round starts only when `round_turns`, measured in the pilot, is left, and
notes take only what the round leaves. Labelling that stops short (the turn
limit, or too few questions judged because turns failed) stops the loop
with the slice still `targeted`; it is never read as an ambiguous slice,
and a rerun resumes it. A rerun restores the loop from its own records:
the kept splits, the stalled features, and an unfinished round with its own
slice and split; a new round takes the next number, so one split never
holds two slices, and every candidate carries the slice it was aimed at.
Labelling writes each state's verdict before its labelled row and notes,
and the loop journals each round's intent before any paid turn and each
outcome before its page status; a torn last line is never committed. An
interruption is finished on the rerun, never counted twice or lost
([recovery research](../../research/laya-loop-recovery.md)).

## Acceptance

Per kind and round, in `laya/data/<kind>/rounds.jsonl`: the slice chosen,
labelling stats, the gain and noise floor, adopted or why not, and each
prune. The notebook's index shows every slice's status.

## Tests

`laya/test_laya.py` with stub training and scoring: the noise floor
separates a real gain from noise; a round adopted, one falsified, one
ambiguous; exploration after a stall; prune; falsified and ambiguous slices
in the generator's brief.

## Rollback

Drop the round's states and keep the previous checkpoint.
