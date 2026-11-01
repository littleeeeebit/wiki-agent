---
scope: project
severity: preference
triggers: ["(?i)laya.*(shape|모양|shape1).*(pilot|파일럿)"]
reads: [laya/generate.py, laya/prompts/generate.md, laya/guided.py, docs/plans/laya/5-augmentation.md]
slots: []
sources: []
links: [laya, laya-exp-retrieval-full, laya-insight-jev-usage]
---

# Coverage production-shaped pilot — batch `shape1`, 2026-10-20

Part of [laya](laya.md). The owner approved the pilot only (about $1.40);
the batch (cap $20) needs its own approval. The generator is the one merged
in #189 (01f9947): a slot draws a conversation and a length, the teacher
writes the turns before a question and the asks of one that asks several,
and the capture keeps up to three judge rounds. Plan:
`docs/plans/laya/5-augmentation.md`, "Coverage: production-shaped states".

## Generation

The command was `python laya/generate.py train --batch shape1 --kinds coverage --target 1000 --teacher sonnet --max-turns 13`.

- 13 teacher turns (claude-sonnet-5-5), $0.829 in total, $0.064 a turn.
  99 questions ran, $0.0084 a question run; the last full run was $0.0067
  (708 for $4.72), so the longer teacher output costs about a quarter more.
- 127 coverage questions kept in 117 rows. 61 rows were near a kept one
  (`near_kept`), mostly a later round repeating an earlier round's view.
  None was near a pool state; one request was another split's.

Shape, the plan's measures (lengths in characters; p90 at index `int(n * 0.9)`):

| Shape | Pilot, first 50 | Pilot, all 99 | Development pool (20) |
| --- | --- | --- | --- |
| `current_state` present | 52% | 53% | 50% |
| `omitted_context` present | 24% | 19% | 20% |
| More than one requirement | 52% | 57% | 60% |
| Query p90 / max | 615 / 1,011 | 665 / 1,011 | 1,173 / 1,812 |
| Passages max | 38 | 38 | 30 |
| State characters p50 / p90 | 13,709 / 18,357 | 13,721 / 18,242 | 14,317 / 31,286 |

The conversation, the omitted part, the split and later rounds' passages
now reach the development pool's shares. Queries are longer than the old
generator's (p90 266) but still about half of production's, and the state
p90 is short of production's long tail.

## Labelling

Coverage: `python laya/guided.py coverage shape1 --rows shape1-train.candidates.jsonl --labeller haiku --verifier codex:gpt-6.1-sol --rounds 1 --per-round 117 --max-usd 0.55`.

- One round, the base guide: 117 Haiku turns ($0.176) and 6 Sol verifier
  turns (subscription, no cost recorded).
- 89 of 127 questions kept (70%): 10 unsure, 28 rejected, none
  unreliable. $0.0020 a kept label, $0.0014 a question asked.
- Kept answers: 39 yes, 50 no (0.44 yes).

Support: 172 rows, 174 questions derived from the 89 kept rows (`support.derive`
with `support.held_for("train")`, called from a scratch script since the CLI
derives only train, validation and calibration), labelled with
`--rounds 1 --per-round 172 --max-usd 0.35`.

- 172 Haiku turns ($0.089), 8 Sol verifier turns (subscription).
- 132 of 174 kept (76%): 17 unsure, 25 rejected. $0.00067 a kept label,
  half the last full run's $0.0014, which paid for guide trials over
  several rounds.
- Kept answers: none 99, part 27, all 6.

## Cost and the batch

The pilot spent $1.094 of its $1.40: generation $0.829, coverage labels
$0.176, support labels $0.089. Joint was not run.

Per question generated: 1.28 coverage questions asked, 0.90 kept; about
1.8 support questions asked, 1.33 kept. Generation is $0.0084 and the two
label kinds about $0.0027, before joint (the last full run's $0.0016 a kept
label) and before guide trials in later rounds. At the plan's 1,500
questions that is about $12.60 generation, $2.70 coverage, $2.70 for
4,000 kept support labels with more views a question, and $2.40 for 1,500
joint labels: about $20.40, just over the $20 cap. 1,400 questions fit
with the batch stopping at the cap.

## Batch `shape2`, first run (stopped)

