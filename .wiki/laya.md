---
scope: project
severity: preference
triggers: ["(?i)\\blaya\\b", "라야"]
reads: [docs/plans/laya/0-overview.md, docs/research/laya-replacement.md]
slots: []
sources: []
links: [laya-exp-useful-data, laya-exp-useful-training, laya-insight-select-by-log-loss, laya-insight-temperature-ceiling, laya-insight-validation-size, laya-insight-label-turns, laya-insight-seed-variance, laya-exp-guided-labelling, laya-insight-guide-probe, laya-insight-cold-index, laya-exp-retrieval-pilot, laya-exp-retrieval-full, laya-exp-retrieval-training, laya-insight-unclear-absence, laya-insight-jev-usage, laya-exp-coverage-shape-pilot]
---

# Laya — what the experiments found, and where each record lives

The hub of the Laya knowledge graph. Laya replaces Jev's typed decisions with
fourteen per-kind fine-tunes of ModernBERT-large (`laya` 0.4.6), trained on
labels where two LLMs agree and no human labels. The plan is
`docs/plans/laya/`; this graph holds what was measured and what it taught.
Every experiment and insight page links back here.

## Where things stand (2026-10-20)

- Nine retrieval kinds trained; seven learned
  ([laya-exp-retrieval-training](laya-exp-retrieval-training.md)).
