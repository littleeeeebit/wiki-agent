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

## Coverage: production-shaped states (plan, 2026-10-20)

Why. Coverage (support, then joint) failed its stage 6 gate (2026-10-19):
accuracy 0.790 against Jev's 0.967 at 2,048 tokens, with 1 of 10 covered
requirements found. Its validation (0.78 support, about 0.72 coverage) did
not predict that, because every train and validation state is generated
and production's are shaped differently. Measured on the production pool's
coverage states **outside the gate** (73 states; the gate chooses nothing
here):

| Shape | Train (1,699) | Validation (165) | Pool outside the gate (73) |
| --- | --- | --- | --- |
| `current_state` present | 0% | 0% | 60%, median 1,832 characters, max 3,950 |
| `omitted_context` present | 0% | 0% | 40% |
| Query length p90 / max | 266 / 452 | 257 / 328 | 1,083 / 1,995 |
| Passages max | 13 | 13 | 37 |
| State characters p50 / p90 | 13,851 / 16,796 | 14,676 / 17,732 | 15,177 / 21,333 |
| Yes rate | 0.53 (balanced) | 0.47 | (unlabelled; the gate's is 0.05) |

The generator (`laya/generate.py` `captured`) runs `knowledge.prepare` with
an empty conversation, leaves the query unsplit, and stops at the first
grade-evidence request. So no generated state has conversation context, an
omitted part, a split query or a later round's passages.

Changes, one batch sized to the pool (not top-ups):

1. Validation and calibration from production. Label the pool's coverage
   states outside the gate, after `gate.Near` drops any near a gate state,
   with guided labelling (stage 3). They replace generated validation as the
   measure for support and joint; their natural yes rate is kept. Too few
   for calibration alone (73 states), so calibration stays the generated
   split until the pool grows.
2. Generator. `laya/generate.py` writes the conversation production sees:
   a prior exchange passed as `state` to `prepare` (so `current_state` and,
   past `STATE_CHARS`, `omitted_context` are production's own), queries
   that paste material or quote someone else's question, multi-part
   queries that `Flow.split` splits, and repair rounds whose passages
   accumulate. Slices weighted hard, by the owner's rule: numbers, dates,
   literal wording, the longest states, and Jev's documented weak spots
   (`.wiki/laya-insight-jev-usage.md`).
3. Balance. Train is balanced by class; validation and calibration keep the
   production rate.
4. Length. support trains and serves at 2,048 tokens, where every gate view
   fits (522 of 1,415 views exceeded 1,024).
5. Retrain support, then joint over support's picks; measure on the
   production validation.

Budget, estimated from earlier runs (generation $4.72 for 708 queries;
labels about $0.0005 each; Codex Sol on the subscription): 1,500 queries
about $10.50, about 4,000 support views $2, about 1,500 joint labels $0.75,
production validation about $0.50. Cap $20, approved before anything is
sent; nothing above is approved yet.

Open, for the owner before the next gate:

- The gate has been read once for coverage. Slices here come from the pool
  outside it, but the next gate read is a second look at the same states.
  Accept that (reported as such), or reserve a fresh gate from production
  states recorded after 2026-10-19.
- Jev's side of the gate is its recorded answers under the old wording.
  Since #184 production asks arm B's wording, so the next comparison needs
  Jev's B answers on the gate states (paid; stage 11's incomplete gate run
  holds part of them).

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
