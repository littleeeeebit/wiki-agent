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
