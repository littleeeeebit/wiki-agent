---
scope: project
severity: preference
triggers: ["(?i)laya.*(label|라벨|생성|generat|비용|cost)"]
reads: [docs/plans/laya/2-data.md, docs/plans/laya/3-labels.md, laya/generate.py, laya/label.py]
slots: []
sources: []
links: [laya, laya-insight-label-turns]
---

# Laya `useful` data — generation and two-labeller agreement, 2026-10-13/14

Part of [laya](laya.md).

## Setup

- States: `laya/generate.py` asks a teacher (Claude Sonnet 5.5, about $0.043
  a turn, 8 queries a turn) for search paths the product runs, and keeps the
  passages as candidate states. A candidate near the gate test or already in
  another split is dropped.
- Labels: `laya/label.py` asks Claude Sonnet 5.5 (about $0.0075 a state) and
  Codex `gpt-6.1-sol` (subscription, no per-call cost) the same `useful`
  question. A label is kept only when both answer the same and neither is
  unsure. No human labels.
- Question: one noul (`useful_p0`) per state.

## Numbers

| Split | Candidates | Judged | Labels | Agreement | Unsure | Disagree | Label cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| train | 740 | 692 | 500 | 72% | 119 | 73 | $5.23 |
| validation | 420 | 393 | 300 | 76% | 60 | 33 | $2.97 |
| calibration | 420 | 394 | 300 | 76% | 62 | 32 | $3.06 |

- Generation: $0.81 over 38 turns. Total $12.07.
- Base rate: validation is 47% true (300), 49% on the first 100.
- Drops are mostly one labeller unsure (two thirds), not a disagreement.
- Cost per kept label: about $0.010 (labelling) plus $0.001 (generation).

## Raw files (local)

`laya/data/useful/<split>.jsonl` (labels), `.candidates.jsonl`,
`.judged.jsonl`, `.ledger.jsonl` (every labeller call with its cost),
`generate.ledger.jsonl`. They stay on the PC; only the Colab zip leaves it.
