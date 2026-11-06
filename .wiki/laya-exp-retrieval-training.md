---
scope: project
severity: preference
triggers: ["(?i)laya.*(9종|nine|retrieval|검색).*(training|학습|colab|result|결과)"]
reads: [laya/train.py, laya/notebooks/train.ipynb, docs/plans/laya/4-training.md]
slots: []
sources: []
links: [laya, laya-exp-retrieval-full, laya-exp-useful-training, laya-insight-select-by-log-loss]
---

# Nine retrieval kinds trained on Colab — result 20261015-053449

Part of [laya](laya.md). Data from [laya-exp-retrieval-full](laya-exp-retrieval-full.md);
notebook and `colab.zip` from #149 (packed at ca800a6). One L4 run, all nine kinds
in turn, `e2-ls0.1` and `e3-ls0.1` per kind, chosen by validation log loss after
calibration on the calibration split. No kind failed.

## Cost and memory

- Peak GPU memory 7.8 GB for every kind at `max_len` 1,024/2,048, and 7.9 GB for
  `coverage` at 8,192 with micro-batch 1. The L4's 24 GB was never close; micro-batch
  1 was not needed for memory.
- Time per kind: 2.5–6 minutes a fine-tune at 1,024 (useful 6–8 at 2,048);
  `coverage` 36 and 53 minutes. Checkpoint zip about 80 seconds each.

## Results (validation, chosen model in bold)

TPR is recall of true, TNR of false. Class AUROC is p(true) against the label.

| Kind | n (true) | Majority | Zero-shot best | Chosen | Accuracy | Log loss | TPR | TNR | Class AUROC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ask | 287 (229) | 0.798 | base 0.805 | e2 | 0.986 | 0.065 | 1.00 | 0.95 | 0.991 |
| progress | 317 (45) | 0.858 | base 0.729 | e3 | 0.987 | 0.059 | 0.93 | 1.00 | 0.965 |
| route | 315 (281) | 0.892 | base 0.733 | e2 | 0.984 | 0.058 | 1.00 | 0.88 | 0.997 |
| redirect | 295 (10) | 0.966 | base 0.519 | e3 | 0.986 | 0.083 | 0.70 | 1.00 | 0.917 |
| source | 220 (137) | 0.623 | typed 0.577 | e3 | 0.950 | 0.154 | 0.96 | 0.94 | 0.992 |
| analysis | 254 (73) | 0.713 | base 0.736 | e3 | 0.866 | 0.363 | 0.53 | 1.00 | 0.919 |
| useful | 300 (141) | 0.530 | base 0.547 | e3 | 0.833 | 0.405 | 0.77 | 0.89 | 0.917 |
| conflict | 312 (9) | 0.971 | typed 0.971 | e3 | 0.971 | 0.130 | 0.00 | 1.00 | 0.677 |
| coverage | 127 (103) | 0.811 | base 0.772 | e2 | 0.811 | 0.493 | 1.00 | 0.00 | 0.659 |

## Insights

- Seven kinds learned. `ask`, `progress`, `route`, `redirect` and `source` reach
  0.95–0.99 with class AUROC 0.92–0.997, from zero-shot near or below the majority
  rate. These are LLM labels, so this is agreement with the labeller-plus-verifier,
  not truth.
- `useful` gained from data: 1,524 train labels gave 0.833 and AUROC 0.917, where
  500 gave 0.780 and 0.865 (run 5 in [laya-exp-useful-training](laya-exp-useful-training.md)).
  The validation split is the same 300.
- `analysis` ranks well (AUROC 0.919) but answers false too readily: TPR 0.53 at the
  0.5 cut. Its 30 answers at confidence 0.9 or above were all right, so a threshold
  policy can still use it.
- Two kinds collapsed to the majority class.
  - `conflict` answers false to all 312, at 0.97 confidence. Train holds 83 true of
    1,050 (8%), validation 9. Class AUROC 0.68: almost no ranking either.
  - `coverage` answers true to all 127. Train holds 946 true of 1,104 (86%),
    validation 24 false. AUROC 0.66.
  - Laya 0.4.6 has no class weight in `TrainConfig`; balance would have to come
    from the data (oversampling the minority) or from more minority labels.
- `e2` and `e3` with label smoothing 0.1 are within noise of each other on most
  kinds; `e3` helped `analysis` most (TPR 0.30 → 0.53).

Raw files: `Downloads/res/result-<kind>-20261015-053449.zip` (local).

## Follow-up: minority data (2026-10-15)

The owner's principle: augment where the model learned worst.

- `conflict`: "yes" comes almost only from false-premise questions (81 of 310
  labelled). The 712 unlabelled false-premise train candidates were labelled
  as `aug1`: 161 yes / 407 no, $0.62 (Codex cost unknown). With 16 recovered
  labels, train is now 244 yes / 1,390 no (was 83 / 967).
