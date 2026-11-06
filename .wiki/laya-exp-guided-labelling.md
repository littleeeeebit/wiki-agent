---
scope: project
severity: preference
triggers: ["(?i)laya.*(guided|verifier|검증자|라벨러|labeller|haiku|pilot|파일럿|라벨 단가)"]
reads: [laya/guided.py, laya/prompts/verify.md, laya/prompts/lesson.md, docs/plans/laya/3-labels.md]
slots: []
sources: []
links: [laya, laya-exp-useful-data, laya-insight-guide-probe]
---

# Guided labelling pilot on `useful` — Haiku labeller, Codex Sol batch verifier, 2026-10-14

Part of [laya](laya.md). The owner asked for cheaper labels and a labeller that
improves with use; design in `docs/plans/laya/3-labels.md` (guided labelling).

## Setup

All 740 `train` candidates of `useful` (the 500 earlier agreed labels plus the
240 the two-frontier rule dropped), five rounds of 148. Labeller `haiku`
(claude-haiku-5-5), one turn a state; verifier `codex:gpt-6.1-sol` high, 20
items a turn plus two flipped mirrors. Probe: the first 60 of `validation`
(earlier agreed labels). Earlier labels were attached with `--gold` for
scoring only. Branch `feat/laya-guided-labeller` at 7b221e0; turns eight
labellers and four verifiers at once (about 100 turns in 50 s).

## Results

| Round | Kept | Labeller unsure | Rejected | Failed | Kept matching earlier label |
| --- | --- | --- | --- | --- | --- |
| 0 | 107 | 20 | 21 | 0 | 0.988 |
| 1 | 82 | 19 | 18 | 29 | 0.984 |
| 2 | 112 | 20 | 16 | 0 | 1.000 |
| 3 | 121 | 14 | 13 | 0 | 1.000 |
| 4 | 114 | 19 | 15 | 0 | 1.000 |

- Kept 536 of 740 (72%), the same yield as the two-frontier rule (72%), but
  drawn from all 740 including the 240 it had dropped.
- Kept labels matched the earlier two-frontier labels on 99.6% of the overlap.
- Spend $0.449 for 1,100 Haiku turns (740 labels and 360 probe turns), about
  $0.0004 a turn; the verifier is a subscription. About $0.0008 per kept
  label with probes, $0.0006 without: roughly 13–18× cheaper than the
  two-frontier rule's $0.0105.
- No verifier batch endorsed a mirror. Seven verifier turns failed with no
  reason (`RuntimeError: None`); one round lost 29 items after three
  attempts each, made again on a rerun.
- No guide was adopted. Haiku alone scored 0.883 on the 60-question probe
  with every proposed guide within noise (gains −0.033 to +0.017 against
  floors 0.048–0.082). See [laya-insight-guide-probe](laya-insight-guide-probe.md).

## Pilot 2 — guides judged on the next round's states (6cd6de6)

Same 740 candidates and seed, turns reused from pilot 1 where the payload
matched. Each candidate guide was tried on the next round's 148 states,
both guides answering, the verifier blind to which wrote an answer.

| Round | Kept | Rejected | Trial: kept rate current → candidate | Gain | Floor | Adopted |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 107 | 21 | — | — | — | — |
| 1 | 105 | 24 | 0.709 → 0.730 | +0.020 | 0.065 | no |
| 2 | 111 | 17 | 0.750 → 0.730 | −0.020 | 0.078 | no |
| 3 | 119 | 15 | 0.804 → 0.743 | −0.061 | 0.063 | no |
| 4 | 114 | 15 | 0.770 → 0.777 | +0.007 | 0.055 | no |

- 556 kept, 98.8–100% matching earlier labels; $0.28 more (total journal
  $0.733).
- No candidate helped beyond noise; one (round 3) nearly hurt. On `useful`
  the rejections look like boundary cases rather than a pattern a rule can
  fix, so the loop rightly keeps the empty guide. A trial of 148 states
  detects only gains of about 6 points or more.

## Raw files (local)

`laya/data/useful/pilot.turns.jsonl`, `pilot.guide.jsonl` (five proposed
guides with their rules), `pilot.jsonl` (536 kept labels); notebook
`laya/notes/useful/ledger.jsonl` (`verifier_reject`, `unsure`).
