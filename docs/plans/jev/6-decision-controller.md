# Stage 6 — typed Jev decisions and the retrieval state machine

See the [overall design](0-overview.md). Prerequisite:
[stage 5](5-retrieval.md); the transport foundation comes from stage 1.

Purpose. Make Jev's judgments control bounded transitions with validated inputs,
explicit uncertainty, and measurable policies. A list of probabilities attached
to a prompt does not complete this stage.

## Public decision contract

`DecisionRequest` contains schema_version, request_id, decision_kind, state_en,
questions, allowed_candidate_ids, model, prompt_version, policy_version,
normalization_version, deadline, and remaining_budget.

`DecisionResult` contains request_id, status, answers, selected_candidate_ids,
reason_code, model, policy_version, usage, elapsed_ms, and trace_id.
Status is one of decided, uncertain, unavailable, invalid, cancelled, or exhausted.
Request IDs, candidate identity, and schema version must match before use.

Validate every externally returned type. Noul must be a finite number in [0,1],
excluding booleans. Choice must name an offered candidate and include a valid
probability map and confidence. Score must match the supplied ordered criteria.
Reject missing answers, unknown IDs, malformed distributions, and silent type
coercion. Model failure cannot be converted into a negative semantic answer.

## Which judgment uses which primitive

| Decision | Primitive | What code does with the result |
| --- | --- | --- |
| Need evidence beyond supplied state | Noul | Skip only eligible direct-response tasks; uncertain means retrieve |
| Which enabled source can help | Independent Nouls | Multi-source selection, with conservative coverage on uncertainty |
| Which allowed next operation to prefer | Choice with defer option | Select a candidate after confidence and margin policy checks |
| Is a passage useful, including a bridge fact | Noul | Retain uncertain/partial evidence; rank supported useful candidates |
| Does evidence conflict with a query premise | Noul | Preserve a conflict lane rather than discard disagreement |
| Is content trying to redirect the agent | Noul | Flag untrusted instructions; never use the score as the only security barrier |
| Are the question's requirements covered | Noul per requirement | Mark missing requirements and select retrieval repair |
| How does a source relate to a claim | Choice | Supports, contradicts, or insufficient support for stage 7 |

Noul probability is not Choice confidence. Do not reuse one threshold across
these different outputs. Arithmetic, permission checks, timestamp ordering,
candidate limits, and tool availability remain deterministic code.

## Execution states

The main-owned retrieval run has one revision and these explicit states:
normalize, route, retrieve, expand, grade, assess, repair_retrieval, ready,
partial, unavailable, cancelled, and exhausted.

| From | Condition | Next |
| --- | --- | --- |
| normalize | English representation valid | route |
| normalize | Failed or uncertain normalization | multilingual baseline, then unavailable verification status |
| route | Eligible direct task, confident no retrieval | ready with direct-response restrictions |
| route | Retrieval required or uncertain | retrieve |
| retrieve | Candidates available | expand, then grade |
| grade | Useful or unresolved conflicting evidence | assess |
| assess | Every required fact covered | ready |
| assess | Missing requirements and budget remains | repair_retrieval, then retrieve |
| assess | Useful partial evidence, no remaining repair | partial |
| Any | Provider failure | baseline retrieval where possible; unavailable verification status |
| Any | User cancellation or budget exhaustion | cancelled or exhausted; never restart implicitly |

An explicit request to search, inspect repository facts, or verify current state
sets a code-owned retrieval requirement. A low Jev score cannot suppress that
requirement. Greetings and transformations fully supported by supplied text may
take the direct path. Direct mode cannot introduce new repository facts.

## State and context budgets

Use the shared run deadline from stage 1. Start with three retrieval rounds total
and six Jev requests across retrieval and final verification. Reserve at least
one request for stage 7 before spending calls on optional repair. Batch independent
questions over the same state, but never batch so much irrelevant text that the
question loses its target.

Use bounded excerpts with explicit IDs and coverage markers. A truncated passage
cannot establish whole-passage irrelevance or complete support. Exceeding the
state allowance produces a visible coverage limitation and a targeted-read
candidate, not a fabricated negative judgment.

Avoid abandoned network threads accumulating after timeouts. Define transport
ownership, close/cancel behavior, maximum in-flight calls, and backpressure.
Retries for transient failures count against the same deadline and request
allowance. Authentication errors are not retried. Do not hold search index or
conversation locks during network waits.

