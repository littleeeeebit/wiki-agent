# Stage 3 — labels by two-labeller agreement

See the [overview](0-overview.md). After [stage 2](2-data.md).

Purpose. Give every train, validation and calibration state a label two
independent LLMs agree on, dropping the rest, until each kind has 3,000
usable train labels, 300 validation and 300 calibration. No person labels
anything (owner, 2026-10-13). Where the gate test's answers come from is
still open (stage 2).

## Rule (all three splits)

- Two labellers from different model families each answer the kind's
  question for the state, with the same instructions Jev receives, and give
  a probability. They run as host turns (`agent.chat_session.oneshot`: no
  tools, an empty folder) with `laya/prompts/label.md`.
- Agreement: same valid answer, both probabilities at least 0.7, and, for a
  generated state, the answer the generator intended. An agreed question is
  usable with that answer.
- Anything else is dropped: a disagreement, an agreement below 0.7, an
  invalid answer, or an agreed answer other than the intended one. Each
  drop is a line of the error notebook (below) with its category.
- A failed turn settles nothing: it is retried on a rerun, never read as a
  disagreement.
- Validation and calibration take the same rule. The same two labellers are
  used for all three splits, so their cost is in the budget below.
- Each label records labeller models, the source observation and the state
  hash.

## Guided labelling — a labeller that improves with use (owner, 2026-10-14)

Two frontier labellers per state cost about $0.01 a label for `useful`
(Sonnet $0.0075 a turn; the Codex side is a subscription), and the owner
asked for cheaper labels. The labelling stage becomes an RRSI loop whose
candidate is the labeller's guide (`laya/guided.py`):

- A small labeller (Claude Haiku 5.5) answers each state, one turn a
  state, with `laya/prompts/label.md` and the kind's current guide.
- A stronger verifier from another family (Codex `gpt-6.1-sol`, high)
  checks the labels 20 at a time with `laya/prompts/verify.md`: it quotes
  the evidence, writes its own answer, and only then compares. Each batch
  also holds two items copied with the answer flipped; a batch that
  endorses both an answer and its opposite is checked once more,
  reshuffled, and dropped the second time (`verifier_unreliable`).
- A label is kept when both answers match at confidence 0.7 or more.
  Rejections go to the error notebook (`verifier_reject`).
- After each round the verifier writes a candidate guide from the
  rejections and the whole guide history (`laya/prompts/lesson.md`). The
  next round labels its states with both the current and the candidate
  guide; the verifier judges every answer without knowing which guide wrote
  it (an answer both gave is judged once). The candidate is adopted only
  when its paired gain in kept labels clears the noise floor
  (`loop.floor`), and the round keeps the adopted guide's labels. A guide
  not adopted stays in the history. No earlier labels are needed, so this
  works for kinds that have none.
- Every call is journalled under its prompt's name and text, model and
  payload, so a rerun pays only for turns not yet made; a failed call is
  journalled with its cost and error and asked again, never replayed.
  `--max-usd` caps the journal's total, failed calls included. Two workers
  asking the same turn at once make one call.
- A round with a failed turn left settles nothing: no trial decision, no
  label and no lesson. The run stops there (`failed_turns`), and a rerun
  replays what succeeded and finishes the round. A lesson whose `rules` is
  not a list of strings counts as a failed turn, and a failed lesson stops
  the run after its round.
- A split's guide and label files carry their setup (the three prompts'
  text, the models, effort, seed, round size, and the rows' states and
  questions in order, never their earlier answers); a run with another
  setup refuses to add to them, so a file never mixes labels an edited
  prompt would not give. More rounds of the same setup continue it.

The two-labeller rule above stays for the existing splits.

Pilot (2026-10-14, `useful`'s 740 train candidates, approved up to $10):
536 kept (72%), 99.6% matching the earlier two-frontier labels where they
exist, $0.449 for 1,100 Haiku turns, about $0.0008 a kept label against
$0.0105. No verifier batch endorsed a mirror. The first design judged
guides on a 60-question probe of earlier agreed labels: Haiku already
scored 0.883 there and no guide moved it beyond the noise, which is why a
guide is now judged on the next round's states.

## Error notebook

A local wiki under `laya/notes/` (ignored by Git: it quotes production
states) keeps every question the pipeline drops or the trained model gets
wrong, so a later round reads why, as RRSI keeps its history:

| Category | Recorded when |
| --- | --- |
| `disagree` | the two labellers gave different answers |
| `unsure` | the same answer, but a confidence under 0.7 |
| `invalid` | an answer the question does not offer, missing, or malformed |
| `teacher_miss` | the labellers agreed on an answer other than the generator's |
| `off_slice` | a generated state lacks the feature its slice was aimed at |
| `leak` | a generated state duplicates a gate or validation state |
| `student_wrong` | the trained model answered a validation question wrongly |
| `verifier_reject` | guided labelling: the verifier's answer differed from the labeller's, or it was unsure |
| `verifier_unreliable` | guided labelling: the batch endorsed an answer and its planted opposite, twice |

Each error is one ledger line (`<kind>/ledger.jsonl`). A cluster of one
category and one slice gets a page (`<kind>/<category>--<slice>.md`) at
five errors: front matter (kind, category, slice, count, rounds, status)
and cases written by code, cause and next action written by a model
(`laya/prompts/note.md`). Status moves `open` → `targeted` → `resolved`,
`falsified` or `ambiguous`. `laya/README.md` has the page format.

## Completion and refill

- A split is complete when it holds its target of usable labels (train
  3,000, validation 300, calibration 300).
- A dropped question's place is filled from stage 2's 20% surplus,
  labelled under the same rule.
- If the surplus runs out before a split is complete, the kind stops at
  stage 3 and is reported with its counts. It does not train on fewer.
  Generating more needs the owner's approval as a new budget.

## Budget

Per kind, stage 2 generates 4,320 candidates: the 3,600 usable targets
(train 3,000, validation 300, calibration 300) and a 20% surplus per split
(600, 60, 60). Candidates are labelled in order and labelling of a split
stops when its target is met, so the surplus is labelled only to replace
drops. The upper bound is 4,320 × two labellers = 8,640 labels; with about
2k input tokens a label, about 17.3M input tokens a kind and about 242M for
fourteen, before augmentation. Pilot labels count toward the targets. The
approved limit must cover the upper bound, or the owner accepts that a kind
may stop short (below). The real figure comes from the pilot.

## Pilot, then the full run

1. Pilot: 200 states of `useful`, with the owner's approval of its
   budget. Measure agreement rate, drops by category, and cost per label.
2. The owner approves the full run with those numbers and a total turn
   limit. The tool stops at the limit; spend already made is reported, not
   undone.

## Tool

`laya/label.py <kind> <split> --target N --labellers A B --max-turns N`
runs both labellers within the turn limit and resumes without paying twice;
nothing is sent without `--labellers`, and `--dry` shows the first turn.
`laya/notes.py` keeps the error notebook.

## Acceptance

- Each kind's three splits are complete with usable labels and
  provenance, or the kind is stopped and reported.
- Reported per kind and split: agreement rate, drops by category, refills,
  turns and spend.

## Tests

`laya/test_laya.py` with stubbed labellers: every drop category; refill
after drops; resume without paying twice; a failed turn retried, not
counted as a disagreement; the turn limit; notebook pages, statuses and the
generator's brief.

## Rollback

Labels and the notebook are local files under `laya/data/` and
`laya/notes/`; delete them. No product code changes.
