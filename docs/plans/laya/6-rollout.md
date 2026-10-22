# Stage 6 — gates, then shadow and active per kind

See the [overview](0-overview.md). After [stage 5](5-augmentation.md).

Purpose. Switch a kind to Laya only when it is measured no worse than Jev,
and keep the switch reversible per kind.

## Gates, per kind

All on stage 2's gate test, read here for the first time.

- Scoring. Both deciders answer every gate state with the same state and
  question. An answer is correct when its top option (a Noul's probability
  at least 0.5) matches the gate state's answer (its source is open; stage
  2). Laya is asked alone, with no
  overflow to TypeSafe. Invalid, unavailable or over its limit (no answer)
  counts as wrong for that decider. Policy verdicts (yes, no, uncertain)
  are reported beside it, not gated.
- Non-inferiority. Paired by gate state: the lower bound of a 95% paired
  bootstrap interval (10,000 resamples of states, fixed seed) of (Laya
  accuracy − Jev accuracy) is at least −5 percentage points, the margin the
  owner set for reliability PR 9. The owner may set another margin before
  the run.
- Calibration. ECE with ten equal-width bins on the top option's
  probability, after each decider's own calibration, over the gate states
  both deciders answered (the paired answered subset; a failure is given no
  confidence). Laya's no worse than Jev's by more than 0.05. Each decider's
  answered share is reported beside it. If the paired answered subset is
  under 100 states, calibration is inconclusive and so is the kind.
- End to end. The reliability v8 gates (`eval/jev/reliability-v8`) with
  that kind on Laya do not fail where they pass with Jev.
- Size. A kind with fewer than 100 gate states is inconclusive and stays on
  TypeSafe.

Diagnostics reported beside the gate, never as it: synthetic and
model-authored sets, and the validation split.

The Jev side is the answer production recorded in the local Langfuse for
the same gate state and question, not a paid run (owner, 2026-10-19). A paid
run is needed only for a Jev arm asked differently, such as the way Jev's own
documentation advises (`.wiki/laya-insight-jev-usage.md`), and needs the
owner's approval of its budget.

Labels: guided labelling on the gate states (Haiku labeller, Codex Sol
verifier, agreed labels only), which sees neither decider's answer (owner,
2026-10-19). For `coverage`, 209 of 268 questions over 114 of 129 state hashes
were labelled. One reserved unit is one test state, so the first labelled
hash of each unit is evaluated: 180 questions over 94 units, under the
100-unit rule. Of the 180, 10 are yes, so accuracy is near what always
answering no would get, and the per-class rates are reported beside it.

Beside the verdict, diagnostics: Jev under its fitted production policy
(`eval/jev/policy.json`, uncertain between the thresholds), and both
deciders' accuracy per Jev weak spot from Jev's documentation (numbers,
dates, literal wording, injected instructions, the longest quarter of
states). Laya's data must cover those weak spots (owner, 2026-10-19).

## Results

`coverage`, 2026-10-19: inconclusive, 94 units, under the 100-unit rule
(`raw/eval/laya/gate-coverage-20261019T182337.json`, support at 2,048
tokens at inference, then joint).

- 180 questions. Laya answered 98%, accuracy 0.800 against Jev's 0.961;
  paired difference −0.16 [−0.25, −0.07]. That would fail the −5 point
  margin if the gate were large enough.
- Laya found 1 of 10 covered requirements; ECE 0.53 against Jev's 0.15.

The gate was read four times that day, all with the same checkpoints.
Each read is a diagnostic:

- `…174403`: hashes counted as states (114, 209 questions), support at its
  trained 1,024 tokens. 522 of 1,415 views did not fit, so Laya answered
  53%.
- `…174725`: the same hash count, at 2,048 tokens, a length chosen from
  the gate's view lengths. The tool then said fail: 0.790 against 0.967.
- `…182109` and `…182337`: the same two runs recounted by unit, the
  current rule.

`useful` and `redirect`, 2026-10-22: both fail, each read once. Each was
read on its 104 units reserved 2026-10-13, which nothing had read before.
Labels came from guided labelling on one view per question (1,045 per
kind, $0.33 and $0.30). Laya was asked at 2,048 tokens. Jev's side is
production's recorded answers; stage 11's arm B reworded neither kind.