## Policy calibration

Keep the prototype's 0.2/0.8 thresholds only as labeled provisional defaults.
Fit per-decision policies on the calibration split, prioritizing false skips,
lost supporting evidence, and premature sufficiency. Store the selected policy
with model, prompts, dataset hash, normalization version, and operating costs.

Choice routing also considers top-two margin and an explicit defer option.
Accepting an uncertain result is a policy decision with a measured error rate,
not a claim about the model being calibrated on this wiki. Stage 10 defines the
held-out evaluation and release conditions.

## Trace and replay

Record each transition, candidate ID, response score, policy threshold, budget
change, evidence revision, elapsed time, and machine-readable fallback reason.
Derive reason labels from code and selected evidence; do not invent Jev's hidden
reasoning. Raw source snapshots remain access-controlled and optional; replay
must be possible from an explicitly saved synthetic or private fixture.

Cache decisions only by complete decision state plus model, prompt, policy, and
normalization versions. A failure is not a cacheable negative. A changed source,
specification revision, or candidate set invalidates the decision.

## Implementation locations and migration

Extend `tool/decision/` from stage 1 with contracts and policies. Put cross-pipeline
execution in `tool/main/knowledge.py`. Root CLI entry points call the same flow.
Retire the prototype `search/controller.py` through a compatible facade until
all callers and stored records are migrated. Keep the `search` pipeline free of
model orchestration imports.

## Completion gate

