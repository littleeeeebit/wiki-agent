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
the whole state or of a passage or listed view (`laya/gate.py`), is dropped.

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

The passage kinds' generator is `laya/generate.py` (built 2026-10-13; its
sources, query types and kinds are widened by the next section, and the
safeguards below are unchanged):

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
  before any of its candidates is written. A run interrupted after its
  claim finishes from the claimed requests, never from a new capture that
  may differ. A resumed run claimed by another split is left to that split.
  A finished run with no claim (written before claims existed) makes the
  generator refuse to start, since its split's ownership cannot be told.
- The daemon is asked again before each teacher turn. A question whose own
  retrieval did not finish (`prepare` ends neither `ready` nor `partial`,
  as when unavailable or out of budget, before any grade-evidence request) is left unrun with its reason, and the run stops
  (`search_unavailable`) before paying for another turn. An empty retrieval
  is recorded as a run with no candidates. A question that fails on its own,
  the same on every rerun (one the stubbed translator leaves unread ends
  `normalization_failed`), is recorded as its run's error and skipped. The
  teacher prompt asks for English in every character.
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

## Diverse data for the ten retrieval kinds (owner, 2026-10-14)

Run 5 (`.wiki/laya-exp-useful-training.md`) showed `useful`'s 500 train
labels came from 58 queries, up to 13 passages each, all over this
repository's 90 wiki pages, and seeds alone moved accuracy as much as any
setting. The owner set three rules for every kind: at least 1,000 train
labels; each kind's dataset holds that kind only; within a kind the
distribution is diverse, from the other repositories on this PC and from
varied query types. The teacher stays Sonnet 5.5; this stage covers the ten
retrieval kinds at once.

What one run gives. A query's production run makes two requests the
generator now keeps: the route request (`retrieve`, `analysis`, `progress`,
`source_<s>` for each available source, and `ask_<r>` for each query part
when the query has more than one) and the first grade-evidence request
(`useful`, `conflict` and `redirect` for each passage, `coverage_<r>` for
each requirement once a passage is complete, and `repair` when a next
retrieval step exists). Each kind's candidates are cut from these, one
question per row, as passage kinds are today.

Sources. The wiki corpora of the repositories on this PC: wiki-agent (90
documents), ai-coding-wiki (274), ai-companion-voice-agent (140),
project-codeit-mid (40), ai-generation (39), ai-coding-agent-wiki-public
(22), rfg_rag (7), ai-coding-agent-wiki (6). A teacher turn picks one
repository, weighted by the square root of its document count so the
largest does not dominate, and six of its documents; its queries run
through `knowledge.prepare` in that repository, so the hub and memory stay
those production reads, and the search daemon builds that repository's
index with the embedder it already holds. A repository is a unit of
diversity, so splits are drawn by query, never by passage: every
candidate of one query goes to the same split.

Query types. Two axes per teacher turn, each assigned to the turn so a
turn cannot drift to one type: the content types already used (answered,
partial, bridging, unanswered, false premise) and an intent axis the route
kinds need — a repository lookup, an analysis (a comparison, judgment or
plan), a progress question, a general question that needs no repository
evidence, a compound query of two or three questions (which `requirements`
splits, so it gives `ask` questions), and a query carrying pasted material
(a log, notice or message) beside the real request. Forms vary as today.
After the pilot a seventh intent, `chat`, was added: a greeting, thanks or a
request to reword text the message gives. It is the only intent whose route
answer is no; the pilot's 43 kept `route` labels were all yes.

Per-query cap. From one query at most two candidates per kind (a seeded
draw), and one for `retrieve`, `analysis`, `progress` and `repair`, so a
kind's 1,000 train labels come from at least 500 queries.

