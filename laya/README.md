# laya — data, labels, training and the error notebook

Everything that turns production decision requests into fourteen fine-tuned Laya models
lives here (`docs/plans/laya/`). The serving side (the provider map in `tool/decision`)
does not; it reads only the checkpoints and policies this folder produces.

This folder is not a Python package. Its modules import each other by bare name, so
`import laya` always means the installed Laya package. Run them as scripts from the
repository root.

| File | What it does |
| --- | --- |
| `store.py` | Append-only JSONL journals that survive an interrupted write; whole-file replacement for pages |
| `kinds.py` | The fourteen kinds, and which kind a production question name is |
| `pool.py` | Production requests from the local Langfuse (and action requests from specs) to `data/pool.jsonl`, one row per distinct state |
| `gate.py` | Reserves the gate test (`eval/laya/tests.json`) and finds near-duplicates of it, by whole state or passage view |
| `stage1.py` | Stage 1's measurements: length, latency, option order, zero-shot agreement with Jev |
| `generate.py` | Candidate states for a passage kind: teacher-written questions run through the production path, decider stubbed |
| `label.py` | Two-labeller agreement labels; every dropped question goes to the error notebook |
| `guided.py` | Guided labelling: a small labeller, a batch verifier, and a guide that improves by RRSI rounds |
| `notes.py` | The error notebook: ledger, pages, statuses, the brief the generator reads |
| `train.py` | Fine-tune one kind from the base, calibrate, score validation; `pack` zips code and labels for Colab |
| `loop.py` | The recursive improvement loop (RRSI) for one kind |
| `prompts/` | The teacher, labeller, verifier, lesson and note-writer prompts |
| `notebooks/train.ipynb` | One kind, end to end, for a GPU session |
| `test_laya.py` | The pipeline with stub labellers and models |

Ignored by Git, local only: `data/` (pools, candidates, labels, ledgers, rounds),
`notes/` (the error notebook, which quotes production states) and `checkpoints/`.

## Labels

Two labellers from different model families answer each candidate state as host turns
(Claude or Codex, no tools, an empty folder, `prompts/label.md`). A question is labelled
when both give the same valid answer with confidence at least 0.7, and, for a generated
state, that answer is the one the generator intended. Every other question is dropped and
the next candidate takes its place: no person labels anything. The ledger keeps every turn,
so a rerun pays only for turns it has not made; a failed turn is retried, never read as a
disagreement.

Guided labelling (`guided.py`) is the cheaper path: a small labeller answers each state
with the kind's guide, a stronger verifier from another family checks 20 labels a turn
(two of them planted copies with the answer flipped, so a batch that endorses both an
answer and its opposite is caught), and after each round the verifier rewrites the guide
from its rejections. The next round labels its states with both guides, the verifier
judging every answer without knowing which guide wrote it, and the new guide is adopted
only when it earns more verified labels by more than the noise floor; no earlier labels
are needed. Its docstring has the full round.

## The loop

After RRSI (google-research/rrsi): a round proposes a data candidate with the whole
history in view, and it is adopted only when its paired gain on validation clears the
noise floor (two bootstrap standard deviations, resampling whole states). Paid turns
(generation, labelling, note writing) come from one approved total, `max_turns`; a round
starts only when what it takes is left, and labelling that stops short stops the loop
rather than condemning the slice; running the loop again continues it from
`data/<kind>/rounds.jsonl`. A slice whose round was not adopted is
`falsified` and never aimed at again; after such a stall the next round tries a feature
not tried since. After the last round each adopted round is removed again if keeping it
gains no more than the noise floor. The gate test never enters the loop. `loop.py`'s
docstring has the full round.

## The error notebook

Every question the pipeline throws away or gets wrong is one line of
`notes/<kind>/ledger.jsonl`:

| Category | Recorded when |
| --- | --- |
| `disagree` | the two labellers gave different answers |
| `unsure` | the same answer, but a labeller's confidence was under 0.7 |
| `invalid` | an answer the question does not offer, missing, or malformed |
| `teacher_miss` | the labellers agreed on an answer other than the one the generator intended |
| `off_slice` | a generated state lacks the feature its slice was aimed at |
| `leak` | a generated state duplicates a gate or validation state |
| `student_wrong` | the trained model answered a validation question wrongly |
| `verifier_reject` | guided labelling: the verifier's answer differed from the labeller's, or it was unsure |
| `verifier_unreliable` | guided labelling: the batch endorsed an answer and its planted opposite, twice |

A line holds `id, kind, round, category, slice, state_hash, source, question, answers
(each labeller's answer and confidence), intended, predicted, state, at`.

Once a cluster (one category and one slice) has five errors it gets a page,
`notes/<kind>/<category>--<slice>.md`:

```markdown
---
kind: useful
category: disagree
slice: ["numbers", true]
count: 7
rounds: 1,2
status: targeted
---

# disagree--numbers-true

the two labellers gave different answers.

## Cases
(three cases from the ledger)

## Cause
(why they fail, written by a model from up to twenty cases and the previous page)

## Next
(one concrete action for the next round)
```

Code keeps the front matter and the cases; a model writes Cause and Next
(`prompts/note.md`). A page's status moves `open` → `targeted` → `resolved`, `falsified`
or `ambiguous` (labellers agree on under half of a slice). `notes/index.md` lists every
page. Before each round the generator reads the whole history: every page whatever its
status, every cluster's size, every round's record, and the slices never to aim at again.

## Commands

```bash
python laya/pool.py                                               # local Langfuse only, free
python laya/label.py useful train --target 3000 --dry             # what the first turn would send
python laya/label.py useful train --target 3000 --labellers opus codex:gpt-6.1-sol --max-turns 400
python laya/guided.py useful pilot --rows train.candidates.jsonl --gold train.jsonl \
    --labeller haiku --verifier codex:gpt-6.1-sol --rounds 5 --per-round 148 --max-usd 10
python -m pytest laya -q -p no:cacheprovider -o addopts=""
```

Labelling, note writing and generation are paid host turns: each run needs approval with
a turn limit, and running it again needs approval again. Training needs the `laya`
package and the base checkpoint, which are installed only with approval.
