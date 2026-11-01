---
scope: project
severity: preference
triggers: ["(?i)laya.*(retrieval|검색 10종|9종).*(full|본 실행|전체)"]
reads: [laya/generate.py, laya/guided.py, docs/plans/laya/2-data.md, docs/plans/laya/3-labels.md]
slots: []
sources: []
links: [laya, laya-exp-retrieval-pilot, laya-exp-guided-labelling, laya-insight-cold-index]
---

# Retrieval full run — 1,000 train labels for nine kinds, 2026-10-14 to 2026-10-15

Part of [laya](laya.md). It follows [laya-exp-retrieval-pilot](laya-exp-retrieval-pilot.md).
The owner set the rules: at least 1,000 train labels per kind, one kind per
dataset, and a diverse distribution. `repair` was left out until its design is
settled. The cap started at $22 and was raised to $25, $31, then $34.

## Generation

Teacher claude-sonnet-5-5, `laya/generate.py` for all ten kinds.

| Run | Result | Cost $ |
| --- | --- | --- |
| train | 155 turns; nine kinds at 1,150 questions, `repair` 1,042 | 8.27 |
| calibration | nine kinds at 340, `repair` 260 | about 2.6 |
| validation | nine kinds at 340, `repair` 271 | about 1.2 |
| train top-up 1 (`--target 1700 --max-turns 85`) | 85 turns | 3.97 |
| train top-up 2 (`--target 9999 --max-turns 90`), for `coverage` | 90 turns, 708 queries; `coverage` +672 rows (1,027 questions) | 4.72 |

- Validation lost many turns to transient `retrieval_unavailable` from the
  search daemon under CPU load. Every index was healthy when checked; the run
  was resumed until full.
- Top-up 2 cost $0.052 a turn, above the $0.046 of earlier runs, so it went
  $0.5 past its estimate. One turn writes about 8 queries for all ten kinds,
  so a `coverage`-only top-up also grows every other kind's train file
  (`route` +701 questions, left unlabelled). `--kinds` cannot narrow it: the
  run log is pinned to the kind set it started with.
- A top-up script that waited for "no other generator" deadlocked: its own
  command line matched the check.
- Rows appended after labelling started go to a new split file
  (`full2.candidates.jsonl`, `full3...`), and `full1.candidates.jsonl` is a
  frozen copy, so a later append does not change the labelling setup hash.

## Train labels

Labeller haiku, verifier codex:gpt-6.1-sol, 150 states a round, guide trials on
(RRSI). Command: `python laya/guided.py <kind> full1 --rows full1.candidates.jsonl --labeller haiku --verifier codex:gpt-6.1-sol --rounds <n> --per-round 150 --max-usd <cap>`.

| Kind | Labels | True / False | Guide adopted | Cost $ |
| --- | --- | --- | --- | --- |
| redirect | 1,083 | 417 / 666 | round 4 | 0.81 |
| route | 1,099 | 1,005 / 94 | round 4 | 0.51 |
| progress | 1,097 | 146 / 951 | no (5 tried) | 0.58 |
| conflict | 1,050 | 83 / 967 | round 3 | 1.02 |
| ask | 1,040 | 812 / 228 | round 3 | 0.44 |
| useful | 1,024 (905 + 119 full2) | 408 / 616 | no (7 tried) | 1.02 |
| coverage | 1,104 (570 + 196 full2 + 338 full3) | 946 / 158 | round 1 in full1 (39.5% → 55% kept) and full3 (40.9% → 55.7%) | 4.11 |
| analysis | 1,020 | 173 / 847 | round 1 (78.7% → 96% kept) | 0.81 |
| source | 1,102 (768 + 334 full2) | 690 / 412 | round 2 (60% → 78% kept); full2 none | 0.58 |

## Validation and calibration labels

One round of 400 states, no trials (`full1-val`, `full1-cal`). `useful` keeps
its earlier 300 + 300. About $2.3 in all.

| Kind | Validation True / False | Calibration True / False |
| --- | --- | --- |
| analysis | 73 / 181 | 68 / 182 |
| ask | 229 / 58 | 236 / 73 |
| conflict | 9 / 303 | 11 / 308 |
| coverage | 103 / 24 | 97 / 32 |
| progress | 45 / 272 | 44 / 286 |
| redirect | 10 / 285 | 18 / 286 |
| route | 281 / 34 | 285 / 37 |
| source | 137 / 83 | 135 / 70 |
| useful | 141 / 159 | 146 / 154 |

## Insights

- RRSI earns its place at 150-state rounds. A guide was adopted in at least
  six kinds (`redirect`, `route`, `conflict`, `ask`, `coverage`, `analysis`),
  where the 17-state pilot adopted none. `coverage` and `analysis` gained the
  most. `progress` and `useful` tried 5 and 7 lessons and adopted none.
- A guide belongs to one split. `source` full2 started again at guide 0, so
  its unsure share went back to 103 of 300, where full1 had fallen to 47 with
  its adopted guide. A top-up split should start from the earlier split's
  guide.
- An adopted guide is the last one that helps. Every later trial in a kind
  failed (`conflict` rounds 4–6, `coverage` rounds 2–5).
- Haiku refuses some states outright ("can't help with this", AUP `[bio]`).
  Before #148 a refusal was a failed turn retried forever, so `analysis`
  stopped at round 2 and `source` at round 1. Since #148 a refusal is an empty
  reply and the state's questions end `invalid`.
- `conflict` and `redirect` validation holds only 9–18 positives. That is too
  few to measure recall on the rare class; read their validation as a
  false-positive check, or add planted positives to validation.
- `coverage` keeps far fewer labels per row (about 0.6) than the others and
  leans true about 6:1. The verifier rejects most of the labeller's "no".
- Per-kind caps were set for one-question kinds; two-question kinds (`source`,
  `ask`, `coverage`) needed raised caps mid-run.
- `max_usd` stops mid-round, and the turns already paid replay on the next
  run. `coverage` full3 stopped at `limit` inside round 2; finishing that
  round on a rerun cost only $0.05 more.
- Total spend for the full run: $34.55 against a final cap of $35.3.

## Training setup (#149)

- Split files per kind: `train.jsonl` is full1 + full2 + full3; `validation.jsonl`
  and `calibration.jsonl` are full1-val and full1-cal. `useful` keeps its earlier
  300 + 300, and its train is the earlier 500 (now `v1-train.jsonl`) plus the
  1,024 guided labels, 1,524 in all. No state hash is shared across the three
  splits of any kind.
- Token lengths on these splits (state plus head, `typed` tokenizer), p95: 277–370
  for `source`, `progress`, `route`, `ask`, `analysis`; about 683 for `conflict`,
  `redirect`, `useful`; 4,844 for `coverage` (median 3,939, max 5,529, 40% over
  4,096).
- `laya/data/colab.zip` packed at ca800a6, 9.2 MB, nine kinds with `coverage`
  last; the notebook trains e2-ls0.1 and e3-ls0.1 per kind.

Raw files stay local in `laya/data/<kind>/` (`full1*.jsonl`, `full2*.jsonl`,
`*.turns.jsonl`, `*.guide.jsonl`).