The owner approved 1,400 questions (cap $20) after #190 made long queries
1,000 to 2,500 characters and 0.3 of slots, to stop if the first 50
states' shape did not improve. The first 7 turns ($0.402) did not:

- Query p90 274 over 40 states, below the pilot's 615.
- Turn seed 3 wrote `placeholder` for 6 of its 8 questions, which ran and
  gave candidates. Turn seed 5 did not parse (`Extra data`).
- 7 of 48 slots were long, and only 3 of those reached 1,000 characters.
  In the pilot it was 2 of 24, so the teacher undershoots the length.
- The longest questions copy Korean from the pages into pasted material.
  The capture's translator answers nothing, so they end
  `normalization_failed` (3 here, 5 in the pilot), and the longest states
  are the ones lost. Production would have translated them.

The `shape2` rows are not to be labelled as they stand: the placeholder
questions are in them.

## Generator fixes before `shape3` (PR #192, merged as 809a675)

- A known stand-in (`placeholder`, `todo`, ...) or a question repeated in
  the turn is dropped, in any slot. Length alone drops nothing: review
  round 1 found a 20-character floor lost short follow-up lookups
  ("Which port?") and still let a chat-slot placeholder through.
- The prompt asks for English pasted material and `prior`, no stand-ins,
  and long as 170 to 400 words. A turn asks 6 questions, not 8.
- Review round 1 also found Korean entering through code, not the
  teacher: a long conversation quotes page excerpts as `assistant:` turns,
  and the pilot's `t11-q0`, `t12-q1`, `t12-q2` failed normalization with
  English questions and Korean in their state. Quoted lines in Hangul or
  CJK are now left out. Round 2 found that a raw Hangul/CJK range also
  matched CJK punctuation and dropped English lines, so a quoted line is
  kept when `common.language.language` reads it as English, the check
  normalization itself uses.
- Still open: a turn whose JSON has `Extra data` is lost. `shape3`'s
  first 50 states show how often, with the shape table.

## Batch `shape3`, first 9 turns (passed the check)

9 turns, 54 questions, $0.515 ($0.0095 a question), 71 coverage
candidates. Every turn parsed; no stand-in; no `normalization_failed`
(`shape2` 3, `shape1` 5). One state holds Hangul, inside a code span,
which production reads as English too.

| Shape | `shape3` first 50 | Development pool (20) |
| --- | --- | --- |
| `current_state` | 0.56 | 0.50 |
| `omitted_context` | 0.18 | 0.20 |
| More than one requirement | 0.60 | 0.60 |
| Query p90 / max | 1,193 / 1,388 | 1,173 / 1,812 |
| Passages max | 25 | 30 |
| State characters p50 / p90 | 13,940 / 17,723 | 14,317 / 31,286 |

Long slots: 15, of which 9 are under 1,000 characters (476 to 1,388), so
the teacher still writes short of the asked length, but the query p90 now
meets the pool's. The state p90 stays below the pool's: production's
longest states are its repair-heavy ones.

At 59 turns ($3.64, 336 questions), 2 turns (seeds 30, 42) were lost to
JSON `Extra data` ($0.13): about 3% of turns, paid and empty. Spend runs
$0.062 a turn, so 200 turns cost about $12.3, and the labelling cap is
what is left under $20.

The second run stopped at 115 turns in all ($7.20, 637 questions, 847
coverage candidates): the search daemon was briefly unavailable
(`search_unavailable`), and a rerun resumes. Shape over 600 states:
`current_state` 0.46, `omitted_context` 0.17, query p90 1,147, passages
max 39, state p90 19,809. 26 runs failed normalization (first counted
as 29 by a raw-line grep, see below):

- 9 are Korean questions: the teacher broke the English rule, a whole
  turn (seed 78) at once.
- 19 are English, with a long context: past `STATE_CHARS`,
  `knowledge.summarized` adds the repository's plans and decisions, in
  Korean in `ai-coding-agent-wiki-public` and `wiki-agent`. Production
  translates them; the capture's translator answers nothing. So 19 of
  125 long-context questions are lost, from two repositories: a skew in
  which repositories' long states survive, not a loss of the shape.

