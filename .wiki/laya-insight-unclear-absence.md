---
scope: project
severity: landmine
triggers: ["(?i)guided\\.py", "(?i)verify\\.md", "(?i)verdict.*unclear", "(?i)laya.*(skew|치우)"]
reads: [laya/guided.py, laya/prompts/verify.md]
slots: []
sources: []
links: [laya, laya-exp-retrieval-training, laya-exp-guided-labelling]
---

# A "no" that means absence has no evidence to quote; never read the verifier's `unclear` as a rejection

Part of [laya](laya.md).

Rule. Keep a guided label when the labeller and the verifier give the same
answer at confidence 0.7 or more, whatever the verifier's verdict word says.
When a kind collapses to one class, first count the agreed answers the error
notebook dropped, by answer, before generating more data.

Why. `verify.md` tells the verifier to write `unclear` when its evidence is
`none`. A `coverage` "no" (the passages do not state the requirement) has
nothing to quote, so the verifier answered "no" and wrote `unclear`.
`guided.kept` accepted only the verdict `accept`, so 983 coverage questions
both models answered "no" were dropped as `verifier_reject`. 823 of them had
verifier confidence 1.0. Train came out 946 yes / 158 no, validation 103 / 24,
and the model collapsed to all yes
([laya-exp-retrieval-training](laya-exp-retrieval-training.md)). The same
rejections fed the guide lessons as "wrong" examples.

The other kinds were barely touched (conflict 19, source 6, useful 1), because
their "no" usually has something to quote.

Fixed on 2026-10-15 (PR #151). The recorded verdicts were re-read with the fixed
rule at no cost:

| coverage | yes | no |
| --- | --- | --- |
| train before | 946 | 158 |
| train after (with aug1) | 1,133 | 971 |
| validation before | 103 | 24 |
| validation after | 103 | 116 |
| calibration after | 97 | 137 |

The validation split was wrong too: its majority rate of 0.81 was an artefact
of the rule.

How to apply. Count `(labeller answer, verifier answer, verdict)` triples in
`laya/notes/<kind>/ledger.jsonl`; a large "no, no, unclear" cell is this bug.
The recovered rows are `laya/data/<kind>/recovered.jsonl`, with the pre-recovery
splits kept as `v2-<split>.jsonl`. They came from re-reading each ledger
`verifier_reject` with the fixed `kept`, not from resuming the splits.

Do not resume a split to re-judge it under a new keep rule. Review of PR #151
showed that a resume replays the turns but skips states already written, so the
newly kept answers never reach the file, and the changed rejections ask for new,
paid lesson turns. `guided.KEEP_RULE` is now part of a split's setup, so a split
written under another rule refuses a resume. Bump it whenever `kept` changes.
