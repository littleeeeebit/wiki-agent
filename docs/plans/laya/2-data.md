# Stage 2 — states per kind, splits and frozen tests

See the [overview](0-overview.md). After [stage 1](1-serving.md).

Purpose. Build 3,000 unlabelled training states per kind, and freeze test
sets that training never sees.

## Frozen tests first

Before any training state exists, freeze per kind a test set from real
logged states (`raw/eval/jev`, `raw/actions`, replay tapes) and existing
labelled sets (`eval/jev/*.json`). Record their state hashes in
`eval/laya/tests.json`. A training or augmentation state whose hash, or
near-duplicate text (shingle Jaccard ≥ 0.8), matches a test state is
dropped. Existing labels are kept; unlabelled real states are labelled by a
person in stage 3. Where a kind has fewer than 100 test states, the plan
says so for that kind and its gate in stage 6 is reported as inconclusive
rather than passed.

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

Per kind: 3,000 train, 300 validation (drives augmentation and early
stopping), 300 calibration (temperatures and policy only). Test is frozen
separately, above.

## Format

Read Laya's fine-tuning format from its repository (L2) before writing the
builder; store `eval/laya/<kind>/{train,validation,calibration}.jsonl` in
that format plus a provenance field.

## Acceptance

- Fourteen kinds × the three splits exist, deduplicated against test.
- Every state passes the kind's existing request validation
  (`decision.request`), so no state is one production could not send.

## Tests

`tool/test_laya_data.py`: dedupe against test, provenance present, split
sizes, request validation per kind.

## Rollback

Data only; delete `eval/laya/<kind>`.
