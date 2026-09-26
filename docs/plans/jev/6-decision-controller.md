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
| 1 | Types | Noul, Choice, Score contracts and validation | Not started |
| 2 | Policy | Per-decision thresholds, uncertainty, explicit retrieval requirements | Not started |
| 3 | Workflow | States, budgets, targeted repair, cancellation | Not started |
| 4 | Records | Trace, cache identity, and replay | Not started |
| 5 | Migration | Shared app/CLI flow and prototype retirement | Not started |
| 6 | Verification | Transition coverage, live sample, calibrated policy artifact | Not started |
