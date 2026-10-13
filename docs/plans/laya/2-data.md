# Stage 2 — states per kind, splits and the gate test

See the [overview](0-overview.md). After [stage 1](1-serving.md).

Purpose. Build the training, validation and calibration states per kind,
under rules that keep the gate test independent.

## Gate test

Reserved by stage 1 before any measurement, so no checkpoint choice,
augmentation, calibration or threshold fit ever sees it.

- Admitted. Only states production really sent: decision requests the
  local Langfuse traced (`route-question`, `grade-evidence`,
  `judge-claims`; the run logs keep only input digests) and action requests
  kept in `raw/specs`, collected by `laya/pool.py` into
  `laya/data/pool.jsonl`. A state from a synthetic or model-authored set is not admitted,
  wherever its run log lives: `review-routing*.json` and its runs say
  "synthetic, model-authored" with `reviewed_by` null, and `actions.json` is
  a built fixture.
- Unit. One logical state counts once: replays, retries, cache hits and
  trace copies of the same state hash, and near-duplicates (shingle Jaccard
  ≥ 0.8 on the state text), collapse to one test state.
- Labels. No person labels the gate test (owner, 2026-10-13). Where its
  answers come from is open and is decided before stage 6; whatever the
  source, it never sees Jev's or a candidate model's answer, and the record
  names it.
- Size. Two thirds of the admitted units, at most 300 per kind, chosen at
  random; the rest is the development pool (owner, 2026-10-13; reserved by
  `laya/gate.py`, see stage 1). A kind
  with fewer than 100 stays on TypeSafe until enough accumulate. Distinct
  states in the pool on 2026-10-13: `coverage` 187; `useful`, `conflict`
  and `redirect` 185; `route`, `analysis` and `source` 88; `ask` 40,
  `answers` 39, `repair` 31, `relation` and `progress` 24, `action` 15,
  `faithful` 8.
- Diagnostics. Synthetic and model-authored sets, and the development pool,
  are evaluated in stage 6 and reported beside the gate, never as it.

`eval/laya/tests.json` records each test state's hash, source file and
locator, never its text. A training, validation, calibration or
augmentation state matching a test state by hash or near-duplicate, of
the whole state or of a passage view (`laya/gate.py`), is dropped.

## Paid generation

Generating states with a teacher LLM is a paid run. The owner first
approves a small budget for a 50-state pilot of the kind, which measures the
cost per state; then, before the full generation sends anything, a budget
(calls, tokens, seconds, and an upper spend) set from that cost.
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

The passage kinds' generator is `laya/generate.py` (built 2026-10-13):

- A teacher host turn (`prompts/generate.md`) writes eight questions over
  six seeded documents of this repository (`.wiki/corpus.json`), across
  five types: answered, partial, bridging, unanswered and false premise.
  - The turn is journalled `pending` before it is sent.
  - While one is left pending by an interruption, no new turn starts until
    the owner closes it (`--abandon`). So a turn that may have been paid
    for is never sent again unseen.
- Each question runs through `knowledge.prepare` in this repository (hub,
  documents and memory sources), in shadow mode.
  - The stubbed decider answers every question 0.5, so a route still
    retrieves and a source is still searched.
  - The run is cancelled once its first grade-evidence request is
    answered, before code's own repairs. Later rounds' states are not
    generated yet.
  - The translator's one request (`translate._ask`) answers nothing:
    - English passes as production reads it, and a cached translation is
      used.
    - Other text is left unread.
    - A question is not split into parts (`translate.parts`), where
      production may split one.
  - No host fallback runs.
  - Retrieval asks only a search daemon already running. None is
    started and no index is built in the process, either of which may
    download embedding models. The generator refuses to start while the
    daemon does not answer, and while any gate state's text is missing from
    `laya/data/pool.jsonl`.
  - Under all of these, no connection or name lookup leaves this machine
    while a question runs (`loopback_only`). A path the stubs miss (arXiv, a
    URL fetch, the Jev decider) fails there instead of sending.
- A run's whole requests are claimed for its split (`generate.runs.jsonl`)
  before any of its candidates is written. A resumed run claimed by
  another split is left to that split.
- The captured request is checked whole, by whole state and every view. It
  gives no candidate if it is a near-duplicate of a gate state, or of a
  request whose candidates went to another split.
- Each `useful`, `conflict` or `redirect` question then becomes one
  candidate with its passage view, dropped if near a candidate already kept
  in any split.
- A manual run of one question, with no translator request, gave one
  grade-evidence request with 13 passages, so 13 candidates per kind. A
  50-candidate pilot is one teacher turn.

Its tests are in `laya/test_laya.py`.

## Splits

Per kind: 3,000 usable train, 300 validation (checkpoint choice, early
stopping, augmentation), 300 calibration (temperatures and thresholds
only). Usable means labelled under stage 3's rule. Stage 2 generates 20%
more for each split, 4,320 candidates in all (3,600, 360, 360); stage 3
refills from that surplus when states are
dropped, and stops the kind if the surplus runs out (see stage 3). The gate
test is separate, above.

## Format

Read Laya's fine-tuning format from its repository (L2) before writing the
builder; store `laya/data/<kind>/{train,validation,calibration}.jsonl` in
that format plus a provenance field. Candidates go to
`<split>.candidates.jsonl` in `laya/pool.py`'s row shape, with each
generated question's intended answer. A passage kind's candidate is one
question per row, its state the passage view (`laya/kinds.py`, stage 1's
Length), so labelling and training see what serving sends.

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

Data only; delete `laya/data/<kind>`.
