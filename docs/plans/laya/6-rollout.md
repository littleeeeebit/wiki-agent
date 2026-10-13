# Stage 6 — gates, then shadow and active per kind

See the [overview](0-overview.md). After [stage 5](5-augmentation.md).

Purpose. Switch a kind to Laya only when it is measured no worse than Jev,
and keep the switch reversible per kind.

## Gates, per kind

All on stage 2's gate test, read here for the first time.

- Scoring. Both deciders answer every gate state with the same state and
  question. An answer is correct when its top option (a Noul's probability
  at least 0.5) matches the person's label. Invalid, unavailable or
  over-long (no answer) counts as wrong for that decider. Policy verdicts
  (yes, no, uncertain) are reported beside it, not gated.
- Non-inferiority. Paired by gate state: the lower bound of a 95% paired
  bootstrap interval (10,000 resamples of states, fixed seed) of (Laya
  accuracy − Jev accuracy) is at least −5 percentage points, the margin the
  owner set for reliability PR 9. The owner may set another margin before
  the run.
- Calibration. ECE with ten equal-width bins on the top option's
  probability, after each decider's own calibration; Laya's no worse than
  Jev's by more than 0.05.
- End to end. The reliability v8 gates (`eval/jev/reliability-v8`) with
  that kind on Laya do not fail where they pass with Jev.
- Size. A kind with fewer than 100 gate states is inconclusive and stays on
  TypeSafe.

Diagnostics reported beside the gate, never as it: synthetic and
model-authored sets, and the validation split.

The Jev side is one paid run of at most 300 states × fourteen kinds, with
the owner's approval of its budget; a repetition needs a second approval.

## Tool

`tool/eval/laya_gate.py <kind>`: reads `eval/laya/tests.json`, runs both
deciders within the approved budget, resumes without paying twice, and
writes `raw/eval/laya/gate-<kind>-<timestamp>.json` with per-state answers,
the paired interval, ECE and the verdict (`pass`, `fail`,
`inconclusive`). `tool/test_laya_gate.py`: scoring of invalid and
over-long answers, the paired bootstrap on a known case, ECE bins, the
100-state rule, budget stop.

## Rollout

1. Shadow. A per-kind provider value `typesafe+laya` keeps TypeSafe's answer
   for the decision and asks Laya beside it without waiting for it; Laya's
   answer goes into the trace entry's `groups` as `shadow` and changes no
   cache key or verdict. Agreement is reported weekly per kind.
2. Active, one kind at a time, starting with `route`, by the provider
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