- `coverage` (support, then joint) on its gate, 2026-10-19: 0.800 against
  Jev's 0.961 by unit, inconclusive (94 units, under 100); the gate was read
  four times that day, so the next verdict needs new reserved units. Its
  generated train and validation states lack production's shape as a
  hypothesis for the gap (conversation context, omitted context, the model
  split, long queries, later rounds' passages; yes rate 0.53 against the
  gate's 0.06). The development pool outside every reserved unit is only 20
  states, 14 units.
  Next: production-shaped states and a production validation split, plan in
  `docs/plans/laya/5-augmentation.md`
  ([laya-insight-jev-usage](laya-insight-jev-usage.md)).
- Jev now asks arm B's wording (Jev stage 11, #184): the next gate needs
  Jev's answers to it.
- The production-shaped batch `shape3` ($15.06) retrained support (2,048
  tokens) and joint; neither gained above the noise floor on the
  generated validation, so the incumbents (support 211003, joint 060734)
  stay until the fresh gate can read them side by side
  ([laya-exp-coverage-shape-pilot](laya-exp-coverage-shape-pilot.md)).
- Jev's arm B wording (quote the requirement) tried on Laya (#197):
  support +0.012 (floor 0.014), coverage through joint -0.055 (floor
  0.049). Reverted for both (#201, owner 2026-10-21).
- Stage 6 count, 2026-10-22 (local Langfuse, in memory, nothing written):
  55 production states since the 10-13 pool, 36 units in all; coverage 28
  states, 17 units, 78 questions; useful 27 states, 17 units, 239
  questions (about 9 per state, coverage about 3); so the fresh coverage
  gate is far from 100 units (about 4 units a day, all kinds, on the days
  the app ran). `useful` and `redirect` each
  still have 104 reserved 10-13 gate units (127 hashes, 1,057 questions)
  that nothing has read; Jev stage 11's adopted arm B did not reword
  either, so production's recorded Jev answers there are the current
  wording. Their gate needs view candidates, gate labels and
  `jev_gate.py` beyond coverage. Checkpoints on Drive:
  `useful-train-e3-ls0.1-20261021-075007-lfyaedty.zip`,
  `redirect-train-e2-ls0.1-20261022-014821-x_v7dehb.zip`.
  Gate tool for the passage kinds merged (#214): one view row per question,
  asked at 2,048 tokens, because gate views run longer than train's (median
  880 against 520 tokens; 36% of redirect's past its 1,024). Gate labels
  (owner-approved, cap $1.5, Haiku with the Sol verifier, one round):
  useful 771 of 1,045 kept, 121 states, 102 units, 184 yes, $0.33;
  redirect 828 kept, 125 states, 104 units, 103 yes, $0.30.
- Gates read once, 2026-10-22: both fail. useful 0.783 against Jev's 0.945,
  -0.163 [-0.228, -0.102], yes right 70/175 against 169/175, ECE 0.052
  against 0.155. redirect 0.773 against 0.941, -0.168 [-0.242, -0.100],
  yes right 66/100 against 87/100, 141 of 681 negatives said yes and 2
  unanswered, ECE 0.166
  against 0.089 (`raw/eval/laya/gate-useful-20261022T185138.json`,
  `gate-redirect-20261022T185258.json`). Insight: all three gated kinds sit
  about 16 points under Jev on production states, while generated
  validation was 0.817 and 0.990. Short views (under 3,000 characters)
  still miss half of useful's positives, and no threshold recovers the gap
  (best 0.791 and 0.776), so length is not the main cause; the generated
  states stand in for production poorly. Any next verdict needs unseen
  units.
- Owner, 2026-10-22: Laya's paid work stops, Jev stays in production.
  Approved next step (`docs/plans/laya/0-overview.md`, "Reconsideration"):
  wait for a fresh coverage gate of 100 units (17 now), read the shape3
  checkpoints on it, then choose training on production states or ending
  Laya. No more augmentation from generated states alone. That gate's
  labels are pre-approved (owner, 2026-10-22): Haiku with the Sol verifier,
  cap $0.5.

## Where things stood (2026-10-14)

- Kind: `useful` first, end to end, before the other thirteen.
- Data: train 500, validation 300, calibration 300 labels, $12.07 spent.
  [laya-exp-useful-data](laya-exp-useful-data.md)
- Training: on Colab's L4 from `laya/notebooks/train.ipynb`; the PC packs
  the data (`python laya/train.py pack useful`) and reads the result zip.
  Zero-shot is at chance (0.49–0.55); a fine-tune reaches 0.74–0.78.
  [laya-exp-useful-training](laya-exp-useful-training.md)
- Chosen: 3 epochs with label smoothing 0.1 from `base` (run 5: accuracy
  0.78, log loss 0.471, temperature 2.00; 0.90 accurate on the 137 of 300
  at confidence 0.8 or above). Its lead over 2 epochs is near the seed
  spread, so it is provisional.
- Open: the threshold policy on the calibration split (plan stage 4).

## Owner's direction (2026-10-14, after run 5)

Tuning `useful` one setting at a time was too slow for fourteen kinds. The
owner set the data rules for every kind:

- At least 1,000 train labels per kind.
- Each kind's dataset holds that kind only; kinds are never mixed.
- Within a kind the distribution is diverse: sources from the other
  repositories on this PC (hub wiki, other projects), not only this
  repository's 90 wiki pages, and varied query types. Run 5's `useful`
  train set came from just 58 queries (up to 13 passages each), which may
  explain its seed spread ([laya-insight-seed-variance](laya-insight-seed-variance.md)).
- Labels must be cheaper: a small labeller (Haiku) whose guide a stronger
  batch verifier (Codex Sol) improves round by round, after RRSI, instead
  of two frontier labellers per state. Pilot approved up to $10.
  `laya/guided.py`, plan `docs/plans/laya/3-labels.md`. Pilot: 99.6%
  match with earlier labels at about $0.0008 a kept label, but no guide
  adopted in either pilot (probe, then next-round trial) ([laya-exp-guided-labelling](laya-exp-guided-labelling.md), [laya-insight-guide-probe](laya-insight-guide-probe.md)).

## Experiments

| Page | What |
| --- | --- |
| [laya-exp-useful-data](laya-exp-useful-data.md) | Generating and labelling `useful` states: counts, agreement, cost |
| [laya-exp-useful-training](laya-exp-useful-training.md) | Fine-tune runs 1–5: zero-shot, epochs, calibration, seed/data/smoothing/lr sweep |
| [laya-exp-guided-labelling](laya-exp-guided-labelling.md) | Guided labelling pilot: Haiku labeller, Sol batch verifier, cost and match |
| [laya-exp-retrieval-pilot](laya-exp-retrieval-pilot.md) | Diverse 50-question pilot over ten retrieval kinds: yield, keep rate, class balance |
| [laya-exp-retrieval-full](laya-exp-retrieval-full.md) | Full run: 1,000 train labels for nine retrieval kinds, val/cal, cost, refusals |
| [laya-exp-retrieval-training](laya-exp-retrieval-training.md) | Nine kinds trained on Colab: seven learned, `conflict` and `coverage` collapsed to the majority |
| [laya-exp-coverage-shape-pilot](laya-exp-coverage-shape-pilot.md) | Production-shaped generator pilot (`shape1`): cost per question, shape against the development pool, labels |

## Insights

| Page | In one line |
| --- | --- |
| [laya-insight-select-by-log-loss](laya-insight-select-by-log-loss.md) | Choose a checkpoint by calibrated log loss, not accuracy |
| [laya-insight-temperature-ceiling](laya-insight-temperature-ceiling.md) | A temperature at Laya's 5.0 ceiling means the checkpoint overfit |
| [laya-insight-validation-size](laya-insight-validation-size.md) | 100 validation questions cannot rank epoch counts |
| [laya-insight-label-turns](laya-insight-label-turns.md) | `label.py --max-turns` counts labeller calls, not states |
| [laya-insight-seed-variance](laya-insight-seed-variance.md) | One run per setting cannot rank settings; the seed moves it as much |
| [laya-insight-guide-probe](laya-insight-guide-probe.md) | A small probe of easy agreed labels cannot show a labeller guide helping |
| [laya-insight-cold-index](laya-insight-cold-index.md) | A repository the search daemon has not indexed makes every capture fail; warm it first |
| [laya-insight-unclear-absence](laya-insight-unclear-absence.md) | A "no" that means absence has no evidence to quote; the verifier's `unclear` is not a rejection |
| [laya-insight-jev-usage](laya-insight-jev-usage.md) | Our Jev questions sit on Jev's documented weak spots; compare against Jev used as its docs advise |

## Adding to this graph

One page per experiment (`laya-exp-<kind>-<topic>`) with the setup, the
numbers and where its raw files are; one page per insight
(`laya-insight-<topic>`) with the claim, the evidence and how to apply it.
Each new page links to this hub, and this hub's tables and `links:` list it.
Raw run files (`laya/data/`, result zips) stay local; pages carry the numbers.
