# Stage 6 — gates, then shadow and active per kind

See the [overview](0-overview.md). After [stage 5](5-augmentation.md).

Purpose. Switch a kind to Laya only when it is measured no worse than Jev,
and keep the switch reversible per kind.

## Gates, per kind

- Non-inferiority on the frozen test: the lower 95% bound of (Laya accuracy
  − Jev accuracy) is at least −5 percentage points, the margin the owner set
  for reliability PR 9. The owner may set another margin before the run.
- Calibration: ECE on test no worse than Jev's by more than 0.05.
- End to end: the reliability v8 gates (`eval/jev/reliability-v8`) with that
  kind on Laya do not fail where they pass with Jev.
- A kind with fewer than 100 test states is inconclusive and stays on
  TypeSafe.

The Jev side of the comparison is one paid run on the frozen tests; a
repetition needs a second approval.

## Rollout

1. Shadow. Production runs Jev and records Laya's answer beside it, as the
   existing shadow mode does; report agreement weekly per kind.
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