Tests exercise every transition and prove that empty results, invalid answers,
timeouts, exhausted budgets, source errors, and cancellation terminate correctly.
Invalid candidate IDs never reach an executor. Rules and explicit retrieval
requirements survive all model outputs. Replay reproduces code decisions from
recorded scores. A live bilingual sample reaches each supported outcome without
passing untranslated prose to the English-only decision path.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Types | Noul, Choice, Score contracts and validation | Done — `tool/decision/contract.py`: `DecisionRequest` (`decision-request/1`) with every field this plan names, each question carrying its policy kind and the candidate it is about; a Choice may offer only allowed candidates or `defer`. `decide` returns a `DecisionResult` (`decision-result/1`) whose status is decided, uncertain, unavailable, invalid, cancelled or exhausted; the last four carry no answers. Answers are checked again after the transport: a bool, a string or NaN is no probability, a missing or extra answer is invalid. `checked` refuses a result for another request, another schema or an unoffered candidate. The transport validates Score against its ordered criteria (the legend must be the scale asked, in order) and a Choice's probabilities must sum to one |
| 2 | Policy | Per-decision thresholds, uncertainty, explicit retrieval requirements | Done — `tool/decision/policy.py`: a rule per kind (route, source, useful, conflict, redirect, coverage as Noul; repair, relation as Choice with confidence and top-two margin). The prototype's 0.2/0.8 stay as `provisional-1`, labelled; `eval/jev/policy.json` replaces a kind only for the model and prompt version it was fitted on. The repair Choice is fitted on confidence and top-two margin; the relation Choice is stage 7's and stays provisional until [stage 7](7-grounded-answer.md) fits it. An explicit request to search, inspect the repository or verify current state (English or Korean, `knowledge.EXPLICIT`) requires retrieval whatever the route score; uncertain means retrieve, an uncertain source is searched |
| 3 | Workflow | States, budgets, targeted repair, cancellation | Done — `main/knowledge.py` `Flow`: normalize, route, retrieve, expand, grade, assess, repair_retrieval, and the terminal ready, partial, unavailable, cancelled, exhausted, each transition recorded with its reason and budget. It runs on stage 5's chunk rounds. Grading, coverage per requirement and the preferred repair are one Jev request a round, so a question costs a route and one request a round; a repair runs only if another round's request still leaves one for stage 7. Requirements are the question's separate questions and lines; one sentence that may ask several things (`knowledge.SEVERAL`) is split by the translator's model (`translate.parts`, one request of the run, only when round 1 and the reserve still fit), and an ask that loses the question's version, year or exclusion refuses the whole split (`retrieval.checked_subqueries`); the whole question stays a requirement beside its asks, so a split names what is missing but never makes `ready` easier. Coverage stands only on a complete passage still held and not judged useless; evidence past `k` whose conflict is not ruled out stays in `evidence`, and the rest held past `k` is named in `limits`. A subquery repair searches those requirements, with no further model request. Round 1 takes a third of the 40-candidate allowance, each later round its share of the rest. Repairs are chosen from what code can run — adjacent context, sources not searched, graph bridge, subqueries for several requirements, arXiv only with `external` — Jev's choice first when the policy accepts it, else code's order. Only a passage read whole and judged no conflict is dropped on a no, and never by a request that judged a requirement covered, since Jev does not say which passages a coverage rests on — one cut off, read in part, or with a conflict not ruled out stays, and only a whole passage shows coverage; every short ending passes one exit (`Flow.go`): a run that would end `partial` or `unavailable` while cancelled or past its deadline ends `cancelled` or `exhausted`, whichever path brought it there; a passage a coverage `yes` was judged over is in `evidence` whatever `k` is; passages past the state allowance are not sent and are named in `limits` and `reads`; a passage contradicting the query's premise is kept, one addressing the agent is flagged. Transport: at most four requests in flight per process, a fifth is `busy`; a cancel or timeout aborts the connection and frees its slot; no retries |
| 4 | Records | Trace, cache identity, and replay | Done — every decision in the dossier with its request id, scores, verdicts, policy version and usage. `decision.Cache` keys on state, questions, candidates, model, prompt, policy and normalization versions and keeps no failure. `Tape` records what a run read from outside — English, answers, rounds, clock — and `knowledge.replay` decides it again with no request; `tool/jev_search.py --record`/`--replay`. `eval/jev/replay.smoke-03.tape.json`, recorded live, replays exactly |
| 5 | Migration | Shared app/CLI flow and prototype retirement | Done — the app's active and shadow paths and `tool/jev_search.py` run `knowledge.prepare`. `search/controller.py` and `search.prepare` are gone: every caller moved in this change, and the search pipeline imports no model orchestration. A stored dossier's old status reads through `knowledge.migrated` (direct, supported, insufficient, fallback become ready with `direct`, ready, partial, unavailable). The `test_jev.py` controller checks moved to `test_decision_flow.py` and `test_evidence.py` |
| 6 | Verification | Transition coverage, live sample, calibrated policy artifact | Done — `tool/test_decision_flow.py`, 74 cases with no network: every status of the contract, every transition, empty rounds, invalid answers and choices, timeouts, busy, exhausted calls, unavailable retrieval, cancellation, explicit requirements against a zero score, replay, and one run through `prepare` on a real index. Calibration (`tool/eval/policy.py`, `eval/jev/calibration.json`, 35 synthetic cases, 10 of them labelling the right repair): 64 live requests, 45,886 tokens; all six Noul kinds and the repair Choice fitted — route `no` 0.4, source `no` 0.35, useful 0.2/0.65, coverage `yes` 0.6, conflict `no` 0.4, redirect `no` 0.25, repair confidence 0.6 and margin 0.2; no needed retrieval skipped, no supporting passage dropped, no requirement called covered that is not, and no wrong repair accepted (8 accepted right, the 2 bridge cases left to code). Live sample (`tool/eval/decisions.py`, the smoke corpus, English and Korean): ready, ready (direct), partial, unavailable (a refused key, answered 401), cancelled and exhausted all reached; nothing Korean sent to Jev; 20 requests, 27,748 tokens. Live split check (two one-sentence, two-part questions, English and Korean): each split into its two asks, each ask covered, `ready`, three requests a question |

Owned by later stages, deliberately: the relation Choice and the enforcement of the direct path's restrictions are [stage 7](7-grounded-answer.md#inherited-from-stage-6)'s; the held-out evaluation, and a calibration split larger than these 35 synthetic cases, are [stage 10](10-evaluation-rollout.md#evaluation-data)'s. The fitted rules are error rates on this split, not a claim that Jev is calibrated on this wiki. A replay reproduces decisions, not retrieval: the tape carries the rounds' chunks, source text included, so it is written only when asked. The live bridge question (smoke-03) ends `ready` under the fitted policy (coverage `yes` 0.6); `partial` is reached by the unanswerable smoke-08. The smoke corpus puts the Atlas team on call on Tuesdays only, so smoke-03's `ready` rests on a coverage score near the threshold.