Generation ended at 169 turns (owner's call, 2026-10-20): $10.70, 941
questions, 9 turns lost to `Extra data`, 1,089 coverage candidate states
(1,259 questions), 50 runs `normalization_failed` (counted by the run's
`error` field; a raw-line grep gave 53, as three successful captures
quote the string in a passage): 25 Korean questions (none 14, short 6,
long 5) and 25 English ones with a long context. Two more
`search_unavailable` stops were rerun; then Claude Code stopped the
background run twice for low system memory while the session was idle,
and the owner chose to label what was there. Seed 169 stays `pending`
in the ledger, unpaid as far as the ledger shows. Batch spend so far:
`shape2` $0.40 + `shape3` $10.70 = $11.10 of the $20 cap.

Coverage labelling: `guided.py coverage shape3`, one round over all
1,089 states, cap $2.50. Kept 912 of 1,259 questions (72%; 139 unsure,
208 rejected) in 827 states, $1.80 (56 calls of unknown cost), $0.0020 a
kept label as in the pilot. Yes rate 0.41 (371 / 541), so the batch is
not balanced and train balancing (plan item 5) applies.

Support: `support.derive` over the kept rows, held from train's holds,
gives every pair there is: 1,784 questions in 1,727 views
(`support/shape3.candidates.jsonl`), labelled with a cap of $1.50. Kept
1,395 (78%; 162 unsure, 227 rejected) in 1,362 views, $0.93 (82 calls of
unknown cost): none 1,051, part 271, all 73.

Joint: `joint.subsets` over the kept coverage rows, sizes from
`picks-train.jsonl`'s 800 undecided picks, held from train's holds and
the other splits' joint rows: 1,806 questions in 1,801 views
(`joint/shape3.candidates.jsonl`), labelled with a cap of $3. Spend
before it: $13.83 of $20. Kept 1,491 (83%; 88 unsure, 227 rejected) in
1,487 views, $1.23 (86 calls of unknown cost). Only 79 are yes (5%):
a random passage subset rarely covers a requirement, so joint's new rows
are nearly all negatives, and balancing leaves few of them.

Batch total: $15.06 of $20 (`shape2` $0.40, generation $10.70, coverage
$1.80, support $0.93, joint $1.23), plus about 224 calls whose cost was
not reported. Kept: coverage 912, support 1,395, joint 1,491. Next in the
plan: retrain support, then joint over support's picks (item 7).

## Support retrain at 2,048 tokens (packed 2026-10-20)

- `support/train.jsonl` is now `v6-train.jsonl` (17,195 rows, kept
  byte for byte) with `shape1.jsonl` (131) and `shape3.jsonl` (1,362)
  appended: 18,688 rows, none 14,579, part 4,350, all 1,809. The
  appended rows are near no validation, calibration, gate or development
  pool view (PR #195 review).
- PR #195 (merged as 2f6f0be): `train.MAX_LEN["support"]` 2,048, read by
  training, scoring, `support.evaluate`, `joint.evaluate` and `jev_gate`.
  Micro-batch stays 4; the L4 record (7.8 GB peak at 2,048 for `useful`)
  gives no reason to lower it.
- `laya/data/colab.zip` packed at 2f6f0be with `support` only (train
  hash 4e7e652453). The previous zip (joint, a011636) is kept as
  `colab-joint-a011636.zip`.
- Then: score the new support on validation and as coverage, rebuild
  joint's picks from it, and retrain joint with `joint/shape3.jsonl`.

## Joint train with shape3 (owner's call, 2026-10-20)

Adding all 1,491 `shape3` joint questions would drop train's yes rate
from 0.40 to 0.29 and break its serving shape (yes rate by passage count
matching validation's). The owner chose: every `shape3` yes row, plus no
rows drawn (seed `shape3-joint`) to hold each passage-count bin's rate.

| Passages | Train rate | `shape3` yes | No added |
| --- | --- | --- | --- |
| 1 | 0.17 | 13 | 63 |
| 2 | 0.27 | 12 | 32 |
| 3 | 0.39 | 7 | 11 |
| 4+ | 0.60 | 47 | 31 |

`joint/train.jsonl`: 3,350 rows, 3,495 questions, yes 0.397; the added
rows carry `provenance.origin` `shape3`. The previous train is kept as
`train.v4-serving.jsonl`. `shape3`'s views were drawn held from train's
holds and every other split's joint rows (`joint.others`). Against the
$20 cap: `shape2` $0.40, generation about $11.5, labelling about $6.5
(coverage, support, joint at the pilot's rates).

## Support retrain result (2026-10-20, run 20261020-072747)

Packed at 2f6f0be, train 18,688 rows (20,738 questions), 2,048 tokens.
e3 chosen by log loss. Fine-tuning took 82 and 120 minutes on the L4,
8.2 GB peak. The checkpoint is on Drive as
`support-train-e3-ls0.1-20261020-072747-k4ypyxls.zip`.

| Support on 1,772 validation | Incumbent 211003 e3 (1,024) | New e3 (2,048) |
| --- | --- | --- |
| Accuracy | 0.859 | 0.857 |
| Log loss | 0.472 | 0.469 |
| ECE | 0.008 | 0.017 |
| none | 1,383 / 1,450 | 1,390 / 1,450 |
| part | 117 / 254 | 114 / 254 |
| all (precision) | 22 / 68 (22 / 39) | 15 / 68 (15 / 21) |

| Coverage made from support | Decided | Accuracy decided | TPR / TNR decided | AUROC | Undecided as no |
| --- | --- | --- | --- | --- | --- |
| Incumbent 211003 | 114 | 0.842 | 0.77 / 0.90 | 0.742 | 0.662 |
| New 072747 | 110 | 0.836 | 0.59 / 0.97 | 0.752 | 0.626 |

- Validation is flat: accuracy -0.002, inside any noise floor, so by
  stage 5's adoption rule the new support is not adopted. The paired
  bootstrap was not run: the incumbent's per-question predictions are no
  longer local (its result zip was removed from `Downloads/res`), and a
  negative gain cannot pass it anyway.
- The new model says all less often and more precisely (21 calls, 15
  right). Coverage made from it has 2 false yes against 7, and 16 false
  no (9 of them with 12 listed passages). TNR 0.97 is the error that
  matters at the gate's 0.06 yes rate, but validation is the generated,
  old-shape split, and the gate never chooses an adoption.
- Validation is not production-shaped, so it cannot show what `shape3`
  was for. Only the fresh gate can, once at least 100 units are reserved.

Raw files: `Downloads/res/result-support-20261020-072747.zip` and
`result-setup-20261020-072747.zip` (local).

Owner, 2026-10-20: keep the incumbent support (211003) and retrain joint
only. `laya/data/colab.zip` was packed at 2f6f0be with `joint` only: train
3,350 rows (hash 09c5a970f2), with validation, calibration and
`picks-validation` the same bytes as the a011636 bundle, so the result
compares directly with the last joint run. The support bundle is kept as
`colab-support-2f6f0be.zip`.

## Joint retrain with shape3 rows (2026-10-20, run 20261020-114125)

Train 3,350 rows (3,495 questions), e3 chosen by log loss. The incumbent
is run 20261019-060734 (bundle a011636, the same validation,
calibration and `picks-validation` bytes). Paired over the same
questions, bootstrap over states (4,000 draws, seed 0, scratchpad
`paired.py`):

| Measure | Incumbent 060734 | New 114125 | Gain | Noise floor (2 sd) |
| --- | --- | --- | --- | --- |
| Joint validation accuracy (435) | 0.715 | 0.720 | +0.005 (31 fixed, 29 broken) | 0.035 |
| Joint log loss / ECE | 0.610 / 0.087 | 0.584 / 0.049 | | |
| Coverage from support 211003 + joint (219) | 0.744 | 0.767 | +0.023 (13 fixed, 8 broken) | 0.042 |
| Coverage TPR / TNR | 0.64 / 0.84 | 0.71 / 0.82 | | |
| Coverage AUROC | 0.810 | 0.820 | | |
| Joint on the 105 questions support left | 0.638 | 0.686 | | |

- Every gain is below the noise floor, so by stage 5's rule the new joint
  is not adopted; the incumbent pair (support 211003, joint 060734)
  stays. Calibration did improve: log loss -0.026, ECE nearly halved.
- With the support result above, the `shape3` batch ($15.06) moved
  nothing measurable on the generated validation for either model. That
  split is old-shape, so this does not test the batch's purpose. That
  test is the fresh coverage gate (at least 100 units of post-2026-10-13
  states; 6 on 2026-10-20), where both new checkpoints can be read as
  diagnostics beside the incumbents.
- Checkpoint on Drive: `joint-train-e3-ls0.1-20261020-114125-rdv7ccfr.zip`.

Raw files: `Downloads/res/result-joint-20261020-114125.zip` and
`result-setup-20261020-114125.zip` (local).

## Direct wording for support and joint (owner, 2026-10-20, PR #197)

Jev's adopted arm B quotes the requirement; Laya's support and joint
named it only by id. The owner chose to test the same change on Laya
while the fresh gate fills. `support.question(state, rid, pid)` and
`joint.question(state, rid)` now open with `Requirement {rid} asks:
"{text}".`

- Labels do not depend on the wording, so the stored questions of all
  six splits (support and joint train, validation, calibration) were
  rewritten with scratchpad `reword.py`, the old files kept as
  `*.v-ids.jsonl`: 20,738 + 1,772 + 225 support and 3,495 + 437 + 398
  joint questions. The longest requirement adds 1,345 characters
  (`shape3`'s long queries), so some views may now pass `MAX_LEN` and be
  left out; the run's `left_for_length` shows how many.
- Measure: one Colab run of both kinds on the same data as the `shape3`
  runs, paired against support 072747 and joint 114125, with the same
  noise floor. Rejected, the wording and the data go back together.
- PR #197 merged as 43775fd (two review rounds; round 1's P2s fixed:
  `trimmed` rebuilds the question, and the plan pairs only questions
  both runs scored). `laya/data/colab.zip` packed at 43775fd with
  `support` and `joint` (support train 9c05b3dd84, joint train
  ef06ad2011, `picks-validation` unchanged d59605916e). The joint-only
  bundle is kept as `colab-joint-2f6f0be.zip`.

## Direct wording result (run 20261020-215126)

Both kinds chose e2 by log loss. Paired against the `shape3` runs
(support 072747 e3, joint 114125 e3) on questions both scored, bootstrap
over states (4,000 draws, seed 0, scratchpad `paired2.py`). Every
validation question was scored by both runs; train left out for length
went 22 → 37 (support) and 4 → 5 (joint).

| Measure | Ids (`shape3` run) | Quoted requirement | Gain | Noise floor |
| --- | --- | --- | --- | --- |
| Support accuracy (1,772) | 0.857 | 0.869 (e3 0.869 too) | +0.012 (83 fixed, 62 broken) | 0.014 |
| Support log loss / ECE | 0.469 / 0.017 | 0.431 / 0.034 | | |
| Coverage from support: decided | 110, 0.836 | 113, 0.885 | equal on the 87 both decided | |
| Coverage from support: TPR / TNR decided | 0.59 / 0.97 | 0.83 / 0.92 | | |
| Joint accuracy (435) | 0.720 | 0.724 | +0.005 | 0.039 |
| Coverage from support 211003 + joint (219) | 0.767 | 0.712 | -0.055 (10 fixed, 22 broken) | 0.049 |
| Joint on the 105 support left | 0.686 | 0.571 | | |

- Support: quoting the requirement gains 1.2 points, in both epochs, just
  under the floor; log loss falls 0.038, the largest move since aug5.
  Covered requirements are found far more often (true yes 23 → 39) for
  3 more false yes. By the adoption rule it is not adopted.
- Joint: its own validation is flat, but coverage made with it loses 5.5
  points, past the floor: the quoted wording hurts joint where it is used.
  Rejected.
- Checkpoints on Drive: `support-train-e2-ls0.1-20261020-215126-iif51qho.zip`,
  `joint-train-e2-ls0.1-20261020-215126-iif51qho.zip`.

Raw files: `Downloads/res/result-{support,joint,setup}-20261020-215126.zip`
(local).

Owner, 2026-10-21: revert both (PR #201). The six splits are back to
`*.v-ids.jsonl` (support train 4e7e652453, joint train 09c5a970f2, the
`shape3` runs' bytes); the quoted versions are kept as
`*.v-quoted.jsonl`.