Volume and budget. A kind needs 1,600 labels (1,000 train, 300 validation,
300 calibration); at the guided labeller's 72% keep rate, about 2,200
candidates. The one-per-query kinds set the count: about 2,200 queries,
about 275 teacher turns of eight, about $12 at $0.043 a turn. Labels cost
about $0.0008 kept, about $13 for ten kinds. A 50-query pilot measures
each kind's yield per query and the class balance before the full run;
both need the owner's approval with a budget.

Class balance. Kinds whose natural rate is skewed (`redirect` positives
are rare in a wiki) keep that rate in validation and calibration; the
plan's balancing for training applies, and a kind short of positives after
the full run is reported with its counts rather than padded.

In the 50-query pilot `conflict` kept 2 yes of 46, and `redirect` 3 of 39.
At that rate 1,000 labels would hold about 40 and 75 positives. The owner
chose (2026-10-14) to raise them in `train` only, before the full run:

- A train turn draws false premise for 4 of every 12 slots instead of 2 of
  10. The ledger records the turn as `lean`, and its queries are never run
  for another split.
- A `conflict` draw takes one passage whose heading starts with the title of
  a page the teacher says the question is about. That is where a false
  premise is contradicted.
- Each teacher turn also writes four instructions addressed to an assistant
  (`plants`). In 30% of `redirect` candidates, one is put between two lines
  of the passage, and its provenance records `planted`. The planted view is
  checked against the gate test again before it is written.
- Labels still come from the labeller and the verifier; a planted passage is
  not assumed to be a yes.
- Validation and calibration keep the natural draw and get no plants.

`repair` kept only 15 of 50. Haiku answered 30 of them below the 0.7
agreement bar, mostly at 0.6, and the verifier rejected only one. Its wording
is a Jev prompt (`knowledge.PROMPTS`), whose digest the fitted policy is bound
to, so it is left as is. The next pilot tries a stronger labeller for this
kind.

Built (2026-10-14). `laya/generate.py <split> --target N --teacher sonnet
--max-turns T` writes all ten kinds at once; `--kinds` narrows them.

- Repositories are the folders beside this one with a `.wiki/corpus.json`
  and their own `.git` folder, so a linked worktree is not counted twice.
- Each turn's eight slots are drawn from the five types and seven intents,
  each list doubled and shuffled. A train turn adds two more false premises
  before the shuffle. The teacher answers by slot index, and each query keeps the
  slot it was asked for, not a label the teacher gives. It also keeps the
  titles of the pages the teacher says it is about (`about`).
- The teacher turns and query runs of all kinds share one ledger and one
  run log under `laya/data/retrieval/`. The candidates go to
  `laya/data/<kind>/<split>.candidates.jsonl`.
- A run claims the whole route and grade-evidence requests for its split
  before writing, so every candidate of one query is in one split.
- The run stops when every kind has `--target` candidates, or at the turn
  limit.
- A query whose retrieval finished with nothing to judge still gives its
  route candidates. A query whose search failed after the route is left
  unrun, as before.
- Before a repository's first query in a run, the daemon is asked to
  finish that repository's index, waiting up to 30 minutes. A search
  answers from an unfinished index too, so the daemon's `/search` reply now
  says whether the asker's index is complete (`search.indexed`). If the
  index has not finished, the run stops (`search_unavailable`) and no query
  is recorded as empty. On 2026-10-14 a capture in this repository failed
  until its index was rebuilt.
  - The reply changes the search package's version. The running daemon is
    replaced at the next ordinary use, and the repositories' indexes are
    then warmed again before a run.
- A kind's questions that share a state (a route's sources, a request's
  coverage) are one candidate row with each of those questions. Counts and
  `--target` are in questions.
- A corpus entry whose path resolves outside its repository is never read.
- The run log keeps the `--kinds` set it was started with, and a run with
  another set is refused. A finished query is finished only for the kinds
  it wrote.
- The near-duplicate check leaves out a route state's table of source
  descriptions (`gate.FIXED`). Left in, two different short questions
  scored 0.87. The gate was reserved before this change and is not
  reserved again.

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
