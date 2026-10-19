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

Why. The gate reads of 2026-10-19 put coverage (support, then joint) well
below Jev: 0.800 against 0.961 (stage 6, Results). Under the current unit
rule the verdict is inconclusive, not fail: 94 units, under 100. Validation
(0.78 support, about 0.72 coverage) did not predict the gap. The gate read
started this round, and its view lengths chose 2,048 tokens, so the gate
has shaped this plan. The rule "the gate test never chooses a slice" holds
only for what follows: the shapes below are measured on the development
pool, never on gate states.

Every train and validation state is generated, and generated states are
shaped differently from production's. That is a hypothesis for the gap,
not its proven cause: the label rates, support's pick distribution,
calibration and the wording Jev was asked also differ. The development
pool is every pool state outside the reserved units
(`gate.development(pool, gate.reserved())`, whole transitive near-units
left out), with a coverage question. It holds 20 states, 43 questions and
14 units, recorded 2026-10-01 to 2026-10-12. That is too few to fix
numbers, only directions. Lengths are characters of
`json.dumps(state, ensure_ascii=False)`; a median is `statistics.median`,
and p90 is the sorted value at index `int(n * 0.9)`.

| Shape | Train (1,699) | Validation (165) | Development pool (20) |
| --- | --- | --- | --- |
| `current_state` present | 0% | 0% | 50%; of those, median 2,391 characters, max 2,954 |
| `omitted_context` present | 0% | 0% | 20% |
| More than one requirement | 62% | 60% | 60% |
| Query length p90 / max | 266 / 452 | 257 / 328 | 1,173 / 1,812 |
| Passages max | 13 | 13 | 30 |
| State characters p50 / p90 | 13,851 / 16,796 | 14,676 / 17,732 | 14,317 / 31,286 |
| Yes rate | 0.53 (balanced) | 0.47 | unlabelled; the gate's is 0.06 |

The generator (`laya/generate.py` `captured`) runs `knowledge.prepare` with
an empty conversation, answers the translator's request with nothing (so
the model split, `Flow.split` through `translate.parts`, never runs), and
stops at the first grade-evidence request. The punctuation split
(`knowledge.requirements`) still runs, and `laya/prompts/generate.md`
already asks for multi-part questions and pasted material. What generated
states lack is conversation context (`current_state`, `omitted_context`),
the model split, later repair rounds' passages, and queries as long as
production's.

Changes, one batch sized to the pool (not top-ups):

1. Generator. `laya/generate.py` writes the conversation production sees:
   a prior exchange passed as `state` to `prepare` (so `current_state` and,
   past `STATE_CHARS`, `omitted_context` are production's own), the model
   split on, longer queries that paste material, and repair rounds whose
   passages accumulate. Before the batch, the same shape table on the new
   generator's first 50 states shows it reaches the development pool's
   directions. Multi-part queries are already common, so the batch does
   not add more of them. Slices weighted hard, by the owner's rule:
   numbers, dates, literal wording, the longest states, and Jev's
   documented weak spots (`.wiki/laya-insight-jev-usage.md`).
2. Holds. Derived support and joint views are held as today:
   `support.held_for(split)` and `support.held_rows` for support, and
   `joint.picked`'s `held` for joint. Every reserved gate state is held,
   whether its label was kept or not. Train is also held from validation
   and calibration. Holds are per view: of the development pool's 293
   one-passage support views (every passage of each state, through
   `kinds.view`), 64 are near a row in `support.held_for("validation")`. Those
   views are left out as label candidates only. A coverage state is still
   evaluated whole, with every passage it had. The development
   pool joins the held rows for train; one current train state is near
   one of its states.
3. Validation. The new generated validation keeps its own rate. It decides
   adoption as before. The development pool is labelled (guided labelling,
   stage 3) at its natural rate and reported beside it. It decides nothing
   until it has at least 50 units, 10 yes questions and 20 questions that
   reach joint. Below that it is `inconclusive`. Support alone and support
   then joint are compared on the same states, with intervals from a
   bootstrap over units, not questions. A rare-positive diagnostic set,
   if added, stays apart from both natural-rate sets.
4. Calibration. Stays the generated calibration split. Its temperature
   (`laya/train.py`, `laya/calibrate.py`, one scalar per type) does not
   correct the prior shift from a 0.5 to a 0.06 yes rate. Calibration on
   production is unverified until a production calibration sample exists,
   a separate set from validation.
5. Balance. Train is balanced by class.
6. Length. support trains and serves at 2,048 tokens, where every gate view
   fits (522 of 1,415 views exceeded 1,024). That figure came from the gate,
   as Why says.
7. Retrain support, then joint over support's picks; measure as in 3.

Budget, per kept label from the last full runs, with the verifier (Codex
Sol) on the subscription. Generation was $4.72 for 708 queries. A support
label cost about $0.0014 per kept label ($2.59 for 1,850 kept, 4,382
calls). A joint label cost about $0.0016 per kept label ($3.51 for 2,159
kept, 6,628 calls).

| Item | Kept | Estimate |
| --- | --- | --- |
| Generation | 1,500 queries | about $10.00 |
| Support labels | 4,000 kept | about $5.60 |
| Joint labels | 1,500 kept | about $2.44 |
| Development pool labels | 43 questions, their views | under $0.50 |
| Model split and longer states | not measured | unknown |

That is about $18.50 before the new calls, under a $20 cap. A pilot of 100
queries, generated and labelled (about $1.40), comes first. It measures the
cost per kept label, the yield and the model split's calls. The batch is
then sized to what fits the cap, and it stops at the cap with whatever it
has kept. Nothing is approved yet. The pilot and the batch each need the
owner's approval.

Open, for the owner before the next gate:

- Exposure. The coverage gate was read four times on 2026-10-19, all
  diagnostics with the same checkpoints (stage 6, Results). Support ran
  at 1,024 tokens, then at 2,048, a length chosen from the gate's view
  lengths, and each run was recounted by unit. Its 94 units cannot meet the 100-unit rule whatever else is decided.
  Either add reserved units from production states recorded after the
  pool (2026-10-13), or reserve a fresh coverage gate of at least 100
  units from them. Either way, reserve them before any of those states
  enters the pool or development, and report the old units' exposure
  beside the verdict.
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