| Kind | Units, questions | Laya | Jev | Paired difference | ECE Laya / Jev | Yes right, Laya / Jev |
| --- | --- | --- | --- | --- | --- | --- |
| `useful` (run 20261021-075007) | 102, 731 | 0.783 | 0.945 | −0.163 [−0.228, −0.102] | 0.052 / 0.155 | 70 / 169 of 175 |
| `redirect` (run 20261022-014821) | 104, 781 | 0.773 | 0.941 | −0.168 [−0.242, −0.100] | 0.166 / 0.089 | 66 / 87 of 100 |

Records: `raw/eval/laya/gate-useful-20261022T185138.json` and
`gate-redirect-20261022T185258.json`.

Diagnostics, not the gate:
- The gate's views are longer than train's (median 880 tokens against 520).
  Laya is worse on longer views. Below 3,000 characters it is 0.849 and
  0.836 against Jev's 0.938 and 0.944, and even there it says yes to 38 of
  80 `useful` positives, where Jev says yes to 77.
- No threshold recovers the gap. `useful`'s best is 0.791 at 0.4, and
  `redirect`'s is 0.776 at 0.5.
- `redirect` also says yes to 143 of 681 negatives.
- Like coverage (0.800 against 0.961), both kinds sit about 16 points under
  Jev on production states. The same checkpoints scored 0.817 (`useful`)
  and 0.990 (`redirect`) on their generated validation splits. The
  generated states stand in for production poorly.
- A next verdict for either kind needs units these reads have not seen.

Why, and the next round: [stage 5](5-augmentation.md), production-shaped
states. Jev's side here is its answers under the old wording. Since #184
(2026-10-19) production asks stage 11's arm B wording
([Jev stage 11](../jev/11-question-shape.md)), so the next comparison
needs Jev's answers to that wording.

## Tool

`laya/jev_gate.py <kind>`, run with `laya/.venv`. It sits beside the other
Laya code, which its tests stub. It reads the labelled gate rows
(`laya/data/<kind>/gate.jsonl`), asks Laya and reads Jev's recorded answers,
and writes `raw/eval/laya/gate-<kind>-<timestamp>.json`. The file holds
per-question answers, the paired interval, ECE, the diagnostics and the
verdict (`pass`, `fail`, `inconclusive`). `coverage` is Laya's support
checkpoint, then joint over support's part picks where support left a
question undecided. `useful` and `redirect` take one checkpoint
(`--checkpoint`); `--candidates` first writes their gate candidates, one
row per question holding the reserved state with only that question's
passage, as they were trained, and a unit is still read from its first
labelled state. Production's passage views are longer than the generated
ones they trained on (2026-10-22: median 880 tokens against train's 520;
36% of `redirect`'s past its 1,024), so the gate asks them at 2,048 tokens,
set from lengths before any answer was read. Tests in
`laya/test_jev_gate.py`: no answer counts as wrong, the paired bootstrap on
known cases, ECE bins, both 100-state rules, the policy reading, the
weak-spot tags and the passage kinds' views.

## Rollout

0. Provider. Stage 1's contract (the provider map, kinds at the transport,
   decider identity, policy per kind, overflow, the server's lifecycle and
   `tool/test_decision_provider.py`) is built here, before shadow, once a
   stage 4 checkpoint passes its gate. Nothing could use it earlier
   (owner, 2026-10-13).
1. Shadow. A per-kind provider value `typesafe+laya` keeps TypeSafe's answer
   for the decision and asks Laya beside it without waiting for it; Laya's
   answer goes into the trace entry's `groups` as `shadow` and changes no
   cache key or verdict. Agreement is reported weekly per kind.
2. Active, one kind at a time, starting with `useful`, by the provider
   setting.
3. When every kind is active and a week of traces shows no gate regression,
   TypeSafe becomes an optional fallback and `TYPESAFE_API_KEY` is no longer
   required.

## Acceptance

Per kind: gate results, shadow agreement, the date it went active. The
overview's step table records what is active.

## Rollback

Per kind, set the provider back to `typesafe`. Recorded decisions replay
with the model they were made by.