- `coverage`: the skew was mostly a labelling bug, not data
  ([laya-insight-unclear-absence](laya-insight-unclear-absence.md)). The bug fix
  recovered 757 train, 92 validation and 105 calibration "no" labels at no cost.
  `laya/augment.py uncovered` (PR #150) also derived 400 states that drop
  the question's own pages. Labelled as `aug1`, they gave 187 yes / 56 no, and
  132 more "no" were recovered.
- Review of PR #150 found two gaps in `augment.uncovered`, both fixed before the
  merge (305aebb). The 400 `aug1` rows had neither problem.
  - Dropping pages can bring a cut state near a gate, validation or calibration
    state its source was far from. The cut is now checked with `gate.Near`.
  - Two sources that differ only in the dropped pages cut to one state. Each
    drawn cut now joins the index, so a near copy is skipped.
- Data spend after this round: $35.92 of the $38 cap.
- The new `colab.zip` (5d9f437) holds `conflict` and `coverage` only.
  - Judge `coverage` by TNR and AUROC, not accuracy: its validation changed to
    103 yes / 116 no.
  - Judge `conflict` by TPR: its validation still holds 9 yes.

## Retrain of conflict and coverage (2026-10-15, run 20261015-102046)

Same notebook and runs (e2-ls0.1, e3-ls0.1), L4, peak GPU 7.8–7.9 GB. `e3` was
chosen for both.

| Kind | Train | Validation | Majority | Accuracy | TPR | TNR | Class AUROC | Before |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| conflict | 1,634 (244 yes) | 312 (9 yes) | 0.971 | 0.968 | 3/9 | 299/303 | 0.955 | TPR 0/9, AUROC 0.68 |
| coverage | 2,104 (1,133 yes) | 219 (103 yes) | 0.530 | 0.703 | 0.79 | 0.63 | 0.760 | all yes, AUROC 0.66 on the old split |

- `conflict` no longer collapses. Its ranking is good (AUROC 0.955), and 304 of 312
  answers have confidence 0.9 or more at 0.987 accuracy. Nine positives cannot
  measure TPR: 3/9 at the 0.5 cut is mostly a threshold question.
- `coverage` learned something but stays weak. Its wrong answers are confident
  (mean 0.71), and no answer reaches confidence 0.9.
  - Two thirds of its errors (43 of 65) say yes to an uncovered requirement.
  - Error rate by question type: bridging 0.36, partial 0.35, unanswered 0.33,
    answered 0.22, false_premise 0.20.
  - States whose labels came from the unclear-absence recovery err 0.36; the
    others 0.24.
  - `e3` beat `e2` by 0.06 (0.644 → 0.703, TNR 0.47 → 0.63): more epochs may
    still help.
- Time: coverage fine-tunes took 63 and 93 minutes (2,104 questions at 8,192
  tokens); conflict 6 and 9 minutes.

Raw files: `Downloads/res/result-{conflict,coverage}-20261015-102046.zip` (local).

## coverage augmentation 2: unsupported (2026-10-15)

The owner chose more "no" data plus more epochs.

- `augment.unsupported` (PR #153) drops the listed passages that hold a quote a
  verifier gave as the evidence for a yes. It takes only the weakest types
  (bridging, partial, unanswered).
  - The quotes come from every coverage `*.turns.jsonl`: 1,776 quotes of 20
    characters or more.
  - 264 of 300 such yes states had a quoted passage to drop.
- Result: 264 rows, 348 questions, labelled as `aug2` in 2 rounds for $0.50.
  - 218 kept: 137 no / 81 yes (63% no). `uncovered` gave 23% no.
  - The trial guide was not adopted.
- Review of PR #153 (P1) found a leak: reading every turns file let calibration
  and validation verdicts choose which passages to drop from train states.
  - The CLI now takes `--quotes-from` with train splits only.
  - aug2 was filtered at no cost. A row was kept only when quotes from aug1, div1,
    div2, full1, full2 and full3 alone produce the same state: 148 of 179 rows
    (179 questions, 110 no / 69 yes).
- Review round 2 (P1) found a second path: a split's turns judge every input
  state, kept or not, so a split whose kept labels are all train can still
  carry a calibration verdict's quotes.
  - The CLI now needs each quoted split's input rows (`<split>.candidates.jsonl`)
    and refuses any with a held state, or whose labels the file does not hold.
  - `div1` and `div2` have no kept input file, so their quotes are out. With
    aug1 and full1–3 alone (input files clean), 146 of the 148 rows reproduce
    (176 questions, 109 no / 67 yes): `aug2-train-inputs.jsonl`.
  - Filtering by content instead (drop any quote a held state's text holds)
    was tried and dropped: 368 of 1,482 quotes sit in a held state too, since
    train and held states share corpus passages, leaving 30 rows.
- Train is now 1,699 rows, 2,279 labels (1,079 no / 1,200 yes).
  - Earlier files are kept: `v3-train.jsonl` (before aug2),
    `v4-train-heldquotes.jsonl` (unfiltered aug2) and `v5-train-sixsplits.jsonl`
    (aug2 from six splits' quotes).
  - No state overlaps validation or calibration.
- PR #153 merged as f8dd254 after review round 3. Left open (P2): a replaced
  input file is not caught, since guided records no hash of its rows.
- `colab.zip` (db8a76d) holds `coverage` only. The 8ecb752 zip is kept as
  `colab-coverage-8ecb752-sixsplits.zip`; do not use it. Planned runs: e3 and
  e5. Data spend so far: $36.42 of $38.

## Retrain of coverage after aug2 (2026-10-16, run 20261015-214208)

Zip db8a76d, train 1,699 states / 2,279 labels, same validation (219, 103 yes)
and calibration. The runs were named `e3-ls0.1` and `e5-ls0.1` but kept the
notebook's epochs, 2 and 3: no 5-epoch run was made.

| Run (epochs) | Accuracy | Log loss | ECE | TPR | TNR | Class AUROC | Errors (false yes) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| "e3" (2) chosen | 0.722 | 0.579 | 0.065 | 0.80 | 0.66 | 0.79 | 61 (40) |
| "e5" (3) | 0.699 | 0.604 | 0.017 | 0.77 | 0.64 | 0.76 | 66 (42) |
| before aug2, 3 epochs | 0.703 | — | — | 0.79 | 0.63 | 0.76 | 65 (43) |

- The gain is within noise (n 219, one standard error about 0.03). 3 epochs
  now trails 2, so more epochs is not the lever.
- The weak slices did not move. Error rate by type, 2 epochs: partial 0.37,
  bridging 0.33, unanswered 0.33, false_premise 0.17, answered 0.15.
  Validation states with recovered (unclear-absence) labels err 0.32, others 0.24.
- No answer reaches confidence 0.9, so a threshold policy would defer them all.
- Augmentation from existing states is spent: all 258 weak-type train states
  that `unsupported` can cut were already sources of aug1 or aug2. More "no"
  data for those types needs newly generated states (paid; $1.58 of the $38
  cap left).
- Time: 67 and 100 minutes (2 and 3 epochs), peak GPU 7.9 GB.

Raw file: `Downloads/res/result-coverage-20261015-214208.zip` (local).

## Audit of the 61 coverage validation errors (2026-10-16, free)

Every error of the 2-epoch model was read against its state: the requirement, the
listed (`complete_passages`) and unlisted passages.

| Verdict | Errors | False yes | False no |
| --- | --- | --- | --- |
| Label right, model wrong | 37 | 23 | 14 |
| Label wrong | 1 | 1 | 0 |
| Ill-posed requirement | 23 | 16 | 7 |

- Label noise is small: one clear wrong label in 61 (a recovered "no" whose
  listed passage states the answer).
- The 23 ill-posed ones:
  - 5 come from a bad requirement split: a preamble ("I got this when running…:"),
    a quoted error line or a bare code fence as its own requirement.
  - 5 are opinion questions ("would it be better", "what would you recommend").
  - 4 are false premises, where "covered" depends on whether correcting the premise
    counts.
  - 2 are rewrite tasks.
  - 7 others are open status questions, "the cause is still unknown" answers, or
    an answer split between listed and unlisted passages.
- Ill-posed requirements are not where most errors are. A rough text rule finds 37
  of 219 validation questions with such a shape. They err 0.35, the rest 0.28.
  Train holds about 240 of 2,279.
- The model reads unlisted passages as evidence. The question says they do not
  count.

  | Validation questions | Label no | Label yes |
  | --- | --- | --- |
  | Every passage listed | 7/34 wrong (0.21) | 3/24 (0.12) |
  | Some passages unlisted | 33/82 wrong (0.40) | 18/79 (0.23) |

  The weakest slice is a "no" whose answer sits in an unlisted passage. Reading
  the errors shows the same thing: the model answers yes from a passage outside
  `complete_passages`.
- A free fix follows from this. Extend `kinds.view` to `coverage` so the model
  sees only the listed passages. The question already says unlisted ones do not
  count, so every label stays valid. Inputs also get shorter.

Scripts: scratchpad `errs.py`, `reqkind.py`, `listed.py` (session-local).

- The owner chose the listed view (2026-10-16). PR #154 (e956dd5) adds `LISTED` to
  `kinds.view`, applies it in `train.py`, and compares it in `gate.signatures`.
  - With that view, no coverage train row is near a validation, calibration or
    gate state.
  - The median state shrinks about 15% (train 13,851 → 11,850 characters).
  - Merged as 952f4e4 after review round 2. Round 1's one P2 was fixed before the
    merge: `stage1.lengths` now measures the listed view as well.
  - `colab.zip` was repacked at 952f4e4 with `coverage` only. The previous zip is
    kept as `colab-coverage-db8a76d.zip`. The data is unchanged, since the view is
    applied when the model is trained and scored.

## Retrain of coverage with the listed view (2026-10-16, run 20261016-023112)

Zip 952f4e4 (clean), the same split hashes as the local files, and the same runs (2 and
3 epochs, label smoothing 0.1). No question was left out for length.

| Run | Accuracy | Log loss | ECE | TPR | TNR | Class AUROC | Errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| e2 | 0.689 | 0.613 | 0.058 | 0.79 | 0.60 | 0.75 | 68 |
| e3, chosen | 0.694 | 0.594 | 0.025 | 0.76 | 0.64 | 0.77 | 67 |
| before the view, e2 | 0.722 | 0.579 | 0.065 | 0.80 | 0.66 | 0.79 | 61 |

- The view did not help. Every figure is within noise of the run before, or slightly
  below it.
- The slice it targeted did not move. With unlisted passages present, "no" questions
  still err 0.40 (33/82), the same as before. All-listed "no" questions err 0.26.
- So the audit read correlation as cause. States with unlisted passages are harder
  in themselves; the model's yes did not come from reading the unlisted text.
- Time: 54 and 80 minutes, down from 67 and 100, since the inputs are shorter. Peak GPU
  7.8 GB. No answer reaches confidence 0.9.
- Coverage has now held near 0.70 accuracy and 0.76–0.79 AUROC across three
  retrains, with more "no" data, more epochs and a cleaner input. Two thirds of its
  errors had a correct label, so label noise is not the limit either.

Raw file: `Downloads/res/result-coverage-20261016-023112.zip` (local). The
20261015-214208 zip is no longer in that folder.

## Next for coverage: ask one passage at a time (2026-10-16)

The owner chose to split the question by passage, with three options, and raised the
cap to $40.

- New kind `support`, in PR #155. It asks whether passage p states all, part or none
  of requirement r.
- The production `coverage` question stays. `support.decide` answers it from the listed
  passages:
  - yes when any passage states all of it;
  - no when every passage states none of it;
  - otherwise undecided, left to the LLM.
- Candidates come from coverage's labelled splits without reading their labels. Each
  question gives its top word-overlap passage and one random listed passage.
- One-passage labelling turns cost about $0.0004 each (useful's journals). 2,200
  questions should fit the $3.58 left under the cap.
- The measure is coverage's own 219 validation questions: how many are decided, and
  how accurate those are, against the coverage model's 0.69–0.72.

## support labels (2026-10-16, PR #155 merged as d7bf4f7)

The owner chose 2,400 train questions so a 60% kept rate would still clear 1,000.
Candidates: train 2,400 questions in 2,282 states, validation 300 in 285,
calibration 300 in 284 (the first 1,600-question train file is kept as
`train1600.candidates.jsonl`). Haiku labels, Codex `gpt-6.1-sol` verifies.

| Split | Kept labels | none / part / all | Kept rate |
| --- | --- | --- | --- |
| train (full1, 16 rounds of 150) | 1,850 | 1,424 / 241 / 185 | 77% |
| validation (one round, no guide) | 214 | 155 / 42 / 17 | 71% |
| calibration (one round, no guide) | 225 | 177 / 30 / 18 | 75% |

- RRSI adopted two guides of 14 tried: round 1 (69.7% → 78.1% kept) and round 14
  (73.9% → 81.4%). The rules it learned: shared topic is not part; a formula
  with all inputs, or an explicit denial of a false premise, can be all.
- Cost: $2.59 train, $0.13 each for validation and calibration, about $0.0005 a
  turn. Full-run spend is now $39.27 recorded, plus 167 turns that reported no cost
  (about $0.08): about $39.35 of the $40 cap.
- Labels agree with coverage's. Of the train coverage questions whose top-overlap
  passage was labelled, coverage "no" gave none 411, part 66, all 0; coverage "yes"
  gave all 163, part 88, none 104. A coverage "yes" is stated whole by its top-overlap
  passage only 46% of the time, so `decide` will often need another listed passage or
  leave the question undecided.
- `all` is rare: 10% of train, 17–18 in validation and calibration. Random draws
  were almost always none (908 of 1,014).
- Splits: `train.jsonl`, `validation.jsonl` and `calibration.jsonl` are copies
  of full1, full1-val and full1-cal. Packed at d7bf4f7 as `laya/data/colab.zip`.

## support training (2026-10-16, run 20261016-094429)

| Run | Accuracy | Log loss | ECE |
| --- | --- | --- | --- |
| base / typed zero-shot | 0.18 / 0.09 | 1.30 / 1.42 | 0.30 / 0.39 |
| e2 | 0.724 | 0.669 | 0.089 |
| **e3** | **0.752** | **0.630** | **0.053** |

Coverage made from e3 over its 219 validation questions: 216 decided, accuracy 0.662,
TPR 0.72, TNR 0.61, AUROC 0.73. That is below the coverage model (0.694, AUROC 0.77).
The best single threshold on the score reaches 0.676; deciding only the 40% farthest
from it reaches 0.739.

- The model did not learn `part`: recall 1 of 42. It answers `part` passages
  none (27) or all (14), and of 30 `all` answers only 11 are right.
- So `decide` almost never leaves a question undecided (3 of 219). The 44 false
  yes are mostly one wrong `all` among many passages (29 have exactly one). The 29
  false no have all-`none` picks over 6–13 passages: the requirement is likely met by
  parts that add up, which a `part` answer would have sent to the LLM.
- Nearly every coverage question lists 7+ passages (205 of 219). One false `all` in
  a dozen decides a false yes.
- The weak slice is `part`, and `all` against `part`. It is 13% of train
  (241 of 1,850).
- Time: 6.6 and 9.5 minutes of fine-tuning, peak GPU 7.8 GB.

Raw file: `Downloads/res/result-support-20261016-094429.zip` (local).

## support augmentation aimed at part (2026-10-16, PR #156 merged as 5752551)

The owner chose to augment `part` and raised the cap to $42. `support augment aug1
800` made 766 states, 800 questions:

- 349 trimmed: a passage that stated all, less the sentences the verifier quoted
  for an all (quotes from full1's own turns only; nested quotes cut longest
  first).
- 213 overlap2 and 238 overlap3: a covered requirement's second and third
  passages by shared words, never a (view, question) train already asked.

Guided labelling, 6 rounds of 150: guide 1 adopted in round 1 (52.9% → 64.5%
kept), four later trials failed. The kept rate is lower than full1's (about 60%
against 77%): these are the hard cases.

| Source | none / part / all kept |
| --- | --- |
| trimmed | 100 / 77 / 38 |
| overlap2 | 60 / 41 / 27 |
| overlap3 | 81 / 35 / 25 |
| aug1 in all (484) | 241 / 153 / 90 |

- part is 32% of aug1, against 13% of full1. Trimming an all passage left it all
  38 times: the quoted sentence was not the only one stating it.
- Cost $0.85. Recorded spend is now $40.12, plus about 210 turns that reported
  no cost (about $0.10): about $40.2 of the $42 cap.
- `train.jsonl` is now full1 + aug1: 2,266 rows, 2,334 labels (1,665 / 394 /
  275), no repeated (state, question). The earlier train is `v1-train.jsonl`.
  27 states appear in both files with different questions. Packed at 5752551.

## support retrain with aug1 (2026-10-16, run 20261016-112318)

| Run | Accuracy | Log loss | ECE |
| --- | --- | --- | --- |
| **e2** | **0.780** | **0.612** | **0.084** |
| e3 | 0.752 | 0.628 | 0.061 |

| Answer | Recall before (e3, 094429) | Recall now (e2) |
| --- | --- | --- |
| none | 149 / 155 | 147 / 155 |
| part | 1 / 42 | 7 / 42 |
| all | 11 / 17 | 7 / 17 |

Coverage made from e2: 94 of 219 decided (43%), accuracy 0.755 on those, TPR
0.44, TNR 0.92, AUROC 0.73. Undecided as no: 0.571.

- part grew from 1 to 7 of 42, but the model now says all almost never: 20 all
  picks over every listed passage, against 241 before. Most part passages still
  read none (30 of 42).
- So coverage went from deciding everything to deciding under half. A yes verdict
  is right 14 of 19 times and a no 57 of 75; 125 questions go to the LLM.
- At the same rate, the score alone does as well: the 40% farthest from its best
  threshold are 0.750 right. The coverage model's own predictions at 43% are no
  longer at hand (its zip left `Downloads/res`), so that comparison is still open.
- Validation holds only 17 all and 42 part, so a recall moves by 2–6 points per
  question.
- Recorded spend: $40.12 of the $42 cap.

Raw file: `Downloads/res/result-support-20261016-112318.zip` (local).

## support aug2: the whole pool at once (2026-10-16)

The owner chose another round on the weak slices, then said to augment hard, not a
little at a time, and took the whole remaining pool with the cap raised to $46.

aug2 was made with merged functions only, called from a session script; it was not
made by the `augment` CLI:

- `support.trimmed(aug1 candidates, support.quotes(aug1 turns, "all"), held)`: aug1's
  all passages cut again by aug1's own verifier quotes. This gave 179 questions.
- `support.derive([], n, 1, held, drawn, asked_before=train + aug1 candidates + the
  trimmed rows)`. Here `drawn` is every covered train coverage question's 2nd to 5th
  passages by `support.ranked`, shuffled with seed `1-ranks`.
- held is the gate plus coverage validation and calibration. This is the same as
  the CLI.

Result: 3,114 states and 3,410 questions, with no question asked twice and none
asked before.

| Source | Questions |
| --- | --- |
| trimmed | 179 |
| overlap2 | 661 |
| overlap3 | 684 |
| overlap4 | 913 |
| overlap5 | 973 |

Labelling: 21 rounds of 150, cap $5.5. The run finished, with 6,267 turns and
$3.68. Guide 3 was adopted in round 3 (56.8% → 63.3% kept). The other 19 trials
failed. Kept rate per round was 64–83%.

| Source | Kept none / part / all |
| --- | --- |
| trimmed | 57 / 39 / 7 |
| overlap2 | 208 / 125 / 72 |
| overlap3 | 286 / 112 / 41 |
| overlap4 | 434 / 156 / 49 |
| overlap5 | 546 / 127 / 28 |
| aug2 in all (2,287) | 1,531 / 559 / 197 |

- part falls off with rank: 31% at overlap2, 18% at overlap5. all falls faster:
  18% at overlap2, 4% at overlap5.
- `train.jsonl` is now full1 + aug1 + aug2. That is 4,433 rows and 4,621 labels:
  3,196 none, 953 part, 472 all. No (state, question) repeats, and no state is
  shared with validation or calibration. The full1 + aug1 train is
  `v2-train.jsonl`. Packed at 5752551.
- Recorded spend is $43.81, plus about 400 turns that reported no cost (about
  $0.2). That is about $44.0 of the $46 cap.

## support retrain with aug2 (2026-10-17, run 20261016-210840)

| Run | Accuracy | Log loss | ECE |
| --- | --- | --- | --- |
| **e2** | **0.785** | **0.570** | **0.052** |
| e3 | 0.799 | 0.584 | 0.060 |

Fine-tuning took 15 and 22 minutes.

| Answer | 094429 | 112318 (+aug1) | 210840 (+aug2, e2) |
| --- | --- | --- | --- |
| none | 149 / 155 | 147 / 155 | 149 / 155 |
| part | 1 / 42 | 7 / 42 | 20 / 42 |
| all | 11 / 17 | 7 / 17 | 2 / 17 |

Coverage made from e2: 70 of 219 decided (32%), every one a no, accuracy 0.814 on
those. AUROC 0.758 is the best of the three support runs. The best single threshold
on the score gives 0.703, the same as the coverage model.

- part rose to 20 of 42, but all collapsed: 13 of 17 all passages now read part.
  Over coverage's validation the model said all on no listed passage at all (0 of
  2,284), so `decide` can never say yes.
- The boundary moved and did not sharpen. Each augmentation added part and none
  far more than all: 953 part against 472 all in train. all against part is now
  the weak slice.
- Five coverage tries now sit near 0.70 overall: three coverage retrains and the
  support threshold here and in 094429.

Raw file: `Downloads/res/result-support-20261016-210840.zip` (local).

## support aug3: every all restated (2026-10-17, PR #158 merged as 96ce536)

The owner chose to augment all heavily, with the cap raised to $48. The command
was `support restate aug3 0 --from full1 aug1 aug2`. It asks every labelled all
again of the same passage, told another way. That gave 1,637 candidates.

The labelling run took 11 rounds of 150 and cost $1.81 over 3,237 turns. Two
guides were adopted: round 1 (36.7% → 62.7% kept) and round 5 (56% → 75.3%).

| Way | Kept none / part / all |
| --- | --- |
| evidence | 14 / 26 / 199 |
| before | 0 / 1 / 293 |
| after | 1 / 5 / 295 |
| evidence_after | 6 / 21 / 226 |
| aug3 in all (1,087) | 21 / 53 / 1,013 |

- Padding with another listed passage kept the answer all almost every time (588
  of 595 kept). The evidence alone was sometimes judged part (26 of 239), because
  the quotes left out context the requirement needed.
- PR #158's review found 10 candidates whose padded view runs over 1,024 tokens
  under the character cap. `train.fits` leaves them out. Left as is.
- `train.jsonl` is now full1 + aug1 + aug2 + aug3: 5,517 rows and 5,708 labels.
  The counts are 3,217 none, 1,485 all and 1,006 part. No (state, question)
  repeats, and no state is shared with validation or calibration. The earlier
  train is `v3-train.jsonl`. Packed at 96ce536.
- Recorded spend is $45.62, plus about 520 turns that reported no cost (about
  $0.27). That is about $45.9 of the $48 cap.

## support retrain with aug3 (2026-10-17, run 20261017-013631)

| Run | Accuracy | Log loss | ECE |
| --- | --- | --- | --- |
| **e2** | **0.771** | **0.598** | **0.083** |
| e3 | 0.762 | 0.650 | 0.064 |

Fine-tuning took 19 and 29 minutes.

| Answer | 094429 | +aug1 | +aug2 | +aug3 (e2) |
| --- | --- | --- | --- | --- |
| none | 149 / 155 | 147 / 155 | 149 / 155 | 136 / 155 |
| part | 1 / 42 | 7 / 42 | 20 / 42 | 22 / 42 |
| all | 11 / 17 | 7 / 17 | 2 / 17 | 5 / 17 |

| Coverage made from support | Decided | Accuracy decided | TPR / TNR decided | AUROC | Best threshold, all decided |
| --- | --- | --- | --- | --- | --- |
| 094429 | 216 | 0.662 | 0.72 / 0.61 | 0.734 | 0.676 |
| +aug1 | 94 | 0.755 | 0.44 / 0.92 | 0.728 | 0.667 |
| +aug2 | 70 | 0.814 | 0 / 1.0 | 0.758 | 0.703 |
| **+aug3** | **121** | **0.810** | **0.81 / 0.81** | **0.799** | **0.744** |
| coverage model (023112) | 219 | 0.694 | 0.76 / 0.64 | 0.77 | — |

- For the first time coverage made from support beats the coverage model. It
  decides 55% at 0.81, both classes alike, and sends 98 questions to the LLM. On
  the score alone the best single threshold gives 0.744 overall against 0.694. The
  40% farthest from it are 0.830 right.
- all came back from 2 to 5 of 17. It said all on 89 listed passages against 0
  before, so `decide` can say yes again (57 yes, 44 right). part held at 22 of 42.
- Still weak: all read part (11 of 17), and 13 false yes, 11 of them from a single
  wrong all. The support validation holds only 17 all, so its per-answer recalls
  are noisy. Coverage's 219 questions are the measure that matters.

Raw file: `Downloads/res/result-support-20261017-013631.zip` (local).

## support aug4 and wide validation (2026-10-17, PR #160 merged as f84dc72)

The owner asked for all against part augmented heavily, and validation widened
(cap $55). Three splits were labelled by guided labelling:

| Split | Made by | Candidates | Labelled | none / part / all | Cost |
| --- | --- | --- | --- | --- | --- |
| aug4a | `trim --from aug3`: aug3's padded alls less their quoted evidence | 1,069 | 538 (50%) | 329 / 158 / 51 | $1.11 |
| aug4b | `restate --answer part`: every part padded like aug3's alls | 3,280 | 2,289 (70%) | 122 / 2,072 / 95 | $3.89 |
| full1-val2 | `widen validation`: every listed passage `evaluate` asks | 1,984 | 1,558 (79%) | 1,295 / 212 / 51 | $1.73 |

- Trimmed alls mostly turned none (329 of 538), so the quoted sentences were
  the whole evidence. These are minimal pairs against their aug3 views.
- Padded part stayed part 2,072 of 2,289, so length does not make all.
- Train (v5) is full1 plus aug1 to aug4b: 8,534 labels (none 3,668, part 3,236,
  all 1,630). One aug4a view was the same as an aug3 view with the same
  question and answer. The first copy was kept.
- Validation is full1-val plus full1-val2: 1,772 labels (none 1,450, part 254,
  all 68), against 214 with 17 all before. Calibration is unchanged (225).
- No state is shared between train, validation and calibration.
- Spend after this step: $52.34 of $55.
- The first labelling attempt lost turns: Haiku's OAuth session expired
  ("OAuth session expired and could not be refreshed"). The owner logged in
  again and the reruns paid only for missing turns.
- Old files were moved aside as `v4-train.jsonl` and `v1-validation.jsonl`.
  `laya/data/colab.zip` was packed at 0fbef12.

## support retrain with aug4 and wide validation (2026-10-17, run 20261017-121439)

Train had 8,534 labels and validation 1,772. e3 was chosen (log loss 0.533
against e2's 0.563).

| Support answer | e2 on 1,772 | e3 on 1,772 | e3 on the old 214 | aug3 run on the old 214 |
| --- | --- | --- | --- | --- |
| none | 1,319 / 1,450 | 1,302 / 1,450 | 140 / 155 | 136 / 155 |
| part | 83 / 254 | 115 / 254 | 18 / 42 | 22 / 42 |
| all | 0 / 68 | 22 / 68 | 8 / 17 | 5 / 17 |

| Coverage made from support | Decided | Accuracy decided | TPR / TNR decided | AUROC | Best threshold, all decided |
| --- | --- | --- | --- | --- | --- |
| +aug3 (013631) | 121 | 0.810 | 0.81 / 0.81 | 0.799 | 0.744 |
| +aug4, wide val (121439) | 112 | 0.8125 | 0.82 / 0.81 | 0.770 | 0.721 |
| coverage model (023112) | 219 | 0.694 | 0.76 / 0.64 | 0.77 | — |

- Coverage is flat against aug3: the same decided accuracy, AUROC down 0.03.
  It is still above the coverage model on decided questions.
- all is the weak answer on the wide validation: 22 of 68, read as part 26 and
  none 20. Its confidence does not separate it: mean 0.75 when right, 0.73
  when read part, 0.79 when read none. e2 never said all at all.
- Train's 1,630 all labels come from only 529 distinct (row, question) pairs.
  aug3 and aug4 restated the same alls, so the model has seen few distinct
  alls.
- Coverage train's listed passages that were never asked: about 16,700. At the
  wide validation's rate (3.3% all, 14% part) they would give about 550 new
  distinct alls, for about $14.5 at val2's $0.87 per 1,000.

Raw file: `Downloads/res/result-support-20261017-121439.zip` (local).

## support aug5: every unasked listed passage of coverage train (2026-10-18)

The owner chose to augment with new distinct alls (cap raised to $70). The
subscription's weekly limit was at 99%, so Haiku ran on the API credit (a
workspace-scoped `ANTHROPIC_API_KEY` exported into `guided.py`'s process).

- Candidates: every listed passage of coverage train never asked in any train
  candidate file. That gave 12,698 questions, split in half (aug5a, aug5b) and
  labelled in parallel. A scratchpad script made them with `support.derive`
  over `support.everything`, with the train splits' held set.
- Labelled 10,677 (84%) for $6.85: none 9,761, part 816, all 100. Natural all is
  rare in train's listed passages (0.9%), well below the 3.3% the widened
  validation has.
- Train (v6) is v5 plus aug5: 19,211 labels (none 13,429, part 4,052, all 1,730).
  All of aug5 is kept, none included, so the answer mix moves toward the
  validation's (82% none). Validation and calibration are unchanged.
- Spend after this step: $59.19 of $70.
- Packed at a41c7f3; laya's code is unchanged since 0fbef12.

## support retrain with aug5 (2026-10-18, run 20261017-211003)

Train had 19,211 labels. e3 was chosen (log loss 0.472, accuracy 0.859, ECE
0.008; e2 0.477 and 0.845). Training took 2.7 h on the L4.

| Support answer on 1,772 | aug4 run (121439) e3 | aug5 run e3 |
| --- | --- | --- |
| none | 1,302 / 1,450 | 1,383 / 1,450 |
| part | 115 / 254 | 117 / 254 |
| all | 22 / 68 (precision 22 / 56) | 22 / 68 (precision 22 / 39) |

| Coverage made from support | Decided | Accuracy decided | TPR / TNR decided | AUROC | Best threshold, all decided |
| --- | --- | --- | --- | --- | --- |
| +aug3 (013631) | 121 | 0.810 | 0.81 / 0.81 | 0.799 | 0.744 |
| +aug4 (121439) | 112 | 0.8125 | 0.82 / 0.81 | 0.770 | 0.721 |
| **+aug5 (211003)** | **114** | **0.842** | **0.77 / 0.90** | 0.742 | 0.699 |
| coverage model (023112) | 219 | 0.694 | 0.76 / 0.64 | 0.77 | — |

- The aug5 none labels removed most false alls: none read all 23 → 9, and
  false yes 11 → 7. The decided questions are the most accurate yet (0.842).
  The score ranks worse, though: AUROC 0.742, and the best single threshold is
  0.699.
- all recall did not move (22 of 68 in both runs); all is read part 32 times.

Ceiling of the per-passage rule. `decide` was applied to the support labels
themselves (full1-val plus full1-val2) instead of the model's answers:

- It decides 78 of 219 coverage questions, all 78 right. The model decides 114.
- 55 of the 103 covered requirements have no passage labelled all. 39 of
  those 55 have one or more part passages, so the requirement is stated only
  by passages together. 16 have neither all nor part among their labelled
  passages: unlabelled passages (2.3 per question on average) or coverage and
  support labels that disagree.
- So more support augmentation cannot decide those questions. Under this rule
  a covered requirement whose passages each state part of it is left to the
  LLM, whatever support learns.

## joint: views from labels against views from the model's picks (2026-10-18, PR #167 merged as cc0fe16)

The owner chose to ask the part passages together (`laya/joint.py`). The first
validation was labelled on views built from support labels. It came back 79
no to 3 yes, so labelling train that way was stopped.

| Joint view built from | Labelled | (coverage, joint) agree | Covered, joint says no |
| --- | --- | --- | --- |
| support labels (`full1-val`) | 82 of 111 | 47 of 82 | 33 |
| support run 211003's picks (`picks-val`) | 72 of 105 | **68 of 72** | 4 |

- On the views `evaluate` asks (the model's part picks for the 105 questions it
  left undecided), joint labels agree with coverage labels 94% of the time.
  Parts together state the requirement when coverage says so.
- Label-based views miss passages. In each of the 16 covered, no-all cases joint
  called no, 1 to 10 listed passages had no support label: labeller and
  verifier did not agree on them. Those ambiguous passages are the ones that
  complete the requirement. The model answers every passage, so its picks
  include them.
- First fix tried: labelled parts plus every unlabelled listed passage. It made
  views too large: 12,010 train questions, median 1,742 tokens. Model picks
  are 790 at the median.
- Fix taken (owner: real picks on Colab, PR #168 merged as 3b7a81e): every
  split's views come from support run 211003's picks. Validation is the
  model-pick views.
- Each labelled set cost $0.04.

Raw file: `Downloads/res/result-support-20261017-211003.zip` (local).

## joint: support 211003's picks on coverage train and calibration (2026-10-18, run 20261018-013526)

The notebook's picks step (`train.py pack --picks`) read coverage train and
calibration with the support checkpoint, in 849 s on the L4.

| Split | Questions | Decided | Accuracy decided | TPR / TNR | AUROC | Undecided |
| --- | --- | --- | --- | --- | --- | --- |
| train (support's own) | 2,279 | 64.8% | 0.855 | 0.73 / 0.96 | 0.779 | 802 |
| calibration | 234 | 65.8% | 0.877 | 0.75 / 0.94 | 0.769 | 80 |
| validation (run 211003) | 219 | 52% | 0.842 | 0.77 / 0.90 | 0.742 | 105 |

- On its own train data, support decides 65%, against 52% on validation. Its
  decided accuracy is barely higher (0.855 against 0.842), so train picks are
  not much easier than serving's.
- Calibration decides as often as train. Validation's lower rate is that
  split, not overfitting.
- Joint candidates (`joint.py derive`):
  - train: 3,723 questions, of which 725 picks, 2,199 fewer and 799 padded;
    1,257 tokens median, 2,948 at p95, 4 over 4,096.
  - calibration: 80 picks, 2,443 tokens at most.
- Labelled as `full2` and `full2-cal`, Haiku on the API credit with Codex:

  | Split | Kept | Yes / no | Cost |
  | --- | --- | --- | --- |
  | train (`full2`, 23 rounds) | 2,159 of 3,723 (58%) | 1,196 / 963 | $3.51 |
  | calibration (`full2-cal`) | 46 of 80 | 11 / 35 | $0.03 |

  - Train labels by how the view was drawn: picks 227 yes / 221 no; fewer
    746 / 500; padded 223 / 242. In 60% of fewer views, the remaining picks
    still cover the requirement. Support's part picks often carry a redundant
    passage.
  - The guide adopted revisions at rounds 1 and 11, ending with 7 rules. One of
    them is the joint judgement itself: combine complementary facts across the
    passages. The first run stopped at 5,000 turns after 16 rounds; a rerun
    with higher caps replayed those turns and finished.
  - The joint splits: train `full2` (2,159), validation `picks-val` (72) and
    calibration `full2-cal` (46). Spend so far is about $62.9 of the $70 cap.

Raw file: `Downloads/res/result-picks-20261018-013526.zip` (local).

## joint first training (2026-10-18, run 20261018-044421)

Joint was fine-tuned from `typed` on 2,159 questions (2,027 states), 4 left out
for length. Chosen run: e3, label smoothing 0.1, by log loss. Training took 25
and 37 minutes; peak GPU memory was 8.2 GB.

On its own validation labels (72 questions, 21 yes; majority 0.708):

| Model | Accuracy | Yes TP / TN | AUROC | ECE |
| --- | --- | --- | --- | --- |
| typed zero-shot | 0.333 | 20/21, 4/51 | 0.503 | 0.248 |
| e2 | 0.639 | 11/21, 35/51 | 0.629 | 0.075 |
| **e3** | 0.625 | 12/21, 33/51 | 0.649 | 0.024 |

Coverage made from support 211003 plus joint (`joint.evaluate`):

| Coverage | Decided | Accuracy | TPR / TNR | AUROC |
| --- | --- | --- | --- | --- |
| support alone, undecided as no | 219 | 0.662 | — | 0.742 |
| **support + joint** | **219** | **0.740** | 0.68 / 0.79 | **0.825** |
| coverage model (023112) | 219 | 0.694 | 0.76 / 0.64 | 0.77 |

- support + joint is the best coverage so far: +4.6 points of accuracy over
  the coverage model and AUROC +0.055.
- Joint itself learned weakly. It was asked the 105 questions support left
  undecided, 56 of them covered. It got 63% right (TPR 0.61, TNR 0.65), with
  AUROC 0.673 against coverage labels. Its labels agree with coverage 94% of
  the time, so the model is far below its labels' ceiling.
- Calibration fitted a noul temperature of 4.44 on 46 items (11 yes). Every
  joint probability then falls between 0.33 and 0.67, so a confidence
  threshold would send all 105 questions to the LLM anyway.
- Likely limit: few distinct problems. The 725 picks views are the only
  distinct ones; the 2,199 fewer and 799 padded views are variants of the
  same states.

Raw file: `Downloads/res/result-joint-20261018-044421.zip` (local).

## joint aug1: random passage sets are easy (2026-10-18, PR #169, stopped)

`joint subsets train` drew two random sets of listed passages per coverage
train requirement, sized like the part picks: 4,364 questions. Labelling was
stopped after 12 of 29 rounds ($1.42).

| Set size | No | Yes |
| --- | --- | --- |
| 1 | 504 | 15 |
| 2 | 323 | 16 |
| 3–5 | 349 | 32 |
| 6+ | 157 | 38 |

- 1,333 of 1,434 labels are no. On covered requirements it is still 545 no to
  99 yes: random sets rarely hold the passages that matter.
- These are easy problems, unlike serving's views of relevant passages. The
  owner's rule from this: training data must span difficulty, weighted hard.
- Next (PR #170 merged as bbbd14c): picks from support runs 121439 and 094429
  on coverage train and calibration. Each run leaves other questions
  undecided. The 1,434 aug1 labels stay as a capped easy share: all 101 yes,
  and no up to about 10% of train.

## joint aug2: hard problems from two more support runs (2026-10-18, run 20261018-102603)

| Support run | Train decided | Accuracy decided | AUROC | Train undecided |
| --- | --- | --- | --- | --- |
| 211003 (aug5) | 64.8% | 0.855 | 0.779 | 802 |
| 121439 (aug4) | 61.5% | 0.859 | 0.783 | 878 |
| 094429 (early) | 98.8% | 0.722 | 0.802 | 28 |

- 094429 reads "all" freely: it decides nearly everything and is wrong more
  often. It adds little here.
- New candidates, none already labelled: 2,872 questions on 806 requirements,
  1,199 tokens median, none over 4,096.
  - 879 are on requirements 211003 decided, so they are new problems: picks
    227, fewer 372, padded 280.
  - 1,993 are other passage sets on requirements 211003 also left undecided.
  - By coverage label, 1,871 covered and 1,001 not. All are relevant passages
    support read as part, so the hard share.
- Labelled as `aug2` in 18 rounds: $2.59 over 5,182 turns, plus 178 turns
  that reported no cost (about $0.1). 1,716 of 2,872 questions kept (about
  65% a round; the rest are labeller-verifier disagreements). 545 yes / 1,171
  no, while coverage labels said covered for 1,871: a part-pick set often
  misses the passage that completes the requirement.
- Train by difficulty (owner's rule), 4,169 rows and 4,417 questions
  (2,575 no / 1,842 yes):

  | Share | Source | Questions (no / yes) |
  | --- | --- | --- |
  | hard | full2 (211003 picks) | 2,159 (963 / 1,196) |
  | hard | aug2 (121439, 094429 picks) | 1,716 (1,171 / 545) |
  | easy | aug1 random sets: every yes, no to about 10% | 542 (441 / 101) |

  The full2-only train is `train.v1-full2.jsonl`. Packed at bbbd14c.
- Data spend is about $67.0 of the $70 cap.

Raw file: `Downloads/res/result-picks-20261018-102603.zip` (local).

## joint second training: hard-weighted train (2026-10-19, run 20261018-210109)

Joint was fine-tuned from `typed` on the difficulty mix: 4,417 questions (4,169
states). Validation and calibration were the same as the first run. Chosen run:
e3, label smoothing 0.1. Training took 46 and 68 minutes; peak GPU memory was
8.2 GB.

On its own validation labels (72 questions, 21 yes):

| Model | Accuracy | Yes TP / TN | AUROC | ECE | Log loss |
| --- | --- | --- | --- | --- | --- |
| first run e3 | 0.625 | 12/21, 33/51 | 0.649 | 0.024 | — |
| e2 | 0.611 | 8/21, 36/51 | 0.563 | 0.134 | 0.708 |
| **e3** | **0.722** | 10/21, 42/51 | **0.668** | 0.054 | 0.588 |

Coverage made from support 211003 plus joint:

| Coverage | Accuracy | TPR / TNR | AUROC | Joint on the 105 undecided (AUROC) |
| --- | --- | --- | --- | --- |
| first joint | 0.740 | 0.68 / 0.79 | 0.825 | 0.63 (0.673) |
| **second joint** | **0.753** | 0.63 / 0.86 | 0.825 | 0.657 (0.680) |
| coverage model (023112) | 0.694 | 0.76 / 0.64 | 0.77 | — |

- Twice the train, mostly hard, moved joint's discrimination very little: AUROC
  on the 105 went from 0.673 to 0.680. Accuracy rose by saying no more often
  (TNR 0.82, TPR 0.52 on the 105). Data volume is not the lever.
- The probabilities are binary. Calibration temperature 2.78 with label
  smoothing 0.1 puts every joint answer near 0.25 or 0.75 (10th–90th
  percentile 0.25–0.75). Confidence carries no ranking within a class, so a
  threshold policy cannot pick the safe ones.
- Shortcut on passage count. Train's yes rate climbs with the view's passages:
  0.17, 0.26, 0.37 and 0.62 for 1, 2, 3 and 4 or more. On the 105, the model
  said yes to 3 of 32 one-part views (16 covered) and 20 of 29 views with four
  or more (17 covered). The count alone gives AUROC 0.57 on validation, and the
  model 0.67. Within one count it is 0.55 to 0.83 (n 12–26), so it reads some
  content too.
- One-part views are partly beyond joint's reach. When support read one
  passage as part and the requirement is covered, the rest of it sits in a
  passage support read as none, which joint does not see.

Raw file: `Downloads/res/result-joint-20261018-210109.zip` (local).

## joint diagnosis: data or evaluation? (2026-10-19, free)

The owner asked whether joint's flat result is a data problem or an evaluation
problem. Both were checked on local files; nothing was paid.

Evaluation:

- Validation is too small to tell runs apart. On 72 questions (21 yes) the
  second run's AUROC 0.668 has a bootstrap 95% CI of 0.52–0.82, and accuracy
  ±0.10. The first run's 0.649 sits inside it. Choosing e2 or e3 by log loss on
  72 questions is mostly noise.
- Calibration has 46 questions (11 yes), so the one temperature is fitted on
  11 positives.
- The 105-question coverage measure has a ceiling. Joint labels say no for
  16–18% of covered requirements (val 4 of 25, full2 268 of 1,456): the
  completing passage is one support read as none, which joint never sees.
  A perfect joint would still miss these against coverage labels.

Data:

- Labels are consistent. Joint yes where coverage says no: 8 of 1,196 in full2,
  35 of 545 in aug2, 0 in validation and calibration. Of 5,543 nested view
  pairs on the same requirement, 20 (0.4%) have a yes subset and a no superset.
- Fragment requirements ("I got this back from the generation run:") are 12%
  of train and 4 of 72 in validation, all 4 right. Not the cause.
- The passage-count shortcut is in the data. Train's yes rate is 0.17 / 0.26 /
  0.37 / 0.62 for 1 / 2 / 3 / 4+ passages, and the model follows it.
- Agreement filtering drops the hardest problems. aug2 kept 1,716 of 2,872;
  the 1,156 disagreements are exactly the ambiguous cases. Validation and
  calibration hold only agreed labels too, so they measure the easier part.
- Validation by length: 37 of 49 right under 3,000 characters, 15 of 23 above.

Literature (web, 2026-10-19):

- Encoders degrade as evidence must be combined across more passages: on
  HoVer, BERT pipelines fall sharply from two to four hops
  (arxiv.org/abs/2011.03088).
- BERT classifiers trained on LLM labels plateau earlier and are less stable
  across runs; the gap grows with task difficulty. Consistency ensembles
  recovered more than entropy filtering, 60–75% of the gap in all
  (arxiv.org/abs/2504.15432).
- Agreement filtering raises label quality but removes ambiguous samples
  (arxiv.org/abs/2412.07906).
- Label smoothing already calibrates; temperature scaling on top can worsen
  it (Müller et al., NeurIPS 2019). Here the two together leave joint with two
  output values.
- Laya's own card: base checkpoints are near chance zero-shot and need
  fine-tuning; `noul` answers can follow option labels (issue #156); no
  published accuracy past 4,000 tokens.

Reading: evaluation cannot currently measure progress, so the data
augmentation's effect is unknown rather than shown to be nil. The data has a
structural shortcut and lacks the disagreed hard cases.

## joint: wider evaluation and an even train (2026-10-19, PR #174 merged as 82ff81a)

The owner chose to widen evaluation and remove the passage-count shortcut.

- `pack --picks` now reads coverage validation too, so the other support runs'
  views (121439, 094429) join joint validation and calibration. Review round 1
  (P1) found such a view at Jaccard 0.83 to a train view: derive and subsets
  now hold every other split's joint rows, labelled or candidates
  (`joint.others`). Calibration candidates from the two runs: 78 + 2
  questions.
- #172 had left main failing its own CI (specs.py over its ratchet, a Windows
  temp test); #176 (fd7813d) fixed both first, at the owner's choice.
- A held-out evaluation from train, grouped by query (52 of 155 queries span
  several coverage states, so grouping by state leaked): 129 queries give 602
  questions (274 yes) after dropping subset views and 14 views near train.
  Its picks are in-sample for support, so it is reported apart from the
  coverage-validation views.
- Even train: yes rate 0.41 at every passage count, the minority duplicated up
  to twice, then the majority sampled down: 4,356 questions over 3,198 rows.
- Packed at 82ff81a for the picks run on Colab (no training in it).

### Picks run and final splits (result-picks-20261019-023336)

- Coverage validation, picks by run: 121439 decided 112 of 219 (accuracy
  decided 0.813, AUROC 0.770) and left 107 undecided; 094429 decided 216
  (0.662, AUROC 0.734). Their views: 74 rows / 88 questions and 3 / 3.
- Labelled with calibration's 80 as split `widen`: 148 states, 170 questions,
  1 round, $0.06. Kept 115, unsure 19, rejected 36 (the hard ones still drop
  out of evaluation, as in the diagnosis).
- The holdout halved by alternating queries left validation at 26% yes and
  calibration at 58%, since a few queries hold many rows. The most even of 200
  random query halvings was taken instead: 303 questions (146 yes) and 299
  (128 yes).
- Final splits, with `provenance.origin` on each eval row:

| split | questions (yes) | picks-val/cal | widen | holdout |
|---|---|---|---|---|
| validation | 437 (176) | 72 (21) | 62 (9) | 303 (146) |
| calibration | 398 (149) | 46 (11) | 53 (10) | 299 (128) |
| train | 4,354 (1,799) | — | — | — |

- One picks-val view was near a train view: its two train rows were dropped.
  Earlier splits are kept as `*.v2-*.jsonl`.
- Packed `joint` at 82ff81a for the third training.

## joint third training: even train, wider evaluation (2026-10-19, run 20261019-034012)

Train 4,354 questions (yes 0.41 at every passage count); validation 437,
calibration 398. e3 chosen; temperature 2.45. Two validation questions were
too long to score.

| Validation source | n | Yes | Majority | Accuracy | AUROC [95% bootstrap] | Mean P(yes) |
| --- | --- | --- | --- | --- | --- | --- |
| all | 435 | 174 | 0.600 | 0.683 | 0.782 [0.73–0.83] | 0.54 |
| picks-val (run 211003) | 72 | 21 | 0.708 | 0.639 | 0.656 [0.53–0.79] | 0.48 |
| widen (runs 121439, 094429) | 62 | 9 | 0.855 | 0.710 | 0.764 [0.54–0.92] | 0.42 |
| picks-val + widen | 134 | 30 | 0.776 | 0.672 | 0.703 [0.60–0.81] | — |
| train holdout | 301 | 144 | 0.522 | 0.688 | 0.777 [0.73–0.83] | 0.58 |

The same 72 picks-val questions under run 2 (210109): accuracy 0.722, AUROC
0.668, mean P(yes) 0.39.

By passage count (all sources): AUROC 0.68, 0.64, 0.62 and 0.79 for 1, 2, 3
and 4 or more passages; mean P(yes) 0.35, 0.45, 0.54 and 0.69, while
validation's yes rate is 0.17, 0.29, 0.38 and 0.59.

Coverage made from support 211003 plus joint:

| Coverage | Accuracy | TPR / TNR | AUROC | Joint on the 105 (TPR, TNR, AUROC) |
| --- | --- | --- | --- | --- |
| support alone, undecided as no | 0.685 | — | — | — |
| second joint (210109) | **0.753** | 0.63 / 0.86 | **0.825** | 29/56, 40/49, 0.680 |
| third joint (034012) | 0.731 | 0.67 / 0.78 | 0.806 | 33/56, 31/49, 0.660 |

- The wider evaluation works: over 435 questions the AUROC interval is
  ±0.05, against 0.52–0.82 for the 72 alone.
- The even train did not help on the views joint actually serves. On
  picks-val, AUROC 0.656 against 0.668, and accuracy fell from 0.722 to
  0.639. The model moved toward yes (mean P(yes) 0.39 → 0.48) on a set with
  29% yes.
- The passage-count shortcut is gone as a cause. Within one count,
  discrimination stays 0.62–0.68 below four passages. What the model reads in
  the passages is weak at every count, not only the count.
- Joint scores 0.78 on train's held-out views and 0.70 on coverage
  validation's picks views. Coverage validation's views are harder or labelled
  differently. Its states are new problems, whereas train holdout shares the
  retrieval pool's distribution.
- Three trainings (aug data, hard-weighted, even) leave joint on the 105
  between AUROC 0.66 and 0.68. Run 2 stays the best serving checkpoint:
  coverage 0.753 / 0.825, +0.07 over support alone.

### Why augmenting the weak slices did not lift joint (2026-10-19, free)

The owner asked why augmenting the weak slices did not lift joint the way it
lifted other tasks, and where the dataset's bias comes from.

- It is not underfitting. Train loss falls to 0.09 by epoch 3, while
  validation sits at 0.68: a generalization gap.
- It is not label noise. Within one coverage state and requirement, a passage
  set labelled yes makes every superset yes in 99.5% of 3,635 subset pairs in
  train, and in 100% of 359 pairs in calibration.
- It is not the requirement id. Single-requirement states score lower (AUROC
  0.67) than states with several (0.83).
- **A word-overlap score beats the model on every source.** The score is the
  share of the requirement's content words that appear in the passages. Its
  validation AUROC is 0.79 on picks-val (model 0.66), 0.90 on widen (0.76),
  0.80 on holdout (0.78) and 0.82 overall (0.78).
- The cue is weak in train: overlap AUROC is 0.64 on train's picks views and
  0.66–0.73 on the variants. Passage count gives 0.49–0.56 in train, against
  0.75 in validation. Train's cue-label relation differs from serving's:
  - hard-weighting (run 2) kept the cases where surface cues mislead;
  - evening the passage count (run 3) removed a cue that is real in
    validation, where the yes rate climbs 0.17 → 0.60 with passage count;
  - 83% of train is synthetic variants (fewer 2,057, padded 1,011, subset 536
    questions), against 750 picks-shaped questions, the shape joint serves.
- Concentration: the 4,354 questions come from 706 queries (median 4 per
  query, at most 49). The top 10% of queries hold 34% of the questions. In
  multi-question queries, 53% of questions share one label across the whole
  query, so memorizing a query pays in train and not on new queries.
- Reading: the model learned that the obvious cues are unreliable, and on
  serving-shaped data, where they are reliable, it does worse than the cue.
  "Hard" chosen by errors and disagreements selects cases where cues mislead.
  Weighting train toward them without anchoring to the serving distribution
  inverts what the data teaches.
- Pool for a rebuild without new labels: 4,500 distinct labelled train-side
  views, 4,707 questions over 1,035 queries (before removing the holdout
  queries now in validation).

### Train rebuilt to the serving distribution (2026-10-19, owner's choice)

The pool is every labelled train-side view. Views of validation and
calibration queries are left out, as are views near an eval view. That leaves
4,594 questions over 967 queries.

- Natural passage counts: no evening.
- At most 12 questions per query, picks-shaped views first (the old train had
  up to 49).
- Random subsets are at most 10% of train.
- Hard means the overlap cue gets the question wrong, at threshold 0.69 (the
  best cut on serving-shaped validation, cue accuracy 0.84). The capped set
  is already 35% hard, the share chosen, so no reweighting is needed.
  - Low overlap but covered: 874 questions.
  - High overlap but not covered: 259.
  - Capping the larger direction at 2:1 or 1.5:1 shrank train to 2,183 or
    1,804 questions, and moved overlap AUROC the wrong way (0.738, 0.723).
- Result: 3,134 rows, 3,278 questions over 715 queries, yes 0.40.
  - Yes rate by passage count: 0.17, 0.27, 0.39, 0.60, matching validation's
    0.17, 0.29, 0.38, 0.60.
  - Overlap AUROC 0.757, passage-count AUROC 0.714.
  - Drawn: fewer 1,542, padded 756, picks 646, subset 334.
- No eval view is near train. The previous train is kept as
  `train.v3-even.jsonl`. Script: the session's `jointserve.py`.
- Packed `joint` at a011636 (no laya change since 82ff81a). The bar for the
  next run is the word-overlap score on each source (picks-val 0.79, widen
  0.90, holdout 0.80), not only run 2.

## joint fourth training: serving-shaped train (2026-10-19, run 20261019-060640)

Train 3,278 questions (rebuilt above); validation and calibration unchanged.
e3 chosen. Split hashes match the pack (a011636).

| Validation source | n | Run 4 acc | Run 4 AUROC [95%] | Run 3 acc / AUROC | Word overlap AUROC |
| --- | --- | --- | --- | --- | --- |
| picks-val | 72 | 0.667 | 0.645 [0.52–0.78] | 0.639 / 0.656 | 0.793 |
| widen | 62 | 0.758 | 0.634 [0.51–0.80] | 0.710 / 0.764 | 0.897 |
| holdout | 301 | 0.718 | 0.731 [0.67–0.79] | 0.688 / 0.777 | 0.802 |
| all | 435 | **0.715** | 0.739 [0.69–0.78] | 0.683 / 0.782 | 0.816 |

Coverage made from support 211003 plus joint:

| Coverage | Accuracy | TPR / TNR | AUROC | Joint on the 105 (TP, TN, AUROC) |
| --- | --- | --- | --- | --- |
| second joint (210109) | **0.753** | 0.63 / 0.86 | **0.825** | 29/56, 40/49, 0.680 |
| third joint (034012) | 0.731 | 0.67 / 0.78 | 0.806 | 33/56, 31/49, 0.660 |
| fourth joint (060640) | 0.744 | 0.64 / 0.84 | 0.810 | 30/56, 37/49, 0.666 |
| word overlap as joint | 0.708 | — | 0.805 | AUROC 0.607 |

- Accuracy rose on every source, and AUROC fell on widen and holdout. The
  model still trails word overlap on the joint labels.
- On coverage's own answers the order flips: on the 105, word overlap gets
  0.607 and every joint run 0.66–0.68. The joint labels the labellers agreed
  on follow lexical overlap more than coverage's truth does.
- The 105 split by agreement:
  - 72 questions whose view was labelled (labeller and verifier agreed), 25
    covered. The joint label equals coverage on 68 of the 72, so a perfect
    joint-label model would be about 0.94 there. Runs 2–4 get AUROC
    0.58–0.60 and word overlap 0.67.
  - 33 questions whose view was not agreed, 31 covered. The model is right on
    0.61–0.64; AUROC is meaningless with 2 negatives. These are the ones
    labellers could not settle, and train lacks them by construction.
- Reading: four trainings over three different train designs (more data,
  hard-weighted, even, serving-shaped) leave joint on the 105 between AUROC
  0.66 and 0.68. Changing the data's distribution is not lifting it, and on
  clean, agreed questions the model is below a word count. Run 2 stays the
  best serving checkpoint.

Raw files (local): run 4 `Downloads/res/result-joint-20261019-060640.zip`;
run 3 `Downloads/res/result-joint-20261019-034012.zip`. A later
`result-setup-20261019-053236.zip` holds only a setup step, with no training.

## Policy run for the seven learned kinds (packed 2026-10-21)

Owner's choice while the coverage gate fills: fit each learned kind's
threshold policy (plan stage 4). PR #202 (merged as 1d499a6) has the
notebook save the chosen checkpoint's calibration probabilities and adds
`python tool/eval/policy.py --laya <result zip>...`, writing
`eval/laya/policy/<kind>.json`. Run 20261015-053449 kept no calibration
probabilities, and its result zips are gone, so the seven kinds are
retrained: `laya/data/colab.zip` packed at 1d499a6 with analysis, ask,
progress, redirect, route, source and useful. Every split is byte for
byte the ca800a6 bundle's, so the new validation scores also measure
seed spread against the table above. `ask` and `progress` have no cost
table and get no rule. The previous bundle is kept as
`colab-support-joint-43775fd.zip`.

## Policy run result (run 20261021-045345)

No kind failed. Every kind chose the same epoch as 20261015-053449, so the
seed spread is small:

| Kind | Chosen | Validation then | Validation now | ECE now |
| --- | --- | --- | --- | --- |
| ask | e2 | 0.986 | 0.990 | 0.010 |
| progress | e3 | 0.987 | 0.991 | 0.002 |
| route | e2 | 0.984 | 0.984 | 0.010 |
| redirect | e3 | 0.986 | 0.986 | 0.033 |
| source | e3 | 0.950 | 0.950 | 0.033 |
| analysis | e3 | 0.866 | 0.862 | 0.030 |
| useful | e3 | 0.833 | 0.850 | 0.043 |

Fitted rules on the calibration split (`eval/laya/policy/`, PR #205):

| Kind | Rule no / yes | Cost / n | Deferred | Jev's rule (its own 89–192 cases) |
| --- | --- | --- | --- | --- |
| route | 0.35 / 0.80 | 5 / 322 | 1 | 0.25 / 0.80, cost 0 / 89 |
| source | 0.05 / 0.95 | 23 / 205 | 39 | 0.35 / 0.85, cost 20 / 192 |
| useful | 0.10 / 0.70 | 103.5 / 300 | 175 | 0.20 / 0.60, cost 7 / 159 |
| redirect | 0.20 / 0.95 | 44 / 304 | 7 | 0.40 / 0.80, cost 2 / 159 |
| analysis | 0.20 / 0.70 | 36 / 250 | 10 | arm D costs |

Insights:

- Retraining on identical data reproduces the checkpoints' quality, so a
  difference above about 0.02 on these validation splits is not seed noise.
- `useful` is the weak policy: it never answers no (138 of 154 negatives
  deferred) because a dropped supporting passage costs 10 and 37 positives
  sit between 0.10 and 0.70. Its cost per question (0.35) is eight times
  Jev's (0.044), though on different questions and labels.
- `redirect` answers no to 8 of its 18 positives with confidence; its
  minority recall is the risk, as TPR 0.70 already showed.
- Jev's and Laya's numbers are on different sets; the gate (stage 6) is
  the comparison. Raw files: `Downloads/res/result-<kind>-20261021-045345.zip`.

## Why `useful`'s policy is weak (diagnosis, 2026-10-21)

- The floor is not the cause. Label smoothing 0.1 and noul temperature
  1.72 keep P(yes) in 0.111–0.882, but the bottom bucket (P ≤ 0.15) holds
  75 negatives and 10 positives. With a dropped positive costing 10 and a
  deferral 0.5, answering no pays only below about 5% positives.
- The measurement is clustered. `useful`'s validation and calibration are
  the pilot's (2026-10-13): 33 and 32 queries, up to 12 passages each,
  labelled by Sonnet + Sol. Train's full1/full2 (1,024 of 1,524) has 647
  queries at 1–2 passages, labelled by Haiku + Sol. The 51 confident misses
  on positives across both splits come from 24 queries, 6 from one query.
  So the effective size is about 32 queries, not 300 questions, and the
  rule is fitted on a different shape and labeller than most of train.
- The full run's validation and calibration generation (380 and 390
  queries, all ten kinds) is captured in `retrieval/generate.runs.jsonl`.
  `useful` got no new candidates because its old files already met the
  target. Rebuilding them from those claims, with no teacher call, gives
  620 and 624 candidates.

## New `useful` validation and calibration (2026-10-21)

Owner's choice: remake the measurement before augmenting. PR #206 (merged
as fea47a3) added `python laya/generate.py <split> --rebuild KIND OUT`.

| Split | Candidates (queries) | Labels | True / False | Label queries | Unsure / rejected | Cost $ |
| --- | --- | --- | --- | --- | --- | --- |
| full1-val | 619 (310) | 476 (77%) | 233 / 243 | 288 | 79 / 64 | 0.16 |
| full1-cal | 624 (312) | 487 (78%) | 204 / 283 | 294 | 76 / 61 | 0.16 |

- Labelling: Haiku on the API key, Codex Sol verifier, one round, guide 0
  (`useful` adopted no guide in train). Two Haiku turns failed when the
  CLI's `.claude.json` was being rewritten concurrently; retries recovered.
- No state or query is shared between train and the new splits, or
  between the two.
- `useful/validation.jsonl` and `calibration.jsonl` are now full1-val and
  full1-cal; the pilot's are kept as `v1-validation.jsonl` and
  `v1-calibration.jsonl`.
- `laya/data/colab.zip` packed at fea47a3 with `useful` only. Train is
  byte for byte the 1d499a6 bundle's (d8efb107…), so the run changes only
  the measurement. The seven-kind bundle is kept as
  `colab-policy7-1d499a6.zip`.

## `useful` retrained on the rebuilt validation and calibration (run 20261021-075007)

Packed at fea47a3, `useful` only, same train. It chose e3-ls0.1 again: e2 scored 0.8235 and e3 0.8172, but e3 had lower ECE and log loss.

| Measure | Pilot split (run 045345) | Rebuilt split (run 075007) |
| --- | --- | --- |
| Validation questions / queries | 300 / 33 | 476 / 288 |
| Validation accuracy | 0.850 | 0.817 (base 0.504) |
| Rule no / yes | 0.10 / 0.70 | 0.10 / 0.65 |
| Cost / calibration questions | 103.5 / 300 | 139 / 487 |
| Deferred | 175 (58%) | 210 (43%) |
| Negatives answered no | 16 of 154 | 103 of 283 (1 positive answered no) |

Insights:
- The two accuracies are not comparable. The rebuilt split is the honest one, with 288 queries and the same labeller as train. On it the policy improved: it now answers no to a third of the negatives at almost no recall cost.
- The 87 validation errors (33 false yes, 54 false no) are spread across query types: partial 0.17, bridging 0.18, answered 0.21, false_premise 0.16, unanswered 0.19. Across intents they range from 0.13 to 0.27, with lookup highest at 0.27. No single slice is weak, so the error looks like general difficulty or label noise rather than a missing pattern.
- 43% deferred is still high. The next lever is a large train augmentation across the board (train is only 1,524), or a label-agreement check on the errors before paying for more data.
- PR #207 carries the refitted `eval/laya/policy/useful.json`.

### Label check on the errors (2026-10-21)

The check sent the 87 validation errors and 40 correct controls to Sonnet as a second labeller (`errcheck-val`, Sol verifier, one round). It stopped at the turn limit after 41 Sonnet turns and $0.18. The cap was $0.15; parallel turns overshoot it, and Sonnet costs about $0.0045 a turn, roughly 15× Haiku. No round settled, so the Sonnet answers were read straight from the turns file:

| Rows | Sonnet agrees with label | Sonnet agrees with model |
| --- | --- | --- |
| Model wrong (30) | 26 (23 at confidence ≥ 0.8) | 4 (all below 0.8) |
| Model right (11) | 11 | — |

The labels hold: most of `useful`'s validation errors are real model errors, not Haiku noise. More train data, not relabelling, is the lever. Train is only 1,524 rows and the errors are spread across slices, so augment broadly.

### Train augmentation full3 (2026-10-21, owner-approved, cap $1.5)

- Candidates: `python laya/generate.py train --rebuild useful full3-train` cut 2,172 new candidates from 2,726 train-split queries' captured claims, at no generation cost. 3,144 were dropped as near an already kept candidate.
- Labels: Haiku with the Sol verifier, one round, $0.56 in all.
  - The first run stopped at guided's default `--max-turns 2000` before the round settled. Labeller turns alone were 2,172. The rerun with `--max-turns 4000` replayed the paid turns.
  - Kept 1,647 of 2,172 (278 unsure, 246 rejected, 1 invalid); 664 are positive (40%, against 41% in the old train).
- No state or query overlap with train, validation or calibration.
- `train.jsonl` is now v2 + full3: 3,171 rows. The old 1,524-row train is `v2-train.jsonl`.
- Packed at e0c6c1c with `useful` only: train b3979f090ff0, validation and calibration unchanged (da686685503a, a1204765c338). The previous bundle is `colab-useful-full1-fea47a3.zip`.

### Result of full3 (run 20261021-114803)

| Measure | Train 1,524 (run 075007) | Train 3,171 (run 114803) |
| --- | --- | --- |
| Chosen | e3-ls0.1 | e2-ls0.1 |
| Validation accuracy | 0.817 | 0.849 (e3 0.830) |
| Validation ECE / log loss | 0.068 / 0.432 | 0.016 / 0.395 |
| Policy on the 0.05 grid | 0.10 / 0.65, cost 139, 210 deferred | 0.10 / 0.85, cost 176.5, 319 deferred |
| Best rule on a 0.005 grid | cost 138.5 | cost 143.5 (0.125 / 0.855) |

Insights:
- Doubling train helped accuracy and calibration well beyond seed noise (+0.032), but not the cost policy. On calibration, the cost is at best equal and on the 0.05 grid clearly worse. The refit was not committed; `eval/laya/policy/useful.json` stays #207's.
- Why: the new model's P(yes) floor sits at 0.10–0.15. 202 calibration rows fall in (0.10, 0.15], 18 of them positive, and none fall at or below 0.10, so the 0.05 grid cannot cut inside the floor. The old model had 104 rows at or below 0.10 with one positive.
- Cost is dominated by positives with low P(yes) (a dropped positive costs 10). More data reduced them (at or below 0.15: 26 then 18) but mixed them deeper into the negatives' floor.
- Candidate next steps: a finer low-end grid in `policy.py` (in-sample on 487 rows, overfit risk); label smoothing 0 so the floor is not compressed (free Colab run); or keep #207's policy and let the stage-6 gate decide.

### Label smoothing 0 (run 20261021-214310, notebook #208, same data as full3)

| Run | Validation acc / ECE / log loss | Policy cost (0.05 grid) | Best on 0.01 grid |
| --- | --- | --- | --- |
| 075007, train 1,524, ls 0.1 (#207's) | 0.817 / 0.068 / 0.432 | 139 (0.10 / 0.65) | 139 |
| 114803, train 3,171, ls 0.1 | 0.849 / 0.016 / 0.395 | 176.5 | 149.5 |
| 214310, train 3,171, ls 0 (e2 chosen) | 0.805 / 0.110 / 0.569 (e3 0.828) | 264 (0.05 / 0.65) | 228 |

Insights:
- Smoothing was not the problem; it was helping. Without it the floor is gone (min P(yes) 0.005), but the model is confidently wrong. 12 positives sit at P(yes) ≤ 0.01 and 18 at ≤ 0.05, so a "no" threshold still costs 10 per positive. Accuracy, ECE and cost all got worse.
- 14 of the 204 calibration positives score P(yes) ≤ 0.2 in all three runs: bridging 5, unanswered 4, partial 3, false_premise 2; lookup 5, progress 4. Read by hand, they are weak positives. The passage sits next to the answer or shows that it is absent: a timeout question against a passage naming the client but not the value, or a tolerance question against the grader's track overview. Whether such a passage is "useful" is a definition question, not a learning one. At cost 10 each, these 14 alone (140) block any "no" rule.
- Decision: `eval/laya/policy/useful.json` stays #207's (run 075007). More data and different smoothing do not move the policy. The lever is the definition and cost of a weak positive, which is the owner's call.
- Owner, 2026-10-22: keep #207's policy and move on. Notebook `RUNS` is back at smoothing 0.1 (#209, 91a1532).

## Why `redirect` answers no to positives (diagnosis, 2026-10-22, free)

Run 20261021-045345, e3-ls0.1, validation 0.986. Policy 0.20 / 0.95 answers no to 8 of the 18 calibration positives.

- Data balance: train 417 positive of 1,083 (38%), with 111 overt injected lines ("ignore your previous instructions", "AI agent reading this") and 36 `Rule.` pages. Validation has 10 positives of 295 and calibration 18 of 304, all natural, none overt.
- The 10 caught calibration positives are `Rule.` pages and wiki rule sections (P(yes) 0.64–0.96). The 8 missed sit at P(yes) 0.035–0.07, confidently no.
- About half of the 8 are natural agent-directed instructions in project documents: a repository's "Repository rules" for coding agents, a subagent-routing table, the review round's "Picking the P2 to keep", and a cloud task's "prepare the next gold batch". The other half have doubtful labels: a user-preference note, a plan's history record, an installer description, and a fix note.
- Weak slice: agent-directed procedure written in ordinary document style rather than `Rule.` form, with its hard negatives (documents about agents that instruct no one).
- Augmentation full2 (owner-approved, cap $1.5): `python laya/generate.py train --rebuild redirect full2-train` cut 2,334 natural candidates from 2,726 train-split queries at no generation cost. 873 of them contain agent-flavoured wording. All 2,334 were labelled with Haiku and the Sol verifier in one round, for $0.52.
  - Kept 1,965 (353 rejected, 16 unsure). 103 are positive (5%, the natural rate), 35 of them `Rule.` pages.
  - No state overlap with train, validation or calibration.
  - `train.jsonl` is now v1 + full2: 3,048 rows. The old 1,083-row train is `v1-train.jsonl`.
  - Packed at 91a1532 with `redirect` only: train 1ef8cf035d02, validation and calibration unchanged (4597bb36cf7a, 3f025d065e6c). The previous bundle, useful full3, is `colab-useful-full3-e0c6c1c.zip`.
  - Baseline to beat (run 045345): validation 0.986, 3 of 10 positives missed; policy 0.20 / 0.95 at cost 44 / 304, answering no to 8 of 18 calibration positives.

### Result of redirect full2 (run 20261022-014821)

| Measure | Train 1,083 (run 045345) | Train 3,048 (run 014821) |
| --- | --- | --- |
| Chosen | e3-ls0.1 | e2-ls0.1 (e3 tied at 0.9898) |
| Validation accuracy / ECE / log loss | 0.986 / — / — | 0.990 / 0.031 / 0.053 |
| Validation positives missed | 3 of 10 | 2 of 10 (1 false yes) |
| Policy (redirect costs: positive→no 5) | 0.20 / 0.95, cost 44 | 0.20 / 0.95, cost 33.5 |
| Calibration positives answered no / deferred | 8 / 7 | 6 / 1 |

Insights:
- Natural positives helped where the overt injected ones could not. Two of the eight missed calibration positives now score 0.96 or more, and the confident split is sharper: positives sit at 0.96–0.98 or 0.03–0.04, with almost nothing between.
- The 6 still missed sit at P(yes) 0.030–0.039, confidently no. They are the doubtful-label half of the diagnosis plus a few agent-procedure passages. More of the same data is unlikely to move them; a label check on them is the next lever, if any.
- PR #210 carries the refitted `eval/laya/policy/redirect.json`.
