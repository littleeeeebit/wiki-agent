# Stage 2 — runner refactoring profile

Use `tool/improvement.py` to choose among candidate refactorings under a frozen
proof of today's behaviour.

## Work

- A characterization step: the work model writes tests for the public paths of
  the step's files. A test is kept only if it passes on the unchanged code;
  the kept set becomes the experiment's frozen `controller_files`, which no
  candidate may edit.
- The evaluator runs the frozen tests and the repository gate, then scores
  with `debt.measure`: a candidate that fails any test is inadmissible; among
  the rest the score is the weighted fall in `lines`, `dup` and `block` over
  the step's files, ratchet violations disqualifying.
- Candidate counts by tier: L0 one, L1 two, L2–L3 three.
- When every candidate fails, one retry receives the failing test output; a
  second failure stops the step with a proposal to split it.

## Contracts

The experiment JSON (`wiki-improvement/1`) is generated, not hand-written:
components are the step's files, `protected` holds the frozen tests,
`limits` come from the mode budget. Unknown usage stops the run, as today.

## Tests

A fake proposer with one behaviour-changing and one behaviour-preserving
patch: the first is rejected by the frozen tests, the second selected; an
edit to a frozen test is refused; the retry happens exactly once.

## Rollback

The profile is additive; existing experiments keep their configuration.

## Implementation notes

- `tool/refactor_profile.py`: `prepare` refuses uncommitted tests or tests
  failing today, then writes `frozen.json`, `tasks.json` and `experiment.json`
  under `raw/refactor/<scope>/<name>/`. `drive` initializes, runs rounds until a
  winner or the second round, then hands off or returns `split` with every
  candidate's reason.
- Debt for scoring is `lines over cap + dup + block over 80`, summed under the
  step's directories, so a split file scores and a mere move does not.
  Reward is the fraction of that debt removed.
- Tasks: `preserve` (frozen tests, then the ratchet; failures go to stderr and
  reach the next proposal), `shrink` (a `scored` offline task, new in
  `improvement_evaluate.py`), held-out `gate` (the adapter gate plus the ratchet).
- The owner chose a native proposer over an API adapter.
  `improvement_host.py --profile refactor` opens an isolated write session in
  the candidate checkout, returns everything it changed as one patch and
  resets the checkout. `soft_caps: ["propose"]` is accepted only with
  `profile: "refactor"`; a crossing lands in `overruns`. The critic accepts
  deterministically.
- `tool/test_refactor_profile.py` drives the real runner with a scripted
  proposer: a behaviour change fails the frozen test, a test edit is refused,
  the deduplication wins in the second round, a no-gain change is rejected and
  the overrun is recorded.
