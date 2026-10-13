# Stage 2 — states per kind, splits and the gate test

See the [overview](0-overview.md). After [stage 1](1-serving.md).

Purpose. Build the training, validation and calibration states per kind,
under rules that keep the gate test independent.

## Gate test

Reserved by stage 1 before any measurement, so no checkpoint choice,
augmentation, calibration or threshold fit ever sees it.

- Admitted. Only states production really sent: decision requests in
  `raw/eval/jev` runs of the real app or CLI, `raw/actions` rows, and replay
  tapes. A state from a synthetic or model-authored set is not admitted,
  wherever its run log lives: `review-routing*.json` and its runs say
  "synthetic, model-authored" with `reviewed_by` null, and `actions.json` is
  a built fixture.
- Unit. One logical state counts once: replays, retries, cache hits and
  trace copies of the same state hash, and near-duplicates (shingle Jaccard
  ≥ 0.8 on the state text), collapse to one test state.
- Labels. Each test state is labelled by a person, who sees the state and
  the question, not Jev's or any model's answer. The label records who and
  when. An existing label is reused only if a person reviewed it and the
  record says so.
- Size. Up to 300 per kind, chosen at random from what is admitted. A kind
  with fewer than 100 is reported in stage 6 as inconclusive and stays on
  TypeSafe.
- Diagnostics. Synthetic and model-authored sets, and the development pool,
  are evaluated in stage 6 and reported beside the gate, never as it.

`eval/laya/tests.json` records each test state's hash, source file and
locator, never its text. A training, validation, calibration or
augmentation state matching a test state by hash or near-duplicate is
dropped.

## Paid generation

Generating states with a teacher LLM is a paid run. Before stage 2 sends
anything, the owner approves a budget (calls, tokens, seconds, and an upper
spend) set from a 50-state pilot of the kind that measures the cost per state.
The generator records each call's usage, resumes without paying twice, and
stops at the budget. A local teacher model, if the owner prefers one, needs
no spend approval but is recorded the same way.

## Training states

Real traffic is a few hundred requests in all, so training states are
mostly generated, from the same builders production uses:

- Retrieval kinds (`route`, `source`, `useful`, `conflict`, `redirect`,
  `coverage`, `ask`, `analysis`, `progress`, `repair`): questions written by
  a teacher LLM over the hub and repository corpus, run through
  `knowledge.prepare` up to the decision point with the decider stubbed, so
  each state is what that point would really send.
- Claim kinds (`relation`, `answers`, `faithful`): claims and passages from
  the same runs, with perturbed claims (negation, number, scope) for the
  negative side.
- `action`: the stage 8 points (`work.start`, `specs.check`, `loop.fix`,
  `specs.answered`) over synthetic specs and review results.

Each state records its generator, prompt version, source pages and seed.
The class balance per kind is stated; a kind whose natural rate is skewed
keeps that rate in test and is balanced only in training.

## Splits

Per kind: 3,000 usable train, 300 validation (checkpoint choice, early
stopping, augmentation), 300 calibration (temperatures and thresholds
only). Usable means labelled under stage 3's rule. Stage 2 generates 20%
more for each split; stage 3 refills from that surplus when states are
dropped, and stops the kind if the surplus runs out (see stage 3). The gate
test is separate, above.

## Format

Read Laya's fine-tuning format from its repository (L2) before writing the
builder; store `eval/laya/<kind>/{train,validation,calibration}.jsonl` in
that format plus a provenance field.

## Acceptance

- The gate test is reserved and recorded before stage 1's measurements.
- Fourteen kinds × the three splits exist with the 20% surplus,
  deduplicated against the gate test.
- Every state passes the kind's existing request validation
  (`decision.request`), so no state is one production could not send.
- Generation spend is within the approved budget.

## Tests

`tool/test_laya_data.py`: synthetic and model-authored sources refused for
the gate test; replay, retry and trace copies collapsing to one test state;
dedupe of every split against the gate test; provenance present; split
sizes with surplus; request validation per kind; generation stopping at its
budget and resuming without paying twice.

## Rollback

Data only; delete `eval/laya/<kind>`.
