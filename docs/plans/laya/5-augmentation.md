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
   passages accumulate. Built so (2026-10-20):
   - Each slot draws a `context` (none 0.5, short 0.3, long 0.2) and a
     `length` (long 0.3, written 1,000 to 2,500 characters; the pilot's
     0.25 at 600 to 1,800 reached a query p90 of 615).
   - The teacher writes the turns before a question (`prior`). A long one
     starts with an assistant quoting the drawn pages, so it passes
     `STATE_CHARS` and is summarized.
   - The teacher writes the separate asks of a question that asks several.
     They answer the model split, so no translator request is sent.
   - The capture keeps up to three grade-evidence rounds.
   - `--batch` writes a round's ledger, runs and candidates to files of
     their own, held from every pool state.

   A free local capture showed all three: a split to three requirements, a
   summarized conversation, and rounds of 13, 15 and 17 passages.
   Before the batch, the same shape table on the new generator's first 50
   states shows it reaches the development pool's directions. Multi-part queries are already common, so the batch does
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
| Teacher's conversations, asks and long queries | in generation's output | not measured |

That is about $18.50 before the longer teacher output, under a $20 cap.
The model split sends no request (item 1). A pilot of 100 queries,
generated and labelled (about $1.40), comes first. It measures the cost
per query and per kept label, and the yield. The batch is then sized to
what fits the cap, and it stops at the cap with whatever it has kept.

Owner, 2026-10-20 (after #187):

- Budget. The pilot (batch `shape1`) spent $1.09 on 99 questions:
  generation $0.0084 a question, a kept coverage label $0.0020, a kept
  support label $0.00067. Its shape matched the development pool except
  query length (p90 615 against 1,173). The owner then approved the
  batch: 1,400 questions, cap $20, after long queries are written longer.
  It stops and reports if its first 50 states' shape does not improve.
  Its first 7 turns (`shape2`, $0.40) did not: query p90 274. One turn
  wrote `placeholder` for six questions, long slots fell short of 1,000
  characters, and the longest questions pasted Korean from the pages and
  failed normalization. Now a known stand-in or a repeated question is
  dropped (a short question is kept), pasted material is English too, a
  long conversation's quoted page lines in Korean are left out, long is
  counted in words, and a turn asks six questions, not eight. A turn whose
  JSON fails to parse (`Extra data`) is still lost; `shape3`'s first 50
  states show how often.
  `shape3` passed the check (query p90 1,193 over its first 50 states,
  `current_state` 0.56, `omitted_context` 0.18; no stand-in, no
  normalization failure) and ran to 169 turns, stopped by the owner after
  Claude Code twice ended the background run for low memory: 941
  questions, $10.70. 9 turns were lost to `Extra data`. 50 runs failed
  normalization: 25 Korean questions, where the teacher broke the English
  rule, and 25 English ones with a long context, whose summary carries a
  repository's Korean plans and decisions, which the capture does not
  translate. Kept labels: coverage 912 (yes 0.41), support 1,395 (none
  1,051, part 271, all 73), joint 1,491 from random passage subsets (yes
  0.05). The batch spent $15.06 of the $20 cap. Support's train now holds
  the pilot's and `shape3`'s labels: 18,688 rows. The batch restarts as `shape3`; `shape2`
  is not labelled, and its spend counts against the cap.
- Gate. The coverage gate was read four times on 2026-10-19, all
  diagnostics with the same checkpoints (stage 6, Results). Support ran
  at 1,024 tokens, then at 2,048, a length chosen from the gate's view
  lengths, and each run was recounted by unit. Its 94 units cannot meet
  the 100-unit rule. The next gate is a fresh coverage reservation of at
  least 100 units from production states recorded after the pool
  (2026-10-13). It fills by ordinary use: on 2026-10-20 there were 10 such
  states, 6 units. Until it is reserved, `laya/pool.py` is not run again,
  so none of those states enters the pool or development first.
- Jev. Jev's side of the gate is its recorded answers under the old
  wording. Since #184 production asks arm B's wording, so Jev's B answers
  are collected on the fresh gate's states once they are reserved, with
  their own budget.
- Results. Retrained on `shape3`, support (2,048 tokens) and joint each
  gained less than the noise floor on the generated validation (support
  accuracy 0.859 → 0.857; coverage from support and joint 0.744 → 0.767,
  floor 0.042), so the incumbents stay.
- Direct wording (owner, 2026-10-20). Support and joint questions quote the
  requirement, as Jev's adopted arm B does (`support.question`,
  `joint.question`), where they named it only by id. The labels are
  unchanged, so the stored questions of every split are reworded, not
  relabelled. One Colab run retrains both on the same data as the
  `shape3` runs, so the paired comparison against them isolates the
  wording, by the adoption rule above. Rejected, the wording is reverted
  with the data.

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
