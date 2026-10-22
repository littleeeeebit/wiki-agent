# Laya — replace Jev with fourteen fine-tuned decision models

Purpose. Replace the hosted Jev model (`jev-1.13.0`) with Laya models trained
on this repository's own decisions: one model per decision kind, chosen by
code. Every current guarantee stays: code owns permissions, budgets and
execution; uncertain answers take the existing safe path; modes stay off,
shadow and active. Research, sources and measurements are on
[the research page](../../research/laya-replacement.md).

## Decisions

| Decision | Choice | By |
| --- | --- | --- |
| Model | [Laya](https://huggingface.co/convaiinnovations/laya), Apache-2.0, fine-tuned locally | Owner, 2026-10-13 |
| Split | Fourteen models, one per decision kind; code routes by the kind each question already carries | Owner, 2026-10-13 |
| Training data | 3,000 states per kind to start; then augmentation of the slices validation gets most wrong | Owner, 2026-10-13 |
| Labels | Two LLM labellers independently; agreed labels train; a disagreement is dropped, recorded in the error notebook and refilled from the surplus; no person labels | Owner, 2026-10-13 |
| Gate test | States production really sent, reserved before any measurement; synthetic sets are diagnostics only. Not labelled by a person; where its answers come from is still open | Owner, 2026-10-13 |
| Gate split | Two thirds of the real states (near-duplicates as one unit) go to the gate test at random, one third is the development pool, so stage 1 can measure on real states | Owner, 2026-10-13 |
| Pilot kind | `useful` (1,521 production questions, 185 states); a kind with under 100 production states stays on TypeSafe until enough accumulate | Owner, 2026-10-13 |
| Passage view | `useful`, `conflict` and `redirect` ask about one passage against the query, so their model sees the state with only that passage: a median 900 tokens, max 2,250, instead of 4,300 and past ModernBERT's 8,192 at p95. `coverage` reads the passages together and stays whole | Owner, 2026-10-13, from stage 1's length measurement |
| Improvement | After RRSI: a round is adopted only above the noise floor, falsified slices are never retried, adopted rounds are pruned; every dropped or wrong question goes to a local error notebook | Owner, 2026-10-13 |
| Code | Data, labels, training, the loop and the notebook in the top-level `laya/` folder; serving stays in `tool/decision` | Owner, 2026-10-13 |
| Runtime | Local server on the RTX 3080 (10 GB); training on Colab's L4 (owner, 2026-10-14); TypeSafe stays as a per-kind fallback until rollout | This plan |
| Language | English states only, as Jev today; normalization is unchanged | Existing rule |

The fourteen kinds (`tool/decision/policy.py`):

| Form | Kinds |
| --- | --- |
| Noul (yes/no) | `route`, `source`, `useful`, `conflict`, `redirect`, `coverage`, `ask`, `analysis`, `progress` |
| Choice | `repair`, `relation`, `answers`, `faithful`, `action` |

## Architecture

```mermaid
flowchart LR
    C[Decision request, kinds named] --> P{Provider per kind}
    P -->|laya| S[Local Laya server]
    P -->|typesafe| T[api.typesafe.ai]
    S --> M[(Fourteen checkpoints, LRU on GPU)]
    S --> R[Answers + usage, same shape]
    T --> R
    R --> D[decision.checked, policy, safe paths]
```

A request that asks several kinds at once (a judge round asks useful,
conflict, redirect, coverage and repair) is grouped by provider: TypeSafe
questions stay one call, Laya questions go to each kind's model, and the
answers are joined in question order. The kinds reach the transport, the
request's `model` becomes the identity of the deciders it asked, the policy
is composed per kind, and one trace entry keeps every group's model and
usage. [Stage 1](1-serving.md) defines this contract and what happens to a
state over a kind's length limit (the whole request on TypeSafe with its
existing policy, else `unavailable`).

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | [Serving](1-serving.md) | Local server, per-kind provider, context and zero-shot baseline | Done — gate test reserved, measurements recorded (#138); the provider contract moved to stage 6, step 0 (owner) |
| 2 | [Data](2-data.md) | States per kind, splits, the gate test | In progress — gate test reserved (`laya/gate.py`); nine retrieval kinds generated over the corpora on this machine, at least 1,000 train labels each (2026-10-15), with `support` and `joint` derived for coverage; generated states lack production's shape (stage 5); claim kinds and `action` not started |
| 3 | [Labels](3-labels.md) | Two-labeller agreement, drops refilled and noted, pilot then full run | In progress — guided labelling (`laya/guided.py`: Haiku labeller, Codex Sol verifier, RRSI guides) labelled the nine retrieval kinds, `support` and `joint`; spend per run in the `.wiki/laya-exp-*` pages; claim kinds and `action` remain |
| 4 | [Training](4-training.md) | Fourteen fine-tunes, calibration, fitted policy | In progress — nine retrieval kinds trained on Colab (`.wiki/laya-exp-retrieval-training.md`): seven learned at 0.83–0.99 on validation; coverage is answered by `support` then `joint`; policies fitted for the seven (`eval/laya/policy/`, run 20261021-045345, `useful` refitted on rebuilt validation and calibration in run 20261021-075007, `redirect` retrained on augmented train in run 20261022-014821; `ask` and `progress` have no cost table, so no rule) |
| 5 | [Augmentation](5-augmentation.md) | Error-slice loop per kind, after RRSI | In progress — targeted rounds by hand for `conflict`, `coverage` and `support`; coverage's production-shaped generator built, its pilot approved, the batch not (2026-10-20) |
| 6 | [Rollout](6-rollout.md) | Non-inferiority gates, shadow then active per kind | In progress — gate tool built (#181); coverage gate 2026-10-19: inconclusive (94 units, under 100), 0.800 against Jev's 0.961; `useful` and `redirect` gates 2026-10-22 (#214): fail, 0.783 and 0.773 against Jev's 0.945 and 0.941; provider and shadow not started. Paused (owner, 2026-10-22): paid work stops, Jev stays, the plan is reconsidered |

Order: 1 → 2 → 3 → 4 → 5 → 6. Stage 1 first reserves the gate test, then
runs no paid call and decides whether the rest is worth paying for. Its
provider contract is built at stage 6, once a checkpoint can use it. Every
paid step needs the owner's approval with a stated budget before it sends
anything: teacher generation (stage 2), labelling (stage 3), augmentation
(stage 5) and the Jev comparison (stage 6); a repetition needs a second
approval. `useful` goes through stages 2–6 first; the other kinds start
after its stage 6 result, each once it has at least 100 production states.

## Relation to open work

- Reliability PR 9 (#135) is paused at review round 5. Its findings are in
  the set's substring label checker, which calls no model, so this plan does
  not resolve them. A trained `faithful` model could later replace that
  checker; that is PR 9's decision, not this plan's.
- Reliability PR 10 and the review-routing stages keep using whichever
  provider each kind is set to.

## Budget

Per kind; fourteen kinds multiply it. Token figures assume about 2k input
tokens per call and are replaced by each pilot's measurement.

| Item | Per kind | Gate |
| --- | --- | --- |
| Teacher generation | 4,320 candidates: 3,600 usable targets (3,000 + 300 + 300) plus a 20% surplus per split (600 + 60 + 60) | Stage 2 pilot of 50, then owner approval |
| Labelling | Up to 4,320 × 2 = 8,640 labels, about 17.3M input tokens (about 242M for fourteen); labelling stops when the targets are met, so surplus is paid only for refills | Stage 3 pilot of 200, then owner approval of a total limit |
| Error notebook | One host turn per page that reached five errors and grew | Within the round's approved limit |
| Augmentation | Up to 3 rounds × 300 states, generated and labelled twice | One approved total limit before round 1 |
| Training | About 1–3 GPU-hours locally, per run and per augmentation round | Local, no spend |
| Jev comparison | Up to 300 gate states | Owner approval; a repetition needs a second |

## Sources

See [the research page](../../research/laya-replacement.md), L1–L9.
